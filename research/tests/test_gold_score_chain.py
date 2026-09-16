"""
research/tests/test_gold_score_chain.py
=========================================
Los tests de la cadena gold_score, movidos con el código el 16-sep-2026.

SON LOS MISMOS TESTS. No se reescribió ninguno, no se relajó ningún assert
y no se borró ninguno -- lo único que cambió es de dónde vienen los
imports. Que pasen sin editarlos es la prueba de que el retiro fue una
mudanza y no una reescritura: si hubiera hecho falta tocar un assert, el
código habría cambiado de comportamiento al moverse.

Incluye el benchmark A/B/C completo (helpers `_caso_a_legacy_fijo` y
`_caso_b_godel_puro` incluidos) y NO porque los cuatro tests toquen la
cadena -- dos de ellos solo llaman a los helpers. Viaja entero porque su
"Caso C" ES `compute_gold_score_bma`: partirlo dejaría en `tests/` un
benchmark que compara dos casos contra un tercero que ya no está ahí.

`godel_active` se importa de `core.scoring` y no de `research`: se quedó en
el motor a propósito, porque sigue siendo la máscara que el ciclo diario
evalúa todos los días.

CI: estos tests NO corren en el job que bloquea un merge -- ver
`research/__init__.py`. A mano: `pytest research/tests/ -q`.
"""

from __future__ import annotations

import pytest

from core.scoring import godel_active
from research.gold_score_chain import (
    BMA_WEIGHTS,
    NASH_FROZEN_THRESHOLD,
    VAL_DIR_SIN_INFERENCIA,
    GodelScoreResult,
    GoldScoreAction,
    GoldScoreKillReason,
    GoldScoreRegime,
    NashFrozenSource,
    compute_godel_score,
    compute_gold_score_bma,
    compute_nash_frozen_7d,
)


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

def test_godel_score_es_val_dir_cuando_la_mascara_dispara():
    """`godel_score = float(godel_active) * val_dir if godel_active else 0.0`.
    Dentro de la rama, `float(godel_active)` vale 1.0 -- o sea, el score ES
    val_dir, sin transformación."""
    for val_dir in (0.0, 0.25, 0.5, 0.5614, 0.9, 1.0):
        r = compute_godel_score(godel_is_active=True, val_dir=val_dir)
        assert r.value == pytest.approx(val_dir), (
            "el port aplicó alguna transformación que el legacy no tiene")
        assert r.has_inference is True
        assert r.godel_is_active is True

def test_godel_score_es_cero_cuando_la_mascara_no_dispara():
    """La rama `else 0.0`, con val_dir presente y alto: si el score no
    fuera 0.0 acá, el `if godel_active` se habría perdido en el port."""
    r = compute_godel_score(godel_is_active=False, val_dir=0.95)

    assert r.value == 0.0
    assert r.godel_is_active is False

def test_godel_score_reproduce_la_formula_del_legacy_literal():
    """Se replica la línea 94 tal cual y se compara valor por valor, en vez
    de confiar en la lectura del docstring."""
    def legacy(godel_active: bool, val_dir: float) -> float:
        return float(godel_active) * val_dir if godel_active else 0.0

    for activo in (True, False):
        for val_dir in (0.0, 0.1, 0.5, 0.73, 1.0):
            assert compute_godel_score(activo, val_dir).value == pytest.approx(
                legacy(activo, val_dir)), f"activo={activo} val_dir={val_dir}"

def test_sin_inferencia_el_score_es_cero_y_no_el_neutro_de_val_dir():
    """LA DECISIÓN QUE MÁS IMPORTA DE ESTE PORT.

    El legacy define `val_dir = inference_result.get("val_dir", 0.5)`, y
    ese 0.5 podría parecer el valor a usar cuando no hay modelo. NO lo es:
    en el legacy ese 0.5 nunca llega a la fórmula, porque las MISMAS tres
    ramas OFFLINE que lo devuelven ponen `godel_activo=False`, y entonces
    el score sale 0.0.

    O sea: el legacy corriendo sin torch produce godel_score = 0.0. Este
    port hace lo mismo. Devolver 0.5 sería fabricar una salida de modelo
    sin modelo."""
    r = compute_godel_score(godel_is_active=True, val_dir=None)

    assert r.value == 0.0
    assert r.value != VAL_DIR_SIN_INFERENCIA, (
        "se está usando el default de val_dir para fabricar un score")
    assert r.has_inference is False

