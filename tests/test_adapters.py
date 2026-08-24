"""
tests/test_adapters.py
========================
Cobertura de ingestion/adapters.py. Ningún test toca la red real — el
transporte WebSocket se inyecta como doble de prueba (ver FakeWebSocket),
no se monkeypatchea la librería websockets por dentro.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import asyncio

import pytest

from ingestion.adapters import (
    AdapterAuthError,
    AdapterChain,
    AdapterConnectionError,
    AdapterDataError,
    AdapterException,
    AdapterResult,
    BaseAdapter,
    DerivAdapter,
    REQUIRED_COLUMNS,
    drop_unclosed_candles,
    validate_ohlcv_schema,
)


# ══════════════════════════════════════════════════════════════════════════
#  Doble de prueba del transporte WebSocket — reemplaza websockets.connect
# ══════════════════════════════════════════════════════════════════════════

class FakeWebSocket:
    """
    Simula una conexión websocket. Se configura con una lista de respuestas
    (dicts, se serializan a JSON) que .recv() devuelve en orden — una por
    cada llamada, imitando authorize -> ticks_history.
    """

    def __init__(self, responses: list[dict] | None = None, raise_on_connect: Exception | None = None):
        self._responses = list(responses or [])
        self._raise_on_connect = raise_on_connect
        self.sent_messages: list[dict] = []

    async def __aenter__(self):
        if self._raise_on_connect:
            raise self._raise_on_connect
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def send(self, payload: str) -> None:
        self.sent_messages.append(json.loads(payload))

    async def recv(self) -> str:
        if not self._responses:
            raise AssertionError("FakeWebSocket.recv() llamado más veces de las configuradas")
        return json.dumps(self._responses.pop(0))


def make_connector(responses: list[dict] | None = None, raise_on_connect: Exception | None = None):
    """Factory que imita la firma de websockets.connect(uri) -> async context manager."""
    ws = FakeWebSocket(responses=responses, raise_on_connect=raise_on_connect)

    def _connector(uri: str):
        return ws
    # Mismo objeto lista que ws.sent_messages (no una copia) -- expuesto acá
    # para que los tests puedan assertar qué se mandó de verdad al broker
    # sin necesitar guardar `ws` por separado. Aditivo: no cambia el
    # comportamiento para los tests que ya existían antes de esta sesión.
    _connector.sent_messages = ws.sent_messages
    return _connector


def make_candle(epoch: int, o: float = 1.10, h: float = 1.11, l: float = 1.09, c: float = 1.105) -> dict:
    return {"epoch": epoch, "open": o, "high": h, "low": l, "close": c}


AUTH_OK = {"authorize": {"loginid": "TEST123"}}


def candles_response(candles: list[dict]) -> dict:
    return {"candles": candles}


# ══════════════════════════════════════════════════════════════════════════
#  BaseAdapter — la interfaz abstracta no debe poder instanciarse directo
# ══════════════════════════════════════════════════════════════════════════

def test_base_adapter_no_se_puede_instanciar_directamente():
    with pytest.raises(TypeError):
        BaseAdapter()  # type: ignore[abstract]


# ══════════════════════════════════════════════════════════════════════════
#  Instanciación y validación básica de DerivAdapter
# ══════════════════════════════════════════════════════════════════════════

def test_deriv_adapter_requiere_token_y_app_id():
    with pytest.raises(ValueError):
        DerivAdapter(api_token="", app_id="1089")
    with pytest.raises(ValueError):
        DerivAdapter(api_token="tok", app_id="")


def test_deriv_adapter_instanciacion_correcta():
    adapter = DerivAdapter(api_token="tok", app_id="1089", connector=make_connector())
    assert adapter.source_name == "deriv"
    assert "EURUSD" in adapter.SUPPORTED_SYMBOLS
    assert "15m" in adapter.SUPPORTED_TIMEFRAMES


# ══════════════════════════════════════════════════════════════════════════
#  Índices de Volatilidad (sintéticos) -- agregados esta sesión, Fase 6.
#  Símbolos confirmados contra github.com/deriv-com/deriv-api (fuente
#  oficial), no adivinados por patrón "R_" + número.
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("clean_symbol,deriv_symbol", [
    ("VOL10", "R_10"), ("VOL25", "R_25"), ("VOL50", "R_50"),
    ("VOL75", "R_75"), ("VOL100", "R_100"),
])
def test_indices_volatilidad_estan_en_supported_symbols(clean_symbol, deriv_symbol):
    adapter = DerivAdapter(api_token="tok", app_id="1089", connector=make_connector())
    assert clean_symbol in adapter.SUPPORTED_SYMBOLS


@pytest.mark.asyncio
async def test_fetch_ohlcv_indice_volatilidad_pide_el_simbolo_deriv_correcto():
    """Confirma que VOL50 (nombre limpio de SPEL) pide R_50 (símbolo real
    de Deriv) al broker -- no VOL50 literal, que no existe del lado de
    Deriv y fallaría silenciosamente distinto (o el broker lo rechazaría
    con un error confuso en vez de este test fallando con un mensaje claro
    apuntando exactamente a dónde se rompió el mapeo)."""
    now = datetime.now(timezone.utc)
    candles = [make_candle(int((now - timedelta(minutes=15)).timestamp()))]
    connector = make_connector(responses=[AUTH_OK, candles_response(candles)])
    adapter = DerivAdapter(api_token="tok", app_id="1089", connector=connector)

    await adapter.fetch_ohlcv("VOL50", "5m", limit=1)

    sent = connector.sent_messages  # ver fixture make_connector más abajo
    ticks_history_msg = next(m for m in sent if "ticks_history" in m)
    assert ticks_history_msg["ticks_history"] == "R_50"


def test_indice_volatilidad_no_confirmado_sigue_fuera_del_mapa():
    """Regresión de alcance: R_150/R_250/variantes '(1s)' se ENCONTRARON en
    fuentes de terceros durante la investigación de esta sesión, pero NO
    contra una fuente oficial de Deriv -- deben seguir ausentes hasta que
    se confirmen, no agregarse "por patrón" con los que sí están confirmados."""
    adapter = DerivAdapter(api_token="tok", app_id="1089", connector=make_connector())
    assert "VOL150" not in adapter.SUPPORTED_SYMBOLS
    assert "VOL250" not in adapter.SUPPORTED_SYMBOLS


# ══════════════════════════════════════════════════════════════════════════
#  fetch_ohlcv — camino feliz + validación de esquema del DataFrame
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_fetch_ohlcv_esquema_correcto():
    now = datetime.now(timezone.utc)
    # 3 velas de 15m, todas claramente cerradas (bien en el pasado)
    epochs = [int((now - timedelta(minutes=45)).timestamp()),
              int((now - timedelta(minutes=30)).timestamp()),
              int((now - timedelta(minutes=15)).timestamp())]
    candles = [make_candle(e) for e in epochs]

    connector = make_connector(responses=[AUTH_OK, candles_response(candles)])
    adapter = DerivAdapter(api_token="tok", app_id="1089", connector=connector)

    df = await adapter.fetch_ohlcv("EURUSD", "15m", limit=3)

    assert list(df.columns) == list(REQUIRED_COLUMNS)
    assert len(df) == 3
    assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])
    assert str(df["timestamp"].dt.tz) == "UTC"
    assert df["timestamp"].is_monotonic_increasing
    assert (df["volume"] == 0.0).all()  # Deriv no reporta volumen real
    assert df["open"].dtype == float


@pytest.mark.asyncio
async def test_fetch_ohlcv_envia_los_parametros_correctos_a_deriv():
    now = datetime.now(timezone.utc)
    candles = [make_candle(int((now - timedelta(hours=1)).timestamp()))]
    connector = make_connector(responses=[AUTH_OK, candles_response(candles)])
    adapter = DerivAdapter(api_token="tok-secreto", app_id="1089", connector=connector)

    await adapter.fetch_ohlcv("GBPUSD", "1h", limit=50)

    ws = connector("wss://fake")
    sent = ws.sent_messages
    assert sent[0] == {"authorize": "tok-secreto"}
    assert sent[1]["ticks_history"] == "frxGBPUSD"  # mapeo verificado, no adivinado
    assert sent[1]["granularity"] == 3600            # 1h -> 3600s
    assert sent[1]["count"] == 50
    assert sent[1]["style"] == "candles"


# ══════════════════════════════════════════════════════════════════════════
#  Tamiz #2 — errores de red / autenticación (equivalentes a 500 / 403)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_fallo_de_conexion_lanza_adapter_connection_error():
    """Equivalente WS-nativo de un 500: el transporte ni siquiera conecta.
    Deriv habla WebSocket, no HTTP — no hay un status code 500 literal que
    simular; el análogo real es la conexión fallando a nivel de transporte."""
    connector = make_connector(raise_on_connect=ConnectionRefusedError("conexión rechazada"))
    adapter = DerivAdapter(api_token="tok", app_id="1089", connector=connector)

    with pytest.raises(AdapterConnectionError):
        await adapter.fetch_ohlcv("EURUSD", "15m", limit=10)


@pytest.mark.asyncio
async def test_timeout_lanza_adapter_connection_error():
    connector = make_connector(raise_on_connect=TimeoutError("timeout"))
    adapter = DerivAdapter(api_token="tok", app_id="1089", connector=connector)

    with pytest.raises(AdapterConnectionError):
        await adapter.fetch_ohlcv("EURUSD", "15m", limit=10)


@pytest.mark.asyncio
async def test_token_invalido_lanza_adapter_auth_error():
    """Equivalente WS-nativo de un 403: la fuente respondió, rechazó las
    credenciales. Deriv señaliza esto con {"error": {"code": "InvalidToken"}}
    en vez de un status HTTP — ver DerivAdapter._raise_if_error."""
    error_resp = {"error": {"code": "InvalidToken", "message": "El token no es válido"}}
    connector = make_connector(responses=[error_resp])
    adapter = DerivAdapter(api_token="tok-malo", app_id="1089", connector=connector)

    with pytest.raises(AdapterAuthError):
        await adapter.fetch_ohlcv("EURUSD", "15m", limit=10)


@pytest.mark.asyncio
async def test_auth_error_es_tambien_connection_error():
    """AdapterAuthError es subtipo de AdapterConnectionError -- un llamador
    que solo maneja el caso general igual lo atrapa."""
    error_resp = {"error": {"code": "InvalidToken", "message": "x"}}
    connector = make_connector(responses=[error_resp])
    adapter = DerivAdapter(api_token="tok-malo", app_id="1089", connector=connector)

    with pytest.raises(AdapterConnectionError):
        await adapter.fetch_ohlcv("EURUSD", "15m", limit=10)


@pytest.mark.asyncio
async def test_error_generico_de_deriv_lanza_connection_error_no_auth():
    error_resp = {"error": {"code": "RateLimitExceeded", "message": "demasiadas peticiones"}}
    connector = make_connector(responses=[error_resp])
    adapter = DerivAdapter(api_token="tok", app_id="1089", connector=connector)

    with pytest.raises(AdapterConnectionError) as exc_info:
        await adapter.fetch_ohlcv("EURUSD", "15m", limit=10)
    assert not isinstance(exc_info.value, AdapterAuthError)


# ══════════════════════════════════════════════════════════════════════════
#  Tamiz #2 — datos malformados o faltantes
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_respuesta_sin_campo_candles_lanza_adapter_data_error():
    connector = make_connector(responses=[AUTH_OK, {"msg_type": "candles"}])  # sin 'candles'
    adapter = DerivAdapter(api_token="tok", app_id="1089", connector=connector)

    with pytest.raises(AdapterDataError):
        await adapter.fetch_ohlcv("EURUSD", "15m", limit=10)


@pytest.mark.asyncio
async def test_vela_con_campo_faltante_lanza_adapter_data_error():
    vela_rota = {"epoch": 1700000000, "open": 1.1, "high": 1.11}  # falta low, close
    connector = make_connector(responses=[AUTH_OK, candles_response([vela_rota])])
    adapter = DerivAdapter(api_token="tok", app_id="1089", connector=connector)

    with pytest.raises(AdapterDataError):
        await adapter.fetch_ohlcv("EURUSD", "15m", limit=10)


@pytest.mark.asyncio
async def test_respuesta_json_corrupto_lanza_adapter_data_error():
    class BrokenWebSocket(FakeWebSocket):
        async def recv(self) -> str:
            if not self.sent_messages:
                return json.dumps(AUTH_OK)
            return "{esto no es json valido"

    def connector(uri: str):
        return BrokenWebSocket()

    adapter = DerivAdapter(api_token="tok", app_id="1089", connector=connector)
    with pytest.raises(AdapterDataError):
        await adapter.fetch_ohlcv("EURUSD", "15m", limit=10)


def test_validate_ohlcv_schema_detecta_timestamp_sin_timezone():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-01-01"]),  # naive, sin tz
        "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [0.0],
    })
    with pytest.raises(AdapterDataError):
        validate_ohlcv_schema(df, source="test", symbol="X")


def test_validate_ohlcv_schema_detecta_nan_en_ohlc():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-01-01"], utc=True),
        "open": [float("nan")], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [0.0],
    })
    with pytest.raises(AdapterDataError):
        validate_ohlcv_schema(df, source="test", symbol="X")


def test_validate_ohlcv_schema_dataframe_vacio_es_valido():
    """Vacío-pero-bien-formado no es un error de esquema -- es una
    respuesta legítima (ej. sin actividad en el rango pedido)."""
    df = pd.DataFrame(columns=list(REQUIRED_COLUMNS))
    validate_ohlcv_schema(df, source="test", symbol="X")  # no debe lanzar


# ══════════════════════════════════════════════════════════════════════════
#  Invarianza temporal — la vela en formación nunca debe aparecer
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_vela_aun_no_cerrada_se_descarta():
    now = datetime.now(timezone.utc)
    vela_cerrada = make_candle(int((now - timedelta(minutes=20)).timestamp()))
    # Esta vela "cierra" en el futuro respecto a ahora -> debe descartarse
    vela_en_formacion = make_candle(int((now - timedelta(minutes=2)).timestamp()))

    connector = make_connector(responses=[AUTH_OK, candles_response([vela_cerrada, vela_en_formacion])])
    adapter = DerivAdapter(api_token="tok", app_id="1089", connector=connector)

    df = await adapter.fetch_ohlcv("EURUSD", "15m", limit=2)

    assert len(df) == 1  # la vela en formación se descartó


# ══════════════════════════════════════════════════════════════════════════
#  Validación de parámetros de uso (símbolo/timeframe no soportado)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_simbolo_no_mapeado_lanza_value_error_sin_tocar_la_red():
    connector = make_connector()
    adapter = DerivAdapter(api_token="tok", app_id="1089", connector=connector)

    with pytest.raises(ValueError):
        await adapter.fetch_ohlcv("XAUUSD", "15m", limit=10)  # no está en el mapeo verificado

    ws = connector("wss://fake")
    assert ws.sent_messages == []  # nunca se intentó conectar


@pytest.mark.asyncio
async def test_timeframe_no_soportado_lanza_value_error():
    adapter = DerivAdapter(api_token="tok", app_id="1089", connector=make_connector())
    with pytest.raises(ValueError):
        await adapter.fetch_ohlcv("EURUSD", "3m", limit=10)


@pytest.mark.asyncio
async def test_limit_no_positivo_lanza_value_error():
    adapter = DerivAdapter(api_token="tok", app_id="1089", connector=make_connector())
    with pytest.raises(ValueError):
        await adapter.fetch_ohlcv("EURUSD", "15m", limit=0)
    with pytest.raises(ValueError):
        await adapter.fetch_ohlcv("EURUSD", "15m", limit=-5)


# ══════════════════════════════════════════════════════════════════════════
#  health_check — nunca lanza, siempre bool
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_health_check_exitoso():
    connector = make_connector(responses=[{"ping": "pong"}])
    adapter = DerivAdapter(api_token="tok", app_id="1089", connector=connector)
    assert await adapter.health_check() is True


@pytest.mark.asyncio
async def test_health_check_ante_fallo_de_conexion_devuelve_false_no_lanza():
    connector = make_connector(raise_on_connect=ConnectionRefusedError("caído"))
    adapter = DerivAdapter(api_token="tok", app_id="1089", connector=connector)
    resultado = await adapter.health_check()
    assert resultado is False  # nunca una excepción, pase lo que pase


@pytest.mark.asyncio
async def test_health_check_ante_respuesta_inesperada_devuelve_false():
    connector = make_connector(responses=[{"algo": "inesperado"}])
    adapter = DerivAdapter(api_token="tok", app_id="1089", connector=connector)
    assert await adapter.health_check() is False


# ══════════════════════════════════════════════════════════════════════════
#  AdapterChain — incorporación de la sesión del 13-14 ago 2026. Ver
#  docstring de AdapterChain en ingestion/adapters.py para la nota de
#  procedencia completa (portado y adaptado desde base_adapter.py v8).
#
#  Doble de prueba propio (FakeAdapter) en vez de reusar FakeWebSocket:
#  estos tests ejercitan AdapterChain, no DerivAdapter -- lo que hace
#  falta es un BaseAdapter cuyo fetch_ohlcv() se pueda hacer fallar o
#  responder a voluntad en cada llamada sucesiva, sin pasar por
#  WebSocket/JSON en absoluto.
# ══════════════════════════════════════════════════════════════════════════

def _ohlcv_valido(n: int = 3) -> pd.DataFrame:
    ts = pd.date_range("2026-08-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({
        "timestamp": ts,
        "open": [1.1] * n,
        "high": [1.2] * n,
        "low": [1.0] * n,
        "close": [1.15] * n,
        "volume": [0.0] * n,
    })


class FakeAdapter(BaseAdapter):
    """Doble mínimo de BaseAdapter. `behavior` controla qué hace
    fetch_ohlcv en cada llamada sucesiva -- permite simular "falla las
    primeras N veces, después funciona" sin mockear nada de transporte."""

    def __init__(self, source_name: str, behavior: list):
        self.source_name = source_name
        self._behavior = list(behavior)
        self.calls = 0

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        self.calls += 1
        idx = min(self.calls - 1, len(self._behavior) - 1)
        outcome = self._behavior[idx]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def health_check(self) -> bool:
        return True


