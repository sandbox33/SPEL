"""
tests/test_import_gdelt_entropy.py
===================================
Cobertura de tools/import_gdelt_entropy.py.

Dos tests sostienen el resto:

`test_timestamp_al_oeste_de_utc_no_corre_el_dia`: un corrimiento de un día
desalinea el join OHLCV↔GDELT entero, y nadie lo nota hasta que el modelo no
aprende. Es el modo de fallo silencioso más caro de este import.

`test_correrlo_dos_veces_no_duplica_dias`: `append_day()` es append puro y no
deduplica. Sin la comprobación previa contra la serie existente, cada corrida
duplicaría el JSONL.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

import tools.import_gdelt_entropy as mod
from ingestion.gdelt_aggregation import MIN_EVENTS_FOR_VALID_DAY
from ingestion.gdelt_series import _series_file_path, read_series
from tools.import_gdelt_entropy import (
    a_dia_utc,
    es_archivo_excluido,
    importar_asset,
    main,
)

pytest.importorskip("pyarrow", reason="el origen es parquet")

D0 = datetime(2015, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    """Lake y Drive aislados. Nada toca el Drive real."""
    lake = tmp_path / "lake"
    drive = tmp_path / "drive"
    lake.mkdir()
    drive.mkdir()
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(drive))
    return lake, drive


def escribir_parquet(
    lake, asset: str, *, n: int = 10, n_events=None, saltar: set[int] | None = None,
    tz=timezone.utc, columnas_extra: bool = True, nombre: str | None = None,
):
    """Parquet de origen sintético con la forma verificada del real."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    saltar = saltar or set()
    idx = [k for k in range(n) if k not in saltar]
    base = D0.astimezone(tz) if tz else D0.replace(tzinfo=None)
    fechas = [base + timedelta(days=k) for k in idx]
    eventos = ([n_events] * len(idx) if isinstance(n_events, int)
               else (n_events or [100] * len(idx)))

    datos = {
        "date": pa.array(fechas, type=pa.timestamp("us", tz=str(tz)) if tz
                         else pa.timestamp("us")),
        "asset": [asset] * len(idx),
        "entropy_shannon": [1.0 + k * 0.01 for k in range(len(idx))],
        "zipf_concentration": [0.5] * len(idx),
        "goldstein_mean": [1.8] * len(idx),
        "tone_variance": [2.0] * len(idx),
        "n_events": eventos,
    }
    if columnas_extra:
        datos["nash_frozen_7d"] = [0.1] * len(idx)
        datos["vitality_tesla"] = [9] * len(idx)

    destino = lake / (nombre or f"{asset}_gdelt_entropy.parquet")
    pq.write_table(pa.table(datos), destino)
    return destino


# ══════════════════════════════════════════════════════════════════════════
#  Timezone — conversión EXPLÍCITA a día UTC
# ══════════════════════════════════════════════════════════════════════════

def test_timestamp_al_oeste_de_utc_no_corre_el_dia():
    """El caso que hace daño: 2015-01-01 23:00-05:00 ES 2015-01-02 en UTC.
    Un `.date()` ingenuo daría el 1 y desalinearía el join entero."""
    ts = datetime(2015, 1, 1, 23, 0, tzinfo=timezone(timedelta(hours=-5)))

    assert a_dia_utc(ts) == date(2015, 1, 2)
    assert ts.date() == date(2015, 1, 1), "el .date() ingenuo sí se corre"


def test_timestamp_al_este_de_utc_tampoco_corre_el_dia():
    ts = datetime(2015, 1, 2, 2, 0, tzinfo=timezone(timedelta(hours=9)))

    assert a_dia_utc(ts) == date(2015, 1, 1)


def test_timestamp_utc_conserva_su_dia():
    assert a_dia_utc(datetime(2015, 3, 7, 12, 0, tzinfo=timezone.utc)) == date(2015, 3, 7)
    assert a_dia_utc(datetime(2015, 3, 7, 0, 0, tzinfo=timezone.utc)) == date(2015, 3, 7)
    assert a_dia_utc(datetime(2015, 3, 7, 23, 59, tzinfo=timezone.utc)) == date(2015, 3, 7)


