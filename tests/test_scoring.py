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
    compute_godel_p66,
    compute_godel_p90,
    compute_mass_panic_index,
    MIN_WINDOW_FOR_PERCENTILE,
    compute_nash_frozen_7d,
    compute_vitality_tesla,
    godel_active,
    GODEL_CRITERIA_VERSION,
    GODEL_ROLLING_WINDOW_DAYS,
    GODEL_MASK_PERCENTILE,
    entropy_state,
    ENTROPY_STATE_LOW,
    ENTROPY_STATE_MID,
    ENTROPY_STATE_HIGH,
    ENTROPY_STATE_WARMUP,
    ENTROPY_STATE_PERCENTILES,
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

def test_godel_activo_por_entropia_sobre_el_tercil_superior():
    assert godel_active(entropy_shannon=1.5, p66_entropy=1.2) is True


def test_godel_inactivo_bajo_el_tercil_superior():
    assert godel_active(entropy_shannon=0.5, p66_entropy=1.2) is False


def test_godel_en_el_borde_exacto_NO_dispara_la_comparacion_es_estricta():
    """PORT DEL LEGACY, y un cambio respecto de la versión anterior. El
    legacy asigna el tercil superior cuando el valor NO cumple `e <= p66`,
    o sea con `>` estricto. La fórmula vieja usaba `entropy >= p90` y por
    eso el borde exacto disparaba; ahora no.

    No es una preferencia de estilo: un `>=` movería de tercil a los días
    que caen justo sobre el umbral, y el borde de un percentil sobre una
    serie discreta se toca más seguido de lo que parece."""
    assert godel_active(entropy_shannon=1.2, p66_entropy=1.2) is False
    assert godel_active(entropy_shannon=1.2000001, p66_entropy=1.2) is True


def test_vitality_tesla_ya_no_participa_de_la_mascara():
    """Versión 4.0.0: la máscara dejó de ser un OR. Antes existía este
    test, y pasaba:

        godel_active(entropy_shannon=0.1, p90_entropy=1.2, vitality_tesla=9)
        -> True

    Un día con entropía muy por debajo del umbral entraba igual porque
    vitality valía 9. Eso ya no ocurre: `godel_active` no recibe vitality
    y una entropía baja no pasa el filtro por ninguna vía.

    Que este test exista y no sea solo una eliminación importa: deja
    constancia de qué comportamiento se quitó, para que reaparecer no sea
    gratis."""
    import inspect

    assert "vitality_tesla" not in inspect.signature(godel_active).parameters
    assert godel_active(entropy_shannon=0.1, p66_entropy=1.2) is False


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
        entropy_shannon=0.3, p66_entropy=1.0,
    )
    assert result.weights_used == {"godel": 0.40, "te_entropy": 0.30, "backbone": 0.30}
    assert result.asset_type == "native"
    assert result.gold_score == pytest.approx(0.5)
    assert result.kill_signal is False


def test_gold_pesos_synthetic_correctos_eurusd():
    result = compute_gold_score_bma(
        godel_score=0.5, te_score=0.5, backbone_score=1.0, asset="EURUSD",
        entropy_shannon=0.3, p66_entropy=1.0,
    )
    assert result.weights_used == {"godel": 0.55, "te_entropy": 0.45, "backbone": 0.00}
    assert result.asset_type == "synthetic"
    # backbone_score=1.0 no debe influir -- peso 0.0 en synthetic
    assert result.gold_score == pytest.approx(0.55 * 0.5 + 0.45 * 0.5)


def test_gold_reconoce_activo_en_minuscula():
    result = compute_gold_score_bma(
        godel_score=0.5, te_score=0.5, backbone_score=0.5, asset="xau",
        entropy_shannon=0.3, p66_entropy=1.0,
    )
    assert result.asset_type == "native"


def test_gold_kill_por_godel_active_via_entropia_sobre_p90():
    result = compute_gold_score_bma(
        godel_score=0.9, te_score=0.9, backbone_score=0.9, asset="XAU",
        entropy_shannon=1.5, p66_entropy=1.2,
    )
    assert result.kill_signal is True
    assert result.kill_reason == GoldScoreKillReason.GODEL_ACTIVE
    assert result.gold_score == 0.0
    assert result.action == GoldScoreAction.HOLD
    assert result.regime == GoldScoreRegime.GODEL_ACTIVE_KILL


def test_gold_ya_no_mata_por_vitality_con_entropia_baja():
    """Versión 4.0.0. Antes este test se llamaba
    `test_gold_kill_por_godel_active_via_vitality_9` y pasaba: con
    entropía 0.1 y vitality 9, gold_score salía 0.0 con kill_signal.

    Ya no hay vía de vitality. Con entropía 0.1 y umbral 1.2 no dispara
    NINGUNA de las tres condiciones de kill, así que el score se calcula.

    Nota: bajo la fórmula del legacy ese escenario era imposible de todos
    modos -- `vitality == 9` significa `entropy > p66`, o sea que una
    entropía de 0.1 con umbral 1.2 nunca podría haber tenido vitality 9.
    El caso solo existía porque este repo calculaba vitality sobre
    n_events, donde entropía y vitality sí podían contradecirse."""
    result = compute_gold_score_bma(
        godel_score=0.9, te_score=0.9, backbone_score=0.9, asset="XAU",
        entropy_shannon=0.1, p66_entropy=1.2,
    )
    assert result.kill_signal is False
    assert result.kill_reason == GoldScoreKillReason.NONE
    assert result.gold_score > 0.0


def test_gold_kill_por_drift_control_kl_divergence():
    result = compute_gold_score_bma(
        godel_score=0.9, te_score=0.9, backbone_score=0.9, asset="XAU",
        entropy_shannon=0.1, p66_entropy=1.2,
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
        entropy_shannon=0.1, p66_entropy=1.2,
        kl_divergence=0.20,  # == threshold, no > threshold
    )
    assert result.kill_signal is False


def test_gold_godel_active_tiene_prioridad_sobre_drift_si_ambos_disparan():
    result = compute_gold_score_bma(
        godel_score=0.9, te_score=0.9, backbone_score=0.9, asset="XAU",
        entropy_shannon=1.5, p66_entropy=1.2,
        kl_divergence=0.99,
    )
    assert result.kill_reason == GoldScoreKillReason.GODEL_ACTIVE


def test_gold_regime_transcendence_cuando_godel_score_es_090_o_mas():
    result = compute_gold_score_bma(
        godel_score=0.95, te_score=0.9, backbone_score=0.9, asset="XAU",
        entropy_shannon=0.1, p66_entropy=1.2,
    )
    assert result.regime == GoldScoreRegime.TRANSCENDENCE
    assert result.action == GoldScoreAction.EXECUTE_STRONG


def test_gold_regime_creation_cuando_godel_score_bajo():
    result = compute_gold_score_bma(
        godel_score=0.1, te_score=0.1, backbone_score=0.1, asset="XAU",
        entropy_shannon=0.1, p66_entropy=1.2,
    )
    assert result.regime == GoldScoreRegime.CREATION
    assert result.action == GoldScoreAction.HOLD


