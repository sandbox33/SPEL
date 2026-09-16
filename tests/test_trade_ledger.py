"""
tests/test_trade_ledger.py
============================
Cobertura de core/trade_ledger.py.

Usa el patrón de drive_root() temporal ya establecido por test_persistence.py
y test_gdelt_series.py (SPEL_DRIVE_ROOT + tmp_path). No un mecanismo nuevo.

LOS DOS QUE DEFINEN EL ARCHIVO:

  · `test_doscientos_trades_con_140_perdedoras_dan_200_lineas` -- la regla
    entera del ledger en un test. Un ledger que filtra no produce una curva
    de equity mala: produce una que sube siempre, y el sesgo no se ve en el
    archivo filtrado porque el archivo filtrado ya no puede refutarlo.

  · `test_escribir_en_el_fallback_lanza_en_vez_de_perder_la_corrida` -- el
    peor resultado posible no es el error, es la corrida que parece haber
    funcionado y dejó el registro en un directorio que nadie va a leer.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import governance.persistence as persistence_module
from core.execution_costs import (
    Lado,
    Outcome,
    Tarifas,
    compute_trade_costs,
)
from core.trade_ledger import (
    LEDGER_FILENAME,
    LedgerEnFallbackError,
    TradeLedgerEntry,
    _ledger_file_path,
    append_trade,
    build_entry,
    contar_por_outcome,
    read_ledger,
)
from governance.persistence import (
    DRIVE_ROOT_ENV_VAR,
    LOCAL_FALLBACK_DRIVE_ROOT,
    PersistenceStream,
    stream_path,
)


@pytest.fixture(autouse=True)
def _drive_root_temporal(monkeypatch, tmp_path):
    monkeypatch.setenv(DRIVE_ROOT_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)


def _utc(h=1, d=1) -> datetime:
    return datetime(2026, 9, d, h, 0, tzinfo=timezone.utc)


SIN_COSTOS = Tarifas(fee_taker=0.0, fee_maker=0.0, spread=0.0,
                     slippage=0.0, funding_por_periodo=0.0)


def _entrada(trade_id="t1", *, precio_salida=101.0, asset="BTC",
             fraccion=1.0, ejecutada=True, d=1) -> TradeLedgerEntry:
    ts_e, ts_s = _utc(1, d), _utc(2, d)
    resultado = compute_trade_costs(
        ts_entrada=ts_e, ts_salida=ts_s, lado=Lado.LARGO,
        precio_entrada=100.0, precio_salida=precio_salida, nocional=10_000.0,
        tarifas=SIN_COSTOS, entrada_es_taker=True, salida_es_taker=True,
        fraccion_completada=fraccion, ejecutada=ejecutada)
    return build_entry(
        trade_id=trade_id, asset=asset, lado=Lado.LARGO,
        ts_entrada=ts_e, ts_salida=ts_s, precio_entrada=100.0,
        precio_salida=precio_salida, nocional=10_000.0, resultado=resultado)


# ═══ LA regla: nunca se filtra ════════════════════════════════════════════

def test_doscientos_trades_con_140_perdedoras_dan_200_lineas():
    """LA REGLA QUE DEFINE EL ARCHIVO. Si esto se rompe, el ledger dejó de
    servir para lo único que tiene que servir: un ledger filtrado no
    produce una curva de equity mala, produce una que sube siempre."""
    for i in range(140):
        append_trade(_entrada(f"perd-{i}", precio_salida=99.0))
    for i in range(60):
        append_trade(_entrada(f"gana-{i}", precio_salida=101.0))

    leidas = read_ledger()

    assert len(leidas) == 200, "el ledger filtró filas"
    conteo = contar_por_outcome(leidas)
    assert conteo["perdedora"] == 140
    assert conteo["ganadora"] == 60

    # y en el archivo físico también hay 200 líneas
    fisicas = _ledger_file_path().read_text(encoding="utf-8").strip().splitlines()
    assert len(fisicas) == 200


def test_fills_fallidos_y_senales_no_ejecutadas_entran_igual():
    """Los dos tienen neto 0 y ninguno de los dos es un trade "que no pasó":
    son exactamente las filas que permiten calcular qué fracción de las
    señales se descartó."""
    append_trade(_entrada("ok", precio_salida=101.0))
    append_trade(_entrada("fallido", fraccion=0.0))
    append_trade(_entrada("descartada", ejecutada=False))

    conteo = contar_por_outcome(read_ledger())

    assert len(read_ledger()) == 3
    assert conteo["fill_fallido"] == 1
    assert conteo["no_ejecutada"] == 1
    assert conteo["ganadora"] == 1


def test_breakeven_no_se_pierde_entre_ganadora_y_perdedora():
    append_trade(_entrada("tablas", precio_salida=100.0))
    assert contar_por_outcome(read_ledger())["breakeven"] == 1


def test_el_conteo_trae_todos_los_outcomes_aunque_valgan_cero():
    """Un dict al que le falta la clave `perdedora` se lee como "no hubo
    perdedoras" o como "no se midió", y son cosas distintas."""
    append_trade(_entrada("una", precio_salida=101.0))
    conteo = contar_por_outcome(read_ledger())

    assert set(conteo) == {o.value for o in Outcome}
    assert conteo["perdedora"] == 0