@pytest.mark.parametrize("valor,esperado", [
    ("2015-06-13", date(2015, 6, 13)),
    (date(2015, 6, 13), date(2015, 6, 13)),
    (datetime(2015, 6, 13, 10, 0), date(2015, 6, 13)),  # naive
    (None, None),
    ("no es fecha", None),
])
def test_otras_formas_de_fecha_se_delegan_al_helper_auditado(valor, esperado):
    assert a_dia_utc(valor) == esperado


def test_el_dia_escrito_coincide_con_el_del_parquet(entorno):
    """Extremo a extremo: lo que llega a la serie es el día UTC del origen."""
    lake, _ = entorno
    escribir_parquet(lake, "BTC", n=5)

    importar_asset("BTC", lake_root=lake, patron=mod.PATRON_DEFAULT, write=True)

    dias = [r.day for r in read_series("BTC")]
    assert dias == [date(2015, 1, 1) + timedelta(days=k) for k in range(5)]


# ══════════════════════════════════════════════════════════════════════════
#  Idempotencia
# ══════════════════════════════════════════════════════════════════════════

def test_correrlo_dos_veces_no_duplica_dias(entorno):
    """`append_day()` es append puro y NO deduplica (su docstring delega en
    read_series). Sin la comprobación previa, la segunda corrida duplicaría
    cada línea del JSONL."""
    lake, _ = entorno
    escribir_parquet(lake, "BTC", n=20)

    primera = importar_asset("BTC", lake_root=lake, patron=mod.PATRON_DEFAULT, write=True)
    lineas_1 = _series_file_path("BTC").read_text().count("\n")

    segunda = importar_asset("BTC", lake_root=lake, patron=mod.PATRON_DEFAULT, write=True)
    lineas_2 = _series_file_path("BTC").read_text().count("\n")

    assert primera.written == 20 and primera.already_present == 0
    assert segunda.written == 0 and segunda.already_present == 20
    assert lineas_1 == lineas_2 == 20, "el archivo no creció"
    assert len(read_series("BTC")) == 20


def test_un_import_parcial_completa_solo_lo_que_falta(entorno):
    """Caso realista: la serie ya tiene parte del rango."""
    lake, _ = entorno
    escribir_parquet(lake, "BTC", n=10)
    importar_asset("BTC", lake_root=lake, patron=mod.PATRON_DEFAULT, write=True)

    # Origen ampliado: los mismos 10 días más 5 nuevos.
    escribir_parquet(lake, "BTC", n=15)
    segunda = importar_asset("BTC", lake_root=lake, patron=mod.PATRON_DEFAULT, write=True)

    assert segunda.already_present == 10
    assert segunda.written == 5
    assert len(read_series("BTC")) == 15


# ══════════════════════════════════════════════════════════════════════════
#  Dry-run por defecto
# ══════════════════════════════════════════════════════════════════════════

def test_dry_run_es_el_default_y_no_escribe_nada(entorno):
    """Un import que escribe por omisión corrompe por omisión."""
    lake, drive = entorno
    escribir_parquet(lake, "BTC", n=10)

    res = importar_asset("BTC", lake_root=lake, patron=mod.PATRON_DEFAULT, write=False)

    assert res.status == "DRY_RUN"
    assert res.to_write == 10 and res.written == 0
    assert not _series_file_path("BTC").exists()
    assert list(drive.rglob("*.jsonl")) == []


def test_dry_run_reporta_lo_que_escribiria(entorno, capsys):
    lake, _ = entorno
    escribir_parquet(lake, "BTC", n=10)

    codigo = main(["--assets", "BTC", "--lake-root", str(lake)])

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "DRY-RUN" in salida
    assert "a escribir: 10" in salida
    assert "falta --write" in salida


def test_con_write_si_escribe(entorno, capsys):
    lake, _ = entorno
    escribir_parquet(lake, "BTC", n=10)

    main(["--assets", "BTC", "--lake-root", str(lake), "--write"])

    assert "MODO ESCRITURA" in capsys.readouterr().out
    assert len(read_series("BTC")) == 10


# ══════════════════════════════════════════════════════════════════════════
#  Mapeo origen -> destino
# ══════════════════════════════════════════════════════════════════════════