class TestAdapterChain:
    def test_requiere_al_menos_un_adapter(self):
        with pytest.raises(ValueError, match="al menos un adapter"):
            AdapterChain([])

    def test_primer_adapter_ok_no_prueba_el_segundo(self):
        primero = FakeAdapter("fuente_a", [_ohlcv_valido()])
        segundo = FakeAdapter("fuente_b", [_ohlcv_valido()])
        chain = AdapterChain([primero, segundo])

        resultado = chain.fetch("EURUSD")

        assert not resultado.is_degraded
        assert resultado.adapter_name == "fuente_a"
        assert segundo.calls == 0  # nunca se llegó a probar

    def test_primero_falla_todos_los_reintentos_cae_al_segundo(self):
        primero = FakeAdapter("fuente_a", [
            AdapterConnectionError("timeout 1"),
            AdapterConnectionError("timeout 2"),
            AdapterConnectionError("timeout 3"),
        ])
        segundo = FakeAdapter("fuente_b", [_ohlcv_valido()])
        chain = AdapterChain([primero, segundo])
        chain.RETRY_DELAY_S = 0  # no dormir de verdad en el test

        resultado = chain.fetch("EURUSD")

        assert not resultado.is_degraded
        assert resultado.adapter_name == "fuente_b"
        assert primero.calls == 3  # agotó MAX_RETRIES antes de pasar al siguiente

    def test_todos_fallan_devuelve_degradado_sin_lanzar(self):
        primero = FakeAdapter("fuente_a", [AdapterConnectionError("caído")])
        segundo = FakeAdapter("fuente_b", [AdapterDataError("esquema roto")])
        chain = AdapterChain([primero, segundo])
        chain.RETRY_DELAY_S = 0

        resultado = chain.fetch("EURUSD")  # no debe lanzar

        assert resultado.is_degraded
        assert resultado.adapter_name == "fuente_b"  # el último probado
        assert resultado.data.empty

    def test_auth_error_corta_reintentos_de_inmediato(self):
        adapter = FakeAdapter("fuente_a", [AdapterAuthError("InvalidToken")])
        chain = AdapterChain([adapter])
        chain.RETRY_DELAY_S = 0

        resultado = chain.fetch("EURUSD")

        assert resultado.is_degraded
        # Un solo intento -- no tiene sentido reintentar un token inválido
        # tres veces (ver comentario en AdapterChain._fetch_con_reintentos_async).
        assert adapter.calls == 1

    def test_escribe_log_de_auditoria_cuando_logs_dir_provisto(self, tmp_path):
        adapter = FakeAdapter("fuente_a", [_ohlcv_valido()])
        chain = AdapterChain([adapter], logs_dir=tmp_path)

        chain.fetch("EURUSD")

        log_path = tmp_path / "adapters_audit.json"
        assert log_path.exists()
        entradas = json.loads(log_path.read_text())
        assert len(entradas) == 1
        assert entradas[0]["adapter"] == "fuente_a"
        assert entradas[0]["is_degraded"] is False

    def test_log_de_auditoria_conserva_ultimas_200(self, tmp_path):
        adapter = FakeAdapter("fuente_a", [_ohlcv_valido()])
        chain = AdapterChain([adapter], logs_dir=tmp_path)

        for _ in range(205):
            chain.fetch("EURUSD")

        entradas = json.loads((tmp_path / "adapters_audit.json").read_text())
        assert len(entradas) == 200

    def test_result_log_summary_no_lanza(self):
        # log_summary() solo loguea -- confirmar que no explota con
        # is_degraded=True y error_msg presente.
        resultado = AdapterResult(
            data=pd.DataFrame(), adapter_name="x", symbol="EURUSD",
            is_degraded=True, error_msg="algo falló",
        )
        resultado.log_summary()

    # ─── fetch_async() / reentrancia (refactor de esta sesión) ─────────────

    @pytest.mark.asyncio
    async def test_fetch_async_funciona_dentro_de_un_loop_activo(self):
        # asyncio_mode=auto ya hace esto automático -- @pytest.mark.asyncio
        # explícito de todas formas, por pedido directo y para que quede
        # auditable sin depender de leer pytest.ini.
        adapter = FakeAdapter("fuente_a", [_ohlcv_valido()])
        chain = AdapterChain([adapter])

        resultado = await chain.fetch_async("EURUSD")

        assert not resultado.is_degraded
        assert resultado.adapter_name == "fuente_a"

    @pytest.mark.asyncio
    async def test_fetch_async_respeta_fallback_y_reintentos_igual_que_fetch(self):
        # Mismo escenario que test_primero_falla_todos_los_reintentos_cae_al_segundo,
        # pero por la vía async -- confirma que fetch_async() no perdió
        # ninguna lógica de la cadena al separarse de fetch().
        adapter_a = FakeAdapter("fuente_a", [AdapterConnectionError("timeout")] * 3)
        adapter_b = FakeAdapter("fuente_b", [_ohlcv_valido()])
        chain = AdapterChain([adapter_a, adapter_b])
        chain.RETRY_DELAY_S = 0

        resultado = await chain.fetch_async("EURUSD")

        assert not resultado.is_degraded
        assert resultado.adapter_name == "fuente_b"
        assert adapter_a.calls == 3
        assert adapter_b.calls == 1

    @pytest.mark.asyncio
    async def test_fetch_sincrono_lanza_runtimeerror_claro_si_hay_loop_activo(self):
        # El bug original: llamar fetch() (síncrono) desde DENTRO de un
        # contexto que ya tiene un event loop corriendo -- este mismo
        # test, por ser `async def`, YA está corriendo dentro de un loop.
        # Antes del fix: RuntimeError genérico de asyncio.run() ("cannot
        # be called from a running event loop"). Con el fix: mensaje
        # claro señalando fetch_async().
        adapter = FakeAdapter("fuente_a", [_ohlcv_valido()])
        chain = AdapterChain([adapter])

        with pytest.raises(RuntimeError, match="fetch_async"):
            chain.fetch("EURUSD")

    def test_fetch_sincrono_sigue_funcionando_fuera_de_un_loop(self):
        # Contraparte del test anterior -- fuera de un contexto async
        # (test NO async), fetch() debe seguir funcionando exactamente
        # igual que siempre. Ya cubierto indirectamente por el resto de
        # esta clase, pero se deja explícito como contrato documentado.
        adapter = FakeAdapter("fuente_a", [_ohlcv_valido()])
        chain = AdapterChain([adapter])

        resultado = chain.fetch("EURUSD")

        assert not resultado.is_degraded

    @pytest.mark.asyncio
    async def test_fetch_async_no_usa_asyncio_run_internamente(self):
        # fetch_async() debe ser 100% coroutine nativa -- si contuviera
        # un asyncio.run() por dentro, este mismo test (ya corriendo
        # dentro de un loop) fallaría con el error de reentrancia.
        # Cubre la instrucción de "cero asyncio.run() salvo en fetch()".
        contador = {"n": 0}
        run_original = asyncio.run

        def run_contado(*a, **kw):
            contador["n"] += 1
            return run_original(*a, **kw)

        asyncio.run = run_contado
        try:
            adapter = FakeAdapter("fuente_a", [_ohlcv_valido()])
            chain = AdapterChain([adapter])
            await chain.fetch_async("EURUSD")
        finally:
            asyncio.run = run_original

        assert contador["n"] == 0

    def test_fetch_sincrono_invoca_asyncio_run_exactamente_una_vez_pese_a_reintentos(self):
        # Hallazgo de eficiencia de esta sesión, verificado con números:
        # antes, asyncio.run() se llamaba una vez POR INTENTO (hasta 3
        # veces por adapter). Ahora hay un único asyncio.run() en fetch(),
        # sin importar cuántos reintentos internos ocurran.
        contador = {"n": 0}
        run_original = asyncio.run

        def run_contado(*a, **kw):
            contador["n"] += 1
            return run_original(*a, **kw)

        asyncio.run = run_contado
        try:
            adapter = FakeAdapter("fuente_a", [
                AdapterConnectionError("timeout 1"),
                AdapterConnectionError("timeout 2"),
                _ohlcv_valido(),
            ])
            chain = AdapterChain([adapter])
            chain.RETRY_DELAY_S = 0
            resultado = chain.fetch("EURUSD")
        finally:
            asyncio.run = run_original

        assert adapter.calls == 3       # 3 intentos reales al adapter
        assert contador["n"] == 1       # pero un solo ciclo de event loop
        assert not resultado.is_degraded


