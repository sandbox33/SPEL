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
SHA256_PREREGISTRO = "53a1d5a0d5a2b59af3a0c3277ef821a17d4918aef1e504678d27f9c2fceed9d8"


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
    """El registro de ensayos no hace backtest: es la única fuente del N
    (D3, P3), de donde salen los previos."""
    arbol = ast.parse((RAIZ / "core" / "preregistro_h3.py").read_text(encoding="utf-8"))
    importados = {a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names}
    importados |= {n.module for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom)}
    assert importados <= {"__future__", "math", "governance.ensayos"}, importados


# ═══ Brief 11–13, tarea 12: D7, D2 y el sizing ═════════════════════════════

def _plano() -> str:
    return " ".join(_texto().split())


def _seccion(titulo: str) -> str:
    texto = _plano()
    i = texto.index(titulo)
    j = texto.find(" ## ", i + len(titulo))
    return texto[i:j if j != -1 else len(texto)]


@pytest.mark.parametrize("fragmento", [
    "### Precios que ya están en `data` (D7)",
    "`metrics/velas/frxXAUUSD/300.jsonl` | M5 de Deriv: 70.718 velas",
    "`metrics/velas/td_XAUUSD/300.jsonl` | M5 de TwelveData: 499.252 velas",
    "`metrics/velas/frxXAUUSD/revisiones_300.jsonl`",
    "`metrics/calendario/frxXAUUSD/trading_times.jsonl`",
    "el 08-oct (`852e5ce`, `54f63fc`, `db491b4`)",
    "| el 14-oct (`bc2597a`) |",
    "**Ninguna se miró con reglas de H3.**",
    "`previo-ema-20-63`, fallido",
])
def test_d7_declara_los_precios_del_oro_que_ya_estan_en_data(fragmento):
    assert fragmento in _plano(), fragmento


def test_el_paso_a_real_es_el_de_d2_y_db3():
    """Antes, a) se medía en la serie combinada histórico + demo y d) pedía
    solo cero discrepancias. Con D2 y DB-3, a) es solo el histórico y d)
    suma los no_verificables y los trades sin reconciliar."""
    s12 = _seccion("## 12. Compuertas")
    assert "serie combinada" not in s12 and "histórico + demo" not in s12
    assert "solo en el backtest histórico" in s12
    assert "sin discrepancias, no_verificables ni trades terminados sin reconciliar" in s12
    assert "La latencia que nombra D2 no tiene umbral todavía" in s12


def test_las_cifras_de_dg3_son_las_de_governance():
    """El documento duplica a propósito las cifras de DG-3; si DG-3 cambia en
    governance/paso_a_real.py, este test obliga a revisar H3."""
    from governance import paso_a_real as dg3
    assert (pre.DIAS_MINIMOS_DEMO, pre.OPERACIONES_CERRADAS_MINIMAS_DEMO,
            pre.RAZON_MAXIMA_COSTO_OBSERVADO_MODELADO, pre.MESES_MAXIMOS_ZONA_GRIS) == (
        dg3.DIAS_MINIMOS_DEMO, dg3.OPERACIONES_CERRADAS_MINIMAS_DEMO,
        dg3.RAZON_MAXIMA_COSTO_OBSERVADO_MODELADO, dg3.MESES_MAXIMOS_ZONA_GRIS)


def test_el_sizing_se_justifica_frente_a_la_version_sin_escalar():
    s6 = _seccion("## 6. Sizing")
    assert "**Por qué por volatilidad, y no sin escalar.**" in s6
    assert "La versión sin escalar **no se corre**" in s6


def test_h3_esta_en_el_registro_de_ensayos_con_este_documento():
    """D3: una sola variante, pre-registrada y no corrida, que apunta al
    sha256 de este documento."""
    from governance import ensayos
    de_h3 = [e for e in ensayos.leer() if e["hipotesis"] == "H3"]
    assert [e["id"] for e in de_h3] == ["h3-cartera"] and len(de_h3) == pre.VARIANTES_H3
    e = de_h3[0]
    assert e["estado"] == "pre-registrado" and e["cuenta_en_n"] is True
    assert e["preregistro_sha256"] == SHA256_PREREGISTRO


# ═══ Brief 11–13-B (17-oct-2026): P1 a P5 ═════════════════════════════════

def test_no_queda_ninguna_marca_de_pendiente_del_admin():
    """Eran tres: la historia del universo, el N y la estadística del Sharpe."""
    assert "PENDIENTE DEL ADMIN" not in _texto()


def test_p1_la_fuente_de_precios():
    s2 = _seccion("## 2. Universo")
    assert ("**Los precios (P1, auditor, 17-oct-2026):** las velas diarias de D1 (TwelveData) "
            "de cada activo, y solo después de que el brief 14 las valide con su compuerta.") in s2
    assert ("H3 no corre sobre ese activo: no hay fuente de reemplazo sin validar, y la "
            "compuerta no se relaja.") in s2