def test_n_events_bajo_el_umbral_marca_insufficient_y_nula_las_senales(entorno):
    """El productor canónico devuelve las 4 señales en None cuando
    n_events < MIN_EVENTS_FOR_VALID_DAY. Arrastrar el valor del parquet haría
    que `_build_windows` (que filtra por entropy_shannon is not None) contara
    esos días como válidos mientras la bandera dice lo contrario."""
    lake, _ = entorno
    escribir_parquet(lake, "BTC", n=4, n_events=[100, 2, 100, 0])

    res = importar_asset("BTC", lake_root=lake, patron=mod.PATRON_DEFAULT, write=True)

    assert res.insufficient_rows == 2
    assert res.signals_nulled == 2, "se reporta, no se descarta en silencio"

    serie = read_series("BTC")
    insuficientes = [r for r in serie if r.insufficient_events]
    assert len(insuficientes) == 2
    for r in insuficientes:
        assert r.entropy_shannon is None
        assert r.zipf_concentration is None
        assert r.goldstein_mean is None
        assert r.tone_variance is None
        assert r.n_events in (2, 0), "n_events sí se conserva"


def test_el_umbral_es_el_del_modulo_canonico(entorno):
    """No se reimplementa el 5: se importa la constante."""
    assert mod.MIN_EVENTS_FOR_VALID_DAY is MIN_EVENTS_FOR_VALID_DAY

    lake, _ = entorno
    escribir_parquet(lake, "BTC", n=2,
                     n_events=[MIN_EVENTS_FOR_VALID_DAY, MIN_EVENTS_FOR_VALID_DAY - 1])

    importar_asset("BTC", lake_root=lake, patron=mod.PATRON_DEFAULT, write=True)

    serie = read_series("BTC")
    assert serie[0].insufficient_events is False   # == umbral: alcanza
    assert serie[1].insufficient_events is True    # uno menos: no


def test_las_siete_columnas_directas_llegan_intactas(entorno):
    lake, _ = entorno
    escribir_parquet(lake, "BTC", n=3)

    importar_asset("BTC", lake_root=lake, patron=mod.PATRON_DEFAULT, write=True)

    r = read_series("BTC")[0]
    assert r.asset == "BTC"
    assert r.entropy_shannon == pytest.approx(1.0)
    assert r.zipf_concentration == pytest.approx(0.5)
    assert r.goldstein_mean == pytest.approx(1.8)
    assert r.tone_variance == pytest.approx(2.0)
    assert r.n_events == 100
    assert r.insufficient_events is False


def test_los_campos_sobrantes_se_reportan_no_se_descartan_en_silencio(entorno):
    """nash_frozen_7d y vitality_tesla no tienen destino. El consumidor
    recalcula la vitalidad de forma causal, así que arrastrar el valor del
    parquet sería un segundo origen de verdad para el mismo dato."""
    lake, _ = entorno
    escribir_parquet(lake, "BTC", n=7)

    res = importar_asset("BTC", lake_root=lake, patron=mod.PATRON_DEFAULT, write=False)

    assert res.dropped_fields == {"nash_frozen_7d": 7, "vitality_tesla": 7}


def test_los_campos_sobrantes_no_se_inventan_en_destino(entorno):
    lake, _ = entorno
    escribir_parquet(lake, "BTC", n=3)

    importar_asset("BTC", lake_root=lake, patron=mod.PATRON_DEFAULT, write=True)

    r = read_series("BTC")[0]
    assert not hasattr(r, "nash_frozen_7d")
    assert not hasattr(r, "vitality_tesla")


# ══════════════════════════════════════════════════════════════════════════
#  Huecos de calendario — dato, no error
# ══════════════════════════════════════════════════════════════════════════

def test_los_dias_faltantes_se_reportan_y_no_se_rellenan(entorno):
    """3998 filas contra 4018 de calendario. El hueco es información sobre
    la fuente; rellenarlo sería inventar días."""
    lake, _ = entorno
    # 30 días de calendario, faltan el 5 y del 10 al 14 (racha de 5).
    escribir_parquet(lake, "BTC", n=30, saltar={5, 10, 11, 12, 13, 14})

    res = importar_asset("BTC", lake_root=lake, patron=mod.PATRON_DEFAULT, write=True)

    assert res.rows_read == 24
    assert res.calendar_days == 30
    assert res.missing_days == 6
    assert len(read_series("BTC")) == 24, "no se rellenó ninguno"


