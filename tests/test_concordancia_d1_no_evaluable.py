"""
tests/test_concordancia_d1_no_evaluable.py
============================================
Decisión 3 del Admin (31-oct, brief 16a): un activo con menos de
MIN_DIAS_COMUNES días comunes es NO EVALUABLE, nunca verde, y no un rojo de
concordancia. La regla del paso 2 no cambia. Offline, con BTC sobre un
metrics/ temporal; el oro, sin archivos, queda detenido.
"""

from __future__ import annotations

import pytest

from tests.test_concordancia_d1 import D, H, _dias, _ep, _escribir, _intradia, precio
from tools import concordancia_d1 as cd


@pytest.fixture(autouse=True)
def _drive(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(tmp_path))


def _btc(n: int, *, ruido: float = 1e-7) -> dict:
    """n días de BTC en las dos fuentes, con rango; `ruido` alterna el cierre
    de Deriv (1e-7 coincide; 5 % no)."""
    dias = _dias(n)
    _escribir("td_BTCUSD", D, {_ep(d): precio(_ep(d) + D) for d in dias}, rango=1.0)
    _escribir("cryBTCUSD", D, {_ep(d): precio(_ep(d) + D) * (1 + ruido * (-1) ** k)
                               for k, d in enumerate(dias)}, rango=1.0)
    _escribir("td_BTCUSD", H, _intradia(dias[-20:], H))
    return dict(cd.informe())


def test_con_249_dias_comunes_btc_es_no_evaluable_y_no_verde():
    bloques = _btc(cd.MIN_DIAS_COMUNES - 1)
    estado = bloques["COMPUERTA"]["BTC"]
    assert estado == "NO EVALUABLE: 249 días comunes, mínimo 250"
    assert estado != "verde" and estado != "roja"
    p2, p3 = bloques["PASO 2 BTC"], bloques["PASO 3 BTC"]
    assert p2["fraccion"] == 1.0 and p3["paso_3"] == "verde", "concuerda: lo que falta son días"
    assert p2["compuerta"] == "roja" and p2["dias_comunes"] == 249, "la regla del paso 2 no cambia"
    assert p2["solo_informativo"] == p3["solo_informativo"] == estado


def test_con_250_dias_que_concuerdan_y_paso_3_verde_btc_es_verde():
    bloques = _btc(cd.MIN_DIAS_COMUNES)
    assert bloques["PASO 2 BTC"]["dias_comunes"] == 250
    assert bloques["PASO 2 BTC"]["fraccion"] >= cd.UMBRAL_COMPUERTA
    assert bloques["PASO 3 BTC"]["paso_3"] == "verde"
    assert bloques["COMPUERTA"]["BTC"] == "verde"
    assert "solo_informativo" not in bloques["PASO 2 BTC"]
    assert "solo_informativo" not in bloques["PASO 3 BTC"]


def test_con_250_dias_que_no_concuerdan_btc_es_rojo_y_no_no_evaluable():
    bloques = _btc(cd.MIN_DIAS_COMUNES, ruido=0.05)
    assert bloques["PASO 2 BTC"]["dias_comunes"] == 250
    assert bloques["PASO 2 BTC"]["fraccion"] < cd.UMBRAL_COMPUERTA
    assert bloques["COMPUERTA"]["BTC"] == "roja"
    assert "solo_informativo" not in bloques["PASO 2 BTC"]


@pytest.mark.parametrize("dias, esperado", [
    (0, "NO EVALUABLE: 0 días comunes, mínimo 250"),
    (249, "NO EVALUABLE: 249 días comunes, mínimo 250"),
    (250, None),
    (251, None),
])
def test_no_evaluable_por_debajo_del_minimo_y_solo_ahi(dias, esperado):
    assert cd.no_evaluable(dias) == esperado


def test_el_resumen_publica_no_evaluable_con_el_conteo_y_el_minimo():
    bloques = _btc(cd.MIN_DIAS_COMUNES - 1)
    inf = cd.informe_para_data(list(bloques.items()), fecha="2026-10-31", run_id="1")
    md = cd.resumen_markdown(inf)
    assert "| BTC | NO EVALUABLE: 249 días comunes, mínimo 250 |" in md
