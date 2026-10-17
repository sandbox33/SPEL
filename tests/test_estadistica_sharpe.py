"""
tests/test_estadistica_sharpe.py
==================================
core/estadistica_sharpe.py (brief del Admin 10, punto 2).

El vector del brief: μ = 0,036 %, σ = 0,079 %, γ₃ = −2,448, γ₄ = 10,164,
ρ = 0,2, T = 24, SR̂ = 0,45570, z = Φ⁻¹(0,95). Con ρ = 0 se contrasta además
contra los números de los tests del código de réplica de los autores
(github.com/zoonek/2025-sharpe-ratio, functions.py, commit 6199ecb):
σ[SR̂] = 0,329, MinTRL = 13,029, PSR(0) = 0,987 y PSR(0,1) = 0,939.
"""

from __future__ import annotations

import math
from statistics import NormalDist

import pytest

from core import estadistica_sharpe as es

PHI = NormalDist()
SR = 0.00036 / 0.00079
T = 24
Z95 = PHI.inv_cdf(0.95)
NO_NORMAL = dict(asimetria=-2.448, curtosis=10.164)
VECTOR = dict(NO_NORMAL, rho=0.2)


def _sigma(sr0, **k):
    return math.sqrt(es.varianza_sharpe(sr0, t=T, **k))


def test_el_sharpe_del_vector_es_el_del_brief():
    assert round(SR, 5) == 0.45570


# ═══ El vector del brief ══════════════════════════════════════════════════

@pytest.mark.parametrize("sr0, sigma, psr, mintrl", [
    (0.0, 0.2500, 0.9658, 19.543),
    (0.1, 0.2770, 0.9005, 39.369),
])
def test_el_vector_del_brief(sr0, sigma, psr, mintrl):
    assert round(_sigma(sr0, **VECTOR), 4) == sigma
    assert round(es.psr(SR, sr0, t=T, **VECTOR), 4) == psr
    assert round(es.min_trl(SR, sr0, z=Z95, **VECTOR), 3) == mintrl


def test_sigma_en_el_sharpe_observado_no_es_la_del_psr():
    """σ[SR̂] = 0,3795 es otro número: el PSR divide por σ[SR₀]. Usar la
    del Sharpe observado da 0,885 en vez de 0,9658."""
    s_obs = _sigma(SR, **VECTOR)
    assert round(s_obs, 4) == 0.3795
    con_la_equivocada = PHI.cdf((SR - 0) / s_obs)
    assert round(con_la_equivocada, 3) == 0.885
    assert es.psr(SR, 0.0, t=T, **VECTOR) == pytest.approx(PHI.cdf(SR / 0.25))
    assert es.psr(SR, 0.0, t=T, **VECTOR) != pytest.approx(con_la_equivocada, abs=1e-3)


# ═══ Contra el código de réplica de los autores, con ρ = 0 ════════════════

def test_con_rho_cero_da_los_numeros_de_los_autores():
    assert round(_sigma(SR, **NO_NORMAL), 3) == 0.329
    assert round(es.min_trl(SR, 0.0, z=Z95, **NO_NORMAL), 3) == 13.029
    assert round(es.psr(SR, 0.0, t=T, **NO_NORMAL), 3) == 0.987
    assert round(es.psr(SR, 0.1, t=T, **NO_NORMAL), 3) == 0.939


def test_con_normales_iid_la_sigma_es_la_de_lo_gaussiano():
    """Los autores: σ_SR = 0,214 para γ₃ = 0, γ₄ = 3, ρ = 0."""
    assert round(_sigma(SR, asimetria=0.0, curtosis=3.0), 3) == 0.214


# ═══ Con ρ = 0, la de 2012 salvo T en vez de T−1 ══════════════════════════

def _varianza_2012(sr, *, t, asimetria, curtosis):
    """preregistro_h1.md §9 (Bailey y López de Prado, 2012): el denominador
    del PSR es √(1 − γ₃·SR + (γ₄−1)/4 · SR²) / √(T−1)."""
    return (1 - asimetria * sr + (curtosis - 1) / 4 * sr ** 2) / (t - 1)


@pytest.mark.parametrize("x", [0.0, 0.1, 0.25, SR, -0.3])
@pytest.mark.parametrize("t", [24, 252, 2])
def test_con_rho_cero_se_reduce_a_la_de_2012_salvo_t(x, t):
    nueva = es.varianza_sharpe(x, t=t, rho=0.0, **NO_NORMAL)
    vieja = _varianza_2012(x, t=t, **NO_NORMAL)
    assert nueva * t == pytest.approx(vieja * (t - 1))
    assert nueva != pytest.approx(vieja)


def test_el_psr_sellado_de_h1_evalua_la_sigma_en_otro_punto():
    """preregistro_h1 v1 está sellado y conserva su fórmula: además de T−1,
    evalúa la varianza en el SR observado, no en SR₀. Para SR₀ = 0 no es
    el PSR de este módulo con ρ = 0."""
    psr_h1 = PHI.cdf((SR - 0) / math.sqrt(_varianza_2012(SR, t=T, **NO_NORMAL)))
    assert round(psr_h1, 3) == 0.913
    assert round(es.psr(SR, 0.0, t=T, **NO_NORMAL), 3) == 0.987


# ═══ MinTRL ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("t", [1, 24, 1000])
def test_mintrl_es_t_por_sigma2_y_no_depende_de_t(t):
    esperado = t * es.varianza_sharpe(0.1, t=t, **VECTOR) * (Z95 / (SR - 0.1)) ** 2
    assert es.min_trl(SR, 0.1, z=Z95, **VECTOR) == pytest.approx(esperado)


