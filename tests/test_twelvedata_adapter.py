"""
tests/test_twelvedata_adapter.py
=================================
Cobertura de TwelveDataAdapter. Ningún test offline toca la red: el
transporte se inyecta con httpx.MockTransport, no se monkeypatchea httpx
por dentro.

⚠️ PROCEDENCIA DE LOS FIXTURES — LEER ANTES DE CONFIAR EN ESTOS LITERALES.

Estas tres respuestas NO son capturas reales de la API. Están construidas a
partir de la FORMA documentada del endpoint `time_series` (bloque `meta`,
lista `values` con `datetime`/`open`/`high`/`low`/`close` y `volume` solo en
algunos instrumentos, `status`), y los valores numéricos son inventados.

Se dejan así, marcados, en vez de rotularlos como capturas reales, porque el
entorno donde se escribió este patch no pudo hacer ninguna de las dos cosas
que harían falta para capturarlas: no hay `TWELVEDATA_API_KEY` configurada, y
`api.twelvedata.com` está fuera de la política de red del sandbox (el gateway
responde 403 al CONNECT — denegación de política, no error del servidor).
Inventar valores y firmarlos como "respuesta real del 23 ago" sería
exactamente el patrón que este proyecto prohíbe.

QUÉ CAMBIA Y QUÉ NO cuando se peguen las capturas reales: lo que estos tests
verifican es la FORMA (qué campos se leen, cómo se traduce cada error, qué
se manda en la petición), y eso no depende de los números. Reemplazar los
literales por las capturas reales debería dejar la suite en verde sin tocar
una sola aserción; si algo se rompe ahí, el hallazgo es real y hay que
mirarlo. Los dos únicos supuestos de forma que una captura real podría
desmentir están marcados con `SUPUESTO DE FORMA` más abajo.

El test `live` del final es el que sí toca la red: corre solo con
TWELVEDATA_API_KEY presente, y es el que cierra este hueco de verdad.
"""

from __future__ import annotations

import os

import httpx
import pandas as pd
import pytest

from ingestion.adapters import (
    AdapterAuthError,
    AdapterConnectionError,
    AdapterDataError,
    REQUIRED_COLUMNS,
    TWELVEDATA_ENDPOINT,
    TwelveDataAdapter,
)

API_KEY_FALSA = "clave-de-prueba-que-no-debe-filtrarse"


# ══════════════════════════════════════════════════════════════════════════
#  Fixtures — forma documentada del endpoint time_series (ver ⚠️ arriba)
# ══════════════════════════════════════════════════════════════════════════

#: Forex diario. SUPUESTO DE FORMA: los pares de forex no traen `volume`.
RESPUESTA_EURUSD_1DAY = {
    "meta": {
        "symbol": "EUR/USD",
        "interval": "1day",
        "currency_base": "Euro",
        "currency_quote": "US Dollar",
        "type": "Physical Currency",
    },
    "values": [
        {"datetime": "2026-08-20", "open": "1.16542", "high": "1.16893",
         "low": "1.16401", "close": "1.16775"},
        {"datetime": "2026-08-21", "open": "1.16775", "high": "1.17012",
         "low": "1.16688", "close": "1.16934"},
    ],
    "status": "ok",
}

#: Cripto intradía. SUPUESTO DE FORMA: cripto sí trae `volume`.
RESPUESTA_BTCUSD_1H = {
    "meta": {
        "symbol": "BTC/USD",
        "interval": "1h",
        "currency_base": "Bitcoin",
        "currency_quote": "US Dollar",
        "exchange": "Coinbase Pro",
        "type": "Digital Currency",
    },
    "values": [
        {"datetime": "2026-08-21 08:00:00", "open": "63120.40",
         "high": "63480.00", "low": "62990.15", "close": "63301.75",
         "volume": "142.38"},
        {"datetime": "2026-08-21 09:00:00", "open": "63301.75",
         "high": "63655.20", "low": "63250.00", "close": "63588.90",
         "volume": "118.62"},
    ],
    "status": "ok",
}

#: Acción diaria — trae `volume` entero grande.
RESPUESTA_AAPL_1DAY = {
    "meta": {
        "symbol": "AAPL",
        "interval": "1day",
        "currency": "USD",
        "exchange_timezone": "America/New_York",
        "exchange": "NASDAQ",
        "mic_code": "XNGS",
        "type": "Common Stock",
    },
    "values": [
        {"datetime": "2026-08-20", "open": "226.05", "high": "228.34",
         "low": "225.41", "close": "227.76", "volume": "38412900"},
        {"datetime": "2026-08-21", "open": "227.90", "high": "229.15",
         "low": "227.02", "close": "228.44", "volume": "41027300"},
    ],
    "status": "ok",
}