def test_gold_action_execute_strong_en_el_borde_085():
    result = compute_gold_score_bma(
        godel_score=0.85, te_score=0.85, backbone_score=0.85, asset="XAU",
        entropy_shannon=0.1, p66_entropy=1.2,
    )
    assert result.gold_score == pytest.approx(0.85)
    assert result.action == GoldScoreAction.EXECUTE_STRONG


def test_gold_action_watch_en_el_borde_040():
    result = compute_gold_score_bma(
        godel_score=0.40, te_score=0.40, backbone_score=0.40, asset="XAU",
        entropy_shannon=0.1, p66_entropy=1.2,
    )
    assert result.gold_score == pytest.approx(0.40)
    assert result.action == GoldScoreAction.WATCH


def test_gold_clampea_inputs_fuera_de_rango():
    # godel_score=1.5 debe clampearse a 1.0 antes de ponderar
    result = compute_gold_score_bma(
        godel_score=1.5, te_score=0.0, backbone_score=0.0, asset="XAU",
        entropy_shannon=0.1, p66_entropy=1.2,
    )
    assert result.gold_score == pytest.approx(0.40)  # 0.40 * 1.0 clampeado


def test_gold_no_kill_por_defecto_sin_kl_divergence():
    result = compute_gold_score_bma(
        godel_score=0.5, te_score=0.5, backbone_score=0.5, asset="XAU",
        entropy_shannon=0.1, p66_entropy=1.2,
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
        entropy_shannon=0.5, p66_entropy=1.2,
    )
    assert godel_active(entropy_shannon=0.5, p66_entropy=1.2) is False  # confirma la premisa
    assert result.kill_signal is True
    assert result.kill_reason == GoldScoreKillReason.LEGACY_ENTROPY_THRESHOLD
    assert result.regime == GoldScoreRegime.HIGH_ENTROPY_LEGACY_KILL
    assert result.gold_score == 0.0


def test_gold_legacy_threshold_none_desactiva_la_red_de_seguridad():
    result = compute_gold_score_bma(
        godel_score=0.8, te_score=0.8, backbone_score=0.8, asset="XAU",
        entropy_shannon=0.5, p66_entropy=1.2,
        legacy_entropy_threshold=None,
    )
    assert result.kill_signal is False  # vuelve al comportamiento anterior


def test_gold_legacy_threshold_en_el_borde_no_dispara_es_estricto():
    result = compute_gold_score_bma(
        godel_score=0.5, te_score=0.5, backbone_score=0.5, asset="XAU",
        entropy_shannon=0.42, p66_entropy=1.2,  # == umbral, no >
    )
    assert result.kill_signal is False


def test_gold_godel_active_tiene_prioridad_sobre_legacy_threshold():
    # entropy=1.5 dispara AMBOS (godel_active por >=p90, y legacy por >0.42)
    result = compute_gold_score_bma(
        godel_score=0.8, te_score=0.8, backbone_score=0.8, asset="XAU",
        entropy_shannon=1.5, p66_entropy=1.2,
    )
    assert result.kill_reason == GoldScoreKillReason.GODEL_ACTIVE  # no LEGACY_ENTROPY_THRESHOLD


def test_gold_legacy_threshold_respeta_valor_personalizado():
    result = compute_gold_score_bma(
        godel_score=0.5, te_score=0.5, backbone_score=0.5, asset="XAU",
        entropy_shannon=0.35, p66_entropy=1.2,
        legacy_entropy_threshold=0.30,  # umbral más estricto que el default
    )
    assert result.kill_signal is True
    assert result.kill_reason == GoldScoreKillReason.LEGACY_ENTROPY_THRESHOLD


# ─── Benchmark A/B/C: legacy fijo vs. godel_active vs. producción ──────────
#
# Compara 3 lógicas de kill signal en 5 escenarios operacionales:
#   Caso A: umbral legacy fijo puro -- entropy_shannon > 0.42, sin nada más.
#   Caso B: godel_active() puro -- entropy > p66 (versión 4.0.0; era
#           `entropy >= p90 OR vitality == 9`).
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


def _caso_b_godel_puro(entropy: float, p66: float) -> bool:
    """Caso B -- llama a godel_active() real del módulo, sin wrapping.

    Perdió el parámetro `vitality` en la versión 4.0.0: la máscara dejó de
    ser un OR. El benchmark sigue teniendo sentido -- compara un umbral
    ABSOLUTO (A, 0.42 fijo) contra uno ADAPTATIVO (B, el tercil de su
    propia historia), y esa divergencia es la que motiva que C tenga
    ambos."""
    return godel_active(entropy, p66)


