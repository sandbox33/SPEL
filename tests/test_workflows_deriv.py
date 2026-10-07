"""
tests/test_workflows_deriv.py
===============================
Fija lo que mantiene a velas.yml y sonda.yml escribiendo en `data`, en
demo, y uno a la vez con gdelt.yml. Mismo enfoque que
test_workflow_gdelt.py: se lee el YAML, no se hace grep.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
NUEVOS = {"velas.yml": "ingestion/velas.py", "sonda.yml": "ingestion/sonda_instrumentos.py"}


def _cargar(nombre):
    wf = yaml.safe_load((WORKFLOWS / nombre).read_text(encoding="utf-8"))
    wf["on"] = wf.pop(True, wf.get("on"))
    return wf


def _job(wf):
    (job,) = wf["jobs"].values()
    return job


def _paso_que_corre(job, script):
    return next(s for s in job["steps"] if script in s.get("run", ""))


@pytest.mark.parametrize("nombre", sorted(NUEVOS))
def test_comparten_el_grupo_de_concurrency_con_gdelt(nombre):
    """Los tres escriben en `data`: dos pushes a la vez se pisarían."""
    gdelt = _cargar("gdelt.yml")["concurrency"]
    wf = _cargar(nombre)
    assert wf["concurrency"] == gdelt
    assert wf["concurrency"]["cancel-in-progress"] is False


@pytest.mark.parametrize("nombre", sorted(NUEVOS))
def test_son_diarios(nombre):
    (cron,) = _cargar(nombre)["on"]["schedule"]
    minuto, hora, dia, mes, dow = cron["cron"].split()
    assert (dia, mes, dow) == ("*", "*", "*")
    assert minuto.isdigit() and hora.isdigit()


@pytest.mark.parametrize("nombre", sorted(NUEVOS))
def test_escriben_en_data_con_permiso_solo_en_su_job(nombre):
    wf = _cargar(nombre)
    job = _job(wf)
    assert wf["permissions"] == {"contents": "read"}
    assert job["permissions"] == {"contents": "write"}
    datos = [s for s in job["steps"] if s.get("with", {}).get("ref") == "data"]
    assert len(datos) == 1
    assert job["env"]["SPEL_DRIVE_ROOT"].endswith("/" + datos[0]["with"]["path"])
    commit = next(s["run"] for s in job["steps"] if "git push" in s.get("run", ""))
    assert "origin HEAD:data" in commit and "main" not in commit


@pytest.mark.parametrize("nombre, script", sorted(NUEVOS.items()))
def test_corren_en_demo_explicito_y_nunca_en_real(nombre, script):
    """Sobre la estructura, no el texto: los comentarios del YAML nombran
    el permiso de `real` justamente para decir que no se define."""
    wf = _cargar(nombre)
    job = _job(wf)
    paso = _paso_que_corre(job, script)
    assert "--entorno demo" in paso["run"]
    assert "--write" in paso["run"]
    envs = [wf.get("env", {}), job.get("env", {})] + [s.get("env", {}) for s in job["steps"]]
    assert not any("SPEL_DERIV_PERMITIR_REAL" in e for e in envs)
    assert not any("--entorno real" in s.get("run", "") for s in job["steps"])


@pytest.mark.parametrize("nombre, script", sorted(NUEVOS.items()))
def test_los_secrets_entran_por_env_del_paso_y_nunca_en_un_run(nombre, script):
    job = _job(_cargar(nombre))
    paso = _paso_que_corre(job, script)
    assert set(paso["env"]) == {"DERIV_APP_ID", "DERIV_API_TOKEN"}
    for s in job["steps"]:
        assert "secrets." not in s.get("run", "")
        if s is not paso:
            assert not any("secrets." in str(v) for v in s.get("env", {}).values())
    assert not any("secrets." in str(v) for v in job.get("env", {}).values())


@pytest.mark.parametrize("nombre", sorted(NUEVOS))
def test_un_rojo_del_script_no_impide_commitear_lo_escrito(nombre):
    job = _job(_cargar(nombre))
    runs = [s.get("run", "") for s in job["steps"]]
    i_script = next(i for i, s in enumerate(job["steps"]) if s.get("continue-on-error"))
    i_commit = next(i for i, r in enumerate(runs) if "git push" in r)
    assert i_commit > i_script
    assert job["steps"][-1]["if"].endswith("outcome == 'failure'")
