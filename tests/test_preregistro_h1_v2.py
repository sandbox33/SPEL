"""
tests/test_preregistro_h1_v2.py
=================================
H1 v2 (brief del Admin 11–13, tarea 13): se escribe antes del backtest y no
se modifica. Este test hace valer eso, como el de v1, y además separa v2 de
v1 donde D5 y D12 los separan:

  · el sha256 de research/preregistro_h1_v2.md está fijado acá;
  · cada número que v2 toma de v1 tiene que estar escrito en v2, y es el
    mismo objeto que el de v1: no se duplica;
  · las compuertas (D5, D12) y la estadística del Sharpe (D5), con σ en
    SR₀ y el ρ de la serie, distinta de la de v1.
"""

from __future__ import annotations

import ast
import hashlib
import math
from pathlib import Path
from statistics import NormalDist

import pytest

import core.preregistro_h1 as v1
import core.preregistro_h1_v2 as v2
from core import estadistica_sharpe as es

RAIZ = Path(__file__).resolve().parent.parent
DOC = RAIZ / "research" / "preregistro_h1_v2.md"

#: Si cambia, el documento cambió. Después de fusionado, eso es un
#: experimento nuevo: preregistro_h1_v3.md, no una edición de este.
SHA256_PREREGISTRO = "7ffc6150fb46cd0d5882b885f3287050f3cef7b56b5a54965053827815976e75"


def _plano() -> str:
    return " ".join(DOC.read_text(encoding="utf-8").split())


def test_el_documento_no_cambio():
    assert hashlib.sha256(DOC.read_bytes()).hexdigest() == SHA256_PREREGISTRO


def test_v1_sigue_sellado():
    """H1 v1 queda sellado, sin tocar: su sha256 es el que fija su test."""
    from tests.test_preregistro_h1 import SHA256_PREREGISTRO as SHA_V1
    doc_v1 = RAIZ / "research" / "preregistro_h1.md"
    assert hashlib.sha256(doc_v1.read_bytes()).hexdigest() == SHA_V1


# ═══ Lo que v2 toma de v1 ═══════════════════════════════════════════════════

@pytest.mark.parametrize("nombre, como_esta_escrito", [
    ("REJILLA_H1", "{10, 20, 40, 80, 160, 320}"),
    ("ENTRENAMIENTO_INICIAL_BARRAS", "entrenamiento inicial de **504** barras"),
    ("OOS_BARRAS_POR_FOLD", "**126** barras fuera de muestra"),
    ("N_FOLDS", "**4** folds"),
    ("FRACCION_VOL_OBJETIVO", "capital × 0,25 / vol_realizada_20d_anualizada"),
    ("VENTANA_VOL_BARRAS", "las **20** barras anteriores"),
    ("APALANCAMIENTO_MAXIMO", "tope de apalancamiento de 2×"),
    ("RAZON_STOPOUT_SALIDA", "**al menos 1,5 veces**"),
    ("COMISION_REFERENCIA_FRACCION", "**0,1 % del nocional"),
    ("COMISION_REFERENCIA_MINIMO_USD", "mínimo de 0,10 USD**"),
    ("SHARPE_MINIMO_AVANZAR", "Sharpe **≥ 0,5**"),
    ("PSR_MINIMO", "**PSR(0) ≥ 0,90**"),
    ("DSR_MINIMO", "**DSR ≥ 0,90**"),
    ("SHARPE_RECHAZO", "Sharpe **< 0,3**"),
    ("ENSAYOS_PREVIOS_FALLIDOS", "Suman **3**"),
    ("N_INGENUO", "**N = 10**"),
])
def test_cada_numero_esta_escrito_en_v2(nombre, como_esta_escrito):
    valor = getattr(v2, nombre)
    assert como_esta_escrito in _plano(), como_esta_escrito
    if isinstance(valor, tuple):
        assert como_esta_escrito == "{" + ", ".join(map(str, valor)) + "}"
    elif nombre == "COMISION_REFERENCIA_FRACCION":
        assert f"{valor * 100:.1f}".replace(".", ",") in como_esta_escrito
    else:
        assert f"{valor:g}".replace(".", ",") in como_esta_escrito


