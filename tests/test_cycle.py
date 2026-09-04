"""
tests/test_cycle.py
=====================
Cobertura de orchestration/cycle.py. Mismo patrón de drive_root()
temporal que tests/test_gdelt_series.py -- no un mecanismo nuevo.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import governance.persistence as persistence_module
from governance.persistence import DRIVE_ROOT_ENV_VAR
from ingestion.gdelt_aggregation import DailyAggregationResult
from ingestion.gdelt_series import append_day
from orchestration.cycle import (
    DEFAULT_CYCLE_ASSETS,
    GOLD_SCORE_BLOCKED_REASON,
    run_scoring_cycle,
)

P90_TEST_DEFAULT = 0.7  # placeholder consciente para tests -- ver docstring
                        # de run_scoring_cycle sobre por qué no hay default.


@pytest.fixture(autouse=True)
def _drive_root_temporal(monkeypatch, tmp_path):
    monkeypatch.setenv(DRIVE_ROOT_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)


def _dia(asset: str, d: date, entropy=0.5, n_events=10, insufficient=False) -> DailyAggregationResult:
    return DailyAggregationResult(
        day=d, asset=asset,
        entropy_shannon=None if insufficient else entropy,
        zipf_concentration=None if insufficient else 0.2,
        goldstein_mean=None if insufficient else 1.0,
        tone_variance=None if insufficient else 0.3,
        n_events=n_events, insufficient_events=insufficient,
    )


def _sembrar_historia(asset: str, n_dias: int, start=date(2026, 1, 1), **kwargs):
    for i in range(n_dias):
        append_day(_dia(asset, start + timedelta(days=i), **kwargs))


# ─── Cold start -- caso válido, no un error ────────────────────────────────

def test_activo_sin_historia_es_cold_start_no_data():
    resultado = run_scoring_cycle(["NVDA"], p90_entropy_global_default=P90_TEST_DEFAULT)
    r = resultado["NVDA"]
    assert r.data_status == "cold_start_no_data"
    assert r.n_days_history == 0
    assert r.vitality_tesla is None
    assert r.nash_frozen is None
    assert r.godel_is_active is None


# ─── Camino feliz -- historia real, las 3 funciones corren de verdad ──────

def test_activo_con_historia_calcula_los_3_reales():
    _sembrar_historia("BTC", 15, entropy=0.4, n_events=20)
    resultado = run_scoring_cycle(["BTC"], p90_entropy_global_default=P90_TEST_DEFAULT)
    r = resultado["BTC"]

    assert r.data_status == "ok"
    assert r.n_days_history == 15
    assert r.vitality_tesla is not None
    assert r.vitality_tesla.value in (3, 6, 9)
    assert r.nash_frozen is not None
    assert isinstance(r.godel_is_active, bool)


# ─── gold_score: SIEMPRE None, SIEMPRE con la misma razón explícita ───────

def test_gold_score_siempre_none_con_o_sin_historia():
    _sembrar_historia("XAU", 5)
    resultado = run_scoring_cycle(["XAU", "NIFTY50"], p90_entropy_global_default=P90_TEST_DEFAULT)
    for r in resultado.values():
        assert r.gold_score is None
        assert r.gold_score_blocked_reason == GOLD_SCORE_BLOCKED_REASON
        assert "godel_score" in r.gold_score_blocked_reason
        assert "backbone_score" in r.gold_score_blocked_reason


# ─── EURUSD -- el fix del patch anterior, ejercitado end-to-end acá ───────

def test_eurusd_funciona_end_to_end_gracias_al_fix_anterior():
    """Antes del fix a classify_gdelt_event, este activo ni siquiera podía
    clasificar eventos GDELT -- este test confirma que el ciclo completo
    (que no llama classify_gdelt_event directamente, pero depende de que
    EURUSD sea un activo válido para el resto del pipeline) lo acepta."""
    _sembrar_historia("EURUSD", 12, entropy=0.6)
    resultado = run_scoring_cycle(["EURUSD"], p90_entropy_global_default=P90_TEST_DEFAULT)
    assert resultado["EURUSD"].data_status == "ok"


# ─── Activo no configurado -- falla temprano y claro, no en silencio ──────

def test_activo_no_configurado_lanza_valueerror_no_degrada_en_silencio():
    with pytest.raises(ValueError, match="no tiene classify_gdelt_event"):
        run_scoring_cycle(["ACTIVO_INVENTADO_XYZ"], p90_entropy_global_default=P90_TEST_DEFAULT)


def test_indice_volatilidad_no_esta_configurado_a_proposito():
    """VOL50 tiene precio real (DerivAdapter) pero GDELT no aplica sobre
    índices sintéticos (Fase 6, Hallazgo 1) -- debe seguir sin cobertura
    acá, no agregarse por accidente."""
    with pytest.raises(ValueError):
        run_scoring_cycle(["VOL50"], p90_entropy_global_default=P90_TEST_DEFAULT)


# ─── p90_entropy_global_default es obligatorio -- sin valor inventado ─────

def test_p90_global_default_es_argumento_obligatorio():
    with pytest.raises(TypeError):
        run_scoring_cycle(["NVDA"])  # type: ignore[call-arg]


# ─── DEFAULT_CYCLE_ASSETS -- exactamente los 5, ni más ni menos ──────────

def test_default_cycle_assets_son_exactamente_los_5_confirmados():
    assert set(DEFAULT_CYCLE_ASSETS) == {"NVDA", "XAU", "BTC", "NIFTY50", "EURUSD"}
    assert "VOL50" not in DEFAULT_CYCLE_ASSETS


# ─── Multi-activo real: mezcla de cold-start y con historia en una corrida ─

def test_corrida_multi_activo_mezcla_cold_start_y_con_historia():
    _sembrar_historia("NVDA", 8)
    # XAU, BTC, NIFTY50, EURUSD quedan sin historia -- cold start real
    resultado = run_scoring_cycle(p90_entropy_global_default=P90_TEST_DEFAULT)  # default: los 5

    assert resultado["NVDA"].data_status == "ok"
    for asset in ("XAU", "BTC", "NIFTY50", "EURUSD"):
        assert resultado[asset].data_status == "cold_start_no_data"


# ─── Días con insufficient_events=True se excluyen de las ventanas ───────

def test_dias_insuficientes_no_entran_en_las_ventanas():
    _sembrar_historia("NVDA", 5, entropy=0.5)
    append_day(_dia("NVDA", date(2026, 1, 6), insufficient=True))  # sin entropy
    _sembrar_historia("NVDA", 3, start=date(2026, 1, 7), entropy=0.6)

    resultado = run_scoring_cycle(["NVDA"], p90_entropy_global_default=P90_TEST_DEFAULT)
    r = resultado["NVDA"]
    # 5 + 3 = 8 días válidos; el día insuficiente (día 6) no cuenta
    assert r.n_days_history == 8


# ══════════════════════════════════════════════════════════════════════════
#  El criterio del p90: ventana móvil, no acumulado
#
#  ESTE BLOQUE EXISTE PORQUE LA SUITE ERA CIEGA A ESTE CAMBIO. Antes de
#  agregarlo, la fixture más grande de este archivo era de 15 días -- todas
#  caían DENTRO del warm-up de 252, donde móvil y acumulado son idénticos
#  por construcción. Los 607 tests pasaban con cualquiera de los dos
#  criterios, así que "siguen verdes" no era evidencia de que el cambio
#  fuera seguro: era evidencia de que no lo estaban mirando.
#
#  `test_con_deriva_a_la_baja_el_movil_ve_un_dia_que_el_acumulado_pierde`
#  es el que separa los dos criterios. Si alguna vez pasa con los dos, dejó
#  de separar nada y hay que arreglarlo, no relajarlo.
# ══════════════════════════════════════════════════════════════════════════

#: Deriva diaria a la escala REAL medida sobre BTC: la entropía media cae
#: de 1.1726 a 0.9970 en once años (~1,5 puntos porcentuales al año). No es
#: un número elegido para que el test dé: es el fenómeno que motivó el
#: cambio de criterio, a su tamaño real.
_ENTROPIA_INICIAL_BTC = 1.1726
_ENTROPIA_FINAL_BTC = 0.9970
_DERIVA_DIARIA = (_ENTROPIA_INICIAL_BTC - _ENTROPIA_FINAL_BTC) / (11 * 365)


def _sembrar_deriva(asset: str, n_dias: int, start=date(2020, 1, 1),
                    n_events: int = 10) -> list[float]:
    """Siembra `n_dias` con deriva descendente a escala real y devuelve la
    serie de entropías sembradas.

    Sin ruido a propósito: la deriva es lo que hace divergir los dos
    criterios, y un test que dependa de un `default_rng` para separarlos
    sería frágil por una razón que no tiene que ver con lo que mide.

    `n_events` constante hace que el tercil de vitality_tesla deje a todos
    los días en 3 -- necesario para que este test mida la RAMA DE ENTROPÍA
    del OR. Con vitality en 9 la máscara dispararía igual y el test pasaría
    sin haber comprobado nada del percentil.
    """
    entropias = [_ENTROPIA_INICIAL_BTC - _DERIVA_DIARIA * i for i in range(n_dias)]
    for i, e in enumerate(entropias):
        append_day(_dia(asset, start + timedelta(days=i),
                        entropy=e, n_events=n_events))
    return entropias


def test_con_deriva_a_la_baja_el_movil_ve_un_dia_que_el_acumulado_pierde():
    """EL TEST QUE SEPARA LOS DOS CRITERIOS.

    Escenario real, a escala real: once años de deriva descendente y un
    día con un repunte MODERADO -- alto para el último año, normal para
    2015. El percentil acumulado lo sigue comparando contra la cola vieja
    y no lo ve; la ventana móvil sí.

    La aserción no se apoya en un número mágico: se calculan los DOS
    umbrales sobre la misma historia y se comprueba que el día cae
    exactamente entre ellos. Con el criterio acumulado este test falla."""
    from core.scoring import compute_adaptive_percentile, compute_godel_p90

    n_historia = 599  # > 252: fuera del warm-up, los criterios divergen
    historia = _sembrar_deriva("BTC", n_historia)

    umbral_acumulado = compute_adaptive_percentile(
        history=historia, percentile=90.0, global_default=P90_TEST_DEFAULT).value
    umbral_movil = compute_godel_p90(
        historia, global_default=P90_TEST_DEFAULT).value

    # La premisa del escenario, comprobada y no supuesta: con deriva a la
    # baja el umbral acumulado queda POR ENCIMA del móvil.
    assert umbral_acumulado > umbral_movil, (
        "sin esta brecha el test no separa nada")

    # El repunte: justo en el medio de los dos umbrales.
    repunte = (umbral_movil + umbral_acumulado) / 2
    assert umbral_movil < repunte < umbral_acumulado
    append_day(_dia("BTC", date(2020, 1, 1) + timedelta(days=n_historia),
                    entropy=repunte, n_events=10))

    r = run_scoring_cycle(["BTC"], p90_entropy_global_default=P90_TEST_DEFAULT)["BTC"]

    assert r.data_status == "ok"
    assert r.vitality_tesla.value != 9, (
        "vitality dispararía la máscara por su cuenta y el test no estaría "
        "midiendo la rama de entropía")
    assert r.godel_is_active is True, (
        f"el ciclo NO vio el día: entropía={repunte:.6f}, umbral móvil="
        f"{umbral_movil:.6f}, umbral acumulado={umbral_acumulado:.6f}. "
        f"Si el ciclo estuviera usando el acumulado, este es exactamente "
        f"el día que perdería."
    )


def test_dentro_del_warmup_los_dos_criterios_dan_el_mismo_resultado():
    """La otra cara de la frontera. Con menos de 252 días la ventana móvil
    ES toda la historia, así que móvil y acumulado coinciden por
    construcción -- y por eso ningún test corto puede distinguirlos."""
    from core.scoring import GODEL_ROLLING_WINDOW_DAYS, compute_adaptive_percentile, compute_godel_p90

    n_historia = 200  # < 252: dentro del warm-up
    assert n_historia < GODEL_ROLLING_WINDOW_DAYS
    historia = _sembrar_deriva("XAU", n_historia)

    acumulado = compute_adaptive_percentile(
        history=historia, percentile=90.0, global_default=P90_TEST_DEFAULT)
    movil = compute_godel_p90(historia, global_default=P90_TEST_DEFAULT)

    assert movil.value == acumulado.value
    assert movil.n_obs == acumulado.n_obs == n_historia


def test_la_frontera_de_252_dias_es_donde_los_criterios_se_separan():
    """El día exacto en que dejan de coincidir. Con 252 observaciones la
    ventana todavía abarca toda la historia; con 253 ya recorta."""
    from core.scoring import GODEL_ROLLING_WINDOW_DAYS, compute_adaptive_percentile, compute_godel_p90

    serie = [_ENTROPIA_INICIAL_BTC - _DERIVA_DIARIA * i for i in range(400)]

    def umbrales(n):
        h = serie[:n]
        return (compute_adaptive_percentile(history=h, percentile=90.0,
                                            global_default=P90_TEST_DEFAULT).value,
                compute_godel_p90(h, global_default=P90_TEST_DEFAULT).value)

    acum_252, movil_252 = umbrales(GODEL_ROLLING_WINDOW_DAYS)
    assert movil_252 == acum_252, "en 252 todavía coinciden"

    acum_253, movil_253 = umbrales(GODEL_ROLLING_WINDOW_DAYS + 1)
    assert movil_253 != acum_253, "en 253 la ventana ya tiene que recortar"
    assert acum_253 > movil_253, "con deriva a la baja el acumulado queda más alto"


# ─── Versionado del criterio: sellado, sin comprobación todavía ───────────

def test_el_resultado_sella_con_que_criterio_se_calculo():
    """Un p90 acumulado y uno móvil son dos floats indistinguibles. Sin
    esta marca, un artefacto viejo se mezclaría en silencio con uno nuevo."""
    from core.scoring import GODEL_CRITERIA_VERSION

    _sembrar_historia("BTC", 15, entropy=0.4, n_events=20)

    r = run_scoring_cycle(["BTC"], p90_entropy_global_default=P90_TEST_DEFAULT)["BTC"]

    assert r.godel_criteria_version == GODEL_CRITERIA_VERSION
    assert "rolling" in r.godel_criteria_version
    assert "252" in r.godel_criteria_version


def test_el_sello_no_cambia_la_firma_de_run_scoring_cycle():
    """El campo tiene default: ningún llamador existente se rompe por
    haberlo agregado."""
    import dataclasses

    from orchestration.cycle import AssetCycleResult

    campo = next(f for f in dataclasses.fields(AssetCycleResult)
                 if f.name == "godel_criteria_version")
    assert campo.default is not dataclasses.MISSING

    r = AssetCycleResult(
        asset="BTC", data_status="cold_start_no_data", n_days_history=0,
        vitality_tesla=None, nash_frozen=None, godel_is_active=None,
        gold_score=None, gold_score_blocked_reason="x",
    )
    assert r.godel_criteria_version