@pytest.mark.parametrize(
    "nombre, entropy, p66, kl, esperado_a, esperado_b, esperado_c, razon_c_esperada",
    [
        # 1: entropía alta en términos absolutos, pero el régimen reciente
        # fue tan volátil (p90=0.70) que 0.55 no llega a cruzarlo -- B
        # (adaptativo puro) queda ciego acá. Solo A y C, vía la red de
        # seguridad legacy, lo detienen.
        ("Ruido moderado alto", 0.55, 0.70, 0.02, True, False, True, "legacy_entropy_threshold"),
        # 2: entropía baja en términos absolutos (0.35 < 0.42, A no lo ve),
        # pero el régimen reciente fue MUY estable (p90=0.30) -- 0.35 sí
        # rompe ese percentil. B y C lo detectan, A no.
        ("Micro-ruptura de percentil local", 0.35, 0.30, 0.02, False, True, True, "godel_active"),
        # El escenario "Vitality Tesla 9 fuerza kill pese a entropía muy
        # baja" SE ELIMINÓ en la versión 4.0.0. Describía un caso que solo
        # era posible con vitality calculada sobre n_events: bajo la
        # definición del legacy, `vitality == 9` ES `entropy > p66`, así
        # que entropía muy baja y vitality 9 no pueden coexistir. El
        # escenario probaba una contradicción, no un caso límite.
        # 4: corregido -- ver nota arriba. KL=0.25 sí supera el umbral real.
        ("Drift severo, KL sobre el umbral real", 0.30, 0.50, 0.25, False, False, True, "drift_control"),
        ("Régimen nominal, ningún caso dispara", 0.25, 0.60, 0.01, False, False, False, "none"),
    ],
)
def test_benchmark_abc_casos_limite_donde_las_3_logicas_divergen(
    nombre, entropy, p66, kl, esperado_a, esperado_b, esperado_c, razon_c_esperada,
):
    a = _caso_a_legacy_fijo(entropy)
    b = _caso_b_godel_puro(entropy, p66)
    resultado_c = compute_gold_score_bma(
        godel_score=0.8, te_score=0.8, backbone_score=0.8, asset="XAU",
        entropy_shannon=entropy, p66_entropy=p66,
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
    assert _caso_b_godel_puro(0.55, p66=0.70) is False


def test_benchmark_abc_al_menos_un_escenario_donde_b_ve_y_a_no():
    # Confirma la divergencia inversa -- ninguno de los 2 domina al otro.
    assert _caso_a_legacy_fijo(0.35) is False
    assert _caso_b_godel_puro(0.35, p66=0.30) is True


def test_benchmark_abc_c_nunca_dispara_menos_que_a_o_b_por_separado():
    # C es el OR de los 3 mecanismos -- por construcción, en cualquier
    # escenario donde A o B disparan, C también debe disparar. Si esto
    # fallara, la síntesis de kill_signal tendría un caso donde perdió
    # cobertura respecto a sus propios componentes.
    escenarios = [
        (0.55, 0.70, 0.02), (0.35, 0.30, 0.02),
        (0.30, 0.50, 0.25), (0.25, 0.60, 0.01),
    ]
    for entropy, p66, kl in escenarios:
        a = _caso_a_legacy_fijo(entropy)
        b = _caso_b_godel_puro(entropy, p66)
        c = compute_gold_score_bma(
            0.8, 0.8, 0.8, "XAU", entropy_shannon=entropy, p66_entropy=p66, kl_divergence=kl,
        ).kill_signal
        if a or b:
            assert c is True, f"entropy={entropy} p66={p66}: A={a} B={b} pero C=False"


# ══════════════════════════════════════════════════════════════════════════
#  compute_godel_p90 -- percentil de ventana móvil (GODEL_CRITERIA_VERSION 2)
#
#  Los dos tests que sostienen todo lo demás son
#  `test_godel_p90_solo_mira_la_ventana_de_252_dias` y
#  `test_godel_p90_nunca_ve_el_dia_que_se_evalua`: ventana y
#  desplazamiento son las DOS mitades del contrato. Sin la ventana el
#  criterio vuelve a ser el acumulado que dejó 1.077 días sin muestra;
#  sin el desplazamiento hay fuga temporal y el número que salga no
#  significa nada.
# ══════════════════════════════════════════════════════════════════════════

# ─── La ventana: 252 observaciones, ni una más ────────────────────────────

def test_godel_p90_solo_mira_la_ventana_de_252_dias():
    """Historia vieja alta + ventana reciente baja: si el percentil viera
    más de 252 observaciones, la cola vieja lo arrastraría hacia arriba.
    Ese arrastre es exactamente lo que dejó 1.077 días recientes sin una
    sola muestra en BTC y XAU."""
    vieja = [10.0] * 3000            # 2015-2018, entropía alta
    reciente = [1.0] * GODEL_ROLLING_WINDOW_DAYS   # el último año

    p90 = compute_godel_p90(vieja + reciente, global_default=1.19)

    assert p90.n_obs == GODEL_ROLLING_WINDOW_DAYS
    assert p90.value == pytest.approx(1.0), (
        "la cola vieja se filtró en la ventana: el percentil la está "
        "arrastrando igual que el criterio acumulado")


def test_godel_p90_con_ventana_acumulada_daria_otro_numero():
    """Contraprueba del anterior: el mismo dato SIN recortar la ventana da
    un umbral inalcanzable. Si este test pasara a dar lo mismo que el
    anterior, es que la ventana dejó de aplicarse."""
    vieja = [10.0] * 3000
    reciente = [1.0] * GODEL_ROLLING_WINDOW_DAYS
    serie = vieja + reciente

    movil = compute_godel_p90(serie, global_default=1.19)
    acumulado = compute_adaptive_percentile(
        history=serie, percentile=90.0, global_default=1.19)

    assert acumulado.value > movil.value
    assert acumulado.value == pytest.approx(10.0)


def test_godel_p90_la_ventana_se_toma_del_final_no_del_principio():
    """Las últimas 252 observaciones, no las primeras. Un `[:window]` en
    vez de `[-window:]` pasa los tests de tamaño y falla acá."""
    historia = [float(i) for i in range(1000)]

    p90 = compute_godel_p90(historia, global_default=1.19)

    esperado = float(np.percentile(historia[-GODEL_ROLLING_WINDOW_DAYS:], 90))
    assert p90.value == pytest.approx(esperado)
    assert p90.value > 900, "está mirando el principio de la serie"


def test_godel_p90_la_ventana_avanza_con_el_dia():
    """Que sea MÓVIL: dos días distintos de la misma serie no comparten
    umbral cuando la serie se mueve."""
    serie = [float(i) for i in range(1000)]

    hoy = compute_godel_p90(serie[:600], global_default=1.19)
    manana = compute_godel_p90(serie[:601], global_default=1.19)

    assert manana.value > hoy.value, "la ventana está congelada"


def test_godel_p90_ventana_configurable_pero_el_default_es_252():
    assert GODEL_ROLLING_WINDOW_DAYS == 252

    historia = [float(i) for i in range(1000)]
    corta = compute_godel_p90(historia, global_default=1.19, window=50)

    assert corta.n_obs == 50
    assert compute_godel_p90(historia, global_default=1.19).n_obs == 252


def test_godel_p90_ventana_menor_a_uno_es_error_no_degradacion_silenciosa():
    """Una ventana vacía devolvería el default global todos los días. Eso
    se ve igual que un criterio conservador y no lo es: es un fallo
    silencioso."""
    with pytest.raises(ValueError, match="window"):
        compute_godel_p90([1.0] * 500, global_default=1.19, window=0)

    with pytest.raises(ValueError, match="window"):
        compute_godel_p90([1.0] * 500, global_default=1.19, window=-252)


# ─── El desplazamiento de un día: la otra mitad del contrato ──────────────

def test_godel_p90_la_ventana_termina_en_el_dia_anterior():
    """El contrato es que `entropy_history` NO incluye el día evaluado, y
    que la ventana TERMINA en el último día de esa historia -- el
    anterior al que se evalúa.

    Se verifica sobre el contenido exacto de la ventana y no sobre el
    valor del umbral a propósito: con 252 observaciones, un solo día
    mueve el P90 muy poco, así que una aserción numérica podría pasar
    con la frontera rota. La ventana es el contrato; el número es su
    consecuencia."""
    import core.scoring as scoring

    historia = [float(i) for i in range(1000)]   # todos distintos
    vistas: list[list[float]] = []
    real = scoring.compute_adaptive_percentile

    def espia(history, percentile, global_default, **kw):
        vistas.append(list(history))
        return real(history, percentile, global_default, **kw)

    scoring.compute_adaptive_percentile = espia
    try:
        compute_godel_p90(historia, global_default=1.19)
    finally:
        scoring.compute_adaptive_percentile = real

    (ventana,) = vistas
    assert ventana == historia[-GODEL_ROLLING_WINDOW_DAYS:]
    assert ventana[-1] == historia[-1], "no termina en el día anterior"
    assert max(ventana) == historia[-1], "la ventana mira hacia adelante"


def test_godel_p90_si_el_dia_entrara_en_su_ventana_el_umbral_cambiaria():
    """Por qué el desplazamiento es contrato y no un detalle. Se usa una
    ventana chica a propósito: es donde el efecto de UN día es visible en
    el número. Con 252 el efecto sigue existiendo, solo que diluido -- la
    frontera no depende de que se note."""
    historia = [1.0] * 40
    dia_atipico = 99.0

    sin_el_dia = compute_godel_p90(historia, global_default=1.19, window=10)
    con_el_dia = compute_godel_p90(historia + [dia_atipico],
                                   global_default=1.19, window=10)

    assert con_el_dia.value > sin_el_dia.value, (
        "el día propio contamina el umbral contra el que se lo compara: "
        "un día que se autoevalúa empuja su propia vara hacia arriba")
    assert godel_active(dia_atipico, sin_el_dia.value)


def test_godel_p90_el_desplazamiento_no_se_pierde_con_ventana_llena():
    """El caso que importa en producción: con más de 252 días de historia
    la ventana sigue siendo las 252 anteriores, sin sumar el día propio."""
    historia = [float(i) for i in range(1000)]

    sin_el_dia = compute_godel_p90(historia, global_default=1.19)
    con_el_dia = compute_godel_p90(historia + [1000.0], global_default=1.19)

    assert sin_el_dia.n_obs == con_el_dia.n_obs == GODEL_ROLLING_WINDOW_DAYS
    assert con_el_dia.value > sin_el_dia.value, (
        "la ventana no avanzó al agregar un día")


# ─── Warm-up: ventana expandible con lag de un día ────────────────────────

def test_godel_p90_en_warmup_usa_toda_la_historia_disponible():
    """Para los primeros 252 días no hay 252 observaciones previas. La
    ventana es todo lo que haya (0...t-1) -- de facto el criterio
    ACUMULADO, documentado como tal. La alternativa sería tomar
    observaciones del futuro."""
    for t in (5, 50, 150, 251):
        p90 = compute_godel_p90([float(i) for i in range(t)],
                                global_default=1.19)
        assert p90.n_obs == t, f"día {t}: la ventana debería ser toda la historia"


def test_godel_p90_el_warmup_termina_exactamente_en_252():
    historia = [float(i) for i in range(400)]

    assert compute_godel_p90(historia[:251], global_default=1.19).n_obs == 251
    assert compute_godel_p90(historia[:252], global_default=1.19).n_obs == 252
    assert compute_godel_p90(historia[:253], global_default=1.19).n_obs == 252


def test_godel_p90_el_warmup_se_acopla_al_fallback_existente():
    """No se duplica la lógica de arranque en frío: los cortes de 10 y 100
    observaciones siguen siendo los de compute_adaptive_percentile."""
    assert compute_godel_p90([1.0] * 5, global_default=1.19).source is PercentileSource.GLOBAL
    assert compute_godel_p90([1.0] * 50, global_default=1.19).source is PercentileSource.HYBRID
    assert compute_godel_p90([1.0] * 150, global_default=1.19).source is PercentileSource.ROLLING

    assert compute_godel_p90([1.0] * MIN_OBS_FOR_HYBRID,
                             global_default=1.19).source is PercentileSource.HYBRID
    assert compute_godel_p90([1.0] * MIN_OBS_FOR_ROLLING,
                             global_default=1.19).source is PercentileSource.ROLLING


def test_godel_p90_sin_historia_es_el_default_global_no_un_error():
    """Cold start: cero historia es un caso VÁLIDO, igual que en el resto
    del módulo. No se inventa un umbral y no se lanza."""
    assert compute_godel_p90(None, global_default=1.19).value == 1.19
    assert compute_godel_p90([], global_default=1.19).value == 1.19
    assert compute_godel_p90([], global_default=1.19).source is PercentileSource.GLOBAL


# ─── Reusar, no reimplementar ─────────────────────────────────────────────

def test_godel_p90_llama_a_compute_adaptive_percentile_no_la_reimplementa():
    """Port, don't rewrite. Si alguien copiara la lógica del percentil acá,
    habría dos implementaciones que pueden divergir en silencio."""
    import core.scoring as scoring

    vistas = []
    real = scoring.compute_adaptive_percentile

    def espia(history, percentile, global_default, **kw):
        vistas.append((list(history), percentile, global_default))
        return real(history, percentile, global_default, **kw)

    original = scoring.compute_adaptive_percentile
    scoring.compute_adaptive_percentile = espia
    try:
        compute_godel_p90([float(i) for i in range(500)], global_default=1.19)
    finally:
        scoring.compute_adaptive_percentile = original

    assert len(vistas) == 1, "no llamó a compute_adaptive_percentile"
    historia_vista, percentil, default = vistas[0]
    assert percentil == 90.0
    assert default == 1.19
    assert len(historia_vista) == GODEL_ROLLING_WINDOW_DAYS


def test_godel_p90_devuelve_el_mismo_tipo_que_el_percentil_adaptativo():
    """Mismo contrato de retorno: quien ya consumía value/source/n_obs no
    tiene que cambiar nada."""
    r = compute_godel_p90([1.0] * 500, global_default=1.19)
    assert isinstance(r, AdaptivePercentileResult)
    assert r.source in tuple(PercentileSource)


# ─── La máscara NO cambió ─────────────────────────────────────────────────

def test_godel_active_tiene_la_firma_de_un_filtro_de_un_solo_termino():
    """La versión 4.0.0 SÍ cambia la firma -- es el punto del cambio. Este
    test la fija en su forma nueva: (entropía, umbral) -> bool. Y fija que
    el parámetro no vuelva a llamarse p90: ese nombre describía un término
    que nunca cambió un resultado."""
    import inspect

    firma = inspect.signature(godel_active)
    assert list(firma.parameters) == ["entropy_shannon", "p66_entropy"]
    assert isinstance(godel_active(1.0, 0.5), bool)

    assert godel_active(1.0, 0.5) is True     # sobre el tercil superior
    assert godel_active(0.1, 0.5) is False    # debajo
    assert godel_active(0.5, 0.5) is False    # en el borde: estricto


# ─── Versionado del criterio ──────────────────────────────────────────────

def test_existe_version_del_criterio_y_dice_ventana_movil_de_252():
    """Un artefacto persistido con el criterio anterior tiene que poder
    DETECTARSE. Sin una versión, un p90 acumulado y uno móvil son dos
    floats indistinguibles."""
    assert GODEL_CRITERIA_VERSION == "4.0.0-entropy_state_p66"
    # Mayor, no menor: la 4.0.0 cambia de qué serie sale el umbral (tercil
    # de entropía, no de n_events), así que un artefacto 3.x no es
    # comparable con uno 4.x.
    assert GODEL_CRITERIA_VERSION.startswith("4.")
    assert str(int(GODEL_MASK_PERCENTILE)) in GODEL_CRITERIA_VERSION


def test_la_version_no_entra_en_el_retorno_de_godel_active():
    """Deliberado: godel_active devuelve bool. Meterle la versión al
    retorno le cambiaría la firma a todos sus llamadores, que es
    exactamente lo que este PR no hace."""
    assert godel_active(1.0, 0.5) is True
    assert not isinstance(godel_active(1.0, 0.5), tuple)


# ─── Fidelidad del port contra tools/measure_godel_samples.py ─────────────

def test_el_port_reproduce_dia_a_dia_el_modo_movil_del_tool():
    """La medición que motivó este cambio (BTC 611, XAU 398) salió del modo
    MOVIL del tool. Si el port no produce EXACTAMENTE los mismos umbrales,
    esos números no aplican a producción.

    No hay datos reales de GDELT/OHLCV en el entorno de test, así que la
    fidelidad se prueba donde de verdad vive: umbral por umbral sobre la
    misma serie, cubriendo warm-up y régimen de ventana llena."""
    from datetime import date, timedelta

    import tools.measure_godel_samples as tool

    rng = np.random.default_rng(11)
    n = 800
    entropy = np.array([1.30 - 0.0002 * i + rng.normal(0, 0.10)
                        for i in range(n)])
    serie = tool.SerieDerivada(
        fechas=[date(2020, 1, 1) + timedelta(days=k) for k in range(n)],
        entropy=entropy,
        vitality=np.array([3] * n, dtype=int),
        log_return=np.array([0.01] * n, dtype=float),
        forward_filled=np.array([False] * n, dtype=bool),
    )

    del_tool = tool.resolver_umbrales(
        serie, 1, n, mode=tool.PercentileMode.MOVIL,
        window=GODEL_ROLLING_WINDOW_DAYS, umbral_global_default=1.19,
    )

    for i in range(1, n):
        # compute_godel_p66 y no p90: desde la versión 4.0.0 el tool mide
        # el percentil de la máscara (GODEL_MASK_PERCENTILE), que es el que
        # usa producción. Un tool que midiera otro percentil mediría otra
        # cosa sin que se note.
        del_core = compute_godel_p66(list(entropy[:i]), global_default=1.19)
        assert del_core.value == pytest.approx(del_tool.por_dia[i], abs=1e-12), (
            f"día {i}: el port no reproduce el modo MOVIL del tool")


# ══════════════════════════════════════════════════════════════════════════
#  Ventana móvil en el tercil de vitality_tesla (versión 3.0.0)
#
#  El test que separa los dos criterios es
#  `test_un_dia_tipico_del_ultimo_ano_ya_no_se_compara_contra_hace_diez`.
#  Con el cálculo anterior falla. La suite NO tenía ningún test que
#  distinguiera los dos criterios: al hacer el cambio, el único que se
#  puso en rojo fue el pin literal de GODEL_CRITERIA_VERSION -- ninguno
#  miraba el tercil.
# ══════════════════════════════════════════════════════════════════════════

#: Ventana "sin recorte" para expresar el criterio VIEJO en los tests sin
#: reimplementarlo: una ventana más larga que cualquier fixture equivale a
#: no recortar. Así el contraste se hace con la función real y no con una
#: copia del cálculo anterior que podría divergir de él.
_SIN_RECORTE = 10**9


def _rampa_creciente(n: int, base: float = 50.0, paso: float = 0.75) -> list[float]:
    """n_events con tendencia creciente -- la forma que produce el defecto.

    Determinista, sin ruido: la tendencia es lo que hace divergir los dos
    criterios, y un test que dependa de un `default_rng` para separarlos
    sería frágil por una razón que no tiene que ver con lo que mide."""
    return [base + paso * i for i in range(n)]


# ─── EL TEST QUE SEPARA LOS DOS CRITERIOS ─────────────────────────────────

def test_un_dia_tipico_del_ultimo_ano_ya_no_se_compara_contra_hace_diez():
    """El defecto exacto, con la forma exacta que tiene en los datos
    reales: n_events crece, y un día con cobertura MEDIANA para el último
    año queda por encima del p66 de toda la historia previa porque esa
    historia incluye el volumen de hace diez años.

    Ese día se etiquetaba 9 -- vitalidad máxima -- siendo perfectamente
    normal para su época. Con la ventana móvil cae en 6, que es lo que un
    tercil bien calculado tiene que decir de un valor mediano.

    Con el cálculo anterior este test falla."""
    historia = _rampa_creciente(600)
    # "Hoy": la mediana del último año. Ni pico ni valle.
    hoy = float(np.median(historia[-GODEL_ROLLING_WINDOW_DAYS:]))
    ventana = historia + [hoy]

    viejo = compute_vitality_tesla(ventana, None, 0.5,
                                   n_events_rolling_window=_SIN_RECORTE)
    nuevo = compute_vitality_tesla(ventana, None, 0.5)

    assert viejo.value == 9, (
        "la premisa del escenario no se cumple: sin recorte el día mediano "
        "tiene que salir 9, que es el defecto que se está corrigiendo")
    assert nuevo.value == 6, (
        f"un día mediano para su ventana debe caer en 6, salió {nuevo.value}")
    # Los dos por el nivel primario: esto no es un efecto de degradación.
    assert viejo.tier_used is VitalityTier.PRIMARY_N_EVENTS
    assert nuevo.tier_used is VitalityTier.PRIMARY_N_EVENTS


def test_la_ventana_movil_baja_la_tasa_de_nueves_pero_no_la_lleva_a_un_tercio():
    """Cuánto corrige, medido, y hasta dónde -- que no es hasta el 33%.

    La ventana móvil elimina la deriva que queda FUERA de la ventana. La
    que ocurre DENTRO de los 252 días sigue empujando al día actual hacia
    arriba, así que un tercil sobre una serie con tendencia sigue dando
    más de un tercio de nueves. Es una corrección grande, no completa, y
    decirlo acá evita que alguien lea el cambio como una garantía de 33%
    que no es."""
    rng = np.random.default_rng(7)
    n = 1200
    serie = [max(1.0, 50.0 + 0.05 * i + rng.normal(0, 25.0)) for i in range(n)]

    def tasa_de_nueves(window: int) -> float:
        nueves = evaluados = 0
        for i in range(300, n):     # después del warm-up
            nueves += compute_vitality_tesla(
                serie[:i + 1], None, 0.5,
                n_events_rolling_window=window).value == 9
            evaluados += 1
        return 100.0 * nueves / evaluados

    viejo = tasa_de_nueves(_SIN_RECORTE)
    nuevo = tasa_de_nueves(GODEL_ROLLING_WINDOW_DAYS)

    assert viejo > 55.0, f"la fixture no reproduce la inflación: {viejo:.1f}%"
    assert nuevo < viejo - 10.0, (
        f"la ventana móvil tiene que bajar la tasa de forma clara: "
        f"{viejo:.1f}% -> {nuevo:.1f}%")
    # Y la parte honesta: sigue por encima de un tercio.
    assert nuevo > 33.0, (
        "si esto alguna vez baja de 33%, la corrección hace más de lo que "
        "este PR dice que hace y hay que volver a medir, no relajar el test")


# ─── La ventana ───────────────────────────────────────────────────────────

def test_el_tercil_solo_mira_las_ultimas_252_observaciones():
    """Historia vieja baja + ventana reciente alta: si el tercil viera más
    de 252 observaciones, la cola vieja bajaría los umbrales y cualquier
    día reciente saldría 9."""
    vieja = [1.0] * 3000
    reciente = [500.0] * GODEL_ROLLING_WINDOW_DAYS

    assert compute_vitality_tesla(vieja + reciente, None, 0.5,
                                  n_events_rolling_window=_SIN_RECORTE).value == 9
    # Dentro de su propia ventana, 500 es el valor de todos: cae en 3.
    assert compute_vitality_tesla(vieja + reciente, None, 0.5).value == 3


def test_el_tercil_toma_la_ventana_del_final_no_del_principio():
    """Un `[:window]` en vez de `[-window:]` pasa cualquier test de tamaño
    y falla acá."""
    historia = _rampa_creciente(1000)
    ventana_esperada = historia[-GODEL_ROLLING_WINDOW_DAYS:]

    resultado = compute_vitality_tesla(historia, None, 0.5)
    p33 = float(np.percentile(ventana_esperada, 33))
    p66 = float(np.percentile(ventana_esperada, 66))
    esperado = 3 if historia[-1] <= p33 else (6 if historia[-1] <= p66 else 9)

    assert resultado.value == esperado
    # Contra el principio de la serie daría otra cosa.
    assert p33 > float(np.percentile(historia[:GODEL_ROLLING_WINDOW_DAYS], 33))


def test_la_ventana_del_tercil_es_configurable_y_por_defecto_252():
    historia = _rampa_creciente(1000)

    por_defecto = compute_vitality_tesla(historia, None, 0.5)
    explicito = compute_vitality_tesla(
        historia, None, 0.5, n_events_rolling_window=GODEL_ROLLING_WINDOW_DAYS)

    assert por_defecto.value == explicito.value


def test_ventana_del_tercil_menor_a_uno_es_error():
    with pytest.raises(ValueError, match="window"):
        compute_vitality_tesla([10.0] * 500, None, 0.5,
                               n_events_rolling_window=0)


# ─── Warm-up ──────────────────────────────────────────────────────────────

def test_en_warmup_el_tercil_usa_toda_la_historia_igual_que_antes():
    """Con menos de 252 observaciones la ventana ES toda la historia, así
    que el cambio no toca ninguno de esos días. Es también la razón por la
    que ningún test corto podía distinguir los dos criterios."""
    for n in (3, 10, 100, GODEL_ROLLING_WINDOW_DAYS - 1):
        historia = _rampa_creciente(n)
        assert (compute_vitality_tesla(historia, None, 0.5).value
                == compute_vitality_tesla(
                    historia, None, 0.5,
                    n_events_rolling_window=_SIN_RECORTE).value), (
            f"con {n} observaciones los dos criterios tienen que coincidir")


def test_la_frontera_del_warmup_esta_en_252():
    historia = _rampa_creciente(400)

    def coinciden(n):
        h = historia[:n]
        return (compute_vitality_tesla(h, None, 0.5).value
                == compute_vitality_tesla(
                    h, None, 0.5, n_events_rolling_window=_SIN_RECORTE).value)

    assert coinciden(GODEL_ROLLING_WINDOW_DAYS), "en 252 todavía coinciden"


def test_el_recorte_no_puede_romper_el_piso_de_tres_puntos():
    """MIN_WINDOW_FOR_PERCENTILE sigue mandando: el recorte solo achica
    hacia 252, que es mucho mayor que 3, así que nunca puede dejar al
    nivel primario con menos puntos de los que exige."""
    assert GODEL_ROLLING_WINDOW_DAYS > MIN_WINDOW_FOR_PERCENTILE

    con_tres = compute_vitality_tesla([10.0, 50.0, 90.0], None, 0.5)
    assert con_tres.tier_used is VitalityTier.PRIMARY_N_EVENTS

    con_dos = compute_vitality_tesla([10.0, 50.0], [0.1, 0.2, 0.3], 0.5)
    assert con_dos.tier_used is VitalityTier.FALLBACK_ENTROPY_ROLLING


# ─── Lo que este cambio NO toca ───────────────────────────────────────────

def test_la_ventana_del_tercil_sigue_incluyendo_el_dia_actual():
    """DELIBERADO, no un olvido: el P90 de entropía usa la ventana previa
    SIN el día evaluado; `n_events_window` es auto-referencial por diseño
    heredado del legacy. Este PR cambia el TAMAÑO de la ventana, no su
    semántica. Si algún día se saca el día propio, que sea una decisión
    explícita que rompa este test, no un efecto colateral."""
    # El último elemento ES el día evaluado: cambiarlo cambia el resultado.
    base = [10.0] * 300
    assert compute_vitality_tesla(base + [10.0], None, 0.5).value == 3
    assert compute_vitality_tesla(base + [9999.0], None, 0.5).value == 9


def test_el_respaldo_a_no_recibe_ventana_movil():
    """Pendiente explícito, no un descuido: el tercil de entropía del
    Respaldo A tiene la misma exposición estructural, y queda sin tocar
    porque la medición lo pone en 2 de 4.880 días. Este test fija que hoy
    NO se le aplica recorte -- si alguien se lo agrega, que sea con su
    propia medición."""
    entropia_vieja = [0.1] * 3000
    entropia_reciente = [0.9] * GODEL_ROLLING_WINDOW_DAYS

    r = compute_vitality_tesla(
        None, entropia_vieja + entropia_reciente, current_entropy=0.5,
        n_events_rolling_window=GODEL_ROLLING_WINDOW_DAYS)

    assert r.tier_used is VitalityTier.FALLBACK_ENTROPY_ROLLING
    # Si A recortara a las últimas 252 (todas 0.9), 0.5 caería en 3.
    # Sin recortar, la historia está dominada por 0.1 y 0.5 sale 9.
    assert r.value == 9, "el Respaldo A dejó de usar toda su ventana"


def test_la_cascada_y_su_orden_de_degradacion_no_cambiaron():
    """B -> A -> C, en ese orden, con los mismos disparadores."""
    assert compute_vitality_tesla([10.0, 50.0, 90.0], [0.1] * 10, 0.5
                                  ).tier_used is VitalityTier.PRIMARY_N_EVENTS
    assert compute_vitality_tesla(None, [0.1, 0.5, 0.9], 0.5
                                  ).tier_used is VitalityTier.FALLBACK_ENTROPY_ROLLING
    assert compute_vitality_tesla(None, None, 0.5
                                  ).tier_used is VitalityTier.FALLBACK_GLOBAL_THRESHOLDS


def test_los_umbrales_del_tercil_siguen_siendo_33_y_66():
    """Se cambió qué observaciones entran al percentil, no qué percentil se
    pide ni la regla de comparación (<=, no <)."""
    uniforme = [5.0] * 500
    assert compute_vitality_tesla(uniforme, None, 0.5).value == 3

    ventana = list(range(1, 101)) * 3      # 300 puntos, 1..100 repetidos
    p33 = float(np.percentile(ventana[-GODEL_ROLLING_WINDOW_DAYS:], 33))
    p66 = float(np.percentile(ventana[-GODEL_ROLLING_WINDOW_DAYS:], 66))
    assert 0 < p33 < p66 < 100


# ─── Reusar, no reimplementar ─────────────────────────────────────────────

def test_las_dos_ramas_recortan_con_el_mismo_mecanismo():
    """Port, don't rewrite. Si el tercil copiara el `[-window:]` de
    compute_godel_p90 habría dos recortes paralelos que pueden divergir en
    silencio. Se verifica sobre el AST y no sobre el texto: un docstring
    que mencione `_ventana_movil` no cuenta como llamarla."""
    import ast
    import inspect

    import core.scoring as scoring

    fuente = inspect.getsource(scoring)
    arbol = ast.parse(fuente)

    def llama_a_ventana_movil(nombre_funcion: str) -> bool:
        fn = next(n for n in ast.walk(arbol)
                  if isinstance(n, ast.FunctionDef) and n.name == nombre_funcion)
        return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == "_ventana_movil"
                   for n in ast.walk(fn))

    assert llama_a_ventana_movil("compute_godel_p90")
    assert llama_a_ventana_movil("compute_vitality_tesla")


