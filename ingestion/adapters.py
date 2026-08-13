"""
ingestion/adapters.py
======================
Contrato único para cualquier fuente de datos de mercado. Escrito desde cero
— no importa nada de archive/* (Principio 4 / Tamiz #3).

Por qué existe este archivo (no es un wrapper cosmético de la librería del
broker): cada fuente real (Deriv, GDELT, y lo que venga después) habla un
protocolo distinto, con distintos modos de fallar. Lo que este módulo
garantiza es que, sin importar el protocolo de abajo, quien llama nunca ve:
  - una excepción cruda de la librería de transporte (websockets, requests)
  - un DataFrame con columnas o tipos inesperados
  - una vela que todavía no cerró disfrazada de dato histórico completo
  - un fallo silencioso que se confunda con "no hay datos hoy"

Diseño async desde el arranque — Deriv es WebSocket nativo, y el resto del
proyecto ya trabaja con httpx/aiohttp/websockets (ver PRINCIPLES.md).
"""

from __future__ import annotations

import abc
import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import pandas as pd

logger = logging.getLogger("spel.ingestion.adapters")


# ══════════════════════════════════════════════════════════════════════════
#  Excepciones tipadas — nunca dejamos escapar un KeyError o una excepción
#  cruda de la librería de transporte. Tres categorías, porque un llamador
#  necesita tratarlas distinto:
#    - AdapterConnectionError: el intento de red falló. Puede tener sentido
#      reintentar o caer a otra fuente.
#    - AdapterAuthError: la fuente respondió, pero rechazó las credenciales.
#      Reintentar con la misma config no va a arreglar nada — es un
#      subtipo de connection error para que el llamador pueda distinguirlo
#      si quiere, sin obligarlo.
#    - AdapterDataError: la fuente respondió, la conexión funcionó, pero el
#      contenido no es válido (esquema roto, vela sin cerrar, NaN donde no
#      debería haber). Reintentar la MISMA petición no ayuda.
# ══════════════════════════════════════════════════════════════════════════

class AdapterException(Exception):
    """Base de toda excepción de ingestion/. Nunca se instancia directo."""


class AdapterConnectionError(AdapterException):
    """Fallo de transporte: timeout, conexión rechazada, servidor caído."""


class AdapterAuthError(AdapterConnectionError):
    """La fuente respondió pero rechazó token/credenciales. No reintentar
    igual — hace falta intervención (rotar token, revisar permisos)."""


class AdapterDataError(AdapterException):
    """La conexión funcionó, pero el contenido devuelto no es válido:
    esquema roto, vela sin cerrar, campos faltantes, tipos incorrectos."""


# ══════════════════════════════════════════════════════════════════════════
#  Esquema OHLCV canónico — una sola definición, todo adapter debe cumplirla
# ══════════════════════════════════════════════════════════════════════════

REQUIRED_COLUMNS: tuple[str, ...] = ("timestamp", "open", "high", "low", "close", "volume")


