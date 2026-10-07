"""
tests/test_preregistro_h1.py
==============================
El pre-registro se escribe antes del backtest y no se modifica. Este test es
lo que hace valer esas dos frases:

  · el sha256 de research/preregistro_h1.md está fijado acá. Cambiar el
    documento exige cambiar este test en el mismo PR, a la vista;
  · cada número de core/preregistro_h1.py tiene que estar escrito en el
    documento. H1-B no puede usar otro sin que esto falle.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

import core.preregistro_h1 as pre

RAIZ = Path(__file__).resolve().parent.parent
DOC = RAIZ / "research" / "preregistro_h1.md"

#: Si cambia, el documento cambió. Después de fusionado, eso es un
#: experimento nuevo: preregistro_h1_v2.md, no una edición de este.
SHA256_PREREGISTRO = "3ba23c8dbf6ceae20bde53eb3477a1e5dc35c82cba813d8c7b6c9c5c6bfdb9ae"


def test_el_documento_no_cambio():
    assert hashlib.sha256(DOC.read_bytes()).hexdigest() == SHA256_PREREGISTRO


@pytest.mark.parametrize("valor, como_esta_escrito", [
    (pre.REJILLA_H1, "{10, 20, 40, 80, 160, 320}"),
    (pre.ENTRENAMIENTO_INICIAL_BARRAS, "entrenamiento inicial de **504** barras"),
    (pre.OOS_BARRAS_POR_FOLD, "**126** barras fuera de muestra"),
    (pre.N_FOLDS, "**4** folds"),
    (pre.FRACCION_VOL_OBJETIVO, "capital × 0,25 / vol_realizada_20d_anualizada"),
    (pre.VENTANA_VOL_BARRAS, "las **20** barras anteriores"),
    (pre.APALANCAMIENTO_MAXIMO, "tope de apalancamiento de 2×"),
    (pre.RAZON_STOPOUT_SALIDA, "**al menos 1,5 veces**"),
    (pre.COMISION_REFERENCIA_FRACCION, "**0,1 % del nocional"),
    (pre.COMISION_REFERENCIA_MINIMO_USD, "mínimo de 0,10 USD**"),
    (pre.SHARPE_MINIMO_AVANZAR, "**≥ 0,5**"),
    (pre.PSR_MINIMO, "**PSR(0) ≥ 0,90**"),
    (pre.DSR_MINIMO, "**DSR ≥ 0,90**"),
    (pre.SHARPE_UMBRAL_ALTERNATIVA, "**Sharpe < 0,3**"),
    (pre.ENSAYOS_PREVIOS_FALLIDOS, "Suman **3**"),
    (pre.N_INGENUO, "**N = 10**"),
])
def test_cada_numero_del_modulo_esta_escrito_en_el_documento(valor, como_esta_escrito):
    texto = DOC.read_text(encoding="utf-8")
    assert como_esta_escrito in texto
    # Y el valor del módulo es el que el texto dice: el formato español
    # (coma decimal) del valor tiene que aparecer en el fragmento.
    if isinstance(valor, tuple):
        assert como_esta_escrito == "{" + ", ".join(map(str, valor)) + "}"
    elif isinstance(valor, float) and valor < 1 and valor != pre.COMISION_REFERENCIA_FRACCION:
        assert f"{valor:.2f}".rstrip("0").replace(".", ",") in como_esta_escrito
    elif valor == pre.COMISION_REFERENCIA_FRACCION:
        assert f"{valor * 100:.1f}".replace(".", ",") in como_esta_escrito
    else:
        numero = f"{valor:g}".replace(".", ",")
        assert numero in como_esta_escrito


def test_la_condicion_de_parada_es_la_del_documento():
    assert pre.historia_requerida(320) == 320 + 504 + 4 * (320 + 126) == 2608
    assert "son 2.608 barras" in DOC.read_text(encoding="utf-8")


@pytest.mark.parametrize("historia, rejilla", [
    (2608, [10, 20, 40, 80, 160, 320]),
    (2607, [10, 20, 40, 80, 160]),
    (pre.historia_requerida(160), [10, 20, 40, 80, 160]),
    (pre.historia_requerida(10), [10]),
    (pre.historia_requerida(10) - 1, []),
    (0, []),
])
def test_la_rejilla_se_recorta_desde_arriba_y_solo_por_longitud(historia, rejilla):
    assert pre.rejilla_soportada(historia) == rejilla


def test_rejilla_soportada_no_recibe_precios():
    """El pre-registro prohíbe elegir la rejilla mirando precios o
    retornos. La forma más simple de cumplirlo es que la función no los
    reciba."""
    import inspect
    assert list(inspect.signature(pre.rejilla_soportada).parameters) == ["historia_usable"]


def test_scipy_no_entra_hasta_h1b():
    """Ni en los requirements ni importado en ningún lado. El backtest es
    H1-B; este PR no tiene ninguna línea de él."""
    for req in ("requirements.txt", "requirements-dev.txt"):
        lineas = [l.split("#")[0].strip().lower()
                  for l in (RAIZ / req).read_text().splitlines()]
        assert not any(l.startswith("scipy") for l in lineas), req
    for paq in ("core", "ingestion", "orchestration", "governance", "tools", "research"):
        for py in (RAIZ / paq).rglob("*.py"):
            for n in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
                if isinstance(n, ast.Import):
                    assert not any(a.name.split(".")[0] == "scipy" for a in n.names), py
                elif isinstance(n, ast.ImportFrom) and n.module:
                    assert n.module.split(".")[0] != "scipy", py
