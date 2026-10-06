"""
tests/test_estado_al_dia.py
=============================
El test anti-desfase de ESTADO.md (DG-6, Brief final v3 §G.5). Falla si hay
más de 3 PRs fusionados desde el commit de referencia de su encabezado, si
ese commit no es ancestro de HEAD, o si el clon es superficial.

La lógica vive en governance/estado.py (el umbral tiene que estar en el
registro de constantes, que no recorre tests/). Acá está el control real
sobre este repo, y repos git temporales para probar cada rama del control
sin depender de la historia de SPEL.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from governance.estado import (
    MAX_MERGES_SIN_ACTUALIZAR_ESTADO,
    RAIZ,
    DesfaseError,
    commit_de_referencia,
    medir,
)


# ═══ El control, sobre este repo ══════════════════════════════════════════

def test_estado_md_esta_al_dia():
    d = medir()
    assert d.al_dia, (
        f"ESTADO.md está desfasado: {d.merges} PRs fusionados desde su commit de "
        f"referencia {d.referencia} (máximo {MAX_MERGES_SIN_ACTUALIZAR_ESTADO}, "
        f"DG-6). Actualizar el encabezado: fecha, commit de main y conteo de "
        f"tests medido.")


def test_el_estado_de_main_antes_de_la_regla_estaba_desfasado():
    """La demostración que pide el brief, fijada: el ESTADO.md de main en
    2832af8 (el commit sobre el que se escribió DG-6) apuntaba a dd9ea63, y
    desde ahí hubo 5 merges. El control lo pone en rojo."""
    viejo = subprocess.run(["git", "-C", str(RAIZ), "show", "2832af8:ESTADO.md"],
                           capture_output=True, text=True, check=True).stdout
    d = medir(texto_estado=viejo, head="2832af8")
    assert d.referencia == "dd9ea63"
    assert d.merges == 5
    assert not d.al_dia


def test_el_checkout_de_ci_trae_la_historia_completa():
    """Sin fetch-depth: 0 el checkout de actions/checkout es superficial y
    el control falla en cada PR (por diseño: no se saltea)."""
    wf = yaml.safe_load((RAIZ / ".github/workflows/tests.yml").read_text(encoding="utf-8"))
    pasos = wf["jobs"]["test"]["steps"]
    checkout = next(p for p in pasos if str(p.get("uses", "")).startswith("actions/checkout"))
    assert checkout.get("with", {}).get("fetch-depth") == 0


# ═══ El encabezado ════════════════════════════════════════════════════════

def test_lee_el_commit_de_referencia_del_encabezado():
    texto = "# ESTADO\n\n**Commit de referencia:** `2832af8` (merge del PR #30).\n"
    assert commit_de_referencia(texto) == "2832af8"


@pytest.mark.parametrize("texto", [
    "sin referencia\n",
    "**Commit de referencia:** `abc1234`\n**Commit de referencia:** `def5678`\n",
    "**Commit de referencia:** abc1234 sin backticks\n",
])
def test_sin_exactamente_una_referencia_falla(texto):
    with pytest.raises(DesfaseError):
        commit_de_referencia(texto)


# ═══ Repos temporales ═════════════════════════════════════════════════════

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
         "-c", "init.defaultBranch=main", *args],
        capture_output=True, text=True, check=True).stdout.strip()


def _repo_con_merges(tmp_path: Path, n_merges: int) -> tuple[Path, str]:
    """main con un commit base y `n_merges` PRs fusionados con --no-ff.
    Devuelve (repo, hash del commit base)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "f.txt").write_text("0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    for k in range(n_merges):
        _git(repo, "checkout", "-q", "-b", f"pr{k}")
        (repo / f"pr{k}.txt").write_text(f"{k}\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"pr {k}")
        _git(repo, "checkout", "-q", "main")
        _git(repo, "merge", "-q", "--no-ff", "-m", f"Merge pull request #{k}", f"pr{k}")
    return repo, base


def _estado(ref: str) -> str:
    return f"# ESTADO\n\n**Commit de referencia:** `{ref}`\n"


@pytest.mark.parametrize("n, al_dia", [(0, True), (3, True), (4, False)])
def test_el_umbral_es_mas_de_tres_merges(tmp_path, n, al_dia):
    repo, base = _repo_con_merges(tmp_path, n)
    d = medir(repo, texto_estado=_estado(base[:7]))
    assert d.merges == n
    assert d.al_dia is al_dia


def test_cuenta_merges_y_no_commits(tmp_path):
    """Un PR con varios commits es UN merge en la línea principal:
    --first-parent no entra en la historia de la rama fusionada."""
    repo, base = _repo_con_merges(tmp_path, 1)
    _git(repo, "checkout", "-q", "-b", "largo")
    for k in range(5):
        (repo / f"l{k}.txt").write_text("x\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"l{k}")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge pull request #9", "largo")
    (repo / "directo.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "commit directo, no es un PR")
    assert medir(repo, texto_estado=_estado(base)).merges == 2


def test_mergear_main_en_una_rama_de_pr_no_cuenta_como_otro_pr(tmp_path):
    """Resolver conflictos mergeando main en la rama del PR (lo que manda el
    proceso: nunca rebase sobre una rama ajena) deja un merge DENTRO de la
    rama. Sin --first-parent se contaría como un PR más. Lo encontró un
    mutante que sobrevivía: ningún test tenía un merge adentro de una rama."""
    repo, base = _repo_con_merges(tmp_path, 1)
    _git(repo, "checkout", "-q", "-b", "con-conflicto", base)
    (repo / "c.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "trabajo")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge main into con-conflicto", "main")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge pull request #2", "con-conflicto")
    assert medir(repo, texto_estado=_estado(base)).merges == 2


def test_una_referencia_que_no_es_ancestro_falla(tmp_path):
    repo, _ = _repo_con_merges(tmp_path, 1)
    _git(repo, "checkout", "-q", "-b", "suelta")
    (repo / "s.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fuera de main")
    suelta = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    with pytest.raises(DesfaseError, match="no es ancestro"):
        medir(repo, texto_estado=_estado(suelta))


def test_una_referencia_inexistente_falla(tmp_path):
    repo, _ = _repo_con_merges(tmp_path, 1)
    with pytest.raises(DesfaseError, match="no existe"):
        medir(repo, texto_estado=_estado("0" * 40))


def test_un_clon_superficial_falla_y_dice_por_que(tmp_path):
    repo, base = _repo_con_merges(tmp_path, 2)
    superficial = tmp_path / "superficial"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{repo}", str(superficial)],
                   check=True, capture_output=True)
    with pytest.raises(DesfaseError, match="superficial.*fetch-depth: 0"):
        medir(superficial, texto_estado=_estado(base))


def test_fuera_de_un_repo_git_falla(tmp_path):
    with pytest.raises(DesfaseError, match="no es un repo git"):
        medir(tmp_path, texto_estado=_estado("abc1234"))
