"""
tools/measure_godel_samples.py
===============================
Mide cuántas muestras sobreviven la máscara Gödel. NO entrena nada.

LA PREGUNTA QUE CONTESTA, y por qué es la primera que hay que contestar:
el proyecto entrena sobre un subconjunto filtrado — solo los días donde
`(entropy_shannon >= P90) OR (vitality_tesla == 9)`. Nadie midió nunca
cuántos días sobreviven ese filtro. Sin ese número, ninguna comparación de
modelos es defendible: un modelo que "mejora" sobre 40 muestras de
validación no mejoró nada, y no hay forma de saberlo sin contar primero.
Este tool cuenta. La decisión de entrenar o no viene después, con el número
en la mano.

REGLA DE INTEGRIDAD TEMPORAL — NO NEGOCIABLE:
el P90 se recalcula EN CADA FOLD usando solo los días anteriores al inicio
de su ventana de validación. Nunca sobre el dataset completo. Ese fue
exactamente el leakage que invalidó el trabajo anterior: un umbral que vio
el futuro selecciona las muestras "difíciles" con información que en
producción no existe, y el resultado se ve bien y no significa nada.
`core.scoring.godel_active()` lo documenta en su propio docstring; acá se
cumple, no se asume.

READ-ONLY, SIN EXCEPCIÓN. No escribe ningún archivo: el reporte sale por
stdout (texto o JSON). No normaliza, no repara y no persiste ningún parquet
ni jsonl. Un tool de medición que además escribe es un tool que puede
cambiar lo que está midiendo.

"NO HAY DATOS SUFICIENTES" ES UN RESULTADO, NO UN ERROR:
exit code 0 siempre que la medición se complete, incluso con veredicto
negativo. El veredicto es un CAMPO del reporte. Exit != 0 se reserva para
fallo real — ruta raíz inexistente, archivo ilegible, excepción no
controlada. Un activo sin datos reporta cero y sigue; no se rellena, no se
interpola y no se bajan los umbrales para que dé verde.

TARGET — portado literal, no reinventado. Fuente leída del repo:
`archive/legacy-pre-20260813`, `04_GOLD_MODULES/spel_patch_coordinated.py`
líneas 205-206:

    X.append(arr_norm[i-lookback:i])            # ventana EXCLUYE i
    y.append(1.0 if arr_raw[i, 2] > 0 else 0.0) # log_return crudo en i

O sea: features de los días `i-lookback .. i-1`, target del día `i`. La
ventana excluye el día del target, que es lo que hace que el par sea
predictivo y no una tautología. `lookback` es parámetro (default 63,
`_LOOKBACK_DEFAULT` en `04_GOLD_MODULES/capa_c_inference.py:42`).
Ese código se LEYÓ para portar; este módulo no importa nada de `archive/*`.

EL ESQUEMA WALK-FORWARD ES PARÁMETRO, no está hardcodeado. El split del
legacy era un split único por fecha, NO walk-forward — no se toma como
referencia.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import pandas as pd

# Mismo idiom que tools/heartbeat.py: permite `python tools/measure_godel_samples.py`
# desde la raíz del repo sin instalarlo como paquete.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.scoring import (  # noqa: E402
    GODEL_MASK_PERCENTILE,
    compute_adaptive_percentile,
    compute_vitality_tesla,
    godel_active,
)
from governance.persistence import drive_root  # noqa: E402
from ingestion.gdelt_series import read_series  # noqa: E402
from ingestion.training_dataset import (  # noqa: E402
    BuildDatasetResult,
    build_training_dataset,
)

logger = logging.getLogger("spel.tools.measure_godel_samples")

#: `_LOOKBACK_DEFAULT` en 04_GOLD_MODULES/capa_c_inference.py:42.
LOOKBACK_DEFAULT = 63

#: Umbrales del veredicto. NO se tocan para que un experimento dé verde.
#: 620 = n mínimo para detectar un edge de +5pp sobre 0.50 con potencia 80%
#:       (binomial exacto).
#: 100 = primer n cuyo IC 95% no cruza 0.50.
#:  20 = mínimo por clase para que el desbalance no domine la métrica.
OOF_MIN_DEFENDIBLE = 620
OOF_MIN_PARA_CORRER = 150
FOLD_MIN_N = 100
FOLD_MIN_CLASE = 20


class PercentileMode:
    """
    Cómo se calcula el umbral de entropía. ESTE TOOL NO CAMBIA EL CRITERIO
    DEL SISTEMA — lo mide. `core/scoring.py` y `godel_active()` quedan
    intactos; acá solo se elige QUÉ historia se le pasa a
    `compute_adaptive_percentile()`, que se llama igual en los tres modos.

    Por qué hace falta poder compararlos: la primera medición real dio OOF
    de 284 (BTC) y 68 (XAU), y el diagnóstico mostró que la causa no es
    escasez de señal sino un umbral inalcanzable. La entropía deriva a la
    baja de forma monótona, y un percentil ACUMULADO arrastra la cola de
    2015-2018 para siempre: en XAU, el P90 de cuatro años consecutivos queda
    por debajo del umbral global, y quedan 1.077 días recientes sin una sola
    muestra en ambos activos. Cambiar el criterio sin medir el impacto sobre
    `n` sería tocar el corazón del sistema a ciegas.
    """
    #: Lo que el sistema hace HOY: un percentil sobre toda la historia
    #: previa al fold. Es el default y no cambia nada.
    ACUMULADO = "ACUMULADO"
    #: Ventana de N días que TERMINA EN EL DÍA ANTERIOR: para el día `i` se
    #: usa `entropy[i-N : i]`. El desplazamiento de un día es lo que impide
    #: que un día se evalúe contra un percentil que lo incluye.
    MOVIL = "MOVIL"
    #: Normaliza cada día por la media y desviación de sus N días previos, y
    #: aplica el percentil sobre la serie NORMALIZADA de todo lo anterior al
    #: fold. Ataca la deriva sin tirar la historia: al quitar el nivel, los
    #: días de 2015 vuelven a ser comparables con los de 2025.
    ZSCORE = "ZSCORE"

    @classmethod
    def todos(cls) -> tuple[str, ...]:
        return (cls.ACUMULADO, cls.MOVIL, cls.ZSCORE)


#: Ventana de los modos móviles. 252 = días hábiles de un año.
ROLLING_WINDOW_DEFAULT = 252

#: Default global para el modo ZSCORE. El `--umbral-global-default` del usuario
#: está en unidades de entropía y no sirve en espacio z, así que hace falta
#: uno propio: Φ⁻¹(GODEL_MASK_PERCENTILE/100). Con el percentil de la máscara
#: en 66 eso es Φ⁻¹(0.66) = 0.4124631294. NO es un número inventado — es una
#: constante matemática, verificada por bisección sobre la CDF
#: (Φ(0.4124631294) = 0.66000000). Es el valor correcto si la entropía
#: normalizada fuera normal; que lo sea o no es precisamente una de las cosas
#: que la comparación deja ver.
#:
#: Cambió en la versión 4.0.0 junto con el percentil de la máscara: era
#: Φ⁻¹(0.90) = 1.2815515655 cuando el tool medía un P90. Un tool que mide un
#: percentil distinto del que usa producción mide otra cosa y no se nota.
ZSCORE_UMBRAL_GLOBAL_DEFAULT = 0.4124631294


class VerdictLevel:
    """Veredictos posibles. Ninguno significa 'falló el proceso' — los
    cuatro son mediciones completadas con éxito."""
    SIN_DATOS = "SIN_DATOS"
    NO_CORRER = "NO_CORRER"
    INSUFICIENTE_PARA_COMPARAR = "INSUFICIENTE_PARA_COMPARAR"
    DEFENDIBLE = "DEFENDIBLE"


@dataclass(frozen=True)
class FoldMeasurement:
    """Una ventana de validación walk-forward, ya medida."""
    fold: int
    n_train_days: int
    val_start: Optional[str]
    val_end: Optional[str]
    umbral_used: float
    umbral_source: str
    umbral_n_obs: int          # cuántos días de TRAIN alimentaron el P90
    n_total: int            # candidatos en validación (los que tienen i >= lookback)
    n_post_mask: int        # los que además pasan la máscara Gödel
    n_post_propia: int      # de los post-máscara, con entropía DEL PROPIO DÍA
    n_post_arrastrada: int  # de los post-máscara, con entropía forward-filled
    n_up: int               # de los post-máscara, cuántos con log_return > 0
    n_down: int
    estable: bool           # n_post_mask >= 100 Y min(clase) >= 20
    #: Qué criterio de percentil produjo estos números. Default ACUMULADO =
    #: el comportamiento de siempre.
    percentile_mode: str = PercentileMode.ACUMULADO
    # ── DESGLOSE DE LAS DOS RAMAS DEL OR ─────────────────────────────────
    # La máscara es (entropy >= P90) OR (vitality == 9). Los tres campos de
    # abajo suman exactamente n_post_mask y dicen cuál rama sostuvo cada
    # disparo. Importa para interpretar cualquier comparación de criterios:
    # si el percentil solo mueve la rama de entropía y esa rama aporta el
    # 7% de los disparos, cambiarlo no puede arreglar el problema.
    n_solo_entropia: int = 0    # entropía sobre el umbral, vitality != 9
    n_solo_vitality: int = 0    # vitality == 9, entropía bajo el umbral
    n_ambas_ramas: int = 0      # las dos a la vez
    #: n_post_mask / n_total. Un fold con 2 de 714 y otro con 172 de 710 son
    #: problemas distintos, y mirando solo el conteo se ven parecidos.
    tasa_disparo: float = 0.0


@dataclass(frozen=True)
class AssetMeasurement:
    """Medición completa de un activo. `notes` lleva lo que el número solo
    no dice — por qué un fold quedó vacío, si faltó un archivo, etc."""
    asset: str
    # 1. profundidad de datos real — medida, nunca supuesta
    gdelt_days: int
    gdelt_first: Optional[str]
    gdelt_last: Optional[str]
    ohlcv_days: int
    ohlcv_first: Optional[str]
    ohlcv_last: Optional[str]
    overlap_days: int
    # 2. join
    joined_rows: int
    coverage_ratio: float
    n_dropped_no_entropy: int
    # 3-4. folds y agregado
    folds: list[FoldMeasurement]
    oof_post_mask: int
    oof_post_propia: int      # "n post-máscara sin arrastre"
    oof_post_arrastrada: int
    oof_up: int
    oof_down: int
    # 5. veredicto
    verdict: str
    verdict_reason: str
    #: Criterio de percentil usado en esta medición.
    percentile_mode: str = PercentileMode.ACUMULADO
    #: Agregado OOF del desglose por rama del OR. Suman oof_post_mask.
    oof_solo_entropia: int = 0
    oof_solo_vitality: int = 0
    oof_ambas_ramas: int = 0
    #: Tasa de disparo agregada: oof_post_mask / candidatos totales.
    tasa_disparo_oof: float = 0.0
    #: Se llena SOLO cuando el n sin arrastre cae por debajo de un umbral
    #: que el n total sí supera — es decir, cuando el veredicto depende de
    #: muestras que entraron con entropía prestada. None significa que el
    #: arrastre no cambia la lectura, no que no haya arrastre.
    arrastre_warning: Optional[str] = None
    notes: list[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════
#  Lectura READ-ONLY del OHLCV
# ══════════════════════════════════════════════════════════════════════════

#: Convención de ubicación que declara ESTE tool: el repo todavía no tiene
#: un store de OHLCV persistido (los adapters lo traen en vivo), así que no
#: hay una convención previa que respetar. Se deriva de drive_root() y NUNCA
#: es una ruta fija de Colab; se puede sobreescribir con --ohlcv-root.
OHLCV_SUBDIR = ("metrics", "ohlcv")


def default_ohlcv_root() -> Path:
    return drive_root().joinpath(*OHLCV_SUBDIR)


#: Patrones de nombre por defecto. Conservan EXACTAMENTE el comportamiento
#: anterior (se probaba .csv y después .parquet) para no romper nada que ya
#: funcione. Los nombres reales del proyecto no siguen esta forma
#: (`BTC_ohlcv_v5.parquet`), y por eso el patrón es un flag: el desajuste
#: era el NOMBRE, no solo la carpeta, y ninguna cantidad de --ohlcv-root
#: arregla un nombre distinto.
DEFAULT_OHLCV_PATTERNS: tuple[str, ...] = ("{asset}.csv", "{asset}.parquet")

#: Nombres aceptados para la columna de fecha. `timestamp` es lo que produce
#: el contrato de ingestion/adapters.py; `date` es lo que usan los parquets
#: del data lake legacy (ver tools/audit_data_lake.py, que audita justamente
#: la inconsistencia de esa columna). Se acepta cualquiera de las dos y se
#: normaliza a `timestamp`, que es lo que espera validate_ohlcv_schema().
DATE_COLUMNS: tuple[str, ...] = ("timestamp", "date")


@dataclass(frozen=True)
class OhlcvLookup:
    """Resultado de buscar el OHLCV de un activo.

    `intentos` existe por un motivo concreto: sin él, "no encontré nada" y
    "busqué en el lugar equivocado" se ven idénticos en el reporte, y no hay
    forma de distinguirlos sin leer el código. Con la lista de rutas
    intentadas, el reporte se explica solo."""
    df: Optional[pd.DataFrame]
    path: Optional[Path]
    intentos: list[str]


def _directorios_candidatos(root: Path, asset: str) -> list[Path]:
    """
    Dónde puede vivir el archivo de un activo. Plano en la raíz es el caso
    que ya funcionaba; los tres siguientes cubren los layouts por activo
    que usa el Drive real (una carpeta por activo, con o sin subcarpeta).
    El orden importa: lo más específico primero, para que un archivo suelto
    en la raíz no le gane a uno dentro de la carpeta del activo.
    """
    return [
        root / asset / "ohlcv",
        root / asset,
        root / "ohlcv" / asset,
        root,
    ]


def _leer_tabla(ruta: Path) -> pd.DataFrame:
    """pyarrow se importa de forma perezosa y solo si hace falta: no está en
    requirements.txt (el motor no usa Parquet, ver requirements-dev.txt), así
    que importarlo arriba rompería este módulo donde no esté instalado."""
    if ruta.suffix == ".parquet":
        import pyarrow.parquet as pq  # lazy: ver docstring
        return pq.read_table(ruta).to_pandas()
    return pd.read_csv(ruta)


def _normalizar_fecha(df: pd.DataFrame, ruta: Path) -> pd.DataFrame:
    """
    Acepta `timestamp` o `date` y normaliza a `timestamp`. Si no hay
    ninguna, el error lista las columnas que SÍ vinieron — sin eso, un
    parquet con la fecha bajo otro nombre produce un fallo que no dice qué
    hacer al respecto.
    """
    presentes = [c for c in DATE_COLUMNS if c in df.columns]
    if not presentes:
        raise ValueError(
            f"{ruta} no tiene ninguna columna de fecha reconocida "
            f"{list(DATE_COLUMNS)}. Columnas encontradas: {list(df.columns)}"
        )
    col = presentes[0]
    if col != "timestamp":
        # Renombrar, no duplicar: dejar las dos columnas invita a que aguas
        # abajo alguien lea la que no se normalizó.
        df = df.rename(columns={col: "timestamp"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.sort_values("timestamp").reset_index(drop=True)


def load_ohlcv(
    root: Path,
    asset: str,
    *,
    patterns: Sequence[str] = DEFAULT_OHLCV_PATTERNS,
) -> OhlcvLookup:
    """
    Busca y lee el OHLCV de un activo. Devuelve `df=None` si no hay archivo
    — resultado válido ("este activo no tiene precio persistido"), no un
    error. Un error de verdad (archivo corrupto, columna de fecha ausente)
    sí se propaga.

    Cada patrón lleva `{asset}` como placeholder y se prueba en cada
    directorio candidato. Como último recurso hace una búsqueda recursiva
    desde la raíz, para no fallar por una diferencia de profundidad que no
    cambia nada. Todo lo intentado queda en `OhlcvLookup.intentos`.
    """
    intentos: list[str] = []

    for patron in patterns:
        nombre = patron.format(asset=asset)
        for directorio in _directorios_candidatos(root, asset):
            ruta = directorio / nombre
            intentos.append(str(ruta))
            if ruta.is_file():
                return OhlcvLookup(_normalizar_fecha(_leer_tabla(ruta), ruta),
                                   ruta, intentos)

    # Último recurso: el archivo existe con ese nombre pero a otra
    # profundidad. Se reporta como intento aparte para que quede claro que
    # el match no vino de un directorio esperado.
    for patron in patterns:
        nombre = patron.format(asset=asset)
        intentos.append(f"{root}/**/{nombre} (búsqueda recursiva)")
        for ruta in sorted(root.rglob(nombre)):
            if ruta.is_file():
                return OhlcvLookup(_normalizar_fecha(_leer_tabla(ruta), ruta),
                                   ruta, intentos)

    return OhlcvLookup(None, None, intentos)


# ══════════════════════════════════════════════════════════════════════════
#  Construcción causal de las series derivadas
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SerieDerivada:
    """Las series que la máscara y el target necesitan, alineadas al mismo
    índice de día. `log_return[0]` es NaN por definición (no hay día
    previo), y por eso el primer índice usable siempre es >= 1 — aparte del
    piso que impone el lookback.

    `forward_filled[i]` marca que la entropía de ese día NO es suya: viene
    arrastrada de un día GDELT anterior (`build_training_dataset` hace
    forward-fill). Importa porque la máscara dispara sobre
    `entropy >= P90`, así que un día puede entrar al conteo por una
    entropía que no le pertenece. Un n de 700 con 400 arrastradas no es
    700, y sin este desglose no hay forma de saberlo."""
    fechas: list[date]
    entropy: np.ndarray
    vitality: np.ndarray
    log_return: np.ndarray
    forward_filled: np.ndarray


def _log_returns(closes: Sequence[float]) -> np.ndarray:
    """log_return[i] = ln(close[i] / close[i-1]). El legacy tomaba esta
    columna cruda (`arr_raw[:, 2]`) como fuente del target; acá se calcula
    desde el close del join, que es el mismo dato sin pasar por el parquet
    cuyo formato de fecha era el defecto documentado el 2026-08-18."""
    arr = np.asarray(closes, dtype=float)
    out = np.full(len(arr), np.nan)
    if len(arr) < 2:
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = arr[1:] / arr[:-1]
        out[1:] = np.where(ratio > 0, np.log(np.where(ratio > 0, ratio, 1.0)), np.nan)
    return out


def build_serie_derivada(result: BuildDatasetResult) -> SerieDerivada:
    """
    Arma entropy / vitality / log_return por día.

    vitality_tesla se calcula DÍA POR DÍA de forma causal: para el día `i`
    se usan `n_events[:i+1]` (incluye el actual, igual que el legacy y que
    `orchestration/cycle.py::_build_windows`) y `entropy[:i]` (sin el
    actual). Nunca mira hacia adelante, así que calcularlo una sola vez
    para toda la serie da exactamente lo mismo que recalcularlo por fold —
    a diferencia del P90, que sí es un umbral de decisión y por eso va por
    fold.
    """
    filas = result.rows
    fechas = [r.date for r in filas]
    entropy = np.array([r.entropy_shannon for r in filas], dtype=float)
    n_events = [float(r.n_events) for r in filas]
    log_return = _log_returns([r.close for r in filas])

    vitality = np.zeros(len(filas), dtype=int)
    for i in range(len(filas)):
        vitality[i] = compute_vitality_tesla(
            n_events_window=n_events[: i + 1],
            entropy_window=list(entropy[:i]),
            current_entropy=float(entropy[i]),
        ).value

    return SerieDerivada(
        fechas=fechas, entropy=entropy, vitality=vitality, log_return=log_return,
        forward_filled=np.array(
            [r.entropy_is_forward_filled for r in filas], dtype=bool
        ),
    )


# ══════════════════════════════════════════════════════════════════════════
#  Walk-forward
# ══════════════════════════════════════════════════════════════════════════

def walk_forward_splits(n: int, n_folds: int) -> list[tuple[int, int]]:
    """
    Ventana expansiva: la serie se parte en `n_folds + 1` bloques
    contiguos. El bloque 0 es el train inicial; el fold k valida sobre el
    bloque k y entrena con todo lo anterior. Devuelve
    [(val_start, val_end_exclusivo), ...].

    Expansiva y no deslizante porque con series cortas —que es justo lo que
    este tool sospecha que hay— una ventana deslizante desperdicia historia
    que el P90 necesita para no caer al default global.
    """
    if n_folds < 1:
        raise ValueError(f"n_folds debe ser >= 1, recibido: {n_folds}")
    if n <= 0:
        return []
    tam = n // (n_folds + 1)
    if tam == 0:
        return []
    cortes = [tam * (k + 1) for k in range(n_folds)]
    return [
        (inicio, fin)
        for inicio, fin in zip(cortes, cortes[1:] + [n])
        if fin > inicio
    ]


def _zscores_causales(
    entropy: np.ndarray, window: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Para cada día, la media y desviación de sus `window` días PREVIOS (sin
    incluirlo) y su z-score contra ellos.

    Todo lo que devuelve es causal por construcción: el día `i` se normaliza
    con `entropy[i-window : i]`, que termina en el día anterior. Un día que
    entrara en su propio cálculo se estaría comparando contra un estadístico
    que él mismo movió.

    Días sin ventana suficiente, o con desviación nula (una ventana de
    valores idénticos), quedan en NaN: no se inventa un z-score donde no hay
    dispersión que lo defina.
    """
    n = len(entropy)
    mu = np.full(n, np.nan)
    sigma = np.full(n, np.nan)
    z = np.full(n, np.nan)
    for i in range(n):
        ini = max(0, i - window)
        ventana = entropy[ini:i]
        ventana = ventana[~np.isnan(ventana)]
        if len(ventana) < 2:
            continue
        m = float(np.mean(ventana))
        s = float(np.std(ventana))
        mu[i], sigma[i] = m, s
        if s > 0 and not math.isnan(entropy[i]):
            z[i] = (float(entropy[i]) - m) / s
    return mu, sigma, z


