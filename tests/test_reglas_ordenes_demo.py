"""
tests/test_reglas_ordenes_demo.py
===================================
La regla de órdenes demo de CLAUDE.md y las decisiones DG-7 y DG-8
(decision-log 2026-10-05). La regla vieja citaba `authorize.is_virtual`, de
la API legacy retirada; este test separa la redacción nueva de la vieja.
"""

from __future__ import annotations

from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _plano(nombre: str) -> str:
    return " ".join((RAIZ / nombre).read_text(encoding="utf-8").split())


def test_la_regla_de_ordenes_demo_ya_no_cita_la_api_retirada():
    texto = _plano("CLAUDE.md")
    assert "is_virtual" not in texto
    assert ("Órdenes demo: solo desde `integracion_demo/`, con OTP emitido para una cuenta "
            "cuyo `account_type` sea `demo` según `GET /accounts`, y conexión solo a "
            "`/ws/demo`.") in texto


def test_las_sondas_cotizan_solo_con_autorizacion_registrada():
    assert ("Las sondas en `tests/` pueden pedir OTP y cotizar (nunca comprar) solo con "
            "autorización explícita del Admin en un brief fechado, registrada en el "
            "decision-log.") in _plano("CLAUDE.md")


def test_dg7_y_dg8_estan_en_el_decision_log():
    texto = _plano("decision-log.md")
    assert "| DG-7 | **Autonomía asimétrica.**" in texto
    assert "**Ningún parámetro sube en ejecución.**" in texto
    assert "| DG-8 | **Arquitectura.**" in texto
    assert "Usa `execution/circuit_breaker.py` y `execution/execution_guard.py` sin modificarlos" in texto