@pytest.mark.parametrize("nombre", [
    "REJILLA_H1", "ENTRENAMIENTO_INICIAL_BARRAS", "OOS_BARRAS_POR_FOLD", "N_FOLDS",
    "FRACCION_VOL_OBJETIVO", "VENTANA_VOL_BARRAS", "APALANCAMIENTO_MAXIMO",
    "RAZON_STOPOUT_SALIDA", "COMISION_REFERENCIA_FRACCION", "COMISION_REFERENCIA_MINIMO_USD",
    "SHARPE_MINIMO_AVANZAR", "PSR_MINIMO", "DSR_MINIMO", "ENSAYOS_PREVIOS_FALLIDOS",
    "N_INGENUO", "historia_requerida", "rejilla_soportada",
])
def test_lo_que_no_cambia_es_lo_de_v1_y_no_una_copia(nombre):
    assert getattr(v2, nombre) is getattr(v1, nombre)


def test_v2_no_reescribe_ningun_numero_de_v1():
    arbol = ast.parse((RAIZ / "core" / "preregistro_h1_v2.py").read_text(encoding="utf-8"))
    asignados = {t.id for n in arbol.body if isinstance(n, (ast.Assign, ast.AnnAssign))
                 for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
                 if isinstance(t, ast.Name)}
    de_v1 = {n for n in dir(v1) if n.isupper()}
    assert not asignados & de_v1, asignados & de_v1


def test_la_comision_medida_es_la_del_registro_de_kappa():
    from ingestion.kappa_deriv import KAPPA_DERIV
    assert KAPPA_DERIV["cryBTCUSD"] == 0.0003
    assert "da **0,0003** para `cryBTCUSD`" in _plano()


# ═══ D12: sin alternativa ═══════════════════════════════════════════════════

def test_no_hay_alternativa_declarada():
    assert v2.ALTERNATIVAS_DECLARADAS == ()
    assert "La alternativa declarada es **ninguna**." in _plano()
    assert v2.SHARPE_RECHAZO == v1.SHARPE_UMBRAL_ALTERNATIVA == 0.3


# ═══ Las compuertas (D5, D12) ═══════════════════════════════════════════════

A, R, N = v2.Veredicto.AVANZA, v2.Veredicto.RECHAZADA, v2.Veredicto.NO_CONCLUYENTE


@pytest.mark.parametrize("sharpe, psr, dsr, esperado", [
    (0.5, 0.90, 0.90, A),
    (2.0, 0.99, 0.99, A),
    (0.2999, 0.99, 0.99, R),
    (-1.0, 1.0, 1.0, R),
    (0.3, 0.99, 0.99, N),
    (0.4999, 0.99, 0.99, N),
    (0.5, 0.8999, 0.99, N),
    (0.5, 0.99, 0.8999, N),
    (3.0, 0.10, 0.10, N),
])
def test_la_tabla_de_la_seccion_10(sharpe, psr, dsr, esperado):
    assert v2.veredicto(sharpe, psr, dsr) == esperado


def test_debajo_de_03_se_rechaza_sin_mirar_psr_ni_dsr():
    """D12: v1 probaba una alternativa; v2 rechaza y se detiene."""
    for psr in (0.0, 0.5, 1.0):
        for dsr in (0.0, 0.5, 1.0):
            assert v2.veredicto(0.29, psr, dsr) == R


def test_lo_intermedio_no_pasa_a_demo():
    """D5: lo que ni avanza ni se rechaza es no concluyente, nunca AVANZA."""
    for sharpe in (0.3, 0.45, 0.5, 1.0):
        for psr, dsr in ((0.89, 0.95), (0.95, 0.89), (0.5, 0.5)):
            assert v2.veredicto(sharpe, psr, dsr) == N


@pytest.mark.parametrize("args", [
    (float("nan"), 0.95, 0.95), (0.6, float("inf"), 0.95), (0.6, 0.95, True), ("0.6", 0.95, 0.95),
])
def test_el_veredicto_no_acepta_basura(args):
    with pytest.raises(ValueError):
        v2.veredicto(*args)


# ═══ La estadística del Sharpe (D5) ═════════════════════════════════════════

def test_los_estimadores_de_la_seccion_9_a_mano():
    e = v2.estadisticos([1.0, 2.0, 3.0, 4.0])
    # media 2,5; desvíos ±1,5 y ±0,5; m2 = 1,25; m3 = 0; m4 = 2,5625
    assert e["t"] == 4
    assert e["sharpe"] == pytest.approx(2.5 / math.sqrt(1.25))
    assert e["asimetria"] == pytest.approx(0.0)
    assert e["curtosis"] == pytest.approx(2.5625 / 1.25 ** 2)
    # ρ = (0,75 − 0,25 + 0,75) / (4 · 1,25)
    assert e["rho"] == pytest.approx(0.25)