def test_una_sola_constante_de_ventana_para_las_dos_ramas():
    """Dos ventanas distintas obligarían a justificar por qué difieren."""
    import inspect

    from core.scoring import _ventana_movil

    firma_vitality = inspect.signature(compute_vitality_tesla)
    firma_p90 = inspect.signature(compute_godel_p90)

    assert (firma_vitality.parameters["n_events_rolling_window"].default
            == firma_p90.parameters["window"].default
            == GODEL_ROLLING_WINDOW_DAYS)
    assert _ventana_movil is not None


def test_ventana_movil_es_solo_el_recorte():
    """La función compartida no hace nada más que recortar: si algún día
    empieza a filtrar, ordenar o rellenar, las dos ramas heredan ese
    cambio sin pedirlo."""
    from core.scoring import _ventana_movil

    assert _ventana_movil([1.0, 2.0, 3.0, 4.0], 2) == [3.0, 4.0]
    assert _ventana_movil([1.0, 2.0], 10) == [1.0, 2.0]
    assert _ventana_movil([], 5) == []
    assert _ventana_movil(None, 5) == []
    # Ni ordena ni deduplica: devuelve la cola tal cual.
    assert _ventana_movil([3.0, 1.0, 2.0, 1.0], 3) == [1.0, 2.0, 1.0]

    with pytest.raises(ValueError, match="window"):
        _ventana_movil([1.0, 2.0], 0)