@dataclass(frozen=True)
class Umbrales:
    """Los umbrales de un fold, ya resueltos. `por_dia` es un dict porque en
    los modos móviles cada día tiene el suyo; en ACUMULADO todos comparten
    valor.

    `representativo` es la MEDIANA de `por_dia`, siempre en unidades de
    entropía. En ACUMULADO eso es exactamente el único valor, así que la
    columna P90 del reporte no cambia. En los modos móviles es lo que hace
    que esa misma columna siga siendo comparable entre criterios: reportar
    el z crudo del modo ZSCORE ahí pondría un número de otra escala (y de
    otro signo) al lado de umbrales de entropía."""
    por_dia: dict[int, float]
    representativo: float
    fuente: str
    n_obs: int


def _mediana(valores: Sequence[float], respaldo: float) -> float:
    finitos = sorted(v for v in valores if not math.isnan(v))
    if not finitos:
        return respaldo
    mitad = len(finitos) // 2
    if len(finitos) % 2:
        return finitos[mitad]
    return 0.5 * (finitos[mitad - 1] + finitos[mitad])


def resolver_umbrales(
    serie: SerieDerivada, val_ini: int, val_fin: int, *,
    mode: str, window: int, umbral_global_default: float,
    z_global_default: float = ZSCORE_UMBRAL_GLOBAL_DEFAULT,
) -> Umbrales:
    """
    Calcula el umbral de entropía de cada día de validación según el modo.

    LOS TRES LLAMAN A `compute_adaptive_percentile()` — no la reimplementan
    ni la modifican. Lo único que cambia entre modos es QUÉ historia se le
    pasa, que es exactamente donde vive la diferencia que se quiere medir.

    FRONTERA TEMPORAL, y una precisión que importa: ningún modo usa jamás un
    valor posterior al día que evalúa.
      · ACUMULADO usa `entropy[:val_ini]` — toda la historia previa al fold.
      · MOVIL y ZSCORE usan, para el día `i`, `entropy[i-window : i]`. Esa
        ventana CRUZA la frontera del fold cuando `i > val_ini`, y eso NO es
        fuga: en producción, al decidir sobre el día `i` ya se conocen los
        días `i-1`, `i-2`... Congelar la ventana en `val_ini` sería más
        conservador que la realidad y volvería el criterio cada vez más
        viejo a lo largo de la validación — justo el defecto que se quiere
        medir. Lo que nunca se toca es `entropy[i]` y posteriores.
    """
    if mode not in PercentileMode.todos():
        raise ValueError(
            f"Modo de percentil desconocido: {mode!r}. "
            f"Válidos: {list(PercentileMode.todos())}"
        )

    if mode == PercentileMode.ACUMULADO:
        # Comportamiento de HOY, sin un solo cambio.
        historia = [float(v) for v in serie.entropy[:val_ini] if not math.isnan(v)]
        p = compute_adaptive_percentile(
            history=historia, percentile=GODEL_MASK_PERCENTILE,
            global_default=umbral_global_default,
        )
        return Umbrales(
            por_dia={i: p.value for i in range(val_ini, val_fin)},
            representativo=p.value,
            fuente=getattr(p.source, "name", str(p.source)), n_obs=p.n_obs,
        )

    if mode == PercentileMode.MOVIL:
        por_dia: dict[int, float] = {}
        ultimo = None
        for i in range(val_ini, val_fin):
            ini = max(0, i - window)
            # [i-window, i) — termina en el día ANTERIOR. Ese desplazamiento
            # de un día es la diferencia entre medir y hacer trampa.
            historia = [float(v) for v in serie.entropy[ini:i] if not math.isnan(v)]
            ultimo = compute_adaptive_percentile(
                history=historia, percentile=GODEL_MASK_PERCENTILE,
                global_default=umbral_global_default,
            )
            por_dia[i] = ultimo.value
        return Umbrales(
            por_dia=por_dia,
            representativo=_mediana(list(por_dia.values()), umbral_global_default),
            fuente=getattr(ultimo.source, "name", "GLOBAL") if ultimo else "GLOBAL",
            n_obs=ultimo.n_obs if ultimo else 0,
        )

    # ZSCORE: el percentil se calcula sobre la serie NORMALIZADA previa al
    # fold, y después se devuelve a unidades de entropía con la media y la
    # desviación del propio día. Así `godel_active()` sigue comparando
    # entropía contra entropía y no hace falta tocarlo.
    mu, sigma, z = _zscores_causales(serie.entropy, window)
    historia_z = [float(v) for v in z[:val_ini] if not math.isnan(v)]
    p = compute_adaptive_percentile(
        history=historia_z, percentile=GODEL_MASK_PERCENTILE,
        global_default=z_global_default,
    )
    por_dia = {}
    for i in range(val_ini, val_fin):
        if math.isnan(mu[i]) or math.isnan(sigma[i]):
            # Sin ventana utilizable, no se inventa un umbral: se usa el
            # global en unidades de entropía, que es lo que haría el modo
            # acumulado en frío.
            por_dia[i] = umbral_global_default
            continue
        por_dia[i] = float(mu[i]) + p.value * float(sigma[i])
    return Umbrales(
        por_dia=por_dia,
        # Mediana de los umbrales YA devueltos a unidades de entropía. El
        # percentil en espacio z (p.value) no va acá: es de otra escala.
        representativo=_mediana(list(por_dia.values()), umbral_global_default),
        fuente=getattr(p.source, "name", str(p.source)), n_obs=p.n_obs,
    )


