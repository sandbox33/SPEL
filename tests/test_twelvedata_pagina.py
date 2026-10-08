"""
tests/test_twelvedata_pagina.py
=================================
`TwelveDataAdapter.fetch_pagina` y XAUUSD en el mapa (brief del Admin del
06-oct-2026 (4), punto 1a, con su autorización para editar el adapter).
Offline: el transporte es un httpx.MockTransport.

Los valores de XAU/USD de abajo son SINTÉTICOS: lo que se prueba es la
petición emitida, el cierre por la hora del servidor y el 404 del fondo.
"""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from ingestion.adapters import (
    TWELVEDATA_MAX_OUTPUTSIZE,
    TWELVEDATA_SIN_DATOS_400,
    AdapterConnectionError,
    AdapterDataError,
    AdapterException,
    TwelveDataAdapter,
)

KEY = "clave-falsa-de-pagina"
#: La hora del servidor en la cabecera Date: 2026-10-06 10:12:30 UTC.
DATE = "Tue, 06 Oct 2026 10:12:30 GMT"

XAU_5MIN = {
    "meta": {"symbol": "XAU/USD", "interval": "5min", "exchange_timezone": None},
    "values": [
        {"datetime": "2026-10-06 10:10:00", "open": "2650.1", "high": "2651.0",
         "low": "2649.9", "close": "2650.5"},   # abierta: cierra 10:15
        {"datetime": "2026-10-06 10:05:00", "open": "2649.0", "high": "2650.3",
         "low": "2648.8", "close": "2650.1"},   # cerró 10:10
        {"datetime": "2026-10-06 10:00:00", "open": "2648.5", "high": "2649.2",
         "low": "2648.1", "close": "2649.0"},
    ],
    "status": "ok",
}


def _adapter(cuerpo, *, status=200, cabeceras=None, captura=None):
    cab = {"Date": DATE, "api-credits-used": "3", "api-credits-left": "797"}
    if cabeceras is not None:
        cab = cabeceras

    def handler(req: httpx.Request) -> httpx.Response:
        if captura is not None:
            captura.append(req)
        return httpx.Response(status, content=json.dumps(cuerpo).encode(),
                              headers={**cab, "Content-Type": "application/json"})
    return TwelveDataAdapter(api_key=KEY, client=httpx.AsyncClient(
        transport=httpx.MockTransport(handler)))


async def test_xauusd_esta_en_el_mapa():
    assert "XAUUSD" in TwelveDataAdapter.SUPPORTED_SYMBOLS
    captura = []
    df = await _adapter(XAU_5MIN, captura=captura).fetch_ohlcv("XAUUSD", "5m", 3)
    assert captura[0].url.params["symbol"] == "XAU/USD"
    assert len(df) >= 2


async def test_la_pagina_pide_rango_utc_y_la_key_en_el_header():
    captura = []
    await _adapter(XAU_5MIN, captura=captura).fetch_pagina(
        "XAUUSD", "5m", start_date="2020-03-16 00:00:00", end_date="2026-10-06 10:12:30")
    req = captura[0]
    assert dict(req.url.params) == {
        "symbol": "XAU/USD", "interval": "5min", "outputsize": str(TWELVEDATA_MAX_OUTPUTSIZE),
        "timezone": "UTC", "start_date": "2020-03-16 00:00:00",
        "end_date": "2026-10-06 10:12:30"}
    assert req.headers["Authorization"] == f"apikey {KEY}"
    assert KEY not in str(req.url)


async def test_sin_fechas_no_las_manda():
    captura = []
    await _adapter(XAU_5MIN, captura=captura).fetch_pagina("XAUUSD", "5m")
    assert "start_date" not in captura[0].url.params and "end_date" not in captura[0].url.params


async def test_la_vela_abierta_se_decide_con_la_hora_del_servidor():
    p = await _adapter(XAU_5MIN).fetch_pagina("XAUUSD", "5m")
    assert p.abiertas_descartadas == 1
    assert [str(t) for t in p.velas["timestamp"]] == [
        "2026-10-06 10:00:00+00:00", "2026-10-06 10:05:00+00:00"]
    assert p.vacia is False
    assert p.hora_servidor == 1791281550, "Tue, 06 Oct 2026 10:12:30 GMT"


async def test_sin_cabecera_date_no_se_decide_nada():
    with pytest.raises(AdapterDataError, match="Date"):
        await _adapter(XAU_5MIN, cabeceras={}).fetch_pagina("XAUUSD", "5m")


async def test_sha256_y_creditos():
    p = await _adapter(XAU_5MIN).fetch_pagina("XAUUSD", "5m")
    assert p.creditos == {"api-credits-used": "3", "api-credits-left": "797"}
    assert p.sha256 == hashlib.sha256(json.dumps(XAU_5MIN).encode()).hexdigest()


