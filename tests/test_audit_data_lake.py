"""
tests/test_audit_data_lake.py
==============================
Cobertura de tools/audit_data_lake.py.

Los parquets de prueba se construyen acá con pyarrow, replicando EXACTAMENTE
los dos schemas reales encontrados en el Drive de Altair el 2026-08-18
(ver docstring de tools/audit_data_lake.py): el "bueno" (9 columnas,
timestamp[ms, UTC]) y el "anómalo" (2 columnas, date como string). El test
`test_reproduce_el_hallazgo_real_btc_2024_vs_2026` es una regresión directa
de ese hallazgo -- si el auditor deja de detectarlo, ese test se pone rojo.

Todos los tests que necesitan pyarrow se saltean solos si no está instalado
(no está en requirements.txt a propósito -- ver docstring del módulo).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.audit_data_lake import (
    DATA_LAKE_ROOT_ENV_VAR,
    audit_file,
    audit_lake,
    build_coverage,
    data_lake_root,
    find_group_findings,
    format_report,
    _to_iso_date,
    _year_from_filename,
)

pa = pytest.importorskip("pyarrow", reason="pyarrow no está en requirements.txt")
pq = pytest.importorskip("pyarrow.parquet", reason="pyarrow no está en requirements.txt")


# ─── Helpers: construyen los DOS schemas reales encontrados ────────────────

def _write_schema_bueno(path: Path, days: list[date], asset: str = "BTC") -> None:
    """9 columnas, `date` como timestamp[ms, UTC] -- el schema de los
    archivos 2015..2025 del data lake real."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = len(days)
    ts = [datetime(d.year, d.month, d.day, tzinfo=timezone.utc) for d in days]
    table = pa.table({
        "date": pa.array(ts, type=pa.timestamp("ms", tz="UTC")),
        "asset": pa.array([asset] * n),
        "entropy_shannon": pa.array([1.0 + i * 0.01 for i in range(n)], type=pa.float64()),
        "zipf_concentration": pa.array([0.2] * n, type=pa.float64()),
        "goldstein_mean": pa.array([1.5] * n, type=pa.float64()),
        "tone_variance": pa.array([0.3] * n, type=pa.float64()),
        "n_events": pa.array([10] * n, type=pa.int32()),
        "nash_frozen_7d": pa.array([0.1] * n, type=pa.float64()),
        "vitality_tesla": pa.array([5] * n, type=pa.int32()),
    })
    pq.write_table(table, str(path))


def _write_schema_anomalo(path: Path, days: list[date]) -> None:
    """2 columnas, `date` como STRING -- el schema real de
    BTC_2026_entropy.parquet, el archivo que rompe la consistencia."""
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table({
        "date": pa.array([d.isoformat() for d in days], type=pa.large_string()),
        "entropy_shannon": pa.array([1.0] * len(days), type=pa.float64()),
    })
    pq.write_table(table, str(path))


def _rango(inicio: date, fin: date) -> list[date]:
    n = (fin - inicio).days + 1
    return [inicio + timedelta(days=i) for i in range(n)]


# ─── data_lake_root: sin fallback a ruta hardcodeada ──────────────────────

class TestDataLakeRoot:
    def test_argumento_explicito_gana(self, monkeypatch):
        monkeypatch.setenv(DATA_LAKE_ROOT_ENV_VAR, "/de/la/env")
        assert data_lake_root("/explicito") == Path("/explicito")

    def test_usa_env_var_si_no_hay_argumento(self, monkeypatch):
        monkeypatch.setenv(DATA_LAKE_ROOT_ENV_VAR, "/de/la/env")
        assert data_lake_root(None) == Path("/de/la/env")

    def test_sin_argumento_ni_env_lanza_en_vez_de_adivinar(self, monkeypatch):
        """Deliberadamente distinto de drive_root(): acá NO hay fallback.
        Auditar la carpeta equivocada en silencio es peor que fallar."""
        monkeypatch.delenv(DATA_LAKE_ROOT_ENV_VAR, raising=False)
        with pytest.raises(ValueError, match="No se indicó la raíz"):
            data_lake_root(None)

    def test_el_mensaje_de_error_no_menciona_ninguna_ruta_de_colab(self, monkeypatch):
        monkeypatch.delenv(DATA_LAKE_ROOT_ENV_VAR, raising=False)
        with pytest.raises(ValueError) as exc:
            data_lake_root(None)
        assert "/content/" not in str(exc.value)


