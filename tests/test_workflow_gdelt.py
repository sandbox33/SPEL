"""
tests/test_workflow_gdelt.py
==============================
Fija lo que mantiene a `.github/workflows/gdelt.yml` escribiendo en la rama
`data` y en ningún otro lado.

Desde el Brief D ese workflow es el escritor único de la serie. Los
comentarios del YAML explican por qué; este test es lo que impide que un
cambio de tres caracteres (un `permissions` subido al nivel del workflow, un
`branches:` que se amplía) lo contradiga sin que nadie lo note. Se lee como
YAML, no con grep: los comentarios nombran exactamente lo que se busca.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools.verificar_siembra import CIFRAS_SIEMBRA

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _cargar(nombre):
    wf = yaml.safe_load((WORKFLOWS / nombre).read_text(encoding="utf-8"))
    # YAML 1.1 lee la clave `on` como el booleano True.
    wf["on"] = wf.pop(True, wf.get("on"))
    return wf


def _runs(job):
    return [s.get("run", "") for s in job["steps"]]


@pytest.fixture(scope="module")
def gdelt():
    return _cargar("gdelt.yml")


def test_el_cron_esta_activo_y_es_diario(gdelt):
    assert gdelt["on"]["schedule"] == [{"cron": "30 6 * * *"}]


def test_max_days_vacio_por_default(gdelt):
    """Sin techo nuevo: vacío deja a DEFAULT_MAX_DAYS del módulo decidir.
    Un "10" acá sería una segunda copia del valor que puede divergir."""
    entradas = gdelt["on"]["workflow_dispatch"]["inputs"]
    assert entradas["max_days"]["default"] == ""
    assert entradas["modo"]["options"] == ["ingesta", "verificar_siembra"]


def test_una_sola_corrida_a_la_vez_sin_cancelar_la_que_esta_en_curso(gdelt):
    assert gdelt["concurrency"]["cancel-in-progress"] is False
    assert gdelt["concurrency"]["group"]


def test_solo_el_job_de_ingesta_puede_escribir(gdelt):
    assert gdelt["permissions"] == {"contents": "read"}
    assert gdelt["jobs"]["ingesta"]["permissions"] == {"contents": "write"}
    assert "permissions" not in gdelt["jobs"]["verificar_siembra"]


@pytest.mark.parametrize("job", ["ingesta", "verificar_siembra"])
def test_los_dos_jobs_leen_la_serie_de_la_rama_data(gdelt, job):
    j = gdelt["jobs"][job]
    checkouts = [s for s in j["steps"]
                 if str(s.get("uses", "")).startswith("actions/checkout")]
    datos = [s for s in checkouts if s.get("with", {}).get("ref") == "data"]
    assert len(datos) == 1
    assert j["env"]["SPEL_DRIVE_ROOT"].endswith("/" + datos[0]["with"]["path"])


def test_la_ingesta_escribe_con_la_compuerta_de_siembra(gdelt):
    """--exigir-serie tiene que nombrar exactamente los activos que tienen
    siembra: si mañana se siembra NVDA, este test pide sumarlo."""
    run = next(r for r in _runs(gdelt["jobs"]["ingesta"]) if "run_gdelt.py" in r)
    assert "--write" in run
    assert f"--exigir-serie {' '.join(sorted(CIFRAS_SIEMBRA))}" in run


def test_el_push_va_a_data_y_solo_a_data(gdelt):
    commit = next(r for r in _runs(gdelt["jobs"]["ingesta"]) if "git push" in r)
    assert "git pull --rebase -q origin data" in commit
    assert "origin HEAD:data" in commit
    assert "main" not in commit
    assert "github-actions[bot]" in commit


def test_la_alarma_corre_despues_del_commit(gdelt):
    """Una alarma roja no puede impedir que se guarden los días bajados."""
    runs = _runs(gdelt["jobs"]["ingesta"])
    i_commit = next(i for i, r in enumerate(runs) if "git push" in r)
    i_alarma = next(i for i, r in enumerate(runs) if "frescura.py" in r)
    assert i_alarma > i_commit


def test_la_verificacion_no_escribe(gdelt):
    runs = " ".join(_runs(gdelt["jobs"]["verificar_siembra"]))
    assert "verificar_siembra.py" in runs
    for prohibido in ("--write", "run_gdelt", "git push", "git commit"):
        assert prohibido not in runs


def test_ningun_input_se_interpola_dentro_de_un_run(gdelt):
    """`${{ inputs.x }}` dentro de `run:` se expande antes del shell: un
    input con `;` ejecuta lo que quiera. Entran por `env:`."""
    for nombre, job in gdelt["jobs"].items():
        for r in _runs(job):
            assert "inputs." not in r, f"{nombre}: input interpolado en run"


def test_un_push_a_data_no_dispara_ningun_workflow():
    """La rama `data` no corre CI. tests.yml escucha solo main, y ningún
    workflow tiene un trigger de push que alcance a `data`."""
    tests = _cargar("tests.yml")
    assert tests["on"]["push"] == {"branches": ["main"]}
    assert tests["on"]["pull_request"] == {"branches": ["main"]}
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        push = _cargar(wf.name)["on"].get("push")
        if push is not None:
            assert push.get("branches") == ["main"], wf.name
