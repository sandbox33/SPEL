"""
tests/test_guarda_integracion_demo.py
=======================================
Guardas del brief del Admin del 06-oct-2026 (3), punto 4, por AST (como la
del 5f, tests/test_guarda_canal_real.py):

  a) Solo integracion_demo/ puede contener `buy`, `sell` o
     `contract_update` como literal de código.
  b) integracion_demo/ no puede nombrar el canal real: ni en el código ni
     en un comentario ni en un docstring.
  c) integracion_demo/ no importa nada de execution/, core/trade_ledger.py
     ni core/execution_costs.py, ni de tests/, ni importa por nombre
     armado (importlib, __import__).
  d) La cadena de hashes detecta una fila editada o borrada: está en
     tests/test_integracion_demo_registro.py (test_la_cadena_detecta_*).

[INTERPRETACIÓN] de a): los tests que prueban que esos mensajes se
RECHAZAN, o que hacen de Deriv, tienen que escribirlos. Se permiten solo en
la lista explícita ARCHIVOS_DE_TEST_PERMITIDOS; un archivo de test nuevo con
uno de esos literales hace fallar la guarda hasta que alguien lo agregue a
mano. Fuera de tests/ no hay excepción.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
PAQUETE = "integracion_demo"

MENSAJES_DE_ORDEN: frozenset[str] = frozenset({"buy", "sell", "contract_update"})

ARCHIVOS_DE_TEST_PERMITIDOS: frozenset[Path] = frozenset({
    # Prueban que la lista blanca de solo lectura los rechaza.
    Path("tests/test_deriv_ws.py"),
    Path("tests/test_deriv_sonda3_live.py"),
    Path("tests/test_deriv_cotizacion_real_live.py"),
    Path("tests/test_velas_intradia.py"),
    # Prueban integracion_demo y hacen de Deriv.
    Path("tests/test_integracion_demo_ejecucion.py"),
    Path("tests/test_integracion_demo_registro.py"),
    Path("tests/test_guarda_integracion_demo.py"),
})

#: El canal real, armado por partes para que este archivo no lo tenga como
#: literal (tests/test_guarda_canal_real.py).
RUTAS_REALES: tuple[str, ...] = ("ws/" + "real", "bulk-purchase/" + "real")

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


def archivos_python(raiz: Path = RAIZ) -> list[Path]:
    return sorted(p for p in raiz.rglob("*.py")
                  if not _FUERA & set(p.relative_to(raiz).parts))


# ═══ a) buy, sell y contract_update solo en integracion_demo/ ═════════════

def mensajes_de_orden(fuente: str) -> list[tuple[int, str]]:
    """(línea, literal) por cada string de código que ES un mensaje de
    orden. Los docstrings no cuentan."""
    arbol = ast.parse(fuente)
    docs = _docstrings(arbol)
    return [(n.lineno, n.value) for n in ast.walk(arbol)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs and n.value.strip().lower() in MENSAJES_DE_ORDEN]


def violaciones_de_mensajes(raiz: Path = RAIZ) -> list[str]:
    out = []
    for p in archivos_python(raiz):
        rel = p.relative_to(raiz)
        if rel.parts[0] == PAQUETE or rel in ARCHIVOS_DE_TEST_PERMITIDOS:
            continue
        out += [f"{rel}:{linea}: {v!r}" for linea, v in mensajes_de_orden(
            p.read_text(encoding="utf-8"))]
    return out


def test_solo_integracion_demo_contiene_mensajes_de_orden():
    assert violaciones_de_mensajes() == []


def test_la_guarda_de_mensajes_no_es_vacia():
    """integracion_demo/ SÍ los tiene, y la guarda recorre todo el repo."""
    hallados = {v for p in (RAIZ / PAQUETE).glob("*.py")
                for _, v in mensajes_de_orden(p.read_text(encoding="utf-8"))}
    assert hallados == MENSAJES_DE_ORDEN
    partes = {p.relative_to(RAIZ).parts[0] for p in archivos_python()}
    assert {"core", "execution", "ingestion", "governance", "tests", PAQUETE} <= partes


def test_los_archivos_de_test_permitidos_existen():
    """Una entrada que ya no existe es una excepción que nadie revisa."""
    for rel in ARCHIVOS_DE_TEST_PERMITIDOS:
        assert (RAIZ / rel).exists(), rel


@pytest.mark.parametrize("fuente, cuenta", [
    ('x = {"buy": "1"}\n', 1),
    ('x = " Sell "\n', 1),
    ('x = {"contract_update": 1}\n', 1),
    ('"""Docstring que dice buy."""\nx = 1\n', 0),
    ('# buy\nx = 1\n', 0),
    ('x = "buy_price"\n', 0),
    ('x = "buyer"\n', 0),
])
def test_la_guarda_de_mensajes_ve_el_codigo(fuente, cuenta):
    assert len(mensajes_de_orden(fuente)) == cuenta


def test_la_guarda_de_mensajes_ve_cualquier_otro_archivo(tmp_path):
    for rel, texto in {"strategies/x.py": 'M = "sell"\n',
                       "tests/test_otro.py": 'M = {"buy": "1"}\n',
                       "core/y.py": '"""buy"""\n',
                       f"{PAQUETE}/z.py": 'M = "buy"\n',
                       "tests/test_deriv_ws.py": 'M = "buy"\n'}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(texto, encoding="utf-8")
    v = violaciones_de_mensajes(tmp_path)
    assert len(v) == 2
    assert any(s.startswith("strategies/x.py:1") for s in v)
    assert any(s.startswith("tests/test_otro.py:1") for s in v)


# ═══ b) integracion_demo/ no nombra el canal real ═════════════════════════

#: La palabra suelta, en un string de código: atrapa también una ruta
#: armada por partes ("/ws/" + "real").
_PALABRA_REAL = re.compile(r"\breal\b", re.IGNORECASE)


def nombra_el_canal_real(fuente: str) -> list[str]:
    """Lo que nombra el canal real: la ruta en cualquier parte del texto
    (comentarios y docstrings incluidos), o la palabra suelta en un string
    de código."""
    out = [r for r in RUTAS_REALES if r in fuente]
    arbol = ast.parse(fuente)
    docs = _docstrings(arbol)
    out += [f"línea {n.lineno}: {n.value!r}" for n in ast.walk(arbol)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs and _PALABRA_REAL.search(n.value)]
    return out


def test_integracion_demo_no_nombra_el_canal_real():
    hallados = {str(p.relative_to(RAIZ)): nombra_el_canal_real(p.read_text(encoding="utf-8"))
                for p in sorted((RAIZ / PAQUETE).rglob("*.py"))}
    assert {k: v for k, v in hallados.items() if v} == {}
    assert len(hallados) >= 6, "recorre los seis módulos del paquete"


@pytest.mark.parametrize("fuente, nombra", [
    ('U = "wss://h/' + 'ws/re' + 'al"\n', True),
    ('# comentario con ' + 'ws/re' + 'al\nx = 1\n', True),
    ('"""Docstring con ' + 'bulk-purchase/re' + 'al."""\n', True),
    ('U = "/ws/" + "re' + 'al"\n', True),
    ('T = "Re' + 'al"\n', True),
    ('U = "wss://h/ws/demo"\n', False),
    ('T = "realizado"\n', False),
    ('"""La cuenta re' + 'al no."""\nx = 1\n', False),
])
def test_la_guarda_del_canal_real_ve_rutas_y_palabras(fuente, nombra):
    assert bool(nombra_el_canal_real(fuente)) is nombra


# ═══ c) integracion_demo/ no importa lo que no puede ══════════════════════

def importaciones_prohibidas(fuente: str) -> list[str]:
    out = []
    for n in ast.walk(ast.parse(fuente)):
        modulos: list[str] = []
        if isinstance(n, ast.Import):
            modulos = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            modulos = [base] + [f"{base}.{a.name}" for a in n.names]
        for m in modulos:
            if (m == "execution" or m.startswith("execution.")
                    or m in ("core.trade_ledger", "core.execution_costs")
                    or m == "tests" or m.startswith("tests.")
                    or m == "importlib" or m.startswith("importlib.")
                    or m.startswith("archive")):
                out.append(f"línea {n.lineno}: {m}")
        if isinstance(n, ast.Name) and n.id == "__import__":
            out.append(f"línea {n.lineno}: __import__")
    return out


def test_integracion_demo_no_importa_lo_congelado():
    hallados = {str(p.relative_to(RAIZ)): importaciones_prohibidas(p.read_text(encoding="utf-8"))
                for p in sorted((RAIZ / PAQUETE).rglob("*.py"))}
    assert {k: v for k, v in hallados.items() if v} == {}


@pytest.mark.parametrize("fuente, prohibida", [
    ("import execution.circuit_breaker\n", True),
    ("from execution import execution_guard\n", True),
    ("from execution.execution_guard import X\n", True),
    ("from core.trade_ledger import append_trade\n", True),
    ("from core import trade_ledger\n", True),
    ("from core import execution_costs as ec\n", True),
    ("import core.execution_costs\n", True),
    ("from tests.deriv_falso import DerivFalso\n", True),
    ("import importlib\n", True),
    ("m = __import__('execution')\n", True),
    ("from core import preregistro_h1\n", False),
    ("from ingestion.kappa_deriv import KAPPA_DERIV\n", False),
    ("from governance.persistence import drive_root\n", False),
])
def test_la_guarda_de_importaciones(fuente, prohibida):
    assert bool(importaciones_prohibidas(fuente)) is prohibida