# ══════════════════════════════════════════════════════════════════════════
#  entropy_state -- la Capa 1 (GODEL_CRITERIA_VERSION 4.0.0)
#
#  Los dos que sostienen el resto:
#    · `test_entropy_state_no_mira_el_dia_que_clasifica` -- integridad
#      temporal, verificada sobre la VENTANA recibida y no sobre el valor.
#    · `test_la_distribucion_no_se_concentra_en_un_solo_estado` -- la
#      prueba que habría detectado el 63%->11% de la implementación con
#      n_events antes de que llegara a producción.
# ══════════════════════════════════════════════════════════════════════════

# ─── Integridad temporal ──────────────────────────────────────────────────

def test_entropy_state_no_mira_el_dia_que_clasifica(monkeypatch):
    """Se inspecciona la VENTANA que recibe np.percentile, no el estado que
    sale. Un test sobre el valor podría pasar con la frontera rota: con 252
    observaciones un día mueve el percentil muy poco, así que el número
    seguiría cayendo en el mismo tercil casi siempre."""
    import core.scoring as scoring

    n = 400
    # Todos distintos y crecientes: el día que se clasifica es el máximo, y
    # si entrara en su ventana se vería sin ambigüedad.
    serie = [float(i) for i in range(n)]

    ventanas: list[list[float]] = []
    real = scoring.np.percentile

    def espia(a, q, *args, **kw):
        ventanas.append(list(a))
        return real(a, q, *args, **kw)

    monkeypatch.setattr(scoring.np, "percentile", espia)
    entropy_state(serie)

    assert ventanas, "no llamó a np.percentile"
    for ventana in ventanas:
        assert serie[-1] not in ventana, "el día propio entró en su ventana"
        assert max(ventana) == serie[-2], "la ventana no termina el día anterior"
        assert len(ventana) == GODEL_ROLLING_WINDOW_DAYS


