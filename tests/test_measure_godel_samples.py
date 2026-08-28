"""
tests/test_measure_godel_samples.py
====================================
Cobertura de tools/measure_godel_samples.py.

El test que más importa de este archivo es
`test_p90_de_cada_fold_solo_ve_dias_anteriores_a_su_validacion`: la regla de
integridad temporal es la única cuyo incumplimiento produce un resultado que
se ve BIEN y no significa nada. Un P90 que vio el futuro selecciona las
muestras con información que en producción no existe, y el número que sale
de ahí es exactamente el que invalidó el trabajo anterior. Ese test espía el
argumento real que recibe compute_adaptive_percentile, no el resultado.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

import tools.measure_godel_samples as mod
from ingestion.gdelt_aggregation import DailyAggregationResult
from ingestion.gdelt_series import append_day
from tools.measure_godel_samples import (
    FOLD_MIN_CLASE,
    FOLD_MIN_N,
    OOF_MIN_DEFENDIBLE,
    OOF_MIN_PARA_CORRER,
    FoldMeasurement,
    SerieDerivada,
    VerdictLevel,
    _log_returns,
    dictaminar,
    main,
    measure_folds,
    walk_forward_splits,
)

D0 = date(2024, 1, 1)


def _serie(
    n: int, *, entropy=None, vitality=None, log_return=None, forward_filled=None
) -> SerieDerivada:
    """Serie derivada sintética con control total de cada columna.
    `forward_filled` default en False: sin arrastre salvo que el test lo
    pida explícitamente."""
    return SerieDerivada(
        fechas=[D0 + timedelta(days=k) for k in range(n)],
        entropy=np.array(entropy if entropy is not None else [1.0] * n, dtype=float),
        vitality=np.array(vitality if vitality is not None else [3] * n, dtype=int),
        log_return=np.array(
            log_return if log_return is not None else [0.01] * n, dtype=float
        ),
        forward_filled=np.array(
            forward_filled if forward_filled is not None else [False] * n, dtype=bool
        ),
    )


def _fold(**kw) -> FoldMeasurement:
    base = dict(
        fold=1, n_train_days=10, val_start="2024-01-01", val_end="2024-02-01",
        p90_used=1.0, p90_source="GLOBAL", p90_n_obs=10,
        n_total=0, n_post_mask=0, n_post_propia=0, n_post_arrastrada=0,
        n_up=0, n_down=0, estable=False,
    )
    base.update(kw)
    return FoldMeasurement(**base)


# ══════════════════════════════════════════════════════════════════════════
#  Integridad temporal — el test que sostiene todo lo demás
# ══════════════════════════════════════════════════════════════════════════

def test_p90_de_cada_fold_solo_ve_dias_anteriores_a_su_validacion(monkeypatch):
    """Espía el argumento `history` que recibe compute_adaptive_percentile en
    cada fold y verifica que sea exactamente el prefijo anterior al inicio de
    la validación — ni un día más. Con el dataset completo el test falla."""
    n = 200
    # Entropía estrictamente creciente: si un fold viera días futuros, su
    # historia contendría valores mayores a los de su propio corte, y eso es
    # detectable sin ambigüedad.
    serie = _serie(n, entropy=[float(i) for i in range(n)])

    historias: list[list[float]] = []
    real = mod.compute_adaptive_percentile

    def espia(history, percentile, global_default, **kw):
        historias.append(list(history))
        return real(history, percentile, global_default, **kw)

    monkeypatch.setattr(mod, "compute_adaptive_percentile", espia)

    folds = measure_folds(serie, lookback=10, n_folds=4, p90_global_default=1.19)

    assert len(historias) == len(folds) == 4
    for fold, historia in zip(folds, historias):
        inicio_val = fold.n_train_days
        # Largo exacto: el prefijo, nada más.
        assert len(historia) == inicio_val
        # Y ningún valor del futuro: con entropía = índice, el máximo de la
        # historia tiene que ser el día anterior al corte.
        assert max(historia) == float(inicio_val - 1)
        assert all(v < inicio_val for v in historia)


def test_el_p90_cambia_entre_folds_cuando_la_historia_cambia():
    """Corolario observable de lo anterior: si el P90 fuera global, sería el
    mismo número en los cuatro folds."""
    serie = _serie(200, entropy=[float(i) for i in range(200)])

    folds = measure_folds(serie, lookback=10, n_folds=4, p90_global_default=1.19)
    p90s = [f.p90_used for f in folds]

    assert len(set(p90s)) == len(p90s), f"P90 repetido entre folds: {p90s}"
    assert p90s == sorted(p90s)  # historia creciente -> percentil creciente


# ══════════════════════════════════════════════════════════════════════════
#  Target y ventana — portados literal del legacy
# ══════════════════════════════════════════════════════════════════════════

def test_log_return_primer_dia_es_nan_y_el_resto_es_ln_del_cociente():
    out = _log_returns([100.0, 110.0, 99.0])

    assert np.isnan(out[0])
    assert out[1] == pytest.approx(np.log(110 / 100))
    assert out[2] == pytest.approx(np.log(99 / 110))


def test_target_sube_si_log_return_positivo_baja_si_no():
    """`y.append(1.0 if arr_raw[i, 2] > 0 else 0.0)` — el cero cuenta como
    'baja', igual que el legacy (es `> 0`, no `>= 0`)."""
    n = 60
    # Intercalados, no en bloques: si los ceros quedaran todos en el train,
    # la validación no vería nunca esa clase y el test no probaría nada.
    serie = _serie(
        n, vitality=[9] * n,  # máscara siempre activa: aísla el target
        log_return=[0.5 if i % 2 else 0.0 for i in range(n)],
    )

    folds = measure_folds(serie, lookback=0, n_folds=1, p90_global_default=99.0)
    total_up = sum(f.n_up for f in folds)
    total_down = sum(f.n_down for f in folds)

    # El 0.0 cuenta como "baja": la condición del legacy es `> 0`, no `>= 0`.
    assert total_up > 0 and total_down > 0
    assert total_up == total_down
    assert total_up + total_down == sum(f.n_post_mask for f in folds)


def test_indices_por_debajo_del_lookback_no_se_cuentan():
    """Piso del legacy: `for i in range(lookback, len(arr))`. Un índice sin
    ventana completa hacia atrás no es una muestra utilizable."""
    n = 100
    serie = _serie(n, vitality=[9] * n)

    laxo = measure_folds(serie, lookback=0, n_folds=1, p90_global_default=99.0)
    estricto = measure_folds(serie, lookback=90, n_folds=1, p90_global_default=99.0)

    assert sum(f.n_total for f in laxo) > sum(f.n_total for f in estricto)


def test_lookback_mayor_que_la_serie_da_cero_muestras_sin_lanzar():
    serie = _serie(50, vitality=[9] * 50)

    folds = measure_folds(serie, lookback=500, n_folds=2, p90_global_default=99.0)

    assert sum(f.n_post_mask for f in folds) == 0


# ══════════════════════════════════════════════════════════════════════════
#  La máscara Gödel — se delega en core.scoring, no se reimplementa
# ══════════════════════════════════════════════════════════════════════════

def test_vitality_9_activa_la_mascara_aunque_la_entropia_sea_baja():
    n = 100
    serie = _serie(n, entropy=[0.0] * n, vitality=[9] * n)

    folds = measure_folds(serie, lookback=0, n_folds=1, p90_global_default=99.0)

    assert sum(f.n_post_mask for f in folds) == sum(f.n_total for f in folds)


def test_entropia_bajo_p90_y_vitality_distinto_de_9_no_pasa_la_mascara():
    n = 100
    serie = _serie(n, entropy=[0.0] * n, vitality=[3] * n)

    folds = measure_folds(serie, lookback=0, n_folds=1, p90_global_default=99.0)

    assert sum(f.n_total for f in folds) > 0   # había candidatos
    assert sum(f.n_post_mask for f in folds) == 0  # ninguno sobrevivió


def test_dias_sin_entropia_o_sin_retorno_no_se_cuentan_como_candidatos():
    n = 80
    entropy = [1.0] * n
    entropy[40] = float("nan")
    serie = _serie(n, entropy=entropy, vitality=[9] * n)

    folds = measure_folds(serie, lookback=0, n_folds=1, p90_global_default=99.0)
    con_nan = sum(f.n_total for f in folds)

    serie_limpia = _serie(n, vitality=[9] * n)
    sin_nan = sum(
        f.n_total for f in
        measure_folds(serie_limpia, lookback=0, n_folds=1, p90_global_default=99.0)
    )

    assert con_nan == sin_nan - 1


# ══════════════════════════════════════════════════════════════════════════
#  Walk-forward — parametrizado, no hardcodeado
# ══════════════════════════════════════════════════════════════════════════

def test_walk_forward_produce_ventanas_contiguas_y_sin_solapamiento():
    splits = walk_forward_splits(100, 4)

    assert len(splits) == 4
    assert splits[0][0] > 0, "el primer bloque queda como train inicial"
    for (_, fin_previo), (ini, _) in zip(splits, splits[1:]):
        assert ini == fin_previo
    assert splits[-1][1] == 100  # el último fold llega hasta el final


@pytest.mark.parametrize("n_folds", [1, 2, 3, 7])
def test_el_numero_de_folds_es_parametro(n_folds):
    serie = _serie(400, vitality=[9] * 400)

    folds = measure_folds(serie, lookback=0, n_folds=n_folds, p90_global_default=99.0)

    assert len(folds) == n_folds


def test_serie_demasiado_corta_para_partir_no_lanza():
    assert walk_forward_splits(3, 5) == []
    assert measure_folds(_serie(3), lookback=0, n_folds=5, p90_global_default=1.0) == []


def test_n_folds_invalido_es_error_de_uso():
    with pytest.raises(ValueError, match="n_folds"):
        walk_forward_splits(100, 0)


# ══════════════════════════════════════════════════════════════════════════
#  Veredicto — los umbrales no se negocian
# ══════════════════════════════════════════════════════════════════════════

def test_veredicto_defendible_en_el_umbral_exacto():
    v, _ = dictaminar(OOF_MIN_DEFENDIBLE, [_fold()])
    assert v == VerdictLevel.DEFENDIBLE


def test_veredicto_no_correr_por_debajo_del_minimo():
    v, _ = dictaminar(OOF_MIN_PARA_CORRER - 1, [_fold()])
    assert v == VerdictLevel.NO_CORRER


def test_banda_intermedia_no_se_disfraza_de_defendible():
    """150 <= OOF < 620 alcanza para explorar y NO para comparar modelos.
    Que sea su propio veredicto evita que se lea como un aprobado."""
    v, motivo = dictaminar(OOF_MIN_PARA_CORRER, [_fold()])

    assert v == VerdictLevel.INSUFICIENTE_PARA_COMPARAR
    assert "NO para comparar" in motivo


def test_sin_folds_es_sin_datos_no_una_excepcion():
    v, _ = dictaminar(0, [])
    assert v == VerdictLevel.SIN_DATOS


def test_fold_estable_exige_n_y_ambas_clases():
    serie_ok = _serie(
        800, vitality=[9] * 800,
        log_return=[0.01 if i % 2 else -0.01 for i in range(800)],
    )
    folds = measure_folds(serie_ok, lookback=0, n_folds=1, p90_global_default=99.0)
    assert folds[0].n_post_mask >= FOLD_MIN_N
    assert min(folds[0].n_up, folds[0].n_down) >= FOLD_MIN_CLASE
    assert folds[0].estable is True

    # Mismo n, una sola clase -> no estable.
    serie_desbalanceada = _serie(800, vitality=[9] * 800, log_return=[0.01] * 800)
    desbalanceado = measure_folds(
        serie_desbalanceada, lookback=0, n_folds=1, p90_global_default=99.0
    )[0]
    assert desbalanceado.n_post_mask >= FOLD_MIN_N
    assert desbalanceado.estable is False


# ══════════════════════════════════════════════════════════════════════════
#  Contrato del proceso — read-only, y "sin datos" no es un fallo
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def entorno(tmp_path, monkeypatch):
    """Drive y OHLCV aislados en tmp_path. Nada toca el Drive real."""
    drive = tmp_path / "drive"
    ohlcv = tmp_path / "ohlcv"
    drive.mkdir()
    ohlcv.mkdir()
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(drive))
    return drive, ohlcv


def _escribir_ohlcv(destino, asset: str, n: int = 300) -> None:
    ts = pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC")
    rng = np.random.default_rng(3)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    pd.DataFrame({
        "timestamp": ts, "open": close, "high": close * 1.01,
        "low": close * 0.99, "close": close, "volume": 0.0,
    }).to_csv(destino / f"{asset}.csv", index=False)


def _escribir_gdelt(asset: str, n: int = 300) -> None:
    rng = np.random.default_rng(5)
    for k in range(n):
        append_day(DailyAggregationResult(
            day=D0 + timedelta(days=k), asset=asset,
            entropy_shannon=float(rng.normal(1.0, 0.25)),
            zipf_concentration=0.5, goldstein_mean=1.8, tone_variance=2.0,
            n_events=int(rng.integers(50, 500)), insufficient_events=False,
        ))


def test_exit_0_aunque_el_veredicto_sea_negativo(entorno, capsys):
    """'No hay datos suficientes' es un RESULTADO, no un fallo del proceso.
    Un exit != 0 acá haría que un CI marque en rojo una medición correcta."""
    _, ohlcv = entorno
    _escribir_ohlcv(ohlcv, "BTC", n=120)
    _escribir_gdelt("BTC", n=120)

    codigo = main([
        "--assets", "BTC", "--p90-global-default", "1.19",
        "--ohlcv-root", str(ohlcv), "--n-folds", "3",
    ])

    assert codigo == 0
    salida = capsys.readouterr().out
    assert "VEREDICTO:" in salida


def test_activo_sin_datos_reporta_cero_y_no_lanza(entorno, capsys):
    _, ohlcv = entorno

    codigo = main([
        "--assets", "NO_EXISTE", "--p90-global-default", "1.19",
        "--ohlcv-root", str(ohlcv),
    ])

    assert codigo == 0
    salida = capsys.readouterr().out
    assert VerdictLevel.SIN_DATOS in salida
    assert "Sin OHLCV" in salida


def test_ruta_raiz_inexistente_si_es_fallo_real(tmp_path, capsys):
    """La distinción: 'este activo no tiene datos' es medición; 'la ruta que
    me diste no existe' es un error de invocación."""
    codigo = main([
        "--assets", "BTC", "--p90-global-default", "1.19",
        "--ohlcv-root", str(tmp_path / "no_existe"),
    ])

    assert codigo == 2
    assert "no existe" in capsys.readouterr().err


