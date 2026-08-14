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

Nota de procedencia (auditoría explícita, 13-14 ago 2026):
  BaseAdapter, DerivAdapter y las excepciones tipadas de este archivo NO
  cambiaron en esta sesión — ya estaban en main, ya probados. Lo que se
  agrega acá es AdapterResult y AdapterChain (al final del archivo):
  portados y adaptados desde infrastructure/adapters/base_adapter.py
  (linaje "SPEL v8"), tomando el patrón de degradación elegante
  (is_degraded, fallback en cadena, log de auditoría) y dejando afuera
  todo lo que pertenecía a ese linaje pero no a esta capa — polars en vez
  de pandas, columnas con features ya calculadas (fibonacci_lag_*,
  goldstein_geo), logging.basicConfig a nivel de módulo, y las factories
  que hardcodeaban fuentes (AlphaVantage/Tiingo/BigQuery) fuera del
  alcance de Fase 1. Diff completo de qué entró/quedó afuera: discutido y
  aprobado en el chat de refactorización del 13-14 ago 2026.
"""

from __future__ import annotations

import abc
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
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


# ══════════════════════════════════════════════════════════════════════════
#  AdapterResult / AdapterChain — degradación elegante con fallback en cadena
#
#  Portado y adaptado desde infrastructure/adapters/base_adapter.py (linaje
#  "SPEL v8"). Ver nota de procedencia en el docstring del módulo para el
#  detalle completo de qué se descartó y por qué. Los cambios respecto al
#  original, resumidos:
#    - pl.DataFrame -> pd.DataFrame (no se introduce una segunda librería
#      de datos en la misma capa que ya está en pandas).
#    - "except Exception" genérico -> "except AdapterException" al capturar
#      fallas de un adapter dentro del retry: un BaseAdapter conforme a
#      este contrato SIEMPRE traduce sus fallas a AdapterException (ver
#      BaseAdapter.fetch_ohlcv arriba). Atrapar Exception a secas
#      convertiría también un bug de programación (TypeError, AttributeError)
#      en un "degradado silencioso" indistinguible de una falla de red real
#      — exactamente el tipo de fallo silencioso que este módulo existe
#      para prevenir (ver docstring del módulo, primer párrafo).
#    - Sin las factories build_ohlcv_chain/build_gdelt_chain: hardcodeaban
#      fuentes (AlphaVantage, Tiingo, BigQuery) fuera del alcance de
#      Fase 1. Quien arme una cadena la arma explícito en el call site,
#      con los adapters que correspondan a esta fase.
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class AdapterResult:
    """
    Contenedor canónico del resultado de pasar por un AdapterChain.
    A diferencia de BaseAdapter.fetch_ohlcv() (que lanza AdapterException),
    AdapterChain.fetch() NUNCA lanza — el pipeline aguas abajo siempre
    recibe un AdapterResult y decide qué hacer con is_degraded.
    """
    data: pd.DataFrame
    adapter_name: str
    symbol: str
    is_degraded: bool = False
    error_msg: Optional[str] = None
    rows_fetched: int = 0
    latency_ms: float = 0.0
    timestamp_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def log_summary(self) -> None:
        estado = "DEGRADADO" if self.is_degraded else "OK"
        logger.info(
            "[%s] %s -> %s | filas=%d | latencia=%.0fms%s",
            estado, self.adapter_name, self.symbol,
            self.rows_fetched, self.latency_ms,
            f" | error: {self.error_msg}" if self.error_msg else "",
        )


class AdapterChain:
    """
    Orquesta una lista de BaseAdapter con fallback automático y reintentos.

    IMPORTANTE — reentrancia de asyncio.run():
      fetch() es SÍNCRONA a propósito (decisión explícita del 13-14 ago
      2026: portar la interfaz síncrona del original en vez de reescribir
      la cadena entera como async). Por dentro, cada intento contra un
      adapter async se resuelve con asyncio.run(). asyncio.run() NO es
      reentrante: llamar a este método desde código que ya está corriendo
      dentro de un event loop (por ejemplo, desde dentro de otro `async def`
      ya en ejecución) lanza RuntimeError.
      Hoy (Fase 1) esto no es un problema — nada en ingestion/ ni en
      core/scoring.py llama fetch() desde un contexto ya-async. Si en
      Fase 4 execution/ necesita invocar esta cadena desde dentro de un
      loop async existente (por ejemplo, un orquestador async que también
      llama a Deriv para ejecución de órdenes), este wrapper hay que
      revisarlo entonces — no se resuelve por adelantado acá.
    """

    MAX_RETRIES: int = 3
    RETRY_DELAY_S: float = 2.0

    def __init__(self, adapters: list[BaseAdapter], *, logs_dir: Optional[Path] = None):
        if not adapters:
            raise ValueError("AdapterChain requiere al menos un adapter")
        self.adapters = adapters
        self.logs_dir = logs_dir

    def fetch(
        self,
        symbol: str,
        *,
        timeframe: str = "1h",
        limit: int = 100,
    ) -> AdapterResult:
        """
        Prueba cada adapter en orden. Devuelve el primer resultado no
        degradado. Si todos fallan, devuelve el resultado degradado del
        último adapter probado (nunca lanza).
        """
        ultimo_resultado: Optional[AdapterResult] = None

        for adapter in self.adapters:
            resultado = self._fetch_con_reintentos(adapter, symbol, timeframe, limit)
            ultimo_resultado = resultado

            if not resultado.is_degraded:
                if self.logs_dir:
                    self._escribir_log_auditoria(resultado)
                return resultado

            logger.warning(
                "[chain] '%s' degradado para %s — probando siguiente fuente",
                adapter.source_name, symbol,
            )

        if self.logs_dir and ultimo_resultado:
            self._escribir_log_auditoria(ultimo_resultado)

        if ultimo_resultado is not None:
            return ultimo_resultado

        # Caso extremo: self.adapters no puede estar vacío (validado en
        # __init__), así que esta rama es inalcanzable en la práctica —
        # se deja como red de seguridad explícita, no silenciosa.
        return AdapterResult(
            data=pd.DataFrame(),
            adapter_name="chain_empty",
            symbol=symbol,
            is_degraded=True,
            error_msg="AdapterChain sin adapters ejecutables",
        )

    def _fetch_con_reintentos(
        self, adapter: BaseAdapter, symbol: str, timeframe: str, limit: int,
    ) -> AdapterResult:
        last_error: Optional[str] = None
        t0 = time.monotonic()

        for intento in range(1, self.MAX_RETRIES + 1):
            try:
                df = asyncio.run(adapter.fetch_ohlcv(symbol, timeframe, limit))
                latencia_ms = (time.monotonic() - t0) * 1000
                resultado = AdapterResult(
                    data=df,
                    adapter_name=adapter.source_name,
                    symbol=symbol,
                    is_degraded=False,
                    rows_fetched=len(df),
                    latency_ms=latencia_ms,
                )
                resultado.log_summary()
                return resultado

            except AdapterException as exc:
                last_error = f"[intento {intento}/{self.MAX_RETRIES}] {type(exc).__name__}: {exc}"
                logger.warning("[%s] %s | symbol=%s", adapter.source_name, last_error, symbol)
                if isinstance(exc, AdapterAuthError):
                    # Reintentar con las mismas credenciales no arregla un
                    # 403/InvalidToken — cortar los reintentos ahora mismo
                    # y pasar directo a degradado, en vez de esperar y
                    # repetir la misma falla dos veces más.
                    break
                if intento < self.MAX_RETRIES:
                    time.sleep(self.RETRY_DELAY_S * intento)

        latencia_ms = (time.monotonic() - t0) * 1000
        resultado = AdapterResult(
            data=pd.DataFrame(),
            adapter_name=adapter.source_name,
            symbol=symbol,
            is_degraded=True,
            error_msg=last_error,
            rows_fetched=0,
            latency_ms=latencia_ms,
        )
        resultado.log_summary()
        return resultado

    def _escribir_log_auditoria(self, resultado: AdapterResult) -> None:
        """Persiste el resultado en {logs_dir}/adapters_audit.json
        (últimas 200 entradas). Mismo formato que el original."""
        log_path = self.logs_dir / "adapters_audit.json"
        entrada = {
            "timestamp_utc": resultado.timestamp_utc,
            "adapter": resultado.adapter_name,
            "symbol": resultado.symbol,
            "is_degraded": resultado.is_degraded,
            "rows_fetched": resultado.rows_fetched,
            "latency_ms": round(resultado.latency_ms, 1),
            "error": resultado.error_msg,
        }
        historial: list = []
        if log_path.exists():
            try:
                historial = json.loads(log_path.read_text())
            except (json.JSONDecodeError, OSError):
                historial = []
        historial.append(entrada)
        historial = historial[-200:]
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(historial, indent=2, ensure_ascii=False))