# ══════════════════════════════════════════════════════════════════════════
#  CONTRATO DE DATOS OHLCV — velas cerradas, metadata de calidad, attrs
#
#  Reloj FIJO en todos estos tests: sin now_utc inyectado, un fixture con
#  una vela deliberadamente "abierta" se convierte en cerrada apenas el
#  reloj real avanza más allá de su cierre, y el test empieza a fallar solo
#  a una hora del día que después nadie reproduce.
# ══════════════════════════════════════════════════════════════════════════

AHORA_FIJO = pd.Timestamp("2026-08-23T12:00:00Z")


def _df_velas(timestamps: list[pd.Timestamp]) -> pd.DataFrame:
    n = len(timestamps)
    return pd.DataFrame({
        "timestamp": pd.DatetimeIndex(timestamps),
        "open": [1.10] * n,
        "high": [1.11] * n,
        "low": [1.09] * n,
        "close": [1.105] * n,
        "volume": [0.0] * n,
    })


# ── Validación de cierre (5) ──────────────────────────────────────────────

def test_vela_abierta_es_rechazada_cuando_hay_granularity_s():
    # Vela de 1h abierta a las 11:30 -> cierra 12:30, después de AHORA_FIJO.
    df = _df_velas([pd.Timestamp("2026-08-23T11:30:00Z")])
    with pytest.raises(AdapterDataError, match="sin cerrar"):
        validate_ohlcv_schema(
            df, source="test", symbol="X",
            granularity_s=3600, now_utc=AHORA_FIJO,
        )