def measure_folds(
    serie: SerieDerivada,
    *,
    lookback: int,
    n_folds: int,
    umbral_global_default: float,
    percentile_mode: str = PercentileMode.ACUMULADO,
    rolling_window: int = ROLLING_WINDOW_DEFAULT,
) -> list[FoldMeasurement]:
    """
    Mide cada fold. El P90 de cada uno sale SOLO de la entropía de los días
    anteriores al inicio de su validación — esa rebanada es la regla de
    integridad temporal hecha código, y es la línea que no se cruza.
    """
    n = len(serie.fechas)
    mediciones: list[FoldMeasurement] = []

    for k, (val_ini, val_fin) in enumerate(walk_forward_splits(n, n_folds), start=1):
        # ── LA LÍNEA QUE NO SE CRUZA ──────────────────────────────────
        # Ningún modo usa un valor posterior al día que evalúa. Ver
        # `resolver_umbrales()` para por qué la ventana móvil puede cruzar
        # la frontera del fold sin que eso sea fuga.
        umbrales = resolver_umbrales(
            serie, val_ini, val_fin, mode=percentile_mode,
            window=rolling_window, umbral_global_default=umbral_global_default,
        )

        n_total = n_post = n_up = n_down = 0
        n_propia = n_arrastrada = 0
        n_solo_ent = n_solo_vit = n_ambas = 0
        for i in range(val_ini, val_fin):
            # Piso del legacy: `for i in range(lookback, len(arr))`. Un
            # índice por debajo no tiene ventana completa hacia atrás.
            if i < lookback:
                continue
            if math.isnan(serie.log_return[i]) or math.isnan(serie.entropy[i]):
                continue
            n_total += 1
            umbral = umbrales.por_dia[i]
            if not godel_active(
                entropy_shannon=float(serie.entropy[i]),
                p66_entropy=umbral,
            ):
                continue
            n_post += 1
            # DESGLOSE DE LAS RAMAS: DESDE LA VERSIÓN 4.0.0 HAY UNA SOLA.
            # La máscara era `(entropy >= P90) OR (vitality == 9)` y este
            # desglose existía para ver cuál rama la sostenía -- fue lo que
            # mostró que vitality aportaba el 93% de los disparos de BTC.
            # Ese desglose cumplió su función: llevó a descubrir que la
            # rama de vitality usaba n_events, una desviación del legacy, y
            # a sacarla del filtro. Hoy la máscara es `entropy > p66` y
            # todos los disparos son de entropía por construcción.
            #
            # Los tres campos se conservan para no romper el contrato del
            # JSON, y siguen sumando n_post_mask. Que solo_vitality y
            # ambas queden en cero es información, no un bug: es la forma
            # que tiene el reporte de decir que ya no hay un OR.
            n_solo_ent += 1
            # La muestra entró a la máscara; ahora, ¿entró con entropía
            # propia o con una arrastrada de un día GDELT anterior?
            if bool(serie.forward_filled[i]):
                n_arrastrada += 1
            else:
                n_propia += 1
            if serie.log_return[i] > 0:
                n_up += 1
            else:
                n_down += 1

        mediciones.append(FoldMeasurement(
            fold=k,
            n_train_days=val_ini,
            val_start=str(serie.fechas[val_ini]) if val_ini < n else None,
            val_end=str(serie.fechas[val_fin - 1]) if val_fin - 1 < n else None,
            umbral_used=round(umbrales.representativo, 6),
            umbral_source=umbrales.fuente,
            umbral_n_obs=umbrales.n_obs,
            percentile_mode=percentile_mode,
            n_total=n_total,
            n_post_mask=n_post,
            n_post_propia=n_propia,
            n_post_arrastrada=n_arrastrada,
            n_up=n_up,
            n_down=n_down,
            n_solo_entropia=n_solo_ent,
            n_solo_vitality=n_solo_vit,
            n_ambas_ramas=n_ambas,
            # Tasa, no solo conteo: 2 de 714 y 172 de 710 son problemas
            # distintos y en la tabla se ven parecidos si solo se mira n.
            tasa_disparo=(n_post / n_total) if n_total else 0.0,
            estable=n_post >= FOLD_MIN_N and min(n_up, n_down) >= FOLD_MIN_CLASE,
        ))

    return mediciones


