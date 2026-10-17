"""
core/estadistica_sharpe.py
==========================
La estadística del Sharpe, en una sola implementación (brief del Admin 10,
punto 2): varianza del estimador con asimetría, curtosis y autocorrelación,
PSR, MinTRL y DSR. Funciones puras, sin numpy ni scipy: `math` y
`statistics.NormalDist` de la biblioteca estándar.

══ LA VARIANZA GENERALIZADA ══

López de Prado, Lipton y Zoonekynd (SSRN 5520741 y 5950754), con retornos
AR(1) de autocorrelación ρ:

    σ²[SR₀] = (1/T)·[ (1+ρ)/(1−ρ)
                      − (1+ρ+ρ²)/(1−ρ²) · γ₃ · SR₀
                      + (1+ρ²)/(1−ρ²) · (γ₄−1)/4 · SR₀² ]

con γ₃ la asimetría y γ₄ la CURTOSIS (no el exceso: una normal tiene 3).

UNVERIFIED contra el texto del paper: SSRN no se pudo leer desde el entorno
donde se escribió esto (decision-log 2026-10-17, brief 10). Sí se contrastó
contra el código de réplica de los autores (github.com/zoonek/2025-sharpe-ratio,
`functions.py::sharpe_ratio_variance`, commit 6199ecb del 05-feb-2026). Ahí
los coeficientes se escriben a = 1 + 2ρ/(1−ρ), b = 1 + ρ/(1−ρ) + ρ²/(1−ρ²)
y c = 1 + 2ρ²/(1−ρ²), que son algebraicamente los de arriba.

Con ρ = 0 es la fórmula de Bailey y López de Prado (2012) que cita
`research/preregistro_h1.md` §9, salvo T en vez de T−1. Ese pre-registro
está sellado y conserva su fórmula: este módulo no lo reemplaza.

══ CUÁL σ VA EN CADA COSA ══

  · PSR(SR₀) = Φ( (SR̂ − SR₀) / σ[SR₀] ): la σ se evalúa en SR₀, la
    hipótesis nula, no en el Sharpe observado.
  · MinTRL = T·σ²[SR₀]·( z / (SR̂ − SR₀) )², con z un parámetro (por
    ejemplo Φ⁻¹(0,95)). No depende de T: T·σ²[SR₀] es la varianza de una
    sola observación.
  · DSR = PSR(SR*), con SR* portado de `research/preregistro_h1.md` §9:
      SR* = √V · ( (1−γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e)) )
    V la varianza de los Sharpe por barra de los ensayos, N el N efectivo y
    γ la constante de Euler–Mascheroni.

σ[SR̂] (la varianza evaluada en el Sharpe observado) es otro número y no es
la σ de ninguna de las tres. tests/test_estadistica_sharpe.py lo fija.
"""

from __future__ import annotations

import math
from statistics import NormalDist

#: La constante de Euler–Mascheroni, γ. La usa SR* (preregistro_h1.md §9).
EULER_MASCHERONI = 0.5772156649015329


def _finito(nombre: str, x: float) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ValueError(f"{nombre}={x!r}: tiene que ser un número finito")
    return float(x)


def varianza_sharpe(sr0: float, *, t: float, asimetria: float, curtosis: float,
                    rho: float = 0.0) -> float:
    """σ²[SR₀], la varianza del estimador del Sharpe evaluada en `sr0`.

    `t` es el número de observaciones; `curtosis` es γ₄ (no el exceso);
    `rho` es la autocorrelación de orden 1 de los retornos, en (−1, 1)."""
    sr0, t = _finito("sr0", sr0), _finito("t", t)
    asimetria, curtosis = _finito("asimetria", asimetria), _finito("curtosis", curtosis)
    rho = _finito("rho", rho)
    if t <= 0:
        raise ValueError(f"t={t}: hacen falta observaciones")
    if not -1 < rho < 1:
        raise ValueError(f"rho={rho}: la autocorrelación de un AR(1) estacionario está en (−1, 1)")
    a = (1 + rho) / (1 - rho)
    b = (1 + rho + rho ** 2) / (1 - rho ** 2)
    c = (1 + rho ** 2) / (1 - rho ** 2)
    v = (a - b * asimetria * sr0 + c * (curtosis - 1) / 4 * sr0 ** 2) / t
    if not v > 0:
        raise ValueError(f"σ²[{sr0}] = {v}: asimetría y curtosis incompatibles con ese SR₀")
    return v


def psr(sr: float, sr0: float, *, t: float, asimetria: float, curtosis: float,
        rho: float = 0.0) -> float:
    """PSR(SR₀) = Φ( (SR̂ − SR₀) / σ[SR₀] ), con `sr` el Sharpe observado
    por barra."""
    sr = _finito("sr", sr)
    sigma = math.sqrt(varianza_sharpe(sr0, t=t, asimetria=asimetria, curtosis=curtosis,
                                      rho=rho))
    return NormalDist().cdf((sr - sr0) / sigma)


def min_trl(sr: float, sr0: float, *, asimetria: float, curtosis: float, z: float,
            rho: float = 0.0) -> float:
    """MinTRL = T·σ²[SR₀]·( z / (SR̂ − SR₀) )², en observaciones. Solo
    existe si el Sharpe observado supera a SR₀."""
    sr, z = _finito("sr", sr), _finito("z", z)
    if not sr > sr0:
        raise ValueError(f"MinTRL no existe con SR̂ = {sr} ≤ SR₀ = {sr0}")
    varianza_de_una = varianza_sharpe(sr0, t=1, asimetria=asimetria, curtosis=curtosis,
                                      rho=rho)
    return varianza_de_una * (z / (sr - sr0)) ** 2


def sr_estrella(*, varianza_ensayos: float, n_efectivo: int) -> float:
    """SR*, el máximo esperado de N Sharpe sin habilidad (preregistro_h1.md
    §9). `varianza_ensayos` es V, la varianza de los Sharpe por barra de los
    ensayos; `n_efectivo` es N, entero y al menos 2 (con N = 1, Φ⁻¹(0) no
    existe)."""
    v = _finito("varianza_ensayos", varianza_ensayos)
    if v < 0:
        raise ValueError(f"varianza_ensayos={v}: una varianza no es negativa")
    if isinstance(n_efectivo, bool) or not isinstance(n_efectivo, int) or n_efectivo < 2:
        raise ValueError(f"n_efectivo={n_efectivo!r}: SR* necesita un N entero ≥ 2")
    g = EULER_MASCHERONI
    n = n_efectivo
    phi_inv = NormalDist().inv_cdf
    return math.sqrt(v) * ((1 - g) * phi_inv(1 - 1 / n)
                           + g * phi_inv(1 - 1 / (n * math.e)))


def dsr(sr: float, *, t: float, asimetria: float, curtosis: float, varianza_ensayos: float,
        n_efectivo: int, rho: float = 0.0) -> float:
    """DSR = PSR(SR*): el PSR contra el máximo esperado de N ensayos."""
    sr0 = sr_estrella(varianza_ensayos=varianza_ensayos, n_efectivo=n_efectivo)
    return psr(sr, sr0, t=t, asimetria=asimetria, curtosis=curtosis, rho=rho)