def test_vela_cerrada_es_aceptada_con_granularity_s():
    # Vela de 1h abierta a las 10:00 -> cerró 11:00, antes de AHORA_FIJO.
    df = _df_velas([pd.Timestamp("2026-08-23T10:00:00Z")])
    validate_ohlcv_schema(
        df, source="test", symbol="X",
        granularity_s=3600, now_utc=AHORA_FIJO,
    )  # no debe lanzar


def test_sin_granularity_s_no_se_valida_el_cierre():
    """require_closed=True significa 'valida el cierre cuando es posible
    saberlo', no 'siempre'. Sin granularidad no se puede saber, y NO se
    infiere del espaciado -- este es el caso de training_dataset.py:119."""
    df = _df_velas([pd.Timestamp("2026-08-23T11:30:00Z")])
    validate_ohlcv_schema(
        df, source="training_dataset", symbol="X",
        require_closed=True, granularity_s=None, now_utc=AHORA_FIJO,
    )  # no debe lanzar: la vela está abierta pero es indecidible


def test_sin_granularity_s_las_demas_validaciones_siguen_activas():
    """Omitir la verificación de cierre no debe degradar el resto del
    contrato -- si esto se rompiera, granularity_s=None sería un bypass
    silencioso del validador entero."""
    df = _df_velas([pd.Timestamp("2026-08-23T10:00:00Z")])
    df["timestamp"] = df["timestamp"].dt.tz_localize(None)  # naive
    with pytest.raises(AdapterDataError, match="timezone"):
        validate_ohlcv_schema(
            df, source="test", symbol="X",
            granularity_s=None, now_utc=AHORA_FIJO,
        )