def test_z_es_un_parametro():
    z90 = PHI.inv_cdf(0.90)
    assert es.min_trl(SR, 0.0, z=z90, **VECTOR) < es.min_trl(SR, 0.0, z=Z95, **VECTOR)
    assert es.min_trl(SR, 0.0, z=2 * Z95, **VECTOR) == pytest.approx(
        4 * es.min_trl(SR, 0.0, z=Z95, **VECTOR))


@pytest.mark.parametrize("sr0", [SR, SR + 0.1])
def test_sin_ventaja_no_hay_mintrl(sr0):
    with pytest.raises(ValueError, match="MinTRL"):
        es.min_trl(SR, sr0, z=Z95, **VECTOR)


# ═══ SR* y DSR (preregistro_h1.md §9) ═════════════════════════════════════

def test_euler_mascheroni():
    # Σ 1/k − ln n converge a γ; con n = 10⁶ el error es ~1/(2n).
    n = 1_000_000
    aprox = sum(1 / k for k in range(1, n + 1)) - math.log(n) - 1 / (2 * n)
    assert es.EULER_MASCHERONI == pytest.approx(aprox, abs=1e-10)
    assert es.EULER_MASCHERONI == 0.5772156649015329


def test_sr_estrella_es_la_formula_del_preregistro():
    v, n = 0.1, 10
    g = es.EULER_MASCHERONI
    esperado = math.sqrt(v) * ((1 - g) * PHI.inv_cdf(1 - 1 / n)
                               + g * PHI.inv_cdf(1 - 1 / (n * math.e)))
    assert es.sr_estrella(varianza_ensayos=v, n_efectivo=n) == pytest.approx(esperado)
    assert round(es.sr_estrella(varianza_ensayos=v, n_efectivo=n), 4) == 0.4979


def test_sr_estrella_crece_con_n_y_con_v_y_es_cero_sin_varianza():
    a = es.sr_estrella(varianza_ensayos=0.01, n_efectivo=2)
    b = es.sr_estrella(varianza_ensayos=0.01, n_efectivo=10)
    c = es.sr_estrella(varianza_ensayos=0.04, n_efectivo=10)
    assert a < b and c == pytest.approx(2 * b)
    assert es.sr_estrella(varianza_ensayos=0.0, n_efectivo=10) == 0.0


@pytest.mark.parametrize("n", [1, 0, -3, 2.5, True])
def test_sr_estrella_pide_un_n_entero_de_al_menos_2(n):
    with pytest.raises(ValueError, match="N"):
        es.sr_estrella(varianza_ensayos=0.01, n_efectivo=n)


def test_dsr_es_el_psr_contra_sr_estrella():
    k = dict(t=T, **VECTOR)
    s = es.sr_estrella(varianza_ensayos=0.01, n_efectivo=10)
    assert es.dsr(SR, varianza_ensayos=0.01, n_efectivo=10, **k) == pytest.approx(
        es.psr(SR, s, **k))
    assert (es.dsr(SR, varianza_ensayos=0.01, n_efectivo=100, **k)
            < es.dsr(SR, varianza_ensayos=0.01, n_efectivo=10, **k)
            < es.psr(SR, 0.0, **k))


# ═══ Entradas imposibles ══════════════════════════════════════════════════

@pytest.mark.parametrize("cambio", [
    {"rho": 1.0}, {"rho": -1.0}, {"rho": 1.5}, {"t": 0}, {"t": -24},
    {"curtosis": float("nan")}, {"asimetria": float("inf")}, {"rho": True},
])
def test_entradas_imposibles_lanzan(cambio):
    k = dict(t=T, **VECTOR)
    k.update(cambio)
    with pytest.raises(ValueError):
        es.varianza_sharpe(0.1, **k)


def test_una_varianza_no_positiva_lanza():
    """Asimetría positiva grande con SR₀ grande: la expresión cae bajo cero."""
    with pytest.raises(ValueError, match="incompatibles"):
        es.varianza_sharpe(3.0, t=T, asimetria=5.0, curtosis=3.0)


def test_rho_negativo_es_valido_y_achica_la_varianza():
    assert es.varianza_sharpe(0.0, t=T, rho=-0.2, **NO_NORMAL) < es.varianza_sharpe(
        0.0, t=T, rho=0.0, **NO_NORMAL)


def test_el_modulo_no_importa_numpy_ni_scipy():
    import ast
    import pathlib
    arbol = ast.parse(pathlib.Path(es.__file__).read_text(encoding="utf-8"))
    importados = set()
    for n in ast.walk(arbol):
        if isinstance(n, ast.Import):
            importados |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            importados.add(n.module.split(".")[0])
    assert importados <= {"__future__", "math", "statistics"}, importados


@pytest.mark.parametrize("llamada", [
    lambda: es.psr(float("nan"), 0.0, t=T, **VECTOR),
    lambda: es.psr(SR, 0.0, t=True, **VECTOR),
    lambda: es.min_trl(SR, 0.0, z=float("nan"), **VECTOR),
    lambda: es.varianza_sharpe("0.1", t=T, **VECTOR),
    lambda: es.sr_estrella(varianza_ensayos=float("inf"), n_efectivo=10),
])
def test_ni_nan_ni_bool_ni_texto_pasan_en_silencio(llamada):
    """Sin el control de finitos, un NaN en el Sharpe daría un PSR NaN sin
    error, y True contaría como T = 1."""
    with pytest.raises(ValueError):
        llamada()


def test_una_varianza_de_ensayos_negativa_lanza():
    with pytest.raises(ValueError, match="negativa"):
        es.sr_estrella(varianza_ensayos=-0.01, n_efectivo=10)
