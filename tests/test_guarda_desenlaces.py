"""
tests/test_guarda_desenlaces.py
=================================
Guarda del punto 0 del brief del Admin del 06-oct-2026 (4): este trabajo NO
calcula ningún desenlace del ORB (ni P&L, ni R, ni aciertos, ni el precio
después de la entrada). Por AST, sobre los módulos nuevos:

  a) Ningún identificador ni string de código nombra un desenlace
     (VOCABULARIO). Los docstrings y los comentarios no cuentan.
  b) En tools/concordancia_xauusd.py, los campos de precio de una vela
     ("open", "high", "low", "close") solo aparecen en los LECTORES: las
     funciones que leen velas y paran en la de ruptura.
  c) `primera_ruptura` sale del bucle en la primera ruptura: cada bucle
     tiene un `return` dentro de un `if`.
  d) `velas_hasta`, que lista velas de un día, solo se llama desde
     `_listado`, que le pasa como tope la vela de ruptura de esa fuente.

Lo que el AST no puede ver —que en la práctica no se lea una vela de
después— lo prueba tests/test_concordancia_xauusd.py con una serie espía y
con velas envenenadas.

[INTERPRETACIÓN] "Los módulos nuevos" son los de este brief. La ingesta
(ingestion/velas_intradia.py) lee todas las velas para guardarlas, que no
es calcular un ORB: para ella vale (a), no (b).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent

MODULOS_NUEVOS: tuple[str, ...] = (
    "tools/concordancia_xauusd.py", "ingestion/velas_intradia.py",
    "ingestion/deriv_publico.py", "ingestion/limitador.py")
ORB = "tools/concordancia_xauusd.py"

VOCABULARIO: tuple[str, ...] = (
    "pnl", "profit", "retorno", "ganancia", "perdida", "pérdida", "acierto",
    "r_multiple", "r_usd", "take_profit", "stop_loss", "payoff", "desenlace",
    "equity", "drawdown", "sharpe", "winrate", "hit_rate")

CAMPOS_DE_PRECIO = frozenset({"open", "high", "low", "close"})
LECTORES = frozenset({"rangos_de_apertura", "rango_del_dia", "primera_ruptura", "velas_hasta"})


def _docstrings(arbol: ast.AST) -> set[int]:
    ids = set()
    for n in ast.walk(arbol):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            c = getattr(n, "body", [])
            if c and isinstance(c[0], ast.Expr) and isinstance(c[0].value, ast.Constant) \
                    and isinstance(c[0].value.value, str):
                ids.add(id(c[0].value))
    return ids


def _nombres(arbol: ast.AST) -> list[tuple[int, str]]:
    docs = _docstrings(arbol)
    out = []
    for n in ast.walk(arbol):
        if isinstance(n, ast.Name):
            out.append((n.lineno, n.id))
        elif isinstance(n, ast.Attribute):
            out.append((n.lineno, n.attr))
        elif isinstance(n, ast.arg):
            out.append((n.lineno, n.arg))
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append((n.lineno, n.name))
        elif isinstance(n, ast.keyword) and n.arg:
            out.append((n.value.lineno, n.arg))
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            out.append((n.lineno, n.value))
    return out


def vocabulario_prohibido(fuente: str) -> list[str]:
    return [f"línea {l}: {t!r}" for l, t in _nombres(ast.parse(fuente))
            if any(p in t.lower() for p in VOCABULARIO)]


def _funcion_de(arbol: ast.AST) -> dict[int, str]:
    """id de cada nodo -> nombre de la función de nivel superior que lo contiene."""
    out = {}
    for f in arbol.body:
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(f):
                out[id(n)] = f.name
    return out


def campos_fuera_de_los_lectores(fuente: str) -> list[str]:
    arbol = ast.parse(fuente)
    docs, donde = _docstrings(arbol), _funcion_de(arbol)
    return [f"línea {n.lineno}: {n.value!r} en {donde.get(id(n), '<módulo>')}"
            for n in ast.walk(arbol)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
            and n.value in CAMPOS_DE_PRECIO and donde.get(id(n)) not in LECTORES]


def bucles_sin_salida(fuente: str, funcion: str) -> list[int]:
    """Las líneas de los bucles de `funcion` que no tienen un return dentro
    de un if en su cuerpo."""
    arbol = ast.parse(fuente)
    f = next(n for n in arbol.body if isinstance(n, ast.FunctionDef) and n.name == funcion)
    malos = []
    for b in ast.walk(f):
        if isinstance(b, (ast.For, ast.While)):
            sale = any(isinstance(i, ast.If) and any(isinstance(r, ast.Return) for r in ast.walk(i))
                       for i in ast.walk(b))
            if not sale:
                malos.append(b.lineno)
    return malos


def llamadores_de(fuente: str, funcion: str) -> set[str]:
    arbol = ast.parse(fuente)
    donde = _funcion_de(arbol)
    return {donde.get(id(n), "<módulo>") for n in ast.walk(arbol)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == funcion}


def _fuente(rel: str) -> str:
    return (RAIZ / rel).read_text(encoding="utf-8")


# ═══ Sobre el código real ═════════════════════════════════════════════════

@pytest.mark.parametrize("rel", MODULOS_NUEVOS)
def test_ningun_modulo_nuevo_nombra_un_desenlace(rel):
    assert vocabulario_prohibido(_fuente(rel)) == []


def test_los_precios_solo_se_leen_en_los_lectores():
    assert campos_fuera_de_los_lectores(_fuente(ORB)) == []


def test_la_guarda_de_campos_no_es_vacia():
    """Los lectores SÍ leen precios: si la guarda dejara de verlos, este
    test lo dice."""
    arbol = ast.parse(_fuente(ORB))
    donde = _funcion_de(arbol)
    vistos = {donde.get(id(n)) for n in ast.walk(arbol)
              if isinstance(n, ast.Constant) and n.value in CAMPOS_DE_PRECIO}
    assert vistos == LECTORES


def test_primera_ruptura_sale_en_la_primera():
    assert bucles_sin_salida(_fuente(ORB), "primera_ruptura") == []


def test_velas_hasta_solo_se_llama_con_el_tope_de_la_ruptura():
    assert llamadores_de(_fuente(ORB), "velas_hasta") == {"_listado"}
    import inspect

    from tools.concordancia_xauusd import _listado
    fuente = inspect.getsource(_listado)
    assert 'dia["ruptura"]' in fuente and 'r["epoch"]' in fuente


# ═══ La guarda ve lo que tiene que ver ════════════════════════════════════

@pytest.mark.parametrize("fuente, cuenta", [
    ("pnl = 1\n", 1),
    ("def f(r_multiple): return 0\n", 1),
    ("x = {'profit': 1}\n", 1),
    ("x = obj.drawdown\n", 1),
    ("f(take_profit=1)\n", 1),
    ('"""El P&L, el retorno y el acierto no se calculan."""\nx = 1\n', 0),
    ("# pnl\nx = 1\n", 0),
    ("rango = 1\n", 0),
])
def test_la_guarda_de_vocabulario(fuente, cuenta):
    assert len(vocabulario_prohibido(fuente)) == cuenta


@pytest.mark.parametrize("fuente, cuenta", [
    ("def rango_del_dia(v):\n    return v['high']\n", 0),
    ("def concordancia(v):\n    return v['close']\n", 1),
    ("def informe(td):\n    return [x['close'] for x in td.values()]\n", 1),
    ("PRECIO = 'open'\n", 1),
    ("def f():\n    '''close'''\n    return 1\n", 0),
])
def test_la_guarda_de_campos(fuente, cuenta):
    assert len(campos_fuera_de_los_lectores(fuente)) == cuenta


@pytest.mark.parametrize("cuerpo, malos", [
    ("    for e in xs:\n        if e > 1:\n            return e\n    return None\n", 0),
    ("    for e in xs:\n        ultimo = e\n    return ultimo\n", 1),
    ("    while True:\n        if xs:\n            return 1\n", 0),
])
def test_la_guarda_de_bucles(cuerpo, malos):
    assert len(bucles_sin_salida("def primera_ruptura(xs):\n" + cuerpo, "primera_ruptura")) == malos