def test_el_tool_no_escribe_ningun_archivo(entorno, capsys):
    """Read-only de verdad: se fotografía el árbol antes y después."""
    drive, ohlcv = entorno
    _escribir_ohlcv(ohlcv, "BTC", n=200)
    _escribir_gdelt("BTC", n=200)

    def foto(raiz):
        return {p: p.stat().st_mtime_ns for p in sorted(raiz.rglob("*")) if p.is_file()}

    antes_drive, antes_ohlcv = foto(drive), foto(ohlcv)

    assert main([
        "--assets", "BTC", "--p90-global-default", "1.19",
        "--ohlcv-root", str(ohlcv), "--format", "json",
    ]) == 0

    assert foto(drive) == antes_drive
    assert foto(ohlcv) == antes_ohlcv


def test_salida_json_es_parseable_y_lleva_el_veredicto(entorno, capsys):
    import json
    _, ohlcv = entorno
    _escribir_ohlcv(ohlcv, "BTC", n=200)
    _escribir_gdelt("BTC", n=200)

    main([
        "--assets", "BTC", "--p90-global-default", "1.19",
        "--ohlcv-root", str(ohlcv), "--format", "json",
    ])

    reporte = json.loads(capsys.readouterr().out)
    activo = reporte["assets"][0]
    assert activo["asset"] == "BTC"
    assert activo["verdict"] in vars(VerdictLevel).values()
    assert activo["oof_post_mask"] == sum(f["n_post_mask"] for f in activo["folds"])


