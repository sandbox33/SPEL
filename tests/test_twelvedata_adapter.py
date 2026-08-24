"""
tests/test_twelvedata_adapter.py
=================================
Cobertura de TwelveDataAdapter. Ningún test offline toca la red: el
transporte se inyecta con httpx.MockTransport, no se monkeypatchea httpx
por dentro.

PROCEDENCIA DE LOS FIXTURES — tres son capturas reales, dos son sintéticos
a propósito, y la diferencia está marcada en cada uno.

CAPTURAS REALES (`api.twelvedata.com/time_series`, 2026-08-23):
`RESPUESTA_EURUSD_1DAY`, `RESPUESTA_BTCUSD_1DAY`, `RESPUESTA_AAPL_1DAY`.
Valores literales, sin retocar.

HALLAZGO QUE TRAJERON ESAS CAPTURAS, y es el motivo por el que valía la pena
conseguirlas: **BTC/USD NO trae `volume`.** La versión anterior de este
archivo suponía que sí —"forex no, cripto y acciones sí"— y la captura real
lo desmiente: de los tres instrumentos, el único que trae volumen es AAPL.
El adapter no necesitó ni un cambio, y eso no es suerte: `volume_available`
se deriva de si la clave VINO en la respuesta, nunca de una regla por clase
de activo (ver `decision-log.md`, PR-3, Decisión 3). Una regla declarada
habría marcado BTC con volumen disponible y el relleno 0.0 habría entrado al
pipeline como si fuera un dato.

SINTÉTICOS, marcados como tales y con motivo:
  - `RESPUESTA_FOREX_CON_VOLUMEN_SINTETICA` — contrafáctico deliberado. Su
    valor está justamente en que no puede existir en la realidad observada:
    prueba que la derivación de `volume_available` mira la respuesta y no el
    tipo de símbolo. Una regla por clase de activo lo rompería.
  - `RESPUESTA_BTCUSD_1H_SINTETICA` — las tres capturas disponibles son
    diarias, y hacen falta dos tests sobre el comportamiento intradía. Lo
    que esos dos verifican es la PETICIÓN que se emite y el flag que decide
    el `timeframe`, no los valores del payload.

El test `live` del final es el que toca la red de verdad: corre solo con
TWELVEDATA_API_KEY presente. Hasta que corra una vez, lo intradía y el
manejo del cliente httpx propio siguen sin confirmar contra la API real.
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
#  Capturas REALES de la API — 2026-08-23, literales, sin retocar
# ══════════════════════════════════════════════════════════════════════════

#: EUR/USD 1day — Physical Currency, SIN volume.
RESPUESTA_EURUSD_1DAY = {
    "meta": {"symbol": "EUR/USD", "interval": "1day",
             "currency_base": "Euro", "currency_quote": "US Dollar",
             "type": "Physical Currency"},
    "values": [
        {"datetime": "2026-08-21", "open": "1.1679", "high": "1.17108",
         "low": "1.16694", "close": "1.16764"},
        {"datetime": "2026-08-22", "open": "1.16764", "high": "1.16933",
         "low": "1.16715", "close": "1.16788"},
    ],
    "status": "ok",
}

#: BTC/USD 1day — exchange=Binance. Digital Currency, SIN volume.
#: Desmiente el supuesto anterior de que cripto trae volumen: de los tres
#: instrumentos capturados, el único con `volume` es AAPL. Ver el docstring
#: del módulo — el adapter no necesitó cambios porque nunca dependió de esa
#: suposición.
RESPUESTA_BTCUSD_1DAY = {
    "meta": {"symbol": "BTC/USD", "interval": "1day",
             "currency_base": "Bitcoin", "currency_quote": "US Dollar",
             "exchange": "Binance", "type": "Digital Currency"},
    "values": [
        {"datetime": "2026-08-21", "open": "73027.02", "high": "79500",
         "low": "73027.02", "close": "78338.03"},
        {"datetime": "2026-08-22", "open": "78338.03", "high": "78828.15",
         "low": "76500", "close": "77074.93"},
    ],
    "status": "ok",
}

#: AAPL 1day — Common Stock NASDAQ/XNGS, CON volume.
RESPUESTA_AAPL_1DAY = {
    "meta": {"symbol": "AAPL", "interval": "1day", "currency": "USD",
             "exchange_timezone": "America/New_York", "exchange": "NASDAQ",
             "mic_code": "XNGS", "type": "Common Stock"},
    "values": [
        {"datetime": "2026-08-19", "open": "310.14001", "high": "319.28000",
         "low": "309.60001", "close": "316.82999", "volume": "50505600"},
        {"datetime": "2026-08-20", "open": "317.45999", "high": "320.28000",
         "low": "310.64999", "close": "311.29999", "volume": "40959200"},
    ],
    "status": "ok",
}


# ══════════════════════════════════════════════════════════════════════════
#  Fixtures SINTÉTICOS — inventados a propósito, cada uno con su motivo
# ══════════════════════════════════════════════════════════════════════════

#: CONTRAFÁCTICO DELIBERADO: un par de forex que sí trae `volume`. Con las
#: capturas reales en la mano, ningún instrumento se comporta así — y ese es
#: exactamente el punto. Si `volume_available` se derivara del tipo de
#: símbolo ("forex no trae volumen") en vez de la respuesta, este caso daría
#: False y el 999 se perdería. Es el único fixture cuyo valor depende de que
#: NO sea real.
RESPUESTA_FOREX_CON_VOLUMEN_SINTETICA = {
    "meta": {"symbol": "EUR/USD", "interval": "1day",
             "currency_base": "Euro", "currency_quote": "US Dollar",
             "type": "Physical Currency"},
    "values": [
        {"datetime": "2026-08-21", "open": "1.1679", "high": "1.17108",
         "low": "1.16694", "close": "1.16764", "volume": "999"},
    ],
    "status": "ok",
}

#: SINTÉTICO POR NECESIDAD: las tres capturas disponibles son diarias, y hay
#: dos comportamientos intradía que probar (se manda `timezone`, y
#: `timestamp_is_convention` queda en False). Los dos dependen del argumento
#: `timeframe` y de la petición emitida, no de los valores del payload — así
#: que los OHLC de abajo son los de la captura real de BTC/USD y lo único
#: inventado es el `datetime` con hora. Se reemplaza por una captura
#: intradía real en cuanto haya una.
RESPUESTA_BTCUSD_1H_SINTETICA = {
    "meta": {"symbol": "BTC/USD", "interval": "1h",
             "currency_base": "Bitcoin", "currency_quote": "US Dollar",
             "exchange": "Binance", "type": "Digital Currency"},
    "values": [
        {"datetime": "2026-08-22 08:00:00", "open": "78338.03",
         "high": "78828.15", "low": "76500", "close": "77074.93"},
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
    assert df["close"].iloc[-1] == pytest.approx(1.16788)
    assert str(df["timestamp"].dt.tz) == "UTC"


async def test_parseo_cripto_btcusd():
    """La captura real de BTC/USD (Binance) NO trae `volume` — el 0.0 de la
    columna es relleno, y por eso queda marcado como tal."""
    df = await adapter_con(RESPUESTA_BTCUSD_1DAY).fetch_ohlcv("BTCUSD", "1d", 2)

    assert len(df) == 2
    assert df["close"].iloc[-1] == pytest.approx(77074.93)
    assert (df["volume"] == 0.0).all()
    assert df.attrs["volume_available"] is False


async def test_parseo_accion_aapl():
    df = await adapter_con(RESPUESTA_AAPL_1DAY).fetch_ohlcv("AAPL", "1d", 2)

    assert len(df) == 2
    assert df["close"].iloc[-1] == pytest.approx(311.29999)
    assert df["volume"].iloc[-1] == pytest.approx(40959200)


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
    por clase de activo. Se prueba con el fixture CONTRAFÁCTICO (forex con
    volumen, sintético a propósito) porque es el caso que una regla
    declarada rompería: con las capturas reales en la mano, el único
    instrumento con volumen es AAPL, así que una regla del tipo 'forex no
    trae volumen' pasaría los tests reales y fallaría acá.

    Que AAPL parsea su volumen real ya lo cubre test_parseo_accion_aapl."""
    df = await adapter_con(RESPUESTA_FOREX_CON_VOLUMEN_SINTETICA).fetch_ohlcv("EURUSD", "1d", 1)

    assert df.attrs["volume_available"] is True
    assert df["volume"].iloc[0] == pytest.approx(999)


