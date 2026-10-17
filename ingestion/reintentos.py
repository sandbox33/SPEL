"""
ingestion/reintentos.py
=======================
Reintentos acotados de una llamada HTTP de ingesta ante fallos transitorios:
HTTP 429, 5xx y errores de red (issue #39; brief del Admin del 14-oct-2026
(9), tarea C).

══ QUÉ SE REINTENTA Y QUÉ NO ══

Se reintenta solo lo que el llamador clasifica como `Transitorio`: el 429,
los 5xx de STATUS_5XX_REINTENTABLES y los cortes de red. Nada más. Un 400
(incluido el "No data is available…" que cierra la paginación de
TwelveData, PR #38), un 401 o un 404 no son transitorios: reintentarlos
gasta crédito y no cambia la respuesta.

══ CUÁNTO SE ESPERA ══

  · Si la respuesta trae `Retry-After` (segundos o fecha HTTP), se espera
    eso.
  · Si no, un 429 espera ESPERA_429_SIN_RETRY_AFTER_S (la ventana de
    TwelveData es por minuto), y un 5xx o un corte de red, un backoff
    exponencial: BACKOFF_BASE_S · 2^(n−1), con tope BACKOFF_MAX_S.

══ TOPES ══

  · A lo sumo MAX_REINTENTOS reintentos (MAX_REINTENTOS + 1 llamadas).
  · La suma de las esperas no pasa de ESPERA_TOTAL_MAX_S: si la próxima
    espera la haría pasar, no se espera y se lanza.
  · Cada reintento pide antes su turno al limitador (`turno`); si el
    limitador dice que no hay crédito, no se reintenta y se lanza.

Agotados los reintentos, el error es explícito (`AdapterConnectionError`
con el motivo, cuántos reintentos y cuánto se esperó), y quien llama lo
trata como cualquier otro error: la ingesta de velas corta en "error" y no
escribe nada (la regla anti-hueco no cambia).

El reloj y la espera son inyectables: los tests no duermen.

[INTERPRETACIÓN] De los 5xx se reintentan 500, 502, 503 y 504. 501 y 505
dicen que el servidor no soporta el pedido: reintentar no lo arregla.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable, Optional

from ingestion.adapters import AdapterConnectionError

logger = logging.getLogger(__name__)

#: Reintentos después de la primera llamada.
MAX_REINTENTOS = 3

#: Tope de la suma de esperas de una misma llamada, en segundos.
ESPERA_TOTAL_MAX_S = 180.0

#: Espera de un 429 que no trae Retry-After, en segundos.
ESPERA_429_SIN_RETRY_AFTER_S = 60.0

#: Primera espera del backoff de un 5xx o de un corte de red, en segundos.
BACKOFF_BASE_S = 2.0

#: Tope de una espera del backoff, en segundos.
BACKOFF_MAX_S = 30.0

#: Los 5xx que se reintentan.
STATUS_5XX_REINTENTABLES = frozenset({500, 502, 503, 504})


@dataclass(frozen=True)
class Transitorio:
    """Un fallo que vale la pena reintentar. `es_429` elige la espera por
    defecto; `retry_after_s` es la del servidor, si la mandó."""
    motivo: str
    es_429: bool = False
    retry_after_s: Optional[float] = None


class FalloTransitorio(AdapterConnectionError):
    """Lo lanza la llamada única cuando la respuesta es transitoria."""

    def __init__(self, fuente: str, transitorio: Transitorio) -> None:
        super().__init__(f"[{fuente}] {transitorio.motivo}")
        self.transitorio = transitorio


def es_status_reintentable(status: object) -> bool:
    return status == 429 or status in STATUS_5XX_REINTENTABLES


def segundos_de_retry_after(valor: Optional[str], *, ahora_epoch: float) -> Optional[float]:
    """La espera que pide `Retry-After`: un entero de segundos o una fecha
    HTTP (RFC 9110 §10.2.3). None si no viene o no se entiende. Una fecha ya
    pasada es 0."""
    if valor is None:
        return None
    texto = valor.strip()
    if texto.isdigit():
        return float(texto)
    try:
        fecha = parsedate_to_datetime(texto)
    except (TypeError, ValueError):
        return None
    if fecha is None or fecha.tzinfo is None:
        return None
    return max(0.0, fecha.timestamp() - ahora_epoch)


def espera_de(t: Transitorio, reintento: int) -> float:
    """La espera antes del reintento número `reintento` (desde 1)."""
    if t.retry_after_s is not None:
        return t.retry_after_s
    if t.es_429:
        return ESPERA_429_SIN_RETRY_AFTER_S
    return min(BACKOFF_MAX_S, BACKOFF_BASE_S * 2 ** (reintento - 1))


async def reintentar(llamada: Callable[[], Awaitable[Any]], *, fuente: str,
                     turno: Callable[[], Awaitable[bool]],
                     dormir: Callable[[float], Awaitable[object]],
                     max_reintentos: int = MAX_REINTENTOS,
                     espera_total_max_s: float = ESPERA_TOTAL_MAX_S) -> Any:
    """Llama a `llamada` y la repite mientras lance `FalloTransitorio`,
    dentro de los topes. Cualquier otra excepción sale tal cual, sin
    reintentar."""
    esperado = 0.0
    reintento = 0
    while True:
        try:
            return await llamada()
        except FalloTransitorio as exc:
            t = exc.transitorio
            ultimo = exc
        reintento += 1
        espera = espera_de(t, reintento)
        if reintento > max_reintentos:
            corte = f"sin éxito tras {max_reintentos} reintentos"
        elif esperado + espera > espera_total_max_s:
            corte = (f"esperar {espera:.0f} s más pasaría el tope de {espera_total_max_s:.0f} s "
                     f"de espera total")
        elif not await turno():
            corte = "el limitador no tiene crédito para otro reintento"
        else:
            logger.warning("reintento %s", json.dumps(
                {"fuente": fuente, "reintento": reintento, "de": max_reintentos,
                 "motivo": t.motivo, "espera_s": espera,
                 "origen_espera": ("retry-after" if t.retry_after_s is not None
                                   else "429 sin retry-after" if t.es_429 else "backoff"),
                 "esperado_s": esperado}, ensure_ascii=False))
            await dormir(espera)
            esperado += espera
            continue
        raise AdapterConnectionError(
            f"[{fuente}] {t.motivo}: {corte} ({esperado:.0f} s de espera)") from ultimo