def adapter_con(respuesta, status_code: int = 200, captura: list | None = None):
    """Arma un TwelveDataAdapter cuyo transporte devuelve `respuesta` sin
    salir a la red. Si se pasa `captura`, cada httpx.Request emitido se
    apila ahí para poder assertar QUÉ se mandó, no solo qué volvió."""
    def handler(request: httpx.Request) -> httpx.Response:
        if captura is not None:
            captura.append(request)
        return httpx.Response(status_code, json=respuesta)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TwelveDataAdapter(api_key=API_KEY_FALSA, client=client)


# ══════════════════════════════════════════════════════════════════════════
#  Parseo de los tres instrumentos verificados
# ══════════════════════════════════════════════════════════════════════════

async def test_parseo_forex_eurusd():
    df = await adapter_con(RESPUESTA_EURUSD_1DAY).fetch_ohlcv("EURUSD", "1d", 2)

    assert list(df.columns) == list(REQUIRED_COLUMNS)
    assert len(df) == 2
    assert df["close"].iloc[-1] == pytest.approx(1.16934)
    assert str(df["timestamp"].dt.tz) == "UTC"


async def test_parseo_cripto_btcusd():
    df = await adapter_con(RESPUESTA_BTCUSD_1H).fetch_ohlcv("BTCUSD", "1h", 2)

    assert len(df) == 2
    assert df["close"].iloc[-1] == pytest.approx(63588.90)
    assert df["volume"].iloc[-1] == pytest.approx(118.62)


async def test_parseo_accion_aapl():
    df = await adapter_con(RESPUESTA_AAPL_1DAY).fetch_ohlcv("AAPL", "1d", 2)

    assert len(df) == 2
    assert df["close"].iloc[-1] == pytest.approx(228.44)
    assert df["volume"].iloc[-1] == pytest.approx(41027300)


# ══════════════════════════════════════════════════════════════════════════
#  Metadata de calidad — observada de la respuesta, no declarada por tipo
# ══════════════════════════════════════════════════════════════════════════

async def test_volume_available_false_cuando_la_fuente_no_lo_trae():
    """Forex no trae volumen: el 0.0 de la columna es relleno y hay que
    poder distinguirlo de un cero real."""
    df = await adapter_con(RESPUESTA_EURUSD_1DAY).fetch_ohlcv("EURUSD", "1d", 2)

    assert df.attrs["volume_available"] is False
    assert (df["volume"] == 0.0).all()


async def test_volume_available_true_se_deriva_de_lo_observado_no_del_simbolo():
    """La bandera sale de si la clave VINO en la respuesta, no de una regla
    'forex no, acciones sí'. Por eso el caso True se prueba con un símbolo
    de FOREX que sí trae volumen: una regla por tipo de instrumento daría
    False acá y se rompería en silencio. (Que AAPL parsea su volumen real
    ya lo cubre test_parseo_accion_aapl.)"""
    respuesta = {
        "meta": {"symbol": "EUR/USD", "interval": "1day"},
        "values": [
            {"datetime": "2026-08-20", "open": "1.1", "high": "1.2",
             "low": "1.0", "close": "1.15", "volume": "999"},
        ],
        "status": "ok",
    }
    df = await adapter_con(respuesta).fetch_ohlcv("EURUSD", "1d", 1)

    assert df.attrs["volume_available"] is True
    assert df["volume"].iloc[0] == pytest.approx(999)


async def test_timestamp_is_convention_true_en_diario():
    """La barra diaria trae solo la fecha: su timestamp es la convención del
    día de mercado, no el instante en que ocurrió nada."""
    df = await adapter_con(RESPUESTA_AAPL_1DAY).fetch_ohlcv("AAPL", "1d", 2)

    assert df.attrs["timestamp_is_convention"] is True


async def test_timestamp_is_convention_false_en_intradia():
    df = await adapter_con(RESPUESTA_BTCUSD_1H).fetch_ohlcv("BTCUSD", "1h", 2)

    assert df.attrs["timestamp_is_convention"] is False


# ══════════════════════════════════════════════════════════════════════════
#  La petición que se emite — la key, el orden, la zona
# ══════════════════════════════════════════════════════════════════════════

async def test_la_key_viaja_en_el_header_authorization():
    captura: list[httpx.Request] = []
    await adapter_con(RESPUESTA_EURUSD_1DAY, captura=captura).fetch_ohlcv("EURUSD", "1d", 2)

    assert captura[0].headers["Authorization"] == f"apikey {API_KEY_FALSA}"