async def test_timestamp_is_convention_true_en_diario():
    """La barra diaria trae solo la fecha: su timestamp es la convención del
    día de mercado, no el instante en que ocurrió nada."""
    df = await adapter_con(RESPUESTA_AAPL_1DAY).fetch_ohlcv("AAPL", "1d", 2)

    assert df.attrs["timestamp_is_convention"] is True


async def test_timestamp_is_convention_false_en_intradia():
    df = await adapter_con(RESPUESTA_BTCUSD_1H_SINTETICA).fetch_ohlcv("BTCUSD", "1h", 1)

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
    await adapter_con(RESPUESTA_BTCUSD_1H_SINTETICA, captura=captura).fetch_ohlcv("BTCUSD", "1h", 1)

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
    assert df["close"].iloc[-1] == pytest.approx(1.16788)


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
    """Lo que ningún fixture puede probar, por real que sea: que el camino
    completo funciona contra la API viva — el cliente httpx propio (el que
    el adapter abre y cierra solo, que los tests offline nunca ejercitan
    porque inyectan el suyo), la autenticación aceptada de verdad, y que la
    forma de la respuesta sigue siendo la de las capturas del 2026-08-23."""
    adapter = TwelveDataAdapter(api_key=os.environ["TWELVEDATA_API_KEY"])

    df = await adapter.fetch_ohlcv("EURUSD", "1d", 5)

    assert list(df.columns) == list(REQUIRED_COLUMNS)
    assert not df.empty
    assert df["timestamp"].is_monotonic_increasing
    assert str(df["timestamp"].dt.tz) == "UTC"
    # Confirmado en la captura del 2026-08-23: forex no trae volumen.
    assert df.attrs["volume_available"] is False
    assert df.attrs["timestamp_is_convention"] is True
