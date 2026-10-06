"""
tests/test_registro_linguistico_md.py
=======================================
El registro del español en los `.md` del repo: ninguna forma voseante.

POR QUÉ UN ARCHIVO NUEVO Y NO UNA AMPLIACIÓN DEL VIEJO. El brief final v3
exige que "el test de voseo corra sobre los .md tocados", y el test que
existe dice lo contrario a propósito: `tests/test_registro_linguistico.py`
"No mira `.md`: ESTADO.md y decision-log.md son notas de trabajo de Altair
y su registro es suyo". La regla del brief es no editar un test existente
que codifica el comportamiento viejo, sino reportarlo. Así que el viejo
queda como está, y este aplica LAS MISMAS reglas -- importa
`formas_voseantes`, no las copia -- a los `.md`.

POR QUÉ TODOS LOS `.md` Y NO SOLO LOS TOCADOS. "Tocados" exige saber contra
qué base se compara, y el checkout de CI no la tiene de forma confiable. Y
no hace falta: medido el 29-sep-2026, ningún `.md` del repo tiene voseo,
salvo una MENCIÓN en decision-log.md (abajo). Cubrirlos todos es un
superconjunto de "los tocados" que hoy ya está en verde.

LA MENCIÓN. La entrada del 16-sep-2026 del decision-log explica la
limitación del test viejo citando tres imperativos voseantes como ejemplo
de lo que la regla automática no atrapa. Es hablar de las palabras, no
usarlas. La excepción se ancla a ESA LÍNEA, no a las palabras: las mismas
tres en otra línea, o en otro archivo, se marcan. Y un test falla si la
línea deja de existir o de necesitar la excepción.

ESTE ARCHIVO NO TIENE VOSEO LITERAL, y es a propósito: el test de `.py`
recorre también `tests/`. Los ejemplos de abajo se toman de
`IMPERATIVOS_EXPLICITOS`, la lista canónica del test viejo.

Se excluye `execution/`, igual que en el test de `.py`: congelado hasta
Fase 4.
"""

from __future__ import annotations

import pathlib

from tests.test_registro_linguistico import (
    EXCLUIDO_POR_CONGELADO,
    IMPERATIVOS_EXPLICITOS,
    RAIZ,
    _IGNORADOS,
    formas_voseantes,
)

#: (archivo, comienzo de la línea) donde el voseo se MENCIONA, no se usa.
MENCIONES_METALINGUISTICAS: frozenset[tuple[str, str]] = frozenset({
    ("decision-log.md", "El precio: los imperativos voseantes de verbos con raíz en r"),
})


def archivos_md_del_repo(raiz: pathlib.Path = RAIZ) -> list[pathlib.Path]:
    out = []
    for md in sorted(raiz.rglob("*.md")):
        if any(parte in _IGNORADOS for parte in md.relative_to(raiz).parts):
            continue
        rel = md.relative_to(raiz).as_posix()
        if rel.split("/")[0] in EXCLUIDO_POR_CONGELADO:
            continue
        out.append(md)
    return out


def _es_mencion(rel: str, linea: str) -> bool:
    return any(rel == archivo and linea.startswith(inicio)
               for archivo, inicio in MENCIONES_METALINGUISTICAS)


def voseo_en_md(raiz: pathlib.Path = RAIZ) -> list[tuple[str, int, str]]:
    """(archivo, número de línea, palabra) de cada forma voseante."""
    hallazgos = []
    for md in archivos_md_del_repo(raiz):
        rel = md.relative_to(raiz).as_posix()
        for n, linea in enumerate(md.read_text(encoding="utf-8").splitlines(), start=1):
            if _es_mencion(rel, linea):
                continue
            hallazgos.extend((rel, n, p) for p in formas_voseantes(linea))
    return hallazgos


# Dos imperativos de la lista canónica, para armar ejemplos sin escribirlos.
_EJEMPLO_A, _EJEMPLO_B = sorted(IMPERATIVOS_EXPLICITOS)[:2]


def test_ningun_md_del_repo_usa_voseo():
    hallazgos = voseo_en_md()
    assert not hallazgos, (
        "Formas voseantes en .md (el registro del repo es español neutro con "
        "tú; CLAUDE.md):\n" + "\n".join(f"  {a}:{n}: {p}" for a, n, p in hallazgos))


def test_cubre_los_md_que_este_brief_toca():
    rels = {p.relative_to(RAIZ).as_posix() for p in archivos_md_del_repo()}
    for esperado in ("CLAUDE.md", "ESTADO.md", "BLUEPRINT.md", "decision-log.md",
                     "governance/PRINCIPLES.md"):
        assert esperado in rels, esperado


def test_cada_mencion_exceptuada_sigue_existiendo_y_haciendo_falta():
    """Una excepción que ya no hace falta se borra, no se acumula."""
    for archivo, inicio in MENCIONES_METALINGUISTICAS:
        lineas = [l for l in (RAIZ / archivo).read_text(encoding="utf-8").splitlines()
                  if l.startswith(inicio)]
        assert len(lineas) == 1, (archivo, inicio)
        assert formas_voseantes(lineas[0]), f"la línea ya no tiene voseo: {archivo}"


def test_un_md_nuevo_con_voseo_se_detecta(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "nota.md").write_text(
        f"Primero {_EJEMPLO_A} esto.\nDespués {_EJEMPLO_B} lo otro.\n", encoding="utf-8")
    (tmp_path / "limpio.md").write_text("Si quieres probarlo, corre la suite.\n",
                                        encoding="utf-8")
    assert voseo_en_md(tmp_path) == [("docs/nota.md", 1, _EJEMPLO_A),
                                     ("docs/nota.md", 2, _EJEMPLO_B)]


def test_la_excepcion_vale_solo_para_su_linea_y_su_archivo(tmp_path):
    ((archivo, inicio),) = MENCIONES_METALINGUISTICAS
    linea = f"{inicio} ({_EJEMPLO_A})"
    (tmp_path / archivo).write_text(f"{linea}\nOtra línea: {_EJEMPLO_A}.\n",
                                    encoding="utf-8")
    (tmp_path / "otro.md").write_text(f"{linea}\n", encoding="utf-8")
    assert voseo_en_md(tmp_path) == [(archivo, 2, _EJEMPLO_A), ("otro.md", 1, _EJEMPLO_A)]
