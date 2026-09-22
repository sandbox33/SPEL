"""
ingestion/run_gdelt.py
========================
Entry point de la ingesta GDELT. El archivo que `.github/workflows/tests.yml`
(línea 13) da por existente desde el patch 0011 y que no existía hasta hoy --
ese hueco es lo que cerró la Incógnita #1 de ESTADO.md: no había nada que
verificar porque no había nada que correr. `ingestion/gdelt.py` tenía cero
consumidores; este módulo es el primero.

Encadena las tres piezas que ya existían sueltas, sin reimplementar ninguna:

    gdelt.py::GDELTDailyAdapter.fetch_day(día)   -> eventos crudos
    gdelt_aggregation.py::aggregate_day(...)     -> una fila de señales
    gdelt_series.py::append_day(fila)            -> JSONL persistente

══ INCREMENTAL Y REANUDABLE, QUE NO ES LO MISMO ══

INCREMENTAL: arranca en `last_day(asset) + 1 día`, no en cero. Es el patrón
de `spel_ingest_incremental.py::ingest_asset` (legacy, auditado línea por
línea): leer el último día guardado, calcular el gap, salir temprano si no
hay. Lo que NO se porta de ahí está abajo, en su propia sección.

REANUDABLE: se escribe con `append_day()` día por día, dentro del bucle, no
al final. Si el job se corta a la mitad, los días ya escritos quedan
escritos y la próxima corrida arranca en el siguiente. No hay un buffer que
se pierda ni una transacción que revertir.

De ahí sale una regla que parece un detalle y no lo es: **un día sin datos
también se escribe**. Si GDELT devuelve 404 (no publicó nada ese día) o si
quedan menos de `MIN_EVENTS_FOR_VALID_DAY` eventos tras filtrar, la fila se
guarda igual con `n_events=0`/`insufficient_events=True`. Saltearla dejaría
`last_day()` clavado en el día anterior para siempre, y la ingesta se
quedaría golpeando el mismo día muerto en cada corrida. La fila se produce
con `aggregate_day([], asset, día)` -- la misma función, no un dataclass
armado a mano, para que el "día vacío" tenga exactamente la forma que
tendría cualquier otro día vacío.

══ LO QUE NO SE PORTA DEL LEGACY, A PROPÓSITO ══

`spel_ingest_incremental.py::ingest_asset` (líneas ~330-340) hace esto
cuando GDELT no devuelve datos:

    day_data = hist_window[-1].copy()   # copia el día anterior
    print(f"usando fallback (GDELT sin datos)")

Y `fetch_gdelt_day` devuelve constantes inventadas cuando falta una columna:
`goldstein_mean = 1.845`, `tone_variance = 117.0`, `zipf = 0.0002`,
`entropy = 1.0`. Eso escribe un dato que nadie midió y lo deja
indistinguible de uno real en el JSONL -- un día de calma perfecta y un día
que GDELT no publicó terminan escritos igual. Es exactamente la clase de
cosa contra la que `DailyAggregationResult` tiene un campo
`insufficient_events` en vez de un 0.0 disfrazado. No se porta: acá el día
vacío se escribe VACÍO y marcado.

Tampoco se porta el filtro por keywords sobre Actor1Name/Actor2Name del
legacy: `aggregate_day()` ya filtra por país con
`core.scoring.classify_gdelt_event()`, que es el port de
`gdelt_foundation.py::ASSET_COUNTRY_FILTERS`. Dos filtros distintos para lo
mismo serían dos criterios que pueden divergir.

Y no se porta el `SHA_REGISTRY` ni el chequeo de SHA previo: ese mecanismo
existía para proteger parquets del data lake legacy, y la serie de acá es
JSONL append-only, donde el equivalente es que `read_series()` deduplica.

══ DESCARGA COMPARTIDA ENTRE ACTIVOS ══

El archivo diario de GDELT es UNO para todos los activos: el filtro por país
se aplica después, al agregar. El legacy bajaba el día una vez POR ACTIVO
(`fetch_gdelt_day` dentro del bucle de `ingest_asset`, que corre por activo)
-- con 5 activos eso son 5 descargas del mismo archivo. Acá el día se baja
una vez y se agrega N veces. No es una optimización prematura: con
`--max-days 10` y 5 activos son 10 descargas en vez de 50, y el techo de
tiempo de un job de Actions es real.

══ CI ES EL ESCRITOR ÚNICO. DESDE EL 21-SEP-2026 ══

`.github/workflows/gdelt.yml` corre este script con `--write` todos los
días, apuntando `SPEL_DRIVE_ROOT` a un checkout de la rama huérfana `data`,
y commitea ahí lo que escribió. Drive dejó de ser escritor, y NINGÚN
notebook vuelve a correr `run_gdelt --write`. Ver `decision-log.md`
(enmienda a la Decisión #14).

EL MOTIVO, que es el mismo que antes impedía que CI escribiera:
`append_day()` es append puro y `read_series()` deduplica por día. Ese par
tolera DUPLICADOS pero no DIVERGENCIA -- con dos escritores quedarían dos
series con días distintos y nada que las reconcilie. Antes se resolvía
dejando a Drive como único escritor; ahora se resuelve dejando a CI. Lo que
no cambia es que haya UNO.

Lo que antes lo impedía -- Colab bajó 640 días en 17 minutos, y con
`--max-days 10` eso eran 64 corridas de Actions -- ya no aplica: la
historia llega sembrada a mano en la rama `data` (subir un archivo por la
web no es escribir código), y CI solo cierra el gap desde ahí. Con el cron
diario, el gap de régimen es de un día.

LO QUE ESTE SCRIPT SIGUE HACIENDO EN DRY-RUN, que es el default:

  1. MIDE EL GAP. Reporta, por activo, cuántos días faltan entre
     `last_day(asset)` y el último día publicado.
  2. PRUEBA LA CADENA ENTERA HASTA UN PASO ANTES DE ESCRIBIR. Descarga real
     de GDELT, unzip, parseo de las 57 columnas, `aggregate_day()`, y el
     conteo de lo que escribiría. Si la descarga falla o el formato cambia,
     el script sale != 0 y el workflow se pone rojo.

  Sin `SPEL_DRIVE_ROOT` apuntando a la serie, `drive_root()` cae al fallback
  local (`.spel_drive_stream`, que no existe en un clon recién hecho) y
  todos los activos salen SIN_SERIE. Eso NO es un gap de cero y el reporte
  lo imprime distinto a propósito ("gap NO MEDIDO", nunca "0").

══ DÍAS QUE GDELT TODAVÍA NO HABÍA PUBLICADO ══

Si GDELT publica el día anterior después de las 06:30 UTC, el cron lo
escribe vacío (404) y `last_day()` avanza. Antes de avanzar, cada corrida
vuelve a pedir esos días -- ver `run_ingesta` y `ingestion/frescura.py`,
que define cuándo un día cuenta como no publicado. Un día vacío SOLO en
EURUSD no se reintenta: eso es su filtro, no GDELT.

══ `--since` REESCRIBE ══

El cron nunca lo pasa. Un `workflow_dispatch` puede, y con `--write` los
días que ya estaban en la serie se vuelven a escribir: `read_series()` se
queda con la última fila, así que el reproceso GANA. El script lo avisa en
el log; no es un error, es la semántica de la serie.

══ LOS CÓDIGOS DE SALIDA DICEN COSAS DISTINTAS ══

  0  La corrida se completó. Incluye el caso "la serie está al día y no
     había nada que bajar" -- que se dice explícitamente en el reporte, no
     se deduce de un silencio.
  1  LA CADENA SE ROMPIÓ: la descarga falló o el formato de GDELT cambió.
     Esto es lo que pone el workflow en rojo, y es deliberado que exista.
     `tools/import_gdelt_entropy.py` y `tools/heartbeat.py` salen 0 pase lo
     que pase porque son reportes; este además es un chequeo de que la
     fuente sigue viva, y un chequeo que nunca falla no es un chequeo.
  2  Fallo de invocación (activo desconocido, flags contradictorios) --
     misma convención que tools/measure_godel_samples.py y heartbeat.py.

`--exigir-serie BTC XAU` sale 0 SIN BAJAR NADA si alguno de esos activos no
tiene serie todavía. Es la guarda contra una carrera concreta: el cron queda
activo al fusionar, y si dispara antes de que la siembra esté subida,
empezaría BTC y XAU desde los últimos 10 días -- y la marca de inicio
quedaría fijada ahí, con la siembra llegando después por debajo.

Uso:
    python ingestion/run_gdelt.py                      # dry-run (default)
    python ingestion/run_gdelt.py --max-days 3
    python ingestion/run_gdelt.py --assets BTC XAU
    python ingestion/run_gdelt.py --since 2026-09-01   # días explícitos
    python ingestion/run_gdelt.py --write              # escribe de verdad
    python ingestion/run_gdelt.py --write --exigir-serie BTC XAU   # lo de CI
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Sequence

# Mismo idiom que tools/*: este archivo se corre como script
# (`python ingestion/run_gdelt.py`), y ahí sys.path[0] es ingestion/, no la
# raíz -- sin esto, `from ingestion.gdelt import ...` no resuelve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from governance.persistence import PersistenceStream, stream_path  # noqa: E402
from ingestion.adapters import (  # noqa: E402
    AdapterConnectionError,
    AdapterDataError,
)
from ingestion.gdelt import GDELTDailyAdapter  # noqa: E402
from ingestion.gdelt_aggregation import (  # noqa: E402
    MIN_EVENTS_FOR_VALID_DAY,
    DailyAggregationResult,
    aggregate_day,
)
from ingestion.frescura import (  # noqa: E402
    dias_no_publicados,
    indexar,
    leer_marca_de_inicio,
    registrar_inicio_si_falta,
)
from ingestion.gdelt_series import append_day, last_day  # noqa: E402
from orchestration.cycle import DEFAULT_CYCLE_ASSETS  # noqa: E402

logger = logging.getLogger("spel.ingestion.run_gdelt")

#: Default BAJO a propósito. Un job de GitHub Actions tiene techo de tiempo
#: y GDELT tarda ~3 s por día entre descarga y unzip; 10 días son ~30 s de
#: red, que entra cómodo. Subirlo es una decisión de quien corre, no el
#: default -- un default alto convierte cada corrida en una apuesta contra
#: el timeout.
DEFAULT_MAX_DAYS = 10

#: GDELT 1.0 publica el archivo de un día DESPUÉS de que el día terminó, así
#: que el último día descargable es ayer, no hoy. Pedir el de hoy devuelve
#: 404 -- que este módulo trataría como "día sin datos" y escribiría como
#: día vacío, envenenando la serie con un día que sí va a existir mañana.
#: Por eso el techo se calcula, no se asume.
DIAS_DE_RETRASO_DE_PUBLICACION = 1

#: Pausa entre descargas. Port de `spel_ingest_incremental.py`
#: (`time.sleep(0.3)  # Rate limiting GDELT`) -- el valor es el del legacy,
#: no uno elegido acá. Se nombra como constante en vez de dejarlo inline
#: para que se pueda ver y ajustar sin buscarlo en medio del bucle.
PAUSA_ENTRE_DIAS_S = 0.3


@dataclass
class AssetRun:
    """Lo que pasó con un activo en esta corrida. `gap_days` es el dato que
    convierte al dry-run en monitor: es la distancia entre lo que la serie
    tiene y lo que GDELT ya publicó, independiente de cuántos días esta
    corrida alcance a procesar (que lo limita --max-days)."""
    asset: str
    status: str
    last_day: Optional[str] = None
    gap_days: Optional[int] = None
    days_planned: int = 0
    first_planned: Optional[str] = None
    last_planned: Optional[str] = None
    days_processed: int = 0
    days_written: int = 0
    days_no_data: int = 0
    days_insufficient: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class RunReport:
    write: bool
    today: str
    latest_available: str
    max_days: int
    #: De dónde se leyó la serie. Va en el reporte porque el gap se mide
    #: contra esto: en Actions resuelve al fallback local, que está vacío, y
    #: sin este dato un "sin serie" se lee como "la serie está vacía" en vez
    #: de como "no estoy mirando donde vive la serie".
    series_root: str = ""
    downloads: int = 0
    assets: list[AssetRun] = field(default_factory=list)
    chain_error: Optional[str] = None
    #: Reconciliación de días que GDELT no había publicado (ver
    #: `run_ingesta`). Se cuentan DÍAS, no filas: un día reintentado vale
    #: para todos los activos a la vez porque sale del mismo archivo.
    reintentos_pedidos: int = 0
    reintentos_curados: int = 0
    reintentos_siguen_vacios: int = 0
    #: La marca de inicio de la automatización, si esta corrida la fijó.
    marca_fijada: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════
#  Gap: qué días le faltan a cada activo
# ══════════════════════════════════════════════════════════════════════════

def ultimo_dia_disponible(hoy: date) -> date:
    return hoy - timedelta(days=DIAS_DE_RETRASO_DE_PUBLICACION)


def planificar_asset(
    asset: str,
    *,
    hoy: date,
    max_days: int,
    since: Optional[date] = None,
) -> tuple[AssetRun, list[date]]:
    """
    Calcula el gap de un activo y qué días procesaría esta corrida.

    EL GAP Y LOS DÍAS A PROCESAR SON DOS NÚMEROS DISTINTOS, y el reporte
    muestra los dos: el gap es todo lo que falta, `days_planned` es lo que
    entra en esta corrida con el `--max-days` vigente. Colapsarlos haría
    que un gap de 640 días se lea como 10 y que el monitor no sirva para lo
    único que tiene que servir.

    SE PROCESA DEL MÁS VIEJO AL MÁS NUEVO. Es lo que hace que `--write` sea
    reanudable: `last_day()` avanza de a uno y la próxima corrida sigue
    donde quedó. Al revés (los más recientes primero) `last_day()` saltaría
    al final en la primera corrida y los días del medio no se pedirían
    nunca.

    SERIE VACÍA: no hay `last_day()` del cual ser incremental, así que no se
    inventa un origen. Se toma la ventana de `max_days` días más recientes
    y se marca SIN_SERIE en el reporte. Una carga histórica no se hace desde
    acá: se siembra el archivo en la rama `data` (ver `--exigir-serie`).
    """
    tope = ultimo_dia_disponible(hoy)
    ultimo = last_day(asset)

    if since is not None:
        desde = since
        run = AssetRun(asset=asset, status="DESDE_EXPLICITO",
                       last_day=str(ultimo) if ultimo else None)
        if ultimo is not None:
            run.gap_days = max(0, (tope - ultimo).days)
        run.notes.append(f"--since {since}: se ignora last_day() para elegir "
                         f"el origen; el gap reportado sigue siendo el real.")
    elif ultimo is None:
        desde = tope - timedelta(days=max_days - 1)
        run = AssetRun(asset=asset, status="SIN_SERIE")
        run.notes.append(
            f"No hay serie para {asset}: no hay last_day() del cual ser "
            f"incremental. Se toman los últimos {max_days} día(s); la carga "
            f"histórica va por siembra en la rama `data`, no por acá.")
    else:
        desde = ultimo + timedelta(days=1)
        run = AssetRun(asset=asset, status="CON_GAP", last_day=str(ultimo),
                       gap_days=max(0, (tope - ultimo).days))

    if desde > tope:
        run.status = "AL_DIA"
        run.gap_days = 0
        run.notes.append(
            f"La serie está al día: último guardado {ultimo}, último "
            f"publicado por GDELT {tope}. No hay nada que bajar.")
        return run, []

    dias = [desde + timedelta(days=k) for k in range((tope - desde).days + 1)]
    dias = dias[:max_days]

    run.days_planned = len(dias)
    run.first_planned = str(dias[0])
    run.last_planned = str(dias[-1])
    if run.gap_days is not None and run.gap_days > len(dias):
        run.notes.append(
            f"El gap ({run.gap_days} días) excede --max-days ({max_days}): "
            f"esta corrida cubre {len(dias)}. Hacen falta "
            f"{-(-run.gap_days // max_days)} corridas a este ritmo.")
    return run, dias


# ══════════════════════════════════════════════════════════════════════════
#  La corrida
# ══════════════════════════════════════════════════════════════════════════

async def run_ingesta(
    assets: Sequence[str],
    *,
    hoy: date,
    max_days: int = DEFAULT_MAX_DAYS,
    since: Optional[date] = None,
    write: bool = False,
    adapter: Optional[GDELTDailyAdapter] = None,
    pausa_s: Optional[float] = None,
) -> RunReport:
    """
    Baja cada día UNA vez y lo agrega para todos los activos que lo
    necesitan (ver la sección de descarga compartida en el docstring del
    módulo).

    `write=False` (el default, y lo que corre el workflow) hace todo salvo
    `append_day()`: baja, descomprime, parsea, agrega y cuenta. Es la
    diferencia entre un dry-run que prueba la cadena y uno que solo imprime
    intenciones.

    UN FALLO DE DESCARGA ABORTA LA CORRIDA, no se saltea el día. Con
    `--write`, lo ya escrito queda escrito (append_day() escribe dentro del
    bucle) y la próxima corrida sigue desde ahí; sin `--write`, la cadena se
    rompió y eso es justamente lo que el workflow tiene que reportar en
    rojo. Saltear el día en silencio convertiría "GDELT cambió de formato"
    en una corrida verde con menos filas.
    """
    adapter = adapter or GDELTDailyAdapter()
    # Se resuelve acá y no en la firma para que la constante siga siendo el
    # único lugar donde vive el valor: un default evaluado al definir la
    # función congelaría el número al importar el módulo.
    pausa_s = PAUSA_ENTRE_DIAS_S if pausa_s is None else pausa_s
    tope = ultimo_dia_disponible(hoy)
    report = RunReport(write=write, today=str(hoy), latest_available=str(tope),
                       max_days=max_days,
                       series_root=stream_path(PersistenceStream.METRICS))

    # ── RECONCILIACIÓN: los días que GDELT no había publicado ─────────────
    # El cron corre a las 06:30 UTC y GDELT a veces publica el día anterior
    # más tarde. Ese día se escribe vacío (404) y `last_day()` avanza igual:
    # sin esto, cada publicación tardía dejaría un agujero PERMANENTE. Antes
    # de avanzar, se vuelven a pedir.
    #
    # Cuáles: los de `frescura.dias_no_publicados` -- todos los activos en
    # cero y al menos uno CORE --, solo desde la marca de inicio (lo que CI
    # escribió; la historia heredada es deuda, no se toca) y solo sin
    # `--since`, que es reproceso explícito de un humano.
    #
    # CONSUMEN EL MISMO PRESUPUESTO de `max_days`: no se agrega ninguna
    # constante. Un día que GDELT nunca publique va a costar una descarga por
    # corrida para siempre -- y va a dejar la alarma en rojo en cuanto haya
    # un día posterior con datos, que es el aviso de que hace falta mirarlo.
    desde_ci = leer_marca_de_inicio()
    reintentos: list[date] = []
    indice = {}
    if desde_ci is not None and since is None:
        indice = indexar(assets)
        reintentos = sorted(dias_no_publicados(indice, desde=desde_ci, hasta=tope))

    planes: dict[str, list[date]] = {}
    reintentos_de: dict[str, list[date]] = {}
    for asset in assets:
        run, dias = planificar_asset(asset, hoy=hoy, max_days=max_days, since=since)
        propios = [d for d in reintentos if d in indice.get(asset, {})][:max_days]
        restante = max_days - len(propios)
        if len(dias) > restante:
            dias = dias[:restante]
            run.days_planned = len(dias)
            run.first_planned = str(dias[0]) if dias else None
            run.last_planned = str(dias[-1]) if dias else None
        if propios:
            run.notes.append(
                f"{len(propios)} día(s) que GDELT no había publicado se vuelven a "
                f"pedir antes de avanzar; consumen el mismo presupuesto de "
                f"--max-days ({max_days}).")
        report.assets.append(run)
        planes[asset] = dias
        reintentos_de[asset] = propios

    #: {día: [(activo, es_reintento)]}. Un día se baja UNA vez aunque sea
    #: reintento para unos activos y día nuevo para otros.
    por_dia: dict[date, list[tuple[str, bool]]] = {}
    for asset, dias in planes.items():
        for dia in dias:
            por_dia.setdefault(dia, []).append((asset, False))
    for asset, dias in reintentos_de.items():
        for dia in dias:
            por_dia.setdefault(dia, []).append((asset, True))

    runs = {r.asset: r for r in report.assets}
    dias_reintentados = {d for ds in reintentos_de.values() for d in ds}
    report.reintentos_pedidos = len(dias_reintentados)
    primer_dia_nuevo_escrito: Optional[date] = None

    for i, dia in enumerate(sorted(por_dia)):
        if i and pausa_s:
            time.sleep(pausa_s)   # rate limiting, port del legacy
        try:
            resultado = await adapter.fetch_day(dia)
        except (AdapterConnectionError, AdapterDataError) as exc:
            report.chain_error = (
                f"La cadena se rompió en {dia}: {type(exc).__name__}: {exc}")
            return report

        report.downloads += 1
        eventos = resultado.events

        if dia in dias_reintentados:
            if resultado.no_data:
                report.reintentos_siguen_vacios += 1
            else:
                report.reintentos_curados += 1

        for asset, es_reintento in por_dia[dia]:
            if es_reintento and resultado.no_data:
                # Sigue sin publicarse: NO se vuelve a escribir la fila vacía.
                # Ya está en la serie, y reescribirla haría crecer el archivo
                # una línea por activo por corrida sin agregar información.
                continue
            fila = aggregate_day(eventos, asset, dia)
            _contabilizar(runs[asset], fila, resultado.no_data)
            if write:
                append_day(fila)
                runs[asset].days_written += 1
                if not es_reintento and (primer_dia_nuevo_escrito is None
                                         or dia < primer_dia_nuevo_escrito):
                    primer_dia_nuevo_escrito = dia

    # La marca de inicio se fija con el PRIMER día que escribió CI, una sola
    # vez. No se conoce antes: por eso la rama `data` nace con
    # `{"desde": null}`. Ver ingestion/frescura.py. (En dry-run
    # `primer_dia_nuevo_escrito` queda en None: solo se asigna al escribir.)
    if primer_dia_nuevo_escrito is not None:
        if registrar_inicio_si_falta(primer_dia_nuevo_escrito):
            report.marca_fijada = str(primer_dia_nuevo_escrito)

    for run in report.assets:
        if run.days_planned and run.status not in ("AL_DIA",):
            run.status = "ESCRITO" if write else "DRY_RUN"

    return report


def _contabilizar(
    run: AssetRun, fila: DailyAggregationResult, no_data: bool,
) -> None:
    run.days_processed += 1
    if no_data:
        run.days_no_data += 1
    if fila.insufficient_events:
        run.days_insufficient += 1


# ══════════════════════════════════════════════════════════════════════════
#  Reporte
# ══════════════════════════════════════════════════════════════════════════

def render_text(report: RunReport) -> str:
    out = [
        "═══ INGESTA GDELT ═══",
        (f"MODO ESCRITURA — se escribió en la serie canónica."
         if report.write else
         "DRY-RUN — no se escribió nada. La cadena se probó entera hasta un "
         "paso antes de append_day(). Usar --write para escribir."),
        f"hoy: {report.today}   último día publicado por GDELT: "
        f"{report.latest_available}   --max-days: {report.max_days}",
        f"descargas de GDELT en esta corrida: {report.downloads} "
        f"(un archivo por día, compartido entre activos)",
        "",
    ]

    for r in report.assets:
        out.append(f"── {r.asset} " + "─" * max(0, 58 - len(r.asset)))
        out.append(f"  estado: {r.status}")
        out.append(f"  último día en la serie: {r.last_day or '—'}   "
                   f"GAP: {r.gap_days if r.gap_days is not None else '—'} día(s)")
        if r.days_planned:
            out.append(f"  días de esta corrida: {r.days_planned} "
                       f"({r.first_planned} .. {r.last_planned})")
            out.append(f"  procesados: {r.days_processed}   "
                       f"escritos: {r.days_written}"
                       f"{'' if report.write else ' (dry-run: 0 por definición)'}")
            if r.days_no_data:
                out.append(f"  GDELT no publicó nada en {r.days_no_data} día(s) "
                           f"— se escriben vacíos y marcados, no se saltean")
            if r.days_insufficient:
                out.append(f"  días con < {MIN_EVENTS_FOR_VALID_DAY} eventos tras "
                           f"filtrar: {r.days_insufficient} → "
                           f"insufficient_events=True")
        for nota in r.notes:
            out.append(f"  · {nota}")
        out.append("")

    medidos = [r for r in report.assets if r.gap_days is not None]
    sin_medir = [r for r in report.assets if r.gap_days is None]
    escritos = sum(r.days_written for r in report.assets)

    # El gap de un activo SIN SERIE es DESCONOCIDO, no cero, y los dos se
    # imprimen distinto. Sumar los medidos e ignorar los otros daría
    # "gap acumulado: 0" cuando la verdad es "no se pudo medir ninguno" --
    # un cero que parece una medición, que es justo lo que este proyecto no
    # imprime.
    if medidos:
        out.append(f"TOTAL: gap acumulado {sum(r.gap_days for r in medidos)} "
                   f"día(s) sobre {len(medidos)} activo(s) medido(s); "
                   f"{escritos} día(s) escritos.")
    else:
        out.append(f"TOTAL: gap NO MEDIDO en ninguno de los "
                   f"{len(report.assets)} activos; {escritos} día(s) escritos.")
    if sin_medir:
        out.append(f"  Sin gap medible ({len(sin_medir)}): "
                   f"{', '.join(r.asset for r in sin_medir)} — no hay serie "
                   f"que leer en {report.series_root}. Si SPEL_DRIVE_ROOT no "
                   f"apunta a un checkout de la rama `data`, es lo esperado. "
                   f"No es un gap de cero.")
    if report.reintentos_pedidos:
        out.append(f"RECONCILIACIÓN: {report.reintentos_pedidos} día(s) que "
                   f"GDELT no había publicado se volvieron a pedir — "
                   f"curados: {report.reintentos_curados}, siguen vacíos: "
                   f"{report.reintentos_siguen_vacios}.")
    if report.marca_fijada:
        out.append(f"MARCA DE INICIO fijada en {report.marca_fijada}: la "
                   f"alarma de frescura evalúa desde ese día. No se mueve más.")
    if not report.write:
        out.append("  El gap NO se cerró: sin --write este script no escribe. "
                   "El escritor único de la serie es el workflow gdelt.yml, "
                   "sobre la rama `data` (ver el docstring del módulo).")
    if report.chain_error:
        out.append("")
        out.append(f"ERROR DE CADENA: {report.chain_error}")
    return "\n".join(out)


# ══════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_gdelt",
        description="Ingesta incremental de GDELT a la serie canónica JSONL. "
                    "Dry-run por defecto.",
    )
    p.add_argument(
        "--assets", nargs="+", default=list(DEFAULT_CYCLE_ASSETS),
        help=f"Activos a procesar. Default: los del ciclo "
             f"({', '.join(DEFAULT_CYCLE_ASSETS)}).",
    )
    p.add_argument(
        "--max-days", type=int, default=DEFAULT_MAX_DAYS,
        help=f"Techo de días por corrida. Default {DEFAULT_MAX_DAYS}, bajo a "
             f"propósito: Actions tiene límite de tiempo y GDELT tarda ~3 s "
             f"por día.",
    )
    p.add_argument(
        "--since", default=None,
        help="Fecha ISO (YYYY-MM-DD) desde la cual bajar, ignorando "
             "last_day(). Para apuntar la prueba de cadena a días recientes "
             "cuando el gap es grande, o para un backfill acotado.",
    )
    p.add_argument(
        "--dry-run", action="store_true", default=True,
        help="No escribe nada. ES EL DEFAULT y está acá para poder pedirlo "
             "explícitamente; no hace falta pasarlo.",
    )
    p.add_argument(
        "--write", action="store_true",
        help="Escribe de verdad con append_day(). Lo usa SOLO el workflow "
             "gdelt.yml, escritor único de la serie en la rama `data`. "
             "Ningún notebook lo corre (ver el docstring del módulo).",
    )
    p.add_argument(
        "--exigir-serie", nargs="+", default=None, metavar="ASSET",
        help="Si alguno de estos activos no tiene serie, sale 0 sin bajar "
             "nada. Guarda contra un cron que dispara antes de la siembra.",
    )
    p.add_argument("--format", choices=("text", "json"), default="text")
    return p


def main(argv: Optional[Sequence[str]] = None, *, hoy: Optional[date] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    # `--dry-run` es el default, así que pasarlo JUNTO a `--write` no es una
    # preferencia ambigua: es una contradicción explícita. Elegir uno en
    # silencio sería adivinar cuál quiso decir quien lo escribió.
    if args.write and "--dry-run" in (argv if argv is not None else sys.argv[1:]):
        print("ERROR: --dry-run y --write se contradicen. --dry-run ya es el "
              "default; pasa solo --write si quieres escribir.", file=sys.stderr)
        return 2

    if args.max_days < 1:
        print(f"ERROR: --max-days tiene que ser >= 1 (recibido {args.max_days}).",
              file=sys.stderr)
        return 2

    since: Optional[date] = None
    if args.since:
        try:
            since = date.fromisoformat(args.since)
        except ValueError:
            print(f"ERROR: --since no es una fecha ISO válida: {args.since!r}",
                  file=sys.stderr)
            return 2

    if args.exigir_serie:
        sin_serie = [a for a in args.exigir_serie if last_day(a) is None]
        if sin_serie:
            print(f"SIEMBRA PENDIENTE: {', '.join(sin_serie)} sin serie en "
                  f"{stream_path(PersistenceStream.METRICS)}. No se baja ni "
                  f"se escribe nada hasta que la siembra esté subida a la "
                  f"rama `data`. No es un error.")
            return 0

    if since is not None and args.write:
        # No es un error: es la semántica de la serie. Pero tiene que quedar
        # en el log de la corrida, porque cambia días que ya estaban.
        print(f"AVISO: --since {since} con --write REESCRIBE los días que ya "
              f"estaban en la serie. read_series() se queda con la última "
              f"fila escrita: el reproceso gana.", file=sys.stderr)

    report = asyncio.run(run_ingesta(
        args.assets, hoy=hoy or date.today(), max_days=args.max_days,
        since=since, write=args.write,
    ))

    if args.format == "json":
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(render_text(report))

    # Exit 1 SOLO si la cadena se rompió -- ver los códigos de salida en el
    # docstring del módulo. Un gap grande no es un fallo del proceso: es el
    # dato que el monitor tiene que reportar, y ponerlo en rojo entrenaría a
    # ignorar el rojo (mismo razonamiento que tools/heartbeat.py).
    if report.chain_error:
        print(f"ERROR: {report.chain_error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
