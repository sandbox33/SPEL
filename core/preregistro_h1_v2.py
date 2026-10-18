"""
core/preregistro_h1_v2.py
===========================
Los números y las reglas de `research/preregistro_h1_v2.md`, en código.

H1 v2 cambia respecto de v1 solo lo que deciden D5 y D12, y lo que los datos
ya resolvieron. Lo que no cambia —la rejilla, el walk-forward, el sizing, el
multiplicador, los umbrales de las compuertas y los ensayos previos— se
importa de `core/preregistro_h1.py`, sellado por el sha256 de v1: una sola
implementación por concepto, y ningún número de v1 se escribe dos veces.

La estadística del Sharpe es la de `core/estadistica_sharpe.py` (D5). Acá
están solo los estimadores de la serie que el documento fija antes de mirar
—momentos con T en el denominador y la autocorrelación de orden 1— y la
tabla de compuertas. No hay backtest.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Sequence

from core import estadistica_sharpe as es
from core.preregistro_h1 import (  # noqa: F401  (los números de v1, re-exportados)
    APALANCAMIENTO_MAXIMO,
    COMISION_REFERENCIA_FRACCION,
    COMISION_REFERENCIA_MINIMO_USD,
    DSR_MINIMO,
    ENSAYOS_PREVIOS_FALLIDOS,
    ENTRENAMIENTO_INICIAL_BARRAS,
    FRACCION_VOL_OBJETIVO,
    N_FOLDS,
    N_INGENUO,
    OOS_BARRAS_POR_FOLD,
    PSR_MINIMO,
    RAZON_STOPOUT_SALIDA,
    REJILLA_H1,
    SHARPE_MINIMO_AVANZAR,
    SHARPE_UMBRAL_ALTERNATIVA,
    VENTANA_VOL_BARRAS,
    historia_requerida,
    rejilla_soportada,
)

#: D12: con un Sharpe de la serie base debajo de esto, la estrategia se
#: rechaza y el proceso se detiene. Es el umbral de v1, que ya no lleva a
#: ninguna alternativa.
SHARPE_RECHAZO = SHARPE_UMBRAL_ALTERNATIVA

#: D12: las alternativas declaradas. Ninguna, así que ninguna suma al N.
ALTERNATIVAS_DECLARADAS: tuple[str, ...] = ()


class Veredicto:
    AVANZA = "AVANZA"
    RECHAZADA = "RECHAZADA"
    NO_CONCLUYENTE = "NO_CONCLUYENTE"


def _finito(nombre: str, x: float) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ValueError(f"{nombre}={x!r}: tiene que ser un número finito")
    return float(x)


def veredicto(sharpe: float, psr: float, dsr: float) -> str:
    """La tabla de la sección 10, con el Sharpe neto fuera de muestra
    anualizado de la serie base (BTC), su PSR(0) y su DSR. Debajo del
    umbral de rechazo se rechaza, sin mirar PSR ni DSR (D12); las tres
    juntas avanzan; cualquier otro caso es no concluyente (D5)."""
    sharpe, psr, dsr = _finito("sharpe", sharpe), _finito("psr", psr), _finito("dsr", dsr)
    if sharpe < SHARPE_RECHAZO:
        return Veredicto.RECHAZADA
    if sharpe >= SHARPE_MINIMO_AVANZAR and psr >= PSR_MINIMO and dsr >= DSR_MINIMO:
        return Veredicto.AVANZA
    return Veredicto.NO_CONCLUYENTE


def estadisticos(retornos: Sequence[float]) -> dict[str, float]:
    """T, el Sharpe por barra, la asimetría, la curtosis (no el exceso) y ρ
    de una serie de retornos, con los estimadores de la sección 9: momentos
    con T en el denominador, y ρ la autocorrelación de orden 1."""
    r = [_finito("retorno", x) for x in retornos]
    t = len(r)
    if t < 3:
        raise ValueError(f"T={t}: hacen falta al menos 3 retornos")
    if max(r) == min(r):
        # Se compara antes de restar: la media de una serie constante no
        # siempre da exacta, y su m2 saldría positivo por redondeo.
        raise ValueError("serie constante: el Sharpe no existe")
    media = sum(r) / t
    d = [x - media for x in r]
    m2 = sum(x * x for x in d) / t
    m3 = sum(x ** 3 for x in d) / t
    m4 = sum(x ** 4 for x in d) / t
    rho = sum(d[i] * d[i - 1] for i in range(1, t)) / (t * m2)
    return {"t": t, "sharpe": media / math.sqrt(m2), "asimetria": m3 / m2 ** 1.5,
            "curtosis": m4 / m2 ** 2, "rho": rho}


def _momentos(e: dict[str, float]) -> dict[str, float]:
    return {"asimetria": e["asimetria"], "curtosis": e["curtosis"], "rho": e["rho"]}


def psr_cero(retornos: Sequence[float]) -> float:
    """PSR(0) de la sección 9: la σ evaluada en SR₀ = 0, con T y con el ρ
    de la serie (D5)."""
    e = estadisticos(retornos)
    return es.psr(e["sharpe"], 0.0, t=e["t"], **_momentos(e))


def dsr_serie(retornos: Sequence[float], *, varianza_ensayos: float, n_efectivo: int) -> float:
    """DSR de la sección 9: PSR(SR*) con el N efectivo y la V de los ensayos."""
    e = estadisticos(retornos)
    return es.dsr(e["sharpe"], t=e["t"], varianza_ensayos=varianza_ensayos,
                  n_efectivo=n_efectivo, **_momentos(e))


def min_trl_diagnostico(retornos: Sequence[float]) -> float:
    """MinTRL de la sección 9, solo diagnóstico: las barras que harían falta
    para que PSR(0) llegue a PSR_MINIMO con el Sharpe observado."""
    e = estadisticos(retornos)
    return es.min_trl(e["sharpe"], 0.0, z=NormalDist().inv_cdf(PSR_MINIMO), **_momentos(e))
