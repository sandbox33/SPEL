"""
governance/estado.py
======================
El control anti-desfase de `ESTADO.md` (DG-6, decision-log 25-sep-2026):
falla si hay MÁS DE 3 PRs fusionados desde el commit de referencia que
declara su encabezado.

La regla vieja -- el punto 6 de "cómo actualizar este archivo" -- era un
recordatorio: si pasaban ~5 días con patches nuevos sin tocar ESTADO.md,
eso era "señal de circularidad". Falló dos veces con el diagnóstico ya
escrito en el propio archivo (16 y 17 días de desfase). Un recordatorio que
depende de que alguien se acuerde no es un control; esto corre en cada PR.

══ QUÉ SE CUENTA ══

    git rev-list --count --first-parent --merges <ref>..HEAD

Los commits de merge de la línea principal desde la referencia: uno por PR
fusionado, porque este repo fusiona con merge commits ("Merge pull request
#N"). `--first-parent` sigue la línea de `main` y no entra en la historia
de cada rama fusionada.

EN UN PR, HEAD es el merge de prueba que arma GitHub (PR sobre `main`), que
también es un merge y cuenta como uno más. Es exactamente el número que va
a tener `main` si ese PR se fusiona: el check del PR avisa ANTES de que el
desfase llegue a `main`.

LÍMITE CONOCIDO: si el repo pasara a fusionar con squash o rebase, no
habría merge commits y el conteo quedaría en cero para siempre. Medido el
29-sep-2026: los 29 PRs fusionados (#1 a #30, salvo el #8) entraron con
merge commit, y ninguno por squash.

══ UN CLON SUPERFICIAL FALLA, NO SE SALTA ══

Con historia truncada, `<ref>..HEAD` no se puede contar -- o peor, se
cuenta mal sin avisar. El checkout de `tests.yml` necesita `fetch-depth: 0`
y lo tiene; en local, `git fetch --unshallow`.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: DG-6: más de esta cantidad de PRs fusionados desde el commit de
#: referencia de ESTADO.md pone el test en rojo.
MAX_MERGES_SIN_ACTUALIZAR_ESTADO = 3

#: Cómo declara ESTADO.md su commit de referencia. Una sola línea así en el
#: archivo; el hash va entre backticks.
PATRON_COMMIT_DE_REFERENCIA = r"^\*\*Commit de referencia:\*\*\s*`([0-9a-f]{7,40})`"

RAIZ = Path(__file__).resolve().parent.parent


class DesfaseError(AssertionError):
    """ESTADO.md no se puede dar por al día. El mensaje dice por qué."""


def commit_de_referencia(texto: str) -> str:
    hallados = re.findall(PATRON_COMMIT_DE_REFERENCIA, texto, flags=re.MULTILINE)
    if len(hallados) != 1:
        raise DesfaseError(
            f"ESTADO.md tiene que declarar exactamente un "
            f"'**Commit de referencia:** `<hash>`' en su encabezado; tiene "
            f"{len(hallados)}.")
    return hallados[0]


def _git(raiz: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(raiz), *args],
                              capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise DesfaseError("git no está instalado: el control anti-desfase "
                           "necesita la historia del repo.") from exc


def es_superficial(raiz: Path) -> bool:
    r = _git(raiz, "rev-parse", "--is-shallow-repository")
    if r.returncode != 0:
        raise DesfaseError(f"{raiz} no es un repo git ({r.stderr.strip()}): el "
                           f"control anti-desfase necesita la historia.")
    return r.stdout.strip() == "true"


def es_ancestro(raiz: Path, ref: str, head: str) -> bool:
    r = _git(raiz, "merge-base", "--is-ancestor", ref, head)
    if r.returncode not in (0, 1):
        raise DesfaseError(f"el commit de referencia {ref} no existe en esta "
                           f"historia ({r.stderr.strip()}).")
    return r.returncode == 0


def merges_desde(raiz: Path, ref: str, head: str) -> int:
    r = _git(raiz, "rev-list", "--count", "--first-parent", "--merges", f"{ref}..{head}")
    if r.returncode != 0:
        raise DesfaseError(f"no se pudieron contar los merges {ref}..{head}: "
                           f"{r.stderr.strip()}")
    return int(r.stdout.strip())


@dataclass(frozen=True)
class Desfase:
    referencia: str
    head: str
    merges: int

    @property
    def al_dia(self) -> bool:
        return self.merges <= MAX_MERGES_SIN_ACTUALIZAR_ESTADO


def medir(raiz: Path = RAIZ, *, texto_estado: Optional[str] = None,
          head: str = "HEAD") -> Desfase:
    """Mide el desfase. Lanza DesfaseError si no se PUEDE medir (clon
    superficial, referencia ausente o fuera de la historia); si se puede,
    devuelve el conteo y deja el veredicto a `al_dia`."""
    if texto_estado is None:
        texto_estado = (raiz / "ESTADO.md").read_text(encoding="utf-8")
    ref = commit_de_referencia(texto_estado)
    if es_superficial(raiz):
        raise DesfaseError(
            "el clon es superficial (git rev-parse --is-shallow-repository = "
            "true): no se pueden contar los merges desde el commit de "
            f"referencia {ref}. En CI hace falta `fetch-depth: 0` en el "
            "checkout de tests.yml; en local, `git fetch --unshallow`. No se "
            "saltea: un desfase que no se puede medir no está al día.")
    if not es_ancestro(raiz, ref, head):
        raise DesfaseError(
            f"el commit de referencia de ESTADO.md ({ref}) no es ancestro de "
            f"{head}: el encabezado apunta a un commit que no está en esta "
            f"historia.")
    return Desfase(ref, head, merges_desde(raiz, ref, head))
