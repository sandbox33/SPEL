"""
tests/test_integracion_demo_orden_live.py
===========================================
Parte B del brief del Admin del 06-oct-2026 (3): la PRIMERA orden demo,
autorizada en ese brief. La dispara el Admin desde live-tests.yml con
`objetivo: orden_demo`; nadie más.

══ LA AUTORIZACIÓN ══

  · Ventana: del 07-oct-2026 00:00 al 16-oct-2026 23:59:59 UTC. Fuera de
    ella el test se salta solo.
  · UNA sola orden: frxXAUUSD, MULTUP, stake mínimo, ×100, stop_loss 0,10
    USD, sin TP, sell a los 60 s si sigue abierta.
  · Antes y después: GET /accounts y el balance.
  · Solo con SPEL_ORDEN_DEMO=1, que pone únicamente el job `orden_demo` del
    workflow: el job de sondas (`pytest -m live`) la salta.

[INTERPRETACIÓN] Lo que el brief no fija:
  · "Una sola orden" es una por autorización, no una por corrida: antes de
    comprar se mira statement desde el inicio de la ventana, y si ya hay
    una compra en la cuenta demo, no se compra y el informe lo dice.
  · El balance es el campo `balance` de GET /accounts (el esquema oficial
    lo declara obligatorio). El mensaje `balance` del WS no está en la
    lista blanca del punto 3c.
  · Stake mínimo: se cotiza con 1 USD y se lee
    `validation_params.stake.min`; si es otro, se vuelve a cotizar con él.
    `contracts_for` tampoco está en la lista blanca.
  · El seguimiento es un proposal_open_contract cada CADA_S (sin
    `subscribe`); si el sell no cierra el contrato, se espera
    ESPERAR_CIERRE_S más.
  · El cierre del socket inactivo se mide al final, hasta MAX_INACTIVO_S;
    si no cierra, el informe dice null.
  · El registro va a un directorio temporal del runner: se pierde con el
    job. Lo que queda es el informe, con la cabeza de la cadena.

══ EL INFORME ══

Una línea JSON en el log con: el esquema real de proposal_open_contract
(todas las claves, aplanadas), dónde aparece `commission` en cada tipo de
respuesta, su valor y tipo en la proposal y en el contrato frente a
κ × nocional, entry_spot, exit_spot, profit, la latencia del buy, el cierre
del socket inactivo, el registro y la reconciliación. Con eso se fija el
parser; hasta entonces tolera claves faltantes y guarda todo lo crudo.

══ CUÁNDO SE PONE ROJO ══

Por plomería (faltan DERIV_APP_ID o DERIV_API_TOKEN), por una fuga (el
token, el App ID, un OTP o el account_id en el informe), o por un error
del código. Lo que conteste Deriv —un rechazo incluido— es el resultado.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import pytest

from governance.secrets import SecretKey, load_secret
from ingestion.kappa_deriv import KAPPA_DERIV
from integracion_demo.conexion import ConexionDemo, abrir_websocket
from integracion_demo.ejecucion import (
    ConexionPerdidaError,
    Contexto,
    Ejecutor,
    NoConectadoError,
    Orden,
)
from integracion_demo.otp import (
    ENDPOINT_DEMO,
    CuentaNoDemoError,
    Getter,
    OtpNoEmitidoError,
    Poster,
    UrlNoDemoError,
    _get_httpx,
    _limpiador,
    _post_httpx,
    elegir_demo,
    enmascarar_id,
    leer_cuentas,
    otp_de,
    url_demo_nueva,
)
from integracion_demo.reconciliar import reconciliar
from integracion_demo.registro import Registro, leer_crudo, leer_registro, num

VENTANA_INICIO = datetime(2026, 10, 7, 0, 0, 0, tzinfo=timezone.utc)
VENTANA_FIN = datetime(2026, 10, 16, 23, 59, 59, tzinfo=timezone.utc)

SIMBOLO = "frxXAUUSD"
TIPO = "MULTUP"
MULTIPLICADOR = 100.0
STOP_LOSS_USD = 0.10
STAKE_DE_SONDEO = 1.0

VENDER_A_LOS_S = 60.0
CADA_S = 2.0
ESPERAR_CIERRE_S = 30.0
#: [INTERPRETACIÓN] Hasta cuánto se deja el socket inactivo al final.
MAX_INACTIVO_S = 300.0

EXPERIMENT_ID = "parte_b_primera_orden_demo"
PERMISO_ENV_VAR = "SPEL_ORDEN_DEMO"


def en_ventana(ahora: datetime) -> bool:
    return VENTANA_INICIO <= ahora <= VENTANA_FIN


# ═══ El esquema que se mide ═══════════════════════════════════════════════

def claves_planas(x: Any, prefijo: str = "") -> set[str]:
    """Todas las claves de un objeto, con ruta: `limit_order.stop_loss.value`,
    `audit_details.all_ticks[].epoch`."""
    out: set[str] = set()
    if isinstance(x, dict):
        for k, v in x.items():
            ruta = f"{prefijo}{k}"
            out.add(ruta)
            out |= claves_planas(v, ruta + ".")
    elif isinstance(x, list):
        for v in x:
            out |= claves_planas(v, prefijo.rstrip(".") + "[].")
    return out


def esquema_medido(crudos: dict[str, str]) -> dict:
    """De todo lo crudo: las claves de proposal_open_contract y dónde
    aparece `commission`, por tipo de respuesta."""
    poc: set[str] = set()
    comision: dict[str, set[str]] = {}
    for texto in crudos.values():
        try:
            d = json.loads(texto)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict):
            continue
        tipo = str(d.get("msg_type"))
        rutas = claves_planas(d)
        if tipo == "proposal_open_contract" and isinstance(d.get(tipo), dict):
            poc |= claves_planas(d[tipo])
        con = {r for r in rutas if "commission" in r.rsplit(".", 1)[-1]}
        if con:
            comision.setdefault(tipo, set()).update(con)
    return {"proposal_open_contract_claves": sorted(poc),
            "commission_aparece_en": {k: sorted(v) for k, v in sorted(comision.items())}}


def _valor_y_tipo(v: Any) -> dict:
    return {"valor": v, "tipo_json": type(v).__name__}


# ═══ La parte B ═══════════════════════════════════════════════════════════

async def ejecutar_parte_b(*, token: str, app_id: str, directorio: Path,
                           get: Getter = _get_httpx, post: Poster = _post_httpx,
                           abrir: Callable[[str], Any] = abrir_websocket,
                           dormir: Callable[[float], Any] = asyncio.sleep,
                           reloj_s: Callable[[], float] = time.monotonic,
                           max_inactivo_s: float = MAX_INACTIVO_S,
                           git_commit_sha: Optional[str] = None
                           ) -> tuple[dict, tuple[str, ...]]:
    """Devuelve el informe y lo que no puede aparecer en él."""
    secretos: list[str] = [token, app_id]
    limpiar = _limpiador((token, app_id))
    informe: dict[str, Any] = {
        "parte": "B, brief 06-oct-2026 (3)",
        "ventana": [VENTANA_INICIO.isoformat(), VENTANA_FIN.isoformat()],
        "orden_pedida": {"underlying_symbol": SIMBOLO, "contract_type": TIPO,
                         "multiplier": MULTIPLICADOR, "stop_loss": STOP_LOSS_USD,
                         "take_profit": None, "vender_a_los_s": VENDER_A_LOS_S}}

    informe["cuentas_antes"], cuentas = await leer_cuentas(token=token, app_id=app_id, get=get)
    demo = elegir_demo(cuentas)
    if demo is None:
        informe["no_aplica"] = ("GET /accounts no confirmó una cuenta demo activa: no se "
                                "pidió OTP ni se mandó nada")
        return informe, tuple(secretos)
    secretos.append("".join(ch for ch in str(demo["account_id"]) if ch.isdigit()))
    informe["balance_antes"] = {"balance": demo.get("balance"), "currency": demo.get("currency")}

    registro = Registro(directorio)

    async def url_nueva() -> str:
        url = await url_demo_nueva(demo, token=token, app_id=app_id, post=post)
        secretos.append(otp_de(url) or "")
        return url

    contexto = Contexto(experiment_id=EXPERIMENT_ID,
                        account_id=enmascarar_id(demo["account_id"]), account_type="demo",
                        ws_path=urlsplit(ENDPOINT_DEMO).path, currency=demo.get("currency"),
                        git_commit_sha=git_commit_sha)
    conexion = ConexionDemo(url_nueva, al_recibir=registro.guardar_crudo, abrir=abrir)
    try:
        async with conexion as c:
            e = Ejecutor(c, registro, contexto)
            informe.update(await _operar(e, c, registro, dormir=dormir, reloj_s=reloj_s,
                                         max_inactivo_s=max_inactivo_s))
    except (CuentaNoDemoError, UrlNoDemoError, OtpNoEmitidoError, NoConectadoError,
            ConexionPerdidaError) as exc:
        informe["no_conectado"] = limpiar(f"{type(exc).__name__}: {exc}")
    informe["conexion"] = {"n_otps": conexion.n_otps, "n_conexiones": conexion.n_conexiones,
                           "historial": conexion.historial,
                           "no_pedidos": len(conexion.no_pedidos)}

    informe["cuentas_despues"], cuentas = await leer_cuentas(token=token, app_id=app_id, get=get)
    despues = next((c for c in cuentas if c.get("account_id") == demo["account_id"]), None)
    informe["balance_despues"] = ({"balance": despues.get("balance"),
                                   "currency": despues.get("currency")} if despues else None)

    leido = leer_registro(registro.directorio)
    informe["registro"] = {
        "integro": leido.integro, "cabeza": list(registro.cabeza),
        "eventos": [f["evento"] for f in leido.filas],
        "filas": [{k: f[k] for k in ("seq", "evento", "outcome", "evidencia", "reconciliado")}
                  for f in leido.filas]}
    informe["esquema"] = esquema_medido(leer_crudo(registro.directorio))
    return informe, tuple(secretos)


async def _operar(e: Ejecutor, c: ConexionDemo, registro: Registro, *,
                  dormir: Callable[[float], Any], reloj_s: Callable[[], float],
                  max_inactivo_s: float) -> dict:
    out: dict[str, Any] = {}
    previas, _ = await e.compras_desde(int(VENTANA_INICIO.timestamp()))
    if previas:
        out["no_aplica"] = (f"statement ya registra {len(previas)} compra(s) en la cuenta "
                            f"demo desde el inicio de la ventana: la autorización es de UNA "
                            f"orden. No se compró.")
        return out

    orden = Orden(SIMBOLO, TIPO, STAKE_DE_SONDEO, MULTIPLICADOR, STOP_LOSS_USD)
    datos, _ = await e.cotizar(orden)
    prop = datos.get("proposal") or {}
    stake_min = num(((prop.get("validation_params") or {}).get("stake") or {}).get("min"))
    out["cotizacion_de_sondeo"] = {"error": datos.get("error"), "stake_min": stake_min}
    if stake_min is not None and stake_min != STAKE_DE_SONDEO:
        orden = Orden(SIMBOLO, TIPO, stake_min, MULTIPLICADOR, STOP_LOSS_USD)
        datos, _ = await e.cotizar(orden)
        prop = datos.get("proposal") or {}
    if datos.get("error") or not prop:
        out["no_aplica"] = "la cotización volvió con error: no se compró"
        out["cotizacion"] = {"error": datos.get("error")}
        return out
    nocional = orden.stake * orden.multiplier
    out["cotizacion"] = {
        "stake": orden.stake, "nocional": nocional, "spot": prop.get("spot"),
        "commission": _valor_y_tipo(prop.get("commission")),
        "kappa_por_nocional": KAPPA_DERIV[SIMBOLO] * nocional,
        "limit_order": prop.get("limit_order"),
        "validation_params": prop.get("validation_params")}

    trade = await e.comprar(orden, quote_at_signal=num(prop.get("spot")),
                            t_senal_ms=int(time.time() * 1000))
    filas = leer_registro(registro.directorio).filas
    out["compra"] = {"estado": trade.estado, "contract_id": trade.contract_id,
                     "latencia_ms": filas[-1].get("latencia_ms"),
                     "evidencia": filas[-1].get("evidencia")}
    if trade.estado == "abierto":
        cierre = await e.seguir_hasta_cierre(trade, vender_a_los_s=VENDER_A_LOS_S, cada_s=CADA_S,
                                             esperar_cierre_s=ESPERAR_CIERRE_S, dormir=dormir,
                                             reloj_s=reloj_s)
        poc = trade.ultimo_poc or {}
        out["cierre"] = {k: cierre.get(k) for k in (
            "outcome", "evidencia", "entry_spot", "exit_spot", "sl_precio", "sl_confirmado",
            "R_usd", "profit_deriv", "r_multiple", "pnl_recalculado", "commission_cruda",
            "commission_usd", "commission_modelada", "cost_ratio", "slip_entrada",
            "slip_salida", "purchase_time", "sell_time", "latencia_ms")}
        out["cierre"]["commission_en_el_contrato"] = _valor_y_tipo(poc.get("commission"))
        out["cierre"]["status"] = poc.get("status")
        out["cierre"]["venta_propia"] = trade.venta_propia is not None
        out["cierre"]["consultas"] = len(trade.pocs)

    pt, sha_pt = await e.profit_table(limit=10, sort="DESC")
    st, sha_st = await e.statement(limit=20)
    out["reconciliacion"] = [{k: f[k] for k in ("reconciliado", "evidencia")}
                             for f in reconciliar(registro, profit_table=pt, statement=st,
                                                  raw_profit_table=sha_pt, raw_statement=sha_st)]
    out["cierre_inactivo_s"] = await c.medir_cierre_inactivo(max_inactivo_s)
    out["max_inactivo_s"] = max_inactivo_s
    return out


def _publicar(capsys, informe: dict) -> None:
    with capsys.disabled():
        print("\n=== PARTE B: PRIMERA ORDEN DEMO ===")
        print(json.dumps(informe, ensure_ascii=False, separators=(",", ":")))


@pytest.mark.live
@pytest.mark.skipif(os.environ.get(PERMISO_ENV_VAR) != "1",
                    reason="solo en el job orden_demo de live-tests.yml (SPEL_ORDEN_DEMO=1)")
async def test_live_primera_orden_demo(capsys, tmp_path):
    if not en_ventana(datetime.now(timezone.utc)):
        pytest.skip("fuera de la ventana de la autorización (07-oct 00:00 .. 16-oct 23:59 UTC)")
    app_id = load_secret(SecretKey.DERIV_APP_ID, required=False)
    token = load_secret(SecretKey.DERIV_API_TOKEN, required=False)
    assert app_id, "SPEL_ORDEN_DEMO=1 pero DERIV_APP_ID no llegó al job"
    assert token, "SPEL_ORDEN_DEMO=1 pero DERIV_API_TOKEN no llegó al job"
    informe, secretos = await ejecutar_parte_b(token=token, app_id=app_id,
                                               directorio=tmp_path / "registro_demo",
                                               git_commit_sha=os.environ.get("GITHUB_SHA"))
    texto = json.dumps(informe, ensure_ascii=False)
    for s in secretos:
        if s:
            assert s not in texto, "un secreto o el account_id llegó al informe: no se publica"
    _publicar(capsys, informe)


# ═══ Offline ══════════════════════════════════════════════════════════════

from tests.test_integracion_demo_ejecucion import _Falso  # noqa: E402

_CUENTAS = {"data": [
    {"account_id": "ROT12345678", "account_type": "real", "status": "active",
     "currency": "USD", "balance": 0},
    {"account_id": "DOT90004580", "account_type": "demo", "status": "active",
     "currency": "USD", "balance": 10000.0}]}


def _rest(cuentas=_CUENTAS):
    vistos: list[str] = []
    n = [0]

    async def get(u, h):
        vistos.append("GET")
        return 200, {}, json.dumps(cuentas)

    async def post(u, h):
        n[0] += 1
        vistos.append(f"POST {u.rsplit('/', 2)[-2]}")
        return 200, {}, json.dumps({"data": {"url": f"{ENDPOINT_DEMO}?otp=OTPSECRETO{n[0]}"}})
    return get, post, vistos


def _reloj():
    t = [0.0]

    async def dormir(s):
        t[0] += s
    return dormir, (lambda: t[0])


async def _correr(tmp_path, falso=None, cuentas=_CUENTAS):
    falso = falso or _Falso()
    get, post, vistos = _rest(cuentas)
    dormir, reloj = _reloj()
    informe, secretos = await ejecutar_parte_b(
        token="TOKq7", app_id="APP31", directorio=tmp_path, get=get, post=post,
        abrir=falso.connector, dormir=dormir, reloj_s=reloj, max_inactivo_s=0.05)
    return informe, secretos, vistos, falso


def _tipos(falso) -> list[str]:
    return [next(iter(m)) for m in falso.enviados]


async def test_la_parte_b_entera(tmp_path):
    informe, secretos, vistos, falso = await _correr(tmp_path)
    assert vistos == ["GET", "POST DOT90004580", "GET"], "cuentas antes, UN OTP, cuentas después"
    tipos = _tipos(falso)
    assert tipos[0] == "statement", "primero se mira si ya hubo una compra"
    assert tipos.count("proposal") == 1, "el stake mínimo del falso es 1: no se recotiza"
    assert sum(1 for m in falso.enviados if "parameters" in m) == 1, "UNA orden"
    (orden,) = [m for m in falso.enviados if "parameters" in m]
    assert orden["parameters"]["underlying_symbol"] == SIMBOLO
    assert orden["parameters"]["contract_type"] == TIPO
    assert orden["parameters"]["multiplier"] == MULTIPLICADOR
    assert orden["parameters"]["limit_order"] == {"stop_loss": STOP_LOSS_USD}
    assert informe["balance_antes"] == {"balance": 10000.0, "currency": "USD"}
    assert informe["balance_despues"] == {"balance": 10000.0, "currency": "USD"}
    assert informe["compra"]["estado"] == "abierto"
    assert informe["cierre"]["outcome"] == "sell_tiempo"
    assert informe["cierre"]["r_multiple"] == pytest.approx(0.3)
    assert informe["cierre"]["commission_en_el_contrato"] == {"valor": "0.02", "tipo_json": "str"}
    assert informe["cotizacion"]["commission"] == {"valor": "0.02", "tipo_json": "str"}
    assert informe["cotizacion"]["kappa_por_nocional"] == pytest.approx(0.02)
    assert informe["registro"]["integro"] is True
    assert informe["registro"]["eventos"] == ["envio", "compra", "seguimiento", "venta",
                                              "cierre", "reconciliacion"]
    assert "cierre_inactivo_s" in informe
    esquema = informe["esquema"]
    assert "limit_order.stop_loss.order_amount" in esquema["proposal_open_contract_claves"]
    assert esquema["commission_aparece_en"]["proposal"] == ["proposal.commission"]
    assert esquema["commission_aparece_en"]["proposal_open_contract"] == [
        "proposal_open_contract.commission"]
    texto = json.dumps(informe)
    assert {"OTPSECRETO1", "TOKq7", "APP31", "90004580"} <= set(secretos)
    for s in ("OTPSECRETO1", "TOKq7", "APP31", "90004580"):
        assert s not in texto


async def test_con_una_compra_previa_en_la_ventana_no_se_compra(tmp_path):
    previa = {"statement": lambda p: {"statement": {"transactions": (
        [{"action_type": p["action_type"], "contract_id": 7}] if "action_type" in p else [])}}}
    informe, _, vistos, falso = await _correr(tmp_path, _Falso(extra=previa))
    assert not any("parameters" in m for m in falso.enviados)
    assert _tipos(falso) == ["statement"]
    assert "UNA orden" in informe["no_aplica"]
    assert falso.de_tipo("statement")[0]["date_from"] == int(VENTANA_INICIO.timestamp())


async def test_sin_cuenta_demo_no_hay_otp_ni_orden(tmp_path):
    solo_real = {"data": [_CUENTAS["data"][0]]}
    informe, _, vistos, falso = await _correr(tmp_path, cuentas=solo_real)
    assert vistos == ["GET"] and falso.enviados == []
    assert "no_aplica" in informe


async def test_un_stake_minimo_distinto_se_recotiza(tmp_path):
    def prop(p):
        return {"proposal": {"spot": 2399.9, "commission": "0.04",
                             "validation_params": {"stake": {"min": "2.00"}}}}
    _, _, _, falso = await _correr(tmp_path, _Falso(extra={"proposal": prop}))
    assert [p["amount"] for p in falso.de_tipo("proposal")] == [1.0, 2.0]
    (orden,) = [m for m in falso.enviados if "parameters" in m]
    assert orden["parameters"]["amount"] == 2.0 and orden["price"] == 2.0


async def test_una_cotizacion_con_error_no_compra(tmp_path):
    error = {"proposal": lambda p: {"error": {"code": "MarketIsClosed", "message": "x"}}}
    informe, _, _, falso = await _correr(tmp_path, _Falso(extra=error))
    assert not any("parameters" in m for m in falso.enviados)
    assert informe["cotizacion"]["error"]["code"] == "MarketIsClosed"


def test_la_ventana_del_brief():
    assert not en_ventana(datetime(2026, 10, 6, 23, 59, 59, tzinfo=timezone.utc))
    assert en_ventana(datetime(2026, 10, 7, 0, 0, 0, tzinfo=timezone.utc))
    assert en_ventana(datetime(2026, 10, 16, 23, 59, 59, tzinfo=timezone.utc))
    assert not en_ventana(datetime(2026, 10, 17, 0, 0, 0, tzinfo=timezone.utc))


def test_la_orden_del_brief():
    assert (SIMBOLO, TIPO, MULTIPLICADOR, STOP_LOSS_USD, VENDER_A_LOS_S) == (
        "frxXAUUSD", "MULTUP", 100.0, 0.10, 60.0)


def test_claves_planas():
    assert claves_planas({"a": {"b": 1, "c": [{"d": 2}]}, "e": None}) == {
        "a", "a.b", "a.c", "a.c[].d", "e"}


def test_el_test_live_solo_corre_con_el_permiso():
    marca = [m for m in test_live_primera_orden_demo.pytestmark if m.name == "skipif"][0]
    assert PERMISO_ENV_VAR == "SPEL_ORDEN_DEMO"
    assert marca.args[0] is (os.environ.get(PERMISO_ENV_VAR) != "1")


async def test_un_corte_despues_del_cierre_se_informa_y_no_se_pone_rojo(tmp_path):
    informe, _, vistos, falso = await _correr(tmp_path, _Falso(cortar=("profit_table",)))
    assert "ConexionPerdidaError" in informe["no_conectado"]
    assert informe["registro"]["eventos"][-1] == "cierre", "la orden quedó registrada"
    assert vistos[-1] == "GET", "las cuentas de después se piden igual"


# ═══ El workflow ══════════════════════════════════════════════════════════

def _workflow() -> dict:
    import yaml
    return yaml.safe_load((Path(__file__).resolve().parent.parent / ".github" / "workflows"
                           / "live-tests.yml").read_text(encoding="utf-8"))


def test_el_workflow_solo_se_dispara_a_mano():
    """PyYAML lee la clave `on` como True."""
    disparadores = _workflow()[True]
    assert set(disparadores) == {"workflow_dispatch"}
    objetivo = disparadores["workflow_dispatch"]["inputs"]["objetivo"]
    assert objetivo["options"] == ["sondas", "orden_demo"] and objetivo["default"] == "sondas"


def test_la_orden_demo_tiene_su_propio_job():
    jobs = _workflow()["jobs"]
    orden, sondas = jobs["orden_demo"], jobs["live"]
    assert orden["if"] == "inputs.objetivo == 'orden_demo'"
    assert sondas["if"] == "inputs.objetivo != 'orden_demo'"
    assert orden["env"][PERMISO_ENV_VAR] == "1"
    assert PERMISO_ENV_VAR not in sondas["env"], "el job de sondas no puede comprar"
    assert set(orden["env"]) == {"DERIV_APP_ID", "DERIV_API_TOKEN", PERMISO_ENV_VAR}
    assert orden["concurrency"] == {"group": "orden-demo", "cancel-in-progress": False}
    corre = [s["run"] for s in orden["steps"] if "run" in s][-1]
    assert corre == "python -m pytest tests/test_integracion_demo_orden_live.py -m live -v"