def test_require_closed_false_ignora_velas_abiertas():
    df = _df_velas([pd.Timestamp("2026-08-23T11:30:00Z")])
    validate_ohlcv_schema(
        df, source="test", symbol="X",
        require_closed=False, granularity_s=3600, now_utc=AHORA_FIJO,
    )  # no debe lanzar


# ── drop_unclosed_candles (4) ─────────────────────────────────────────────

def test_drop_unclosed_candles_es_funcion_publica_del_modulo():
    """La salvaguarda anti-fuga-temporal no puede ser cortesía de un
    adapter: un adapter nuevo que no la reimplemente dejaría pasar la vela
    en formación sin que nada lo detecte."""
    import ingestion.adapters as mod
    assert callable(mod.drop_unclosed_candles)
    assert not hasattr(DerivAdapter, "_drop_unclosed_candle")


def test_drop_unclosed_candles_no_muta_el_original():
    df = _df_velas([
        pd.Timestamp("2026-08-23T10:00:00Z"),
        pd.Timestamp("2026-08-23T11:30:00Z"),
    ])
    resultado = drop_unclosed_candles(
        df, 3600, source="test", symbol="X", now_utc=AHORA_FIJO,
    )
    assert len(df) == 2       # el original intacto
    assert len(resultado) == 1


