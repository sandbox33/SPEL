"""
tests/test_preregistro_h2.py
==============================
El pre-registro H2 no se modifica después de fusionarse. Este test lo hace
valer igual que los de H1 y H3: fija el sha256 de research/preregistro_h2.md,
verifica que cada número de core/preregistro_h2.py esté escrito en él
(parseado, no solo buscado) y que el documento no tenga voseo.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

import core.preregistro_h2 as pre
from tests.test_registro_linguistico import formas_voseantes

RAIZ = Path(__file__).resolve().parent.parent
DOC = RAIZ / "research" / "preregistro_h2.md"

#: Si cambia, el documento cambió. Después de fusionado, eso es un
#: experimento nuevo: preregistro_h2_v2.md, no una edición de este.
SHA256_PREREGISTRO = "a3dfb795f2faf9c4a6ce6a9eaab6b7b60eaac1bb2c82096cea9779602c97f7c6"


def _texto() -> str:
    return DOC.read_text(encoding="utf-8")


def _leer(escrito: str) -> float:
    return float(escrito.replace(".", "").replace(",", "."))


def test_el_documento_no_cambio():
    assert hashlib.sha256(DOC.read_bytes()).hexdigest() == SHA256_PREREGISTRO


def test_el_documento_no_usa_voseo():
    assert formas_voseantes(_texto()) == []


@pytest.mark.parametrize("valor, fragmento, escrito", [
    (pre.REZAGO_ENTROPIA_BARRAS, "(`REZAGO_ENTROPIA_BARRAS = 1`)", "1"),
    (pre.ALFA_DIEBOLD_MARIANO, "**Alfa fijado antes: 0,05.**", "0,05"),
    (pre.VENTANA_INICIAL_BARRAS, "arranca con **504** barras", "504"),
])
def test_cada_numero_del_modulo_esta_escrito_en_el_documento(valor, fragmento, escrito):
    assert fragmento in _texto(), fragmento
    assert escrito in fragmento
    assert _leer(escrito) == pytest.approx(valor)


@pytest.mark.parametrize("calendario, ventanas", [
    ("continuo (BTC)", pre.HAR_VENTANAS_CONTINUO),
    ("hábil (XAU)", pre.HAR_VENTANAS_HABIL),
])
def test_las_ventanas_del_har_son_las_de_la_tabla(calendario, ventanas):
    fila = "| " + calendario + " | " + " | ".join(f"**{v}**" for v in ventanas) + " |"
    assert fila in _texto(), fila


def test_el_rezago_pendiente_esta_marcado_y_explicado():
    """El choque con la publicación de GDELT no se decide acá: queda
    marcado para el Admin, con la constante del código que lo prueba."""
    texto = _texto()
    assert "**[PENDIENTE DEL ADMIN]** El brief pide la entropía de `t−1`." in texto
    assert "`DIAS_DE_RETRASO_DE_PUBLICACION = 1`" in texto


@pytest.mark.parametrize("t, ancho", [(100, 4), (504, 5), (1000, 6), (4500, 9)])
def test_el_ancho_de_newey_west_es_la_regla_escrita(t, ancho):
    assert "`⌊4 · (T/100)^(2/9)⌋`" in _texto()
    assert pre.ancho_newey_west(t) == ancho


def test_el_ancho_no_admite_t_cero():
    with pytest.raises(ValueError):
        pre.ancho_newey_west(0)


def test_el_modulo_no_trae_codigo_de_evaluacion():
    """El código de evaluación va en otro PR. Acá, solo math para una regla
    de una línea."""
    arbol = ast.parse((RAIZ / "core" / "preregistro_h2.py").read_text(encoding="utf-8"))
    importados = {a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names}
    importados |= {n.module for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom)}
    assert importados <= {"__future__", "math"}, importados