def test_reporta_profundidad_de_datos_y_solapamiento(entorno, capsys):
    """Punto 1 del entregable: nadie verificó nunca estos números."""
    import json
    _, ohlcv = entorno
    _escribir_ohlcv(ohlcv, "BTC", n=200)
    _escribir_gdelt("BTC", n=150)

    main([
        "--assets", "BTC", "--p90-global-default", "1.19",
        "--ohlcv-root", str(ohlcv), "--format", "json",
    ])

    a = json.loads(capsys.readouterr().out)["assets"][0]
    assert a["ohlcv_days"] == 200
    assert a["gdelt_days"] == 150
    assert a["overlap_days"] == 150
    assert 0.0 < a["coverage_ratio"] <= 1.0
    assert a["gdelt_first"] is not None and a["ohlcv_last"] is not None


# ══════════════════════════════════════════════════════════════════════════
#  Reglas duras del proyecto
# ══════════════════════════════════════════════════════════════════════════

def test_el_tool_no_importa_torch_sklearn_ni_archive():
    """No entrena. Y no importa nada de archive/*: ese código se LEYÓ para
    portar el target, que es distinto de depender de él."""
    import ast
    import inspect
    from pathlib import Path

    arbol = ast.parse(Path(inspect.getfile(mod)).read_text())
    importados: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            importados.update(a.name for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            importados.add(nodo.module)

    prohibidos = {"torch", "sklearn", "tensorflow", "keras"}
    assert not (prohibidos & {m.split(".")[0] for m in importados}), importados
    assert not any(m.startswith("archive") for m in importados), importados


# ══════════════════════════════════════════════════════════════════════════
#  ARREGLO 1 — descubrimiento de archivos: patrón + subdirectorios
#
#  Los nombres reales del proyecto (BTC_ohlcv_v5.parquet, en carpeta por
#  activo) no coinciden con el patrón que el tool buscaba. El desajuste era
#  el NOMBRE, no solo la carpeta: ninguna cantidad de --ohlcv-root lo
#  arregla sola.
# ══════════════════════════════════════════════════════════════════════════

def _csv_minimo(destino, n: int = 10) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    ts = pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC")
    close = np.linspace(100, 110, n)
    pd.DataFrame({
        "timestamp": ts, "open": close, "high": close, "low": close,
        "close": close, "volume": 0.0,
    }).to_csv(destino, index=False)


def test_patron_por_defecto_conserva_el_comportamiento_anterior(tmp_path):
    """Default = {asset}.csv y {asset}.parquet, plano en la raíz. Lo que ya
    funcionaba tiene que seguir funcionando sin pasar ningún flag nuevo."""
    _csv_minimo(tmp_path / "BTC.csv")

    lookup = mod.load_ohlcv(tmp_path, "BTC")

    assert lookup.df is not None
    assert lookup.path == tmp_path / "BTC.csv"


def test_patron_personalizado_encuentra_el_nombre_real_del_proyecto(tmp_path):
    """El caso que motivó el arreglo: BTC_ohlcv_v5.parquet."""
    _csv_minimo(tmp_path / "BTC_ohlcv_v5.csv")

    sin_patron = mod.load_ohlcv(tmp_path, "BTC")
    con_patron = mod.load_ohlcv(tmp_path, "BTC", patterns=["{asset}_ohlcv_v5.csv"])

    assert sin_patron.df is None, "el patrón default no debe encontrarlo"
    assert con_patron.df is not None
    assert con_patron.path.name == "BTC_ohlcv_v5.csv"


@pytest.mark.parametrize("subdir", ["BTC", "BTC/ohlcv", "ohlcv/BTC"])
def test_encuentra_el_archivo_en_subdirectorio_por_activo(tmp_path, subdir):
    """En Drive cada activo vive en su carpeta, no plano en la raíz."""
    _csv_minimo(tmp_path / subdir / "BTC_ohlcv_v5.csv")

    lookup = mod.load_ohlcv(tmp_path, "BTC", patterns=["{asset}_ohlcv_v5.csv"])

    assert lookup.df is not None
    assert lookup.path.parent.name == subdir.split("/")[-1]


def test_busqueda_recursiva_como_ultimo_recurso(tmp_path):
    """Una profundidad inesperada no debería hacer fallar la medición."""
    _csv_minimo(tmp_path / "a" / "b" / "c" / "BTC_ohlcv_v5.csv")

    lookup = mod.load_ohlcv(tmp_path, "BTC", patterns=["{asset}_ohlcv_v5.csv"])

    assert lookup.df is not None
    assert any("recursiva" in i for i in lookup.intentos)


def test_el_subdirectorio_del_activo_le_gana_al_archivo_suelto_en_la_raiz(tmp_path):
    """Orden de precedencia: lo más específico primero."""
    _csv_minimo(tmp_path / "BTC.csv")
    _csv_minimo(tmp_path / "BTC" / "BTC.csv")

    lookup = mod.load_ohlcv(tmp_path, "BTC")

    assert lookup.path == tmp_path / "BTC" / "BTC.csv"


def test_cuando_no_encuentra_nada_reporta_donde_busco(tmp_path):
    """'No hay datos' y 'busqué en el lugar equivocado' son problemas
    distintos con soluciones distintas, y sin la lista de intentos se ven
    idénticos en el reporte."""
    lookup = mod.load_ohlcv(tmp_path, "BTC", patterns=["{asset}_ohlcv_v5.parquet"])

    assert lookup.df is None
    assert lookup.path is None
    assert lookup.intentos, "tiene que decir qué intentó"
    assert all("BTC_ohlcv_v5.parquet" in i for i in lookup.intentos)
    assert any(str(tmp_path / "BTC") in i for i in lookup.intentos)


def test_el_reporte_de_no_encontrado_lista_rutas_y_patron(tmp_path, monkeypatch, capsys):
    """Extremo a extremo: lo intentado tiene que llegar a la salida."""
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(tmp_path / "drive"))
    (tmp_path / "drive").mkdir()
    vacio = tmp_path / "vacio"
    vacio.mkdir()

    codigo = main([
        "--assets", "BTC", "--p90-global-default", "1.19",
        "--ohlcv-root", str(vacio),
        "--ohlcv-pattern", "{asset}_ohlcv_v5.parquet",
    ])

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "BTC_ohlcv_v5.parquet" in salida
    assert str(vacio) in salida
    assert "--ohlcv-pattern" in salida


