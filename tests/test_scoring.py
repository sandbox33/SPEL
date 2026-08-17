"""
tests/test_scoring.py
======================
Cobertura de core/scoring.py: la cascada de 3 niveles de vitality_tesla
(B -> A -> C) y la condición Gödel. Cada nivel de la cascada se prueba
por separado, incluyendo los bordes exactos (<=, no <) porque ahí es
donde un port descuidado suele introducir un bug de un solo carácter.
"""

from __future__ import annotations

import numpy as np
import pytest

from core.scoring import (
    DEFAULT_GLOBAL_P33,
    DEFAULT_GLOBAL_P66,
    FIBONACCI_LAG_DAYS,
    MIN_OBS_FOR_HYBRID,
    MIN_OBS_FOR_ROLLING,
    NASH_FROZEN_THRESHOLD,
    AdaptivePercentileResult,
    CORE_COUNTRY_FILTERS,
    GOBIERNO_COUNTRY_FILTERS,
    FX_GOBIERNO_ONLY_ASSETS,
    GdeltPipeline,
    GoldScoreAction,
    GoldScoreKillReason,
    GoldScoreRegime,
    InvalidThresholdError,
    MassPanicComponent,
    NashFrozenSource,
    PercentileSource,
    VitalityTier,
    classify_gdelt_event,
    compute_adaptive_percentile,
    compute_entropy_delta_lags,
    compute_entropy_fibonacci_lags,
    compute_gold_score_bma,
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


def test_nash_bug_micro_ruido_con_referencia_corta_infla_std():
    # Reproduce el bug confirmado con números en esta sesión: con SOLO 7
    # puntos, min/max de referencia == min/max de la cola -> el rango
    # normalizado se estira a [0,1] sin importar la magnitud real.
    micro_ruido = [0.500, 0.501, 0.4995, 0.5005, 0.500, 0.4998, 0.5002]
    result = compute_nash_frozen_7d(micro_ruido)
    assert result.insufficient_reference is True
    assert result.std_normalized > NASH_FROZEN_THRESHOLD  # falso "no congelado"


def test_nash_referencia_larga_corrige_el_mismo_micro_ruido():
    micro_ruido = [0.500, 0.501, 0.4995, 0.5005, 0.500, 0.4998, 0.5002]
    referencia_larga = [0.3 + 0.4 * (i / 300) for i in range(53)] + micro_ruido
    result = compute_nash_frozen_7d(referencia_larga)
    assert result.insufficient_reference is False
    assert result.std_normalized < NASH_FROZEN_THRESHOLD  # ahora sí "congelado"


def test_nash_insufficient_reference_es_false_con_referencia_suficiente():
    # 21 puntos = 3x window_days (7) -> exactamente en el límite, no insuficiente
    result = compute_nash_frozen_7d([0.5] * 21)
    assert result.insufficient_reference is False


def test_nash_insufficient_reference_es_true_justo_debajo_del_limite():
    result = compute_nash_frozen_7d([0.5] * 20)  # 20 < 21 (3x7)
    assert result.insufficient_reference is True


def test_nash_insufficient_reference_tambien_marcado_cuando_insufficient_data():
    result = compute_nash_frozen_7d([0.5])
    assert result.insufficient_data is True
    assert result.insufficient_reference is True  # 1 punto, muy por debajo de 21


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


def test_panic_is_experimental_es_siempre_true():
    result = compute_mass_panic_index(entropy_window=[0.5, 0.5], current_entropy=0.5)
    assert result.is_experimental is True


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


# ─── entropy_delta_lags ─────────────────────────────────────────────────────

def test_delta_historia_none_devuelve_todos_en_none():
    result = compute_entropy_delta_lags(None)
    assert result.available_lags == ()
    assert all(v is None for v in result.deltas.values())


def test_delta_valores_exactos_con_historia_completa():
    # history[i]=i, 22 puntos, hoy=21 -> delta_N = 21 - (21-N) = N
    history = [float(x) for x in range(22)]
    result = compute_entropy_delta_lags(history)
    assert result.deltas[1] == 1.0
    assert result.deltas[5] == 5.0
    assert result.deltas[21] == 21.0


def test_delta_es_negativo_cuando_entropia_bajo():
    # entropía decreciente: hoy es MENOR que hace N días -> delta negativo
    history = [float(21 - x) for x in range(22)]  # [21,20,...,0], hoy=0
    result = compute_entropy_delta_lags(history)
    assert result.deltas[1] == -1.0  # 0 - 1
    assert result.deltas[21] == -21.0  # 0 - 21


def test_delta_resultado_parcial_no_invalida_los_demas():
    history = [float(x) for x in range(3)]
    result = compute_entropy_delta_lags(history)
    assert result.deltas[1] is not None
    assert result.deltas[21] is None


def test_delta_respeta_lag_days_personalizado():
    history = [float(x) for x in range(5)]
    result = compute_entropy_delta_lags(history, lag_days=(1, 2))
    assert set(result.deltas.keys()) == {1, 2}


def test_delta_no_reemplaza_ni_altera_fibonacci_lags_niveles():
    # ambas funciones coexisten -- delta no es un drop-in replacement
    history = [float(x) for x in range(22)]
    niveles = compute_entropy_fibonacci_lags(history)
    deltas = compute_entropy_delta_lags(history)
    assert niveles.lags[1] == 20.0  # nivel: el valor de hace 1 día
    assert deltas.deltas[1] == 1.0   # delta: cuánto cambió en 1 día
    assert niveles.lags[1] != deltas.deltas[1]


# ─── gold_score_bma ─────────────────────────────────────────────────────────

def test_gold_pesos_native_correctos_xau():
    result = compute_gold_score_bma(
        godel_score=0.5, te_score=0.5, backbone_score=0.5, asset="XAU",
        entropy_shannon=0.3, p90_entropy=1.0, vitality_tesla=6,
    )
    assert result.weights_used == {"godel": 0.40, "te_entropy": 0.30, "backbone": 0.30}
    assert result.asset_type == "native"
    assert result.gold_score == pytest.approx(0.5)
    assert result.kill_signal is False


def test_gold_pesos_synthetic_correctos_eurusd():
    result = compute_gold_score_bma(
        godel_score=0.5, te_score=0.5, backbone_score=1.0, asset="EURUSD",
        entropy_shannon=0.3, p90_entropy=1.0, vitality_tesla=6,
    )
    assert result.weights_used == {"godel": 0.55, "te_entropy": 0.45, "backbone": 0.00}
    assert result.asset_type == "synthetic"
    # backbone_score=1.0 no debe influir -- peso 0.0 en synthetic
    assert result.gold_score == pytest.approx(0.55 * 0.5 + 0.45 * 0.5)


def test_gold_reconoce_activo_en_minuscula():
    result = compute_gold_score_bma(
        godel_score=0.5, te_score=0.5, backbone_score=0.5, asset="xau",
        entropy_shannon=0.3, p90_entropy=1.0, vitality_tesla=6,
    )
    assert result.asset_type == "native"


def test_gold_kill_por_godel_active_via_entropia_sobre_p90():
    result = compute_gold_score_bma(
        godel_score=0.9, te_score=0.9, backbone_score=0.9, asset="XAU",
        entropy_shannon=1.5, p90_entropy=1.2, vitality_tesla=6,
    )
    assert result.kill_signal is True
    assert result.kill_reason == GoldScoreKillReason.GODEL_ACTIVE
    assert result.gold_score == 0.0
    assert result.action == GoldScoreAction.HOLD
    assert result.regime == GoldScoreRegime.GODEL_ACTIVE_KILL


def test_gold_kill_por_godel_active_via_vitality_9():
    result = compute_gold_score_bma(
        godel_score=0.9, te_score=0.9, backbone_score=0.9, asset="XAU",
        entropy_shannon=0.1, p90_entropy=1.2, vitality_tesla=9,
    )
    assert result.kill_signal is True
    assert result.kill_reason == GoldScoreKillReason.GODEL_ACTIVE
    assert result.gold_score == 0.0


def test_gold_kill_por_drift_control_kl_divergence():
    result = compute_gold_score_bma(
        godel_score=0.9, te_score=0.9, backbone_score=0.9, asset="XAU",
        entropy_shannon=0.1, p90_entropy=1.2, vitality_tesla=6,
        kl_divergence=0.25,
    )
    assert result.kill_signal is True
    assert result.kill_reason == GoldScoreKillReason.DRIFT_CONTROL
    assert result.regime == GoldScoreRegime.DRIFT_DETECTED
    assert result.gold_score == 0.0
    assert result.action == GoldScoreAction.HOLD


def test_gold_kl_en_el_borde_no_dispara_drift_es_estricto():
    result = compute_gold_score_bma(
        godel_score=0.5, te_score=0.5, backbone_score=0.5, asset="XAU",
        entropy_shannon=0.1, p90_entropy=1.2, vitality_tesla=6,
        kl_divergence=0.20,  # == threshold, no > threshold
    )
    assert result.kill_signal is False


def test_gold_godel_active_tiene_prioridad_sobre_drift_si_ambos_disparan():
    result = compute_gold_score_bma(
        godel_score=0.9, te_score=0.9, backbone_score=0.9, asset="XAU",
        entropy_shannon=1.5, p90_entropy=1.2, vitality_tesla=6,
        kl_divergence=0.99,
    )
    assert result.kill_reason == GoldScoreKillReason.GODEL_ACTIVE


def test_gold_regime_transcendence_cuando_godel_score_es_090_o_mas():
    result = compute_gold_score_bma(
        godel_score=0.95, te_score=0.9, backbone_score=0.9, asset="XAU",
        entropy_shannon=0.1, p90_entropy=1.2, vitality_tesla=6,
    )
    assert result.regime == GoldScoreRegime.TRANSCENDENCE
    assert result.action == GoldScoreAction.EXECUTE_STRONG


def test_gold_regime_creation_cuando_godel_score_bajo():
    result = compute_gold_score_bma(
        godel_score=0.1, te_score=0.1, backbone_score=0.1, asset="XAU",
        entropy_shannon=0.1, p90_entropy=1.2, vitality_tesla=6,
    )
    assert result.regime == GoldScoreRegime.CREATION
    assert result.action == GoldScoreAction.HOLD


def test_gold_action_execute_strong_en_el_borde_085():
    result = compute_gold_score_bma(
        godel_score=0.85, te_score=0.85, backbone_score=0.85, asset="XAU",
        entropy_shannon=0.1, p90_entropy=1.2, vitality_tesla=6,
    )
    assert result.gold_score == pytest.approx(0.85)
    assert result.action == GoldScoreAction.EXECUTE_STRONG


def test_gold_action_watch_en_el_borde_040():
    result = compute_gold_score_bma(
        godel_score=0.40, te_score=0.40, backbone_score=0.40, asset="XAU",
        entropy_shannon=0.1, p90_entropy=1.2, vitality_tesla=6,
    )
    assert result.gold_score == pytest.approx(0.40)
    assert result.action == GoldScoreAction.WATCH


def test_gold_clampea_inputs_fuera_de_rango():
    # godel_score=1.5 debe clampearse a 1.0 antes de ponderar
    result = compute_gold_score_bma(
        godel_score=1.5, te_score=0.0, backbone_score=0.0, asset="XAU",
        entropy_shannon=0.1, p90_entropy=1.2, vitality_tesla=6,
    )
    assert result.gold_score == pytest.approx(0.40)  # 0.40 * 1.0 clampeado


def test_gold_no_kill_por_defecto_sin_kl_divergence():
    result = compute_gold_score_bma(
        godel_score=0.5, te_score=0.5, backbone_score=0.5, asset="XAU",
        entropy_shannon=0.1, p90_entropy=1.2, vitality_tesla=6,
    )
    assert result.kill_signal is False
    assert result.kill_reason == GoldScoreKillReason.NONE


# ─── classify_gdelt_event ───────────────────────────────────────────────────

def test_gdelt_nvda_matchea_core_por_usa():
    result = classify_gdelt_event(["USA"], asset="NVDA")
    assert result.pipeline == GdeltPipeline.CORE
    assert result.matched_countries == ("USA",)


def test_gdelt_xau_matchea_core_con_cualquier_pais_lista_vacia():
    result = classify_gdelt_event(["BRA"], asset="XAU")
    assert result.pipeline == GdeltPipeline.CORE


def test_gdelt_pais_fuera_de_todas_las_listas_es_none():
    result = classify_gdelt_event(["BRA"], asset="NVDA")
    assert result.pipeline == GdeltPipeline.NONE
    assert result.matched_countries == ()


def test_gdelt_gobierno_por_deu_sin_matchear_core():
    result = classify_gdelt_event(["DEU"], asset="NVDA")
    assert result.pipeline == GdeltPipeline.GOBIERNO
    assert result.matched_countries == ("DEU",)


def test_gdelt_usa_prioriza_core_sobre_gobierno_si_matchea_ambos():
    result = classify_gdelt_event(["USA"], asset="NVDA")
    assert result.pipeline == GdeltPipeline.CORE


def test_gdelt_activo_desconocido_lanza_valueerror():
    with pytest.raises(ValueError):
        classify_gdelt_event(["USA"], asset="AAPL")


def test_gdelt_filtra_none_de_actor_countries():
    result = classify_gdelt_event([None, "USA", None], asset="NVDA")
    assert result.pipeline == GdeltPipeline.CORE
    assert result.matched_countries == ("USA",)


def test_gdelt_btc_matchea_gbr():
    result = classify_gdelt_event(["GBR"], asset="BTC")
    assert result.pipeline == GdeltPipeline.CORE
    assert result.matched_countries == ("GBR",)


def test_gdelt_nifty50_matchea_pak():
    result = classify_gdelt_event(["PAK"], asset="NIFTY50")
    assert result.pipeline == GdeltPipeline.CORE


def test_gdelt_lista_vacia_de_countries_es_none():
    result = classify_gdelt_event([], asset="NVDA")
    assert result.pipeline == GdeltPipeline.NONE


def test_gdelt_core_country_filters_tiene_los_4_activos_confirmados():
    assert set(CORE_COUNTRY_FILTERS.keys()) == {"NVDA", "XAU", "BTC", "NIFTY50"}


def test_gdelt_gobierno_country_filters_es_usa_deu():
    assert GOBIERNO_COUNTRY_FILTERS == ("USA", "DEU")


# ─── Regresión: classify_gdelt_event(asset="EURUSD") -- bug real de esta ──
# ─── sesión, nunca ejercitado por ningún test hasta ahora ─────────────────

def test_gdelt_eurusd_ya_no_lanza_valueerror():
    """Antes del fix: asset='EURUSD' lanzaba ValueError siempre, porque
    el código exigía que estuviera en CORE_COUNTRY_FILTERS antes de
    llegar al chequeo GOBIERNO que el propio docstring documentaba para
    este caso. Confirmado empíricamente antes de escribir el fix."""
    resultado = classify_gdelt_event(["USA", "DEU"], asset="EURUSD")
    assert resultado.pipeline == GdeltPipeline.GOBIERNO


def test_gdelt_eurusd_matchea_usa_solo():
    resultado = classify_gdelt_event(["USA", "JPN"], asset="EURUSD")
    assert resultado.pipeline == GdeltPipeline.GOBIERNO
    assert resultado.matched_countries == ("USA",)


def test_gdelt_eurusd_matchea_deu_solo():
    resultado = classify_gdelt_event(["FRA", "DEU"], asset="EURUSD")
    assert resultado.pipeline == GdeltPipeline.GOBIERNO
    assert resultado.matched_countries == ("DEU",)


def test_gdelt_eurusd_sin_paises_relevantes_es_none():
    resultado = classify_gdelt_event(["BRA", "ZAF"], asset="EURUSD")
    assert resultado.pipeline == GdeltPipeline.NONE
    assert resultado.matched_countries == ()


def test_gdelt_eurusd_nunca_pasa_por_core_country_filters():
    """EURUSD no está en CORE_COUNTRY_FILTERS -- si esto alguna vez
    intentara mirar ese dict para EURUSD, lanzaría ValueError. Este test
    confirma que el camino FX_GOBIERNO_ONLY_ASSETS se toma ANTES."""
    assert "EURUSD" not in CORE_COUNTRY_FILTERS
    assert "EURUSD" in FX_GOBIERNO_ONLY_ASSETS
    classify_gdelt_event(["USA"], asset="EURUSD")  # no debe lanzar


def test_gdelt_asset_core_desconocido_sigue_lanzando_valueerror():
    """Regresión inversa: el fix no debe volver el chequeo permisivo para
    activos que genuinamente no están configurados en ningún lado."""
    with pytest.raises(ValueError, match="sin CORE_COUNTRY_FILTERS"):
        classify_gdelt_event(["USA"], asset="ACTIVO_INVENTADO_XYZ")


def test_gdelt_activos_core_siguen_funcionando_igual_que_antes_del_fix():
    """Regresión de no-ruptura: NVDA/XAU/BTC/NIFTY50 no pasan por
    FX_GOBIERNO_ONLY_ASSETS, su comportamiento es idéntico a antes."""
    r_nvda = classify_gdelt_event(["USA", "TWN"], asset="NVDA")
    assert r_nvda.pipeline == GdeltPipeline.CORE
    r_xau = classify_gdelt_event(["BRA"], asset="XAU")  # XAU: filtro vacío = todo matchea
    assert r_xau.pipeline == GdeltPipeline.CORE


# ─── compute_adaptive_percentile ────────────────────────────────────────────

def test_adaptive_pct_puro_global_con_poca_historia():
    result = compute_adaptive_percentile(
        history=[0.5, 0.6, 0.4], percentile=90.0, global_default=0.42,
    )
    assert result.source == PercentileSource.GLOBAL
    assert result.value == 0.42
    assert result.n_obs == 3


def test_adaptive_pct_puro_global_sin_historia():
    result = compute_adaptive_percentile(history=None, percentile=90.0, global_default=0.42)
    assert result.source == PercentileSource.GLOBAL
    assert result.n_obs == 0


def test_adaptive_pct_puro_rolling_con_historia_suficiente():
    history = list(np.linspace(0.0, 1.0, 150))
    result = compute_adaptive_percentile(history=history, percentile=90.0, global_default=0.42)
    assert result.source == PercentileSource.ROLLING
    assert result.value == pytest.approx(np.percentile(history, 90.0))
    assert result.n_obs == 150


def test_adaptive_pct_hibrido_en_zona_intermedia():
    history = list(np.linspace(0.0, 1.0, 50))
    result = compute_adaptive_percentile(history=history, percentile=90.0, global_default=0.42)
    assert result.source == PercentileSource.HYBRID
    rolling_expected = np.percentile(history, 90.0)
    expected = 0.7 * 0.42 + 0.3 * rolling_expected
    assert result.value == pytest.approx(expected)


def test_adaptive_pct_borde_exacto_diez_obs_es_hibrido():
    history = [float(x) for x in range(10)]
    result = compute_adaptive_percentile(history=history, percentile=50.0, global_default=0.5)
    assert result.source == PercentileSource.HYBRID


def test_adaptive_pct_borde_exacto_cien_obs_es_rolling():
    history = [float(x) for x in range(100)]
    result = compute_adaptive_percentile(history=history, percentile=50.0, global_default=0.5)
    assert result.source == PercentileSource.ROLLING


def test_adaptive_pct_es_generico_sirve_para_p33_y_p66():
    history = list(np.linspace(0.0, 1.0, 200))
    p33 = compute_adaptive_percentile(history, percentile=33.0, global_default=DEFAULT_GLOBAL_P33)
    p66 = compute_adaptive_percentile(history, percentile=66.0, global_default=DEFAULT_GLOBAL_P66)
    assert p33.value < p66.value
    assert p33.source == PercentileSource.ROLLING


def test_adaptive_pct_respeta_umbrales_personalizados():
    history = [float(x) for x in range(20)]
    result = compute_adaptive_percentile(
        history=history, percentile=50.0, global_default=0.5,
        min_obs_for_hybrid=25,  # 20 < 25 -> fuerza GLOBAL aunque normalmente sería HYBRID
    )
    assert result.source == PercentileSource.GLOBAL


def test_gold_legacy_threshold_mata_entropia_moderada_que_godel_active_dejaria_pasar():
    # El caso motivador: entropy=0.5 > 0.42 (legacy mata), pero p90=1.2
    # (godel_active NO se activa, 0.5 < 1.2) y vitality != 9. Sin la red
    # de seguridad, esto pasaría con gold_score > 0 -- exactamente el
    # riesgo de un p90 mal calibrado en frío.
    result = compute_gold_score_bma(
        godel_score=0.8, te_score=0.8, backbone_score=0.8, asset="XAU",
        entropy_shannon=0.5, p90_entropy=1.2, vitality_tesla=6,
    )
    assert godel_active(entropy_shannon=0.5, p90_entropy=1.2, vitality_tesla=6) is False  # confirma la premisa
    assert result.kill_signal is True
    assert result.kill_reason == GoldScoreKillReason.LEGACY_ENTROPY_THRESHOLD
    assert result.regime == GoldScoreRegime.HIGH_ENTROPY_LEGACY_KILL
    assert result.gold_score == 0.0


def test_gold_legacy_threshold_none_desactiva_la_red_de_seguridad():
    result = compute_gold_score_bma(
        godel_score=0.8, te_score=0.8, backbone_score=0.8, asset="XAU",
        entropy_shannon=0.5, p90_entropy=1.2, vitality_tesla=6,
        legacy_entropy_threshold=None,
    )
    assert result.kill_signal is False  # vuelve al comportamiento anterior


def test_gold_legacy_threshold_en_el_borde_no_dispara_es_estricto():
    result = compute_gold_score_bma(
        godel_score=0.5, te_score=0.5, backbone_score=0.5, asset="XAU",
        entropy_shannon=0.42, p90_entropy=1.2, vitality_tesla=6,  # == umbral, no >
    )
    assert result.kill_signal is False


def test_gold_godel_active_tiene_prioridad_sobre_legacy_threshold():
    # entropy=1.5 dispara AMBOS (godel_active por >=p90, y legacy por >0.42)
    result = compute_gold_score_bma(
        godel_score=0.8, te_score=0.8, backbone_score=0.8, asset="XAU",
        entropy_shannon=1.5, p90_entropy=1.2, vitality_tesla=6,
    )
    assert result.kill_reason == GoldScoreKillReason.GODEL_ACTIVE  # no LEGACY_ENTROPY_THRESHOLD


def test_gold_legacy_threshold_respeta_valor_personalizado():
    result = compute_gold_score_bma(
        godel_score=0.5, te_score=0.5, backbone_score=0.5, asset="XAU",
        entropy_shannon=0.35, p90_entropy=1.2, vitality_tesla=6,
        legacy_entropy_threshold=0.30,  # umbral más estricto que el default
    )
    assert result.kill_signal is True
    assert result.kill_reason == GoldScoreKillReason.LEGACY_ENTROPY_THRESHOLD


# ─── Benchmark A/B/C: legacy fijo vs. godel_active vs. producción ──────────
#
# Compara 3 lógicas de kill signal en 5 escenarios operacionales:
#   Caso A: umbral legacy fijo puro -- entropy_shannon > 0.42, sin nada más.
#   Caso B: godel_active() puro -- entropy >= p90 OR vitality == 9.
#   Caso C: el sistema real en producción -- compute_gold_score_bma(),
#           que ya combina los 3 mecanismos (godel_active OR legacy_threshold
#           OR drift) con la prioridad confirmada en la sesión del fix.
#
# Objetivo: encontrar los casos límite donde A, B y C DIVERGEN -- ninguno
# de los 3 es simplemente "más estricto" en general, cada uno queda
# ciego a un tipo distinto de riesgo, y C existe precisamente para
# cubrir los puntos ciegos de A y B por separado.
#
# CORRECCIÓN sobre una matriz de escenarios que circuló antes de este
# patch: el Escenario 4 (Drift Severo) traía KL=0.18 -- por DEBAJO de
# KL_DIVERGENCE_THRESHOLD=0.20 (constante real del módulo). Con ese
# valor, ningún caso dispara -- no demuestra nada sobre drift. Se
# corrigió a KL=0.25 (sí supera el umbral real) para que el escenario
# efectivamente muestre lo que dice mostrar: que solo C detecta el
# desvío de distribución.

def _caso_a_legacy_fijo(entropy: float, threshold: float = 0.42) -> bool:
    """Caso A -- réplica standalone del umbral legacy, sin pasar por
    compute_gold_score_bma() (que ya combina los 3 casos vía OR)."""
    return entropy > threshold


def _caso_b_godel_puro(entropy: float, p90: float, vitality: int) -> bool:
    """Caso B -- llama a godel_active() real del módulo, sin wrapping."""
    return godel_active(entropy, p90, vitality)


@pytest.mark.parametrize(
    "nombre, entropy, p90, vitality, kl, esperado_a, esperado_b, esperado_c, razon_c_esperada",
    [
        # 1: entropía alta en términos absolutos, pero el régimen reciente
        # fue tan volátil (p90=0.70) que 0.55 no llega a cruzarlo -- B
        # (adaptativo puro) queda ciego acá. Solo A y C, vía la red de
        # seguridad legacy, lo detienen.
        ("Ruido moderado alto", 0.55, 0.70, 3, 0.02, True, False, True, "legacy_entropy_threshold"),
        # 2: entropía baja en términos absolutos (0.35 < 0.42, A no lo ve),
        # pero el régimen reciente fue MUY estable (p90=0.30) -- 0.35 sí
        # rompe ese percentil. B y C lo detectan, A no.
        ("Micro-ruptura de percentil local", 0.35, 0.30, 3, 0.02, False, True, True, "godel_active"),
        ("Vitality Tesla 9 fuerza kill pese a entropía muy baja", 0.20, 0.50, 9, 0.01, False, True, True, "godel_active"),
        # 4: corregido -- ver nota arriba. KL=0.25 sí supera el umbral real.
        ("Drift severo, KL sobre el umbral real", 0.30, 0.50, 2, 0.25, False, False, True, "drift_control"),
        ("Régimen nominal, ningún caso dispara", 0.25, 0.60, 4, 0.01, False, False, False, "none"),
    ],
)
def test_benchmark_abc_casos_limite_donde_las_3_logicas_divergen(
    nombre, entropy, p90, vitality, kl, esperado_a, esperado_b, esperado_c, razon_c_esperada,
):
    a = _caso_a_legacy_fijo(entropy)
    b = _caso_b_godel_puro(entropy, p90, vitality)
    resultado_c = compute_gold_score_bma(
        godel_score=0.8, te_score=0.8, backbone_score=0.8, asset="XAU",
        entropy_shannon=entropy, p90_entropy=p90, vitality_tesla=vitality,
        kl_divergence=kl,
    )

    assert a == esperado_a, f"{nombre}: Caso A esperaba {esperado_a}, dio {a}"
    assert b == esperado_b, f"{nombre}: Caso B esperaba {esperado_b}, dio {b}"
    assert resultado_c.kill_signal == esperado_c, f"{nombre}: Caso C esperaba {esperado_c}, dio {resultado_c.kill_signal}"
    assert resultado_c.kill_reason.value == razon_c_esperada, (
        f"{nombre}: razón de C esperaba '{razon_c_esperada}', dio '{resultado_c.kill_reason.value}'"
    )


def test_benchmark_abc_al_menos_un_escenario_donde_a_ve_y_b_no():
    # Confirma que existe divergencia real A>B, no solo B>=A siempre --
    # si este test fallara, "Caso A" sería estrictamente redundante.
    assert _caso_a_legacy_fijo(0.55) is True
    assert _caso_b_godel_puro(0.55, p90=0.70, vitality=3) is False


def test_benchmark_abc_al_menos_un_escenario_donde_b_ve_y_a_no():
    # Confirma la divergencia inversa -- ninguno de los 2 domina al otro.
    assert _caso_a_legacy_fijo(0.35) is False
    assert _caso_b_godel_puro(0.35, p90=0.30, vitality=3) is True


def test_benchmark_abc_c_nunca_dispara_menos_que_a_o_b_por_separado():
    # C es el OR de los 3 mecanismos -- por construcción, en cualquier
    # escenario donde A o B disparan, C también debe disparar. Si esto
    # fallara, la síntesis de kill_signal tendría un caso donde perdió
    # cobertura respecto a sus propios componentes.
    escenarios = [
        (0.55, 0.70, 3, 0.02), (0.35, 0.30, 3, 0.02), (0.20, 0.50, 9, 0.01),
        (0.30, 0.50, 2, 0.25), (0.25, 0.60, 4, 0.01),
    ]
    for entropy, p90, vitality, kl in escenarios:
        a = _caso_a_legacy_fijo(entropy)
        b = _caso_b_godel_puro(entropy, p90, vitality)
        c = compute_gold_score_bma(
            0.8, 0.8, 0.8, "XAU", entropy_shannon=entropy, p90_entropy=p90,
            vitality_tesla=vitality, kl_divergence=kl,
        ).kill_signal
        if a or b:
            assert c is True, f"entropy={entropy} p90={p90} vit={vitality}: A={a} B={b} pero C=False"
