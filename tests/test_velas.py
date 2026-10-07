"""
tests/test_velas.py
=====================
Cobertura de ingestion/velas.py contra un Deriv falso que pagina
`ticks_history` como la API: hasta `count` velas con época <= `end`. Las
velas son sintéticas; los nombres de campo, los del esquema oficial.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import polars as pl
import pytest

import governance.persistence as persistence_module
import ingestion.velas as velas_mod
from core.preregistro_h1 import historia_requerida
from governance.persistence import DRIVE_ROOT_ENV_VAR
from ingestion.velas import (
    ESQUEMA_VELAS,
    GRANULARIDAD_DIARIA,
    Calendario,
    TipoInvalidoError,
    VelaInvalidaError,
    ingerir,
    leer_velas,
    main,
    normalizar_vela,
    profundidad,
    ruta_revisiones,
    ruta_serie,
)
from tests.deriv_falso import DerivFalso

D = GRANULARIDAD_DIARIA
LUNES = int(datetime(2026, 9, 14, tzinfo=timezone.utc).timestamp())


@pytest.fixture(autouse=True)
def _drive_root_temporal(monkeypatch, tmp_path):
    monkeypatch.setenv(DRIVE_ROOT_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)


def _vela(epoch, precio=100.0):
    return {"epoch": epoch, "open": precio, "high": precio + 2,
            "low": precio - 2, "close": precio + 1}


def _serie(n, *, desde=LUNES - 400 * D, saltear=()):
    return [_vela(desde + k * D, 100.0 + k) for k in range(n) if k not in saltear]


ACTIVOS = [{"symbol": "frxEURUSD", "market": "forex"},
           {"symbol": "cryBTCUSD", "market": "cryptocurrency"}]


def _deriv(velas, *, ahora, activos=ACTIVOS, por_simbolo=None):
    por_simbolo = por_simbolo or {}

    def history(p):
        fuente = por_simbolo.get(p["ticks_history"], velas)
        fin = ahora if p["end"] == "latest" else int(p["end"])
        pagina = [c for c in fuente if c["epoch"] <= fin][-p["count"]:]
        return {"candles": pagina}

    return DerivFalso({"time": lambda p: {"time": ahora},
                       "active_symbols": lambda p: {"active_symbols": activos},
                       "ticks_history": history})


async def _ingerir(d, **kw):
    return await ingerir("demo", app_id="1", connector=d.connector, **kw)


# ═══ Validación de cada vela ══════════════════════════════════════════════

@pytest.mark.parametrize("cambio, motivo", [
    ({"high": 50.0}, "OHLC"), ({"low": 150.0}, "OHLC"),
    ({"epoch": LUNES + 1}, "alineado"), ({"close": -1.0}, "close"),
    ({"open": float("nan")}, "open"), ({"open": "100"}, "open"),
    ({"epoch": float(LUNES)}, "entero"), ({"high": True}, "high"),
])
def test_una_vela_incoherente_se_rechaza(cambio, motivo):
    with pytest.raises(VelaInvalidaError, match=motivo):
        normalizar_vela({**_vela(LUNES), **cambio}, D)


def test_un_precio_entero_se_guarda_como_float_en_el_borde():
    fila = normalizar_vela({"epoch": LUNES, "open": 100, "high": 102,
                            "low": 99, "close": 101}, D)
    assert all(type(fila[k]) is float for k in ("open", "high", "low", "close"))


# ═══ Solo velas cerradas, con la hora del servidor ════════════════════════

@pytest.mark.asyncio
async def test_la_vela_del_dia_en_curso_no_se_guarda():
    """El servidor dice que son las 12:00 del lunes: la vela del lunes está
    abierta. La del domingo se cerró a las 00:00."""
    velas = _serie(401)   # la última es la del LUNES
    res = await _ingerir(_deriv(velas, ahora=LUNES + D // 2), write=True)
    r = next(r for r in res.simbolos if r.simbolo == "cryBTCUSD")
    assert r.abiertas_descartadas == 1
    assert leer_velas("cryBTCUSD")["epoch"].max() == LUNES - D


@pytest.mark.asyncio
async def test_el_reloj_del_runner_no_se_consulta(monkeypatch):
    """Si alguien usara datetime.now() en vez de `time`, un runner con el
    reloj corrido un día guardaría la vela abierta. Se rompe el reloj local
    a propósito: el resultado no cambia."""
    class RelojRoto(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2030, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(velas_mod, "datetime", RelojRoto)
    res = await _ingerir(_deriv(_serie(401), ahora=LUNES + D // 2), write=True)
    assert next(r for r in res.simbolos if r.simbolo == "cryBTCUSD").abiertas_descartadas == 1


# ═══ Backfill e idempotencia ══════════════════════════════════════════════

@pytest.mark.asyncio
async def test_el_backfill_pagina_hacia_atras_hasta_que_no_hay_mas(monkeypatch):
    monkeypatch.setattr(velas_mod, "DERIV_MAX_COUNT", 100)
    d = _deriv(_serie(401), ahora=LUNES + D)
    res = await _ingerir(d, write=True)
    r = next(r for r in res.simbolos if r.simbolo == "cryBTCUSD")
    assert r.nuevas == 401
    assert len(d.de_tipo("ticks_history")) == 5, "401 velas en páginas de 100"
    assert "no devolvió más" in r.corte or "fondo" in r.corte


@pytest.mark.asyncio
async def test_una_segunda_corrida_seguida_no_escribe_nada():
    """El criterio de aceptación del brief, comparando BYTES."""
    await _ingerir(_deriv(_serie(401), ahora=LUNES + D), write=True)
    antes = ruta_serie("cryBTCUSD").read_bytes()

    d = _deriv(_serie(401), ahora=LUNES + D)
    res = await _ingerir(d, write=True)

    assert ruta_serie("cryBTCUSD").read_bytes() == antes
    assert all(r.nuevas == 0 for r in res.simbolos)
    assert not ruta_revisiones("cryBTCUSD").exists()


@pytest.mark.asyncio
async def test_la_corrida_incremental_solo_agrega_lo_nuevo():
    await _ingerir(_deriv(_serie(400), ahora=LUNES), write=True)
    res = await _ingerir(_deriv(_serie(402), ahora=LUNES + 2 * D), write=True)
    r = next(r for r in res.simbolos if r.simbolo == "cryBTCUSD")
    assert r.nuevas == 2
    epochs = leer_velas("cryBTCUSD")["epoch"].to_list()
    assert epochs == sorted(set(epochs)) and len(epochs) == 402


@pytest.mark.asyncio
async def test_un_archivo_sin_salto_final_no_pierde_la_ultima_vela():
    """Misma guarda que gdelt_series.append_day: sin ella, la primera vela
    nueva se pega a la última guardada y las dos se pierden al leer."""
    await _ingerir(_deriv(_serie(400), ahora=LUNES), write=True)
    ruta = ruta_serie("cryBTCUSD")
    ruta.write_bytes(ruta.read_bytes().rstrip(b"\n"))

    await _ingerir(_deriv(_serie(401), ahora=LUNES + D), write=True)

    assert len(leer_velas("cryBTCUSD")) == 401


# ═══ Revisiones ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_una_vela_revisada_no_se_sobrescribe_se_registra_y_se_cuenta():
    await _ingerir(_deriv(_serie(400), ahora=LUNES), write=True)
    antes = ruta_serie("cryBTCUSD").read_bytes()
    revisada = _serie(400)
    revisada[-1] = {**revisada[-1], "close": revisada[-1]["close"] + 0.5}

    res = await _ingerir(_deriv(revisada, ahora=LUNES), write=True)

    r = next(r for r in res.simbolos if r.simbolo == "cryBTCUSD")
    assert r.revisiones_nuevas == 1
    assert ruta_serie("cryBTCUSD").read_bytes() == antes
    rev = json.loads(ruta_revisiones("cryBTCUSD").read_text())
    assert rev["nueva"]["close"] == rev["guardada"]["close"] + 0.5


@pytest.mark.asyncio
async def test_la_misma_revision_no_se_registra_dos_veces():
    await _ingerir(_deriv(_serie(400), ahora=LUNES), write=True)
    revisada = _serie(400)
    revisada[-1] = {**revisada[-1], "close": revisada[-1]["close"] + 0.5}
    await _ingerir(_deriv(revisada, ahora=LUNES), write=True)
    antes = ruta_revisiones("cryBTCUSD").read_bytes()

    res = await _ingerir(_deriv(revisada, ahora=LUNES), write=True)

    assert ruta_revisiones("cryBTCUSD").read_bytes() == antes
    r = next(r for r in res.simbolos if r.simbolo == "cryBTCUSD")
    assert (r.revisiones_nuevas, r.revisiones_total) == (0, 1)


def test_una_vela_invalida_no_se_escribe_y_la_corrida_sale_uno(monkeypatch, capsys):
    velas = _serie(10, desde=LUNES - 10 * D)
    velas[3] = {**velas[3], "high": 1.0}
    monkeypatch.setenv("DERIV_APP_ID", "1")
    d = _deriv(velas, ahora=LUNES)
    assert main(["--entorno", "demo", "--write"], connector=d.connector) == 1
    assert len(leer_velas("cryBTCUSD")) == 9


# ═══ Profundidad usable ═══════════════════════════════════════════════════

def _epochs(dias):
    return [int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()) for d in dias]


def test_cripto_un_dia_faltante_es_un_hueco_y_corta_la_historia_usable():
    """La historia usable es el tramo FINAL, no el más largo: un backtest
    que corre hasta hoy no puede saltar el hueco. Los dos números difieren
    a propósito -- con un hueco en el medio, serían iguales y el test no
    distinguiría uno de otro (lo encontró un mutante que sobrevivía)."""
    dias = [date(2026, 9, d) for d in range(1, 21) if d != 15]
    p = profundidad(_epochs(dias), Calendario.CONTINUO)
    assert p.huecos == ["2026-09-15"]
    assert p.barras_totales == 19
    assert (p.tramo_sin_huecos_mas_largo, p.historia_usable) == (14, 5)


def test_en_calendario_habil_el_fin_de_semana_no_es_hueco():
    """Oro: sin velas sábado y domingo. Contar el fin de semana como hueco
    dejaría la historia usable en cinco barras."""
    dias = [date(2026, 9, d) for d in range(1, 29) if date(2026, 9, d).weekday() < 5]
    p = profundidad(_epochs(dias), Calendario.HABIL)
    assert p.huecos == []
    assert p.historia_usable == len(dias)


def test_en_calendario_habil_un_feriado_si_es_hueco():
    """Conservador a propósito: el módulo no conoce los feriados de cada
    mercado. Lo dice el docstring y lo fija este test."""
    dias = [date(2026, 9, d) for d in range(1, 29)
            if date(2026, 9, d).weekday() < 5 and d != 7]
    assert profundidad(_epochs(dias), Calendario.HABIL).huecos == ["2026-09-07"]


def test_la_rejilla_soportada_sale_de_la_historia_usable_y_no_de_la_total():
    n = historia_requerida(320)
    dias = [date(2010, 1, 1).fromordinal(date(2010, 1, 1).toordinal() + k)
            for k in range(n + 50) if k != 60]
    p = profundidad(_epochs(dias), Calendario.CONTINUO)
    assert p.barras_totales >= n
    assert p.historia_usable < n
    assert 320 not in p.rejilla_soportada


# ═══ leer_velas ═══════════════════════════════════════════════════════════

def _escribir(lineas):
    ruta_serie("X").parent.mkdir(parents=True)
    ruta_serie("X").write_text("".join(json.dumps(l) + "\n" for l in lineas))


def test_leer_velas_devuelve_el_esquema_estricto():
    _escribir([{"epoch": LUNES, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}])
    df = leer_velas("X")
    assert dict(df.schema) == ESQUEMA_VELAS
    assert list(df.columns) == list(ESQUEMA_VELAS)


@pytest.mark.parametrize("fila", [
    {"epoch": LUNES, "open": "1.0", "high": 2.0, "low": 0.5, "close": 1.5},
    {"epoch": LUNES, "open": 1, "high": 2.0, "low": 0.5, "close": 1.5},
    {"epoch": True, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
    {"epoch": float(LUNES), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
    {"epoch": LUNES, "open": 1.0, "high": 2.0, "low": 0.5},
    {"epoch": LUNES, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "x": 1},
])
def test_leer_velas_falla_ante_un_tipo_que_no_coincide(fila):
    """Cada uno de estos casos lo convierte polars en silencio con
    strict=True (medido). Acá tiene que fallar."""
    _escribir([fila])
    with pytest.raises(TipoInvalidoError):
        leer_velas("X")


def test_leer_velas_sin_serie_devuelve_vacio_con_el_esquema():
    df = leer_velas("NADA")
    assert df.is_empty() and dict(df.schema) == ESQUEMA_VELAS


def test_leer_velas_deduplica_por_epoca_quedandose_con_la_ultima():
    base = {"epoch": LUNES, "open": 1.0, "high": 2.0, "low": 0.5}
    _escribir([{**base, "close": 1.0}, {**base, "close": 1.5}])
    assert leer_velas("X")["close"].to_list() == [1.5]


# ═══ Símbolos y CLI ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_los_simbolos_salen_de_active_symbols_y_sin_control_no_se_escribe():
    d = _deriv(_serie(10), ahora=LUNES, activos=[ACTIVOS[1]])
    res = await _ingerir(d, write=True)
    assert res.invalido
    assert not ruta_serie("cryBTCUSD").exists()
    assert d.de_tipo("ticks_history") == []


def test_el_cli_exige_entorno():
    with pytest.raises(SystemExit):
        main([])