def evaluar_arrastre(oof: int, oof_propia: int) -> Optional[str]:
    """
    Detecta el caso en que el veredicto se sostiene sobre muestras que
    entraron a la máscara con entropía prestada.

    El veredicto SIGUE calculándose sobre el n total — ese criterio no se
    cambia acá. Lo que esta función agrega es la advertencia: si el n sin
    arrastre no alcanza un umbral que el n total sí supera, el número que
    manda es frágil, y el reporte tiene que decirlo en vez de dejar que
    alguien lea "700" y suponga 700 días con entropía propia.

    Devuelve None cuando el arrastre no cambia en qué banda cae el
    resultado — que no es lo mismo que "no hay arrastre".
    """
    for umbral, etiqueta in (
        (OOF_MIN_DEFENDIBLE, "DEFENDIBLE"),
        (OOF_MIN_PARA_CORRER, "el mínimo para correr el experimento"),
    ):
        if oof >= umbral > oof_propia:
            arrastradas = oof - oof_propia
            pct = 100.0 * arrastradas / oof if oof else 0.0
            return (
                f"El veredicto se apoya en entropía arrastrada: el n total "
                f"({oof}) supera {umbral} ({etiqueta}), pero el n SIN arrastre "
                f"({oof_propia}) no. {arrastradas} de {oof} muestras "
                f"({pct:.1f}%) entraron a la máscara con una entropía que no "
                f"es del día. Tratar este resultado como frágil."
            )
    return None