def test_read_ledger_no_acepta_filtrar_por_outcome():
    """No es que hoy no filtre: es que no tiene por dónde. Una línea de
    filtrado acá reintroduciría el sesgo de supervivencia en el único lugar
    donde nadie la buscaría."""
    import inspect

    firma = inspect.signature(read_ledger)
    assert set(firma.parameters) == {"since", "until"}
    assert "outcome" not in firma.parameters


# ═══ Desglose completo en cada línea ══════════════════════════════════════

def test_cada_linea_trae_el_desglose_entero():
    t = Tarifas(fee_taker=0.001, fee_maker=0.0002, spread=0.0005,
                slippage=0.0003, funding_por_periodo=0.001)
    resultado = compute_trade_costs(
        ts_entrada=_utc(1), ts_salida=_utc(9), lado=Lado.LARGO,
        precio_entrada=100.0, precio_salida=101.0, nocional=10_000.0,
        tarifas=t, entrada_es_taker=True, salida_es_taker=False)
    append_trade(build_entry(
        trade_id="x", asset="BTC", lado=Lado.LARGO, ts_entrada=_utc(1),
        ts_salida=_utc(9), precio_entrada=100.0, precio_salida=101.0,
        nocional=10_000.0, resultado=resultado))

    linea = json.loads(_ledger_file_path().read_text(encoding="utf-8").strip())

    for campo in ("fee_entrada", "fee_salida", "spread", "slippage",
                  "funding", "costo_total", "bruto", "neto", "outcome",
                  "periodos_funding", "ts_cargo_entrada"):
        assert campo in linea, f"falta {campo} en la línea del ledger"

    assert linea["costo_total"] == pytest.approx(
        linea["fee_entrada"] + linea["fee_salida"] + linea["spread"]
        + linea["slippage"] + linea["funding"])


def test_la_linea_es_plana_no_anidada():
    """Legible a mano y cargable a un DataFrame sin normalizar nada."""
    append_trade(_entrada("x"))
    linea = json.loads(_ledger_file_path().read_text(encoding="utf-8").strip())
    assert all(not isinstance(v, (dict, list)) for v in linea.values())


def test_el_ts_de_cargo_llega_intacto_al_ledger():
    """La regla dura de execution_costs tiene que sobrevivir la
    serialización: si el ledger guardara otro instante, el test de allá
    pasaría y el archivo mentiría igual."""
    append_trade(_entrada("x"))
    entry = read_ledger()[0]
    assert entry.ts_cargo_entrada == entry.ts_entrada


# ═══ Append idempotente, dedup por clave ══════════════════════════════════

def test_append_dos_veces_el_mismo_trade_se_lee_una_sola():
    """Mismo contrato que gdelt_series: append puro, dedup AL LEER. Es lo
    que hace que un reintento tras un corte no cuente el trade dos veces."""
    append_trade(_entrada("t1", precio_salida=101.0))
    append_trade(_entrada("t1", precio_salida=101.0))

    assert len(read_ledger()) == 1
    # físicamente sí hay dos: append_day no deduplica, y este módulo tampoco
    assert len(_ledger_file_path().read_text().strip().splitlines()) == 2


def test_la_ultima_ocurrencia_gana():
    """Un reprocesamiento corrige, no empeora -- mismo criterio que
    read_series()."""
    append_trade(_entrada("t1", precio_salida=99.0))
    append_trade(_entrada("t1", precio_salida=101.0))

    leidas = read_ledger()
    assert len(leidas) == 1
    assert leidas[0].outcome == Outcome.GANADORA.value


def test_trades_distintos_no_se_deduplican_entre_si():
    """Contraprueba: si la clave se ignorara, el test de arriba pasaría
    igual y no probaría nada."""
    append_trade(_entrada("a", precio_salida=99.0))
    append_trade(_entrada("b", precio_salida=99.0))
    assert len(read_ledger()) == 2


def test_un_trade_id_vacio_se_rechaza_al_construir():
    """Sin clave, un reintento tras un corte escribe el trade dos veces y
    las dos cuentan."""
    resultado = compute_trade_costs(
        ts_entrada=_utc(1), ts_salida=_utc(2), lado=Lado.LARGO,
        precio_entrada=100.0, precio_salida=101.0, nocional=10_000.0,
        tarifas=SIN_COSTOS, entrada_es_taker=True, salida_es_taker=True)
    with pytest.raises(ValueError, match="trade_id"):
        build_entry(trade_id="", asset="BTC", lado=Lado.LARGO,
                    ts_entrada=_utc(1), ts_salida=_utc(2),
                    precio_entrada=100.0, precio_salida=101.0,
                    nocional=10_000.0, resultado=resultado)


# ═══ Nunca escribe al fallback en silencio ════════════════════════════════