def test_el_reporte_dice_de_donde_leyo_cuando_si_encuentra(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(tmp_path / "drive"))
    (tmp_path / "drive").mkdir()
    root = tmp_path / "ohlcv"
    _csv_minimo(root / "BTC" / "BTC_ohlcv_v5.csv", n=120)

    main([
        "--assets", "BTC", "--p90-global-default", "1.19",
        "--ohlcv-root", str(root), "--ohlcv-pattern", "{asset}_ohlcv_v5.csv",
    ])

    assert "OHLCV leído de:" in capsys.readouterr().out


# ══════════════════════════════════════════════════════════════════════════
#  ARREGLO 2 — la columna de fecha puede llamarse "date"
# ══════════════════════════════════════════════════════════════════════════

def test_acepta_la_columna_date_y_la_normaliza_a_timestamp(tmp_path):
    """Los parquets del data lake usan 'date'; el contrato de adapters usa
    'timestamp'. Se acepta cualquiera y se normaliza a una sola."""
    n = 10
    close = np.linspace(100, 110, n)
    pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC"),
        "open": close, "high": close, "low": close, "close": close, "volume": 0.0,
    }).to_csv(tmp_path / "BTC.csv", index=False)

    lookup = mod.load_ohlcv(tmp_path, "BTC")

    assert lookup.df is not None
    assert "timestamp" in lookup.df.columns
    assert "date" not in lookup.df.columns, "se renombra, no se duplica"
    assert str(lookup.df["timestamp"].dt.tz) == "UTC"