def test_una_serie_con_cola_a_la_derecha_tiene_asimetria_positiva():
    """[0, 0, 0, 3]: media 0,75, m2 = 1,6875, m3 = 2,53125, m4 = 6,64453125.
    Asimetría 2/√3, curtosis 7/3 y ρ = −1/12, a mano."""
    e = v2.estadisticos([0.0, 0.0, 0.0, 3.0])
    assert e["asimetria"] == pytest.approx(2 / math.sqrt(3))
    assert e["curtosis"] == pytest.approx(7 / 3)
    assert e["rho"] == pytest.approx(-1 / 12)


def test_rho_de_una_serie_alternada_es_negativo():
    e = v2.estadisticos([1.0, -1.0, 1.0, -1.0])
    assert e["rho"] == pytest.approx(-0.75) and e["curtosis"] == pytest.approx(1.0)


SERIE = [0.012, 0.004, 0.021, -0.008, 0.015, 0.027, -0.019, 0.006, 0.018, 0.011,
         -0.004, 0.023, 0.009, -0.012, 0.016, 0.031, -0.006, 0.002, 0.014, 0.008]


def test_el_psr_de_v2_es_el_del_modulo_con_sigma_en_sr0_y_el_rho_de_la_serie():
    e = v2.estadisticos(SERIE)
    assert v2.psr_cero(SERIE) == pytest.approx(es.psr(
        e["sharpe"], 0.0, t=e["t"], asimetria=e["asimetria"], curtosis=e["curtosis"],
        rho=e["rho"]))
    assert e["rho"] != 0
    assert v2.psr_cero(SERIE) != pytest.approx(es.psr(
        e["sharpe"], 0.0, t=e["t"], asimetria=e["asimetria"], curtosis=e["curtosis"]))


def test_el_psr_de_v2_no_es_el_de_v1():
    """El cambio de D5, a propósito: v1 evaluaba la σ en el Sharpe observado,
    con T−1 y sin ρ (research/preregistro_h1.md §9)."""
    e = v2.estadisticos(SERIE)
    sr, t, g3, g4 = e["sharpe"], e["t"], e["asimetria"], e["curtosis"]
    psr_v1 = NormalDist().cdf(sr * math.sqrt(t - 1) / math.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2))
    assert abs(v2.psr_cero(SERIE) - psr_v1) > 1e-3


def test_el_dsr_y_el_mintrl_son_los_del_modulo():
    e = v2.estadisticos(SERIE)
    k = dict(asimetria=e["asimetria"], curtosis=e["curtosis"], rho=e["rho"])
    assert v2.dsr_serie(SERIE, varianza_ensayos=0.01, n_efectivo=10) == pytest.approx(
        es.dsr(e["sharpe"], t=e["t"], varianza_ensayos=0.01, n_efectivo=10, **k))
    z90 = NormalDist().inv_cdf(0.90)
    assert v2.min_trl_diagnostico(SERIE) == pytest.approx(
        es.min_trl(e["sharpe"], 0.0, z=z90, **k))


@pytest.mark.parametrize("serie", [[], [0.01, 0.02], [0.01] * 10, [0.01, float("nan"), 0.02]])
def test_una_serie_imposible_lanza(serie):
    with pytest.raises(ValueError):
        v2.estadisticos(serie)


@pytest.mark.parametrize("fragmento", [
    "## 0. Qué cambia respecto de v1",
    "**El cambio respecto de v1, a propósito y antes de ver datos.**",
    "con la σ evaluada en el Sharpe **observado** y `T−1`. v2 la evalúa en **SR₀**, con `T` y con ρ",
    "**ρ, estimado de la serie:**",
    "**No es más estricto en todos los casos.**",
    "**No concluyente (D5).** No pasa a demo",
])
def test_el_cambio_de_d5_esta_declarado_antes_de_los_datos(fragmento):
    assert fragmento in _plano(), fragmento


# ═══ D3: el registro de ensayos ═════════════════════════════════════════════

def test_h1_v2_esta_en_el_registro_de_ensayos_con_este_documento():
    from governance import ensayos
    de_h1 = [e for e in ensayos.leer() if e["hipotesis"] == "H1"]
    assert [e["id"] for e in de_h1] == ["h1-v2"]
    e = de_h1[0]
    assert e["estado"] == "pre-registrado" and e["cuenta_en_n"] is True
    assert e["preregistro_sha256"] == SHA256_PREREGISTRO