# ─── Utilidades de parseo ─────────────────────────────────────────────────

class TestYearFromFilename:
    def test_extrae_el_anio_del_nombre_legacy(self):
        assert _year_from_filename("BTC_2024_entropy.parquet") == 2024

    def test_none_si_no_hay_anio(self):
        assert _year_from_filename("BTC_ohlcv_v5.parquet") is None

    def test_none_si_hay_dos_anios_ambiguo(self):
        """Con dos años en el nombre no se adivina cuál es el 'correcto'."""
        assert _year_from_filename("BTC_2024_2025_entropy.parquet") is None

    def test_no_confunde_un_numero_largo_con_un_anio(self):
        assert _year_from_filename("BTC_20240115_raw.parquet") is None


class TestToIsoDate:
    def test_string_ya_iso(self):
        assert _to_iso_date("2024-09-09") == "2024-09-09"

    def test_datetime_con_tz(self):
        dt = datetime(2024, 9, 9, 13, 45, tzinfo=timezone.utc)
        assert _to_iso_date(dt) == "2024-09-09"

    def test_date_nativo(self):
        assert _to_iso_date(date(2024, 9, 9)) == "2024-09-09"

    def test_none_no_lanza(self):
        assert _to_iso_date(None) is None

    def test_valor_no_convertible_devuelve_none_sin_lanzar(self):
        assert _to_iso_date(object()) is None


# ─── audit_file: nunca lanza ──────────────────────────────────────────────

class TestAuditFile:
    def test_lee_schema_y_rango_real_del_archivo(self, tmp_path):
        p = tmp_path / "BTC" / "entropy" / "BTC_2024_entropy.parquet"
        _write_schema_bueno(p, _rango(date(2024, 1, 1), date(2024, 12, 31)))

        result = audit_file(p, tmp_path)
        assert result.error is None
        assert result.asset == "BTC"
        assert result.stream == "entropy"
        assert result.n_rows == 366  # 2024 es bisiesto
        assert len(result.columns) == 9
        assert result.date_column == "date"
        assert result.date_is_string is False
        assert result.date_min == "2024-01-01"
        assert result.date_max == "2024-12-31"
        assert result.filename_matches_content is True
        assert result.n_missing_days == 0

    def test_detecta_fecha_guardada_como_string(self, tmp_path):
        p = tmp_path / "BTC" / "entropy" / "BTC_2026_entropy.parquet"
        _write_schema_anomalo(p, _rango(date(2026, 1, 1), date(2026, 1, 10)))

        result = audit_file(p, tmp_path)
        assert result.date_is_string is True
        assert "string" in (result.date_dtype or "").lower()

    def test_detecta_que_el_nombre_miente_sobre_el_contenido(self, tmp_path):
        """Regresión del caso real: BTC_2026_entropy.parquet contenía
        2024-09-09 .. 2026-03-08, no solo 2026."""
        p = tmp_path / "BTC" / "entropy" / "BTC_2026_entropy.parquet"
        _write_schema_anomalo(p, [date(2024, 9, 9), date(2025, 5, 1), date(2026, 3, 8)])

        result = audit_file(p, tmp_path)
        assert result.year_in_filename == 2026
        assert result.filename_matches_content is False
        assert result.date_min == "2024-09-09"
        assert result.date_max == "2026-03-08"

    def test_cuenta_dias_faltantes_dentro_del_rango(self, tmp_path):
        p = tmp_path / "BTC" / "entropy" / "b.parquet"
        # 10 días de span, solo 3 presentes -> 7 faltantes
        _write_schema_anomalo(p, [date(2024, 1, 1), date(2024, 1, 5), date(2024, 1, 10)])

        result = audit_file(p, tmp_path)
        assert result.n_missing_days == 7

    def test_archivo_corrupto_reporta_error_sin_lanzar(self, tmp_path):
        p = tmp_path / "BTC" / "entropy" / "roto.parquet"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"esto no es un parquet")

        result = audit_file(p, tmp_path)  # no debe lanzar
        assert result.error is not None
        assert result.n_rows == 0

    def test_parquet_sin_columna_de_fecha_reporta_error_sin_lanzar(self, tmp_path):
        p = tmp_path / "BTC" / "entropy" / "sin_fecha.parquet"
        p.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.table({"close": pa.array([1.0, 2.0])}), str(p))

        result = audit_file(p, tmp_path)
        assert result.error is not None
        assert "fecha" in result.error
        assert result.date_column is None


