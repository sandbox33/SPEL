"""
tests/test_paso_a_real.py
===========================
DG-3 (decision-log 2026-09-29): las cuatro condiciones del paso de demo a
real, la zona gris con su tope, y que los documentos que la citan digan los
mismos números que el módulo.
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
    psr=PSR_MINIMO, dsr=DSR_MINIMO, inicio_demo=INICIO,
    fecha=date(2026, 9, 30),                      # 30 días después
    operaciones_cerradas=OPERACIONES_CERRADAS_MINIMAS_DEMO,
    costo_observado=RAZON_MAXIMA_COSTO_OBSERVADO_MODELADO * 8.0, costo_modelado=8.0,
    discrepancias_reconciliacion=0,
)


def test_el_borde_de_las_cuatro_es_apto():
    assert (_BORDE.fecha - _BORDE.inicio_demo).days == DIAS_MINIMOS_DEMO
    ev = evaluar(_BORDE)
    assert ev.veredicto == Veredicto.APTO
    assert ev.incumplidas == ()


@pytest.mark.parametrize("cambio, letra", [
    ({"psr": 0.8999}, "a"),
    ({"dsr": 0.8999}, "a"),
    ({"fecha": date(2026, 9, 29)}, "b"),
    ({"operaciones_cerradas": OPERACIONES_CERRADAS_MINIMAS_DEMO - 1}, "b"),
    ({"costo_observado": RAZON_MAXIMA_COSTO_OBSERVADO_MODELADO * 8.0 + 0.001}, "c"),
    ({"discrepancias_reconciliacion": 1}, "d"),
])
def test_falta_una_y_es_zona_gris(cambio, letra):
    ev = evaluar(replace(_BORDE, **cambio))
    assert ev.veredicto == Veredicto.ZONA_GRIS
    assert ev.incumplidas == (letra,)


def test_faltan_varias_y_se_listan_todas():
    ev = evaluar(replace(_BORDE, psr=0.5, operaciones_cerradas=0,
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
    sin_cumplir = replace(_BORDE, fecha=vence, psr=0.5)
    assert evaluar(sin_cumplir).veredicto == Veredicto.VENCIDO_PASA_A_H2


def test_despues_del_vencimiento_pasa_a_h2_aunque_cumpla():
    ev = evaluar(replace(_BORDE, fecha=date(2027, 3, 1)))
    assert ev.veredicto == Veredicto.VENCIDO_PASA_A_H2
    assert ev.incumplidas == ()


def test_antes_del_vencimiento_sin_cumplir_sigue_el_forward():
    ev = evaluar(replace(_BORDE, fecha=date(2027, 2, 27), psr=0.5))
    assert ev.veredicto == Veredicto.ZONA_GRIS


@pytest.mark.parametrize("cambio", [
    {"fecha": date(2026, 8, 30)},
    {"costo_modelado": 0.0},
    {"costo_modelado": -1.0},
    {"operaciones_cerradas": -1},
    {"discrepancias_reconciliacion": -1},
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