def dictaminar(oof: int, folds: Sequence[FoldMeasurement]) -> tuple[str, str]:
    """Traduce el número medido a veredicto. No hay margen de ajuste acá a
    propósito: los umbrales son constantes del módulo."""
    if not folds:
        return (
            VerdictLevel.SIN_DATOS,
            "No hubo ningún fold medible — serie demasiado corta para "
            "partirla, o sin filas tras el join.",
        )
    estables = sum(1 for f in folds if f.estable)
    detalle = f"{estables}/{len(folds)} folds con n>={FOLD_MIN_N} y min(clase)>={FOLD_MIN_CLASE}"
    if oof >= OOF_MIN_DEFENDIBLE:
        return (
            VerdictLevel.DEFENDIBLE,
            f"OOF agregado {oof} >= {OOF_MIN_DEFENDIBLE}: alcanza para una "
            f"comparación defendible. {detalle}.",
        )
    if oof < OOF_MIN_PARA_CORRER:
        return (
            VerdictLevel.NO_CORRER,
            f"OOF agregado {oof} < {OOF_MIN_PARA_CORRER}: no se corre el "
            f"experimento. {detalle}.",
        )
    return (
        VerdictLevel.INSUFICIENTE_PARA_COMPARAR,
        f"OOF agregado {oof} está entre {OOF_MIN_PARA_CORRER} y "
        f"{OOF_MIN_DEFENDIBLE}: alcanza para explorar, NO para comparar "
        f"modelos. {detalle}.",
    )


