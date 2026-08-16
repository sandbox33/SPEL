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
    FIBONACCI_LAG_DAYS,
    NASH_FROZEN_THRESHOLD,
    InvalidThresholdError,
    MassPanicComponent,
    NashFrozenSource,
    VitalityTier,
    compute_entropy_fibonacci_lags,
    compute_mass_panic_index,
    compute_nash_frozen_7d,
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


# ─── nash_frozen_7d ─────────────────────────────────────────────────────────

def test_nash_insufficient_data_cuando_ventana_es_none():
    result = compute_nash_frozen_7d(None)
    assert result.insufficient_data is True
    assert result.std_normalized is None


def test_nash_insufficient_data_cuando_ventana_esta_vacia():
    result = compute_nash_frozen_7d([])
    assert result.insufficient_data is True


def test_nash_insufficient_data_con_un_solo_punto():
    result = compute_nash_frozen_7d([0.5])
    assert result.insufficient_data is True


def test_nash_frozen_true_cuando_entropia_es_constante():
    # min == max -> e_range fallback a 1.0, normalizado siempre 0.0, std=0.0
    result = compute_nash_frozen_7d([0.5] * 7)
    assert result.insufficient_data is False
    assert result.std_normalized == 0.0
    assert result.frozen is True


def test_nash_frozen_false_cuando_entropia_alterna_al_maximo():
    # alterna entre min y max -> std normalizado alto, muy por encima de 0.15
    result = compute_nash_frozen_7d([0.1, 0.9, 0.1, 0.9, 0.1, 0.9, 0.1])
    assert result.insufficient_data is False
    assert result.std_normalized > NASH_FROZEN_THRESHOLD
    assert result.frozen is False


def test_nash_usa_menor_estricto_no_menor_o_igual_en_el_borde():
    # calcula el std real de una ventana conocida, y usa ESE valor exacto
    # como threshold -- fuerza el caso borde std == threshold.
    base = compute_nash_frozen_7d([0.2, 0.4, 0.3, 0.5, 0.35, 0.45, 0.25])
    on_boundary = compute_nash_frozen_7d(
        [0.2, 0.4, 0.3, 0.5, 0.35, 0.45, 0.25],
        threshold=base.std_normalized,
    )
    assert on_boundary.frozen is False  # < estricto, no <=


def test_nash_recorta_a_los_ultimos_window_days():
    # 14 puntos, pero solo los últimos 7 deben usarse para el std
    ventana_larga = [0.9] * 7 + [0.5] * 7  # primeros 7 son ruido irrelevante
    solo_cola = compute_nash_frozen_7d([0.5] * 7)
    con_cola_larga = compute_nash_frozen_7d(ventana_larga)
    assert con_cola_larga.std_normalized == solo_cola.std_normalized


def test_nash_funciona_con_exactamente_el_minimo_de_dos_puntos():
    result = compute_nash_frozen_7d([0.3, 0.7])
    assert result.insufficient_data is False


def test_nash_source_es_gdelt_foundation():
    result = compute_nash_frozen_7d([0.5, 0.5])
    assert result.source == NashFrozenSource.GDELT_FOUNDATION_NORMALIZED_STD


# ─── mass_panic_index ────────────────────────────────────────────────────────

def test_panic_insufficient_data_sin_ninguna_ventana():
    result = compute_mass_panic_index(None, current_entropy=0.5)
    assert result.insufficient_data is True
    assert result.component == MassPanicComponent.NONE


def test_panic_no_dispara_con_entropia_normal_y_sin_goldstein():
    # ventana estable, current cerca de la media -> z bajo, no dispara
    result = compute_mass_panic_index(
        entropy_window=[0.5, 0.51, 0.49, 0.50, 0.52, 0.48, 0.50],
        current_entropy=0.50,
    )
    assert result.flag is False
    assert result.component == MassPanicComponent.NONE
    assert result.insufficient_data is False


def test_panic_dispara_por_entropia_cuando_z_supera_el_umbral():
    # ventana muy estable (std chico) + salto grande -> z_entropy alto
    result = compute_mass_panic_index(
        entropy_window=[0.50, 0.50, 0.51, 0.49, 0.50, 0.50, 0.51],
        current_entropy=0.90,
    )
    assert result.flag is True
    assert result.component == MassPanicComponent.ENTROPY
    assert result.z_entropy >= 2.0


