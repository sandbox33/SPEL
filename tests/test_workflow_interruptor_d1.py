"""
tests/test_workflow_interruptor_d1.py
=======================================
El interruptor de la ingesta de .github/workflows/concordancia_d1.yml (brief
del Admin 16a, punto 5): los pasos que bajan velas y las commitean solo
corren si SPEL_INGESTA_CRUDA vale exactamente `true`. El script del paso se
ejecuta con bash, con cada valor; no se hace grep.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "concordancia_d1.yml"
ENCENDIDA = "steps.interruptor.outputs.ingesta == 'encendida'"
#: Lo que delata un paso que escribe velas en data.
ESCRIBEN_VELAS = ("velas_d1.py --write", "velas_intradia.py --write", "metrics/velas")


def _pasos() -> list[dict]:
    return yaml.safe_load(WF.read_text(encoding="utf-8"))["jobs"]["concordancia_d1"]["steps"]


def _interruptor() -> dict:
    return next(s for s in _pasos() if s.get("id") == "interruptor")


def test_el_interruptor_lee_la_variable_del_repo_y_no_un_input_ni_un_secreto():
    assert _interruptor()["env"] == {"SPEL_INGESTA_CRUDA": "${{ vars.SPEL_INGESTA_CRUDA }}"}
    wf = yaml.safe_load(WF.read_text(encoding="utf-8"))
    assert wf.pop(True, wf.get("on")) == {"workflow_dispatch": {}}, "sin inputs nuevos (D8)"


@pytest.mark.parametrize("valor, encendida", [
    (None, False),        # la variable no existe
    ("", False),
    ("true", True),
    ("TRUE", False),
    ("True", False),
    (" true", False),
    ("true ", False),
    ("1", False),
    ("yes", False),
    ("false", False),
])
def test_solo_true_exacto_enciende_la_ingesta(tmp_path, valor, encendida):
    salida = tmp_path / "salida"
    env = {"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(salida)}
    if valor is not None:
        env["SPEL_INGESTA_CRUDA"] = valor
    r = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c",
                        _interruptor()["run"]], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert salida.read_text() == f"ingesta={'encendida' if encendida else 'apagada'}\n"


def test_cada_paso_que_escribe_velas_depende_del_interruptor():
    pasos = _pasos()
    escriben = [s for s in pasos if any(t in s.get("run", "") for t in ESCRIBEN_VELAS)]
    assert [s["name"] for s in escriben] == ["Velas diarias de D1 (escribe en data-store)",
                                             "Commit a data"]
    assert all(s.get("if") == ENCENDIDA for s in escriben)
    i = pasos.index(_interruptor())
    assert all(pasos.index(s) > i for s in escriben), "el interruptor va antes"


def test_solo_el_interruptor_lee_la_variable():
    """Ningún otro paso decide con la variable: según la documentación de
    GitHub (no verificado el 28-oct), un `if:` de Actions la compararía sin
    distinguir mayúsculas."""
    lo_leen = [s.get("name") for s in _pasos() if "SPEL_INGESTA_CRUDA" in yaml.safe_dump(s)]
    assert lo_leen == ["Interruptor de la ingesta"]


def test_con_la_ingesta_apagada_la_concordancia_sigue_corriendo():
    """La concordancia no depende del interruptor: con la ingesta apagada lee
    lo que ya está en data."""
    conc = next(s for s in _pasos() if s.get("name") == "Concordancia diaria D1")
    assert "if" not in conc