# ══════════════════════════════════════════════════════════════════════════
#  Medición por activo
# ══════════════════════════════════════════════════════════════════════════

def _rango(fechas: Sequence[date]) -> tuple[Optional[str], Optional[str]]:
    return (str(fechas[0]), str(fechas[-1])) if fechas else (None, None)


def measure_asset(
    asset: str,
    *,
    ohlcv_root: Path,
    lookback: int,
    n_folds: int,
    umbral_global_default: float,
    patterns: Sequence[str] = DEFAULT_OHLCV_PATTERNS,
    percentile_mode: str = PercentileMode.ACUMULADO,
    rolling_window: int = ROLLING_WINDOW_DEFAULT,
) -> AssetMeasurement:
    """Mide un activo de punta a punta. Nunca lanza por ausencia de datos:
    devuelve la medición con los ceros que correspondan y una nota."""
    notas: list[str] = []

    serie_gdelt = read_series(asset)
    dias_gdelt = [r.day for r in serie_gdelt if r.entropy_shannon is not None]
    g_ini, g_fin = _rango(dias_gdelt)
    if len(dias_gdelt) < len(serie_gdelt):
        notas.append(
            f"{len(serie_gdelt) - len(dias_gdelt)} día(s) GDELT con "
            f"insufficient_events=True excluidos del conteo de profundidad."
        )

    lookup = load_ohlcv(ohlcv_root, asset, patterns=patterns)
    if lookup.df is None:
        # Enumerar lo intentado: "no hay datos" y "busqué en el lugar
        # equivocado" se ven idénticos sin esto, y son problemas distintos
        # con soluciones distintas.
        notas.append(
            f"Sin OHLCV para '{asset}'. Patrones probados: "
            f"{list(patterns)}. Rutas intentadas, en orden:"
        )
        notas.extend(f"    {ruta}" for ruta in lookup.intentos)
        notas.append(
            "Si el archivo existe con otro nombre, pasar --ohlcv-pattern "
            "(ej: --ohlcv-pattern '{asset}_ohlcv_v5.parquet')."
        )
        return AssetMeasurement(
            asset=asset,
            gdelt_days=len(dias_gdelt), gdelt_first=g_ini, gdelt_last=g_fin,
            ohlcv_days=0, ohlcv_first=None, ohlcv_last=None, overlap_days=0,
            joined_rows=0, coverage_ratio=0.0, n_dropped_no_entropy=0,
            folds=[], oof_post_mask=0, oof_post_propia=0, oof_post_arrastrada=0,
            oof_up=0, oof_down=0,
            verdict=VerdictLevel.SIN_DATOS,
            verdict_reason="Sin OHLCV persistido para este activo.",
            notes=notas,
        )

    ohlcv = lookup.df
    notas.append(f"OHLCV leído de: {lookup.path}")
    dias_ohlcv = sorted({ts.date() for ts in ohlcv["timestamp"]})
    o_ini, o_fin = _rango(dias_ohlcv)
    solapamiento = len(set(dias_ohlcv) & set(dias_gdelt))

    resultado: BuildDatasetResult = build_training_dataset(ohlcv, asset)

    if len(resultado.rows) < 2:
        notas.append(
            f"El join produjo {len(resultado.rows)} fila(s): hacen falta al "
            f"menos 2 para tener un solo log_return."
        )
        return AssetMeasurement(
            asset=asset,
            gdelt_days=len(dias_gdelt), gdelt_first=g_ini, gdelt_last=g_fin,
            ohlcv_days=len(dias_ohlcv), ohlcv_first=o_ini, ohlcv_last=o_fin,
            overlap_days=solapamiento,
            joined_rows=len(resultado.rows),
            coverage_ratio=resultado.coverage_ratio,
            n_dropped_no_entropy=resultado.n_dropped_no_entropy,
            folds=[], oof_post_mask=0, oof_post_propia=0, oof_post_arrastrada=0,
            oof_up=0, oof_down=0,
            verdict=VerdictLevel.SIN_DATOS,
            verdict_reason="Join sin filas suficientes para derivar un target.",
            notes=notas,
        )

    if len(resultado.rows) <= lookback:
        notas.append(
            f"El join dio {len(resultado.rows)} filas y el lookback es "
            f"{lookback}: ningún índice alcanza el piso i >= lookback."
        )

    serie = build_serie_derivada(resultado)
    folds = measure_folds(
        serie, lookback=lookback, n_folds=n_folds,
        umbral_global_default=umbral_global_default,
        percentile_mode=percentile_mode, rolling_window=rolling_window,
    )
    oof = sum(f.n_post_mask for f in folds)
    oof_propia = sum(f.n_post_propia for f in folds)
    # El veredicto se calcula sobre el n TOTAL — ese criterio no cambia.
    # El aviso de arrastre es información adicional, no un veredicto nuevo.
    veredicto, motivo = dictaminar(oof, folds)
    aviso = evaluar_arrastre(oof, oof_propia)

    return AssetMeasurement(
        asset=asset,
        gdelt_days=len(dias_gdelt), gdelt_first=g_ini, gdelt_last=g_fin,
        ohlcv_days=len(dias_ohlcv), ohlcv_first=o_ini, ohlcv_last=o_fin,
        overlap_days=solapamiento,
        joined_rows=len(resultado.rows),
        coverage_ratio=resultado.coverage_ratio,
        n_dropped_no_entropy=resultado.n_dropped_no_entropy,
        folds=folds,
        oof_post_mask=oof,
        oof_post_propia=oof_propia,
        oof_post_arrastrada=sum(f.n_post_arrastrada for f in folds),
        oof_up=sum(f.n_up for f in folds),
        oof_down=sum(f.n_down for f in folds),
        percentile_mode=percentile_mode,
        oof_solo_entropia=sum(f.n_solo_entropia for f in folds),
        oof_solo_vitality=sum(f.n_solo_vitality for f in folds),
        oof_ambas_ramas=sum(f.n_ambas_ramas for f in folds),
        tasa_disparo_oof=(oof / sum(f.n_total for f in folds))
                         if sum(f.n_total for f in folds) else 0.0,
        verdict=veredicto, verdict_reason=motivo,
        arrastre_warning=aviso, notes=notas,
    )


