"""
research/tests/test_aislamiento.py
====================================
La dirección de la dependencia, fijada: el motor NUNCA importa de
`research/`.

POR QUÉ ESTE TEST ES EL QUE SOSTIENE EL RETIRO. Mover código a `research/`
no logra nada si mañana `orchestration/cycle.py` escribe
`from research.gold_score_chain import compute_gold_score_bma`. Eso
reintroduciría la cadena muerta por la puerta de atrás, con la diferencia
de que ahora estaría escondida detrás de un import que parece deliberado.

El retiro es una afirmación sobre QUIÉN LLAMA A QUIÉN, no sobre en qué
carpeta viven los archivos. Esto lo verifica; el `git mv` solo lo sugiere.

Al revés SÍ vale, y por eso el test es asimétrico: `research/` importa de
`core/` y de `orchestration/` (`godel_active` sigue siendo la máscara viva,
`_build_windows` sigue leyendo la serie). Esa dirección es la que hace que
el código retirado siga corriendo contra el motor real en vez de contra una
copia congelada que se desactualiza sin que nadie se entere.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent.parent

#: Los paquetes que forman el motor: lo que corre en el ciclo diario.
MOTOR = ("core", "orchestration", "ingestion", "governance", "execution", "tools")


def _modulos_importados(py: pathlib.Path) -> set[str]:
    """Sobre el AST, no con grep: los docstrings de este repo mencionan
    `research/` por todos lados y un grep daría falso positivo en cada uno."""
    arbol = ast.parse(py.read_text(encoding="utf-8"))
    out: set[str] = set()
    for n in ast.walk(arbol):
        if isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module)
    return out


def _archivos_del_motor() -> list[pathlib.Path]:
    out = []
    for paquete in MOTOR:
        out.extend(sorted((RAIZ / paquete).rglob("*.py")))
    return [p for p in out if "__pycache__" not in p.parts]


def test_ningun_modulo_del_motor_importa_research():
    """LA REGLA. Si esto se pone en rojo, alguien volvió a enchufar la
    cadena muerta -- y la decisión de hacerlo va al decision-log.md antes
    que al código, no después."""
    ofensas = []
    for py in _archivos_del_motor():
        for mod in _modulos_importados(py):
            if mod == "research" or mod.startswith("research."):
                ofensas.append(f"{py.relative_to(RAIZ).as_posix()} -> {mod}")

    assert not ofensas, (
        "El motor está importando de research/, que es exactamente lo que "
        "el retiro del 16-sep-2026 sacó del camino caliente:\n  "
        + "\n  ".join(ofensas))


def test_el_barrido_del_motor_mira_archivos_de_verdad():
    """Contraprueba: si `_archivos_del_motor()` devolviera lista vacía por
    un error de rutas, el test de arriba pasaría en verde sin mirar nada.
    Es el modo de falla silencioso de todo test que barre un árbol."""
    archivos = _archivos_del_motor()
    assert len(archivos) > 15, f"solo {len(archivos)} archivos barridos"

    nombres = {p.relative_to(RAIZ).as_posix() for p in archivos}
    for esperado in ("core/scoring.py", "orchestration/cycle.py",
                     "ingestion/run_gdelt.py"):
        assert esperado in nombres, f"{esperado} quedó fuera del barrido"


@pytest.mark.parametrize("modulo", [
    "research.gold_score_chain", "research.price_signals",
    "research.cycle_gold_score"])
def test_lo_retirado_sigue_siendo_importable(modulo):
    """Un retiro a `archive/*` deja el código sin compilar contra el resto
    (fue lo correcto en el PR #22, donde no había vuelta). Acá la condición
    de reversión está escrita, así que el código tiene que seguir vivo: si
    deja de importar, el día que Fase 2 lo retome habrá que redescubrir si
    todavía funciona."""
    import importlib

    assert importlib.import_module(modulo) is not None


def test_research_si_puede_importar_del_motor():
    """La asimetría es el diseño, no un descuido: `research/` corre contra
    el motor real. Si se le prohibiera importar de `core/`, habría que
    congelarle una copia -- y una copia congelada se desactualiza sin que
    nadie se entere."""
    from research.cycle_gold_score import run_scoring_cycle_con_gold_score
    from research.gold_score_chain import compute_gold_score_bma

    importados = _modulos_importados(RAIZ / "research" / "gold_score_chain.py")
    assert any(m.startswith("core.") for m in importados), (
        "research/gold_score_chain.py dejó de apoyarse en core: revisar si "
        "se le copió algo en vez de importarlo")
    assert callable(compute_gold_score_bma)
    assert callable(run_scoring_cycle_con_gold_score)
