"""
tests/test_training_dataset.py
=================================
Cobertura de ingestion/training_dataset.py. Un test en particular
(test_epoch_evita_el_problema_historico_de_formatos_de_fecha_mixtos)
reproduce, de forma directa, el escenario que Altair describió: fuentes
con columnas de fecha en formatos distintos ('/' vs '-') -- y confirma
que la vía epoch-based de este repo nunca pasa por ese problema.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

import governance.persistence as persistence_module
from governance.persistence import DRIVE_ROOT_ENV_VAR
from ingestion.adapters import AdapterDataError
from ingestion.gdelt_aggregation import DailyAggregationResult
from ingestion.gdelt_series import append_day
from ingestion.training_dataset import build_training_dataset


@pytest.fixture(autouse=True)
def _drive_root_temporal(monkeypatch, tmp_path):
    monkeypatch.setenv(DRIVE_ROOT_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)


def _ohlcv(dates: list[date], closes: list[float]) -> pd.DataFrame:
    """OHLCV válido -- mismo camino que DerivAdapter._to_dataframe():
    epoch -> pd.to_datetime(unit='s', utc=True). Nunca un string de
    fecha parseado a mano."""
    timestamps = pd.to_datetime([int(pd.Timestamp(d).timestamp()) for d in dates],
                                 unit="s", utc=True)
    return pd.DataFrame({
        "timestamp": timestamps, "open": closes, "high": closes,
        "low": closes, "close": closes, "volume": [100.0] * len(closes),
    })


def _dia_gdelt(asset: str, d: date, entropy=0.5, n_events=10) -> DailyAggregationResult:
    return DailyAggregationResult(
        day=d, asset=asset, entropy_shannon=entropy, zipf_concentration=0.2,
        goldstein_mean=1.0, tone_variance=0.3, n_events=n_events, insufficient_events=False,
    )


# ─── El escenario real que motivó este módulo ─────────────────────────────

def test_epoch_evita_el_problema_historico_de_formatos_de_fecha_mixtos():
    """Regresión directa del problema que Altair describió (2026-08-18):
    LSTM anterior entrenado sobre parquets con columnas de fecha en
    formatos distintos entre fuentes ('/' vs '-'), nunca detectado a
    tiempo. Acá: OHLCV nunca pasa por un parser de string -- viene de
    epoch (Unix seconds), un entero sin ambigüedad de formato posible.
    Este test confirma que el join funciona sin que exista ningún string
    de fecha en ningún punto de la cadena."""
    dias = [date(2026, 1, 1) + timedelta(days=i) for i in range(5)]
    for d in dias:
        append_day(_dia_gdelt("NVDA", d, entropy=0.4 + 0.01 * dias.index(d)))

    ohlcv = _ohlcv(dias, closes=[100.0, 101.0, 102.0, 103.0, 104.0])
    # Confirmar que no hay NINGUNA columna de tipo string/object con fechas
    assert ohlcv["timestamp"].dtype.kind == "M"  # datetime64, nunca 'O' (object/string)

    result = build_training_dataset(ohlcv, asset="NVDA")
    assert result.coverage_ratio == 1.0
    assert len(result.rows) == 5


# ─── Camino feliz -- match exacto día por día ─────────────────────────────

def test_match_exacto_dia_por_dia_sin_forward_fill():
    dias = [date(2026, 2, 1) + timedelta(days=i) for i in range(3)]
    for d in dias:
        append_day(_dia_gdelt("BTC", d))
    ohlcv = _ohlcv(dias, closes=[50000.0, 50100.0, 50200.0])

    result = build_training_dataset(ohlcv, asset="BTC")
    assert result.coverage_ratio == 1.0
    assert all(not r.entropy_is_forward_filled for r in result.rows)


# ─── Forward-fill -- fin de semana sin cobertura GDELT ────────────────────

def test_forward_fill_para_dias_sin_cobertura_gdelt_directa():
    """OHLCV cripto opera 7/7, pero simulamos GDELT solo viernes -- el
    fin de semana debe forward-fillear la entropía del viernes, con
    entropy_is_forward_filled=True marcado explícito."""
    viernes = date(2026, 3, 6)  # viernes
    append_day(_dia_gdelt("BTC", viernes, entropy=0.6))

    dias_ohlcv = [viernes, viernes + timedelta(days=1), viernes + timedelta(days=2)]  # vie, sab, dom
    ohlcv = _ohlcv(dias_ohlcv, closes=[100.0, 101.0, 99.0])

    result = build_training_dataset(ohlcv, asset="BTC")
    assert result.coverage_ratio == 1.0
    assert result.rows[0].entropy_is_forward_filled is False  # viernes, match directo
    assert result.rows[1].entropy_is_forward_filled is True   # sábado, forward-filled
    assert result.rows[2].entropy_is_forward_filled is True   # domingo, forward-filled
    assert result.rows[1].entropy_shannon == 0.6  # heredado del viernes
    assert result.rows[2].entropy_shannon == 0.6


# ─── Días OHLCV antes del primer GDELT -- excluidos, no inventados ───────

def test_dias_antes_del_primer_gdelt_se_excluyen_no_se_inventan():
    primer_gdelt = date(2026, 4, 10)
    append_day(_dia_gdelt("XAU", primer_gdelt, entropy=0.5))

    dias_ohlcv = [primer_gdelt - timedelta(days=2), primer_gdelt - timedelta(days=1), primer_gdelt]
    ohlcv = _ohlcv(dias_ohlcv, closes=[1900.0, 1901.0, 1902.0])

    result = build_training_dataset(ohlcv, asset="XAU")
    assert result.n_dropped_no_entropy == 2  # los 2 días previos al primer GDELT
    assert len(result.rows) == 1
    assert result.coverage_ratio == round(1 / 3, 4)


# ─── Cero historia GDELT -- resultado válido, no un error ─────────────────

def test_sin_historia_gdelt_coverage_cero_no_lanza():
    ohlcv = _ohlcv([date(2026, 5, 1), date(2026, 5, 2)], closes=[100.0, 101.0])
    result = build_training_dataset(ohlcv, asset="NIFTY50")
    assert result.coverage_ratio == 0.0
    assert result.rows == []
    assert result.n_dropped_no_entropy == 2


# ─── Re-valida el schema -- nunca confía en que el caller ya lo hizo ─────

def test_ohlcv_invalido_lanza_el_mismo_error_tipado_del_adapter():
    ohlcv_malo = pd.DataFrame({"timestamp": ["2026-01-01"], "close": [100.0]})  # faltan columnas
    with pytest.raises(AdapterDataError):
        build_training_dataset(ohlcv_malo, asset="NVDA")


def test_timestamp_naive_sin_timezone_lanza_igual_que_en_el_adapter():
    """Reusa validate_ohlcv_schema -- si ese contrato exige tz-aware UTC,
    este módulo hereda esa exigencia sin reimplementarla más débil."""
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-01-01"]),  # sin tz
        "open": [100.0], "high": [100.0], "low": [100.0],
        "close": [100.0], "volume": [10.0],
    })
    with pytest.raises(AdapterDataError):
        build_training_dataset(df, asset="NVDA")