def test_drop_unclosed_candles_con_dataframe_vacio():
    df = _df_velas([])
    resultado = drop_unclosed_candles(
        df, 3600, source="test", now_utc=AHORA_FIJO,
    )
    assert resultado.empty


def test_salida_de_drop_unclosed_candles_pasa_la_validacion_de_cierre():
    """Las dos mitades del contrato tienen que encajar: lo que el filtro
    deja pasar es exactamente lo que el validador acepta."""
    df = _df_velas([
        pd.Timestamp("2026-08-23T09:00:00Z"),
        pd.Timestamp("2026-08-23T10:00:00Z"),
        pd.Timestamp("2026-08-23T11:30:00Z"),
    ])
    limpio = drop_unclosed_candles(
        df, 3600, source="test", symbol="X", now_utc=AHORA_FIJO,
    )
    validate_ohlcv_schema(
        limpio, source="test", symbol="X",
        granularity_s=3600, now_utc=AHORA_FIJO,
    )  # no debe lanzar


# ── AdapterResult: metadata de calidad (3) ────────────────────────────────

def test_adapter_result_conserva_los_campos_de_calidad():
    r = AdapterResult(
        data=_ohlcv_valido(), adapter_name="x", symbol="EURUSD",
        volume_available=False, timestamp_is_convention=True,
        is_fallback=True, provider_status="rate_limited",
    )
    assert r.volume_available is False
    assert r.timestamp_is_convention is True
    assert r.is_fallback is True
    assert r.provider_status == "rate_limited"