async def test_la_key_nunca_aparece_en_la_url():
    """Un ?apikey=... termina escrito en logs de proxy, historiales de shell
    y en los mensajes de error de httpx, que incluyen la URL. El header no
    aparece en ninguno de esos lugares."""
    captura: list[httpx.Request] = []
    await adapter_con(RESPUESTA_EURUSD_1DAY, captura=captura).fetch_ohlcv("EURUSD", "1d", 2)

    url = str(captura[0].url)
    assert API_KEY_FALSA not in url
    assert "apikey" not in url.lower()
    assert url.startswith(TWELVEDATA_ENDPOINT)


async def test_se_pide_order_asc_explicito():
    """El default del proveedor es DESC y el contrato del módulo exige
    ascendente."""
    captura: list[httpx.Request] = []
    await adapter_con(RESPUESTA_EURUSD_1DAY, captura=captura).fetch_ohlcv("EURUSD", "1d", 2)

    assert captura[0].url.params["order"] == "ASC"


async def test_diario_no_manda_timezone():
    """La barra diaria viene con fecha sin hora: no hay nada que
    reinterpretar, y mandar una zona igual invita a que el proveedor corra
    la fecha un día."""
    captura: list[httpx.Request] = []
    await adapter_con(RESPUESTA_AAPL_1DAY, captura=captura).fetch_ohlcv("AAPL", "1d", 2)

    assert "timezone" not in captura[0].url.params


async def test_intradia_si_manda_timezone_utc():
    captura: list[httpx.Request] = []
    await adapter_con(RESPUESTA_BTCUSD_1H, captura=captura).fetch_ohlcv("BTCUSD", "1h", 2)

    assert captura[0].url.params["timezone"] == "UTC"


async def test_respuesta_desc_se_reordena_localmente():
    """Pedir order=ASC no es lo mismo que garantizarlo: si el proveedor
    ignora el parámetro, el reordenado local cumple el contrato igual."""
    desc = {
        "meta": {"symbol": "EUR/USD", "interval": "1day"},
        "values": list(reversed(RESPUESTA_EURUSD_1DAY["values"])),
        "status": "ok",
    }
    df = await adapter_con(desc).fetch_ohlcv("EURUSD", "1d", 2)

    assert df["timestamp"].is_monotonic_increasing
    assert df["close"].iloc[-1] == pytest.approx(1.16934)


# ══════════════════════════════════════════════════════════════════════════
#  Vocabulario de errores del proveedor -> vocabulario del módulo
# ══════════════════════════════════════════════════════════════════════════

async def test_error_401_es_auth_error():
    cuerpo = {"code": 401, "message": "Invalid API key", "status": "error"}
    with pytest.raises(AdapterAuthError, match="401"):
        await adapter_con(cuerpo).fetch_ohlcv("EURUSD", "1d", 2)


async def test_error_429_es_connection_error():
    """Cuota agotada es transitorio por definición (la ventana se renueva),
    así que va como ConnectionError para que el retry y el fallback de
    AdapterChain lo traten como tal."""
    cuerpo = {"code": 429, "message": "You have run out of API credits",
              "status": "error"}
    with pytest.raises(AdapterConnectionError, match="429"):
        await adapter_con(cuerpo).fetch_ohlcv("EURUSD", "1d", 2)


async def test_error_404_con_plan_es_limite_de_cuenta():
    """El MISMO código para dos causas distintas. Esta es la que dice que el
    símbolo existe pero la cuenta no lo cubre."""
    cuerpo = {
        "code": 404,
        "message": "**symbol** not available with your plan. Consider upgrading.",
        "status": "error",
    }
    with pytest.raises(AdapterDataError, match="plan de esta cuenta"):
        await adapter_con(cuerpo).fetch_ohlcv("EURUSD", "1d", 2)


async def test_error_404_sin_plan_es_simbolo_no_encontrado():
    cuerpo = {"code": 404, "message": "**symbol** not found", "status": "error"}
    with pytest.raises(AdapterDataError, match="no encontrado"):
        await adapter_con(cuerpo).fetch_ohlcv("EURUSD", "1d", 2)


async def test_error_en_el_cuerpo_con_http_200_igual_se_detecta():
    """TwelveData señaliza fallos en el CUERPO con HTTP 200 en muchos casos:
    mirar solo el status HTTP deja pasar el error como si fuera un payload
    bueno."""
    cuerpo = {"code": 401, "message": "Invalid API key", "status": "error"}
    with pytest.raises(AdapterAuthError):
        await adapter_con(cuerpo, status_code=200).fetch_ohlcv("EURUSD", "1d", 2)


async def test_respuesta_sin_values_es_data_error():
    cuerpo = {"meta": {"symbol": "EUR/USD"}, "values": [], "status": "ok"}
    with pytest.raises(AdapterDataError, match="values"):
        await adapter_con(cuerpo).fetch_ohlcv("EURUSD", "1d", 2)