def test_entropy_state_usa_las_ultimas_252_no_toda_la_historia(monkeypatch):
    import core.scoring as scoring

    serie = [float(i) for i in range(2000)]
    ventanas: list[list[float]] = []
    real = scoring.np.percentile
    monkeypatch.setattr(
        scoring.np, "percentile",
        lambda a, q, *ar, **kw: (ventanas.append(list(a)), real(a, q, *ar, **kw))[1])

    entropy_state(serie)

    for ventana in ventanas:
        assert ventana == serie[-1 - GODEL_ROLLING_WINDOW_DAYS:-1]


# ─── Distribución: la prueba que habría detectado el 63%->11% ─────────────

def test_la_distribucion_no_se_concentra_en_un_solo_estado():
    """Un tercil sobre una serie CON DERIVA tiene que seguir repartiendo.
    La implementación con n_events daba entre 63% y 11% de días en un solo
    valor según el año; este test la habría puesto en rojo.

    EL WARM-UP SE EXCLUYE, y esa exclusión es parte del test: los primeros
    252 días no tienen ventana completa y `entropy_state` los devuelve como
    ENTROPY_STATE_WARMUP (None), no como un estado. Contarlos metería días
    sin medir dentro de una distribución de días medidos."""
    rng = np.random.default_rng(3)
    n = 1500
    # Deriva descendente, la forma real de la entropía GDELT.
    serie = [1.30 - 0.0002 * i + rng.normal(0, 0.10) for i in range(n)]

    estados = [entropy_state(serie[:i + 1]) for i in range(n)]

    # La exclusión, explícita y verificada -- no un filtro silencioso.
    warmup = [e for e in estados if e is ENTROPY_STATE_WARMUP]
    medidos = [e for e in estados if e is not ENTROPY_STATE_WARMUP]
    assert len(warmup) == GODEL_ROLLING_WINDOW_DAYS
    assert ENTROPY_STATE_WARMUP not in medidos, (
        "un día sin ventana se coló en la distribución")
    assert len(medidos) == n - GODEL_ROLLING_WINDOW_DAYS

    conteo = {e: medidos.count(e) for e in (ENTROPY_STATE_LOW,
                                           ENTROPY_STATE_MID,
                                           ENTROPY_STATE_HIGH)}
    total = len(medidos)
    for estado, veces in conteo.items():
        fraccion = 100.0 * veces / total
        assert fraccion <= 60.0, (
            f"el estado {estado} concentra {fraccion:.1f}% de los días: un "
            f"tercil no puede quedar así ni con deriva")
        assert fraccion >= 15.0, (
            f"el estado {estado} aparece solo {fraccion:.1f}%: el tercil "
            f"dejó de repartir")


