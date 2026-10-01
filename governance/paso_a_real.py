"""
governance/paso_a_real.py
===========================
DG-3: cuándo una hipótesis puede pasar de demo a capital real. Decisión del
Admin del 29-sep-2026, que REEMPLAZA la del 25-sep ("6 meses de forward en
demo"). Ver decision-log.md, 2026-09-29, y governance/PRINCIPLES.md #6.

══ LAS CUATRO CONDICIONES, TODAS ══

  a) PSR ≥ 0,90 y DSR ≥ 0,90 sobre retornos diarios de cartera, en la serie
     COMBINADA histórico + demo, con el mismo N de ensayos que el
     pre-registro. Este módulo recibe los dos números ya calculados; cómo se
     calculan lo fija cada pre-registro.
  b) ≥ 30 días de demo y ≥ 20 operaciones cerradas en demo.
  c) Costos observados en demo ≤ 1,25 × costos modelados.
  d) Reconciliación demo sin discrepancias.

══ ZONA GRIS ══

Mientras falte alguna, el forward en demo sigue, con un máximo de 6 meses.
Si al vencer no se cumplen las cuatro, la hipótesis no pasa a real y el
trabajo pasa a H2.

[INTERPRETACIÓN] Los 6 meses se cuentan desde el primer día de demo, en
meses de calendario (el 31 de un mes corto cae en su último día). El día
del vencimiento es el último en que todavía se puede cumplir: después, el
veredicto es VENCIDO aunque las cuatro se cumplan, porque la ventana ya
cerró.

[INTERPRETACIÓN] Los días de demo son de calendario: `fecha - inicio`.

Esto no autoriza ninguna orden. APTO dice que DG-3 se cumple; la compuerta
de la Fase 4 y la decisión del Admin siguen haciendo falta.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

#: a) Probabilistic Sharpe Ratio mínimo sobre histórico + demo.
PSR_MINIMO = 0.90

#: a) Deflated Sharpe Ratio mínimo, con el mismo N que el pre-registro.
DSR_MINIMO = 0.90

#: b) Días de calendario de demo, como mínimo.
DIAS_MINIMOS_DEMO = 30

#: b) Operaciones cerradas en demo, como mínimo.
OPERACIONES_CERRADAS_MINIMAS_DEMO = 20

#: c) Tope de costos observados en demo, en veces los modelados.
RAZON_MAXIMA_COSTO_OBSERVADO_MODELADO = 1.25

#: Zona gris: máximo de forward en demo, en meses de calendario.
MESES_MAXIMOS_ZONA_GRIS = 6


class Veredicto:
    APTO = "APTO"
    ZONA_GRIS = "ZONA_GRIS"
    VENCIDO_PASA_A_H2 = "VENCIDO_PASA_A_H2"


@dataclass(frozen=True)
class EvidenciaDemo:
    psr: float
    dsr: float
    inicio_demo: date
    fecha: date
    operaciones_cerradas: int
    costo_observado: float
    costo_modelado: float
    discrepancias_reconciliacion: int


@dataclass(frozen=True)
class Evaluacion:
    veredicto: str
    #: Las condiciones que faltan, por letra ("a", "b", "c", "d").
    incumplidas: tuple[str, ...]
    vence_el: date


def sumar_meses(d: date, meses: int) -> date:
    """`d` más `meses` meses de calendario. Si el día no existe en el mes de
    llegada, el último día de ese mes."""
    total = d.year * 12 + (d.month - 1) + meses
    anio, mes = divmod(total, 12)
    ultimo = calendar.monthrange(anio, mes + 1)[1]
    return date(anio, mes + 1, min(d.day, ultimo))


def evaluar(e: EvidenciaDemo) -> Evaluacion:
    if e.fecha < e.inicio_demo:
        raise ValueError(f"fecha {e.fecha} anterior al inicio de demo {e.inicio_demo}")
    if e.costo_modelado <= 0:
        raise ValueError("costo_modelado tiene que ser positivo: sin él, c) no se puede evaluar")
    if e.operaciones_cerradas < 0 or e.discrepancias_reconciliacion < 0:
        raise ValueError("conteos negativos")

    incumplidas = []
    if not (e.psr >= PSR_MINIMO and e.dsr >= DSR_MINIMO):
        incumplidas.append("a")
    if not ((e.fecha - e.inicio_demo).days >= DIAS_MINIMOS_DEMO
            and e.operaciones_cerradas >= OPERACIONES_CERRADAS_MINIMAS_DEMO):
        incumplidas.append("b")
    if not e.costo_observado <= RAZON_MAXIMA_COSTO_OBSERVADO_MODELADO * e.costo_modelado:
        incumplidas.append("c")
    if e.discrepancias_reconciliacion != 0:
        incumplidas.append("d")

    vence_el = sumar_meses(e.inicio_demo, MESES_MAXIMOS_ZONA_GRIS)
    if e.fecha > vence_el or (incumplidas and e.fecha == vence_el):
        veredicto = Veredicto.VENCIDO_PASA_A_H2
    elif incumplidas:
        veredicto = Veredicto.ZONA_GRIS
    else:
        veredicto = Veredicto.APTO
    return Evaluacion(veredicto, tuple(incumplidas), vence_el)