def test_adapter_result_defaults_conservadores():
    """Un adapter que no reporta estas dimensiones no debe quedar marcado
    como sospechoso -- el default asume lo benigno."""
    r = AdapterResult(data=_ohlcv_valido(), adapter_name="x", symbol="EURUSD")
    assert r.volume_available is True
    assert r.timestamp_is_convention is False
    assert r.is_fallback is False
    assert r.provider_status == "ok"


def test_is_fallback_es_independiente_de_is_degraded():
    """Un respaldo puede funcionar perfecto: venir de la segunda fuente no
    significa que el dato esté mal."""
    r = AdapterResult(
        data=_ohlcv_valido(), adapter_name="respaldo", symbol="EURUSD",
        is_fallback=True, is_degraded=False,
    )
    assert r.is_fallback is True
    assert r.is_degraded is False


# ── Transporte por df.attrs (4) ───────────────────────────────────────────

def test_chain_levanta_attrs_del_dataframe_a_adapter_result():
    df = _ohlcv_valido()
    df.attrs["volume_available"] = False
    df.attrs["timestamp_is_convention"] = True
    df.attrs["provider_status"] = "rate_limited"

    resultado = AdapterChain([FakeAdapter("fuente_a", [df])]).fetch("EURUSD")

    assert resultado.volume_available is False
    assert resultado.timestamp_is_convention is True
    assert resultado.provider_status == "rate_limited"


def test_adapter_sin_attrs_no_es_un_error():
    """No escribir attrs no es incumplir el contrato -- es no reportar esa
    dimensión. El chain aplica defaults conservadores."""
    resultado = AdapterChain([FakeAdapter("fuente_a", [_ohlcv_valido()])]).fetch("EURUSD")

    assert not resultado.is_degraded
    assert resultado.volume_available is True
    assert resultado.timestamp_is_convention is False
    assert resultado.provider_status == "ok"


def test_regresion_attrs_muere_en_merge_y_sobrevive_copy_y_sort():
    """Documenta la razón de diseño de AdapterResult, medida no supuesta:
    attrs sobrevive copy/sort_values y SE PIERDE en merge. Como
    training_dataset.py hace exactamente un join OHLCV<->GDELT, usar attrs
    como almacenamiento durable perdería la procedencia justo ahí, en
    silencio. Si una versión futura de pandas cambia esto, este test falla
    y alguien revisa la decisión en vez de heredarla a ciegas."""
    df = _ohlcv_valido()
    df.attrs["volume_available"] = False

    assert df.copy().attrs == {"volume_available": False}
    assert df.sort_values("timestamp").attrs == {"volume_available": False}

    otro = pd.DataFrame({"timestamp": df["timestamp"], "entropy": [0.1, 0.2, 0.3]})
    assert df.merge(otro, on="timestamp").attrs == {}


