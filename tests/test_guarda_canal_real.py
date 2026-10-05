"""
tests/test_guarda_canal_real.py
=================================
Guarda del brief del Admin del 05-oct-2026, punto 5f (y la del 01-oct para
la migración y PR-D): las rutas del canal real de Deriv —el WebSocket real
y la compra masiva real— no pueden aparecer como literal de código en
ningún archivo fuera de `tests/`, y dentro de `tests/` solo en el archivo de
la cotización real autorizada (`tests/test_deriv_cotizacion_real_live.py`).

Lo que se mira es el AST: los strings del código, incluidas las partes
fijas de un f-string. Los docstrings y los comentarios no cuentan: un
comentario no llega al AST, y un docstring no se ejecuta. Lo que esta
guarda no ve es una ruta armada por partes ("ws/" + "re" + "al"): es una
guarda contra el literal, no contra la intención.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent

#: Las rutas prohibidas, armadas por partes para que este archivo no las
#: tenga como literal.
PROHIBIDAS: tuple[str, ...] = ("ws/" + "real", "bulk-purchase/" + "real")

#: El único archivo donde pueden aparecer.
PERMITIDO = Path("tests") / "test_deriv_cotizacion_real_live.py"

_FUERA = {".git", ".venv", "venv", "__pycache__", "node_modules", "site-packages"}


def _docstrings(arbol: ast.AST) -> set[int]:
    ids = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            cuerpo = getattr(nodo, "body", [])
            if (cuerpo and isinstance(cuerpo[0], ast.Expr)
                    and isinstance(cuerpo[0].value, ast.Constant)
                    and isinstance(cuerpo[0].value.value, str)):
                ids.add(id(cuerpo[0].value))
    return ids


def literales_prohibidos(fuente: str) -> list[tuple[int, str]]:
    """(línea, ruta) por cada string de código que contiene una ruta
    prohibida. Los docstrings no cuentan."""
    arbol = ast.parse(fuente)
    docs = _docstrings(arbol)
    out = []
    for nodo in ast.walk(arbol):
        if (isinstance(nodo, ast.Constant) and isinstance(nodo.value, str)
                and id(nodo) not in docs):
            out += [(nodo.lineno, r) for r in PROHIBIDAS if r in nodo.value]
    return out


def archivos_python() -> list[Path]:
    return sorted(p for p in RAIZ.rglob("*.py")
                  if not _FUERA & set(p.relative_to(RAIZ).parts))


def violaciones() -> list[str]:
    out = []
    for p in archivos_python():
        rel = p.relative_to(RAIZ)
        if rel == PERMITIDO:
            continue
        for linea, ruta in literales_prohibidos(p.read_text(encoding="utf-8")):
            out.append(f"{rel}:{linea}: {ruta!r}")
    return out


def test_las_rutas_reales_solo_estan_en_el_archivo_autorizado():
    assert violaciones() == []


def test_la_guarda_no_es_vacia():
    """El archivo autorizado SÍ tiene la ruta del canal real: si la guarda
    dejara de verla, este test lo dice."""
    assert (RAIZ / PERMITIDO).exists()
    encontradas = literales_prohibidos((RAIZ / PERMITIDO).read_text(encoding="utf-8"))
    assert any(r == PROHIBIDAS[0] for _, r in encontradas)
    assert RAIZ / PERMITIDO in archivos_python()
    assert any(p.parts[-2] == "execution" for p in archivos_python()), "recorre execution/"


@pytest.mark.parametrize("fuente, cuenta", [
    ('URL = "wss://api.derivws.com/trading/v1/options/' + 'ws/re' + 'al"\n', 1),
    ('x = f"/trading/v1/options/contracts/' + 'bulk-purchase/re' + 'al?{y}"\n', 1),
    ('"""Docstring que nombra ' + 'ws/re' + 'al."""\nx = 1\n', 0),
    ('def f():\n    """Nombra ' + 'bulk-purchase/re' + 'al."""\n    return 1\n', 0),
    ('# comentario con ' + 'ws/re' + 'al\nx = 1\n', 0),
    ('def f():\n    """doc"""\n    return "' + 'ws/re' + 'al"\n', 1),
    ('x = "ws/" + "re" + "al"\n', 0),
    ('x = "' + 'ws/demo"\n', 0),
])
def test_la_guarda_ve_el_codigo_y_no_los_docstrings(fuente, cuenta):
    assert len(literales_prohibidos(fuente)) == cuenta


def test_la_guarda_ve_un_literal_en_cualquier_otro_archivo(tmp_path, monkeypatch):
    import tests.test_guarda_canal_real as mod
    (tmp_path / "tests").mkdir()
    (tmp_path / "strategies").mkdir()
    (tmp_path / "strategies" / "x.py").write_text('U = "' + 'ws/re' + 'al"\n', encoding="utf-8")
    (tmp_path / "tests" / "test_otro.py").write_text('U = "' + 'bulk-purchase/re' + 'al"\n', encoding="utf-8")
    (tmp_path / PERMITIDO).write_text('U = "' + 'ws/re' + 'al"\n', encoding="utf-8")
    monkeypatch.setattr(mod, "RAIZ", tmp_path)
    v = mod.violaciones()
    assert len(v) == 2
    assert any(s.startswith("strategies/x.py:1") for s in v)
    assert any(s.startswith("tests/test_otro.py:1") for s in v)
