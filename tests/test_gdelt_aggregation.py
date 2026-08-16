"""
tests/test_gdelt_aggregation.py
=================================
Cobertura de ingestion/gdelt_aggregation.py. La fórmula de
_shannon_entropy se auditó contra numpy.histogram real (200 muestras
aleatorias, diferencia máxima 0.0 -- ver sesión de auditoría) antes de
escribir estos tests; acá se cubren los casos de contrato (filtro,
insuficiencia de eventos, campos None vs. valores reales), no se repite
esa auditoría numérica en cada test.
"""

from __future__ import annotations

from datetime import date

import pytest

from ingestion.gdelt_aggregation import (
    MIN_EVENTS_FOR_VALID_DAY,
    _shannon_entropy,
    _zipf_concentration,
    aggregate_day,
)


def _evento(country1="USA", country2="TWN", goldstein=4.0, num_sources=3, avg_tone=-2.0):
    return {
        "date_int": 20260115, "country1": country1, "country2": country2,
        "goldstein": goldstein, "num_mentions": 10, "num_sources": num_sources,
        "num_articles": 5, "avg_tone": avg_tone,
    }


class TestShannonEntropy:
    def test_entropia_cero_con_menos_de_dos_tonos(self):
        assert _shannon_entropy([]) == 0.0
        assert _shannon_entropy([5.0]) == 0.0

    def test_entropia_cero_cuando_todos_los_tonos_caen_en_el_mismo_bin(self):
        # certeza total -- un solo bin ocupado -> H=0
        assert _shannon_entropy([1.0, 1.1, 1.2, 0.9]) == pytest.approx(0.0, abs=1e-9)

    def test_entropia_maxima_con_veinte_bins_igualmente_poblados(self):
        # un valor en cada uno de los 20 bins, en cantidades iguales -> H = log2(20)
        import math
        low, high = -100.0, 100.0
        bin_width = (high - low) / 20
        tonos = [low + bin_width * i + 0.01 for i in range(20)]
        h = _shannon_entropy(tonos)
        assert h == pytest.approx(math.log2(20), abs=1e-6)

    def test_clampea_valores_fuera_del_rango_menos_cien_cien(self):
        # un tono de 500 (fuera de rango) no debe crashear -- se clampea al borde
        resultado = _shannon_entropy([500.0, -500.0, 0.0])
        assert resultado >= 0.0  # no lanza, produce un número válido


class TestZipfConcentration:
    def test_cero_con_lista_vacia(self):
        assert _zipf_concentration([]) == 0.0

    def test_uno_cuando_una_sola_fuente_domina_todo(self):
        assert _zipf_concentration([100.0]) == pytest.approx(1.0)

    def test_bajo_cuando_las_fuentes_estan_equilibradas(self):
        # 4 fuentes iguales -> Herfindahl = 4*(0.25)^2 = 0.25
        assert _zipf_concentration([10.0, 10.0, 10.0, 10.0]) == pytest.approx(0.25)


class TestAggregateDay:
    def test_insufficient_events_con_menos_del_minimo(self):
        eventos = [_evento() for _ in range(MIN_EVENTS_FOR_VALID_DAY - 1)]
        result = aggregate_day(eventos, asset="NVDA", day=date(2026, 1, 15))
        assert result.insufficient_events is True
        assert result.entropy_shannon is None
        assert result.goldstein_mean is None

    def test_dia_valido_con_exactamente_el_minimo(self):
        eventos = [_evento() for _ in range(MIN_EVENTS_FOR_VALID_DAY)]
        result = aggregate_day(eventos, asset="NVDA", day=date(2026, 1, 15))
        assert result.insufficient_events is False
        assert result.n_events == MIN_EVENTS_FOR_VALID_DAY
        assert result.entropy_shannon is not None

    def test_filtra_por_pais_reusando_classify_gdelt_event(self):
        # 10 eventos en USA (matchea NVDA) + 10 en un país que no matchea
        # NINGÚN CORE_COUNTRY_FILTERS -- solo los de USA deben contar.
        eventos_usa = [_evento(country1="USA", country2="XXX") for _ in range(10)]
        eventos_fuera = [_evento(country1="BRA", country2="YYY") for _ in range(10)]
        result = aggregate_day(eventos_usa + eventos_fuera, asset="NVDA", day=date(2026, 1, 15))
        assert result.n_events == 10  # solo los de USA

    def test_xau_no_filtra_por_pais_lista_vacia(self):
        # CORE_COUNTRY_FILTERS["XAU"] es () -- todo matchea
        eventos = [_evento(country1="ZZZ", country2="WWW") for _ in range(10)]
        result = aggregate_day(eventos, asset="XAU", day=date(2026, 1, 15))
        assert result.n_events == 10
        assert result.insufficient_events is False

    def test_goldstein_mean_ignora_eventos_sin_goldstein_no_los_trata_como_cero(self):
        # Un evento con goldstein=None NO debe arrastrar la media hacia 0 --
        # se excluye del promedio, no se cuenta como 0.0.
        eventos = (
            [_evento(goldstein=10.0) for _ in range(5)]
            + [{**_evento(), "goldstein": None}]
        )
        result = aggregate_day(eventos, asset="NVDA", day=date(2026, 1, 15))
        assert result.goldstein_mean == pytest.approx(10.0)  # no 8.33 (que sería tratar None como 0)

    def test_asset_no_reconocido_propaga_valueerror_de_classify_gdelt_event(self):
        eventos = [_evento() for _ in range(MIN_EVENTS_FOR_VALID_DAY)]
        with pytest.raises(ValueError):
            aggregate_day(eventos, asset="AAPL", day=date(2026, 1, 15))

    def test_lista_vacia_de_eventos_es_insufficient(self):
        result = aggregate_day([], asset="NVDA", day=date(2026, 1, 15))
        assert result.insufficient_events is True
        assert result.n_events == 0

    def test_conserva_day_y_asset_en_el_resultado(self):
        eventos = [_evento() for _ in range(MIN_EVENTS_FOR_VALID_DAY)]
        d = date(2026, 3, 20)
        result = aggregate_day(eventos, asset="BTC", day=d)
        assert result.day == d
        assert result.asset == "BTC"