def test_las_rachas_agrupan_dias_contiguos(entorno):
    """18 días seguidos son UN hueco, no 18 hallazgos sueltos."""
    lake, _ = entorno
    escribir_parquet(lake, "BTC", n=30, saltar={5, 10, 11, 12, 13, 14})

    res = importar_asset("BTC", lake_root=lake, patron=mod.PATRON_DEFAULT, write=False)

    assert len(res.missing_runs) == 2
    assert any("5 días" in r for r in res.missing_runs)


# ══════════════════════════════════════════════════════════════════════════
#  Los _2026_entropy.parquet nunca son entrada
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("nombre", [
    "BTC_2026_entropy.parquet", "XAU_2026_entropy.parquet",
    "NIFTY50_2026_entropy.parquet",
])
def test_los_2026_estan_excluidos(nombre):
    assert es_archivo_excluido(mod.Path(nombre)) is True


def test_el_guardian_no_depende_del_patron(entorno):
    """Aunque alguien pase un --pattern que los alcance, quedan afuera:
    están corruptos (2 columnas contra 9, sin timezone, y NIFTY50_2026
    arranca en 2015-10-17 pese a su nombre)."""
    lake, _ = entorno
    escribir_parquet(lake, "BTC", n=5, nombre="BTC_2026_entropy.parquet")

    res = importar_asset("BTC", lake_root=lake,
                         patron="{asset}_2026_entropy.parquet", write=True)

    assert res.status == "EXCLUIDO"
    assert res.written == 0
    assert not _series_file_path("BTC").exists()


def test_el_patron_normal_no_alcanza_a_los_2026(entorno):
    lake, _ = entorno
    escribir_parquet(lake, "BTC", n=5, nombre="BTC_2026_entropy.parquet")

    res = importar_asset("BTC", lake_root=lake, patron=mod.PATRON_DEFAULT, write=True)

    assert res.status == "SIN_ORIGEN"


# ══════════════════════════════════════════════════════════════════════════
#  Casos de origen ausente o incompatible
# ══════════════════════════════════════════════════════════════════════════

def test_sin_archivo_de_origen_lo_dice_con_la_ruta(entorno):
    lake, _ = entorno

    res = importar_asset("XAU", lake_root=lake, patron=mod.PATRON_DEFAULT, write=True)

    assert res.status == "SIN_ORIGEN"
    assert "XAU_gdelt_entropy.parquet" in res.source_path
    assert res.written == 0