# ─── find_group_findings: lo que solo se ve comparando archivos ───────────

class TestGroupFindings:
    def test_reproduce_el_hallazgo_real_btc_2024_vs_2026(self, tmp_path):
        """REGRESIÓN DIRECTA del hallazgo del 2026-08-18 en el Drive real:
        dos archivos del mismo activo y stream, con juegos de columnas
        distintos (9 vs 2), tipos de fecha distintos (timestamp vs string),
        rangos que se solapan, y un nombre que miente sobre su contenido."""
        base = tmp_path / "BTC" / "entropy"
        _write_schema_bueno(base / "BTC_2024_entropy.parquet",
                            _rango(date(2024, 1, 1), date(2024, 12, 31)))
        _write_schema_anomalo(base / "BTC_2026_entropy.parquet",
                              [date(2024, 9, 9), date(2025, 5, 1), date(2026, 3, 8)])

        audit = audit_lake(tmp_path)
        kinds = {f.kind for f in audit.findings}

        assert "schema_drift" in kinds       # 9 columnas vs 2
        assert "date_as_string" in kinds     # string vs timestamp
        assert "filename_mismatch" in kinds  # '2026' pero contiene 2024..2026
        assert "overlap" in kinds            # 2024 y 2026 comparten días

    def test_detecta_drift_de_tipo_de_fecha_explicitamente(self, tmp_path):
        base = tmp_path / "XAU" / "entropy"
        _write_schema_bueno(base / "a.parquet", _rango(date(2024, 1, 1), date(2024, 1, 5)))
        _write_schema_anomalo(base / "b.parquet", _rango(date(2025, 1, 1), date(2025, 1, 5)))

        findings = find_group_findings([
            audit_file(base / "a.parquet", tmp_path),
            audit_file(base / "b.parquet", tmp_path),
        ])
        drift = [f for f in findings if f.kind == "schema_drift" and "fecha" in f.detail]
        assert len(drift) == 1
        assert "timestamp" in drift[0].detail and "string" in drift[0].detail

    def test_grupo_consistente_no_produce_ningun_hallazgo(self, tmp_path):
        base = tmp_path / "NVDA" / "entropy"
        _write_schema_bueno(base / "NVDA_2023_entropy.parquet",
                            _rango(date(2023, 1, 1), date(2023, 12, 31)), asset="NVDA")
        _write_schema_bueno(base / "NVDA_2024_entropy.parquet",
                            _rango(date(2024, 1, 1), date(2024, 12, 31)), asset="NVDA")

        audit = audit_lake(tmp_path)
        assert audit.findings == []

    def test_activos_distintos_no_se_comparan_entre_si(self, tmp_path):
        """BTC con un schema y NVDA con otro NO es drift -- son grupos
        distintos. Solo importa la inconsistencia dentro del mismo grupo."""
        _write_schema_bueno(tmp_path / "BTC" / "entropy" / "a.parquet",
                            _rango(date(2024, 1, 1), date(2024, 1, 5)))
        _write_schema_anomalo(tmp_path / "NVDA" / "entropy" / "b.parquet",
                              _rango(date(2024, 1, 1), date(2024, 1, 5)))

        findings = audit_lake(tmp_path).findings
        assert not [f for f in findings if f.kind == "schema_drift"]

    def test_rangos_contiguos_sin_solapar_no_disparan_overlap(self, tmp_path):
        base = tmp_path / "BTC" / "ohlcv"
        _write_schema_bueno(base / "a.parquet", _rango(date(2024, 1, 1), date(2024, 1, 10)))
        _write_schema_bueno(base / "b.parquet", _rango(date(2024, 1, 11), date(2024, 1, 20)))

        findings = audit_lake(tmp_path).findings
        assert not [f for f in findings if f.kind == "overlap"]

    def test_archivos_con_error_no_participan_de_los_hallazgos(self, tmp_path):
        base = tmp_path / "BTC" / "entropy"
        _write_schema_bueno(base / "ok.parquet", _rango(date(2024, 1, 1), date(2024, 1, 5)))
        base.mkdir(parents=True, exist_ok=True)
        (base / "roto.parquet").write_bytes(b"basura")

        audit = audit_lake(tmp_path)
        assert audit.n_files == 2
        assert audit.findings == []  # un archivo ilegible no inventa un drift


