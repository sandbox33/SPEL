"""
tests/test_preregistro_h3.py
==============================
El pre-registro H3 se escribe antes de que exista un precio del universo y
no se modifica. Este test hace valer eso:

  · fija el sha256 de research/preregistro_h3.md: cambiarlo exige cambiar
    este test en el mismo PR, a la vista;
  · cada número de core/preregistro_h3.py tiene que estar escrito en el
    documento;
  · el documento no tiene voseo. Lo revisa acá mismo, con las reglas del
    test de `.py` (`formas_voseantes`), porque este PR sale de `main`, donde
    todavía no hay un test de voseo sobre `.md`.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path

import pytest

import core.preregistro_h3 as pre
from tests.test_registro_linguistico import formas_voseantes

RAIZ = Path(__file__).resolve().parent.parent
DOC = RAIZ / "research" / "preregistro_h3.md"

#: Si cambia, el documento cambió. Después de fusionado, eso es un
#: experimento nuevo: preregistro_h3_v2.md, no una edición de este.
SHA256_PREREGISTRO = "71826ed211272a583fb71091e1d120fead7e38bb33146d6008323c39ab1572d3"


def _texto() -> str:
    return DOC.read_text(encoding="utf-8")


def _leer(escrito: str) -> float:
    """Un número como lo escribe el documento: punto de miles, coma
    decimal, y `%` para las fracciones."""
    porcentaje = escrito.endswith("%")
    n = float(escrito.rstrip(" %").replace(".", "").replace(",", "."))
    return n / 100 if porcentaje else n


def test_el_documento_no_cambio():
    assert hashlib.sha256(DOC.read_bytes()).hexdigest() == SHA256_PREREGISTRO


def test_el_documento_no_usa_voseo():
    assert formas_voseantes(_texto()) == []


@pytest.mark.parametrize("valor, fragmento, escrito", [
    (pre.HISTORIA_POSTERIOR_AL_CALENTAMIENTO_BARRAS, "**historia_usable ≥ lookback_max + 756**", "756"),
    (pre.FRACCION_VOL_CARTERA, "**target de volatilidad de cartera de 0,25**", "0,25"),
    (pre.VENTANA_VOL_CARTERA_DIAS, "últimos **60** días de retornos", "60"),
    (pre.APALANCAMIENTO_MAXIMO_POR_ACTIVO, "**tope de 2× por activo**", "2"),
    (pre.VENTANA_VOL_BARRAS, "**20** barras anteriores a la entrada", "20"),
    (pre.FRACCION_MAXIMA_SALTADA, "**más del 50 %**", "50 %"),
    (pre.RAZON_STOPOUT_SALIDA, "al menos 1,5 veces la distancia a su canal de salida", "1,5"),
    (pre.CAPITAL_REFERENCIA_USD, "**100 USD** (DG-2)", "100"),
    (pre.CAPITAL_DIAGNOSTICO_USD, "**Reporte obligatorio a 1.500 USD**", "1.500"),
    (pre.COMISION_KID_CRIPTO_FRACCION, "tasa de **0,001** sobre el nocional", "0,001"),
    (pre.COMISION_KID_FOREX_EURUSD_FRACCION, "**0,000199**", "0,000199"),
    (pre.COMISION_PISO_USD, "**Piso de 0,10 USD**", "0,10"),
    (pre.SHARPE_MINIMO_AVANZAR, "Sharpe neto **≥ 0,5**", "0,5"),
    (pre.PSR_MINIMO, "**PSR(0) ≥ 0,90**", "0,90"),
    (pre.DSR_MINIMO, "**DSR ≥ 0,90**", "0,90"),
    (pre.SHARPE_DETENER, "Sharpe neto **< 0,3**", "0,3"),
    (pre.DIAS_MINIMOS_DEMO, "**≥ 30 días** de demo", "30"),
    (pre.OPERACIONES_CERRADAS_MINIMAS_DEMO, "**≥ 20 operaciones cerradas**", "20"),
    (pre.RAZON_MAXIMA_COSTO_OBSERVADO_MODELADO, "**≤ 1,25 ×**", "1,25"),
    (pre.MESES_MAXIMOS_ZONA_GRIS, "**máximo de 6 meses**", "6"),
    (pre.ENSAYOS_PREVIOS_FALLIDOS, "más los **3** ensayos previos", "3"),
    (pre.N_INGENUO, "**N = 11**", "11"),
])
def test_cada_numero_del_modulo_esta_escrito_en_el_documento(valor, fragmento, escrito):
    """El fragmento está en el documento, contiene el número como está
    escrito, y ese número es el del módulo. Cambiar la constante sin el
    documento, o el documento sin la constante, falla acá."""
    assert fragmento in _texto(), fragmento
    assert escrito in fragmento
    assert _leer(escrito) == pytest.approx(valor), (escrito, valor)


def test_una_sola_variante_de_h3():
    assert pre.VARIANTES_H3 == 1
    assert "Las variantes de H3: **una**" in _texto()


def test_la_rejilla_es_la_del_documento():
    assert "{" + ", ".join(map(str, pre.REJILLA_H3)) + "}" in _texto()


def test_la_rejilla_minima_da_la_condicion_del_universo():
    """La interpretación de la sección 2: un activo entra si soporta la
    rejilla mínima, 10 + 756."""
    assert pre.historia_requerida(min(pre.REJILLA_H3)) == 766
    assert "`historia_usable ≥ 10 + 756`" in _texto()
    assert pre.historia_requerida(320) == 1076 and "1.076 barras" in _texto()


@pytest.mark.parametrize("historia, rejilla", [
    (1076, [10, 20, 40, 80, 160, 320]),
    (1075, [10, 20, 40, 80, 160]),
    (766, [10]),
    (765, []),
    (0, []),
])
def test_la_rejilla_se_recorta_desde_arriba_por_activo(historia, rejilla):
    assert pre.rejilla_soportada(historia) == rejilla


@pytest.mark.parametrize("largos, votantes, largo", [
    (3, 6, True), (2, 6, False), (6, 6, True),
    (3, 5, True), (2, 5, False),
    (1, 1, True), (0, 1, False),
    (0, 0, False),
])
def test_el_voto_es_al_menos_la_mitad(largos, votantes, largo):
    """DG-4: 'largo si al menos la mitad de los lookbacks soportados están
    largos'. Con 5, la mitad es 2,5: hacen falta 3."""
    assert pre.largo_por_voto(largos, votantes) is largo


