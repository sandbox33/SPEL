"""
core/preregistro_h3.py
========================
Los números de `research/preregistro_h3.md`, en código. Nada más.

El documento es la fuente: se escribió y fusionó ANTES del PR #31, antes de
que el cron escribiera un solo precio del universo, y no se modifica.
`tests/test_preregistro_h3.py` verifica que cada constante de acá esté
escrita en el documento, que el documento no cambie y que no tenga voseo.

No hay backtest acá. Hay cuatro funciones que el pre-registro obliga a fijar
antes de mirar un precio, y que dependen solo de longitudes, conteos o de
K: la condición de historia, el recorte de la rejilla, el voto por activo y
el reparto del target entre los K activos.

DUPLICA A PROPÓSITO LOS NÚMEROS QUE COMPARTE CON H1 (la rejilla, la ventana
de volatilidad, las compuertas). Cada pre-registro está fijado por su propio
documento y su propio sha256: si H3 importara de `core/preregistro_h1.py`,
un cambio en H1 movería los números de H3 sin tocar su documento, que es
justo lo que un pre-registro existe para impedir. Además, cuando este
módulo se fusiona, el de H1 todavía no está en `main`.
"""

from __future__ import annotations

#: Lookbacks de la rejilla, en barras diarias. Los mismos que H1.
REJILLA_H3: tuple[int, ...] = (10, 20, 40, 80, 160, 320)

#: historia_usable ≥ lookback_max + esto, para cada activo.
HISTORIA_POSTERIOR_AL_CALENTAMIENTO_BARRAS = 756

#: Target de volatilidad de la cartera, repartido entre los K activos.
FRACCION_VOL_CARTERA = 0.25

#: Tope de nocional por activo, en veces el capital.
APALANCAMIENTO_MAXIMO_POR_ACTIVO = 2.0

#: Barras de la volatilidad realizada del sizing.
VENTANA_VOL_BARRAS = 20

#: Un lookback que saltaría más de esta fracción de sus señales es no
#: ejecutable y no vota en ese activo.
FRACCION_MAXIMA_SALTADA = 0.5

#: Razón mínima distancia al stop-out / distancia al canal de salida, para
#: decidir si un lookback es ejecutable (la regla de H1).
RAZON_STOPOUT_SALIDA = 1.5

#: Capital de referencia (DG-2) y el del reporte de diagnóstico.
CAPITAL_REFERENCIA_USD = 100.0
CAPITAL_DIAGNOSTICO_USD = 1500.0

#: KID oficiales de Deriv para Multiplicadores, citados por el Admin: cripto
#: (27-ago-2026) y el ejemplo de EURUSD del de forex (27-jul-2026).
COMISION_KID_CRIPTO_FRACCION = 0.001
COMISION_KID_FOREX_EURUSD_FRACCION = 0.000199

#: Piso de comisión por contrato, en todos los mercados.
COMISION_PISO_USD = 0.10

#: Compuertas (las de H1 §10 del Brief final v3).
SHARPE_MINIMO_AVANZAR = 0.5
PSR_MINIMO = 0.90
DSR_MINIMO = 0.90
SHARPE_DETENER = 0.3

#: Duración del forward en demo (DG-3).
MESES_FORWARD_DEMO = 6

#: Ensayos previos sobre BTC, fallidos y documentados.
ENSAYOS_PREVIOS_FALLIDOS = 3

#: Variantes de H3 que suman al N.
VARIANTES_H3 = 1

#: N contado ingenuamente: los seis lookbacks y el ensemble de H1, los tres
#: previos y la cartera de H3.
N_INGENUO = len(REJILLA_H3) + 1 + ENSAYOS_PREVIOS_FALLIDOS + VARIANTES_H3


def historia_requerida(lookback_max: int) -> int:
    """historia_usable mínima para una rejilla con ese lookback máximo."""
    return lookback_max + HISTORIA_POSTERIOR_AL_CALENTAMIENTO_BARRAS


def rejilla_soportada(historia_usable: int) -> list[int]:
    """La rejilla más larga que la historia usable de UN activo soporta,
    recortando desde arriba. Solo longitudes: no recibe precios. Lista
    vacía = el activo no entra al universo."""
    for k in range(len(REJILLA_H3), 0, -1):
        rejilla = REJILLA_H3[:k]
        if historia_usable >= historia_requerida(max(rejilla)):
            return list(rejilla)
    return []


def largo_por_voto(largos: int, votantes: int) -> bool:
    """DG-4: largo si al menos la mitad de los lookbacks que votan están
    largos. Sin votantes no hay contrato."""
    if votantes <= 0:
        return False
    return 2 * largos >= votantes


def lookback_ejecutable(fraccion_saltada: float) -> bool:
    """Un lookback que saltaría MÁS del 50 % de sus señales no vota."""
    return fraccion_saltada <= FRACCION_MAXIMA_SALTADA


def fraccion_vol_por_activo(k_activos: int) -> float:
    """El reparto del target de cartera entre K activos: 0,25 / K. Depende
    solo de K. Ver la sección 6 del documento para por qué no 0,25 / √K."""
    if k_activos <= 0:
        raise ValueError(f"K tiene que ser positivo, recibido {k_activos}")
    return FRACCION_VOL_CARTERA / k_activos