async def test_vela_malformada_es_data_error():
    cuerpo = {
        "meta": {"symbol": "EUR/USD", "interval": "1day"},
        "values": [{"datetime": "2026-08-20", "open": "no-es-un-numero",
                    "high": "1.2", "low": "1.0", "close": "1.15"}],
        "status": "ok",
    }
    with pytest.raises(AdapterDataError, match="campos faltantes o de tipo"):
        await adapter_con(cuerpo).fetch_ohlcv("EURUSD", "1d", 1)


async def test_timeout_es_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("se agotó el tiempo", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = TwelveDataAdapter(api_key=API_KEY_FALSA, client=client)

    with pytest.raises(AdapterConnectionError, match="timeout"):
        await adapter.fetch_ohlcv("EURUSD", "1d", 2)


# ══════════════════════════════════════════════════════════════════════════
#  Errores de uso del llamador — ValueError, no AdapterException
# ══════════════════════════════════════════════════════════════════════════

def test_key_vacia_es_value_error():
    with pytest.raises(ValueError, match="api_key no vacía"):
        TwelveDataAdapter(api_key="")


async def test_simbolo_no_mapeado_es_value_error():
    """XAUUSD entra acá a propósito: no se pudo confirmar que el plan
    gratuito lo cubra, así que no está en el mapa. 'Probablemente esté' no
    es evidencia."""
    with pytest.raises(ValueError, match="no está en el mapeo verificado"):
        await adapter_con(RESPUESTA_EURUSD_1DAY).fetch_ohlcv("XAUUSD", "1d", 2)


async def test_timeframe_no_soportado_es_value_error():
    with pytest.raises(ValueError, match="no reconocido"):
        await adapter_con(RESPUESTA_EURUSD_1DAY).fetch_ohlcv("EURUSD", "5h", 2)


async def test_limit_no_positivo_es_value_error():
    with pytest.raises(ValueError, match="limit debe ser positivo"):
        await adapter_con(RESPUESTA_EURUSD_1DAY).fetch_ohlcv("EURUSD", "1d", 0)


# ══════════════════════════════════════════════════════════════════════════
#  health_check — nunca lanza (contrato de BaseAdapter)
# ══════════════════════════════════════════════════════════════════════════

async def test_health_check_true_con_respuesta_sana():
    assert await adapter_con(RESPUESTA_EURUSD_1DAY).health_check() is True


async def test_health_check_false_no_lanza():
    """Su propósito es poder preguntar '¿está viva la fuente?' sin arriesgar
    una excepción en el intento."""
    cuerpo = {"code": 401, "message": "Invalid API key", "status": "error"}
    assert await adapter_con(cuerpo).health_check() is False


# ══════════════════════════════════════════════════════════════════════════
#  La key nunca se filtra, por ningún camino de error
# ══════════════════════════════════════════════════════════════════════════

async def test_ningun_mensaje_de_error_expone_la_key():
    """Barrido de los cuatro caminos de error en un solo test: ninguno puede
    llevar la credencial al texto de la excepción, que termina en logs."""
    cuerpos = [
        {"code": 401, "message": "Invalid API key", "status": "error"},
        {"code": 429, "message": "out of credits", "status": "error"},
        {"code": 404, "message": "not found", "status": "error"},
        {"meta": {}, "values": [], "status": "ok"},
    ]
    for cuerpo in cuerpos:
        with pytest.raises(Exception) as excinfo:
            await adapter_con(cuerpo).fetch_ohlcv("EURUSD", "1d", 2)
        assert API_KEY_FALSA not in str(excinfo.value), f"filtró en {cuerpo}"


# ══════════════════════════════════════════════════════════════════════════
#  LIVE — el único que toca la red. Cierra el hueco de los fixtures.
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("TWELVEDATA_API_KEY"),
    reason="requiere TWELVEDATA_API_KEY real — no corre en CI ni sin credencial",
)
async def test_live_eurusd_diario_contra_la_api_real():
    """Lo que los fixtures no pueden probar: que la FORMA supuesta coincide
    con la que el proveedor devuelve de verdad. Si este test pasa, los
    supuestos marcados como `SUPUESTO DE FORMA` arriba quedan confirmados."""
    adapter = TwelveDataAdapter(api_key=os.environ["TWELVEDATA_API_KEY"])

    df = await adapter.fetch_ohlcv("EURUSD", "1d", 5)

    assert list(df.columns) == list(REQUIRED_COLUMNS)
    assert not df.empty
    assert df["timestamp"].is_monotonic_increasing
    assert str(df["timestamp"].dt.tz) == "UTC"
    # El supuesto de forma que más importa confirmar: forex sin volumen.
    assert df.attrs["volume_available"] is False
    assert df.attrs["timestamp_is_convention"] is True