# ══════════════════════════════════════════════════════════════════════════
#  Reporte (stdout — este tool NO escribe archivos)
# ══════════════════════════════════════════════════════════════════════════

def render_comparison(
    por_modo: dict[str, Sequence[AssetMeasurement]], *, lookback: int,
) -> str:
    """
    Los tres criterios sobre los mismos datos, lado a lado.

    Muestra tasa y no solo conteo, y desglosa las dos ramas del OR: si un
    criterio de percentil mueve el n pero la rama de entropía sigue
    aportando una fracción mínima de los disparos, el cuello está en otro
    lado y la comparación lo deja ver en vez de esconderlo.
    """
    out = [
        "═══ COMPARACIÓN DE CRITERIOS DE PERCENTIL ═══",
        f"lookback={lookback} · máscara: (entropy >= P90) OR (vitality == 9)",
        "Ningún modo cambia core/scoring.py: los tres llaman a",
        "compute_adaptive_percentile() con distinta historia.",
        "",
    ]
    activos = [m.asset for m in next(iter(por_modo.values()))]
    for activo in activos:
        out.append(f"── {activo} " + "─" * max(0, 58 - len(activo)))
        out.append("  modo         OOF   tasa    solo_ent  solo_vit  ambas   "
                   "veredicto")
        for modo, mediciones in por_modo.items():
            m = next((x for x in mediciones if x.asset == activo), None)
            if m is None:
                continue
            out.append(
                f"  {modo:<11} {m.oof_post_mask:>4}  "
                f"{m.tasa_disparo_oof * 100:>5.1f}%  "
                f"{m.oof_solo_entropia:>8}  {m.oof_solo_vitality:>8}  "
                f"{m.oof_ambas_ramas:>5}   {m.verdict}"
            )
        base = next((x for x in por_modo[PercentileMode.ACUMULADO]
                     if x.asset == activo), None)
        if base is not None and base.oof_post_mask:
            por_ent = base.oof_solo_entropia + base.oof_ambas_ramas
            pct = 100.0 * por_ent / base.oof_post_mask
            out.append(
                f"  · En ACUMULADO, la entropía participa en {pct:.1f}% de los "
                f"disparos. Un criterio de percentil solo puede mover esa "
                f"fracción."
            )
        out.append("")
    return "\n".join(out)