def test_timestamp_sigue_funcionando_igual_que_antes(tmp_path):
    _csv_minimo(tmp_path / "BTC.csv")

    lookup = mod.load_ohlcv(tmp_path, "BTC")

    assert "timestamp" in lookup.df.columns


def test_sin_columna_de_fecha_el_error_lista_las_columnas_que_si_vinieron(tmp_path):
    pd.DataFrame({"fecha_rara": [1, 2], "close": [1.0, 2.0]}).to_csv(
        tmp_path / "BTC.csv", index=False
    )

    with pytest.raises(ValueError) as excinfo:
        mod.load_ohlcv(tmp_path, "BTC")

    mensaje = str(excinfo.value)
    assert "fecha_rara" in mensaje and "close" in mensaje
    assert "timestamp" in mensaje and "date" in mensaje


# ══════════════════════════════════════════════════════════════════════════
#  ARREGLO 3 — desglose de forward-fill
#
#  La máscara dispara sobre (entropy >= P90), así que un día puede entrar
#  por una entropía ARRASTRADA que no es suya. Un n de 700 con 400
#  arrastradas no es 700.
# ══════════════════════════════════════════════════════════════════════════

def test_desglosa_propias_y_arrastradas_por_fold():
    n = 200
    # Alterna arrastrada/propia: el desglose tiene que partir el total.
    serie = _serie(
        n, vitality=[9] * n,
        forward_filled=[bool(i % 2) for i in range(n)],
    )

    folds = measure_folds(serie, lookback=0, n_folds=3, p90_global_default=99.0)

    for f in folds:
        assert f.n_post_propia + f.n_post_arrastrada == f.n_post_mask
        assert f.n_post_propia > 0 and f.n_post_arrastrada > 0