def test_sobre_una_serie_estacionaria_los_tres_estados_rondan_un_tercio():
    """Sin deriva, un tercil bien calculado tiene que dar ~33% cada uno.
    Es la contraprueba del anterior: fija que los límites de 60/15 no son
    laxos porque el cálculo esté mal, sino porque la deriva desbalancea."""
    rng = np.random.default_rng(11)
    n = 2000
    serie = list(rng.normal(1.0, 0.2, n))

    medidos = [e for e in (entropy_state(serie[:i + 1]) for i in range(n))
               if e is not ENTROPY_STATE_WARMUP]

    for estado in (ENTROPY_STATE_LOW, ENTROPY_STATE_MID, ENTROPY_STATE_HIGH):
        fraccion = 100.0 * medidos.count(estado) / len(medidos)
        assert 25.0 <= fraccion <= 42.0, (
            f"estado {estado}: {fraccion:.1f}%, lejos de un tercio")


# ─── Warm-up: None, no un estado ──────────────────────────────────────────

def test_sin_ventana_completa_no_hay_estado():
    """`None`, no 0 ni 1. Un día de warm-up no está "bajo": no tiene estado,
    porque no hay contra qué compararlo. Devolver un número mezclaría días
    medidos con días adivinados en la misma columna."""
    assert ENTROPY_STATE_WARMUP is None

    for n in (0, 1, 2, 50, GODEL_ROLLING_WINDOW_DAYS):
        serie = [float(i) for i in range(n)]
        assert entropy_state(serie) is ENTROPY_STATE_WARMUP, (
            f"con {n} observaciones no puede haber estado")

    assert entropy_state(None) is ENTROPY_STATE_WARMUP
    assert entropy_state([]) is ENTROPY_STATE_WARMUP


