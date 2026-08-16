"""
tests/test_scoring.py
======================
Cobertura de core/scoring.py: la cascada de 3 niveles de vitality_tesla
(B -> A -> C) y la condición Gödel. Cada nivel de la cascada se prueba
por separado, incluyendo los bordes exactos (<=, no <) porque ahí es
donde un port descuidado suele introducir un bug de un solo carácter.
"""

from __future__ import annotations

import pytest

from core.scoring import (
    DEFAULT_GLOBAL_P33,
    DEFAULT_GLOBAL_P66,
    InvalidThresholdError,
    VitalityTier,
    compute_vitality_tesla,
    godel_active,
)


# ─── Validación de entrada ──────────────────────────────────────────────────

def test_rechaza_global_p33_mayor_o_igual_a_p66():
    with pytest.raises(InvalidThresholdError):
        compute_vitality_tesla(
            n_events_window=[1, 2, 3],
            entropy_window=None,
            current_entropy=0.5,
            global_p33=0.70,
            global_p66=0.30,
        )


def test_rechaza_global_p33_igual_a_p66():
    with pytest.raises(InvalidThresholdError):
        compute_vitality_tesla(
            n_events_window=[1, 2, 3],
            entropy_window=None,
            current_entropy=0.5,
            global_p33=0.5,
            global_p66=0.5,
        )


# ─── PRIMARIA (B): tercil de n_events ───────────────────────────────────────

def test_b_devuelve_3_cuando_el_actual_esta_en_o_bajo_p33():
    # ventana con el punto actual (10) como último elemento, claramente bajo
    result = compute_vitality_tesla(
        n_events_window=[50, 60, 70, 80, 90, 10],
        entropy_window=None,
        current_entropy=0.5,
    )
    assert result.value == 3
    assert result.tier_used == VitalityTier.PRIMARY_N_EVENTS
    assert result.degraded is False


def test_b_devuelve_9_cuando_el_actual_esta_sobre_p66():
    result = compute_vitality_tesla(
        n_events_window=[10, 20, 30, 40, 50, 999],
        entropy_window=None,
        current_entropy=0.5,
    )
    assert result.value == 9
    assert result.tier_used == VitalityTier.PRIMARY_N_EVENTS


def test_b_devuelve_6_en_zona_media():
    result = compute_vitality_tesla(
        n_events_window=[10, 20, 30, 40, 50, 60, 70, 80, 90, 35],
        entropy_window=None,
        current_entropy=0.5,
    )
    assert result.value == 6
    assert result.tier_used == VitalityTier.PRIMARY_N_EVENTS


def test_b_usa_menor_o_igual_no_menor_estricto_en_el_borde_p33():
    # ventana uniforme: p33 == p66 == el valor mismo si todos los puntos
    # son iguales -- el actual, igual al p33 calculado, debe caer en 3
    # (regla: <=, no <), replicando el legacy exacto.
    result = compute_vitality_tesla(
        n_events_window=[5, 5, 5, 5, 5],
        entropy_window=None,
        current_entropy=0.5,
    )
    assert result.value == 3  # 5 <= p33(=5) -> 3, nunca 6 ni 9


def test_b_funciona_con_exactamente_el_minimo_de_puntos():
    # MIN_WINDOW_FOR_PERCENTILE = 3 -- con exactamente 3 debe usar B, no caer a A
    result = compute_vitality_tesla(
        n_events_window=[10, 50, 90],
        entropy_window=[0.9, 0.9, 0.9],  # si cayera a A, daría un resultado distinto
        current_entropy=0.5,
    )
    assert result.tier_used == VitalityTier.PRIMARY_N_EVENTS


# ─── Degradación de B a RESPALDO 1 (A) ──────────────────────────────────────

def test_cae_a_respaldo_1_cuando_n_events_window_es_none():
    result = compute_vitality_tesla(
        n_events_window=None,
        entropy_window=[0.1, 0.2, 0.15],
        current_entropy=0.05,
    )
    assert result.tier_used == VitalityTier.FALLBACK_ENTROPY_ROLLING
    assert result.degraded is True


def test_cae_a_respaldo_1_cuando_n_events_window_tiene_menos_del_minimo():
    result = compute_vitality_tesla(
        n_events_window=[10, 20],  # 2 puntos, menos que MIN_WINDOW_FOR_PERCENTILE=3
        entropy_window=[0.1, 0.2, 0.15, 0.3],
        current_entropy=0.05,
    )
    assert result.tier_used == VitalityTier.FALLBACK_ENTROPY_ROLLING


