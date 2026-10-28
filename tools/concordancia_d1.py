"""
tools/concordancia_d1.py
==========================
¿Las velas diarias de TwelveData (D1) son un proxy fiel de las de Deriv?
Brief del Admin 14, 14b, puntos 2 y 3. Patrón: tools/concordancia_xauusd.py.

Todo lo de este módulo quedó fijado antes de bajar un precio diario, y no se
ajusta después de ver un resultado (brief 14b).

Brief del Admin 15, con las decisiones del Admin del 19-oct: cada activo se
evalúa solo (D1-i). La diaria de TwelveData del oro sale de su M5 (D1-o) y
su 1day no se lee (D1-x); su paso 1 verifica el corte de Deriv. La regla del
paso 2 es la de 41cf853, y un test lo verifica contra ese commit.

══ LO QUE ESTE MÓDULO NO HACE ══

No calcula ningún desenlace de trading: ni P&L, ni posiciones, ni nada que
acumule el movimiento de un día con el del siguiente. Compara cierres entre
fuentes y, día por día, el logaritmo del cociente de dos cierres seguidos de
una misma fuente. Los precios solo se leen en `cierres` y
`cierres_revisados`, y solo el cierre: tests/test_guarda_concordancia_d1.py
lo verifica por AST con la guarda de concordancia_xauusd.py, portada.

══ PASO 1: EL CORTE (no es la compuerta) ══

BTC, la hora de corte de TwelveData: para cada hora h de 00:00 a 23:00 UTC,
el cierre diario con corte h de la fecha D es el cierre de la última vela
intradía que termina a más tardar en el instante de corte T(D, h) y empieza
a lo sumo 24 h antes. Coincide con la barra 1day de TwelveData de fecha D si
difieren en a lo sumo TOLERANCIA_CIERRE_USD (±0,01 USD). Intradía, en UTC:
la muestra 1h de BTC (ingestion/velas_d1.py).

El oro, el corte de Deriv (brief 15, punto 3): para cada día con las dos, el
close de la barra 86400 de frxXAUUSD contra el de su última vela M5 del
mismo día UTC (`diarias_desde_m5`), a ±0,01 USD.

Un activo SE DETIENE, sin calcular su compuerta, si su paso 1 no llega a
UMBRAL_CORTE (95 % de los días) o, en BTC, si la h ganadora no es
HORA_CORTE_EXIGIDA (00:00 UTC): comparar cierres de cortes distintos mediría
el desfase, no la fuente. El otro activo sigue (D1-i).

══ PASO 2: LA COMPUERTA (la de cada activo cuyo paso 1 sigue) ══

  · Días comunes: los que tienen barra cerrada en las dos fuentes. La de
    TwelveData del oro es `diarias_desde_m5` con las velas dentro del
    horario de Deriv (opción 2 del Admin): el viernes cierra con Deriv y no
    con la noche de TwelveData. Sin el horario, el oro se detiene. La misma
    compuerta con la regla de 24:00 (sin el horario) se informa como
    sensibilidad: si el veredicto no cambia, la regla queda.
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
  · El horario de Deriv es la última línea de metrics/calendario y se aplica
    a todos los días, como en el ORB (`horario_vigente` y `franja`, de
    concordancia_xauusd.py): el viernes, desde el primer cierre del día, es
    fin de semana. De lunes a jueves da la misma vela que la regla de 24:00
    (los 206 días de la ventana de Deriv, decision-log del 19-oct).
  · El día de la última vela de una serie M5 no tiene cierre: la serie puede
    terminar a mitad de ese día, y una diaria sale solo de días terminados.

Uso:
    python tools/concordancia_d1.py      # lee metrics/ y publica el informe
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Iterable, Optional, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from governance.persistence import PersistenceStream, stream_path  # noqa: E402
from ingestion.velas import (  # noqa: E402
    GRANULARIDAD_DIARIA,
    Calendario,
    _dia,
    _esperado,
    _leer_filas,
    leer_velas,
    ruta_revisiones,
    ruta_serie,
)
from ingestion.velas_d1 import ACTIVOS_D1, ActivoD1  # noqa: E402
from ingestion.velas_intradia import GRANULARIDAD_M5, publicar, sha256_de_archivo  # noqa: E402
from tools.concordancia_xauusd import datos_del_horario, franja, horario_vigente  # noqa: E402

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
#: D1-o y D1-x (brief 15): la diaria de TwelveData de estos activos sale de
#: su M5 y su paso 1 verifica el corte de Deriv. Su 1day sigue en data, sin leerse.
DIARIA_DESDE_M5: frozenset[str] = frozenset({"XAU"})
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


# ═══ Brief 15: la diaria desde el M5 (D1-o) y el paso 1 del oro ══════════

def velas_por_dia_desde_m5(epocas: Iterable[int],
                           horario: Optional[dict] = None) -> dict[int, list[int]]:
    """Las velas M5 que forman cada día UTC de D1-o: las que abren dentro de
    él, de lunes a viernes, y, con `horario`, solo aquellas en que Deriv
    cotiza según él (`franja`). El día de la última vela de la serie no
    entra: la serie puede terminar a mitad de ese día. Un día sin velas no
    aparece. {época de d a las 00:00: épocas de sus velas, en orden}. Es la
    misma selección para el cierre (`diarias_desde_m5`) y para el máximo y el
    mínimo del paso 3."""
    epocas = sorted(epocas)
    if not epocas:
        return {}
    abierto = epocas[-1] - epocas[-1] % GRANULARIDAD_DIARIA
    out: dict[int, list[int]] = {}
    for e in epocas:
        d = e - e % GRANULARIDAD_DIARIA
        if d < abierto and _esperado(_dia(d), Calendario.HABIL) and (
                horario is None or franja(e, horario) is None):
            out.setdefault(d, []).append(e)
    return out


def diarias_desde_m5(m5: dict[int, float], granularidad: int = GRANULARIDAD_M5,
                     horario: Optional[dict] = None) -> dict[int, float]:
    """D1-o: el cierre del día UTC d es el de la ÚLTIMA vela que abre dentro
    de d, de 00:00 a 23:55: lo que cierra una barra de 00:00 a 00:00 UTC. Es
    el cierre al corte de las 00:00 de d+1. No exige la de las 23:55, un día
    sin velas no tiene cierre (no se interpola), y solo cuenta de lunes a
    viernes. Con `horario`, solo cuentan las velas en que Deriv cotiza según
    él (`franja`): el viernes del oro cierra con Deriv (opción 2 del Admin).
    El día de la última vela de la serie no tiene cierre: la serie puede
    terminar a mitad de ese día. Las velas de cada día salen de
    `velas_por_dia_desde_m5`. {época de d a las 00:00: cierre}. No escribe
    nada."""
    dias = velas_por_dia_desde_m5(m5, horario)
    velas = {e: m5[e] for es in dias.values() for e in es}
    return {d: cierre_al_corte(velas, granularidad, d + GRANULARIDAD_DIARIA) for d in dias}


def paso_1_deriv(diarias_deriv: dict[int, float], m5_deriv: dict[int, float]) -> dict:
    """Paso 1 del oro (brief 15, punto 3): verifica el corte de Deriv. Para
    cada día con las dos, el close de la barra 86400 contra el de la última
    vela M5 de Deriv del mismo día UTC (la regla de D1-o), a
    TOLERANCIA_CIERRE_USD. Sigue si coincide en UMBRAL_CORTE de los días."""
    m5 = diarias_desde_m5(m5_deriv)
    dias = sorted(set(diarias_deriv) & set(m5))
    coinciden = sum(1 for d in dias if abs(diarias_deriv[d] - m5[d]) <= TOLERANCIA_CIERRE_USD)
    fraccion = coinciden / len(dias) if dias else None
    if not dias:
        motivos = ["no hay días con barra diaria y velas M5 de Deriv"]
    elif fraccion < UMBRAL_CORTE:
        motivos = [f"el cierre diario de Deriv coincide con su M5 en {fraccion:.2%} de los "
                   f"días, menos que {UMBRAL_CORTE:.0%}"]
    else:
        motivos = []
    return {"dias": len(dias), "primer_dia": _fecha(dias[0]) if dias else None,
            "ultimo_dia": _fecha(dias[-1]) if dias else None, "coinciden": coinciden,
            "fraccion": fraccion, "tolerancia_usd": TOLERANCIA_CIERRE_USD,
            "umbral_pct": 100 * UMBRAL_CORTE,
            "decision": "detenido" if motivos else "sigue", "motivos": motivos}


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


def _series(a: ActivoD1) -> dict:
    """Lo que lee cada activo, sus datos y su paso 1. El oro no lee su 1day
    de TwelveData (D1-x)."""
    deriv = cierres(a.deriv, GRANULARIDAD_DIARIA)
    deriv_86400 = {**_datos(a.deriv, GRANULARIDAD_DIARIA, deriv),
                   "sha256_revisiones": sha256_de_archivo(ruta_revisiones(a.deriv, GRANULARIDAD_DIARIA))}
    intradia = cierres(a.intradia, a.granularidad_intradia)
    if a.activo in DIARIA_DESDE_M5:
        deriv_m5 = cierres(a.deriv, a.granularidad_intradia)
        fila = horario_vigente()
        return {"deriv": deriv, "intradia": intradia, "horario": (fila or {}).get("horario"),
                "datos": {"intradia_twelvedata": _datos(a.intradia, a.granularidad_intradia, intradia),
                          "deriv_86400": deriv_86400,
                          "deriv_m5": _datos(a.deriv, a.granularidad_intradia, deriv_m5),
                          "horario_deriv": datos_del_horario(fila)},
                "paso_1": paso_1_deriv(deriv, deriv_m5)}
    td = cierres(a.td.serie, GRANULARIDAD_DIARIA)
    return {"deriv": deriv, "intradia": intradia, "td": td,
            "datos": {"twelvedata_1day": _datos(a.td.serie, GRANULARIDAD_DIARIA, td),
                      "deriv_86400": deriv_86400,
                      "intradia_twelvedata": _datos(a.intradia, a.granularidad_intradia, intradia)},
            "paso_1": paso_1(td, intradia, a.granularidad_intradia)}


def _pasos_2(a: ActivoD1, s: dict) -> list[tuple[str, dict]]:
    """La compuerta de un activo cuyo paso 1 sigue. La del oro, con su
    diaria desde el M5 y el horario de Deriv, y con la regla de 24:00 como
    sensibilidad."""
    revisiones = cierres_revisados(a.deriv, GRANULARIDAD_DIARIA)
    if a.activo not in DIARIA_DESDE_M5:
        return [(f"PASO 2 {a.activo}", paso_2(s["td"], s["deriv"], a.td.calendario, revisiones))]
    p = paso_2(diarias_desde_m5(s["intradia"], a.granularidad_intradia, s["horario"]),
               s["deriv"], a.td.calendario, revisiones)
    sens = paso_2(diarias_desde_m5(s["intradia"], a.granularidad_intradia),
                  s["deriv"], a.td.calendario, revisiones)
    return [(f"PASO 2 {a.activo}",
             {"diaria_de_twelvedata": "M5: la última vela del día dentro del horario de Deriv", **p}),
            (f"SENSIBILIDAD {a.activo}",
             {"diaria_de_twelvedata": "M5: la última vela del día UTC (el viernes, a las 24:00)",
              "el_veredicto_cambia": sens["compuerta"] != p["compuerta"], **sens})]


def informe() -> list[tuple[str, dict]]:
    series = {a.activo: _series(a) for a in ACTIVOS_D1}
    bloques: list[tuple[str, dict]] = [("DATOS", {k: s["datos"] for k, s in series.items()})]
    bloques += [(f"PASO 1 {k}", s["paso_1"]) for k, s in series.items()]
    compuerta = {}
    for a in ACTIVOS_D1:
        s = series[a.activo]
        motivos = list(s["paso_1"]["motivos"]) if s["paso_1"]["decision"] != "sigue" else []
        if a.activo in DIARIA_DESDE_M5 and not s["horario"]:
            motivos.append("sin horario de Deriv en metrics/calendario: el viernes de TwelveData "
                           "cierra según ese horario (opción 2 del Admin)")
        if motivos:
            bloques.append((f"DETENIDO {a.activo}", {"motivos": motivos, "compuerta": "no calculada"}))
            compuerta[a.activo] = "detenido"
            continue
        pasos = _pasos_2(a, s)
        bloques += pasos
        compuerta[a.activo] = pasos[0][1]["compuerta"]
    bloques.append(("COMPUERTA", compuerta))
    return bloques


# ═══ Brief 16a: el veredicto en data ══════════════════════════════════════
#
# Cada corrida del workflow deja su informe en la rama `data`: solo métricas
# agregadas, sin series por vela, con el sha256 de cada archivo de
# TwelveData usado y la atribución que piden sus Términos. Append-only: un
# archivo por corrida, que no se reescribe.

def ruta_informes() -> Path:
    """metrics/compuertas/concordancia_d1/ en el stream METRICS (rama data)."""
    return Path(stream_path(PersistenceStream.METRICS)) / "compuertas" / "concordancia_d1"


def archivos_twelvedata() -> list[tuple[str, int]]:
    """(serie, granularidad) de TwelveData que lee la compuerta: por activo,
    su 1day (salvo los que salen del M5, D1-x) y su intradía."""
    out = []
    for a in ACTIVOS_D1:
        if a.activo not in DIARIA_DESDE_M5:
            out.append((a.td.serie, GRANULARIDAD_DIARIA))
        out.append((a.intradia, a.granularidad_intradia))
    return out


def _relativa(ruta: Path) -> str:
    """La ruta dentro de data: metrics/…, sin la raíz de la máquina."""
    return (Path("metrics") / ruta.relative_to(stream_path(PersistenceStream.METRICS))).as_posix()


def faltan_archivos_twelvedata() -> list[str]:
    """Los archivos de TwelveData que la compuerta necesita y no están. Con
    la ingesta apagada no se bajan: si falta uno, la corrida se detiene."""
    return [_relativa(ruta_serie(s, g)) for s, g in archivos_twelvedata()
            if not ruta_serie(s, g).exists()]


def informe_para_data(bloques: Sequence[tuple[str, dict]], *, fecha: str, run_id: str) -> dict:
    """El informe de una corrida para data: los bloques, el sha256 de cada
    archivo de TwelveData usado y la atribución, que piden los Términos de
    TwelveData en lo que se publica."""
    return {"fecha": fecha, "run_id": run_id, "atribucion": "Data provided by Twelve Data",
            "archivos_twelvedata": {_relativa(ruta_serie(s, g)): sha256_de_archivo(ruta_serie(s, g))
                                    for s, g in archivos_twelvedata()},
            "bloques": dict(bloques)}


def guardar_informe(informe: dict) -> Path:
    """Escribe metrics/compuertas/concordancia_d1/<fecha>_<run_id>.json. Si
    el archivo ya existe, no lo toca y lanza: append-only."""
    ruta = ruta_informes() / f"{informe['fecha']}_{informe['run_id']}.json"
    if ruta.exists():
        raise FileExistsError(f"{_relativa(ruta)} ya existe: un informe no se reescribe")
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(informe, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return ruta


def resumen_markdown(informe: dict) -> str:
    """El mismo informe para $GITHUB_STEP_SUMMARY: la compuerta por activo,
    los archivos con su sha256, la atribución y el JSON entero."""
    lineas = [f"## Concordancia diaria D1 — {informe['fecha']}, corrida {informe['run_id']}", ""]
    compuerta = informe["bloques"].get("COMPUERTA") or {}
    if compuerta:
        lineas += ["| activo | compuerta |", "|---|---|"]
        lineas += [f"| {activo} | {estado} |" for activo, estado in compuerta.items()]
        lineas.append("")
    lineas += ["| archivo de TwelveData | sha256 |", "|---|---|"]
    lineas += [f"| `{r}` | `{s}` |" for r, s in informe["archivos_twelvedata"].items()]
    lineas += ["", informe["atribucion"], "", "```json",
               json.dumps(informe, ensure_ascii=False, indent=1), "```", ""]
    return "\n".join(lineas)


def main(argv: Optional[Sequence[str]] = None) -> int:
    argparse.ArgumentParser(prog="concordancia_d1",
                            description="Concordancia diaria de D1 contra Deriv.").parse_args(argv)
    for titulo, bloque in informe():
        publicar(titulo, bloque)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
