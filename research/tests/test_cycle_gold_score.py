"""
research/tests/test_cycle_gold_score.py
========================================
Los tests de COMPOSICIÓN de gold_score, movidos con el cableado que
prueban el 16-sep-2026.

NO SE MUDARON A `research/` POR CAPRICHO DE SIMETRÍA. Estos ocho no
probaban las funciones sueltas -- eso lo hace
`test_gold_score_chain.py` -- sino que el ciclo lee la serie GDELT,
calcula el p66, arma los tres componentes con cierres reales y los combina
en un número exacto. Lo que verifican ES el cableado, así que viajaron con
él (`research/cycle_gold_score.py`).

LO ÚNICO QUE CAMBIÓ: el nombre de la función que llaman
(`run_scoring_cycle` -> `run_scoring_cycle_con_gold_score`) y de dónde
salen los imports. Ni un assert, ni un número, ni un fixture. En
particular sigue intacto `GOLD_SCORE_ESPERADO = 0.539239`, que era el test
de cierre de Fase 1 y el más caro de reconstruir si se hubiera perdido.

Los dos tests de `tests/test_cycle.py` que hablaban de cold start y de "los
3 reales" NO vinieron acá: su asunto es el ciclo vivo, que sigue teniendo
cold start. Se quedaron allá, actualizados a la forma nueva del resultado.

CI: no bloquean un merge -- ver `research/__init__.py`.
A mano: `pytest research/tests/ -q`.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import governance.persistence as persistence_module
from governance.persistence import DRIVE_ROOT_ENV_VAR
from ingestion.gdelt_aggregation import DailyAggregationResult
from ingestion.gdelt_series import append_day
from orchestration.cycle import DEFAULT_CYCLE_ASSETS
from research.cycle_gold_score import run_scoring_cycle_con_gold_score
from research.gold_score_chain import (
    GOLD_SCORE_SIN_PODER_PREDICTIVO,
    GOLD_SCORE_SIN_PRECIO_REASON,
)

P66_TEST_DEFAULT = 0.7


@pytest.fixture(autouse=True)
def _drive_root_temporal(monkeypatch, tmp_path):
    monkeypatch.setenv(DRIVE_ROOT_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)


def _dia(asset: str, d: date, entropy=0.5, n_events=10,
         insufficient=False) -> DailyAggregationResult:
    return DailyAggregationResult(
        day=d, asset=asset,
        entropy_shannon=None if insufficient else entropy,
        zipf_concentration=None if insufficient else 0.2,
        goldstein_mean=None if insufficient else 1.0,
        tone_variance=None if insufficient else 0.3,
        n_events=n_events, insufficient_events=insufficient,
    )


def _sembrar_historia(asset: str, dias: int, entropy=0.5, n_events=10,
                      desde=date(2026, 1, 1)) -> None:
    for k in range(dias):
        append_day(_dia(asset, desde + timedelta(days=k),
                        entropy=entropy, n_events=n_events))


def test_sin_cierres_el_gold_score_es_none_y_el_motivo_dice_que_faltan_datos():
    """Este test REEMPLAZA a `test_gold_score_siempre_none_con_o_sin_historia`,
    que codificaba una afirmación factualmente falsa:

        assert "godel_score" in r.gold_score_blocked_reason
        assert "backbone_score" in r.gold_score_blocked_reason

    El motivo viejo decía que NINGUNO de los tres componentes tenía función
    que lo calculara. `te_score` y `backbone_score` la tenían desde el 18 de
    agosto, y `godel_score` la tiene desde este patch. El test pasaba porque
    verificaba que las palabras estuvieran en el string, no que la
    afirmación fuera cierta.

    Lo que sí falta es de otra clase: datos de precio."""
    _sembrar_historia("XAU", 5)

    resultado = run_scoring_cycle_con_gold_score(["XAU", "NIFTY50"],
                                  p66_entropy_global_default=P66_TEST_DEFAULT)

    for r in resultado.values():
        assert r.gold_score is None
        assert r.gold_score_warning is None, (
            "sin gold_score no corresponde advertencia sobre un gold_score")
    # El activo CON historia reporta el motivo real: faltan cierres.
    assert resultado["XAU"].gold_score_blocked_reason == GOLD_SCORE_SIN_PRECIO_REASON
    assert "cierres" in resultado["XAU"].gold_score_blocked_reason
    # Y el que no tiene historia reporta el suyo, que es otro.
    assert resultado["NIFTY50"].gold_score_blocked_reason != GOLD_SCORE_SIN_PRECIO_REASON

#: Serie de cierres DETERMINISTA: rampa geométrica exacta, sin RNG y sin
#: ruido. Cualquier `default_rng` acá haría que el valor esperado dependa
#: de la versión de numpy.
def _closes_deterministas(n: int = 120, base: float = 100.0,
                          paso: float = 1.01) -> list[float]:
    return [base * (paso ** i) for i in range(n)]

#: Valor exacto que produce la cadena completa con esos cierres, con
#: `val_dir=None` (sin LSTM). Medido, no estimado, y recalculable a mano:
#:     te_score       = 0.7974646425969463
#:     backbone_score = 1.0            (tendencia saturada)
#:     godel_score    = 0.0            (sin inferencia)
#:     0.40*0.0 + 0.30*0.7974646425969463 + 0.30*1.0 = 0.539239 (redondeado a 6)
GOLD_SCORE_ESPERADO = 0.539239

def test_gold_score_end_to_end_da_el_valor_exacto_esperado():
    """EL TEST DE CIERRE DE FASE 1. Corre en CI, calcula un Gold Score con
    las tres funciones de componente reales y verifica el NÚMERO."""
    from research.gold_score_chain import GoldScoreAction, GoldScoreKillReason

    # Entropía plana y baja: la máscara no dispara y no hay legacy-kill
    # (0.30 < SHANNON_KILL_THRESHOLD=0.42). Aísla el cálculo del score.
    _sembrar_historia("BTC", 30, entropy=0.30, n_events=10)

    r = run_scoring_cycle_con_gold_score(
        ["BTC"], p66_entropy_global_default=P66_TEST_DEFAULT,
        closes_por_activo={"BTC": _closes_deterministas()},
    )["BTC"]

    assert r.gold_score is not None, "el gold_score sigue bloqueado"
    assert r.gold_score.gold_score == pytest.approx(GOLD_SCORE_ESPERADO), (
        f"el valor cambió: {r.gold_score.gold_score} vs {GOLD_SCORE_ESPERADO}. "
        f"Si fue a propósito, recalcular la constante a mano y actualizar el "
        f"comentario que la deriva -- no ajustar el número para que pase."
    )
    assert r.gold_score.kill_signal is False
    assert r.gold_score.kill_reason is GoldScoreKillReason.NONE
    assert r.gold_score.asset_type == "native"
    assert r.gold_score.action is GoldScoreAction.WATCH
    assert r.gold_score_blocked_reason is None

def test_el_valor_esperado_se_deriva_de_los_tres_componentes():
    """Contraprueba del anterior: que el número no sea una constante
    copiada de una corrida, sino la combinación que dice ser. Si el
    ponderado cambia, este test lo separa del anterior."""
    from research.price_signals import (compute_backbone_score,
                                    compute_transfer_entropy_proxy)
    from research.gold_score_chain import BMA_WEIGHTS, compute_godel_score

    closes = _closes_deterministas()
    te = compute_transfer_entropy_proxy(closes)
    bb = compute_backbone_score(closes)
    g = compute_godel_score(godel_is_active=False, val_dir=None)
    w = BMA_WEIGHTS["native"]

    a_mano = round(w["godel"] * g.value + w["te_entropy"] * te.value
                   + w["backbone"] * bb.value, 6)

    assert a_mano == pytest.approx(GOLD_SCORE_ESPERADO)
    assert te.insufficient_data is False and bb.insufficient_data is False, (
        "el valor esperado se está apoyando en placeholders, no en datos")

def test_el_gold_score_calculado_viaja_con_su_advertencia():
    """Un gold_score suelto en un log o en un artefacto se lee como una
    recomendación. Dos de sus tres inputs fueron medidos y no son
    significativos, así que la advertencia va pegada al número."""
    _sembrar_historia("BTC", 30, entropy=0.30, n_events=10)

    r = run_scoring_cycle_con_gold_score(
        ["BTC"], p66_entropy_global_default=P66_TEST_DEFAULT,
        closes_por_activo={"BTC": _closes_deterministas()},
    )["BTC"]

    assert r.gold_score_warning == GOLD_SCORE_SIN_PODER_PREDICTIVO
    assert "NO PREDICE" in r.gold_score_warning
    for dato in ("Bonferroni", "Benjamini-Hochberg", "0.4133", "99.2%"):
        assert dato in r.gold_score_warning

def test_sin_lstm_el_componente_godel_no_aporta_aunque_la_mascara_dispare():
    """El caso que separa este PR de uno que hubiera fabricado un 0.5: con
    la máscara ACTIVA y sin modelo, el gold_score tiene que dar lo mismo
    que con la máscara inactiva. Si difiere, val_dir se está inventando."""
    from research.gold_score_chain import compute_godel_score

    activo = compute_godel_score(godel_is_active=True, val_dir=None)
    inactivo = compute_godel_score(godel_is_active=False, val_dir=None)

    assert activo.value == inactivo.value == 0.0
    # Y se distinguen igual, por el motivo -- no se pierde la información.
    assert activo.reason != inactivo.reason

def test_un_activo_sin_cierres_no_rompe_a_los_que_si_tienen():
    """Resultado parcial, no todo-o-nada: el mapa de cierres puede cubrir
    algunos activos y no otros, y eso no es un error."""
    _sembrar_historia("BTC", 30, entropy=0.30, n_events=10)
    _sembrar_historia("XAU", 30, entropy=0.30, n_events=10)

    res = run_scoring_cycle_con_gold_score(
        ["BTC", "XAU"], p66_entropy_global_default=P66_TEST_DEFAULT,
        closes_por_activo={"BTC": _closes_deterministas()},
    )

    assert res["BTC"].gold_score is not None
    assert res["XAU"].gold_score is None
    assert res["XAU"].gold_score_blocked_reason == GOLD_SCORE_SIN_PRECIO_REASON

def test_HALLAZGO_el_componente_godel_nunca_aporta_al_gold_score_final():
    """HALLAZGO ESTRUCTURAL, fijado acá para que no se pierda.

    El término `w_godel * godel_score` NO PUEDE aportar a ningún
    gold_score distinto de cero, ni siquiera con un LSTM perfecto:

      · Si la máscara dispara, `compute_gold_score_bma` activa el
        kill_signal por `godel_active` y pone gold_score en 0.0 -- sin
        importar cuánto valga el componente.
      · Si la máscara NO dispara, `compute_godel_score` devuelve 0.0 por
        definición (la rama `else 0.0` del legacy).

    Los dos casos son exhaustivos, así que el 0.40 (nativos) o 0.55
    (sintéticos) de peso está estructuralmente muerto.

    LA CAUSA no está en ninguna de las dos fuentes legacy, y eso importa:
      · `spel_score_engine.py` usa godel_active para PONDERAR el score, y
        no mata por él.
      · `spel_bayesian_core.py` mata solo por Shannon > 0.42 y KL > 0.20;
        nunca llama a godel_active.
    La rama de kill por `godel_active` es un agregado del port -- ya
    identificado como tal en la auditoría del PR #17, que lo dejó
    explícitamente como tarea aparte. Este PR NO la toca: cambiar la
    lógica de compute_gold_score_bma es una decisión de criterio con su
    propia medición.

    Este test documenta el estado real. Si algún día cambia, que sea a
    conciencia y no por accidente."""
    from research.gold_score_chain import compute_godel_score, compute_gold_score_bma

    for val_dir in (None, 0.80, 1.0):
        con_mascara = compute_gold_score_bma(
            godel_score=compute_godel_score(True, val_dir).value,
            te_score=1.0, backbone_score=1.0, asset="BTC",
            entropy_shannon=0.30, p66_entropy=0.20,   # dispara la máscara
        )
        assert con_mascara.gold_score == 0.0, (
            f"val_dir={val_dir}: con la máscara activa el kill manda")
        assert con_mascara.kill_signal is True

        sin_mascara = compute_gold_score_bma(
            godel_score=compute_godel_score(False, val_dir).value,
            te_score=1.0, backbone_score=1.0, asset="BTC",
            entropy_shannon=0.30, p66_entropy=0.40,   # no dispara
        )
        # 0.30*1.0 + 0.30*1.0 = 0.60 -- el término Gödel aporta cero.
        assert sin_mascara.gold_score == pytest.approx(0.60), (
            f"val_dir={val_dir}: el componente Gödel aportó algo, y no debería "
            f"poder -- revisar si cambió compute_gold_score o el kill")

def test_val_dir_llega_al_componente_aunque_el_kill_lo_neutralice():
    """El parámetro `val_dir_por_activo` no es decorativo: cuando Fase 2
    entregue un modelo, enchufarlo no debe requerir tocar la firma. Se
    verifica sobre el COMPONENTE, que es donde val_dir sí tiene efecto --
    el gold_score final lo neutraliza por el kill (ver el test anterior)."""
    from research.gold_score_chain import compute_godel_score

    assert compute_godel_score(True, 0.80).value == pytest.approx(0.80)
    assert compute_godel_score(True, None).value == 0.0

    # Y el ciclo lo pasa de verdad: con la máscara activa el gold_score da
    # 0.0 por el kill, pero la llamada acepta el mapa y no lo ignora.
    for i in range(30):
        append_day(_dia("BTC", date(2026, 1, 1) + timedelta(days=i),
                        entropy=0.10 + 0.02 * i, n_events=10))

    r = run_scoring_cycle_con_gold_score(
        ["BTC"], p66_entropy_global_default=0.05,
        closes_por_activo={"BTC": _closes_deterministas()},
        val_dir_por_activo={"BTC": 0.80},
    )["BTC"]

    assert r.godel_is_active is True, "la fixture no activa la máscara"
    assert r.gold_score is not None
    assert r.gold_score.kill_signal is True
    assert r.gold_score.gold_score == 0.0