def test_la_frontera_del_warmup_esta_en_252_dias_previos():
    serie = [float(i) for i in range(400)]

    # 252 elementos = 251 previos + el día: falta uno.
    assert entropy_state(serie[:GODEL_ROLLING_WINDOW_DAYS]) is ENTROPY_STATE_WARMUP
    # 253 elementos = 252 previos + el día: alcanza.
    assert entropy_state(serie[:GODEL_ROLLING_WINDOW_DAYS + 1]) is not ENTROPY_STATE_WARMUP


def test_ventana_menor_a_uno_es_error():
    with pytest.raises(ValueError, match="window"):
        entropy_state([1.0] * 500, window=0)


# ─── Port del legacy ──────────────────────────────────────────────────────

def test_los_terciles_son_los_del_legacy_33_y_66():
    """gdelt_foundation.py::TESLA_PERCENTILE_THRESHOLDS = (33.0, 66.0)."""
    assert ENTROPY_STATE_PERCENTILES == (33.0, 66.0)
    assert GODEL_MASK_PERCENTILE == 66.0


def test_la_comparacion_es_menor_o_igual_como_el_legacy():
    """`3 if e <= p33 else (6 if e <= p66 else 9)`. El valor que cae
    exactamente sobre p33 es BAJO, no medio."""
    # 252 previos constantes en 1.0 -> p33 == p66 == 1.0.
    previos = [1.0] * GODEL_ROLLING_WINDOW_DAYS

    assert entropy_state(previos + [1.0]) is ENTROPY_STATE_LOW   # e <= p33
    assert entropy_state(previos + [1.1]) is ENTROPY_STATE_HIGH  # e > p66


def test_el_estado_reproduce_la_formula_del_legacy_sobre_su_ventana():
    """Port verificado contra la fórmula literal, no contra una idea de
    ella: se replica `quantile(0.33)/quantile(0.66)` sobre la misma ventana
    y se compara estado por estado."""
    rng = np.random.default_rng(5)
    n = 600
    serie = list(rng.normal(1.0, 0.25, n))

    for i in range(GODEL_ROLLING_WINDOW_DAYS + 1, n):
        ventana = serie[i - GODEL_ROLLING_WINDOW_DAYS:i]
        e = serie[i]
        p33 = float(np.percentile(ventana, 33.0))
        p66 = float(np.percentile(ventana, 66.0))
        esperado = (ENTROPY_STATE_LOW if e <= p33
                    else ENTROPY_STATE_MID if e <= p66
                    else ENTROPY_STATE_HIGH)
        assert entropy_state(serie[:i + 1]) == esperado, f"día {i}"


def test_entropy_state_no_decide_nada_de_trading():
    """Función pura: mismos datos, mismo resultado, sin efectos. Si algún
    día devuelve algo que se parece a una orden, la separación que este PR
    introduce se perdió."""
    serie = [float(i) for i in range(400)]
    copia = list(serie)

    a = entropy_state(serie)
    b = entropy_state(serie)

    assert a == b
    assert serie == copia, "mutó su entrada"
    assert a in (ENTROPY_STATE_LOW, ENTROPY_STATE_MID, ENTROPY_STATE_HIGH)


# ─── El filtro pregunta lo que la Capa 1 responde ─────────────────────────

def test_godel_active_es_exactamente_preguntar_si_el_estado_es_alto():
    """LA EQUIVALENCIA QUE HACE COHERENTE LA SEPARACIÓN. La Capa 1 devuelve
    el estado; el filtro pregunta si es HIGH. Si estas dos formas dejaran
    de coincidir, habría dos definiciones del tercil superior y el sistema
    podría operar con una mientras se reporta la otra."""
    rng = np.random.default_rng(7)
    n = 800
    serie = list(rng.normal(1.0, 0.25, n))

    comprobados = 0
    for i in range(GODEL_ROLLING_WINDOW_DAYS + 1, n):
        historia = serie[:i]          # sin el día i
        estado = entropy_state(serie[:i + 1])
        umbral = compute_godel_p66(historia, global_default=1.19).value

        assert (estado is ENTROPY_STATE_HIGH) == godel_active(serie[i], umbral), (
            f"día {i}: estado={estado} pero el filtro dice "
            f"{godel_active(serie[i], umbral)}")
        comprobados += 1

    assert comprobados > 500, "el barrido no cubrió suficientes días"


def test_compute_godel_p66_pide_el_percentil_de_la_mascara():
    """Una sola fuente de verdad para el percentil: si alguien cambia
    GODEL_MASK_PERCENTILE, el umbral lo sigue."""
    import core.scoring as scoring

    pedidos = []
    real = scoring.compute_adaptive_percentile

    def espia(history, percentile, global_default, **kw):
        pedidos.append(percentile)
        return real(history, percentile, global_default, **kw)

    original = scoring.compute_adaptive_percentile
    scoring.compute_adaptive_percentile = espia
    try:
        compute_godel_p66([float(i) for i in range(500)], global_default=1.19)
    finally:
        scoring.compute_adaptive_percentile = original

    assert pedidos == [GODEL_MASK_PERCENTILE]


def test_compute_godel_p66_recorta_con_el_mecanismo_compartido():
    """Port, don't rewrite: el mismo `_ventana_movil` que usan las otras
    ramas, verificado sobre el AST."""
    import ast
    import inspect

    import core.scoring as scoring

    arbol = ast.parse(inspect.getsource(scoring))
    fn = next(n for n in ast.walk(arbol)
              if isinstance(n, ast.FunctionDef) and n.name == "compute_godel_p66")

    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "_ventana_movil" for n in ast.walk(fn))