def test_escribir_en_el_fallback_lanza_en_vez_de_perder_la_corrida(monkeypatch):
    """EL PEOR RESULTADO POSIBLE NO ES EL ERROR: es la corrida que parece
    haber funcionado y dejó el registro en un directorio que nadie lee y
    que el próximo runner se lleva."""
    monkeypatch.delenv(DRIVE_ROOT_ENV_VAR, raising=False)
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)

    with pytest.raises(LedgerEnFallbackError, match="fallback"):
        append_trade(_entrada("x"))


def test_el_error_del_fallback_nombra_la_salida(monkeypatch):
    """Nombra SPEL_DRIVE_ROOT, no un "no se puede escribir" genérico: la
    diferencia entre saber qué falta y tener que buscarlo."""
    monkeypatch.delenv(DRIVE_ROOT_ENV_VAR, raising=False)
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)

    with pytest.raises(LedgerEnFallbackError) as exc:
        append_trade(_entrada("x"))
    assert DRIVE_ROOT_ENV_VAR in str(exc.value)
    assert str(LOCAL_FALLBACK_DRIVE_ROOT) in str(exc.value)


def test_el_fallback_no_deja_ni_el_directorio_creado(monkeypatch, tmp_path):
    """Lanzar después de crear el árbol dejaría basura que la próxima
    corrida podría confundir con un ledger real."""
    monkeypatch.delenv(DRIVE_ROOT_ENV_VAR, raising=False)
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(LedgerEnFallbackError):
        append_trade(_entrada("x"))

    assert not (tmp_path / LOCAL_FALLBACK_DRIVE_ROOT).exists()


def test_se_puede_pedir_el_fallback_explicitamente(monkeypatch, tmp_path):
    """La salida existe, pero hay que pedirla: un ledger descartable es un
    caso legítimo (desarrollo local), lo que no es legítimo es que ocurra
    por omisión."""
    monkeypatch.delenv(DRIVE_ROOT_ENV_VAR, raising=False)
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)
    monkeypatch.chdir(tmp_path)

    append_trade(_entrada("x"), permitir_fallback=True)
    assert len(read_ledger()) == 1


def test_con_spel_drive_root_no_se_queja(tmp_path):
    """El caso normal: con la env var definida, drive_root() no es el
    fallback y no hay nada que bloquear."""
    append_trade(_entrada("x"))
    assert len(read_ledger()) == 1
    assert str(tmp_path) in str(_ledger_file_path())


# ═══ Ruta y lectura ═══════════════════════════════════════════════════════

def test_la_ruta_cuelga_del_stream_declarado(tmp_path):
    """Único punto de verdad: la ruta sale de stream_path(), no de un
    string armado acá."""
    assert _ledger_file_path() == (
        tmp_path / "trade_ledger" / LEDGER_FILENAME)
    assert str(_ledger_file_path().parent) == stream_path(
        PersistenceStream.TRADE_LEDGER)


def test_ledger_vacio_devuelve_lista_no_lanza():
    """Un ledger sin trades todavía es un estado válido, no un error."""
    assert read_ledger() == []


def test_una_linea_corrupta_no_aborta_la_lectura_de_las_demas():
    """Misma degradación parcial que read_series(). En un ledger importa
    más que en ninguna otra parte: perder 199 filas buenas por una mala
    sería exactamente el filtrado que el archivo existe para evitar."""
    append_trade(_entrada("a"))
    with _ledger_file_path().open("a", encoding="utf-8") as f:
        f.write("{esto no es json}\n")
    append_trade(_entrada("b"))

    leidas = read_ledger()
    assert {e.trade_id for e in leidas} == {"a", "b"}


def test_filtro_por_rango_de_fechas():
    append_trade(_entrada("dia1", d=1))
    append_trade(_entrada("dia5", d=5))
    append_trade(_entrada("dia9", d=9))

    assert len(read_ledger()) == 3
    assert {e.trade_id for e in read_ledger(since=_utc(0, 5))} == {"dia5", "dia9"}
    assert {e.trade_id for e in read_ledger(until=_utc(23, 5))} == {"dia1", "dia5"}


def test_las_entradas_del_ledger_son_inmutables():
    append_trade(_entrada("x"))
    entry = read_ledger()[0]
    with pytest.raises(Exception):
        entry.neto = 999.0   # type: ignore[misc]


# ═══ Lo que este módulo NO debe tocar ═════════════════════════════════════

def test_trade_ledger_no_importa_secretos_ni_execution():
    import ast
    import inspect

    import core.trade_ledger as tl

    arbol = ast.parse(inspect.getsource(tl))
    importados: set[str] = set()
    for n in ast.walk(arbol):
        if isinstance(n, ast.Import):
            importados.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            importados.add(n.module)

    for prohibido in ("governance.secrets", "execution",
                      "execution.circuit_breaker", "execution.execution_guard"):
        assert not any(m == prohibido or m.startswith(prohibido + ".")
                       for m in importados), f"trade_ledger importa {prohibido}"
