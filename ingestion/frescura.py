"""
ingestion/frescura.py
======================
La alarma de frescura de la serie GDELT, y las dos piezas que comparte con
la ingesta: la MARCA DE INICIO de la automatización y la detección de un
DÍA QUE GDELT NO PUBLICÓ.

Desde el 21-sep-2026 la serie vive en la rama `data` y la escribe CI solo
(`.github/workflows/gdelt.yml`). Este módulo es lo que dice, cada día, si la
automatización está haciendo su trabajo.

══ LA ALARMA NO MIRA LA HISTORIA HEREDADA ══

La serie histórica trae **24 huecos en BTC y 25 en XAU** sobre 4.904 días de
calendario (2013-04-01 a 2026-09-03). Son días de calendario sin fila, y la
aritmética cierra: 4.904 - 4.880 filas = 24; 4.904 - 4.879 = 25.

Una alarma que se disparara ante cualquier hueco interno nacería en rojo y
no serviría nunca: un rojo permanente entrena a ignorar el rojo. Por eso la
alarma solo evalúa desde la fecha de `metrics/ingesta_automatica.json` --
desde que CI es responsable. Los huecos anteriores son deuda heredada,
inventariada aparte (`inventariar_huecos`), no una falla de la automatización.

══ LA FECHA DE CORTE ES POR ACTIVO, AUNQUE LA MARCA SEA UNA SOLA ══

La marca guarda UNA fecha: el primer día que escribió CI. Para BTC y XAU,
sembrados hasta el 2026-09-03, es el 2026-09-04. Pero NVDA, NIFTY50 y EURUSD
no tienen historia: CI les empieza la serie más tarde, con los últimos
`DEFAULT_MAX_DAYS` días.

Si el rango se evaluara desde la marca para los cinco, los días entre el
2026-09-04 y la primera fila de NVDA contarían como huecos internos -- ausentes,
con datos después -- y **la alarma nacería en rojo el primer día**, que es
exactamente lo que este módulo existe para no hacer. Por eso el rango de cada
activo es `[max(marca, primera fila del activo), última fila]`: un hueco
anterior a que el activo exista no es un hueco de ese activo.

══ ROJO SOLO CUANDO NO SE VA A CURAR SOLO ══

  · ROJO (exit 1) -- un HUECO INTERNO dentro del rango: el día X está
    ausente o vacío y sin embargo un día posterior ya se bajó con datos. Si
    GDELT publicó el X+1, publicó el X; que falte es una falla real, y la
    corrida siguiente no lo arregla porque `last_day()` ya pasó de largo.
  · VERDE CON AVISO -- el día pendiente está en la PUNTA: es GDELT que
    todavía no publicó a las 06:30 UTC. La corrida siguiente lo reintenta.
    Sin esta distinción el job se pone rojo casi a diario y la alarma se
    vuelve ruido.
  · VERDE -- nada que decir.

Un día "vacío" NO es un día con `n_events == 0` de un activo. Es un día que
GDELT no publicó, y eso se detecta con el criterio de abajo. Si la alarma
usara el `n_events` de cada activo, EURUSD -- que acumula días insuficientes
por construcción de su filtro -- la tendría en rojo permanente.

══ CÓMO SE DISTINGUE UN 404 SIN TOCAR EL ESQUEMA DE LA SERIE ══

Un día que GDELT no publicó está vacío para TODOS los activos a la vez,
porque todos salen del mismo archivo diario. Los filtros CORE incluyen USA o
son vacíos (`CORE_COUNTRY_FILTERS["XAU"] = ()`), así que en un día publicado
ningún activo CORE puede quedar en cero eventos. El único que sí puede es un
activo de `FX_GOBIERNO_ONLY_ASSETS`, por su filtro.

Regla: **un día es de GDELT-no-publicado si todos los activos con fila ese
día tienen `n_events == 0` y al menos uno de ellos es CORE.** Un día vacío
solo para EURUSD no califica. Reusa las constantes de core.scoring; no agrega
ninguna.

Uso:
    python ingestion/frescura.py                 # exit 1 si hay un hueco interno
    python ingestion/frescura.py --assets BTC XAU
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.scoring import FX_GOBIERNO_ONLY_ASSETS  # noqa: E402
from governance.persistence import PersistenceStream, stream_path  # noqa: E402
from ingestion.gdelt_aggregation import DailyAggregationResult  # noqa: E402
from ingestion.gdelt_series import read_series  # noqa: E402

#: Dónde vive la marca de inicio, dentro del stream METRICS. En la rama
#: `data` queda en `metrics/ingesta_automatica.json`. Nace como
#: `{"desde": null}` y la ingesta la fija la primera vez que escribe: la
#: fecha no se conoce hasta que CI escribe su primer día, y un valor
#: adivinado al crear la rama sería exactamente la fecha equivocada.
MARCA_INICIO_ARCHIVO = "ingesta_automatica.json"


class Nivel:
    VERDE = "verde"
    AVISO = "verde_con_aviso"
    ROJO = "rojo"


# ══════════════════════════════════════════════════════════════════════════
#  Marca de inicio
# ══════════════════════════════════════════════════════════════════════════

def ruta_marca() -> Path:
    return Path(stream_path(PersistenceStream.METRICS)) / MARCA_INICIO_ARCHIVO


def leer_marca_de_inicio() -> Optional[date]:
    """La fecha desde la que CI es responsable, o None si todavía no
    escribió nada. Un archivo ausente y un `null` significan lo mismo."""
    ruta = ruta_marca()
    if not ruta.exists():
        return None
    valor = json.loads(ruta.read_text(encoding="utf-8")).get("desde")
    return date.fromisoformat(valor) if valor else None


def registrar_inicio_si_falta(dia: date) -> bool:
    """Fija la marca UNA vez. Si ya tiene fecha, no la toca: la marca dice
    desde cuándo CI es responsable, y moverla después escondería los huecos
    del tramo que quedaría afuera. Devuelve True si escribió."""
    if leer_marca_de_inicio() is not None:
        return False
    ruta = ruta_marca()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps({"desde": dia.isoformat()}, indent=2) + "\n",
                    encoding="utf-8")
    return True


# ══════════════════════════════════════════════════════════════════════════
#  Días que GDELT no publicó
# ══════════════════════════════════════════════════════════════════════════

def indexar(assets: Iterable[str]) -> dict[str, dict[date, DailyAggregationResult]]:
    """{activo: {día: fila}}, con la deduplicación de `read_series` (la
    última ocurrencia gana)."""
    return {a: {r.day: r for r in read_series(a)} for a in assets}


def dias_no_publicados(
    indice: Mapping[str, Mapping[date, DailyAggregationResult]],
    *,
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
) -> set[date]:
    """Los días que GDELT no publicó, por la regla del docstring del módulo:
    todos los activos con fila ese día en `n_events == 0`, y al menos uno
    CORE."""
    por_dia: dict[date, list[tuple[str, DailyAggregationResult]]] = {}
    for asset, filas in indice.items():
        for dia, fila in filas.items():
            por_dia.setdefault(dia, []).append((asset, fila))

    out = set()
    for dia, filas in por_dia.items():
        if desde is not None and dia < desde:
            continue
        if hasta is not None and dia > hasta:
            continue
        todos_en_cero = all(f.n_events == 0 for _, f in filas)
        hay_un_core = any(a not in FX_GOBIERNO_ONLY_ASSETS for a, _ in filas)
        if todos_en_cero and hay_un_core:
            out.add(dia)
    return out


# ══════════════════════════════════════════════════════════════════════════
#  Alarma
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class EstadoFrescura:
    asset: str
    nivel: str
    ultimo_dia: Optional[date]
    dias_de_retraso: Optional[int]
    huecos_internos: list[date] = field(default_factory=list)
    pendientes_punta: list[date] = field(default_factory=list)
    motivo: str = ""

    def linea(self) -> str:
        """Una línea que se entiende sola, sin el resto del reporte."""
        ultimo = self.ultimo_dia.isoformat() if self.ultimo_dia else "—"
        retraso = "—" if self.dias_de_retraso is None else str(self.dias_de_retraso)
        base = (f"{self.asset:8s} {self.nivel.upper():16s} último {ultimo}  "
                f"retraso {retraso}  huecos internos {len(self.huecos_internos)}  "
                f"pendientes en punta {len(self.pendientes_punta)}")
        return f"{base}  -- {self.motivo}" if self.motivo else base


def evaluar_activo(
    asset: str,
    indice: Mapping[str, Mapping[date, DailyAggregationResult]],
    no_publicados: set[date],
    *,
    desde: Optional[date],
    ultimo_publicado: date,
) -> EstadoFrescura:
    if desde is None:
        return EstadoFrescura(asset, Nivel.AVISO, None, None,
                              motivo="la automatización todavía no escribió "
                                     "ningún día: no hay tramo del que CI sea "
                                     "responsable.")
    filas = indice.get(asset, {})
    if not filas:
        return EstadoFrescura(asset, Nivel.AVISO, None, None,
                              motivo="sin serie para este activo.")

    ultimo = max(filas)
    retraso = max(0, (ultimo_publicado - ultimo).days)
    inicio = max(desde, min(filas))

    if ultimo < inicio:
        return EstadoFrescura(asset, Nivel.VERDE, ultimo, retraso,
                              motivo="sin días desde la fecha de corte.")

    def con_datos(d: date) -> bool:
        return d in filas and d not in no_publicados

    rango = [inicio + timedelta(days=k) for k in range((ultimo - inicio).days + 1)]
    faltan = [d for d in rango if not con_datos(d)]
    dias_con_datos = [d for d in rango if con_datos(d)]
    ultimo_con_datos = max(dias_con_datos) if dias_con_datos else None

    internos = [d for d in faltan if ultimo_con_datos and d < ultimo_con_datos]
    punta = [d for d in faltan if not ultimo_con_datos or d > ultimo_con_datos]

    if internos:
        return EstadoFrescura(
            asset, Nivel.ROJO, ultimo, retraso, internos, punta,
            motivo=(f"día(s) vacío(s) o ausente(s) con datos posteriores ya "
                    f"bajados: {', '.join(d.isoformat() for d in internos[:5])}"
                    f"{' …' if len(internos) > 5 else ''}. No se cura solo: "
                    f"last_day() ya pasó de largo."))
    if punta or retraso:
        partes = []
        if punta:
            partes.append(f"{len(punta)} día(s) en la punta que GDELT todavía no "
                          f"publicó; la corrida siguiente los reintenta")
        if retraso:
            partes.append(f"la serie va {retraso} día(s) detrás del último "
                          f"publicado")
        return EstadoFrescura(asset, Nivel.AVISO, ultimo, retraso, [], punta,
                              motivo="; ".join(partes) + ".")
    return EstadoFrescura(asset, Nivel.VERDE, ultimo, retraso)


def evaluar(assets: Sequence[str], *, ultimo_publicado: date) -> list[EstadoFrescura]:
    desde = leer_marca_de_inicio()
    indice = indexar(assets)
    no_publicados = dias_no_publicados(indice, desde=desde)
    return [evaluar_activo(a, indice, no_publicados, desde=desde,
                           ultimo_publicado=ultimo_publicado)
            for a in assets]


# ══════════════════════════════════════════════════════════════════════════
#  Inventario de la deuda heredada
# ══════════════════════════════════════════════════════════════════════════

def inventariar_huecos(asset: str, *, hasta: Optional[date] = None) -> list[date]:
    """Días de calendario sin fila entre la primera y la última del activo
    (o hasta `hasta`). Es la definición que hace cerrar las cifras de la
    serie histórica: 4.904 días de calendario - 4.880 filas = 24 en BTC."""
    dias = sorted(r.day for r in read_series(asset))
    if hasta is not None:
        dias = [d for d in dias if d <= hasta]
    if not dias:
        return []
    presentes = set(dias)
    total = (dias[-1] - dias[0]).days + 1
    return [dias[0] + timedelta(days=k) for k in range(total)
            if dias[0] + timedelta(days=k) not in presentes]


# ══════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════

def main(argv: Optional[Sequence[str]] = None, *, hoy: Optional[date] = None) -> int:
    # Import diferido: run_gdelt importa este módulo, y el cálculo del último
    # día publicado vive allá. Importarlo arriba sería un ciclo.
    from ingestion.run_gdelt import ultimo_dia_disponible
    from orchestration.cycle import DEFAULT_CYCLE_ASSETS

    p = argparse.ArgumentParser(
        prog="frescura",
        description="Alarma de frescura de la serie GDELT. Exit 1 solo ante un "
                    "hueco interno posterior a la fecha de corte.")
    p.add_argument("--assets", nargs="+", default=list(DEFAULT_CYCLE_ASSETS))
    args = p.parse_args(argv)

    tope = ultimo_dia_disponible(hoy or date.today())
    estados = evaluar(args.assets, ultimo_publicado=tope)
    desde = leer_marca_de_inicio()

    print("═══ FRESCURA DE LA SERIE GDELT ═══")
    print(f"fecha de corte (CI responsable desde): {desde or '— todavía no escribió'}   "
          f"último día publicado por GDELT: {tope}")
    for e in estados:
        print("  " + e.linea())

    rojos = [e.asset for e in estados if e.nivel == Nivel.ROJO]
    if rojos:
        print(f"ROJO: hueco interno en {', '.join(rojos)}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
