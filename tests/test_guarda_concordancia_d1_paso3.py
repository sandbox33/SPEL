"""
tests/test_guarda_concordancia_d1_paso3.py
============================================
La guarda AST de la concordancia diaria (tests/test_guarda_concordancia_d1.py),
para el paso 3 (brief del Admin 16a), que vive en su propio módulo. Se
importan las funciones de las guardas, no se copian.

  a) Ningún identificador ni string de código nombra un desenlace (el
     VOCABULARIO de siempre).
  b) Los campos de precio solo aparecen en los LECTORES (`rangos` y
     `rangos_de_filas`), y son el máximo y el mínimo: el cierre llega del
     paso 2, ya leído por tools/concordancia_d1.py.
  c) Nada acumula el movimiento de un día con el del siguiente (los
     ACUMULADORES de la guarda de D1).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_guarda_concordancia_d1 import _campos_vistos, acumuladores
from tests.test_guarda_desenlaces import campos_fuera_de_los_lectores, vocabulario_prohibido

RAIZ = Path(__file__).resolve().parent.parent
PASO_3 = "tools/concordancia_d1_paso3.py"
LECTORES_PASO_3 = frozenset({"rangos", "rangos_de_filas"})


def _fuente() -> str:
    return (RAIZ / PASO_3).read_text(encoding="utf-8")


def test_el_paso_3_no_nombra_un_desenlace():
    assert vocabulario_prohibido(_fuente()) == []


def test_los_precios_solo_se_leen_en_los_lectores_del_paso_3():
    assert campos_fuera_de_los_lectores(_fuente(), LECTORES_PASO_3) == []


def test_los_lectores_del_paso_3_leen_el_maximo_y_el_minimo_y_nada_mas():
    """Si la guarda dejara de ver los lectores, o si leyeran otro campo,
    este test lo dice."""
    assert _campos_vistos(_fuente()) == {"rangos": {"high", "low"},
                                         "rangos_de_filas": {"high", "low"}}


def test_nada_acumula_en_el_paso_3():
    assert acumuladores(_fuente()) == []


@pytest.mark.parametrize("fuente, cuenta", [
    ("def rangos(df):\n    return df['high'], df['low']\n", 0),
    ("def paso_3_rango(v):\n    return v['high']\n", 1),
    ("def rupturas(v):\n    return v['close']\n", 1),
])
def test_la_guarda_ve_lo_que_tiene_que_ver(fuente, cuenta):
    assert len(campos_fuera_de_los_lectores(fuente, LECTORES_PASO_3)) == cuenta
