"""
tests/test_kappa_deriv.py
===========================
κ de `ingestion/kappa_deriv.py` contra las cotizaciones de la cuenta real de
las que sale (sonda §0.A-3b, run 37404371657, decision-log 2026-10-06). La
tabla de abajo se copió del log del job: (stake, multiplicador, stop-loss,
comisión, sha256 de la respuesta). El ×50 no está porque la cuenta real lo
rechaza.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingestion.kappa_deriv import KAPPA_DERIV, ORIGEN_KAPPA_DERIV

RAIZ = Path(__file__).resolve().parent.parent

COTIZACIONES_REALES: dict[str, list[tuple[float, float, object, float, str]]] = {
    "cryBTCUSD": [
        (1, 100, None, 0.03, "158a5167875bceafd5956a9bbcaa5564741b7bf81dd21dc8b60a6baec573a865"),
        (1, 200, None, 0.05, "091593b931c6af2d9984524511faff2aa7b937845732d69a3ad3b179682ab964"),
        (2, 100, None, 0.05, "5b29492162c944314682311bb08727fbc38ec5388ab864eb4da5ad6e23857cde"),
        (2, 200, None, 0.10, "e2b5034fee654691c791483c75f9be8e2f107458d0c35b17f83aa27aa18afe2c"),
        (1, 100, 0.30, 0.03, "0a1e73ab4b88301e6077cb80c7e12f18fa4f48a148cb45812db32e301e12fa2e"),
    ],
    "frxXAUUSD": [
        (1, 100, None, 0.02, "7d88c8b3ba7851801c80d96894e79412e39acd56583cef80a514a6a6afdf2f5d"),
        (1, 200, None, 0.02, "8488e70d3a3ef876ab8edd19a9052c2430268642ba08f34ae7cddc463b9d2b62"),
        (2, 100, None, 0.02, "84b1e2a1df288880da9222e7c2e921759a1e83bfdd8a17f1afd2a776977ea3eb"),
        (2, 200, None, 0.05, "f99d2291496e45b80a8e3ed562cd125dbcc0353c5f421f7a369329b3aeeba79a"),
        (1, 100, 0.30, 0.02, "e82ec6c294166cfcb2a41d64585e6e96247718e98d821b059a6e163a03d480b5"),
    ],
    "frxEURUSD": [
        (1, 100, None, 0.02, "0855a6643212653769318d038b4acea3c1163ee89d15907877bda1b3479917d7"),
        (1, 200, None, 0.05, "747f54c44aa6a6855474b0e920e8ee145af1d6197e41858bf6340dab25099d52"),
        (2, 100, None, 0.05, "ad7a04b2d10759d423723365fd3f0a36546eaee35f88505e343498b7fbab323e"),
        (2, 200, None, 0.09, "35158844bb91beb10ba24526f3ef71c1e69d7e387b68d78e577a02e7e4cca6ce"),
        (1, 100, 0.30, 0.02, "d878f4494525cfcbffe1ebf37944fe790675f626d6f6b1d78d038a659c6b46e3"),
    ],
}


def _kappas(sim):
    return [(com / (stake * m), sha) for stake, m, _, com, sha in COTIZACIONES_REALES[sim]]


@pytest.mark.parametrize("sim", sorted(KAPPA_DERIV))
def test_kappa_es_el_maximo_medido_en_la_cuenta_real(sim):
    assert KAPPA_DERIV[sim] == pytest.approx(max(k for k, _ in _kappas(sim)))
    assert ORIGEN_KAPPA_DERIV[sim] == "real"


def test_los_tres_activos_y_nada_mas():
    assert set(KAPPA_DERIV) == set(ORIGEN_KAPPA_DERIV) == set(COTIZACIONES_REALES)
    assert set(ORIGEN_KAPPA_DERIV.values()) <= {"real", "respaldo"}


def test_el_registro_cita_el_run_y_los_sha256_de_las_cotizaciones_maximas():
    reg = json.loads((RAIZ / "config" / "constantes.json").read_text(encoding="utf-8"))
    [e] = [x for x in reg["constantes"]
           if x["modulo"] == "ingestion.kappa_deriv" and x["nombre"] == "KAPPA_DERIV"]
    assert e["evidencia"] == "medido" and "37404371657" in e["fuente"]
    for sim in KAPPA_DERIV:
        for k, sha in _kappas(sim):
            if k == pytest.approx(KAPPA_DERIV[sim]):
                assert sha in e["fuente"], (sim, sha)


def test_es_una_cota_y_no_una_tasa_el_oro_paga_lo_mismo_a_100_y_a_200():
    """El redondeo a centavos: nocional 100 y 200 en oro, los mismos 0,02 USD."""
    oro = {(s, m): com for s, m, sl, com, _ in COTIZACIONES_REALES["frxXAUUSD"] if sl is None}
    assert oro[(1, 100)] == oro[(1, 200)] == oro[(2, 100)] == 0.02
