"""
ingestion/gdelt.py
====================
Descarga y parseo de GDELT 1.0 (eventos diarios) -- la pieza que Fase 1
del BLUEPRINT marca como bloqueante real (5,773 líneas legacy, 0%
portado hasta este patch).

Por qué NO implementa BaseAdapter (ingestion/adapters.py): esa interfaz
está diseñada para OHLCV -- fetch_ohlcv() devuelve velas de precio
(open/high/low/close/volume). GDELT no es eso. Es un evento de noticia
por fila, agregado a nivel DÍA (no vela intradía), con columnas que no
tienen análogo de precio (goldstein, tono, país de actor). Forzarlo
dentro de BaseAdapter para reusar la interfaz sería el tipo de cosa que
este proyecto viene evitando en cada decisión: código que "funciona"
en el sentido de que compila, pero miente sobre lo que hace.

Reutiliza SÍ las excepciones tipadas de adapters.py (AdapterConnectionError,
AdapterDataError) -- el contrato de "nunca dejar escapar una excepción
cruda de requests/zipfile" aplica igual acá, no hay razón para una
jerarquía de excepciones paralela.

FUENTE (gdelt_foundation.py::GDELTDownloader, verificada línea por
línea, no reescrita de memoria):
  URL: data.gdeltproject.org/events/YYYYMMDD.export.CSV.zip
  El legacy la escribe con `http://`; acá va con `https://` y siguiendo
  redirects -- ver la nota sobre GDELT_BASE_URL, que es el único punto
  de este módulo donde la fuente NO se porta literal.
  Formato: ZIP conteniendo un CSV tab-separated, sin header, 57
  columnas, encoding latin-1. Solo 8 columnas importan (ver GDELT_COLS).
  404 = GDELT no tiene datos para ese día (festivo/fin de semana en su
  calendario de publicación) -- NO es un error, es un resultado válido.

VERIFICACIÓN DE RED PENDIENTE, explícita: este sandbox no tiene
data.gdeltproject.org en su whitelist de red (confirmado: HEAD request
devuelve 403 desde acá, con Content-Length de solo 108 bytes -- typical
de un bloqueo de proxy egress, no de una respuesta real del servidor).
Un índice público (data.gdeltproject.org/events/index.html, consultado
vía web_search, que sí atraviesa la restricción) muestra archivos
frescos hasta 2026-07-17 -- el servidor está vivo. Pero esta sesión NO
pudo hacer una descarga real end-to-end para confirmarlo con certeza
total. GitHub Actions (ya existe, .github/workflows/tests.yml) no
tiene esta restricción de red -- ahí sí se puede verificar en el
primer run real. Los tests de este patch usan un ZIP construido en
memoria (mismo formato exacto), no la red real -- ver test_gdelt.py.
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

import httpx

from ingestion.adapters import AdapterConnectionError, AdapterDataError

logger = logging.getLogger("spel.ingestion.gdelt")

#: HTTPS, no HTTP. El legacy (gdelt_foundation.py:81) usa `http://` y este
#: módulo lo portó tal cual -- pero GDELT migró a HTTPS y el esquema viejo
#: hoy responde con un redirect, no con el ZIP. httpx NO sigue redirects
#: por defecto (a diferencia de requests, que era lo que usaba el legacy y
#: por eso el legacy nunca notó la migración): un 301 caía en la rama
#: "status inesperado" de fetch_day() y se convertía en
#: AdapterConnectionError. O sea: portar la URL literal del legacy
#: rompía la descarga, y el test suite no lo veía porque ningún test toca
#: la red real.
#:
#: NO VERIFICADO CONTRA LA RED REAL desde esta sesión: el sandbox no tiene
#: data.gdeltproject.org en su whitelist (probado hoy: CONNECT devuelve 403
#: para https y 403 para http, o sea el bloqueo es del proxy egress, no
#: una respuesta de GDELT). La primera corrida manual de gdelt.yml en
#: GitHub Actions es lo que lo confirma end-to-end.
GDELT_BASE_URL = "https://data.gdeltproject.org/events"

#: Se sigue el redirect igual, aunque la URL ya sea HTTPS. Las dos cosas
#: son necesarias y ninguna sustituye a la otra: el esquema correcto evita
#: el salto en el caso normal, y `follow_redirects` es lo que impide que
#: un cambio futuro de ruta del lado de GDELT (un 301 de /events a otro
#: prefijo) vuelva a manifestarse como un error de conexión en vez de como
#: una descarga que funciona.
FOLLOW_REDIRECTS = True

#: gdelt_foundation.py::GDELT_COLS -- índices 0-based dentro de las 57
#: columnas del CSV de GDELT 1.0. Solo estas 8 importan para SPEL.
GDELT_COL_INDICES: dict[str, int] = {
    "date_int": 1,
    "country1": 7,
    "country2": 17,
    "goldstein": 29,
    "num_mentions": 30,
    "num_sources": 31,
    "num_articles": 32,
    "avg_tone": 33,
}

GDELT_TOTAL_COLS = 57

#: Timeout de descarga -- gdelt_foundation.py no nombraba una constante
#: para esto (usaba un valor inline); se nombra acá para que sea
#: ajustable sin buscar el número en medio del código.
DOWNLOAD_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class GdeltDayResult:
    """Un día de eventos GDELT ya parseados. `events` es una lista de
    dicts (no un DataFrame ni un objeto Polars) -- deliberado: este
    módulo no asume qué hace el consumidor con los datos (agregarlos a
    entropía diaria, como hacía gdelt_foundation.py, es responsabilidad
    de core/scoring.py o de un futuro pipeline de agregación, no de
    este adapter). `no_data` distingue explícitamente "GDELT no publicó
    nada este día" (404, válido) de "se descargó pero venía vacío/roto"
    (available=False sin no_data)."""
    day: date
    events: list[dict]
    available: bool
    no_data: bool


class GDELTDailyAdapter:
    """
    Descarga y parsea un día de eventos GDELT 1.0.

    NO implementa BaseAdapter -- ver docstring del módulo para el
    razonamiento. Contrato propio, mismo nivel de rigor:
      - fetch_day() SIEMPRE devuelve GdeltDayResult, nunca None, nunca
        deja escapar una excepción cruda de httpx/zipfile.
      - 404 de GDELT se traduce a GdeltDayResult(available=False,
        no_data=True) -- resultado válido, no una excepción.
      - Cualquier otro fallo de red (timeout, 5xx, conexión rechazada)
        lanza AdapterConnectionError -- mismo tipo que ingestion/adapters.py,
        para que un AdapterChain futuro pueda tratarlo igual si hace
        falta encadenar fuentes de GDELT (espejo del patrón, no
        implementado en este patch -- no hay una segunda fuente GDELT
        gratuita conocida para encadenar todavía).
      - Un ZIP corrupto o un CSV que no tiene las columnas esperadas
        lanza AdapterDataError -- la fuente respondió, pero el
        contenido no es utilizable.
    """

    source_name = "gdelt_1.0_daily"

    def __init__(
        self,
        *,
        timeout_s: float = DOWNLOAD_TIMEOUT_S,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        """`transport` existe para los tests, y con un motivo concreto que
        no es "inyectar por inyectar": los tests de este módulo simulaban
        la descarga REIMPLEMENTANDO el cuerpo de fetch_day() alrededor de
        un MockTransport, así que nunca ejercitaban la construcción real
        del AsyncClient -- que es exactamente donde vive `follow_redirects`.
        Un test del redirect escrito sobre esa copia habría probado la
        copia, no el código. Con esto el test corre el fetch_day() de
        verdad."""
        self._timeout_s = timeout_s
        self._transport = transport

    def _build_client(self) -> httpx.AsyncClient:
        kwargs = {"timeout": self._timeout_s, "follow_redirects": FOLLOW_REDIRECTS}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    def _url_for_day(self, day: date) -> str:
        return f"{GDELT_BASE_URL}/{day.strftime('%Y%m%d')}.export.CSV.zip"

    async def fetch_day(self, day: date) -> GdeltDayResult:
        url = self._url_for_day(day)
        try:
            async with self._build_client() as client:
                resp = await client.get(url)
        except httpx.TimeoutException as e:
            raise AdapterConnectionError(f"{self.source_name}: timeout descargando {day}: {e}") from e
        except httpx.RequestError as e:
            raise AdapterConnectionError(f"{self.source_name}: error de red descargando {day}: {e}") from e

        if resp.status_code == 404:
            logger.debug("%s: sin datos para %s (404, válido)", self.source_name, day)
            return GdeltDayResult(day=day, events=[], available=False, no_data=True)

        if resp.status_code >= 500:
            raise AdapterConnectionError(
                f"{self.source_name}: error de servidor ({resp.status_code}) para {day}"
            )
        if resp.status_code != 200:
            raise AdapterConnectionError(
                f"{self.source_name}: status inesperado ({resp.status_code}) para {day}"
            )

        return self._parse_zip(resp.content, day)

    def _parse_zip(self, raw_bytes: bytes, day: date) -> GdeltDayResult:
        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                names = zf.namelist()
                if not names:
                    raise AdapterDataError(f"{self.source_name}: ZIP vacío para {day}")
                csv_bytes = zf.read(names[0])
        except zipfile.BadZipFile as e:
            raise AdapterDataError(f"{self.source_name}: ZIP corrupto para {day}: {e}") from e

        try:
            text = csv_bytes.decode("latin-1")
        except UnicodeDecodeError as e:
            raise AdapterDataError(f"{self.source_name}: encoding inválido para {day}: {e}") from e

        events: list[dict] = []
        for line in text.split("\n"):
            if not line.strip():
                continue
            fields = line.split("\t")
            if len(fields) < GDELT_TOTAL_COLS:
                continue  # fila truncada -- se descarta, no se aborta el día entero
            events.append(self._parse_row(fields))

        if not events:
            raise AdapterDataError(f"{self.source_name}: 0 filas válidas tras parsear {day}")

        return GdeltDayResult(day=day, events=events, available=True, no_data=False)

    def _parse_row(self, fields: Sequence[str]) -> dict:
        """Extrae las 8 columnas relevantes por índice -- mismo mapeo
        exacto que gdelt_foundation.py::GDELT_COLS. Castea con manejo
        de error por campo: un campo corrupto se vuelve None, no aborta
        toda la fila (una fila con 7 campos buenos y 1 malo sigue
        siendo útil para casi cualquier señal que no dependa de ese campo)."""

        def _get(name: str) -> str:
            return fields[GDELT_COL_INDICES[name]]

        def _as_float(raw: str) -> Optional[float]:
            try:
                return float(raw)
            except (ValueError, TypeError):
                return None

        def _as_int(raw: str) -> Optional[int]:
            try:
                return int(raw)
            except (ValueError, TypeError):
                return None

        return {
            "date_int": _as_int(_get("date_int")),
            "country1": _get("country1") or None,
            "country2": _get("country2") or None,
            "goldstein": _as_float(_get("goldstein")),
            "num_mentions": _as_int(_get("num_mentions")),
            "num_sources": _as_int(_get("num_sources")),
            "num_articles": _as_int(_get("num_articles")),
            "avg_tone": _as_float(_get("avg_tone")),
        }

    async def health_check(self) -> bool:
        """Nunca lanza -- mismo contrato que BaseAdapter.health_check(),
        aunque esta clase no herede de BaseAdapter. Verifica el índice
        público en vez de descargar un día completo."""
        try:
            async with self._build_client() as client:
                resp = await client.head(f"{GDELT_BASE_URL}/index.html")
            return resp.status_code == 200
        except Exception:
            return False
