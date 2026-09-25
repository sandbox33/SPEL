"""
core/preregistro_h1.py
========================
Los números de `research/preregistro_h1.md`, en código. Nada más.

El documento es la fuente: se escribió y fusionó ANTES de que existiera el
backtest (Brief H1-A), y no se modifica. Este módulo existe para que H1-B no
pueda usar otros números sin que un test lo note:
`tests/test_preregistro_h1.py` verifica que cada constante de acá aparezca
escrita en el documento, y que el documento no cambie.

No hay backtest acá. Hay dos funciones que dependen SOLO de la longitud de
la serie -- la condición de parada y el recorte de la rejilla --, que el
pre-registro obliga a calcular antes de mirar un precio. Las usa el reporte
de profundidad de `ingestion/velas.py`.
"""

from __future__ import annotations

#: Lookbacks de la rejilla, en barras diarias.
REJILLA_H1: tuple[int, ...] = (10, 20, 40, 80, 160, 320)

#: Walk-forward anclado.
ENTRENAMIENTO_INICIAL_BARRAS = 504
OOS_BARRAS_POR_FOLD = 126
N_FOLDS = 4

#: Sizing: posición = capital × FRACCION_VOL_OBJETIVO / vol_realizada_anualizada,
#: con la vol realizada sobre VENTANA_VOL_BARRAS barras y un tope de
#: APALANCAMIENTO_MAXIMO veces el capital.
FRACCION_VOL_OBJETIVO = 0.25
VENTANA_VOL_BARRAS = 20
APALANCAMIENTO_MAXIMO = 2.0

#: La distancia al stop-out tiene que ser al menos esto por la distancia al
#: canal de salida.
RAZON_STOPOUT_SALIDA = 1.5

#: Comisión de referencia del documento de multiplicadores de cripto de
#: Deriv, tal como la cita el pre-registro: fracción del nocional y mínimo
#: en USD. Entra al máximo con las mediciones de la sonda.
COMISION_REFERENCIA_FRACCION = 0.001
COMISION_REFERENCIA_MINIMO_USD = 0.10

#: Compuertas.
SHARPE_MINIMO_AVANZAR = 0.5
PSR_MINIMO = 0.90
DSR_MINIMO = 0.90
SHARPE_UMBRAL_ALTERNATIVA = 0.3

#: Ensayos previos sobre BTC, fallidos y documentados (máscara Gödel, proxy
#: de transfer entropy, EMA 20/63), que suman al N del DSR.
ENSAYOS_PREVIOS_FALLIDOS = 3

#: N contado ingenuamente para la cota pesimista del DSR: los seis
#: lookbacks, el ensemble y los tres previos.
N_INGENUO = len(REJILLA_H1) + 1 + ENSAYOS_PREVIOS_FALLIDOS


def historia_requerida(lookback_max: int) -> int:
    """La condición de parada del pre-registro:
    lookback_max + 504 + 4 × (lookback_max + 126).

    El primer lookback_max calienta los indicadores; después vienen el
    entrenamiento inicial y los cuatro folds, cada uno precedido por una
    purga igual al lookback máximo."""
    return (lookback_max + ENTRENAMIENTO_INICIAL_BARRAS
            + N_FOLDS * (lookback_max + OOS_BARRAS_POR_FOLD))


def rejilla_soportada(historia_usable: int) -> list[int]:
    """La rejilla más larga que la historia usable soporta, recortando desde
    arriba. Depende SOLO de la longitud: el pre-registro prohíbe elegirla
    mirando precios o retornos, y esta función ni los recibe.

    Lista vacía = ni la rejilla recortada alcanza, y el backtest no se
    corre."""
    for k in range(len(REJILLA_H1), 0, -1):
        rejilla = REJILLA_H1[:k]
        if historia_usable >= historia_requerida(max(rejilla)):
            return list(rejilla)
    return []