def test_sin_arrastre_todo_cuenta_como_propia():
    n = 100
    serie = _serie(n, vitality=[9] * n, forward_filled=[False] * n)

    folds = measure_folds(serie, lookback=0, n_folds=1, p90_global_default=99.0)

    assert sum(f.n_post_arrastrada for f in folds) == 0
    assert sum(f.n_post_propia for f in folds) == sum(f.n_post_mask for f in folds)


def test_todo_arrastrado_deja_el_n_sin_arrastre_en_cero():
    n = 100
    serie = _serie(n, vitality=[9] * n, forward_filled=[True] * n)

    folds = measure_folds(serie, lookback=0, n_folds=1, p90_global_default=99.0)

    assert sum(f.n_post_propia for f in folds) == 0
    assert sum(f.n_post_arrastrada for f in folds) == sum(f.n_post_mask for f in folds)


def test_avisa_cuando_el_veredicto_se_apoya_en_entropia_arrastrada():
    """El caso exacto del brief: un n total que supera el umbral y un n sin
    arrastre que no."""
    aviso = mod.evaluar_arrastre(oof=700, oof_propia=300)

    assert aviso is not None
    assert "700" in aviso and "300" in aviso
    assert "400" in aviso  # cuántas arrastradas
    assert "frágil" in aviso