def test_cae_a_respaldo_1_cuando_n_events_window_esta_vacia():
    result = compute_vitality_tesla(
        n_events_window=[],
        entropy_window=[0.1, 0.2, 0.15, 0.3],
        current_entropy=0.9,
    )
    assert result.tier_used == VitalityTier.FALLBACK_ENTROPY_ROLLING
    assert result.value == 9  # 0.9 claramente por encima de la historia [0.1-0.3]


def test_respaldo_1_devuelve_3_6_9_segun_percentil_de_la_historia():
    historia = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    bajo = compute_vitality_tesla(None, historia, current_entropy=0.05)
    medio = compute_vitality_tesla(None, historia, current_entropy=0.55)
    alto = compute_vitality_tesla(None, historia, current_entropy=1.5)
    assert (bajo.value, medio.value, alto.value) == (3, 6, 9)
    assert all(r.tier_used == VitalityTier.FALLBACK_ENTROPY_ROLLING for r in (bajo, medio, alto))


# ─── Degradación de A a RESPALDO 2 (C) -- arranque en frío ─────────────────

def test_cae_a_respaldo_2_cuando_ambas_ventanas_son_none():
    result = compute_vitality_tesla(
        n_events_window=None,
        entropy_window=None,
        current_entropy=0.5,
    )
    assert result.tier_used == VitalityTier.FALLBACK_GLOBAL_THRESHOLDS
    assert result.degraded is True


def test_cae_a_respaldo_2_cuando_ambas_ventanas_son_insuficientes():
    result = compute_vitality_tesla(
        n_events_window=[1],
        entropy_window=[0.1, 0.2],
        current_entropy=0.5,
    )
    assert result.tier_used == VitalityTier.FALLBACK_GLOBAL_THRESHOLDS


def test_respaldo_2_usa_los_percentiles_globales_por_defecto():
    bajo = compute_vitality_tesla(None, None, current_entropy=0.10)   # < 0.30
    medio = compute_vitality_tesla(None, None, current_entropy=0.50)  # entre 0.30 y 0.70
    alto = compute_vitality_tesla(None, None, current_entropy=0.90)   # >= 0.70
    assert (bajo.value, medio.value, alto.value) == (3, 6, 9)


def test_respaldo_2_funciona_desde_el_primer_dato_sin_ninguna_ventana():
    """El caso de uso real: t=1, sin ningún historial acumulado todavía."""
    result = compute_vitality_tesla(None, None, current_entropy=0.85)
    assert result.value == 9
    assert result.tier_used == VitalityTier.FALLBACK_GLOBAL_THRESHOLDS


def test_respaldo_2_respeta_percentiles_globales_personalizados():
    result = compute_vitality_tesla(
        None, None, current_entropy=0.45,
        global_p33=0.40, global_p66=0.60,
    )
    assert result.value == 6  # con los defaults (0.30/0.70) hubiera sido distinto


def test_defaults_de_percentiles_globales_coinciden_con_el_legacy():
    assert DEFAULT_GLOBAL_P33 == 0.30
    assert DEFAULT_GLOBAL_P66 == 0.70


# ─── El valor siempre es 3, 6 o 9 -- nunca otra cosa ───────────────────────

@pytest.mark.parametrize("n_events,entropy_hist,current", [
    ([1, 2, 3, 4, 5], None, 0.5),
    (None, [0.1, 0.2, 0.3], 0.15),
    (None, None, 0.99),
    ([100], [0.5], 0.5),  # ambas insuficientes -> C
])
def test_el_valor_siempre_es_3_6_o_9(n_events, entropy_hist, current):
    result = compute_vitality_tesla(n_events, entropy_hist, current)
    assert result.value in (3, 6, 9)


# ─── Condición Gödel ─────────────────────────────────────────────────────────

def test_godel_activo_por_entropia_sobre_p90():
    assert godel_active(entropy_shannon=1.5, p90_entropy=1.2, vitality_tesla=6) is True


def test_godel_activo_por_vitality_tesla_9_aunque_entropia_este_baja():
    assert godel_active(entropy_shannon=0.1, p90_entropy=1.2, vitality_tesla=9) is True


def test_godel_inactivo_si_ninguna_condicion_se_cumple():
    assert godel_active(entropy_shannon=0.5, p90_entropy=1.2, vitality_tesla=6) is False


def test_godel_activo_en_el_borde_exacto_del_p90():
    assert godel_active(entropy_shannon=1.2, p90_entropy=1.2, vitality_tesla=3) is True


def test_godel_activo_cuando_ambas_condiciones_se_cumplen():
    assert godel_active(entropy_shannon=2.0, p90_entropy=1.2, vitality_tesla=9) is True
