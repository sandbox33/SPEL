"""
ingestion/kappa_deriv.py
==========================
κ por activo: la comisión de un MULTUP de Deriv como fracción del nocional
(stake × multiplicador). Es un dato MEDIDO, no un supuesto: sonda §0.A-3b,
run 37404371657, 06-oct-2026 (decision-log de esa fecha).

══ LA REGLA (decisión del Admin del 06-oct) ══

Entra el MÁXIMO medido en la cuenta REAL, por activo. Si en un activo la
medición real falla, entra el máximo entre los canales público y demo, y
`ORIGEN_KAPPA_DERIV` lo marca como "respaldo". Hoy los tres son "real".

══ ES UNA COTA MEDIDA, NO UNA TASA EXACTA ══

Deriv devuelve la comisión redondeada a centavos, y con stakes de 1 a 2 USD
el redondeo pesa: en el oro, un nocional de 100 USD y uno de 200 pagan los
mismos 0,02 USD (κ 0,0002 y 0,0001). El máximo sobre las cotizaciones es
una cota de lo que se pagó, no la tasa de la tabla de Deriv.

Solo datos: nadie lo consume todavía. La procedencia de cada valor (run y
sha256 de la cotización) está en `config/constantes.json`, y
`tests/test_kappa_deriv.py` reconstruye el máximo desde esas cotizaciones.
`core/execution_costs.py` no guarda tarifas a propósito y está congelado
(acta del 21-sep): este módulo no lo toca.
"""

from __future__ import annotations

#: κ = comisión / nocional, máximo medido en la cuenta real.
KAPPA_DERIV: dict[str, float] = {
    "cryBTCUSD": 0.0003,
    "frxXAUUSD": 0.0002,
    "frxEURUSD": 0.00025,
}

#: De dónde sale cada κ: "real", o "respaldo" (máximo de público y demo).
ORIGEN_KAPPA_DERIV: dict[str, str] = {
    "cryBTCUSD": "real",
    "frxXAUUSD": "real",
    "frxEURUSD": "real",
}
