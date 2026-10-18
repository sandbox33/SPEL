"""
tools/concordancia_d1.py
==========================
¿Las velas diarias de TwelveData (D1) son un proxy fiel de las de Deriv?
Brief del Admin 14, 14b, puntos 2 y 3. Patrón: tools/concordancia_xauusd.py.

Todo lo de este módulo quedó fijado antes de bajar un precio diario, y no se
ajusta después de ver un resultado (brief 14b).

══ LO QUE ESTE MÓDULO NO HACE ══

No calcula ningún desenlace de trading: ni P&L, ni posiciones, ni nada que
acumule el movimiento de un día con el del siguiente. Compara cierres entre
fuentes y, día por día, el logaritmo del cociente de dos cierres seguidos de
una misma fuente. Los precios solo se leen en `cierres` y
`cierres_revisados`, y solo el cierre: tests/test_guarda_concordancia_d1.py
lo verifica por AST con la guarda de concordancia_xauusd.py, portada.

══ PASO 1: LA HORA DE CORTE DE TWELVEDATA (no es la compuerta) ══

Para cada hora h de 00:00 a 23:00 UTC, el cierre diario con corte h de la
fecha D es el cierre de la última vela intradía que termina a más tardar en
el instante de corte T(D, h) y empieza a lo sumo 24 h antes. Coincide con
la barra 1day de TwelveData de fecha D si difieren en a lo sumo
TOLERANCIA_CIERRE_USD (±0,01 USD). Intradía, en UTC: el M5 de TwelveData del
oro, que ya está en data, y la muestra 1h de BTC (ingestion/velas_d1.py).

La herramienta SE DETIENE, sin calcular la compuerta de ningún activo, si en
algún activo la h ganadora no llega a UMBRAL_CORTE (95 % de los días) o no es
HORA_CORTE_EXIGIDA (00:00 UTC): comparar cierres de cortes distintos mediría
el desfase, no la fuente.

══ PASO 2: LA COMPUERTA (solo si los dos activos cortan a las 00:00 UTC) ══

  · Días comunes: los que tienen barra cerrada en las dos fuentes.
  · r: el logaritmo del cociente de cierres de barras consecutivas de la misma
    fuente. Un hueco en cualquiera de las dos saca ese par.
  · Un día coincide si (i) r_TD y r_Deriv tienen el mismo signo, o los dos
    valen menos de UMBRAL_PLANO (1 pb) en valor absoluto, y (ii)
    |r_TD − r_Deriv| ≤ FACTOR_MEDIANA (0,2) × mediana(|r_Deriv|).
  · VERDE si coinciden ≥ UMBRAL_COMPUERTA (90 %) y hay al menos
    MIN_DIAS_COMUNES (250) días comunes. Si no, ROJO. Por activo,
    independiente.
  · Informativo, sin efecto en la compuerta: la correlación de r, la
    mediana y el p90 de |r_TD − r_Deriv|, los días de signo opuesto y el
    tamaño de las revisiones de Deriv en la ventana.

[INTERPRETACIÓN] Lo que el brief no fija:
  · T(D, h) es el final de la ventana de 24 h que más se superpone con la
    fecha D: desde HORA_MEDIA (12:00), T = D h:00; antes, T = D+1 h:00. Con
    h = 0, T = D+1 00:00: la barra D de TwelveData cubre el mismo día UTC que
    la de Deriv, que empieza en D 00:00 (sonda §0.A-2).
  · Qué ventana cubre la barra 1day de TwelveData no está medido (decision-log
    2026-10-17, D1): también se informa, para cada h, la fracción con la
    fecha del otro lado (T ∓ 24 h). Si TwelveData fechara la barra por el día
    en que cierra, se vería ahí. No decide nada.
  · Días del paso 1: las barras 1day de TwelveData cuyo día anterior y
    posterior caen enteros dentro del tramo de la serie intradía; los mismos
    para las 24 horas. Un día sin vela intradía en la ventana no coincide.
  · La h ganadora es la de más días coincidentes. Un empate en el máximo no
    tiene ganadora y detiene.
  · Cada fuente se filtra al calendario del activo antes de calcular r
    (cripto todos los días; el oro de lunes a viernes, como ingestion/velas.py):
    filtrar filas y después recalcular, nunca al revés. Un hueco es el día
    esperado anterior que falta en esa fuente.
  · El 90 % se cuenta sobre los pares: los días comunes con r en las dos
    fuentes. Un par que sale por un hueco no cuenta ni a favor ni en contra.
    El mínimo de 250 es sobre los días comunes, como los define el brief. Se
    informan los dos conteos y también la fracción sobre los días comunes.
  · mediana(|r_Deriv|) es la de los pares.
  · "Mismo signo" es signo igual: un 0 solo es del signo de otro 0. "Menos de
    1 pb" es estricto.
  · Los cierres son los guardados: la serie es append-only y una revisión
    queda aparte, así que se compara lo que se bajó primero. Las revisiones
    solo se informan.

Uso:
    python tools/concordancia_d1.py      # lee metrics/ y publica el informe
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Optional, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.velas import (  # noqa: E402
    GRANULARIDAD_DIARIA,
    _dia,
    _esperado,
    _leer_filas,
    leer_velas,
    ruta_revisiones,
    ruta_serie,
)
from ingestion.velas_d1 import ACTIVOS_D1  # noqa: E402
from ingestion.velas_intradia import publicar, sha256_de_archivo  # noqa: E402

#: Fijados antes de bajar un precio diario (brief del Admin 14, 14b).
TOLERANCIA_CIERRE_USD = 0.01
UMBRAL_CORTE = 0.95
HORA_CORTE_EXIGIDA = 0
HORAS_DE_CORTE: tuple[int, ...] = tuple(range(24))
UMBRAL_PLANO = 1e-4
FACTOR_MEDIANA = 0.2
UMBRAL_COMPUERTA = 0.90
MIN_DIAS_COMUNES = 250

#: [INTERPRETACIÓN] Desde esta hora, el corte de la fecha D cae en D; antes, en D+1.
HORA_MEDIA = 12
#: Informativo: la mediana y el p90.
PERCENTILES: tuple[int, ...] = (50, 90)


# ═══ Lectores: los ÚNICOS que tocan precios ═══════════════════════════════

def cierres(serie: str, granularidad: int) -> dict[int, float]:
    """El cierre de cada vela guardada, por época. Solo el cierre."""
    df = leer_velas(serie, granularidad)
    return dict(zip(df["epoch"].to_list(), df["close"].to_list()))


def cierres_revisados(serie: str, granularidad: int) -> list[tuple[int, float, float]]:
    """(época, cierre guardado, cierre nuevo) de cada revisión registrada."""
    return [(int(r["epoch"]), float(r["guardada"]["close"]), float(r["nueva"]["close"]))
            for r in _leer_filas(ruta_revisiones(serie, granularidad))]


# ═══ Paso 1: la hora de corte ═════════════════════════════════════════════

def _epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def instante_de_corte(d: date, h: int) -> int:
    """El final de la ventana de 24 h con corte a la hora h que más se
    superpone con la fecha d."""
    return _epoch(d) + h * 3600 + (0 if h >= HORA_MEDIA else GRANULARIDAD_DIARIA)


def cierre_al_corte(intradia: dict[int, float], granularidad: int, corte: int) -> Optional[float]:
    """El cierre de la última vela que termina a más tardar en `corte` y
    empieza a lo sumo 24 h antes; None si no hay ninguna."""
    e = corte - granularidad
    while e >= corte - GRANULARIDAD_DIARIA:
        if e in intradia:
            return intradia[e]
        e -= granularidad
    return None


def dias_comparables(diarias: dict[date, float], intradia: dict[int, float],
                     granularidad: int) -> list[date]:
    """Las fechas cuyo día anterior y posterior caen enteros en el tramo de
    la serie intradía."""
    if not intradia:
        return []
    desde, hasta = min(intradia), max(intradia) + granularidad
    return [d for d in sorted(diarias)
            if desde <= _epoch(d) - GRANULARIDAD_DIARIA and _epoch(d) + 2 * GRANULARIDAD_DIARIA <= hasta]


def _coincide_el_cierre(intradia: dict[int, float], granularidad: int, corte: int,
                        cierre_td: float) -> Optional[bool]:
    c = cierre_al_corte(intradia, granularidad, corte)
    return None if c is None else abs(c - cierre_td) <= TOLERANCIA_CIERRE_USD


def decidir_corte(por_hora: list[dict]) -> dict:
    """La h con más días coincidentes, y si el paso 1 sigue o se detiene."""
    if not por_hora or por_hora[0]["dias"] == 0:
        return {"ganadora": None, "fraccion_ganadora": None, "decision": "detenido",
                "motivos": ["no hay días comparables"]}
    mejor = max(f["coinciden"] for f in por_hora)
    ganadoras = [f["hora_utc"] for f in por_hora if f["coinciden"] == mejor]
    fraccion = mejor / por_hora[0]["dias"]
    motivos = []
    if len(ganadoras) > 1:
        motivos.append(f"empate en el máximo entre las horas {ganadoras}: no hay ganadora")
    if fraccion < UMBRAL_CORTE:
        motivos.append(f"la h ganadora coincide en {fraccion:.2%} de los días, menos que "
                       f"{UMBRAL_CORTE:.0%}")
    h = ganadoras[0] if len(ganadoras) == 1 else None
    if h is not None and h != HORA_CORTE_EXIGIDA:
        motivos.append(f"la h ganadora es {h:02d}:00 UTC, no {HORA_CORTE_EXIGIDA:02d}:00")
    return {"ganadora": h, "fraccion_ganadora": fraccion,
            "decision": "detenido" if motivos else "sigue", "motivos": motivos}


def paso_1(diarias_td: dict[int, float], intradia: dict[int, float], granularidad: int) -> dict:
    """La fracción de días en que cada corte h reproduce el cierre de la
    barra 1day de TwelveData, y la decisión."""
    diarias = {_dia(e): c for e, c in diarias_td.items()}
    dias = dias_comparables(diarias, intradia, granularidad)
    por_hora = []
    for h in HORAS_DE_CORTE:
        propia = [_coincide_el_cierre(intradia, granularidad, instante_de_corte(d, h), diarias[d])
                  for d in dias]
        otra = [_coincide_el_cierre(intradia, granularidad, instante_de_corte(d, h)
                                    + (GRANULARIDAD_DIARIA if h >= HORA_MEDIA else -GRANULARIDAD_DIARIA),
                                    diarias[d]) for d in dias]
        n = len(dias)
        por_hora.append({"hora_utc": h, "dias": n, "coinciden": propia.count(True),
                         "sin_cierre": propia.count(None),
                         "fraccion": propia.count(True) / n if n else None,
                         "fraccion_con_la_otra_fecha": otra.count(True) / n if n else None})
    return {"dias_comparables": len(dias),
            "primer_dia": dias[0].isoformat() if dias else None,
            "ultimo_dia": dias[-1].isoformat() if dias else None,
            "tolerancia_usd": TOLERANCIA_CIERRE_USD, "umbral_pct": 100 * UMBRAL_CORTE,
            **decidir_corte(por_hora), "por_hora": por_hora}


# ═══ Paso 2: la compuerta ═════════════════════════════════════════════════

def del_calendario(diarias: dict[int, float], calendario: str) -> dict[date, float]:
    """Los cierres de los días que el calendario del activo espera."""
    return {_dia(e): c for e, c in sorted(diarias.items()) if _esperado(_dia(e), calendario)}


def dia_anterior(d: date, calendario: str) -> date:
    p = d - timedelta(days=1)
    while not _esperado(p, calendario):
        p -= timedelta(days=1)
    return p


def r_por_dia(cierres_del_dia: dict[date, float], calendario: str) -> dict[date, float]:
    """r de cada día cuyo día esperado anterior está en la misma fuente."""
    out = {}
    for d, c in cierres_del_dia.items():
        p = dia_anterior(d, calendario)
        if p in cierres_del_dia:
            out[d] = math.log(c / cierres_del_dia[p])
    return out


def coincide(r_td: float, r_deriv: float, tolerancia: float) -> bool:
    mismo_signo = np.sign(r_td) == np.sign(r_deriv)
    planos = abs(r_td) < UMBRAL_PLANO and abs(r_deriv) < UMBRAL_PLANO
    return bool((mismo_signo or planos) and abs(r_td - r_deriv) <= tolerancia)


def _pct(xs: list[float]) -> dict[str, Optional[float]]:
    if not xs:
        return {str(p): None for p in PERCENTILES}
    return {str(p): float(np.percentile(xs, p)) for p in PERCENTILES}


def _correlacion(a: list[float], b: list[float]) -> Optional[float]:
    if len(a) < 2 or np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def revisiones_en(revisiones: list[tuple[int, float, float]], desde: date, hasta: date) -> dict:
    """Cuántas revisiones de Deriv caen en la ventana, y cuánto movieron el cierre."""
    dentro = [(g, n) for e, g, n in revisiones if desde <= _dia(e) <= hasta]
    rel = [abs(n - g) / g for g, n in dentro if n != g]
    return {"en_la_ventana": len(dentro), "con_otro_cierre": len(rel),
            "abs_dcierre_relativo": _pct(rel), "abs_dcierre_relativo_max": max(rel) if rel else None}


def paso_2(diarias_td: dict[int, float], diarias_deriv: dict[int, float], calendario: str,
           revisiones: Sequence[tuple[int, float, float]] = ()) -> dict:
    td, dr = del_calendario(diarias_td, calendario), del_calendario(diarias_deriv, calendario)
    comunes = sorted(set(td) & set(dr))
    r_td, r_dr = r_por_dia(td, calendario), r_por_dia(dr, calendario)
    pares = [d for d in comunes if d in r_td and d in r_dr]
    out = {"dias_comunes": len(comunes), "pares": len(pares),
           "pares_fuera_por_hueco": len(comunes) - len(pares),
           "fuera_del_calendario": {"twelvedata": len(diarias_td) - len(td),
                                    "deriv": len(diarias_deriv) - len(dr)},
           "ventana": [comunes[0].isoformat(), comunes[-1].isoformat()] if comunes else None,
           "umbral_pct": 100 * UMBRAL_COMPUERTA, "min_dias_comunes": MIN_DIAS_COMUNES}
    if not pares:
        return {**out, "coinciden": 0, "fraccion": None, "compuerta": "roja",
                "motivos": ["no hay pares"]}
    mediana_dr = median(abs(r_dr[d]) for d in pares)
    tolerancia = FACTOR_MEDIANA * mediana_dr
    coinciden = sum(1 for d in pares if coincide(r_td[d], r_dr[d], tolerancia))
    fraccion = coinciden / len(pares)
    motivos = []
    if fraccion < UMBRAL_COMPUERTA:
        motivos.append(f"coinciden {fraccion:.2%} de los pares, menos que {UMBRAL_COMPUERTA:.0%}")
    if len(comunes) < MIN_DIAS_COMUNES:
        motivos.append(f"{len(comunes)} días comunes, menos que {MIN_DIAS_COMUNES}")
    a, b = [r_td[d] for d in pares], [r_dr[d] for d in pares]
    return {**out, "mediana_abs_r_deriv": mediana_dr, "tolerancia": tolerancia,
            "coinciden": coinciden, "fraccion": fraccion,
            "fraccion_sobre_dias_comunes": coinciden / len(comunes),
            "compuerta": "roja" if motivos else "verde", "motivos": motivos,
            "informativo": {
                "correlacion_r": _correlacion(a, b),
                "abs_r_td_menos_r_deriv": _pct([abs(x - y) for x, y in zip(a, b)]),
                "dias_de_signo_opuesto": sum(1 for x, y in zip(a, b) if x * y < 0),
                "revisiones_deriv": revisiones_en(list(revisiones), comunes[0], comunes[-1])}}


# ═══ Informe ══════════════════════════════════════════════════════════════

def _fecha(e: Optional[int]) -> Optional[str]:
    return _dia(e).isoformat() if e is not None else None


def _datos(serie: str, granularidad: int, velas: dict[int, float]) -> dict:
    ruta = ruta_serie(serie, granularidad)
    return {"archivo": str(ruta), "velas": len(velas), "sha256": sha256_de_archivo(ruta),
            "primera": _fecha(min(velas) if velas else None),
            "ultima": _fecha(max(velas) if velas else None)}


def informe() -> list[tuple[str, dict]]:
    series, datos = {}, {}
    for a in ACTIVOS_D1:
        s = {"td": cierres(a.td.serie, GRANULARIDAD_DIARIA),
             "deriv": cierres(a.deriv, GRANULARIDAD_DIARIA),
             "intradia": cierres(a.intradia, a.granularidad_intradia)}
        series[a.activo] = s
        datos[a.activo] = {
            "twelvedata_1day": _datos(a.td.serie, GRANULARIDAD_DIARIA, s["td"]),
            "deriv_86400": {**_datos(a.deriv, GRANULARIDAD_DIARIA, s["deriv"]),
                            "sha256_revisiones": sha256_de_archivo(
                                ruta_revisiones(a.deriv, GRANULARIDAD_DIARIA))},
            "intradia_twelvedata": _datos(a.intradia, a.granularidad_intradia, s["intradia"])}
    bloques: list[tuple[str, dict]] = [("DATOS", datos)]
    pasos_1 = {a.activo: paso_1(series[a.activo]["td"], series[a.activo]["intradia"],
                                a.granularidad_intradia) for a in ACTIVOS_D1}
    bloques += [(f"PASO 1 {activo}", p) for activo, p in pasos_1.items()]
    detenidos = {activo: p["motivos"] for activo, p in pasos_1.items() if p["decision"] != "sigue"}
    if detenidos:
        bloques.append(("DETENIDO", {
            "motivos": detenidos, "compuerta": "no calculada, para ningún activo",
            "por_que": "comparar cierres de cortes distintos mediría el desfase, no la fuente; "
                       "el auditor rediseña la comparación con el paso 1"}))
        return bloques
    compuerta = {}
    for a in ACTIVOS_D1:
        p = paso_2(series[a.activo]["td"], series[a.activo]["deriv"], a.td.calendario,
                   cierres_revisados(a.deriv, GRANULARIDAD_DIARIA))
        bloques.append((f"PASO 2 {a.activo}", p))
        compuerta[a.activo] = p["compuerta"]
    bloques.append(("COMPUERTA", compuerta))
    return bloques


def main(argv: Optional[Sequence[str]] = None) -> int:
    argparse.ArgumentParser(prog="concordancia_d1",
                            description="Concordancia diaria de D1 contra Deriv.").parse_args(argv)
    for titulo, bloque in informe():
        publicar(titulo, bloque)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