async def test_el_404_del_fondo_es_una_pagina_vacia():
    cuerpo = {"code": 404, "message": "Data not found", "status": "error"}
    p = await _adapter(cuerpo).fetch_pagina("XAUUSD", "5m", end_date="2018-06-30")
    assert p.vacia is True and p.velas.empty


@pytest.mark.parametrize("mensaje", ["**symbol** not found", "not available with your plan"])
async def test_otro_404_sigue_siendo_error(mensaje):
    cuerpo = {"code": 404, "message": mensaje, "status": "error"}
    with pytest.raises(AdapterDataError):
        await _adapter(cuerpo).fetch_pagina("XAUUSD", "5m")


#: El 400 del run 37721686439 (08-oct-2026), al pedir la página con
#: end_date 2020-03-16 01:09 después de la última página de XAU/USD 5min. El
#: log guarda el `code` y el mensaje LITERAL (el adapter los escribe como
#: "error 400: <mensaje>"); el cuerpo JSON entero no quedó en el log. La
#: clave "status" es la del sobre de error que mandan las demás respuestas
#: de error de TwelveData de este archivo; la regla no depende de ella (ver
#: test_el_400_sin_datos_no_depende_de_status).
MENSAJE_400_RUN_37721686439 = ("No data is available on the specified dates. "
                               "Try setting different start/end dates.")
RESPUESTA_400_SIN_DATOS = {"code": 400, "message": MENSAJE_400_RUN_37721686439,
                           "status": "error"}


def test_el_prefijo_es_el_comienzo_del_mensaje_del_run():
    assert MENSAJE_400_RUN_37721686439.startswith(TWELVEDATA_SIN_DATOS_400)
    assert TWELVEDATA_SIN_DATOS_400 == "No data is available on the specified dates"


@pytest.mark.parametrize("http", [400, 200])
async def test_el_400_sin_datos_es_una_pagina_vacia(http):
    """El log registra el code del cuerpo, no el status HTTP: se prueban
    los dos."""
    p = await _adapter(RESPUESTA_400_SIN_DATOS, status=http).fetch_pagina(
        "XAUUSD", "5m", start_date="2020-03-16 00:00:00", end_date="2020-03-16 01:09:00")
    assert p.vacia is True and p.velas.empty and p.filas_devueltas == 0
    assert p.motivo == f"400: {MENSAJE_400_RUN_37721686439}"


async def test_el_400_sin_datos_no_depende_de_status():
    cuerpo = {k: v for k, v in RESPUESTA_400_SIN_DATOS.items() if k != "status"}
    assert (await _adapter(cuerpo).fetch_pagina("XAUUSD", "5m")).vacia is True


@pytest.mark.parametrize("mensaje", [
    "Invalid **start_date** parameter",
    "no data is available on the specified dates. Try setting different start/end dates.",
    " No data is available on the specified dates.",
    "Error: No data is available on the specified dates.",
    "No data is available for XAU/USD on your plan",
    "",
])
async def test_otro_400_sigue_siendo_error(mensaje):
    cuerpo = {"code": 400, "message": mensaje, "status": "error"}
    with pytest.raises(AdapterConnectionError):
        await _adapter(cuerpo).fetch_pagina("XAUUSD", "5m")


@pytest.mark.parametrize("code", [429, 401, 500, "400"])
async def test_el_mensaje_sin_datos_con_otro_code_sigue_siendo_error(code):
    cuerpo = {**RESPUESTA_400_SIN_DATOS, "code": code}
    with pytest.raises(AdapterException):
        await _adapter(cuerpo).fetch_pagina("XAUUSD", "5m")


async def test_el_404_vacio_lleva_su_motivo():
    cuerpo = {"code": 404, "message": "Data not found", "status": "error"}
    assert (await _adapter(cuerpo).fetch_pagina("XAUUSD", "5m")).motivo == "404: Data not found"


async def test_las_filas_devueltas_cuentan_la_vela_abierta():
    """Una página llena cuya vela más reciente no cerró sigue siendo llena."""
    p = await _adapter(XAU_5MIN).fetch_pagina("XAUUSD", "5m", outputsize=3)
    assert len(p.velas) == 2 and p.abiertas_descartadas == 1
    assert p.filas_devueltas == 3


async def test_1day_no_lleva_timezone():
    captura = []
    cuerpo = {"values": [{"datetime": "2026-10-01", "open": "1", "high": "2", "low": "0.5",
                          "close": "1.5"}], "status": "ok"}
    await _adapter(cuerpo, captura=captura).fetch_pagina("XAUUSD", "1d")
    assert "timezone" not in captura[0].url.params


@pytest.mark.parametrize("simbolo, tf, n", [("XAGUSD", "5m", 10), ("XAUUSD", "7m", 10),
                                            ("XAUUSD", "5m", 0), ("XAUUSD", "5m", 5001)])
async def test_uso_invalido_no_toca_la_red(simbolo, tf, n):
    captura = []
    with pytest.raises(ValueError):
        await _adapter(XAU_5MIN, captura=captura).fetch_pagina(simbolo, tf, outputsize=n)
    assert captura == []