def validate_ohlcv_schema(df: pd.DataFrame, *, source: str, symbol: str) -> None:
    """
    Verifica que un DataFrame recién construido por un adapter cumple el
    contrato antes de devolverlo al llamador. Esto es el adapter
    auto-verificándose — no confiamos en que el parseo de arriba salió bien
    solo porque no tiró una excepción.

    Lanza AdapterDataError con un mensaje específico si algo no cumple.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise AdapterDataError(
            f"[{source}:{symbol}] faltan columnas requeridas: {missing}. "
            f"Columnas recibidas: {list(df.columns)}"
        )

    if df.empty:
        # Un DataFrame vacío-pero-bien-formado es una respuesta válida en
        # sí misma (ej. símbolo sin actividad en el rango) — NO es un error
        # de esquema. Quien llama decide qué hacer con "no hay datos".
        return

    if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        raise AdapterDataError(
            f"[{source}:{symbol}] columna 'timestamp' no es datetime: "
            f"dtype real = {df['timestamp'].dtype}"
        )

    if df["timestamp"].dt.tz is None:
        raise AdapterDataError(
            f"[{source}:{symbol}] 'timestamp' no tiene timezone — "
            f"debe ser UTC explícito, nunca naive (ambigüedad de zona horaria "
            f"es una fuente clásica de fuga temporal)."
        )

    if str(df["timestamp"].dt.tz) != "UTC":
        raise AdapterDataError(
            f"[{source}:{symbol}] 'timestamp' está en {df['timestamp'].dt.tz}, "
            f"debe ser UTC exactamente."
        )

    if not df["timestamp"].is_monotonic_increasing:
        raise AdapterDataError(
            f"[{source}:{symbol}] 'timestamp' no está ordenado ascendente — "
            f"un orden incorrecto puede esconder una fuga temporal aguas abajo."
        )

    numeric_cols = ["open", "high", "low", "close", "volume"]
    for col in numeric_cols:
        if not pd.api.types.is_numeric_dtype(df[col]):
            raise AdapterDataError(
                f"[{source}:{symbol}] columna '{col}' no es numérica: "
                f"dtype real = {df[col].dtype}"
            )

    ohlc_cols = ["open", "high", "low", "close"]
    if df[ohlc_cols].isna().any().any():
        bad = df[ohlc_cols].isna().any()
        raise AdapterDataError(
            f"[{source}:{symbol}] valores NaN en columnas OHLC: "
            f"{bad[bad].index.tolist()}"
        )


# ══════════════════════════════════════════════════════════════════════════
#  BaseAdapter — el contrato. Cualquier fuente nueva implementa esto.
# ══════════════════════════════════════════════════════════════════════════

class BaseAdapter(abc.ABC):
    """
    Interfaz obligatoria para cualquier fuente de datos de mercado.

    Contrato de comportamiento (no solo de firma):
      - fetch_ohlcv() SIEMPRE devuelve un DataFrame con el esquema de
        REQUIRED_COLUMNS, o lanza una AdapterException tipada. Nunca
        devuelve None, nunca deja pasar una excepción cruda de la librería
        de transporte, nunca un DataFrame con columnas o tipos distintos.
      - health_check() SIEMPRE devuelve bool. Nunca lanza — su propósito
        es justamente poder preguntar "¿está viva la fuente?" sin arriesgar
        una excepción en el intento.
    """

    #: Nombre corto de la fuente, usado en logs y en los mensajes de error
    #: de las excepciones tipadas. Cada subclase lo define.
    source_name: str = "unknown"

    @abc.abstractmethod
    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """
        Devuelve las últimas `limit` velas cerradas de `symbol` en `timeframe`.

        Garantías del contrato (verificadas internamente antes de devolver,
        ver validate_ohlcv_schema):
          - Columnas exactas: timestamp, open, high, low, close, volume
          - timestamp: datetime64 tz-aware en UTC, orden ascendente
          - Solo velas CERRADAS — nunca la vela en formación actual
            (ver _drop_unclosed_candle en DerivAdapter para el porqué)

        Lanza:
          AdapterConnectionError si el transporte falla (timeout, rechazo).
          AdapterAuthError si las credenciales son inválidas.
          AdapterDataError si la respuesta no es válida o no se puede
            garantizar que las velas están cerradas.
          ValueError si `symbol` o `timeframe` no son reconocidos por este
            adapter (error de uso del llamador, no de la fuente).
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def health_check(self) -> bool:
        """
        Verifica conectividad con la fuente sin descargar datos de mercado.
        Nunca lanza — cualquier fallo interno se traduce a `False`.
        """
        raise NotImplementedError


# ══════════════════════════════════════════════════════════════════════════
#  DerivAdapter — implementación concreta contra la API oficial de Deriv
#  (WebSocket, ticks_history). Endpoint y formato de símbolo verificados
#  contra la documentación oficial (developers.deriv.com), no adivinados.
# ══════════════════════════════════════════════════════════════════════════

DERIV_WS_ENDPOINT = "wss://ws.derivws.com/websockets/v3?app_id={app_id}"

#: Solo símbolos CONFIRMADOS contra la documentación oficial de Deriv.
#: No se agrega nada acá "por analogía" — un símbolo mal mapeado pide datos
#: de un instrumento distinto al que el llamador cree que está pidiendo,
#: silenciosamente. Ver DerivAdapter.SUPPORTED_SYMBOLS para cómo extenderlo.
_DERIV_SYMBOL_MAP: dict[str, str] = {
    "EURUSD": "frxEURUSD",
    "GBPUSD": "frxGBPUSD",
    "USDJPY": "frxUSDJPY",
    "USDCHF": "frxUSDCHF",
    "AUDUSD": "frxAUDUSD",
}