def test_panic_dispara_por_goldstein_cuando_z_es_muy_negativo():
    result = compute_mass_panic_index(
        entropy_window=[0.5, 0.51, 0.49, 0.50, 0.52, 0.48, 0.50],
        current_entropy=0.50,
        goldstein_window=[1.0, 1.1, 0.9, 1.0, 1.05, 0.95, 1.0],
        current_goldstein=-8.0,
    )
    assert result.flag is True
    assert result.component == MassPanicComponent.GOLDSTEIN
    assert result.z_goldstein <= -2.0


def test_panic_component_both_cuando_las_dos_senales_disparan():
    result = compute_mass_panic_index(
        entropy_window=[0.50, 0.50, 0.51, 0.49, 0.50, 0.50, 0.51],
        current_entropy=0.90,
        goldstein_window=[1.0, 1.1, 0.9, 1.0, 1.05, 0.95, 1.0],
        current_goldstein=-8.0,
    )
    assert result.flag is True
    assert result.component == MassPanicComponent.BOTH


def test_panic_funciona_solo_con_entropia_cuando_no_hay_goldstein():
    # goldstein_window=None (adapter de GDELT event-level no conectado)
    result = compute_mass_panic_index(
        entropy_window=[0.50, 0.50, 0.51, 0.49, 0.50, 0.50, 0.51],
        current_entropy=0.90,
        goldstein_window=None,
        current_goldstein=None,
    )
    assert result.insufficient_data is False
    assert result.z_goldstein is None
    assert result.component == MassPanicComponent.ENTROPY


def test_panic_z_score_es_cero_cuando_la_ventana_no_tiene_varianza():
    result = compute_mass_panic_index(
        entropy_window=[0.5, 0.5, 0.5],
        current_entropy=0.5,
    )
    assert result.z_entropy == 0.0
    assert result.flag is False


def test_panic_respeta_umbrales_personalizados():
    result = compute_mass_panic_index(
        entropy_window=[0.50, 0.50, 0.51, 0.49, 0.50, 0.50, 0.51],
        current_entropy=0.55,  # z moderado, no cruzaría 2.0
        z_entropy_threshold=0.5,  # umbral bajo a propósito -> sí dispara
    )
    assert result.flag is True
    assert result.component == MassPanicComponent.ENTROPY


# ─── entropy_fibonacci_lags ─────────────────────────────────────────────────

def test_fib_historia_none_devuelve_todos_los_lags_en_none():
    result = compute_entropy_fibonacci_lags(None)
    assert result.available_lags == ()
    assert all(v is None for v in result.lags.values())


def test_fib_historia_vacia_devuelve_todos_los_lags_en_none():
    result = compute_entropy_fibonacci_lags([])
    assert result.available_lags == ()


def test_fib_cadence_siempre_es_un_dia():
    result = compute_entropy_fibonacci_lags([0.1, 0.2])
    assert result.cadence_days == 1


def test_fib_valores_exactos_con_historia_completa():
    # history[i] = i, 22 puntos (índices 0..21) -> lag_N = 21 - N,
    # verificado a mano para no confiar en la misma lógica que prueba.
    history = list(range(22))  # [0, 1, 2, ..., 21], hoy = 21
    result = compute_entropy_fibonacci_lags([float(x) for x in history])
    assert result.available_lags == FIBONACCI_LAG_DAYS
    assert result.lags[1] == 20.0
    assert result.lags[2] == 19.0
    assert result.lags[3] == 18.0
    assert result.lags[5] == 16.0
    assert result.lags[8] == 13.0
    assert result.lags[13] == 8.0
    assert result.lags[21] == 0.0


def test_fib_resultado_parcial_con_historia_de_diez_dias():
    # 10 días de historia -> lag 1,2,3,5,8 disponibles; 13,21 no (parcial)
    history = [float(x) for x in range(10)]
    result = compute_entropy_fibonacci_lags(history)
    assert result.available_lags == (1, 2, 3, 5, 8)
    assert result.lags[8] is not None
    assert result.lags[13] is None
    assert result.lags[21] is None


def test_fib_un_lag_faltante_no_invalida_los_demas():
    history = [float(x) for x in range(3)]  # solo alcanza para lag_1 y lag_2
    result = compute_entropy_fibonacci_lags(history)
    assert result.lags[1] is not None
    assert result.lags[2] is not None
    assert result.lags[3] is None


def test_fib_respeta_lag_days_personalizado():
    history = [float(x) for x in range(5)]
    result = compute_entropy_fibonacci_lags(history, lag_days=(1, 2))
    assert set(result.lags.keys()) == {1, 2}
    assert result.available_lags == (1, 2)
