"""
tests/test_integracion_demo_registro.py
=========================================
integracion_demo/registro.py y reconciliar.py (brief del Admin del
06-oct-2026 (3), puntos 3d, 3e, 4 y 5). Offline: no hay red.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from integracion_demo import registro as reg
from integracion_demo.reconciliar import TOLERANCIA_USD, VENTANA_S, reconciliar
from integracion_demo.registro import (
    ARCHIVO_CRUDO,
    ARCHIVO_REGISTRO,
    CAMPOS,
    HASH_INICIAL,
    Outcome,
    Registro,
    RegistroEnFallbackError,
    RegistroRotoError,
    contar_por_outcome,
    deducir_outcome,
    derivados_de_cierre,
    hash_de_fila,
    leer_crudo,
    leer_registro,
    verificar_cadena,
)

_U = "7f6c0b5e-0000-4000-8000-000000000001"


def _tres(tmp_path: Path) -> Registro:
    r = Registro(tmp_path)
    r.agregar(evento="envio", trade_uuid=_U, account_id="DOT********")
    r.agregar(evento="compra", trade_uuid=_U, contract_id=11)
    r.agregar(evento="cierre", trade_uuid=_U, contract_id=11, outcome="sl",
              evidencia="exit_spot cruzó stop_loss")
    return r


def _lineas(tmp_path: Path) -> list[str]:
    return (tmp_path / ARCHIVO_REGISTRO).read_text(encoding="utf-8").splitlines()


def _escribir(tmp_path: Path, lineas: list[str]) -> None:
    (tmp_path / ARCHIVO_REGISTRO).write_text("\n".join(lineas) + "\n", encoding="utf-8")


# ═══ Cadena ═══════════════════════════════════════════════════════════════

def test_la_cadena_encadena_desde_el_hash_inicial(tmp_path):
    _tres(tmp_path)
    filas = leer_registro(tmp_path).filas
    assert [f["seq"] for f in filas] == [1, 2, 3]
    assert filas[0]["prev_hash"] == HASH_INICIAL
    assert filas[1]["prev_hash"] == filas[0]["row_hash"]
    assert filas[2]["prev_hash"] == filas[1]["row_hash"]
    assert verificar_cadena(filas) == []
    assert leer_registro(tmp_path).integro


def test_el_row_hash_es_sha256_de_prev_hash_mas_la_fila_canonica(tmp_path):
    import hashlib
    _tres(tmp_path)
    f = leer_registro(tmp_path).filas[1]
    sin = {k: v for k, v in f.items() if k != "row_hash"}
    canon = json.dumps(sin, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert f["row_hash"] == hashlib.sha256((f["prev_hash"] + canon).encode()).hexdigest()
    assert hash_de_fila(f["prev_hash"], f) == f["row_hash"]


def test_la_cadena_detecta_una_fila_editada(tmp_path):
    _tres(tmp_path)
    lineas = _lineas(tmp_path)
    f = json.loads(lineas[1])
    f["contract_id"] = 12
    lineas[1] = json.dumps(f)
    _escribir(tmp_path, lineas)
    leido = leer_registro(tmp_path)
    assert any("seq 2" in p and "row_hash" in p for p in leido.problemas_cadena)
    assert not leido.integro


def test_la_cadena_detecta_una_fila_editada_con_su_hash_recalculado(tmp_path):
    """Recalcular el hash de la fila editada no alcanza: la siguiente deja
    de encadenar."""
    _tres(tmp_path)
    lineas = _lineas(tmp_path)
    f = json.loads(lineas[1])
    f["contract_id"] = 12
    f["row_hash"] = hash_de_fila(f["prev_hash"], f)
    lineas[1] = json.dumps(f)
    _escribir(tmp_path, lineas)
    problemas = leer_registro(tmp_path).problemas_cadena
    assert problemas == ("fila 3 (seq 3): prev_hash no encadena",)


def test_la_cadena_detecta_una_fila_borrada(tmp_path):
    _tres(tmp_path)
    lineas = _lineas(tmp_path)
    _escribir(tmp_path, [lineas[0], lineas[2]])
    problemas = leer_registro(tmp_path).problemas_cadena
    assert "fila 2 (seq 3): prev_hash no encadena" in problemas
    assert "fila 2: seq 3 después de 1" in problemas


def test_la_cadena_detecta_la_primera_fila_borrada(tmp_path):
    _tres(tmp_path)
    _escribir(tmp_path, _lineas(tmp_path)[1:])
    assert "fila 1 (seq 2): prev_hash no encadena" in leer_registro(tmp_path).problemas_cadena


def test_borrar_la_ultima_fila_se_ve_contra_la_cabeza(tmp_path):
    r = _tres(tmp_path)
    cabeza = r.cabeza
    _escribir(tmp_path, _lineas(tmp_path)[:-1])
    assert leer_registro(tmp_path).problemas_cadena == (), "la cadena sola no lo ve"
    assert cabeza[0] == 3
    assert Registro(tmp_path).cabeza[0] == 2 and Registro(tmp_path).cabeza != cabeza


def test_la_cabeza_es_la_ultima_fila(tmp_path):
    r = _tres(tmp_path)
    ultima = leer_registro(tmp_path).filas[-1]
    assert r.cabeza == (3, ultima["row_hash"])
    assert Registro(tmp_path).cabeza == r.cabeza


def test_un_registro_vacio_tiene_cabeza_inicial(tmp_path):
    assert Registro(tmp_path).cabeza == (0, HASH_INICIAL)


def test_reabrir_sigue_la_cadena(tmp_path):
    _tres(tmp_path)
    r2 = Registro(tmp_path)
    r2.agregar(evento="reconciliacion", trade_uuid=_U, reconciliado="ok", evidencia="x")
    filas = leer_registro(tmp_path).filas
    assert [f["seq"] for f in filas] == [1, 2, 3, 4]
    assert verificar_cadena(filas) == []


def test_no_se_agrega_sobre_un_registro_roto(tmp_path):
    _tres(tmp_path)
    lineas = _lineas(tmp_path)
    _escribir(tmp_path, [lineas[0], lineas[2]])
    with pytest.raises(RegistroRotoError):
        Registro(tmp_path)


def test_no_se_agrega_sobre_un_registro_con_lineas_corruptas(tmp_path):
    _tres(tmp_path)
    _escribir(tmp_path, _lineas(tmp_path) + ["{no es json"])
    with pytest.raises(RegistroRotoError):
        Registro(tmp_path)


# ═══ Lectura (portada de trade_ledger) ════════════════════════════════════

def test_la_lectura_reporta_corruptas_con_su_numero_de_linea(tmp_path):
    _tres(tmp_path)
    lineas = _lineas(tmp_path)
    _escribir(tmp_path, [lineas[0], "{roto", lineas[1], "[1, 2]", lineas[2]])
    leido = leer_registro(tmp_path)
    assert leido.lineas_corruptas == (2, 4) and leido.n_lineas_corruptas == 2
    assert [f["seq"] for f in leido.filas] == [1, 2, 3]
    assert leido.problemas_cadena == ()


def test_una_linea_repetida_se_deduplica_y_se_cuenta(tmp_path):
    _tres(tmp_path)
    lineas = _lineas(tmp_path)
    _escribir(tmp_path, [lineas[0], lineas[1], lineas[1], lineas[2]])
    leido = leer_registro(tmp_path)
    assert leido.n_deduplicadas == 1
    assert [f["seq"] for f in leido.filas] == [1, 2, 3]
    assert leido.integro


def test_sin_archivo_el_registro_esta_vacio(tmp_path):
    leido = leer_registro(tmp_path / "no")
    assert leido.filas == [] and leido.lineas_corruptas == () and leido.n_deduplicadas == 0


def test_las_lineas_vacias_no_cuentan(tmp_path):
    _tres(tmp_path)
    _escribir(tmp_path, ["", *_lineas(tmp_path), ""])
    leido = leer_registro(tmp_path)
    assert len(leido.filas) == 3 and leido.lineas_corruptas == ()


# ═══ Lo que entra y lo que no ═════════════════════════════════════════════

def test_cada_fila_trae_todos_los_campos_del_esquema(tmp_path):
    _tres(tmp_path)
    for f in leer_registro(tmp_path).filas:
        assert list(f) == list(CAMPOS)
        assert f["schema_version"] == reg.SCHEMA_VERSION


def test_los_campos_del_brief_estan_todos():
    brief = """seq trade_uuid contract_id buy_transaction_id sell_transaction_id
    schema_version experiment_id preregistro_sha256 git_commit_sha kappa_model_version
    account_id account_type ws_path underlying_symbol contract_type stake multiplier
    nocional currency sl_solicitado sl_confirmado tp_confirmado quote_at_signal
    entry_spot exit_spot sl_precio slip_entrada slip_salida commission_cruda
    commission_usd commission_modelada cost_ratio profit_deriv pnl_recalculado R_usd
    r_multiple outcome t_senal_ms t_envio_ms t_ack_ms purchase_time sell_time
    latencia_ms raw_sha256 prev_hash row_hash reconciliado""".split()
    assert set(brief) <= set(CAMPOS)
    assert set(CAMPOS) - set(brief) == {"evento", "evidencia", "commission_cotizada",
                                        "reconciliado_profit", "reconciliado_comision"}


@pytest.mark.parametrize("evento", ["cierre", "rechazo", "no_enviada", "sin_respuesta"])
def test_una_fila_terminal_sin_outcome_no_entra(tmp_path, evento):
    r = Registro(tmp_path)
    with pytest.raises(ValueError, match="sin outcome"):
        r.agregar(evento=evento, trade_uuid=_U)
    assert not (tmp_path / ARCHIVO_REGISTRO).exists()


def test_un_outcome_fuera_de_la_lista_no_entra(tmp_path):
    with pytest.raises(ValueError):
        Registro(tmp_path).agregar(evento="cierre", trade_uuid=_U, outcome="ganadora",
                                   evidencia="x")


def test_un_outcome_sin_evidencia_no_entra(tmp_path):
    with pytest.raises(ValueError, match="evidencia"):
        Registro(tmp_path).agregar(evento="cierre", trade_uuid=_U, outcome="sl")


def test_los_nueve_outcomes_del_brief():
    assert {o.value for o in Outcome} == {
        "sl", "tp", "stop_out", "sell_manual", "sell_tiempo", "cancelacion",
        "no_ejecutada", "rechazada", "desconocido"}


@pytest.mark.parametrize("campos, mensaje", [
    ({"evento": "envio", "trade_uuid": _U, "otro": 1}, "fuera del esquema"),
    ({"evento": "envio", "trade_uuid": _U, "seq": 9}, "los pone el registro"),
    ({"evento": "envio", "trade_uuid": _U, "row_hash": "x"}, "los pone el registro"),
    ({"evento": "envio", "trade_uuid": _U, "account_id": "DOT90004580"}, "sin enmascarar"),
    ({"evento": "envio"}, "trade_uuid"),
    ({"evento": "envio", "trade_uuid": _U, "stake": float("nan")}, "NaN"),
])
def test_lo_que_no_entra(tmp_path, campos, mensaje):
    with pytest.raises(ValueError, match=mensaje):
        Registro(tmp_path).agregar(**campos)


def test_un_evento_desconocido_no_entra(tmp_path):
    with pytest.raises(ValueError):
        Registro(tmp_path).agregar(evento="otro", trade_uuid=_U)


def test_reconciliado_arranca_pendiente(tmp_path):
    _tres(tmp_path)
    assert {f["reconciliado"] for f in leer_registro(tmp_path).filas} == {"pendiente"}


def test_contar_por_outcome_solo_cuenta_terminales_y_trae_todos(tmp_path):
    r = _tres(tmp_path)
    r.agregar(evento="rechazo", trade_uuid="u2", outcome="rechazada", evidencia="x")
    r.agregar(evento="reconciliacion", trade_uuid=_U, outcome="sl", evidencia="x")
    conteo = contar_por_outcome(leer_registro(tmp_path).filas)
    assert conteo["sl"] == 1 and conteo["rechazada"] == 1 and conteo["tp"] == 0
    assert set(conteo) == {o.value for o in Outcome}


# ═══ Crudo ════════════════════════════════════════════════════════════════

def test_lo_crudo_va_aparte_con_su_sha256(tmp_path):
    r = Registro(tmp_path)
    sha = r.guardar_crudo('{"msg_type":"buy"}', 1)
    assert leer_crudo(tmp_path) == {sha: '{"msg_type":"buy"}'}
    assert not (tmp_path / ARCHIVO_REGISTRO).exists()
    assert (tmp_path / ARCHIVO_CRUDO).exists()


# ═══ Fallback (portado de trade_ledger.py:235-241) ════════════════════════

def test_el_directorio_por_defecto_en_fallback_lanza(monkeypatch, tmp_path):
    monkeypatch.delenv("SPEL_DRIVE_ROOT", raising=False)
    monkeypatch.setattr(reg, "drive_root", lambda: reg.LOCAL_FALLBACK_DRIVE_ROOT)
    with pytest.raises(RegistroEnFallbackError):
        Registro()


def test_el_fallback_pedido_explicito_se_acepta(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SPEL_DRIVE_ROOT", raising=False)
    monkeypatch.setattr(reg, "drive_root", lambda: reg.LOCAL_FALLBACK_DRIVE_ROOT)
    r = Registro(permitir_fallback=True)
    assert r.directorio == reg.directorio_por_defecto()


def test_con_drive_definido_el_directorio_es_trade_ledger_demo(monkeypatch, tmp_path):
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(tmp_path))
    r = Registro()
    assert r.directorio == tmp_path / "trade_ledger" / "demo"


# ═══ Outcome ══════════════════════════════════════════════════════════════

def _poc(**extra) -> dict:
    base = {"contract_type": "MULTUP", "underlying_symbol": "frxXAUUSD", "currency": "USD",
            "status": "sold", "is_sold": 1, "entry_spot": "2400.00", "exit_spot": "2397.70",
            "profit": "-0.10", "commission": "0.02", "purchase_time": 100, "sell_time": 160,
            "transaction_ids": {"buy": 501, "sell": 502},
            "limit_order": {"stop_loss": {"order_amount": -0.10, "value": "2397.80"},
                            "stop_out": {"order_amount": -1.0, "value": "2376.48"}}}
    base.update(extra)
    return base


def test_el_outcome_es_sl_si_el_exit_cruzo_solo_el_stop_loss():
    o, ev = deducir_outcome(_poc())
    assert o == "sl" and "stop_loss" in ev


def test_el_outcome_es_stop_out_si_cruzo_solo_el_stop_out():
    lo = {"stop_out": {"value": "2376.48"}}
    assert deducir_outcome(_poc(exit_spot="2376.00", limit_order=lo))[0] == "stop_out"


def test_el_outcome_es_tp_si_cruzo_el_take_profit():
    lo = {"stop_loss": {"value": "2397.80"}, "take_profit": {"value": "2402.00"}}
    assert deducir_outcome(_poc(exit_spot="2402.10", limit_order=lo))[0] == "tp"


def test_multdown_mira_los_niveles_al_reves():
    lo = {"stop_loss": {"value": "2402.20"}, "stop_out": {"value": "2423.50"}}
    p = _poc(contract_type="MULTDOWN", exit_spot="2402.30", limit_order=lo)
    assert deducir_outcome(p)[0] == "sl"
    assert deducir_outcome({**p, "exit_spot": "2401.00"})[0] == "desconocido"


def test_el_outcome_es_desconocido_sin_evidencia():
    o, ev = deducir_outcome(_poc(exit_spot="2399.00"))
    assert o == "desconocido" and "no cruzó" in ev
    o, ev = deducir_outcome(_poc(exit_spot=None))
    assert o == "desconocido" and ev == "sin exit_spot"
    o, _ = deducir_outcome(_poc(limit_order={}))
    assert o == "desconocido", "sin niveles devueltos no se deduce nada"


def test_si_cruzo_varios_niveles_es_desconocido():
    o, ev = deducir_outcome(_poc(exit_spot="2370.00"))
    assert o == "desconocido" and "varios" in ev


def test_el_sell_propio_manda_sobre_los_niveles():
    o, ev = deducir_outcome(_poc(), venta_propia={"motivo": "tiempo", "transaction_id": 9,
                                                    "raw_sha256": "ab"})
    assert o == "sell_tiempo" and "9" in ev
    assert deducir_outcome(_poc(), venta_propia={"motivo": "manual"})[0] == "sell_manual"


def test_un_contrato_abierto_no_tiene_outcome():
    assert deducir_outcome(_poc(status="open", is_sold=0)) == (None, None)


def test_un_contrato_cancelado():
    assert deducir_outcome(_poc(status="cancelled"))[0] == "cancelacion"


def test_el_nivel_exacto_cuenta_como_cruzado():
    assert deducir_outcome(_poc(exit_spot="2397.80"))[0] == "sl"


# ═══ Derivados ════════════════════════════════════════════════════════════

def _deriv(poc=None, **kw):
    args = dict(stake=1.0, multiplier=100.0, quote_at_signal=2399.90,
                quote_at_exit_signal=None, outcome="sl")
    args.update(kw)
    return derivados_de_cierre(poc or _poc(), **args)


def test_r_multiple_usa_R_usd_del_stop_loss_confirmado():
    d = _deriv()
    assert d["R_usd"] == pytest.approx(0.10)
    assert d["r_multiple"] == pytest.approx(-1.0)
    assert d["sl_confirmado"] == -0.10


def test_r_multiple_no_usa_el_stake_ni_el_solicitado():
    """Stake 1 y SL confirmado 0,25: el R es 0,25, no 1 ni lo pedido."""
    lo = {"stop_loss": {"order_amount": -0.25, "value": "2394.00"}}
    d = _deriv(_poc(limit_order=lo, profit="-0.25", exit_spot="2393.90"))
    assert d["R_usd"] == pytest.approx(0.25) and d["r_multiple"] == pytest.approx(-1.0)


def test_sin_stop_loss_confirmado_no_hay_r():
    d = _deriv(_poc(limit_order={}))
    assert d["R_usd"] is None and d["r_multiple"] is None


def test_el_stop_loss_confirmado_sale_del_display_si_falta_el_order_amount():
    lo = {"stop_loss": {"display_order_amount": "-0.10", "value": "2397.80"}}
    assert _deriv(_poc(limit_order=lo))["R_usd"] == pytest.approx(0.10)


def test_comision_cruda_string_y_usd_solo_si_la_moneda_es_usd():
    d = _deriv()
    assert d["commission_cruda"] == "0.02" and d["commission_usd"] == pytest.approx(0.02)
    assert d["commission_modelada"] == pytest.approx(0.0002 * 100)
    assert d["cost_ratio"] == pytest.approx(1.0)
    e = _deriv(_poc(currency="EUR"))
    assert e["commission_usd"] is None and e["cost_ratio"] is None
    assert e["commission_cruda"] == "0.02"


def test_profit_deriv_es_string_y_el_pnl_se_recalcula():
    d = _deriv()
    assert d["profit_deriv"] == "-0.10"
    esperado = (2397.70 - 2400.00) / 2400.00 * 100 - 0.02
    assert d["pnl_recalculado"] == pytest.approx(esperado)


def test_slip_con_signo_segun_la_direccion():
    d = _deriv()
    assert d["slip_entrada"] == pytest.approx(0.10), "MULTUP: entró más caro, en contra"
    assert d["slip_salida"] == pytest.approx(0.10), "salió por debajo del SL, en contra"
    lo = {"stop_loss": {"order_amount": -0.10, "value": "2402.20"}}
    p = _poc(contract_type="MULTDOWN", exit_spot="2402.30", limit_order=lo)
    e = _deriv(p)
    assert e["slip_entrada"] == pytest.approx(-0.10), "MULTDOWN: entró más caro, a favor"
    assert e["slip_salida"] == pytest.approx(0.10)
    assert e["pnl_recalculado"] == pytest.approx(-(2402.30 - 2400.00) / 2400.00 * 100 - 0.02)


def test_slip_salida_de_un_sell_por_tiempo_usa_la_cotizacion_de_la_decision():
    d = _deriv(_poc(exit_spot="2401.00"), outcome="sell_tiempo", quote_at_exit_signal=2401.20)
    assert d["slip_salida"] == pytest.approx(0.20)
    assert _deriv(outcome="desconocido")["slip_salida"] is None


def test_el_parser_tolera_claves_faltantes():
    d = derivados_de_cierre({"contract_type": "MULTUP"}, stake=1.0, multiplier=100.0,
                            quote_at_signal=None, quote_at_exit_signal=None, outcome=None)
    assert d["nocional"] == 100.0
    assert all(v is None for k, v in d.items() if k != "nocional")


# ═══ Reconciliación ═══════════════════════════════════════════════════════

def _cerrado(tmp_path, *, cotizada: object = "0.02", cruda: object = "0.02") -> Registro:
    """Un trade cerrado. Desde DB-2 trae su fila de envío con la comisión
    cotizada, y la del contrato en el cierre: sin ellas, la comisión es
    no_verificable y ningún trade llegaría a ok."""
    r = Registro(tmp_path)
    r.agregar(evento="envio", trade_uuid=_U, commission_cotizada=cotizada)
    r.agregar(evento="cierre", trade_uuid=_U, contract_id=11, buy_transaction_id=501,
              sell_transaction_id=502, stake=1.0, profit_deriv="-0.10",
              outcome="sl", evidencia="x", t_envio_ms=100_000, commission_cruda=cruda)
    return r


_PT = [{"contract_id": 11, "buy_price": 1.0, "sell_price": 0.9}]
_ST = [{"action_type": "buy", "contract_id": 11, "transaction_id": 501, "amount": -1.0,
        "transaction_time": 100},
       {"action_type": "sell", "contract_id": 11, "transaction_id": 502, "amount": 0.9,
        "transaction_time": 160}]


def test_reconciliar_ok_agrega_una_linea_y_no_edita(tmp_path):
    r = _cerrado(tmp_path)
    antes = _lineas(tmp_path)
    nuevas = reconciliar(r, profit_table=_PT, statement=_ST, raw_profit_table="aa")
    assert [(n["reconciliado"], n["reconciliado_profit"], n["reconciliado_comision"])
            for n in nuevas] == [("ok", "ok", "ok")]
    despues = _lineas(tmp_path)
    assert despues[:len(antes)] == antes and len(despues) == len(antes) + 1
    assert json.loads(despues[-1])["evento"] == "reconciliacion"
    assert leer_registro(tmp_path).integro


def test_reconciliar_no_repite_un_ok(tmp_path):
    r = _cerrado(tmp_path)
    reconciliar(r, profit_table=_PT, statement=_ST)
    assert reconciliar(r, profit_table=_PT, statement=_ST) == []


@pytest.mark.parametrize("pt, st, fragmento", [
    ([], _ST, "profit_table: 0 filas"),
    ([{**_PT[0], "sell_price": 0.8}], _ST, "≠ profit_deriv"),
    ([{**_PT[0], "buy_price": 2.0}], _ST, "≠ stake"),
    (_PT, [_ST[1]], "0 compras"),
    (_PT, [{**_ST[0], "transaction_id": 9}, _ST[1]], "buy transaction_id"),
    (_PT, [{**_ST[0], "amount": -2.0}, _ST[1]], "buy amount"),
    (_PT, [_ST[0]], "0 ventas"),
    (_PT, [_ST[0], {**_ST[1], "transaction_id": 9}], "sell transaction_id"),
    (_PT, [_ST[0], {**_ST[1], "amount": 0.5}], "sell amount"),
])
def test_reconciliar_marca_discrepancia(tmp_path, pt, st, fragmento):
    r = _cerrado(tmp_path)
    nuevas = reconciliar(r, profit_table=pt, statement=st)
    assert nuevas[0]["reconciliado"] == "discrepancia"
    assert fragmento in nuevas[0]["evidencia"]


def test_reconciliar_tolera_medio_centavo_y_no_mas(tmp_path):
    r = _cerrado(tmp_path)
    pt = [{**_PT[0], "sell_price": 0.9 + TOLERANCIA_USD * 0.9}]
    st = [_ST[0], {**_ST[1], "amount": pt[0]["sell_price"]}]
    n = reconciliar(r, profit_table=pt, statement=st)[0]
    assert (n["reconciliado"], n["reconciliado_profit"]) == ("ok", "ok")
    r2 = _cerrado(tmp_path / "b")
    pt = [{**_PT[0], "sell_price": 0.9 + TOLERANCIA_USD * 1.5}]
    st = [_ST[0], {**_ST[1], "amount": pt[0]["sell_price"]}]
    n = reconciliar(r2, profit_table=pt, statement=st)[0]
    assert (n["reconciliado"], n["reconciliado_profit"]) == ("discrepancia", "discrepancia")


def test_la_tolerancia_es_medio_centavo(tmp_path):
    """Deriv devuelve centavos: un centavo de diferencia ya es discrepancia."""
    assert TOLERANCIA_USD == 0.005
    r = _cerrado(tmp_path)
    pt = [{**_PT[0], "sell_price": 0.91}]
    st = [_ST[0], {**_ST[1], "amount": 0.91}]
    n = reconciliar(r, profit_table=pt, statement=st)
    assert n[0]["reconciliado"] == "discrepancia" and "≠ profit_deriv" in n[0]["evidencia"]


def test_una_rechazada_con_una_compra_cerca_es_discrepancia(tmp_path):
    r = Registro(tmp_path)
    r.agregar(evento="sin_respuesta", trade_uuid=_U, outcome="desconocido",
              evidencia="sin ack", t_envio_ms=100_000)
    cerca = [{**_ST[0], "transaction_time": 100 + VENTANA_S}]
    n = reconciliar(r, profit_table=[], statement=cerca)
    assert n[0]["reconciliado"] == "discrepancia" and "contract_id 11" in n[0]["evidencia"]
    assert (n[0]["reconciliado_profit"], n[0]["reconciliado_comision"]) == (
        "discrepancia", "no_verificable")
    r2 = Registro(tmp_path / "b")
    r2.agregar(evento="rechazo", trade_uuid=_U, outcome="rechazada", evidencia="x",
               t_envio_ms=100_000)
    lejos = [{**_ST[0], "transaction_time": 100 + VENTANA_S + 1}]
    n = reconciliar(r2, profit_table=[], statement=lejos)[0]
    assert (n["reconciliado"], n["reconciliado_profit"], n["reconciliado_comision"]) == (
        "ok", "ok", "no_aplica")


def test_reconciliar_ignora_trades_abiertos(tmp_path):
    r = Registro(tmp_path)
    r.agregar(evento="compra", trade_uuid=_U, contract_id=11)
    assert reconciliar(r, profit_table=_PT, statement=_ST) == []


def test_una_discrepancia_se_vuelve_a_reconciliar(tmp_path):
    r = _cerrado(tmp_path)
    assert reconciliar(r, profit_table=[], statement=_ST)[0]["reconciliado"] == "discrepancia"
    assert reconciliar(r, profit_table=_PT, statement=_ST)[0]["reconciliado"] == "ok"
    marcas = [(f["reconciliado"], f["reconciliado_profit"], f["reconciliado_comision"])
              for f in leer_registro(tmp_path).filas if f["evento"] == "reconciliacion"]
    assert marcas == [("discrepancia", "discrepancia", "ok"), ("ok", "ok", "ok")]


# ═══ La comisión cotizada contra la del contrato (DB-1, DB-2, brief 9-R2) ══
#
# El criterio viejo: la reconciliación marcaba `ok` con la nota "la comisión
# no se reconcilia". El nuevo: la cotizada (fila de envío) y la del contrato
# (`commission_cruda`, fila de cierre) se comparan en Decimal, y lo que no
# se puede comparar es no_verificable, nunca ok.

from integracion_demo.reconciliar import marca_comision, marca_global  # noqa: E402


def _comision(tmp_path, cotizada, cruda):
    n = reconciliar(_cerrado(tmp_path, cotizada=cotizada, cruda=cruda),
                    profit_table=_PT, statement=_ST)[0]
    return n["reconciliado_comision"], n["reconciliado"], json.loads(n["evidencia"])["comision"]


def test_str_del_contrato_contra_float_de_la_cotizacion(tmp_path):
    """La cotización trae 0.02 (float) y el contrato "0.02" (str): comprar
    guarda str(0.02) y los dos pasan a Decimal desde su texto."""
    assert _comision(tmp_path, str(0.02), "0.02")[:2] == ("ok", "ok")


def test_un_float_largo_se_compara_por_su_texto(tmp_path):
    cotizada = str(0.1 + 0.2 - 0.28)          # '0.020000000000000018'
    assert cotizada != "0.02"
    assert _comision(tmp_path, cotizada, "0.02")[0] == "ok"


@pytest.mark.parametrize("cotizada, esperado", [
    ("0.015", "ok"),         # exactamente medio centavo
    ("0.025", "ok"),
    ("0.0149", "discrepancia"),
    ("0.0251", "discrepancia"),
    ("0.02", "ok"),
    ("0.03", "discrepancia"),
])
def test_la_frontera_de_medio_centavo(tmp_path, cotizada, esperado):
    assert _comision(tmp_path, cotizada, "0.02")[0] == esperado


def test_la_frontera_es_exacta_en_decimal_y_no_en_float(tmp_path):
    """Con floats, 0.02 − 0.015 da 0.005000000000000001 y sería discrepancia."""
    assert 0.02 - 0.015 > TOLERANCIA_USD
    estado, _, detalle = _comision(tmp_path, "0.015", "0.02")
    assert estado == "ok" and detalle["diferencia"] == "0.005"


@pytest.mark.parametrize("cotizada, cruda", [
    (None, "0.02"), ("0.02", None), (None, None),
    ("abc", "0.02"), ("0.02", "NaN"), ("Infinity", "0.02"), ("", "0.02"),
])
def test_si_falta_una_o_no_es_un_numero_es_no_verificable(tmp_path, cotizada, cruda):
    estado, global_, detalle = _comision(tmp_path, cotizada, cruda)
    assert (estado, global_) == ("no_verificable", "no_verificable")
    assert detalle["cotizada"] == cotizada and detalle["contrato"] == cruda


def test_sin_contrato_y_sin_compra_cerca_es_no_aplica(tmp_path):
    r = Registro(tmp_path)
    r.agregar(evento="rechazo", trade_uuid=_U, outcome="rechazada", evidencia="x",
              t_envio_ms=100_000)
    n = reconciliar(r, profit_table=[], statement=[])[0]
    assert (n["reconciliado"], n["reconciliado_profit"], n["reconciliado_comision"]) == (
        "ok", "ok", "no_aplica")


def test_una_discrepancia_de_comision_hace_discrepancia_global(tmp_path):
    estado, global_, _ = _comision(tmp_path, "0.05", "0.02")
    assert (estado, global_) == ("discrepancia", "discrepancia")


@pytest.mark.parametrize("profit, comision, esperado", [
    ("ok", "ok", "ok"),
    ("ok", "no_aplica", "ok"),
    ("ok", "no_verificable", "no_verificable"),
    ("ok", "discrepancia", "discrepancia"),
    ("discrepancia", "ok", "discrepancia"),
    ("discrepancia", "no_aplica", "discrepancia"),
    ("discrepancia", "no_verificable", "discrepancia"),
    ("discrepancia", "discrepancia", "discrepancia"),
])
def test_la_marca_global(profit, comision, esperado):
    assert marca_global(profit, comision) == esperado


def test_marca_comision_sin_contrato_con_compra_cerca_no_es_no_aplica():
    estado, _ = marca_comision(None, {"contract_id": None}, ["statement: compra cerca"])
    assert estado == "no_verificable"


def test_un_no_verificable_se_vuelve_a_reconciliar(tmp_path):
    r = _cerrado(tmp_path, cotizada=None)
    assert reconciliar(r, profit_table=_PT, statement=_ST)[0]["reconciliado"] == "no_verificable"
    assert reconciliar(r, profit_table=_PT, statement=_ST)[0]["reconciliado"] == "no_verificable"
    marcas = [f["reconciliado"] for f in leer_registro(tmp_path).filas
              if f["evento"] == "reconciliacion"]
    assert marcas == ["no_verificable", "no_verificable"]


def test_el_envio_que_cuenta_es_el_del_mismo_trade(tmp_path):
    r = Registro(tmp_path)
    r.agregar(evento="envio", trade_uuid="otro-trade", commission_cotizada="0.02")
    r.agregar(evento="envio", trade_uuid=_U)
    r.agregar(evento="cierre", trade_uuid=_U, contract_id=11, buy_transaction_id=501,
              sell_transaction_id=502, stake=1.0, profit_deriv="-0.10",
              outcome="sl", evidencia="x", t_envio_ms=100_000, commission_cruda="0.02")
    assert reconciliar(r, profit_table=_PT, statement=_ST)[0]["reconciliado_comision"] == (
        "no_verificable")


# ── Un registro escrito antes de DB-2 (SCHEMA_VERSION 1, sin la columna) ──

_CAMPOS_V1 = [c for c in CAMPOS if c not in (
    "commission_cotizada", "reconciliado_profit", "reconciliado_comision")]


def _registro_v1(tmp_path, eventos: list[dict]) -> None:
    prev, lineas = reg.HASH_INICIAL, []
    for seq, campos in enumerate(eventos, start=1):
        fila = {c: campos.get(c) for c in _CAMPOS_V1}
        fila.update(seq=seq, schema_version=1, prev_hash=prev,
                    reconciliado=campos.get("reconciliado", "pendiente"))
        fila["row_hash"] = prev = reg.hash_de_fila(prev, fila)
        lineas.append(json.dumps(fila, ensure_ascii=False))
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / reg.ARCHIVO_REGISTRO).write_text("\n".join(lineas) + "\n", encoding="utf-8")


_V1_TRADE = [
    {"evento": "envio", "trade_uuid": _U},
    {"evento": "cierre", "trade_uuid": _U, "contract_id": 11, "buy_transaction_id": 501,
     "sell_transaction_id": 502, "stake": 1.0, "profit_deriv": "-0.10", "outcome": "sl",
     "evidencia": "x", "t_envio_ms": 100_000, "commission_cruda": "0.02"},
]


def test_un_registro_viejo_sin_la_columna_es_no_verificable_y_no_se_rellena(tmp_path):
    _registro_v1(tmp_path, _V1_TRADE)
    antes = _lineas(tmp_path)
    assert "commission_cotizada" not in json.loads(antes[0])
    n = reconciliar(Registro(tmp_path), profit_table=_PT, statement=_ST)[0]
    assert (n["reconciliado"], n["reconciliado_profit"], n["reconciliado_comision"]) == (
        "no_verificable", "ok", "no_verificable")
    despues = _lineas(tmp_path)
    assert despues[:len(antes)] == antes, "las filas viejas no se tocan"
    assert leer_registro(tmp_path).integro


def test_un_ok_viejo_sin_marca_de_comision_se_reconcilia_de_nuevo(tmp_path):
    _registro_v1(tmp_path, [*_V1_TRADE, {"evento": "reconciliacion", "trade_uuid": _U,
                                         "reconciliado": "ok", "evidencia": "{}"}])
    nuevas = reconciliar(Registro(tmp_path), profit_table=_PT, statement=_ST)
    assert [n["reconciliado"] for n in nuevas] == ["no_verificable"]


# ── El mismo ok viejo, en el conteo de d) de DG-3 ──────────────────────────

from integracion_demo.reconciliar import conteo_de_marcas, ok_vigente  # noqa: E402


def test_un_ok_viejo_sin_marca_de_comision_cuenta_como_sin_reconciliar(tmp_path):
    """`reconciliar` no lo toma por ok y lo reconcilia de nuevo; el conteo de
    d) tampoco: hasta esa fila nueva, el trade está sin reconciliar. Antes
    contaba como ok, y d) podía cumplirse con una comisión que nunca se
    contrastó."""
    _registro_v1(tmp_path, [*_V1_TRADE, {"evento": "reconciliacion", "trade_uuid": _U,
                                         "reconciliado": "ok", "evidencia": "{}"}])
    assert conteo_de_marcas(tmp_path) == {"ok": 0, "discrepancia": 0, "no_verificable": 0,
                                          "sin_reconciliar": 1}
    reconciliar(Registro(tmp_path), profit_table=_PT, statement=_ST)
    assert conteo_de_marcas(tmp_path) == {"ok": 0, "discrepancia": 0, "no_verificable": 1,
                                          "sin_reconciliar": 0}


@pytest.mark.parametrize("fila, es_ok", [
    ({"reconciliado": "ok", "reconciliado_comision": "ok"}, True),
    ({"reconciliado": "ok", "reconciliado_comision": "no_aplica"}, True),
    ({"reconciliado": "ok", "reconciliado_comision": None}, False),
    ({"reconciliado": "ok"}, False),
    ({"reconciliado": "discrepancia", "reconciliado_comision": "ok"}, False),
    ({"reconciliado": "no_verificable", "reconciliado_comision": "no_verificable"}, False),
    ({"reconciliado": "pendiente", "reconciliado_comision": None}, False),
])
def test_ok_vigente(fila, es_ok):
    assert ok_vigente(fila) is es_ok


def test_el_conteo_y_reconciliar_coinciden_en_lo_que_es_ok(tmp_path):
    """Una sola definición de ok: lo que el conteo da por ok es justo lo que
    `reconciliar` no vuelve a reconciliar. B es un ok sin la marca de la
    comisión, escrito en un registro de SCHEMA_VERSION 2."""
    r = Registro(tmp_path)
    for u in "ABCDE":
        r.agregar(evento="cierre", trade_uuid=u, contract_id=1, outcome="sl", evidencia="x")
    r.agregar(evento="reconciliacion", trade_uuid="A", reconciliado="ok",
              reconciliado_profit="ok", reconciliado_comision="ok", evidencia="{}")
    r.agregar(evento="reconciliacion", trade_uuid="B", reconciliado="ok", evidencia="{}")
    r.agregar(evento="reconciliacion", trade_uuid="C", reconciliado="discrepancia",
              reconciliado_profit="discrepancia", reconciliado_comision="ok", evidencia="{}")
    r.agregar(evento="reconciliacion", trade_uuid="D", reconciliado="no_verificable",
              reconciliado_profit="ok", reconciliado_comision="no_verificable", evidencia="{}")
    assert conteo_de_marcas(tmp_path) == {"ok": 1, "discrepancia": 1, "no_verificable": 1,
                                          "sin_reconciliar": 2}
    nuevas = reconciliar(r, profit_table=[], statement=[])
    assert sorted(n["trade_uuid"] for n in nuevas) == ["B", "C", "D", "E"]


# ── Lo que el registro acepta ──────────────────────────────────────────────

def test_la_cotizada_se_guarda_cruda_como_str(tmp_path):
    r = Registro(tmp_path)
    with pytest.raises(ValueError, match="cruda"):
        r.agregar(evento="envio", trade_uuid=_U, commission_cotizada=0.02)
    assert r.agregar(evento="envio", trade_uuid=_U,
                     commission_cotizada="0.02")["commission_cotizada"] == "0.02"


@pytest.mark.parametrize("campo, valor", [
    ("reconciliado_profit", "no_verificable"), ("reconciliado_profit", "no_aplica"),
    ("reconciliado_profit", "pendiente"), ("reconciliado_comision", "talvez"),
    ("reconciliado", "no_aplica"),
])
def test_las_marcas_fuera_de_su_conjunto_no_entran(tmp_path, campo, valor):
    with pytest.raises(ValueError):
        Registro(tmp_path).agregar(evento="reconciliacion", trade_uuid=_U, **{campo: valor})


def test_el_esquema_es_la_version_2():
    assert reg.SCHEMA_VERSION == 2
