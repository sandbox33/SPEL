"""
tests/test_paso_a_real.py
===========================
DG-3 (decision-log 2026-09-29, con D2 del 14-oct-2026): las cuatro
condiciones del paso de demo a real, la zona gris con su tope, y que los
documentos que la citan digan los mismos números que el módulo.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from governance.paso_a_real import (
    DIAS_MINIMOS_DEMO,
    DSR_MINIMO,
    MESES_MAXIMOS_ZONA_GRIS,
    OPERACIONES_CERRADAS_MINIMAS_DEMO,
    PSR_MINIMO,
    RAZON_MAXIMA_COSTO_OBSERVADO_MODELADO,
    EvidenciaDemo,
    Veredicto,
    evaluar,
    sumar_meses,
)

RAIZ = Path(__file__).resolve().parent.parent

INICIO = date(2026, 8, 31)

#: Cumple las cuatro, justo en el borde de cada una.
_BORDE = EvidenciaDemo(
    psr_historico=PSR_MINIMO, dsr_historico=DSR_MINIMO, inicio_demo=INICIO,
    fecha=date(2026, 9, 30),                      # 30 días después
    operaciones_cerradas=OPERACIONES_CERRADAS_MINIMAS_DEMO,
    costo_observado=RAZON_MAXIMA_COSTO_OBSERVADO_MODELADO * 8.0, costo_modelado=8.0,
    discrepancias_reconciliacion=0, no_verificables_reconciliacion=0, sin_reconciliar=0,
)


def test_el_borde_de_las_cuatro_es_apto():
    assert (_BORDE.fecha - _BORDE.inicio_demo).days == DIAS_MINIMOS_DEMO
    ev = evaluar(_BORDE)
    assert ev.veredicto == Veredicto.APTO
    assert ev.incumplidas == ()


@pytest.mark.parametrize("cambio, letra", [
    ({"psr_historico": 0.8999}, "a"),
    ({"dsr_historico": 0.8999}, "a"),
    ({"fecha": date(2026, 9, 29)}, "b"),
    ({"operaciones_cerradas": OPERACIONES_CERRADAS_MINIMAS_DEMO - 1}, "b"),
    ({"costo_observado": RAZON_MAXIMA_COSTO_OBSERVADO_MODELADO * 8.0 + 0.001}, "c"),
    ({"discrepancias_reconciliacion": 1}, "d"),
    ({"no_verificables_reconciliacion": 1}, "d"),
    ({"sin_reconciliar": 1}, "d"),
])
def test_falta_una_y_es_zona_gris(cambio, letra):
    ev = evaluar(replace(_BORDE, **cambio))
    assert ev.veredicto == Veredicto.ZONA_GRIS
    assert ev.incumplidas == (letra,)


def test_faltan_varias_y_se_listan_todas():
    ev = evaluar(replace(_BORDE, psr_historico=0.5, operaciones_cerradas=0,
                         discrepancias_reconciliacion=3))
    assert ev.incumplidas == ("a", "b", "d")


def test_el_tope_son_seis_meses_de_calendario_desde_el_inicio():
    assert evaluar(_BORDE).vence_el == date(2027, 2, 28), "31-ago + 6 meses: fin de febrero"
    assert sumar_meses(date(2026, 1, 15), MESES_MAXIMOS_ZONA_GRIS) == date(2026, 7, 15)
    assert sumar_meses(date(2026, 10, 31), 4) == date(2027, 2, 28)
    assert sumar_meses(date(2027, 10, 31), 4) == date(2028, 2, 29)
    assert sumar_meses(date(2026, 12, 1), 1) == date(2027, 1, 1)


def test_el_dia_del_vencimiento_todavia_se_puede_cumplir():
    vence = date(2027, 2, 28)
    assert evaluar(replace(_BORDE, fecha=vence)).veredicto == Veredicto.APTO
    sin_cumplir = replace(_BORDE, fecha=vence, psr_historico=0.5)
    assert evaluar(sin_cumplir).veredicto == Veredicto.VENCIDO_PASA_A_H2


def test_despues_del_vencimiento_pasa_a_h2_aunque_cumpla():
    ev = evaluar(replace(_BORDE, fecha=date(2027, 3, 1)))
    assert ev.veredicto == Veredicto.VENCIDO_PASA_A_H2
    assert ev.incumplidas == ()


def test_antes_del_vencimiento_sin_cumplir_sigue_el_forward():
    ev = evaluar(replace(_BORDE, fecha=date(2027, 2, 27), psr_historico=0.5))
    assert ev.veredicto == Veredicto.ZONA_GRIS


@pytest.mark.parametrize("cambio", [
    {"fecha": date(2026, 8, 30)},
    {"costo_modelado": 0.0},
    {"costo_modelado": -1.0},
    {"operaciones_cerradas": -1},
    {"discrepancias_reconciliacion": -1},
    {"no_verificables_reconciliacion": -1},
    {"sin_reconciliar": -1},
])
def test_evidencia_imposible_lanza(cambio):
    with pytest.raises(ValueError):
        evaluar(replace(_BORDE, **cambio))


# ═══ Los documentos dicen lo mismo que el módulo ═══════════════════════════

def _numero(texto: str) -> float:
    return float(texto.replace(",", "."))


def _cifras(texto: str) -> dict[str, float]:
    plano = " ".join(texto.split())
    patrones = {
        "psr": r"PSR (?:y DSR )?≥ (\d+,\d+)",
        "dsr": r"DSR ≥ (\d+,\d+)",
        "dias": r"≥ (\d+) días",
        "operaciones": r"≥ (\d+) operaciones cerradas",
        "costos": r"≤ (\d+,\d+) ×",
        "meses": r"máximo (?:de )?(\d+) meses",
    }
    out = {}
    for clave, patron in patrones.items():
        m = re.search(patron, plano)
        assert m, f"no se encontró {clave!r} ({patron})"
        out[clave] = _numero(m.group(1))
    return out


_DEL_MODULO = {
    "psr": PSR_MINIMO, "dsr": DSR_MINIMO, "dias": DIAS_MINIMOS_DEMO,
    "operaciones": OPERACIONES_CERRADAS_MINIMAS_DEMO,
    "costos": RAZON_MAXIMA_COSTO_OBSERVADO_MODELADO, "meses": MESES_MAXIMOS_ZONA_GRIS,
}


def _principio_6() -> str:
    texto = (RAIZ / "governance" / "PRINCIPLES.md").read_text(encoding="utf-8")
    return texto[texto.index("\n6. "):texto.index("\n7. ")]


def test_principles_6_dice_los_numeros_del_modulo():
    assert _cifras(_principio_6()) == _DEL_MODULO


def test_principles_6_conserva_la_clausula_de_no_negociar():
    plano = " ".join(_principio_6().split())
    assert "se escribe acá — no se negocia por sesión bajo presión de tiempo" in plano


@pytest.mark.parametrize("archivo", ["CLAUDE.md", "BLUEPRINT.md"])
def test_los_documentos_que_citan_dg3_dicen_los_mismos_numeros(archivo):
    texto = (RAIZ / archivo).read_text(encoding="utf-8")
    i = texto.index("DG-3")
    tramo = texto[i - 200:i + 600]
    assert _cifras(tramo) == _DEL_MODULO
    assert "6 meses de forward en demo" not in " ".join(tramo.split())


# ═══ D2 (14-oct-2026): a) solo histórico, d) sin no_verificables, latencia ═══

def test_a_se_mide_solo_sobre_el_historico():
    """El criterio viejo (la serie combinada histórico + demo) se llamaba
    `psr`/`dsr`. Los campos nuevos dicen sobre qué se miden, y los viejos ya
    no existen: un llamador que pase la serie combinada no compila en
    silencio."""
    import dataclasses
    campos = {f.name for f in dataclasses.fields(EvidenciaDemo)}
    assert {"psr_historico", "dsr_historico"} <= campos
    assert not {"psr", "dsr"} & campos
    with pytest.raises(TypeError):
        EvidenciaDemo(psr=0.95, dsr=0.95, inicio_demo=INICIO, fecha=INICIO,
                      operaciones_cerradas=0, costo_observado=1.0, costo_modelado=1.0,
                      discrepancias_reconciliacion=0, no_verificables_reconciliacion=0,
                      sin_reconciliar=0)


def test_un_no_verificable_basta_para_incumplir_d():
    ev = evaluar(replace(_BORDE, no_verificables_reconciliacion=1))
    assert ev.incumplidas == ("d",) and ev.veredicto == Veredicto.ZONA_GRIS


def test_la_latencia_no_se_evalua_y_el_veredicto_lo_dice():
    for evidencia in (_BORDE, replace(_BORDE, psr_historico=0.5)):
        assert evaluar(evidencia).sin_evaluar == ("latencia",)
    assert evaluar(_BORDE).veredicto == Veredicto.APTO


def _registro_demo(tmp_path):
    """Cinco trades: A ok; B discrepancia y después ok (cuenta la última);
    C no_verificable; D cerrado sin reconciliar; E abierto."""
    from integracion_demo.registro import Registro
    r = Registro(tmp_path)

    def cerrar(u):
        r.agregar(evento="cierre", trade_uuid=u, contract_id=1, outcome="sl", evidencia="x")

    def marcar(u, global_, profit, comision):
        r.agregar(evento="reconciliacion", trade_uuid=u, reconciliado=global_,
                  reconciliado_profit=profit, reconciliado_comision=comision, evidencia="{}")
    for u in "ABCD":
        cerrar(u)
    marcar("A", "ok", "ok", "ok")
    marcar("B", "discrepancia", "discrepancia", "ok")
    marcar("B", "ok", "ok", "ok")
    marcar("C", "no_verificable", "ok", "no_verificable")
    r.agregar(evento="compra", trade_uuid="E", contract_id=2)
    return tmp_path


def test_d_cuenta_desde_la_ultima_marca_global_del_registro(tmp_path):
    from integracion_demo.reconciliar import conteo_de_marcas
    conteo = conteo_de_marcas(_registro_demo(tmp_path))
    assert conteo == {"ok": 2, "discrepancia": 0, "no_verificable": 1, "sin_reconciliar": 1}
    ev = evaluar(replace(_BORDE, discrepancias_reconciliacion=conteo["discrepancia"],
                         no_verificables_reconciliacion=conteo["no_verificable"],
                         sin_reconciliar=conteo["sin_reconciliar"]))
    assert ev.incumplidas == ("d",)
    assert ev.incumplidas_d == ("no_verificables_reconciliacion", "sin_reconciliar")


def test_una_discrepancia_vigente_cuenta(tmp_path):
    from integracion_demo.reconciliar import conteo_de_marcas
    from integracion_demo.registro import Registro
    d = _registro_demo(tmp_path)
    Registro(d).agregar(evento="reconciliacion", trade_uuid="A", reconciliado="discrepancia",
                        reconciliado_profit="discrepancia", reconciliado_comision="ok",
                        evidencia="{}")
    assert conteo_de_marcas(d)["discrepancia"] == 1 and conteo_de_marcas(d)["ok"] == 1


@pytest.mark.parametrize("archivo, recorte", [
    ("governance/PRINCIPLES.md", None), ("CLAUDE.md", "DG-3"), ("BLUEPRINT.md", "DG-3")])
def test_los_documentos_dicen_la_dg3_de_d2(archivo, recorte):
    texto = (RAIZ / archivo).read_text(encoding="utf-8")
    if recorte is None:
        tramo = _principio_6()
    else:
        i = texto.index(recorte)
        tramo = texto[i - 200:i + 600]
    plano = " ".join(tramo.split())
    assert "histórico + demo" not in plano and "serie combinada" not in plano
    assert "backtest histórico" in plano
    assert "no_verificables" in plano
    assert "latencia" in plano


# ═══ DB-3 (17-oct-2026): un trade terminado sin reconciliar incumple d) ═══

def test_un_trade_sin_reconciliar_basta_para_incumplir_d(tmp_path):
    """El caso que separa DB-3 de D2: el único trade está cerrado y nadie lo
    reconcilió. No hay discrepancias ni no_verificables, y con la d) de D2 el
    veredicto era APTO."""
    from integracion_demo.reconciliar import conteo_de_marcas
    from integracion_demo.registro import Registro
    Registro(tmp_path).agregar(evento="cierre", trade_uuid="A", contract_id=1, outcome="sl",
                               evidencia="x")
    conteo = conteo_de_marcas(tmp_path)
    assert conteo == {"ok": 0, "discrepancia": 0, "no_verificable": 0, "sin_reconciliar": 1}
    ev = evaluar(replace(_BORDE, discrepancias_reconciliacion=conteo["discrepancia"],
                         no_verificables_reconciliacion=conteo["no_verificable"],
                         sin_reconciliar=conteo["sin_reconciliar"]))
    assert ev.veredicto == Veredicto.ZONA_GRIS
    assert ev.incumplidas == ("d",)
    assert ev.incumplidas_d == ("sin_reconciliar",)


@pytest.mark.parametrize("campo", [
    "discrepancias_reconciliacion", "no_verificables_reconciliacion", "sin_reconciliar"])
def test_el_veredicto_dice_cual_parte_de_d_falta(campo):
    """Los sin reconciliar no se suman a los no_verificables: cada conteo se
    nombra solo."""
    ev = evaluar(replace(_BORDE, **{campo: 2}))
    assert ev.incumplidas == ("d",)
    assert ev.incumplidas_d == (campo,)


def test_las_tres_partes_de_d_se_listan_en_orden():
    ev = evaluar(replace(_BORDE, discrepancias_reconciliacion=1,
                         no_verificables_reconciliacion=1, sin_reconciliar=1))
    assert ev.incumplidas_d == ("discrepancias_reconciliacion",
                                "no_verificables_reconciliacion", "sin_reconciliar")


def test_si_d_se_cumple_no_falta_ninguna_parte_de_d():
    for evidencia in (_BORDE, replace(_BORDE, psr_historico=0.5, operaciones_cerradas=0)):
        ev = evaluar(evidencia)
        assert "d" not in ev.incumplidas and ev.incumplidas_d == ()


def test_sin_reconciliar_es_obligatorio():
    """Sin valor por defecto: una evidencia que no lo trae no se arma, en vez
    de cumplir d) por omisión."""
    import dataclasses
    campos = {f.name: f for f in dataclasses.fields(EvidenciaDemo)}
    assert campos["sin_reconciliar"].default is dataclasses.MISSING
    with pytest.raises(TypeError):
        EvidenciaDemo(psr_historico=0.95, dsr_historico=0.95, inicio_demo=INICIO,
                      fecha=INICIO, operaciones_cerradas=0, costo_observado=1.0,
                      costo_modelado=1.0, discrepancias_reconciliacion=0,
                      no_verificables_reconciliacion=0)


def _tramo_dg3(archivo: str) -> str:
    if archivo == "governance/PRINCIPLES.md":
        return _principio_6()
    texto = (RAIZ / archivo).read_text(encoding="utf-8")
    i = texto.index("DG-3")
    return texto[i - 200:i + 600]


@pytest.mark.parametrize("archivo", ["governance/PRINCIPLES.md", "CLAUDE.md", "BLUEPRINT.md"])
def test_los_documentos_dicen_la_d_de_db3(archivo):
    assert "trades terminados sin reconciliar" in " ".join(_tramo_dg3(archivo).split())
