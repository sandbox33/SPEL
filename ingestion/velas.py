"""
ingestion/velas.py
====================
Velas diarias de Deriv, persistidas en la rama `data` (Brief H1-A,
Entregable 2). El primer eslabón del camino precio -> señal -> orden ->
reconciliación: hasta hoy el repo no guardaba ni un precio.

    metrics/velas/<símbolo>/86400.jsonl          una vela por línea
    metrics/velas/<símbolo>/revisiones_86400.jsonl   velas que volvieron distintas

══ REGLAS DE ESCRITURA ══

APPEND PURO, con la misma convención que la serie GDELT
(ingestion/gdelt_series.py): nunca se reescribe el archivo, se agrega al
final, y al leer se deduplica por época quedándose con la última. Con la
misma guarda de salto final que `append_day()`.

SOLO VELAS CERRADAS: se guarda si `epoch + granularidad <= hora del
servidor`, consultada con `time`. NUNCA con el reloj del runner:
`drop_unclosed_candles()` de ingestion/adapters.py usa el reloj local, y un
runner adelantado un minuto guardaría la vela del día en curso como si
estuviera cerrada.

VALIDACIÓN, vela por vela, antes de escribir:
  · tipos: época entera, precios numéricos finitos y positivos;
  · alineación: `epoch % granularidad == 0`;
  · coherencia OHLC: low <= min(open, close), high >= max(open, close);
  · épocas ESTRICTAMENTE crecientes respecto de lo ya guardado.
Una vela que no pasa no se escribe, se cuenta y se reporta, y la corrida
sale 1: la API devolvió algo incoherente y eso tiene que verse.

REVISIONES: si una vela ya guardada vuelve de la API con otros valores, NO
se sobrescribe. Se registra en `revisiones_86400.jsonl` (lo guardado y lo
nuevo) y se cuenta. Una revisión ya registrada no se vuelve a registrar:
si no, la misma diferencia agregaría una línea por día y la segunda corrida
seguida no sería idempotente.

══ BACKFILL ══

Pagina hacia atrás con `end` y `count = DERIV_MAX_COUNT` (5000, medido en
el sondeo de tools/provider_coverage.py) hasta que la API no devuelva más, o hasta
tocar la última vela guardada. Es el bucle de `probe_deriv`, portado: las
mismas tres condiciones de corte y el mismo tope de páginas.

══ PROFUNDIDAD USABLE, QUE NO ES LA DEVUELTA ══

Un hueco es un día en que el mercado estaba abierto y no hay vela. Qué días
estaba abierto depende del mercado, y se decide con el mercado que devuelve
`active_symbols`:
  · CONTINUO (cripto): se espera una vela por día de calendario.
  · HÁBIL (el resto, oro incluido): se espera una por día de lunes a
    viernes. Un fin de semana no es un hueco; un feriado SÍ lo es, porque
    este módulo no conoce el calendario de feriados de cada mercado, y
    contarlos como hueco es el error conservador.

El reporte da: barras totales, huecos (con fechas), primera y última
fecha, el tramo sin huecos más largo, y la HISTORIA USABLE: las barras del
tramo sin huecos que termina en la última vela. Es lo que un backtest que
corre hasta hoy puede usar sin cruzar un hueco, y es el número que entra en
la condición de parada del pre-registro.

══ LECTURA ══

`leer_velas()` devuelve un `pl.DataFrame` con esquema estricto. Estricto de
verdad: `pl.DataFrame(..., strict=True)` NO alcanza -- medido el
25-sep-2026 con polars 1.44.2, convierte en silencio el string "2.0" a 2.0,
`True` a 1, ignora claves de más y pone null en las que faltan. Por eso los
tipos se validan fila por fila ANTES de construir el DataFrame.

Uso:
    python ingestion/velas.py --entorno demo            # reporta, no escribe
    python ingestion/velas.py --entorno demo --write
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.preregistro_h1 import rejilla_soportada  # noqa: E402
from governance.persistence import PersistenceStream, stream_path  # noqa: E402
from governance.secrets import SecretKey, load_secret  # noqa: E402
from ingestion.adapters import (  # noqa: E402
    DERIV_MAX_COUNT,
    DERIV_MAX_PAGES_DEFAULT,
    _DERIV_GRANULARITY_SECONDS,
)
from ingestion.deriv_ws import Entorno, SesionDeriv, abrir_sesion  # noqa: E402
from ingestion.sonda_instrumentos import (  # noqa: E402
    CONTROL_POSITIVO,
    MERCADO_CRIPTO,
    codigo,
    seleccionar,
)

#: La granularidad diaria, del mapa verificado del adapter.
GRANULARIDAD_DIARIA: int = _DERIV_GRANULARITY_SECONDS["1d"]

#: Esquema de `leer_velas()`. El orden de las columnas es parte del contrato.
ESQUEMA_VELAS: dict[str, Any] = {
    "epoch": pl.Int64, "open": pl.Float64, "high": pl.Float64,
    "low": pl.Float64, "close": pl.Float64,
}

_PRECIOS = ("open", "high", "low", "close")


class VelaInvalidaError(ValueError):
    pass


class TipoInvalidoError(TypeError):
    pass


class Calendario:
    CONTINUO = "continuo"
    HABIL = "habil"


def calendario_de(item_active_symbols: dict) -> str:
    return (Calendario.CONTINUO if item_active_symbols.get("market") == MERCADO_CRIPTO
            else Calendario.HABIL)


# ══════════════════════════════════════════════════════════════════════════
#  Rutas y archivo
# ══════════════════════════════════════════════════════════════════════════

def ruta_serie(simbolo: str, granularidad: int = GRANULARIDAD_DIARIA) -> Path:
    return (Path(stream_path(PersistenceStream.METRICS)) / "velas" / simbolo
            / f"{granularidad}.jsonl")


def ruta_revisiones(simbolo: str, granularidad: int = GRANULARIDAD_DIARIA) -> Path:
    return ruta_serie(simbolo, granularidad).with_name(f"revisiones_{granularidad}.jsonl")


def _append(ruta: Path, filas: Sequence[dict]) -> None:
    """Append puro, con la guarda de salto final de gdelt_series.append_day."""
    if not filas:
        return
    ruta.parent.mkdir(parents=True, exist_ok=True)
    falta_salto = False
    if ruta.exists() and ruta.stat().st_size > 0:
        with ruta.open("rb") as f:
            f.seek(-1, 2)
            falta_salto = f.read(1) != b"\n"
    with ruta.open("a", encoding="utf-8") as f:
        if falta_salto:
            f.write("\n")
        for fila in filas:
            f.write(json.dumps(fila, ensure_ascii=False) + "\n")


def _leer_filas(ruta: Path) -> list[dict]:
    if not ruta.exists():
        return []
    with ruta.open("r", encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


# ══════════════════════════════════════════════════════════════════════════
#  Validación
# ══════════════════════════════════════════════════════════════════════════

def _es_numero(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def normalizar_vela(c: dict, granularidad: int) -> dict:
    """De una vela de la API a la fila que se guarda. Valida TODO; la única
    conversión es entero -> float en los precios, y ocurre acá, en el borde,
    a la vista: una API que manda `65000` en vez de `65000.0` no es un
    error, y guardar el entero haría fallar a `leer_velas()`."""
    epoch = c.get("epoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool):
        raise VelaInvalidaError(f"epoch no entero: {epoch!r}")
    if epoch % granularidad != 0:
        raise VelaInvalidaError(f"epoch {epoch} no alineado a {granularidad}")
    fila = {"epoch": epoch}
    for k in _PRECIOS:
        v = c.get(k)
        if not _es_numero(v) or not math.isfinite(v) or v <= 0:
            raise VelaInvalidaError(f"{k} inválido en epoch {epoch}: {v!r}")
        fila[k] = float(v)
    o, h, l, cl = (fila[k] for k in _PRECIOS)
    if not (l <= min(o, cl) and h >= max(o, cl) and l <= h):
        raise VelaInvalidaError(f"OHLC incoherente en epoch {epoch}: "
                                f"o={o} h={h} l={l} c={cl}")
    return fila


# ══════════════════════════════════════════════════════════════════════════
#  Lectura
# ══════════════════════════════════════════════════════════════════════════

def _validar_tipos(fila: dict, lineno: int) -> None:
    if list(fila) != list(ESQUEMA_VELAS):
        raise TipoInvalidoError(f"línea {lineno}: columnas {list(fila)}, se "
                                f"esperaban {list(ESQUEMA_VELAS)}")
    if type(fila["epoch"]) is not int:
        raise TipoInvalidoError(f"línea {lineno}: epoch es {type(fila['epoch']).__name__}")
    for k in _PRECIOS:
        if type(fila[k]) is not float:
            raise TipoInvalidoError(f"línea {lineno}: {k} es {type(fila[k]).__name__}")


def leer_velas(simbolo: str, granularidad: int = GRANULARIDAD_DIARIA) -> pl.DataFrame:
    """La serie de un símbolo, ordenada, deduplicada por época (la última
    ocurrencia gana, como en gdelt_series.read_series). Falla si una fila
    no tiene exactamente el esquema: no convierte nada."""
    ruta = ruta_serie(simbolo, granularidad)
    por_epoch: dict[int, dict] = {}
    if ruta.exists():
        with ruta.open("r", encoding="utf-8") as f:
            for lineno, linea in enumerate(f, start=1):
                if not linea.strip():
                    continue
                fila = json.loads(linea)
                _validar_tipos(fila, lineno)
                por_epoch[fila["epoch"]] = fila
    filas = [por_epoch[e] for e in sorted(por_epoch)]
    return pl.DataFrame(filas, schema=ESQUEMA_VELAS, strict=True, orient="row"
                        ) if filas else pl.DataFrame(schema=ESQUEMA_VELAS)


# ══════════════════════════════════════════════════════════════════════════
#  Profundidad usable
# ══════════════════════════════════════════════════════════════════════════

def _dia(epoch: int) -> date:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).date()


def _esperado(d: date, calendario: str) -> bool:
    return calendario == Calendario.CONTINUO or d.weekday() < 5


@dataclass
class Profundidad:
    barras_totales: int
    primera: Optional[str]
    ultima: Optional[str]
    huecos: list[str]
    tramo_sin_huecos_mas_largo: int
    historia_usable: int
    rejilla_soportada: list[int]


def profundidad(epochs: Sequence[int], calendario: str) -> Profundidad:
    dias = sorted({_dia(e) for e in epochs})
    if not dias:
        return Profundidad(0, None, None, [], 0, 0, [])
    presentes = set(dias)
    huecos: list[date] = []
    tramos: list[int] = []
    actual = 0
    d = dias[0]
    while d <= dias[-1]:
        if d in presentes:
            actual += 1
        elif _esperado(d, calendario):
            huecos.append(d)
            tramos.append(actual)
            actual = 0
        d += timedelta(days=1)
    tramos.append(actual)
    return Profundidad(len(dias), str(dias[0]), str(dias[-1]),
                       [str(h) for h in huecos], max(tramos), tramos[-1],
                       rejilla_soportada(tramos[-1]))


# ══════════════════════════════════════════════════════════════════════════
#  Descarga e ingesta
# ══════════════════════════════════════════════════════════════════════════

async def descargar(
    s: SesionDeriv, simbolo: str, granularidad: int, *,
    hasta_epoch: Optional[int] = None, max_paginas: int = DERIV_MAX_PAGES_DEFAULT,
) -> tuple[list[dict], str]:
    """Pagina hacia atrás desde `latest`. Corta cuando la API no devuelve
    más, cuando deja de retroceder, cuando una página trae menos de
    DERIV_MAX_COUNT, o cuando toca `hasta_epoch` (la última vela guardada).
    Devuelve (velas crudas, motivo del corte)."""
    velas: dict[int, dict] = {}
    fin: Any = "latest"
    for _ in range(max_paginas):
        r = await s.pedir({"ticks_history": simbolo, "adjust_start_time": 1,
                           "end": fin, "count": DERIV_MAX_COUNT,
                           "style": "candles", "granularity": granularidad})
        pagina = r.datos.get("candles") or []
        if not pagina:
            return list(velas.values()), "la API no devolvió más velas"
        mas_viejo = min(c.get("epoch", 0) for c in pagina)
        antes = len(velas)
        for c in pagina:
            velas[c.get("epoch")] = c
        if hasta_epoch is not None and mas_viejo <= hasta_epoch:
            return list(velas.values()), "se tocó la última vela guardada"
        if len(velas) == antes:
            return list(velas.values()), "la API dejó de retroceder: se llegó al fondo"
        if len(pagina) < DERIV_MAX_COUNT:
            return list(velas.values()), (f"la última página trajo {len(pagina)} < "
                                          f"{DERIV_MAX_COUNT}: se llegó al fondo")
        fin = mas_viejo - 1
    return list(velas.values()), f"TOPE DE PÁGINAS ({max_paginas}): la historia puede seguir"


@dataclass
class ReporteSimbolo:
    simbolo: str
    calendario: str
    corte: str = ""
    recibidas: int = 0
    abiertas_descartadas: int = 0
    invalidas: list[str] = field(default_factory=list)
    nuevas: int = 0
    revisiones_nuevas: int = 0
    revisiones_total: int = 0
    anteriores_sin_escribir: int = 0
    profundidad: Optional[Profundidad] = None


def _ingerir_simbolo(
    rep: ReporteSimbolo, crudas: list[dict], *, ahora_servidor: int,
    granularidad: int, write: bool, fecha_servidor: str,
) -> None:
    guardadas = {f["epoch"]: f for f in _leer_filas(ruta_serie(rep.simbolo, granularidad))}
    ultima = max(guardadas) if guardadas else None
    registradas = {(r["epoch"], json.dumps(r["nueva"], sort_keys=True))
                   for r in _leer_filas(ruta_revisiones(rep.simbolo, granularidad))}

    nuevas, revisiones = [], []
    rep.recibidas = len(crudas)
    for c in sorted(crudas, key=lambda c: c.get("epoch") if isinstance(c.get("epoch"), int) else -1):
        try:
            fila = normalizar_vela(c, granularidad)
        except VelaInvalidaError as exc:
            rep.invalidas.append(str(exc))
            continue
        if fila["epoch"] + granularidad > ahora_servidor:
            rep.abiertas_descartadas += 1
            continue
        previa = guardadas.get(fila["epoch"])
        if previa is not None:
            if previa != fila:
                clave = (fila["epoch"], json.dumps(fila, sort_keys=True))
                if clave not in registradas:
                    revisiones.append({"epoch": fila["epoch"], "guardada": previa,
                                       "nueva": fila, "detectada": fecha_servidor})
                    registradas.add(clave)
            continue
        if ultima is not None and fila["epoch"] < ultima:
            # Una vela que falta en medio de lo guardado: append puro no
            # puede insertarla sin romper el orden. Se cuenta.
            rep.anteriores_sin_escribir += 1
            continue
        nuevas.append(fila)

    rep.nuevas = len(nuevas)
    rep.revisiones_nuevas = len(revisiones)
    rep.revisiones_total = len(registradas)
    if write:
        _append(ruta_serie(rep.simbolo, granularidad), nuevas)
        _append(ruta_revisiones(rep.simbolo, granularidad), revisiones)
    epochs = list(guardadas) + [f["epoch"] for f in nuevas]
    rep.profundidad = profundidad(epochs, rep.calendario)


@dataclass
class Resultado:
    fecha_servidor: Optional[str] = None
    invalido: Optional[str] = None
    simbolos: list[ReporteSimbolo] = field(default_factory=list)


async def ingerir(
    entorno: Entorno,
    *,
    app_id: str,
    token: Optional[str] = None,
    connector: Any = None,
    write: bool = False,
    granularidad: int = GRANULARIDAD_DIARIA,
) -> Resultado:
    res = Resultado()
    async with abrir_sesion(entorno, app_id=app_id, token=token,
                            connector=connector) as s:
        ahora = (await s.pedir({"time": 1})).datos["time"]
        res.fecha_servidor = _dia(ahora).isoformat()
        sel = seleccionar((await s.pedir({"active_symbols": "brief"})).datos.get("active_symbols") or [])
        if not sel.control_presente:
            res.invalido = f"{CONTROL_POSITIVO} no aparece en active_symbols"
            return res
        for item in sel.btc + sel.oro:
            cod, _ = codigo(item)
            rep = ReporteSimbolo(cod, calendario_de(item))
            guardadas = [f["epoch"] for f in _leer_filas(ruta_serie(cod, granularidad))]
            crudas, rep.corte = await descargar(
                s, cod, granularidad, hasta_epoch=max(guardadas) if guardadas else None)
            _ingerir_simbolo(rep, crudas, ahora_servidor=ahora, granularidad=granularidad,
                             write=write, fecha_servidor=res.fecha_servidor)
            res.simbolos.append(rep)
    return res


# ══════════════════════════════════════════════════════════════════════════
#  Reporte y CLI
# ══════════════════════════════════════════════════════════════════════════

def render_text(res: Resultado, *, write: bool) -> str:
    out = ["═══ VELAS DIARIAS DERIV ═══",
           f"fecha del servidor: {res.fecha_servidor or '—'}   "
           f"{'ESCRIBE' if write else 'DRY-RUN: no escribe'}"]
    if res.invalido:
        out.append(f"INVALIDO: {res.invalido}. No se escribió nada.")
    for r in res.simbolos:
        p = r.profundidad
        out.append(f"── {r.simbolo} (calendario {r.calendario})")
        out.append(f"  recibidas {r.recibidas}, nuevas {r.nuevas}, abiertas descartadas "
                   f"{r.abiertas_descartadas}, inválidas {len(r.invalidas)}   corte: {r.corte}")
        if r.revisiones_nuevas or r.revisiones_total:
            out.append(f"  REVISIONES: {r.revisiones_nuevas} nuevas, {r.revisiones_total} en "
                       f"total. No se sobrescribió nada: ver {ruta_revisiones(r.simbolo).name}.")
        if r.anteriores_sin_escribir:
            out.append(f"  {r.anteriores_sin_escribir} vela(s) anteriores a la última guardada "
                       f"no se escribieron (append puro).")
        for i in r.invalidas[:5]:
            out.append(f"  ✗ {i}")
        if p:
            out.append(f"  profundidad: {p.barras_totales} barras, {p.primera} .. {p.ultima}, "
                       f"{len(p.huecos)} hueco(s)")
            out.append(f"  tramo sin huecos más largo: {p.tramo_sin_huecos_mas_largo}   "
                       f"HISTORIA USABLE (tramo final sin huecos): {p.historia_usable}")
            out.append(f"  rejilla que soporta: {p.rejilla_soportada or 'NINGUNA — no alcanza ni la mínima'}")
            if p.huecos:
                out.append(f"  huecos: {', '.join(p.huecos[:10])}"
                           f"{' …' if len(p.huecos) > 10 else ''}")
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="velas", description="Velas diarias de Deriv (H1-A).")
    p.add_argument("--entorno", required=True, choices=("demo", "real"),
                   help="Obligatorio, sin default.")
    p.add_argument("--write", action="store_true", help="Escribe en metrics/velas/.")
    return p


def main(argv: Optional[Sequence[str]] = None, *, connector: Any = None) -> int:
    args = build_parser().parse_args(argv)
    app_id = load_secret(SecretKey.DERIV_APP_ID, required=False)
    if not app_id:
        print(f"ERROR: falta {SecretKey.DERIV_APP_ID}.", file=sys.stderr)
        return 2
    res = asyncio.run(ingerir(args.entorno, app_id=app_id,
                              token=load_secret(SecretKey.DERIV_API_TOKEN, required=False),
                              connector=connector, write=args.write))
    print(render_text(res, write=args.write))
    if res.invalido or any(r.invalidas for r in res.simbolos):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
