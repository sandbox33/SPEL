"""
tests/test_workflow_concordancia_d1.py
========================================
Fija `.github/workflows/concordancia_d1.yml` (brief del Admin 14, 14b,
punto 4): solo manual y sin inputs (D8), uno a la vez con los demás que
escriben en `data`, la key solo en el paso que baja, y la concordancia
después del commit. Se lee el YAML, no se hace grep. Patrón:
tests/test_workflow_velas_intradia.py.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "concordancia_d1.yml"


def _wf() -> dict:
    wf = yaml.safe_load(WF.read_text(encoding="utf-8"))
    wf["on"] = wf.pop(True, wf.get("on"))
    return wf


def _pasos() -> list[dict]:
    return _wf()["jobs"]["concordancia_d1"]["steps"]


def test_solo_se_dispara_a_mano_y_sin_inputs():
    assert _wf()["on"] == {"workflow_dispatch": {}}
    assert _wf()["name"] == "SPEL concordancia diaria D1"


def test_uno_a_la_vez_con_los_que_escriben_en_data():
    assert _wf()["concurrency"] == {"group": "gdelt-data", "cancel-in-progress": False}


def test_escribe_solo_su_job_y_solo_velas_en_data():
    wf = _wf()
    assert wf["permissions"] == {"contents": "read"}
    job = wf["jobs"]["concordancia_d1"]
    assert job["permissions"] == {"contents": "write"}
    assert job["env"]["SPEL_DRIVE_ROOT"].endswith("/data-store")
    checkout = next(s for s in job["steps"] if s.get("with", {}).get("path") == "data-store")
    assert checkout["with"]["ref"] == "data"
    commit = next(s for s in job["steps"] if s.get("name") == "Commit a data")
    assert "git push -q origin HEAD:data" in commit["run"]
    assert "git add -A metrics/velas\n" in commit["run"]


def test_la_key_entra_por_el_env_del_paso_que_baja_y_nada_mas():
    pasos = _pasos()
    ingesta = next(s for s in pasos if "ingestion/velas_d1.py" in s.get("run", ""))
    assert ingesta["env"] == {"TWELVEDATA_API_KEY": "${{ secrets.TWELVEDATA_API_KEY }}"}
    assert ingesta["run"] == "python ingestion/velas_d1.py --write"
    assert "TWELVEDATA_API_KEY" not in _wf()["jobs"]["concordancia_d1"]["env"]
    for s in pasos:
        assert "secrets." not in s.get("run", "")
        if s is not ingesta:
            assert "TWELVEDATA_API_KEY" not in (s.get("env") or {})


def test_ni_deriv_con_cuenta_ni_real():
    texto = WF.read_text(encoding="utf-8")
    assert "DERIV_API_TOKEN" not in texto and "DERIV_APP_ID" not in texto
    assert "--entorno" not in texto and "deriv_ws" not in texto


def test_la_concordancia_corre_despues_del_commit_y_un_rojo_no_impide_commitear():
    nombres = [s.get("name") for s in _pasos()]
    i_ingesta = nombres.index("Velas diarias de D1 (escribe en data-store)")
    assert nombres.index("Commit a data") > i_ingesta
    i_conc = nombres.index("Concordancia diaria D1")
    assert i_conc > nombres.index("Commit a data")
    assert _pasos()[i_conc]["run"] == "python tools/concordancia_d1.py"
    assert "env" not in _pasos()[i_conc]
    assert _pasos()[i_ingesta]["continue-on-error"] is True
    assert _pasos()[-1]["if"] == "steps.velas.outcome == 'failure'"