def test_no_avisa_cuando_el_arrastre_no_cambia_la_banda():
    """None significa 'el arrastre no cambia la lectura', no 'no hay
    arrastre': acá hay 50 arrastradas y las dos cifras superan 620."""
    assert mod.evaluar_arrastre(oof=700, oof_propia=650) is None


def test_avisa_tambien_en_el_umbral_de_no_correr():
    aviso = mod.evaluar_arrastre(oof=OOF_MIN_PARA_CORRER + 10, oof_propia=10)

    assert aviso is not None
    assert str(OOF_MIN_PARA_CORRER) in aviso


def test_el_veredicto_se_sigue_calculando_sobre_el_n_total():
    """El criterio acordado NO cambia: el aviso es información adicional,
    no un veredicto nuevo. Mismo n total -> mismo veredicto, haya o no
    arrastre."""
    con, _ = dictaminar(OOF_MIN_DEFENDIBLE, [_fold(n_post_propia=0,
                                                   n_post_arrastrada=OOF_MIN_DEFENDIBLE)])
    sin, _ = dictaminar(OOF_MIN_DEFENDIBLE, [_fold(n_post_propia=OOF_MIN_DEFENDIBLE,
                                                   n_post_arrastrada=0)])

    assert con == sin == VerdictLevel.DEFENDIBLE


def test_el_agregado_oof_desglosado_aparece_en_el_json(tmp_path, monkeypatch, capsys):
    import json
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(tmp_path / "drive"))
    (tmp_path / "drive").mkdir()
    root = tmp_path / "ohlcv"
    _csv_minimo(root / "BTC.csv", n=200)
    _escribir_gdelt("BTC", n=200)

    main([
        "--assets", "BTC", "--p90-global-default", "1.19",
        "--ohlcv-root", str(root), "--format", "json",
    ])

    a = json.loads(capsys.readouterr().out)["assets"][0]
    assert a["oof_post_propia"] + a["oof_post_arrastrada"] == a["oof_post_mask"]
    assert "arrastre_warning" in a
    for f in a["folds"]:
        assert f["n_post_propia"] + f["n_post_arrastrada"] == f["n_post_mask"]