def test_esquema_sin_columnas_imprescindibles_no_escribe(entorno):
    """Sin `date` no hay día, y sin `n_events` no se puede derivar
    insufficient_events. Se reporta qué falta y qué vino."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    lake, _ = entorno
    pq.write_table(pa.table({"otra": [1, 2], "cosa": [3, 4]}),
                   lake / "BTC_gdelt_entropy.parquet")

    res = importar_asset("BTC", lake_root=lake, patron=mod.PATRON_DEFAULT, write=True)

    assert res.status == "ESQUEMA_INCOMPATIBLE"
    assert set(res.missing_columns) == {"date", "n_events"}
    assert res.written == 0
    assert not _series_file_path("BTC").exists()


def test_filas_sin_fecha_legible_se_saltean_y_se_reportan(entorno):
    import pyarrow as pa
    import pyarrow.parquet as pq
    lake, _ = entorno
    pq.write_table(pa.table({
        "date": ["2015-01-01", None, "2015-01-03"],
        "n_events": [100, 100, 100],
        "entropy_shannon": [1.0, 1.0, 1.0],
    }), lake / "BTC_gdelt_entropy.parquet")

    res = importar_asset("BTC", lake_root=lake, patron=mod.PATRON_DEFAULT, write=True)

    assert res.written == 2
    assert any("sin fecha legible" in n for n in res.notes)


# ══════════════════════════════════════════════════════════════════════════
#  Contrato del proceso
# ══════════════════════════════════════════════════════════════════════════

def test_raiz_inexistente_es_fallo_de_invocacion(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(tmp_path))

    codigo = main(["--assets", "BTC", "--lake-root", str(tmp_path / "no_existe")])

    assert codigo == 2
    assert "no existe" in capsys.readouterr().err


def test_sin_raiz_ni_env_var_falla_claro(tmp_path, capsys, monkeypatch):
    """Misma regla que audit_data_lake: nunca una ruta de Colab por
    defecto — importar desde la carpeta equivocada en silencio es peor."""
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(tmp_path))
    monkeypatch.delenv("SPEL_DATA_LAKE_ROOT", raising=False)

    codigo = main(["--assets", "BTC"])

    assert codigo == 2
    assert "ERROR" in capsys.readouterr().err


def test_activo_sin_origen_no_tumba_el_import_de_los_demas(entorno, capsys):
    lake, _ = entorno
    escribir_parquet(lake, "BTC", n=5)

    codigo = main(["--assets", "BTC", "NO_EXISTE", "--lake-root", str(lake), "--write"])

    salida = capsys.readouterr().out
    assert codigo == 0, "exit 0: un activo sin origen es un resultado"
    assert "SIN_ORIGEN" in salida
    assert len(read_series("BTC")) == 5


def test_salida_json_parseable(entorno, capsys):
    import json
    lake, _ = entorno
    escribir_parquet(lake, "BTC", n=5)

    main(["--assets", "BTC", "--lake-root", str(lake), "--format", "json"])

    rep = json.loads(capsys.readouterr().out)
    assert rep["write"] is False
    assert rep["min_events_for_valid_day"] == MIN_EVENTS_FOR_VALID_DAY
    assert rep["assets"][0]["to_write"] == 5


# ══════════════════════════════════════════════════════════════════════════
#  Reglas duras
# ══════════════════════════════════════════════════════════════════════════

def test_se_escribe_solo_por_la_api_publica(entorno, monkeypatch):
    """Nunca formateando JSONL a mano: `append_day` es el único camino.
    Se espía que cada día escrito pase por él."""
    lake, _ = entorno
    escribir_parquet(lake, "BTC", n=6)

    llamadas = []
    real = mod.append_day

    def espia(result):
        llamadas.append(result)
        return real(result)

    monkeypatch.setattr(mod, "append_day", espia)
    res = importar_asset("BTC", lake_root=lake, patron=mod.PATRON_DEFAULT, write=True)

    assert len(llamadas) == res.written == 6
    assert all(type(r).__name__ == "DailyAggregationResult" for r in llamadas)


def test_el_tool_no_formatea_jsonl_a_mano():
    """Ni serializa registros ni abre el .jsonl: eso duplicaría
    `_result_to_line()` y crearía dos formatos que pueden divergir.

    Se verifica con AST y no con grep sobre el texto: el docstring del módulo
    MENCIONA `_result_to_line` justamente para explicar por qué no lo
    duplica, y un grep daría un falso positivo permanente — el test se
    terminaría borrando por inútil."""
    import ast
    import inspect
    from pathlib import Path as _P

    arbol = ast.parse(_P(inspect.getfile(mod)).read_text())

    importados: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ImportFrom):
            importados.update(a.name for a in nodo.names)
    assert "_result_to_line" not in importados, "no se reusa el serializador privado"
    assert "_series_file_path" not in importados, "el tool no resuelve la ruta él mismo"

    # Ninguna llamada a open() en todo el módulo: la escritura es de
    # append_day, no de acá.
    llamadas = {n.func.id for n in ast.walk(arbol)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "open" not in llamadas


def test_no_importa_archive_ni_librerias_prohibidas():
    import ast
    import inspect
    from pathlib import Path as _P

    arbol = ast.parse(_P(inspect.getfile(mod)).read_text())
    importados: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            importados.update(a.name for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            importados.add(nodo.module)

    raices = {m.split(".")[0] for m in importados}
    assert not ({"torch", "sklearn", "yfinance", "requests"} & raices), importados
    assert not any(m.startswith("archive") for m in importados), importados