def test_el_default_de_val_dir_del_legacy_esta_documentado_pero_no_se_usa():
    """La constante existe para dejar registro de qué dice el legacy, no
    para alimentar la fórmula. Si algún día el score devuelve 0.5 sin
    modelo, este test lo detecta."""
    assert VAL_DIR_SIN_INFERENCIA == 0.5
    assert compute_godel_score(True, None).value == 0.0
    assert compute_godel_score(False, None).value == 0.0

def test_el_cero_por_mascara_inactiva_se_distingue_del_cero_por_falta_de_modelo():
    """Los dos casos dan `value == 0.0` y significan cosas distintas. Sin
    `reason` y `has_inference` serían indistinguibles en un artefacto
    persistido -- el mismo problema que VitalityResult.degraded y
    NashFrozenResult.insufficient_data ya resuelven así en este módulo."""
    sin_mascara = compute_godel_score(godel_is_active=False, val_dir=0.8)
    sin_modelo = compute_godel_score(godel_is_active=True, val_dir=None)

    assert sin_mascara.value == sin_modelo.value == 0.0
    assert sin_mascara.reason != sin_modelo.reason
    assert sin_mascara.has_inference is True
    assert sin_modelo.has_inference is False
    assert isinstance(sin_mascara, GodelScoreResult)

def test_sin_lstm_el_gold_score_no_puede_alcanzar_execute():
    """Consecuencia estructural, no una preferencia: con el componente
    Gödel en 0.0, el gold_score queda acotado por la suma de los otros dos
    pesos. Un sistema sin modelo NO puede emitir una orden de ejecución
    por esta vía.

    Si este test se pone en rojo, o apareció un LSTM (y entonces hay que
    actualizarlo a conciencia) o alguien fabricó el componente Gödel."""
    for tipo, techo_esperado in (("native", 0.60), ("synthetic", 0.45)):
        w = BMA_WEIGHTS[tipo]
        techo = w["te_entropy"] + w["backbone"]      # godel aporta 0.0
        assert techo == pytest.approx(techo_esperado)
        assert techo < 0.65, "el techo alcanza EXECUTE_WEAK"
        assert techo < 0.85, "el techo alcanza EXECUTE_STRONG"

    # Y comprobado de punta a punta, con los componentes en su máximo.
    r = compute_gold_score_bma(
        godel_score=compute_godel_score(True, None).value,
        te_score=1.0, backbone_score=1.0, asset="BTC",
        entropy_shannon=0.30, p66_entropy=0.40,
    )
    assert r.gold_score == pytest.approx(0.60)
    assert r.action is not GoldScoreAction.EXECUTE_WEAK
    assert r.action is not GoldScoreAction.EXECUTE_STRONG

def test_el_docstring_de_gold_score_advierte_que_no_predice():
    """Los números están medidos y el docstring es donde alguien los va a
    leer antes de usar la función. Si desaparecen, el próximo lector toma
    un promedio ponderado sin significancia por una recomendación."""
    doc = compute_gold_score_bma.__doc__

    assert "NO PREDICE" in doc or "NO ES UNA SEÑAL OPERATIVA" in doc
    for dato in ("Bonferroni", "Benjamini-Hochberg", "0,4133", "0,5921", "99,2%"):
        assert dato in doc, f"falta el dato medido: {dato}"
    assert "pesos" in doc.lower() and "nunca" in doc.lower(), (
        "falta que los pesos 0.40/0.30/0.30 tampoco se midieron")
