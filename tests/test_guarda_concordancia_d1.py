"""
tests/test_guarda_concordancia_d1.py
======================================
La guarda AST de tools/concordancia_xauusd.py (tests/test_guarda_desenlaces.py),
portada a la concordancia diaria de D1 (brief del Admin 14, 14b, punto 5):
ningún desenlace de trading entra. Se importan las funciones de esa guarda,
no se copian.

  a) Ningún identificador ni string de código de los módulos de D1 nombra un
     desenlace (el mismo VOCABULARIO). Los docstrings y los comentarios no
     cuentan.
  b) En tools/concordancia_d1.py, los campos de precio de una vela solo
     aparecen en los LECTORES (`cierres` y `cierres_revisados`), y el único
     que aparece es "close".
  c) En el ORB, los lectores paran en la vela de ruptura. En D1 no hay
     ruptura: lo que corresponde es que nada acumule el movimiento de un día
     con el del siguiente. Ningún nombre de ACUMULADORES aparece en el módulo.

[INTERPRETACIÓN] Como en el ORB, la ingesta (ingestion/velas_d1.py) lee
todas las velas para guardarlas: para ella vale (a), no (b) ni (c).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.test_guarda_desenlaces import (
    CAMPOS_DE_PRECIO,
    _docstrings,
    _funcion_de,
    _nombres,
    campos_fuera_de_los_lectores,
    vocabulario_prohibido,
)

RAIZ = Path(__file__).resolve().parent.parent

MODULOS_D1: tuple[str, ...] = ("tools/concordancia_d1.py", "ingestion/velas_d1.py")
D1 = "tools/concordancia_d1.py"
LECTORES_D1 = frozenset({"cierres", "cierres_revisados"})
ACUMULADORES = frozenset({"cumsum", "cumprod", "prod", "accumulate", "nancumsum", "nancumprod"})


def acumuladores(fuente: str) -> list[str]:
    return [f"línea {l}: {t!r}" for l, t in _nombres(ast.parse(fuente)) if t in ACUMULADORES]


def _fuente(rel: str) -> str:
    return (RAIZ / rel).read_text(encoding="utf-8")


def _campos_vistos(fuente: str) -> dict[str, set[str]]:
    """función -> campos de precio que nombra, fuera de los docstrings."""
    arbol = ast.parse(fuente)
    docs, donde = _docstrings(arbol), _funcion_de(arbol)
    out: dict[str, set[str]] = {}
    for n in ast.walk(arbol):
        if isinstance(n, ast.Constant) and n.value in CAMPOS_DE_PRECIO and id(n) not in docs:
            out.setdefault(donde.get(id(n), "<módulo>"), set()).add(n.value)
    return out


# ═══ Sobre el código real ═════════════════════════════════════════════════

@pytest.mark.parametrize("rel", MODULOS_D1)
def test_ningun_modulo_de_d1_nombra_un_desenlace(rel):
    assert vocabulario_prohibido(_fuente(rel)) == []


def test_los_precios_solo_se_leen_en_los_lectores():
    assert campos_fuera_de_los_lectores(_fuente(D1), LECTORES_D1) == []


def test_la_guarda_de_campos_no_es_vacia_y_solo_ve_el_cierre():
    """Los lectores SÍ leen el cierre: si la guarda dejara de verlo, este
    test lo dice. Y no leen nada más."""
    assert _campos_vistos(_fuente(D1)) == {"cierres": {"close"}, "cierres_revisados": {"close"}}


def test_nada_acumula_de_un_dia_al_siguiente():
    assert acumuladores(_fuente(D1)) == []


# ═══ La guarda ve lo que tiene que ver ════════════════════════════════════

@pytest.mark.parametrize("fuente, cuenta", [
    ("def cierres(df):\n    return df['close']\n", 0),
    ("def paso_2(v):\n    return v['close']\n", 1),
    ("def cierres_revisados(r):\n    return r['nueva']['high']\n", 0),
    ("ULTIMO = 'close'\n", 1),
])
def test_la_guarda_de_campos_con_los_lectores_de_d1(fuente, cuenta):
    assert len(campos_fuera_de_los_lectores(fuente, LECTORES_D1)) == cuenta


@pytest.mark.parametrize("fuente, cuenta", [
    ("import numpy as np\nx = np.cumsum(r)\n", 1),
    ("import math\nx = math.prod(r)\n", 1),
    ("from itertools import accumulate\nx = list(accumulate(r))\n", 1),
    ("x = serie.cumprod()\n", 1),
    ("import numpy as np\nx = np.median(r)\n", 0),
    ('"""cumsum en un docstring no cuenta."""\nx = 1\n', 0),
])
def test_la_guarda_de_acumulacion(fuente, cuenta):
    assert len(acumuladores(fuente)) == cuenta


@pytest.mark.parametrize("fuente, cuenta", [
    ("retorno_td = 1\n", 1),
    ("r_td = 1\n", 0),
    ("x = {'hit_rate': 1}\n", 1),
    ("def coincide(r_td, r_deriv, tolerancia): return 0\n", 0),
])
def test_el_vocabulario_vale_para_los_nombres_de_d1(fuente, cuenta):
    assert len(vocabulario_prohibido(fuente)) == cuenta