@pytest.mark.parametrize("fraccion, ejecutable", [(0.0, True), (0.5, True), (0.5001, False), (1.0, False)])
def test_un_lookback_que_salta_mas_de_la_mitad_no_vota(fraccion, ejecutable):
    assert pre.lookback_ejecutable(fraccion) is ejecutable


@pytest.mark.parametrize("k, fraccion", [(1, 0.25), (4, 0.125), (25, 0.05)])
def test_el_reparto_del_target_es_025_sobre_raiz_de_k(k, fraccion):
    assert pre.fraccion_vol_por_activo(k) == pytest.approx(fraccion)
    assert "`0,25 / √K`" in _texto()


@pytest.mark.parametrize("sigma, s", [
    (0.10, 1.0), (0.25, 1.0), (0.50, 0.5), (1.0, 0.25), (0.0, 1.0),
])
def test_el_escalador_achica_y_nunca_agranda(sigma, s):
    assert pre.escala_cartera(sigma) == pytest.approx(s)
    assert "s = min(1 ; 0,25 / σ̂_cartera)" in _texto()


def test_el_escalador_no_admite_sigma_negativa():
    with pytest.raises(ValueError):
        pre.escala_cartera(-0.1)


def test_el_nocional_es_la_formula_del_documento():
    # 100 × (0,25 / √4) × 0,5 / 0,5 = 12,5
    assert pre.nocional_activo(100.0, 4, 0.5, 0.5) == pytest.approx(12.5)
    assert "capital × (0,25 / √K) × s / vol_realizada_20d" in _texto()


def test_el_nocional_se_topa_en_2x_el_capital():
    # 100 × 0,25 × 1 / 0,01 = 2.500 sin tope
    assert pre.nocional_activo(100.0, 1, 1.0, 0.01) == pytest.approx(200.0)


@pytest.mark.parametrize("s, vol", [(0.0, 0.5), (1.01, 0.5), (0.5, 0.0), (0.5, -1.0)])
def test_el_nocional_rechaza_s_o_vol_imposibles(s, vol):
    with pytest.raises(ValueError):
        pre.nocional_activo(100.0, 4, s, vol)


def test_el_benchmark_usa_el_mismo_sizing():
    texto = " ".join(_texto().split())
    i = texto.index("## 10. Benchmark")
    assert "`0,25 / √K` con el escalador `s`" in texto[i:i + 400]


def test_el_reparto_no_admite_k_cero():
    with pytest.raises(ValueError):
        pre.fraccion_vol_por_activo(0)


def test_las_reglas_dependen_solo_de_longitudes_conteos_k_y_volatilidades():
    """Ninguna recibe precios ni retornos: la forma más simple de cumplir
    'por reglas, nunca por precios'. El sizing recibe volatilidades ya
    medidas, no la serie de la que salen."""
    firmas = {f.__name__: list(inspect.signature(f).parameters) for f in (
        pre.rejilla_soportada, pre.largo_por_voto, pre.lookback_ejecutable,
        pre.fraccion_vol_por_activo, pre.historia_requerida,
        pre.escala_cartera, pre.nocional_activo)}
    assert firmas == {
        "rejilla_soportada": ["historia_usable"],
        "largo_por_voto": ["largos", "votantes"],
        "lookback_ejecutable": ["fraccion_saltada"],
        "fraccion_vol_por_activo": ["k_activos"],
        "historia_requerida": ["lookback_max"],
        "escala_cartera": ["sigma_cartera"],
        "nocional_activo": ["capital", "k_activos", "s", "vol_activo"],
    }


def test_el_modulo_no_importa_nada_que_haga_un_backtest():
    arbol = ast.parse((RAIZ / "core" / "preregistro_h3.py").read_text(encoding="utf-8"))
    importados = {a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names}
    importados |= {n.module for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom)}
    assert importados <= {"__future__", "math"}, importados
