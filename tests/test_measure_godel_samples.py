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

import math
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


#: Umbral global tan bajo que ningún día queda por debajo. Reemplaza al
#: idioma viejo `vitality=[9]*n`, que servía para "máscara siempre
#: activa" y dejó de funcionar en la versión 4.0.0: vitality ya no entra
#: en la máscara. Combinado con entropía estrictamente creciente
#: (el default de `_serie`), garantiza que TODOS los días pasen el filtro
#: en cualquiera de los tres regímenes de compute_adaptive_percentile.
UMBRAL_MASCARA_SIEMPRE_ACTIVA = -1.0


def _serie_mascara_activa(n: int, **kw) -> SerieDerivada:
    """Serie donde la máscara acepta todos los días, para aislar lo que el
    test realmente mide (el target, el arrastre, la estabilidad...).
    Entropía estrictamente creciente: cada día supera el tercil superior
    de su propia historia."""
    kw.setdefault("entropy", [float(i) for i in range(n)])
    return _serie(n, **kw)


def _fold(**kw) -> FoldMeasurement:
    base = dict(
        fold=1, n_train_days=10, val_start="2024-01-01", val_end="2024-02-01",
        umbral_used=1.0, umbral_source="GLOBAL", umbral_n_obs=10,
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

    folds = measure_folds(serie, lookback=10, n_folds=4, umbral_global_default=1.19)

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

    folds = measure_folds(serie, lookback=10, n_folds=4, umbral_global_default=1.19)
    p90s = [f.umbral_used for f in folds]

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
    serie = _serie_mascara_activa(   # máscara siempre activa: aísla el target
        n, log_return=[0.5 if i % 2 else 0.0 for i in range(n)],
    )

    folds = measure_folds(serie, lookback=0, n_folds=1,
                          umbral_global_default=UMBRAL_MASCARA_SIEMPRE_ACTIVA)
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

    laxo = measure_folds(serie, lookback=0, n_folds=1, umbral_global_default=99.0)
    estricto = measure_folds(serie, lookback=90, n_folds=1, umbral_global_default=99.0)

    assert sum(f.n_total for f in laxo) > sum(f.n_total for f in estricto)


def test_lookback_mayor_que_la_serie_da_cero_muestras_sin_lanzar():
    serie = _serie(50, vitality=[9] * 50)

    folds = measure_folds(serie, lookback=500, n_folds=2, umbral_global_default=99.0)

    assert sum(f.n_post_mask for f in folds) == 0


# ══════════════════════════════════════════════════════════════════════════
#  La máscara Gödel — se delega en core.scoring, no se reimplementa
# ══════════════════════════════════════════════════════════════════════════

def test_vitality_9_ya_no_activa_la_mascara():
    """Versión 4.0.0. Antes este test se llamaba
    `test_vitality_9_activa_la_mascara_aunque_la_entropia_sea_baja` y
    verificaba justo lo contrario: con entropía 0 y vitality 9, TODOS los
    días pasaban el filtro. Ese era el 72% de los disparos.

    Ahora vitality no entra en la máscara y una entropía plana bajo su
    propio umbral no pasa por ninguna vía."""
    n = 100
    serie = _serie(n, entropy=[0.0] * n, vitality=[9] * n)

    folds = measure_folds(serie, lookback=0, n_folds=1, umbral_global_default=99.0)

    assert sum(f.n_total for f in folds) > 0        # había candidatos
    assert sum(f.n_post_mask for f in folds) == 0   # vitality ya no los salva


def test_entropia_bajo_el_tercil_superior_no_pasa_la_mascara():
    n = 100
    serie = _serie(n, entropy=[0.0] * n, vitality=[3] * n)

    folds = measure_folds(serie, lookback=0, n_folds=1, umbral_global_default=99.0)

    assert sum(f.n_total for f in folds) > 0   # había candidatos
    assert sum(f.n_post_mask for f in folds) == 0  # ninguno sobrevivió


def test_dias_sin_entropia_o_sin_retorno_no_se_cuentan_como_candidatos():
    n = 80
    entropy = [1.0] * n
    entropy[40] = float("nan")
    serie = _serie(n, entropy=entropy, vitality=[9] * n)

    folds = measure_folds(serie, lookback=0, n_folds=1, umbral_global_default=99.0)
    con_nan = sum(f.n_total for f in folds)

    serie_limpia = _serie(n, vitality=[9] * n)
    sin_nan = sum(
        f.n_total for f in
        measure_folds(serie_limpia, lookback=0, n_folds=1, umbral_global_default=99.0)
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

    folds = measure_folds(serie, lookback=0, n_folds=n_folds, umbral_global_default=99.0)

    assert len(folds) == n_folds


def test_serie_demasiado_corta_para_partir_no_lanza():
    assert walk_forward_splits(3, 5) == []
    assert measure_folds(_serie(3), lookback=0, n_folds=5, umbral_global_default=1.0) == []


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
    serie_ok = _serie_mascara_activa(
        800, log_return=[0.01 if i % 2 else -0.01 for i in range(800)],
    )
    folds = measure_folds(serie_ok, lookback=0, n_folds=1,
                          umbral_global_default=UMBRAL_MASCARA_SIEMPRE_ACTIVA)
    assert folds[0].n_post_mask >= FOLD_MIN_N
    assert min(folds[0].n_up, folds[0].n_down) >= FOLD_MIN_CLASE
    assert folds[0].estable is True

    # Mismo n, una sola clase -> no estable.
    serie_desbalanceada = _serie_mascara_activa(800, log_return=[0.01] * 800)
    desbalanceado = measure_folds(
        serie_desbalanceada, lookback=0, n_folds=1,
        umbral_global_default=UMBRAL_MASCARA_SIEMPRE_ACTIVA,
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
        "--assets", "BTC", "--umbral-global-default", "1.19",
        "--ohlcv-root", str(ohlcv), "--n-folds", "3",
    ])

    assert codigo == 0
    salida = capsys.readouterr().out
    assert "VEREDICTO:" in salida


def test_activo_sin_datos_reporta_cero_y_no_lanza(entorno, capsys):
    _, ohlcv = entorno

    codigo = main([
        "--assets", "NO_EXISTE", "--umbral-global-default", "1.19",
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
        "--assets", "BTC", "--umbral-global-default", "1.19",
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
        "--assets", "BTC", "--umbral-global-default", "1.19",
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
        "--assets", "BTC", "--umbral-global-default", "1.19",
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
        "--assets", "BTC", "--umbral-global-default", "1.19",
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
        "--assets", "BTC", "--umbral-global-default", "1.19",
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
        "--assets", "BTC", "--umbral-global-default", "1.19",
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
    serie = _serie_mascara_activa(
        n, forward_filled=[bool(i % 2) for i in range(n)],
    )

    folds = measure_folds(serie, lookback=0, n_folds=3,
                          umbral_global_default=UMBRAL_MASCARA_SIEMPRE_ACTIVA)

    for f in folds:
        assert f.n_post_propia + f.n_post_arrastrada == f.n_post_mask
        assert f.n_post_propia > 0 and f.n_post_arrastrada > 0


def test_sin_arrastre_todo_cuenta_como_propia():
    n = 100
    serie = _serie(n, vitality=[9] * n, forward_filled=[False] * n)

    folds = measure_folds(serie, lookback=0, n_folds=1, umbral_global_default=99.0)

    assert sum(f.n_post_arrastrada for f in folds) == 0
    assert sum(f.n_post_propia for f in folds) == sum(f.n_post_mask for f in folds)


def test_todo_arrastrado_deja_el_n_sin_arrastre_en_cero():
    n = 100
    serie = _serie(n, vitality=[9] * n, forward_filled=[True] * n)

    folds = measure_folds(serie, lookback=0, n_folds=1, umbral_global_default=99.0)

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
        "--assets", "BTC", "--umbral-global-default", "1.19",
        "--ohlcv-root", str(root), "--format", "json",
    ])

    a = json.loads(capsys.readouterr().out)["assets"][0]
    assert a["oof_post_propia"] + a["oof_post_arrastrada"] == a["oof_post_mask"]
    assert "arrastre_warning" in a
    for f in a["folds"]:
        assert f["n_post_propia"] + f["n_post_arrastrada"] == f["n_post_mask"]


# ══════════════════════════════════════════════════════════════════════════
#  CRITERIOS DE PERCENTIL — este tool los MIDE, no los cambia
#
#  El test que más importa es el de frontera temporal: los tres modos tienen
#  que ser causales, y el modo móvil tiene una sutileza propia (su ventana
#  CRUZA la frontera del fold, y eso no es fuga porque nunca mira hacia
#  adelante). El segundo que más importa es el del default: sin el flag, la
#  salida tiene que ser idéntica a la de antes de este PR.
# ══════════════════════════════════════════════════════════════════════════

def test_los_tres_modos_existen_y_el_default_es_el_actual():
    assert mod.PercentileMode.todos() == ("ACUMULADO", "MOVIL", "ZSCORE")
    assert mod.PercentileMode.ACUMULADO == "ACUMULADO"

    import inspect
    firma = inspect.signature(measure_folds)
    assert firma.parameters["percentile_mode"].default == mod.PercentileMode.ACUMULADO


def test_el_default_no_cambia_absolutamente_nada():
    """Sin el flag, la medición tiene que ser byte por byte la de siempre.
    Un tool de diagnóstico que cambia el número por existir no sirve para
    diagnosticar."""
    serie = _serie(300, entropy=[1.0 + (i % 17) * 0.01 for i in range(300)],
                   vitality=[3] * 300)

    explicito = measure_folds(serie, lookback=0, n_folds=3,
                              umbral_global_default=1.05,
                              percentile_mode=mod.PercentileMode.ACUMULADO)
    implicito = measure_folds(serie, lookback=0, n_folds=3,
                              umbral_global_default=1.05)

    assert explicito == implicito


def test_modo_desconocido_es_error_de_uso():
    serie = _serie(100)

    with pytest.raises(ValueError, match="Modo de percentil desconocido"):
        measure_folds(serie, lookback=0, n_folds=1, umbral_global_default=1.0,
                      percentile_mode="A_OJO")


# ── Frontera temporal en los tres ─────────────────────────────────────────

@pytest.mark.parametrize("modo", ["ACUMULADO", "MOVIL", "ZSCORE"])
def test_ningun_modo_usa_datos_posteriores_al_dia_que_evalua(modo, monkeypatch):
    """Espía cada `history` que llega a compute_adaptive_percentile y
    verifica que no contenga ningún valor del futuro. Con entropía = índice,
    un valor del futuro es detectable sin ambigüedad."""
    n = 400
    serie = _serie(n, entropy=[float(i) for i in range(n)])

    historias: list[list[float]] = []
    real = mod.compute_adaptive_percentile

    def espia(history, percentile, global_default, **kw):
        historias.append(list(history))
        return real(history, percentile, global_default, **kw)

    monkeypatch.setattr(mod, "compute_adaptive_percentile", espia)

    umbrales = mod.resolver_umbrales(
        serie, 300, 400, mode=modo, window=50, umbral_global_default=1.0,
    )

    assert historias, "el modo tiene que consultar el percentil"
    assert umbrales.por_dia, "tiene que haber un umbral por día"
    if modo == "ZSCORE":
        # En espacio z los valores ya no son el índice; lo verificable es que
        # la historia se arma solo con días anteriores al fold.
        assert all(len(h) <= 300 for h in historias)
    else:
        # Ningún valor puede alcanzar el índice del primer día evaluado.
        assert all(max(h) < 400 for h in historias if h)


def test_la_ventana_movil_termina_en_el_dia_anterior(monkeypatch):
    """El desplazamiento de un día es la diferencia entre medir y hacer
    trampa: un día evaluado contra un percentil que lo incluye se está
    comparando con un estadístico que él mismo movió."""
    n = 300
    serie = _serie(n, entropy=[float(i) for i in range(n)])

    vistas: list[list[float]] = []
    real = mod.compute_adaptive_percentile

    def espia(history, percentile, global_default, **kw):
        vistas.append(list(history))
        return real(history, percentile, global_default, **kw)

    monkeypatch.setattr(mod, "compute_adaptive_percentile", espia)

    mod.resolver_umbrales(serie, 250, 253, mode=mod.PercentileMode.MOVIL,
                          window=100, umbral_global_default=1.0)

    # Tres días evaluados (250, 251, 252), una historia por día.
    assert len(vistas) == 3
    for offset, historia in enumerate(vistas):
        dia = 250 + offset
        assert max(historia) == float(dia - 1), "termina en el día anterior"
        assert float(dia) not in historia, "el propio día NO entra"


def test_la_ventana_movil_avanza_con_el_dia(monkeypatch):
    """Que sea MÓVIL: cada día mira su propia ventana, no una congelada al
    inicio del fold."""
    n = 300
    serie = _serie(n, entropy=[float(i) for i in range(n)])

    vistas: list[list[float]] = []
    real = mod.compute_adaptive_percentile
    def espia(history, percentile, global_default, **kw):
        vistas.append(list(history))
        return real(history, percentile, global_default, **kw)

    monkeypatch.setattr(mod, "compute_adaptive_percentile", espia)

    mod.resolver_umbrales(serie, 250, 254, mode=mod.PercentileMode.MOVIL,
                          window=100, umbral_global_default=1.0)

    minimos = [min(h) for h in vistas]
    assert minimos == sorted(minimos) and len(set(minimos)) == len(minimos), (
        "la ventana arrastra su borde inferior: no está congelada")


def test_el_zscore_normaliza_solo_con_dias_previos():
    """`_zscores_causales` no puede incluir el propio día: si lo hiciera, el
    día movería el estadístico contra el que se lo compara."""
    entropy = np.array([float(i) for i in range(100)])

    mu, sigma, z = mod._zscores_causales(entropy, window=10)

    # Para el día 50, la media de [40..49] es 44.5.
    assert mu[50] == pytest.approx(44.5)
    assert 50.0 not in entropy[40:50]
    # Sin ventana suficiente, no se inventa nada.
    assert math.isnan(mu[0]) and math.isnan(z[0])


def test_el_zscore_no_inventa_donde_no_hay_dispersion():
    """Una ventana de valores idénticos no define un z-score."""
    entropy = np.array([1.0] * 50)

    _, sigma, z = mod._zscores_causales(entropy, window=10)

    assert sigma[30] == 0.0
    assert math.isnan(z[30]), "sin dispersión no hay z-score que inventar"


# ── Los modos producen resultados distintos ───────────────────────────────

def test_con_deriva_a_la_baja_el_movil_rescata_dias_que_el_acumulado_pierde():
    """El caso real: la entropía deriva a la baja y el percentil acumulado
    arrastra la cola vieja para siempre, dejando días recientes sin una sola
    muestra."""
    n = 600
    # Deriva monótona a la baja, como BTC (1.1726 -> 0.9970) y XAU, con picos
    # periódicos: son los días que el criterio debería seleccionar y que el
    # acumulado deja de ver a medida que la serie se aleja de su propia cola.
    entropy = [1.40 - 0.0008 * i + (0.20 if i % 20 == 0 else 0.0)
               for i in range(n)]
    serie = _serie(n, entropy=entropy, vitality=[3] * n)

    acumulado = measure_folds(serie, lookback=0, n_folds=3,
                              umbral_global_default=1.30,
                              percentile_mode=mod.PercentileMode.ACUMULADO)
    movil = measure_folds(serie, lookback=0, n_folds=3,
                          umbral_global_default=1.30,
                          percentile_mode=mod.PercentileMode.MOVIL,
                          rolling_window=100)

    n_acum = sum(f.n_post_mask for f in acumulado)
    n_movil = sum(f.n_post_mask for f in movil)

    assert n_movil > n_acum, (
        f"el móvil tiene que rescatar días: acumulado={n_acum}, movil={n_movil}")


def test_los_tres_modos_se_pueden_comparar_en_una_corrida():
    n = 400
    entropy = [1.30 - 0.0006 * i for i in range(n)]
    serie = _serie(n, entropy=entropy, vitality=[3] * n)

    resultados = {
        modo: sum(f.n_post_mask for f in measure_folds(
            serie, lookback=0, n_folds=3, umbral_global_default=1.20,
            percentile_mode=modo, rolling_window=100))
        for modo in mod.PercentileMode.todos()
    }

    assert set(resultados) == set(mod.PercentileMode.todos())


def test_el_p90_reportado_esta_en_unidades_de_entropia_en_los_tres_modos():
    """La columna P90 se compara entre modos. Reportar ahí el z crudo del
    modo ZSCORE pondría un número de otra escala (y de otro signo) al lado de
    umbrales de entropía, y la comparación dejaría de significar nada."""
    # Nivel 100 a propósito: en espacio z el percentil vale ~1.28, así que si
    # algún modo reportara el z crudo el test lo ve sin ambigüedad. El umbral
    # NO tiene por qué ser un valor observado (mu + z*sigma puede quedar
    # apenas fuera del rango), por eso el margen de una amplitud entera.
    n = 500
    entropy = [100.0 + 1.0 * ((i % 7) - 3) for i in range(n)]
    lo, hi = min(entropy), max(entropy)
    amplitud = hi - lo
    serie = _serie(n, entropy=entropy, vitality=[3] * n)

    for modo in mod.PercentileMode.todos():
        folds = measure_folds(serie, lookback=0, n_folds=3,
                              umbral_global_default=99.0,
                              percentile_mode=modo, rolling_window=100)
        for f in folds:
            assert lo - amplitud <= f.umbral_used <= hi + amplitud, (
                f"{modo}: P90={f.umbral_used} no está en unidades de entropía "
                f"(rango observado {lo}..{hi})")


def test_la_mediana_de_umbrales_no_se_deja_arrastrar_por_un_dia():
    """`representativo` es mediana, no último día: un único umbral atípico al
    final del fold no puede reescribir lo que reporta la columna."""
    assert mod._mediana([1.0, 2.0, 3.0], 9.9) == 2.0
    assert mod._mediana([1.0, 2.0, 3.0, 400.0], 9.9) == 2.5
    assert mod._mediana([], 9.9) == 9.9
    assert mod._mediana([float("nan"), 2.0], 9.9) == 2.0


# ── Tasa de disparo ───────────────────────────────────────────────────────

def test_se_reporta_la_tasa_no_solo_el_conteo():
    """2 de 714 y 172 de 710 son problemas distintos y con solo el conteo se
    ven parecidos en la tabla."""
    n = 400
    serie = _serie(n, vitality=[9 if i % 4 == 0 else 3 for i in range(n)],
                   entropy=[2.0 - 0.001 * i for i in range(n)])

    folds = measure_folds(serie, lookback=0, n_folds=3,
                          umbral_global_default=99.0)

    for f in folds:
        assert f.n_total > 0
        assert f.tasa_disparo == pytest.approx(f.n_post_mask / f.n_total)
        assert 0.0 <= f.tasa_disparo <= 1.0


def test_la_tasa_es_cero_sin_disparos_y_no_lanza():
    # Entropía estrictamente decreciente: cada día está por debajo de TODA su
    # historia, así que ningún P90 de historia puede quedar por debajo de él.
    # (Un valor constante NO sirve: con >=100 obs el percentil es ROLLING, el
    # P90 de una constante es esa constante y `>=` dispararía todos los días.)
    serie = _serie(200, entropy=[2.0 - 0.001 * i for i in range(200)],
                   vitality=[3] * 200)

    folds = measure_folds(serie, lookback=0, n_folds=2,
                          umbral_global_default=99.0)

    assert all(f.n_post_mask == 0 for f in folds)
    assert all(f.tasa_disparo == 0.0 for f in folds)


# ── Desglose de las dos ramas del OR ──────────────────────────────────────

def test_las_tres_categorias_suman_exactamente_el_total():
    n = 400
    serie = _serie(n, entropy=[float(i % 7) for i in range(n)],
                   vitality=[9 if i % 3 == 0 else 3 for i in range(n)])

    folds = measure_folds(serie, lookback=0, n_folds=3, umbral_global_default=3.0)

    for f in folds:
        assert (f.n_solo_entropia + f.n_solo_vitality + f.n_ambas_ramas
                == f.n_post_mask)


def test_ya_no_hay_dos_ramas_todos_los_disparos_son_de_entropia():
    """Versión 4.0.0. Antes había tres tests acá
    (`test_vitality_sola_se_distingue_de_entropia_sola`,
    `test_entropia_sola_cuando_vitality_nunca_dispara` y
    `test_ambas_ramas_a_la_vez_se_cuentan_aparte`) que verificaban que el
    desglose separara bien las dos ramas del OR. Ese desglose hizo su
    trabajo: mostró que vitality aportaba el 93% de los disparos de BTC, y
    eso llevó a descubrir que esa rama usaba n_events y a sacarla.

    Hoy la máscara es `entropy > p66` y no hay segunda rama. Los tres
    campos siguen sumando n_post_mask; que dos queden en cero es la forma
    que tiene el reporte de decir que el OR desapareció."""
    n = 300
    # Vitality alterna, y da igual: ya no participa.
    serie = _serie_mascara_activa(
        n, vitality=[9 if i % 5 == 0 else 3 for i in range(n)])

    folds = measure_folds(serie, lookback=0, n_folds=2,
                          umbral_global_default=UMBRAL_MASCARA_SIEMPRE_ACTIVA)

    assert sum(f.n_post_mask for f in folds) > 0
    assert sum(f.n_solo_entropia for f in folds) == sum(f.n_post_mask for f in folds)
    assert sum(f.n_solo_vitality for f in folds) == 0
    assert sum(f.n_ambas_ramas for f in folds) == 0


def test_el_desglose_se_agrega_al_nivel_oof(entorno, capsys):
    import json
    lake, ohlcv = entorno
    _escribir_ohlcv(ohlcv, "BTC", n=300)
    _escribir_gdelt("BTC", n=300)

    main(["--assets", "BTC", "--umbral-global-default", "1.19",
          "--ohlcv-root", str(ohlcv), "--format", "json"])

    a = json.loads(capsys.readouterr().out)["assets"][0]
    assert (a["oof_solo_entropia"] + a["oof_solo_vitality"]
            + a["oof_ambas_ramas"] == a["oof_post_mask"])
    assert 0.0 <= a["tasa_disparo_oof"] <= 1.0


# ── CLI ───────────────────────────────────────────────────────────────────

def test_compare_modes_corre_los_tres(entorno, capsys):
    lake, ohlcv = entorno
    _escribir_ohlcv(ohlcv, "BTC", n=300)
    _escribir_gdelt("BTC", n=300)

    codigo = main(["--assets", "BTC", "--umbral-global-default", "1.19",
                   "--ohlcv-root", str(ohlcv), "--compare-modes",
                   "--rolling-window", "60"])

    salida = capsys.readouterr().out
    assert codigo == 0
    for modo in mod.PercentileMode.todos():
        assert modo in salida
    assert "COMPARACIÓN DE CRITERIOS" in salida


def test_compare_modes_en_json_trae_los_tres(entorno, capsys):
    import json
    lake, ohlcv = entorno
    _escribir_ohlcv(ohlcv, "BTC", n=300)
    _escribir_gdelt("BTC", n=300)

    main(["--assets", "BTC", "--umbral-global-default", "1.19",
          "--ohlcv-root", str(ohlcv), "--compare-modes",
          "--rolling-window", "60", "--format", "json"])

    rep = json.loads(capsys.readouterr().out)
    assert set(rep["por_modo"]) == set(mod.PercentileMode.todos())
    assert rep["rolling_window"] == 60


def test_las_columnas_nuevas_no_desplazan_al_desglose_de_arrastre(entorno,
                                                                  capsys):
    """El desglose propia/arrastrada es la razón por la que el n es
    interpretable. Agregar tasa y ramas del OR no puede costarlo: sin él, el
    número vuelve a ser el que no se podía leer."""
    lake, ohlcv = entorno
    _escribir_ohlcv(ohlcv, "BTC", n=300)
    _escribir_gdelt("BTC", n=300)

    main(["--assets", "BTC", "--umbral-global-default", "1.19",
          "--ohlcv-root", str(ohlcv)])

    salida = capsys.readouterr().out
    for columna in ("propia", "arrast", "tasa", "s_ent", "s_vit", "ambas"):
        assert columna in salida, f"falta la columna {columna}"


def test_sin_el_flag_el_json_no_carga_por_modo(entorno, capsys):
    """Sin --compare-modes, `por_modo` sería una copia literal de `assets`.
    Duplicar el reporte por defecto encarece cada corrida sin agregar nada."""
    import json
    lake, ohlcv = entorno
    _escribir_ohlcv(ohlcv, "BTC", n=300)
    _escribir_gdelt("BTC", n=300)

    main(["--assets", "BTC", "--umbral-global-default", "1.19",
          "--ohlcv-root", str(ohlcv), "--format", "json"])

    rep = json.loads(capsys.readouterr().out)
    assert "por_modo" not in rep
    assert rep["percentile_modes"] == [mod.PercentileMode.ACUMULADO]


def test_rolling_window_invalida_es_fallo_de_invocacion(entorno, capsys):
    lake, ohlcv = entorno

    codigo = main(["--assets", "BTC", "--umbral-global-default", "1.19",
                   "--ohlcv-root", str(ohlcv), "--rolling-window", "1"])

    assert codigo == 2
    assert "rolling-window" in capsys.readouterr().err


# ── El tool no toca producción ────────────────────────────────────────────

def test_los_modos_llaman_a_compute_adaptive_percentile_no_la_reimplementan():
    """Port, don't rewrite: los criterios viven en el tool y LLAMAN a la
    función de core/scoring.py; no la copian ni la modifican."""
    assert mod.compute_adaptive_percentile is not None

    import ast
    import inspect
    from pathlib import Path as _P

    arbol = ast.parse(_P(inspect.getfile(mod)).read_text())
    llamadas = {n.func.id for n in ast.walk(arbol)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "compute_adaptive_percentile" in llamadas

    # Y no se define una versión local que la sombree.
    definidas = {n.name for n in ast.walk(arbol)
                 if isinstance(n, ast.FunctionDef)}
    assert "compute_adaptive_percentile" not in definidas


def test_el_zscore_default_es_el_percentil_de_la_mascara_en_la_normal():
    """Φ⁻¹(GODEL_MASK_PERCENTILE/100). No es un número inventado: se
    verifica contra la CDF. Cambió en la versión 4.0.0 junto con el
    percentil de la máscara -- era Φ⁻¹(0.90) cuando el tool medía un P90.
    Este test lo ata al percentil real en vez de a un literal, para que no
    puedan volver a separarse."""
    from core.scoring import GODEL_MASK_PERCENTILE

    z = mod.ZSCORE_UMBRAL_GLOBAL_DEFAULT
    phi = 0.5 * (1 + math.erf(z / math.sqrt(2)))

    assert phi == pytest.approx(GODEL_MASK_PERCENTILE / 100.0, abs=1e-9)