# ─── Cobertura: la respuesta a "¿qué falta descargar?" ────────────────────

class TestCoverage:
    def test_agrega_rango_total_por_activo_y_stream(self, tmp_path):
        base = tmp_path / "BTC" / "entropy"
        _write_schema_bueno(base / "a.parquet", _rango(date(2023, 1, 1), date(2023, 12, 31)))
        _write_schema_bueno(base / "b.parquet", _rango(date(2024, 1, 1), date(2024, 12, 31)))

        audit = audit_lake(tmp_path, today=date(2026, 8, 18))
        cov = audit.coverage["BTC/entropy"]
        assert cov["date_min"] == "2023-01-01"
        assert cov["date_max"] == "2024-12-31"
        assert cov["n_files"] == 2
        assert cov["n_rows"] == 365 + 366

    def test_days_behind_today_dice_cuanto_falta_descargar(self, tmp_path):
        p = tmp_path / "BTC" / "entropy" / "a.parquet"
        _write_schema_bueno(p, _rango(date(2026, 8, 1), date(2026, 8, 10)))

        coverage = build_coverage([audit_file(p, tmp_path)], today=date(2026, 8, 18))
        assert coverage["BTC/entropy"]["days_behind_today"] == 8

    def test_archivos_sin_fecha_legible_no_entran_en_coverage(self, tmp_path):
        p = tmp_path / "BTC" / "entropy" / "roto.parquet"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"basura")
        assert build_coverage([audit_file(p, tmp_path)]) == {}


# ─── audit_lake y reporte ─────────────────────────────────────────────────

class TestAuditLake:
    def test_recorre_subcarpetas_anidadas(self, tmp_path):
        """El layout real tiene profundidad variable:
        ohlcv/aggregated/, gdelt/raw/ -- no una profundidad fija."""
        _write_schema_bueno(
            tmp_path / "BTC" / "ohlcv" / "aggregated" / "BTC_ohlcv_v5.parquet",
            _rango(date(2024, 1, 1), date(2024, 1, 5)),
        )
        audit = audit_lake(tmp_path)
        assert audit.n_files == 1
        assert audit.files[0].asset == "BTC"
        assert audit.files[0].stream == "ohlcv"

    def test_lake_vacio_no_lanza(self, tmp_path):
        audit = audit_lake(tmp_path)
        assert audit.n_files == 0
        assert audit.files == []
        assert audit.findings == []

    def test_reporte_es_texto_legible_y_menciona_los_hallazgos(self, tmp_path):
        base = tmp_path / "BTC" / "entropy"
        _write_schema_bueno(base / "BTC_2024_entropy.parquet",
                            _rango(date(2024, 1, 1), date(2024, 12, 31)))
        _write_schema_anomalo(base / "BTC_2026_entropy.parquet",
                              [date(2024, 9, 9), date(2026, 3, 8)])

        texto = format_report(audit_lake(tmp_path))
        assert "AUDITORÍA DEL DATA LAKE" in texto
        assert "BTC/entropy" in texto
        assert "schema_drift" in texto

    def test_reporte_de_lake_vacio_lo_dice_explicitamente(self, tmp_path):
        texto = format_report(audit_lake(tmp_path))
        assert "Ninguno" in texto or "ningún" in texto