_DERIV_GRANULARITY_SECONDS: dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
}

#: Tipo de la función que abre la conexión WebSocket. Inyectable para que
#: los tests nunca toquen la red real (Tamiz #3) sin necesidad de
#: monkeypatchear internals de la librería websockets.
WebSocketConnector = Callable[[str], Any]


class DerivAdapter(BaseAdapter):
    """
    Adapter OHLCV contra la API oficial de Deriv.

    NO incluye ejecución de órdenes — solo lectura de mercado (ticks_history
    con style=candles). Ejecución es un adapter/módulo separado, en
    execution/, con su propia auditoría bajo el Tamiz #4.
    """

    source_name = "deriv"
    SUPPORTED_SYMBOLS = frozenset(_DERIV_SYMBOL_MAP.keys())
    SUPPORTED_TIMEFRAMES = frozenset(_DERIV_GRANULARITY_SECONDS.keys())

    def __init__(
        self,
        api_token: str,
        app_id: str,
        connector: Optional[WebSocketConnector] = None,
        timeout_s: float = 10.0,
    ) -> None:
        """
        connector: factory async-context-manager para abrir la conexión,
        misma firma que websockets.connect(uri). Por defecto usa la
        librería real; los tests pasan un doble de prueba acá — inyección
        de dependencia, no monkeypatch de internals.
        """
        if not api_token or not app_id:
            raise ValueError("DerivAdapter requiere api_token y app_id no vacíos")
        self._api_token = api_token
        self._app_id = app_id
        self._timeout_s = timeout_s
        self._connector = connector or self._default_connector

    @staticmethod
    def _default_connector(uri: str):
        import websockets
        return websockets.connect(uri)

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        if symbol not in _DERIV_SYMBOL_MAP:
            raise ValueError(
                f"Símbolo '{symbol}' no está en el mapeo verificado de Deriv. "
                f"Soportados: {sorted(self.SUPPORTED_SYMBOLS)}. "
                f"No se adivina un símbolo nuevo — confirmar contra "
                f"active_symbols antes de agregarlo al mapeo."
            )
        if timeframe not in _DERIV_GRANULARITY_SECONDS:
            raise ValueError(
                f"Timeframe '{timeframe}' no reconocido. "
                f"Soportados: {sorted(self.SUPPORTED_TIMEFRAMES)}."
            )
        if limit <= 0:
            raise ValueError(f"limit debe ser positivo, recibido: {limit}")

        deriv_symbol = _DERIV_SYMBOL_MAP[symbol]
        granularity = _DERIV_GRANULARITY_SECONDS[timeframe]

        raw_candles = await self._request_candles(deriv_symbol, granularity, limit)
        df = self._to_dataframe(raw_candles, granularity, source=symbol)
        validate_ohlcv_schema(df, source=self.source_name, symbol=symbol)
        return df

    async def _request_candles(self, deriv_symbol: str, granularity: int, limit: int) -> list[dict]:
        uri = DERIV_WS_ENDPOINT.format(app_id=self._app_id)
        try:
            async with self._connector(uri) as ws:
                await self._send_json(ws, {"authorize": self._api_token})
                auth_resp = await self._recv_json(ws)
                self._raise_if_error(auth_resp, context="authorize")

                request = {
                    "ticks_history": deriv_symbol,
                    "adjust_start_time": 1,
                    "end": "latest",
                    "count": limit,
                    "style": "candles",
                    "granularity": granularity,
                }
                await self._send_json(ws, request)
                resp = await self._recv_json(ws)
                self._raise_if_error(resp, context="ticks_history")

                candles = resp.get("candles")
                if not candles:
                    raise AdapterDataError(
                        f"[{self.source_name}] respuesta sin campo 'candles' "
                        f"o vacío para {deriv_symbol}: claves recibidas = "
                        f"{list(resp.keys())}"
                    )
                return candles

        except json.JSONDecodeError as exc:
            # DEBE ir antes que el catch de ValueError: JSONDecodeError es
            # subclase de ValueError, así que si este except estuviera
            # después, el bloque de abajo la atraparía primero y la
            # re-lanzaría cruda en vez de convertirla a AdapterDataError.
            # (Bug real que encontró la propia suite de tests -- ver
            # test_respuesta_json_corrupto_lanza_adapter_data_error.)
            raise AdapterDataError(
                f"[{self.source_name}] respuesta no es JSON válido: {exc}"
            ) from exc
        except (AdapterException, ValueError):
            raise  # ya son excepciones tipadas, no las envolvemos de nuevo
        except TimeoutError as exc:
            raise AdapterConnectionError(
                f"[{self.source_name}] timeout conectando a {deriv_symbol}"
            ) from exc
        except (ConnectionError, OSError) as exc:
            raise AdapterConnectionError(
                f"[{self.source_name}] fallo de conexión: {exc}"
            ) from exc

    @staticmethod
    async def _send_json(ws, payload: dict) -> None:
        await ws.send(json.dumps(payload))

    @staticmethod
    async def _recv_json(ws) -> dict:
        raw = await ws.recv()
        return json.loads(raw)

    def _raise_if_error(self, resp: dict, *, context: str) -> None:
        """Deriv señaliza fallos con un campo 'error' en el JSON, no con
        códigos de status HTTP — este método es el punto único donde ese
        vocabulario específico de Deriv se traduce al vocabulario común
        del adapter (AdapterAuthError / AdapterConnectionError)."""
        err = resp.get("error")
        if not err:
            return
        code = err.get("code", "")
        message = err.get("message", str(err))
        if code in ("AuthorizationRequired", "InvalidToken", "InvalidAppID"):
            raise AdapterAuthError(f"[{self.source_name}:{context}] {code}: {message}")
        raise AdapterConnectionError(f"[{self.source_name}:{context}] {code}: {message}")

    def _to_dataframe(self, candles: list[dict], granularity: int, *, source: str) -> pd.DataFrame:
        try:
            rows = [
                {
                    "timestamp": c["epoch"],
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                    # Deriv (CFD/sintéticos) no reporta volumen real por vela.
                    # 0.0 explícito, no NaN — un consumidor que promedie
                    # "volumen" sabe que es una constante, no un dato ausente.
                    "volume": 0.0,
                }
                for c in candles
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise AdapterDataError(
                f"[{self.source_name}] vela con campos faltantes o de tipo "
                f"incorrecto: {exc}. Primera vela recibida: "
                f"{candles[0] if candles else 'ninguna'}"
            ) from exc

        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)
        df = self._drop_unclosed_candle(df, granularity, source=source)
        return df

    def _drop_unclosed_candle(self, df: pd.DataFrame, granularity: int, *, source: str) -> pd.DataFrame:
        """
        Salvaguarda anti-fuga-temporal: si la última vela todavía no pudo
        haber cerrado (su fin de intervalo cae en el futuro respecto a
        ahora), se descarta. No verifiqué con certeza absoluta en la
        documentación de Deriv si end=latest siempre excluye la vela en
        formación — en vez de asumir que sí, este filtro lo garantiza de
        todos modos, sin importar el comportamiento exacto del broker.
        """
        if df.empty:
            return df
        now_utc = pd.Timestamp.now(tz="UTC")
        candle_close_time = df["timestamp"] + pd.Timedelta(seconds=granularity)
        still_forming = candle_close_time > now_utc
        if still_forming.any():
            n_dropped = int(still_forming.sum())
            logger.info(
                "[%s] descartando %d vela(s) aún no cerrada(s) para %s",
                self.source_name, n_dropped, source,
            )
            df = df.loc[~still_forming].reset_index(drop=True)
        return df

    async def health_check(self) -> bool:
        uri = DERIV_WS_ENDPOINT.format(app_id=self._app_id)
        try:
            async with self._connector(uri) as ws:
                await self._send_json(ws, {"ping": 1})
                resp = await self._recv_json(ws)
                return resp.get("ping") == "pong"
        except Exception as exc:
            logger.warning("[%s] health_check falló: %s", self.source_name, exc)
            return False