@pytest.mark.asyncio
async def test_deriv_reporta_volume_available_false():
    """Deriv nunca reporta volumen: su doc oficial define la vela con
    exactamente close/epoch/high/low/open. El 0.0 es relleno, y sin esta
    bandera un 0.0 de relleno y un 0.0 de 'no se operó' son
    indistinguibles."""
    epoch = int(pd.Timestamp("2026-08-01T10:00:00Z").timestamp())
    connector = make_connector([AUTH_OK, candles_response([make_candle(epoch)])])
    adapter = DerivAdapter(api_token="t", app_id="1", connector=connector)

    df = await adapter.fetch_ohlcv("EURUSD", "1h", 1)

    assert df.attrs["volume_available"] is False
    assert df.attrs["timestamp_is_convention"] is False


# ── Logging (4) ───────────────────────────────────────────────────────────
#  El logger del módulo es "spel.ingestion.adapters", NO "ingestion.adapters".

def test_log_de_descarte_incluye_proveedor_y_simbolo(caplog):
    df = _df_velas([pd.Timestamp("2026-08-23T11:30:00Z")])
    with caplog.at_level(logging.INFO, logger="spel.ingestion.adapters"):
        drop_unclosed_candles(
            df, 3600, source="deriv", symbol="EURUSD", now_utc=AHORA_FIJO,
        )
    assert "deriv/EURUSD" in caplog.text


def test_log_sin_simbolo_no_pega_un_none(caplog):
    """symbol es opcional -- un dataset ya compuesto puede no tener uno
    solo. Cuando falta, el log muestra el proveedor a secas, no 'deriv/None'."""
    df = _df_velas([pd.Timestamp("2026-08-23T11:30:00Z")])
    with caplog.at_level(logging.INFO, logger="spel.ingestion.adapters"):
        drop_unclosed_candles(df, 3600, source="deriv", now_utc=AHORA_FIJO)
    assert "[deriv]" in caplog.text
    assert "None" not in caplog.text


@pytest.mark.asyncio
async def test_log_extremo_a_extremo_con_deriv_adapter(caplog):
    """El camino real: DerivAdapter pide velas, una está abierta, y el log
    identifica proveedor e instrumento sin que el adapter tenga que
    reimplementar nada."""
    cerrada = int(pd.Timestamp("2026-08-01T10:00:00Z").timestamp())
    abierta = int(pd.Timestamp.now(tz="UTC").timestamp())  # su hora cierra en el futuro
    connector = make_connector([
        AUTH_OK,
        candles_response([make_candle(cerrada), make_candle(abierta)]),
    ])
    adapter = DerivAdapter(api_token="t", app_id="1", connector=connector)

    with caplog.at_level(logging.INFO, logger="spel.ingestion.adapters"):
        df = await adapter.fetch_ohlcv("EURUSD", "1h", 2)

    assert len(df) == 1
    assert "deriv/EURUSD" in caplog.text


@pytest.mark.asyncio
async def test_ningun_log_expone_valores_de_secreto(caplog):
    """source y symbol se escriben al log -- nunca debe pasarse por ahí algo
    derivado de un secreto. El token del adapter no puede aparecer."""
    token = "token-secreto-que-no-debe-aparecer"
    epoch = int(pd.Timestamp("2026-08-01T10:00:00Z").timestamp())
    connector = make_connector([AUTH_OK, candles_response([make_candle(epoch)])])
    adapter = DerivAdapter(api_token=token, app_id="app-id-secreto", connector=connector)

    with caplog.at_level(logging.DEBUG, logger="spel.ingestion.adapters"):
        await adapter.fetch_ohlcv("EURUSD", "1h", 1)

    assert token not in caplog.text
    assert "app-id-secreto" not in caplog.text


# ── Red de seguridad (1) ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deriv_arma_la_red_de_seguridad_pasando_granularity_s(monkeypatch):
    """Verifica el ARGUMENTO, no el efecto -- y eso es deliberado: como
    _to_dataframe ya filtró las velas abiertas, quitar granularity_s del
    call site dejaría todos los demás tests en verde y desarmaría la red
    de seguridad sin que nadie se entere. Este test es lo único que lo
    detecta."""
    import ingestion.adapters as mod

    capturado = {}

    def validate_espia(df, **kwargs):
        capturado.update(kwargs)

    monkeypatch.setattr(mod, "validate_ohlcv_schema", validate_espia)

    epoch = int(pd.Timestamp("2026-08-01T10:00:00Z").timestamp())
    connector = make_connector([AUTH_OK, candles_response([make_candle(epoch)])])
    adapter = DerivAdapter(api_token="t", app_id="1", connector=connector)

    await adapter.fetch_ohlcv("EURUSD", "15m", 1)

    assert capturado["granularity_s"] == 900
    assert capturado["require_closed"] is True
    assert capturado["symbol"] == "EURUSD"
    assert capturado["source"] == "deriv"