def test_p2_h3_queda_bloqueada_y_no_se_reduce_a_k_2():
    """Antes, la sección 2 dejaba tres salidas sin elegir, una de ellas K = 2.
    P2 conserva la regla de universo y bloquea H3."""
    s2 = _seccion("## 2. Universo")
    assert "**H3 queda bloqueada (P2, auditor, 17-oct-2026)**" in s2
    assert "**H3 no se reduce a K = 2**" in s2 and "H1 contado dos veces" in s2
    assert "Esa extensión es un brief propio, que se decide si H1 v2 o H2 muestran algo." in s2
    assert "limitar H3 a los activos con historia de D1" not in _plano()
    assert "Entra **todo instrumento no sintético con MULTUP según la sonda de instrumentos**" in s2


def test_p3_el_n_son_las_estrategias_del_registro_y_h2_no_suma():
    from governance import ensayos
    s11 = _seccion("## 11. Número de ensayos para el DSR")
    assert "`governance/ensayos.py::que_cuentan_en_el_dsr()`" in s11
    assert "**Los de H2 no suman.**" in s11
    del_dsr = {e["id"]: e for e in ensayos.que_cuentan_en_el_dsr()}
    assert {"previo-mascara-godel", "previo-proxy-transfer-entropy", "previo-ema-20-63",
            "h3-cartera"} <= set(del_dsr)
    assert all(e["hipotesis"] != "H2" for e in del_dsr.values())


def test_los_previos_salen_del_registro_y_no_son_un_literal():
    """Como en H1 (D3): el 3 ya no se escribe a mano en el módulo."""
    from governance.ensayos import previos_fallidos
    assert pre.ENSAYOS_PREVIOS_FALLIDOS == previos_fallidos() == 3
    arbol = ast.parse((RAIZ / "core" / "preregistro_h3.py").read_text(encoding="utf-8"))
    asignacion = next(n for n in arbol.body if isinstance(n, ast.Assign)
                      and [getattr(t, "id", None) for t in n.targets] == ["ENSAYOS_PREVIOS_FALLIDOS"])
    assert isinstance(asignacion.value, ast.Call)
    assert getattr(asignacion.value.func, "id", None) == "previos_fallidos"


def test_p4_la_estadistica_es_la_de_h1_v2_y_el_cambio_esta_declarado():
    """Antes: el PSR de 2012, con la σ en el Sharpe observado y T−1. Ahora:
    core/estadistica_sharpe.py, con la σ en SR₀, T y ρ."""
    from core import estadistica_sharpe as es
    s11 = _seccion("## 11. Número de ensayos para el DSR")
    assert "**Convenciones**, las mismas de H1:" not in s11
    assert "**La estadística del Sharpe (P4)** es la de H1 v2 (D5): `core/estadistica_sharpe.py`" in s11
    assert "**El cambio, a propósito y antes de ver datos (P4).**" in s11
    assert "con la σ evaluada en el Sharpe **observado** y `T−1`. Ahora la σ se evalúa en **SR₀**" in s11
    assert "No es más estricto en todos los casos" in s11
    for llamada, funcion in (("`psr(SR, 0, t=T, asimetria=γ₃, curtosis=γ₄, rho=ρ)`", es.psr),
                             ("`dsr(SR, t=T, asimetria=γ₃, curtosis=γ₄, varianza_ensayos=V, "
                              "n_efectivo=N, rho=ρ)`", es.dsr)):
        assert llamada in s11, llamada
        nombres = [a.split("=")[0] for a in llamada.strip("`").split("(", 1)[1].rstrip(")").split(", ")
                   if "=" in a]
        assert set(nombres) <= set(inspect.signature(funcion).parameters), nombres


def test_p5_la_zona_gris_no_es_concluyente_y_no_va_a_demo():
    """Antes, la zona gris iba a forward en demo. Con P5 no va: con D2, la
    demo no cambia a), así que no la puede rescatar."""
    s12 = _seccion("## 12. Compuertas")
    assert "**No concluyente: no va a demo** (P5, auditor, 17-oct-2026)" in s12
    assert "También va a forward en demo" not in s12
    assert "**Con D2 no tiene camino a real:**" not in s12


def test_h3_en_el_registro_queda_bloqueada_y_sin_pendiente():
    from governance import ensayos
    e = next(e for e in ensayos.leer() if e["id"] == "h3-cartera")
    assert "pendiente del Admin" not in e["activo"]
    assert "bloqueada hasta que haya historia validada para el universo (P2)" in e["nota"]
