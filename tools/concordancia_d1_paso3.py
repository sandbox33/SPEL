"""
tools/concordancia_d1_paso3.py
================================
El paso 3 de la concordancia diaria de D1 (brief del Admin 16a): ¿TwelveData
da los mismos máximos y mínimos que Deriv, y la misma señal de ruptura de
H1 v2? Por activo, sobre los días comunes del paso 2 de
tools/concordancia_d1.py. H1 v2 entra cuando el cierre supera el máximo de
los máximos (research/preregistro_h1_v2.md, sección 2), y H2 usa el máximo y
el mínimo en Garman–Klass (research/preregistro_h2.md, sección 4): el paso 2
solo compara cierres.

Las constantes quedaron fijadas acá, en config/constantes.json y en el
decision-log antes de calcular nada con velas reales (candado 1 del brief).
Ningún test lee `metrics/velas/` reales. Este módulo no abre archivos: recibe
las velas que ya leyó tools/concordancia_d1.py, o las que llegaron en vivo.

══ LO QUE ESTE MÓDULO NO HACE ══

Ningún desenlace de trading: ni P&L, ni posiciones, ni lo que pasa después de
una ruptura. La ruptura es la entrada de H1 v2 leída como un hecho de cada
día, para comparar las dos fuentes; nada se abre ni se cierra. Los precios
solo se leen en los LECTORES (`rangos` y `rangos_de_filas`), y solo el
máximo y el mínimo: los cierres llegan del paso 2.
tests/test_guarda_concordancia_d1_paso3.py lo verifica por AST.

══ RANGO ══

  · Días: los comunes del paso 2, los que tienen barra cerrada en las dos
    fuentes, en el calendario del activo.
  · El máximo coincide si |H_TD − H_Deriv| ≤ TOL_RANGO × (H_Deriv − L_Deriv).
    El mínimo, igual, con L.
  · Un día con rango cero en Deriv queda fuera, y se cuenta.
  · El oro arma su máximo y su mínimo de TwelveData desde el M5, con las
    mismas velas que su cierre de D1-o (`velas_por_dia_desde_m5`): días
    terminados, de lunes a viernes, y velas dentro del horario de Deriv.
  · VERDE si coinciden el máximo y el mínimo en al menos UMBRAL_RANGO_PCT de
    los días que quedan.

══ SEÑAL ══

  · Para cada L de LOOKBACKS_SENAL, la ruptura del día t en una fuente es
    cierre_t > máximo de los máximos de t−L a t−1, solo con barras de la
    ventana común.
  · Métrica: días con ruptura en las dos ÷ días con ruptura en alguna.
  · Una L con menos de MIN_DIAS_CON_RUPTURA días con ruptura en alguna
    fuente queda "sin potencia" y no decide.
  · VERDE si cada L que decide llega a UMBRAL_SENAL_PCT.

Un activo queda verde solo si pasa el paso 2 y el paso 3, rango y señal
(`compuerta_del_activo`, candado 3).

[INTERPRETACIÓN] Lo que el brief no fija:
  · "La ventana común" son los días comunes del paso 2, en orden: las L
    barras anteriores al día t son los L días comunes anteriores, los mismos
    en las dos fuentes. Un día que tiene una sola fuente no entra en la
    ventana de ninguna.
  · Si ninguna L decide, la señal queda "sin potencia": no es verde, porque
    no hay con qué validarla, y tampoco roja.
  · El rango sin ningún día con rango en Deriv es ROJO, como el paso 2 sin
    pares.
  · Los umbrales se comparan en enteros (100 × coinciden ≥ UMBRAL × días):
    el borde no depende del redondeo de un float.
  · El paso 3 se calcula aunque el paso 2 sea rojo: informa. Lo que decide
    es `compuerta_del_activo`.
  · Un día común sin máximo y mínimo en una fuente es un error de
    construcción: los dos salen de las mismas velas que el cierre.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.velas import _dia  # noqa: E402
from tools.concordancia_d1 import _pct, del_calendario, velas_por_dia_desde_m5  # noqa: E402

#: Aprobados por el Admin el 26-oct (brief 16a, punto 3), antes de calcular
#: nada con velas reales.
TOL_RANGO = 0.10
UMBRAL_RANGO_PCT = 90
UMBRAL_SENAL_PCT = 90
#: Brief 16a, punto 2.
LOOKBACKS_SENAL: tuple[int, ...] = (10, 20, 40)
MIN_DIAS_CON_RUPTURA = 10


# ═══ Lectores: los ÚNICOS que tocan precios ═══════════════════════════════

def rangos(velas: pl.DataFrame) -> dict[int, tuple[float, float]]:
    """(máximo, mínimo) de cada vela de una serie ya leída, con el esquema de
    ingestion/velas.py, por época. Nada más."""
    return dict(zip(velas["epoch"].to_list(),
                    zip(velas["high"].to_list(), velas["low"].to_list())))


def rangos_de_filas(filas: dict[int, dict]) -> dict[int, tuple[float, float]]:
    """(máximo, mínimo) de cada vela que llegó en vivo, por época. Nada más."""
    return {e: (f["high"], f["low"]) for e, f in filas.items()}


# ═══ El oro desde el M5 ═══════════════════════════════════════════════════

def rangos_desde_m5(rangos_m5: dict[int, tuple[float, float]],
                    horario: Optional[dict] = None) -> dict[int, tuple[float, float]]:
    """El máximo y el mínimo de cada día UTC de D1-o, con las mismas velas que
    su cierre (`velas_por_dia_desde_m5`). {época de d a las 00:00: (H, L)}."""
    return {d: (max(rangos_m5[e][0] for e in es), min(rangos_m5[e][1] for e in es))
            for d, es in velas_por_dia_desde_m5(rangos_m5, horario).items()}


# ═══ Paso 3 ═══════════════════════════════════════════════════════════════

def dias_comunes(diarias_td: dict[int, float], diarias_deriv: dict[int, float],
                 calendario: str) -> list[date]:
    """Los días comunes del paso 2: con barra en las dos fuentes, en el
    calendario del activo. `paso_2` está fijada en 41cf853 y los cuenta por su
    cuenta; un test verifica que den lo mismo."""
    return sorted(set(del_calendario(diarias_td, calendario))
                  & set(del_calendario(diarias_deriv, calendario)))


def _por_dia(serie: dict[int, Any]) -> dict[date, Any]:
    return {_dia(e): v for e, v in serie.items()}


def _llega(coinciden: int, total: int, umbral_pct: int) -> bool:
    return 100 * coinciden >= umbral_pct * total


def paso_3_rango(rangos_td: dict[int, tuple[float, float]],
                 rangos_deriv: dict[int, tuple[float, float]], dias: list[date]) -> dict:
    """El máximo y el mínimo de cada día común contra los de Deriv, en
    fracción del rango de Deriv. Los días con rango cero quedan fuera."""
    td, dr = _por_dia(rangos_td), _por_dia(rangos_deriv)
    sin_rango = [d for d in dias if d not in td or d not in dr]
    if sin_rango:
        raise ValueError(f"días comunes sin máximo y mínimo: {[d.isoformat() for d in sin_rango[:5]]}")
    rango_cero, ok_max, ok_min, d_max, d_min = [], [], [], [], []
    for d in dias:
        (h_td, l_td), (h_dr, l_dr) = td[d], dr[d]
        ancho = h_dr - l_dr
        if ancho == 0:
            rango_cero.append(d)
            continue
        ok_max.append(abs(h_td - h_dr) <= TOL_RANGO * ancho)
        ok_min.append(abs(l_td - l_dr) <= TOL_RANGO * ancho)
        d_max.append(abs(h_td - h_dr) / ancho)
        d_min.append(abs(l_td - l_dr) / ancho)
    evaluados = len(ok_max)
    coinciden = sum(1 for a, b in zip(ok_max, ok_min) if a and b)
    motivos = []
    if not evaluados:
        motivos.append("no hay días con rango en Deriv")
    elif not _llega(coinciden, evaluados, UMBRAL_RANGO_PCT):
        motivos.append(f"coinciden máximo y mínimo en {coinciden} de {evaluados} días, menos que "
                       f"{UMBRAL_RANGO_PCT} %")
    return {"dias_comunes": len(dias), "dias_rango_cero": len(rango_cero),
            "fechas_rango_cero": [d.isoformat() for d in rango_cero],
            "dias_evaluados": evaluados, "coincide_maximo": sum(ok_max),
            "coincide_minimo": sum(ok_min), "coinciden_los_dos": coinciden,
            "fraccion": coinciden / evaluados if evaluados else None,
            "tolerancia_en_fraccion_del_rango": TOL_RANGO, "umbral_pct": UMBRAL_RANGO_PCT,
            "informativo": {"abs_dmaximo_en_fraccion_del_rango": _pct(d_max),
                            "abs_dminimo_en_fraccion_del_rango": _pct(d_min)},
            "rango": "roja" if motivos else "verde", "motivos": motivos}


def rupturas(cierres: dict[date, float], maximos: dict[date, float], dias: list[date],
             lookback: int) -> set[date]:
    """Los días t de `dias`, desde el lookback-ésimo, con cierre_t mayor que el
    máximo de los máximos de los `lookback` días anteriores de `dias`."""
    return {dias[i] for i in range(lookback, len(dias))
            if cierres[dias[i]] > max(maximos[p] for p in dias[i - lookback:i])}


def paso_3_senal(cierres_td: dict[int, float], rangos_td: dict[int, tuple[float, float]],
                 cierres_deriv: dict[int, float], rangos_deriv: dict[int, tuple[float, float]],
                 dias: list[date]) -> dict:
    """La ruptura de H1 v2 en cada fuente, por lookback, sobre los días comunes."""
    c_td, c_dr = _por_dia(cierres_td), _por_dia(cierres_deriv)
    h_td = {d: hl[0] for d, hl in _por_dia(rangos_td).items()}
    h_dr = {d: hl[0] for d, hl in _por_dia(rangos_deriv).items()}
    por_lookback = []
    for lb in LOOKBACKS_SENAL:
        a, b = rupturas(c_td, h_td, dias, lb), rupturas(c_dr, h_dr, dias, lb)
        alguna, las_dos = len(a | b), len(a & b)
        if alguna < MIN_DIAS_CON_RUPTURA:
            estado = "sin potencia"
        else:
            estado = "verde" if _llega(las_dos, alguna, UMBRAL_SENAL_PCT) else "roja"
        por_lookback.append({"lookback": lb, "dias_evaluados": max(0, len(dias) - lb),
                             "ruptura_en_alguna": alguna, "ruptura_en_las_dos": las_dos,
                             "solo_twelvedata": len(a - b), "solo_deriv": len(b - a),
                             "fraccion": las_dos / alguna if alguna else None,
                             "estado": estado})
    deciden = [f["estado"] for f in por_lookback if f["estado"] != "sin potencia"]
    senal = ("sin potencia" if not deciden
             else "verde" if all(e == "verde" for e in deciden) else "roja")
    return {"por_lookback": por_lookback, "umbral_pct": UMBRAL_SENAL_PCT,
            "min_dias_con_ruptura": MIN_DIAS_CON_RUPTURA, "senal": senal}


def paso_3(cierres_td: dict[int, float], rangos_td: dict[int, tuple[float, float]],
           cierres_deriv: dict[int, float], rangos_deriv: dict[int, tuple[float, float]],
           calendario: str) -> dict:
    """El rango y la señal de un activo, sobre los días comunes del paso 2."""
    dias = dias_comunes(cierres_td, cierres_deriv, calendario)
    r = paso_3_rango(rangos_td, rangos_deriv, dias)
    s = paso_3_senal(cierres_td, rangos_td, cierres_deriv, rangos_deriv, dias)
    if "roja" in (r["rango"], s["senal"]):
        estado = "roja"
    else:
        estado = "verde" if s["senal"] == "verde" else "sin potencia"
    return {"ventana": [dias[0].isoformat(), dias[-1].isoformat()] if dias else None,
            "rango": r, "senal": s, "paso_3": estado}


def compuerta_del_activo(compuerta_paso_2: str, estado_paso_3: str) -> str:
    """Candado 3: verde solo si el paso 2 y el paso 3 son verdes. Si no, lo
    del paso 2 si no es verde, y si no, lo del paso 3."""
    if compuerta_paso_2 != "verde":
        return compuerta_paso_2
    return estado_paso_3
