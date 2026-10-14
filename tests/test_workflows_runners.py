"""
tests/test_workflows_runners.py
=================================
Runners y acciones de .github/workflows/ (brief del Admin del 14-oct-2026
(9), tarea D).

  · `ubuntu-latest` es hoy la imagen ubuntu-24.04 y migra a Ubuntu 26 desde
    el 19-oct-2026 (aviso de los runs del 13 y 14-oct; actions/runner-images
    #14748). Cada job se fija a `ubuntu-24.04`: el sistema del runner cambia
    cuando lo decide un PR, no cuando cambia la etiqueta.
  · actions/checkout@v4 y actions/setup-python@v5 declaran Node 20 y corren
    forzados en Node 24. checkout v5.0.0 y setup-python v6.0.0 son las
    primeras versiones mayores con `runs: using: node24` en su action.yml
    (repositorios oficiales, tags del 11-ago-2025 y del 03-sep-2025).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parents[1] / ".github" / "workflows").glob("*.yml"))
RUNNER = "ubuntu-24.04"
#: La versión mayor mínima de cada acción, la primera con Node 24.
MAYOR_MINIMA = {"actions/checkout": 5, "actions/setup-python": 6}


def _jobs(ruta: Path) -> dict:
    return yaml.safe_load(ruta.read_text(encoding="utf-8"))["jobs"]


def _usos(ruta: Path) -> list[str]:
    return [paso["uses"] for job in _jobs(ruta).values()
            for paso in job.get("steps", []) if "uses" in paso]


def test_hay_workflows_que_revisar():
    assert len(WORKFLOWS) >= 7


@pytest.mark.parametrize("ruta", WORKFLOWS, ids=lambda p: p.name)
def test_ningun_workflow_usa_ubuntu_latest(ruta):
    texto = ruta.read_text(encoding="utf-8")
    assert "ubuntu-latest" not in texto, f"{ruta.name} usa ubuntu-latest"


@pytest.mark.parametrize("ruta", WORKFLOWS, ids=lambda p: p.name)
def test_cada_job_corre_en_ubuntu_24_04(ruta):
    for nombre, job in _jobs(ruta).items():
        assert job.get("runs-on") == RUNNER, f"{ruta.name}:{nombre} runs-on {job.get('runs-on')!r}"


@pytest.mark.parametrize("ruta", WORKFLOWS, ids=lambda p: p.name)
def test_checkout_y_setup_python_en_una_version_con_node_24(ruta):
    for uso in _usos(ruta):
        accion, _, version = uso.partition("@")
        if accion not in MAYOR_MINIMA:
            continue
        m = re.fullmatch(r"v(\d+)", version)
        assert m, f"{ruta.name}: {uso} no es una versión mayor (vN)"
        assert int(m.group(1)) >= MAYOR_MINIMA[accion], f"{ruta.name}: {uso} corre en Node 20"


def test_solo_se_usan_acciones_conocidas():
    """Si aparece otra acción, este test obliga a mirar en qué Node corre."""
    acciones = {u.partition("@")[0] for r in WORKFLOWS for u in _usos(r)}
    assert acciones <= set(MAYOR_MINIMA), acciones - set(MAYOR_MINIMA)