def render_text(mediciones: Sequence[AssetMeasurement], *, lookback: int) -> str:
    out: list[str] = [
        "═══ MEDICIÓN DE MUESTRAS POST-MÁSCARA GÖDEL ═══",
        f"lookback={lookback} · máscara: (entropy >= P90) OR (vitality == 9)",
        "P90 recalculado por fold, solo con días anteriores a su validación.",
        "",
    ]
    for m in mediciones:
        out.append(f"── {m.asset} " + "─" * max(0, 60 - len(m.asset)))
        out.append(
            f"  GDELT : {m.gdelt_days:>5} días  [{m.gdelt_first} .. {m.gdelt_last}]"
        )
        out.append(
            f"  OHLCV : {m.ohlcv_days:>5} días  [{m.ohlcv_first} .. {m.ohlcv_last}]"
        )
        out.append(f"  Solapamiento: {m.overlap_days} días")
        out.append(
            f"  Join  : {m.joined_rows} filas · coverage_ratio="
            f"{m.coverage_ratio:.4f} · descartadas sin entropía="
            f"{m.n_dropped_no_entropy}"
        )
        if m.folds:
            out.append("")
            out.append(
                "  fold  train  validación                  P90      "
                "n_tot  n_mask  propia  arrast   tasa  s_ent  s_vit  ambas"
                "   sube   baja  estable"
            )
            for f in m.folds:
                out.append(
                    f"  {f.fold:>4}  {f.n_train_days:>5}  "
                    f"{str(f.val_start):>10}..{str(f.val_end):<10}  "
                    f"{f.umbral_used:>7.4f}  {f.n_total:>5}  {f.n_post_mask:>6}  "
                    f"{f.n_post_propia:>6}  {f.n_post_arrastrada:>6}  "
                    f"{f.tasa_disparo * 100:>4.1f}%  {f.n_solo_entropia:>5}  "
                    f"{f.n_solo_vitality:>5}  {f.n_ambas_ramas:>5}  "
                    f"{f.n_up:>5}  {f.n_down:>5}  {'sí' if f.estable else 'no':>7}"
                )
        out.append("")
        out.append(
            f"  OOF agregado post-máscara: {m.oof_post_mask} "
            f"(sube={m.oof_up}, baja={m.oof_down})"
        )
        out.append(
            f"  OOF sin arrastre (entropía propia): {m.oof_post_propia}"
            f"   ·   con entropía arrastrada: {m.oof_post_arrastrada}"
        )
        out.append(
            f"  Tasa de disparo OOF: {m.tasa_disparo_oof * 100:.1f}%"
            f"   ·   ramas del OR: solo_entropía={m.oof_solo_entropia}, "
            f"solo_vitality={m.oof_solo_vitality}, ambas={m.oof_ambas_ramas}"
        )
        out.append(f"  VEREDICTO: {m.verdict} — {m.verdict_reason}")
        if m.arrastre_warning:
            out.append(f"  ⚠️  {m.arrastre_warning}")
        for nota in m.notes:
            out.append(f"  · {nota}")
        out.append("")
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="measure_godel_samples",
        description="Mide cuántas muestras sobreviven la máscara Gödel. "
                    "Read-only: no entrena, no escribe, no normaliza nada.",
    )
    p.add_argument("--assets", nargs="+", required=True,
                   help="Activos a medir, ej: BTC XAU NVDA NIFTY50")
    p.add_argument("--ohlcv-root", type=Path, default=None,
                   help="Raíz donde buscar el OHLCV. Se prueba plano y en "
                        "subdirectorio por activo ({asset}/, {asset}/ohlcv/, "
                        "ohlcv/{asset}/), más búsqueda recursiva como último "
                        "recurso. Default: drive_root()/metrics/ohlcv")
    p.add_argument("--ohlcv-pattern", nargs="+", default=list(DEFAULT_OHLCV_PATTERNS),
                   metavar="PATRON",
                   help="Patrón(es) de nombre de archivo con placeholder "
                        "{asset}. Se prueban en orden. "
                        "Default: %(default)s (conserva el comportamiento "
                        "anterior). Ejemplo real: "
                        "--ohlcv-pattern '{asset}_ohlcv_v5.parquet'")
    p.add_argument("--lookback", type=int, default=LOOKBACK_DEFAULT,
                   help=f"Ventana de features. Default {LOOKBACK_DEFAULT} "
                        f"(_LOOKBACK_DEFAULT del legacy).")
    p.add_argument("--n-folds", type=int, default=5,
                   help="Folds walk-forward de ventana expansiva. Default 5.")
    p.add_argument("--umbral-global-default", type=float, required=True,
                   help="Default global del P90 para cold-start. SIN valor "
                        "por defecto a propósito: compute_adaptive_percentile() "
                        "documenta que para P90 no hay default legacy "
                        "confirmado y debe proveerse explícitamente.")
    p.add_argument("--percentile-mode", choices=PercentileMode.todos(),
                   default=PercentileMode.ACUMULADO,
                   help="Cómo se calcula el umbral de entropía. ACUMULADO "
                        "(default) es el comportamiento actual y no cambia "
                        "nada. Este tool MIDE criterios, no los cambia: "
                        "core/scoring.py queda intacto.")
    p.add_argument("--rolling-window", type=int, default=ROLLING_WINDOW_DEFAULT,
                   help=f"Ventana de los modos MOVIL y ZSCORE. Default "
                        f"{ROLLING_WINDOW_DEFAULT} (días hábiles de un año).")
    p.add_argument("--compare-modes", action="store_true",
                   help="Corre los tres criterios sobre los mismos datos y "
                        "los compara fold a fold. Ignora --percentile-mode.")
    p.add_argument("--format", choices=("text", "json"), default="text")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    root = args.ohlcv_root or default_ohlcv_root()
    if not root.is_dir():
        # Ruta inexistente SÍ es fallo real (a diferencia de "este activo no
        # tiene datos", que es una medición válida).
        print(
            f"ERROR: el directorio de OHLCV no existe: {root}\n"
            f"Pasar --ohlcv-root con una ruta válida, o configurar "
            f"SPEL_DRIVE_ROOT.",
            file=sys.stderr,
        )
        return 2

    if args.rolling_window < 2:
        print("ERROR: --rolling-window debe ser >= 2 (una ventana de 1 día "
              "no tiene dispersión que normalizar)", file=sys.stderr)
        return 2

    modos = (list(PercentileMode.todos()) if args.compare_modes
             else [args.percentile_mode])

    por_modo: dict[str, list[AssetMeasurement]] = {}
    for modo in modos:
        por_modo[modo] = [
            measure_asset(
                a, ohlcv_root=root, lookback=args.lookback,
                n_folds=args.n_folds,
                umbral_global_default=args.umbral_global_default,
                patterns=args.ohlcv_pattern,
                percentile_mode=modo, rolling_window=args.rolling_window,
            )
            for a in args.assets
        ]
    mediciones = por_modo[modos[0]]

    if args.format == "json":
        reporte = {
            "lookback": args.lookback, "n_folds": args.n_folds,
            "ohlcv_root": str(root), "ohlcv_pattern": list(args.ohlcv_pattern),
            "rolling_window": args.rolling_window,
            "percentile_modes": modos,
            "assets": [asdict(m) for m in mediciones],
        }
        # `por_modo` solo con --compare-modes: sin el flag sería una copia
        # literal de "assets" y duplicaría el reporte sin agregar nada.
        if args.compare_modes:
            reporte["por_modo"] = {m: [asdict(x) for x in v]
                                   for m, v in por_modo.items()}
        print(json.dumps(reporte, indent=2, ensure_ascii=False))
    elif args.compare_modes:
        print(render_comparison(por_modo, lookback=args.lookback))
    else:
        print(render_text(mediciones, lookback=args.lookback))

    # Exit 0 SIEMPRE que la medición se haya completado, sin importar el
    # veredicto. Un veredicto negativo es información, no un fallo.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
