"""
tests/test_gdelt_series.py
=============================
Cobertura de ingestion/gdelt_series.py. Usa el mismo patrón de
monkeypatch de drive_root() ya establecido en test_persistence.py
(env var SPEL_DRIVE_ROOT + tmp_path) -- no un mecanismo nuevo.
"""

from __future__ import annotations

from datetime import date

import pytest

import governance.persistence as persistence_module
from governance.persistence import DRIVE_ROOT_ENV_VAR
from ingestion.gdelt_aggregation import DailyAggregationResult
from ingestion.gdelt_series import append_day, last_day, read_series


@pytest.fixture(autouse=True)
def _drive_root_temporal(monkeypatch, tmp_path):
    """Cada test corre contra un drive_root() temporal y aislado --
    autouse=True para no repetir esto en cada función de test."""
    monkeypatch.setenv(DRIVE_ROOT_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)


def _dia(d: date, entropy=0.5, n_events=10) -> DailyAggregationResult:
    return DailyAggregationResult(
        day=d, asset="NVDA", entropy_shannon=entropy,
        zipf_concentration=0.2, goldstein_mean=1.0, tone_variance=0.3,
        n_events=n_events, insufficient_events=False,
    )


class TestAppendYRead:
    def test_serie_vacia_para_activo_sin_ningun_dia(self):
        assert read_series("NVDA") == []

    def test_append_y_read_devuelven_el_mismo_dia(self):
        original = _dia(date(2026, 1, 15))
        append_day(original)
        serie = read_series("NVDA")
        assert len(serie) == 1
        assert serie[0] == original

    def test_multiples_dias_quedan_ordenados_cronologicamente(self):
        # se escriben fuera de orden a propósito -- read_series debe ordenar
        append_day(_dia(date(2026, 1, 17)))
        append_day(_dia(date(2026, 1, 15)))
        append_day(_dia(date(2026, 1, 16)))

        serie = read_series("NVDA")
        assert [r.day for r in serie] == [date(2026, 1, 15), date(2026, 1, 16), date(2026, 1, 17)]

    def test_ultimo_elemento_es_el_dia_mas_reciente(self):
        # misma convención que entropy_window en core/scoring.py
        append_day(_dia(date(2026, 1, 15)))
        append_day(_dia(date(2026, 1, 20)))
        serie = read_series("NVDA")
        assert serie[-1].day == date(2026, 1, 20)

    def test_activos_distintos_no_se_mezclan(self):
        append_day(_dia(date(2026, 1, 15)))  # asset="NVDA", por el helper
        xau = DailyAggregationResult(
            day=date(2026, 1, 15), asset="XAU", entropy_shannon=0.9,
            zipf_concentration=0.1, goldstein_mean=2.0, tone_variance=0.1,
            n_events=20, insufficient_events=False,
        )
        append_day(xau)

        assert len(read_series("NVDA")) == 1
        assert len(read_series("XAU")) == 1
        assert read_series("XAU")[0].entropy_shannon == 0.9


class TestDeduplicacion:
    def test_mismo_dia_escrito_dos_veces_se_queda_con_la_ultima_ocurrencia(self):
        append_day(_dia(date(2026, 1, 15), entropy=0.3))
        append_day(_dia(date(2026, 1, 15), entropy=0.9))  # reprocesamiento

        serie = read_series("NVDA")
        assert len(serie) == 1  # no 2 -- deduplicado
        assert serie[0].entropy_shannon == 0.9  # gana la última escrita

    def test_duplicado_no_afecta_el_orden_de_los_demas_dias(self):
        append_day(_dia(date(2026, 1, 15)))
        append_day(_dia(date(2026, 1, 16)))
        append_day(_dia(date(2026, 1, 15), entropy=0.99))  # backfill del primer día

        serie = read_series("NVDA")
        assert len(serie) == 2
        assert serie[0].day == date(2026, 1, 15)
        assert serie[0].entropy_shannon == 0.99


class TestLineaCorrupta:
    def test_linea_corrupta_se_saltea_sin_abortar_las_demas(self):
        append_day(_dia(date(2026, 1, 15)))

        # inyectar una línea corrupta directamente en el archivo
        from ingestion.gdelt_series import _series_file_path
        path = _series_file_path("NVDA")
        with path.open("a", encoding="utf-8") as f:
            f.write("esto no es json valido\n")

        append_day(_dia(date(2026, 1, 16)))

        serie = read_series("NVDA")  # no debe lanzar
        assert len(serie) == 2

    def test_linea_vacia_se_ignora_silenciosamente(self):
        append_day(_dia(date(2026, 1, 15)))
        from ingestion.gdelt_series import _series_file_path
        path = _series_file_path("NVDA")
        with path.open("a", encoding="utf-8") as f:
            f.write("\n\n")
        append_day(_dia(date(2026, 1, 16)))

        assert len(read_series("NVDA")) == 2


class TestFiltroSinceUntil:
    def _serie_de_cinco_dias(self):
        for i in range(1, 6):
            append_day(_dia(date(2026, 1, i)))

    def test_since_excluye_dias_anteriores(self):
        self._serie_de_cinco_dias()
        serie = read_series("NVDA", since=date(2026, 1, 3))
        assert [r.day.day for r in serie] == [3, 4, 5]

    def test_until_excluye_dias_posteriores(self):
        self._serie_de_cinco_dias()
        serie = read_series("NVDA", until=date(2026, 1, 3))
        assert [r.day.day for r in serie] == [1, 2, 3]

    def test_since_y_until_combinados_son_inclusive_en_ambos_extremos(self):
        self._serie_de_cinco_dias()
        serie = read_series("NVDA", since=date(2026, 1, 2), until=date(2026, 1, 4))
        assert [r.day.day for r in serie] == [2, 3, 4]


class TestLastDay:
    def test_none_cuando_la_serie_esta_vacia(self):
        assert last_day("NVDA") is None

    def test_devuelve_el_dia_mas_reciente(self):
        append_day(_dia(date(2026, 1, 15)))
        append_day(_dia(date(2026, 1, 20)))
        append_day(_dia(date(2026, 1, 17)))
        assert last_day("NVDA") == date(2026, 1, 20)


class TestInsufficientEventsSePersisteIgual:
    def test_un_dia_con_insufficient_events_se_guarda_y_se_lee_correctamente(self):
        # aggregate_day() puede producir un día con todos los campos en
        # None -- ese resultado debe poder persistirse igual (es
        # información real: "este día no tuvo señal confiable").
        dia_insuficiente = DailyAggregationResult(
            day=date(2026, 1, 15), asset="NVDA",
            entropy_shannon=None, zipf_concentration=None,
            goldstein_mean=None, tone_variance=None,
            n_events=2, insufficient_events=True,
        )
        append_day(dia_insuficiente)
        serie = read_series("NVDA")
        assert serie[0].insufficient_events is True
        assert serie[0].entropy_shannon is None
