"""
core/preregistro_h2.py
========================
Los números de `research/preregistro_h2.md`, en código, y la regla que une
la entropía con cada retorno.

El documento es la fuente y no se modifica después de fusionarse;
`tests/test_preregistro_h2.py` verifica que cada constante de acá esté
escrita en él, que no cambie y que no tenga voseo. El código de evaluación
—los modelos HAR, la pérdida, Diebold–Mariano, Holm y el MCS— va en otro
PR, después: acá solo está lo que el pre-registro fija antes de mirar.

La unión entropía-retorno sí está acá, porque es la regla que impide usar
una entropía que no estaba publicada (sección 3): se fija antes de mirar y
no recibe precios.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Mapping, Optional

#: La primaria: el retorno del día t usa la entropía de la fecha t − 2, en
#: días de calendario (decisión del Admin del 29-sep-2026 y D6, sección 3).
#: No es un corrimiento de barras.
REZAGO_ENTROPIA_DIAS_CALENDARIO = 2

#: La secundaria (D6): la entropía de t − 1. No es operativa, porque al
#: abrir t todavía no está publicada, y nunca decide la conclusión.
REZAGO_SECUNDARIO_DIAS_CALENDARIO = 1

#: Los únicos rezagos válidos, la primaria primero.
REZAGOS_DIAS_CALENDARIO: tuple[int, ...] = (
    REZAGO_ENTROPIA_DIAS_CALENDARIO, REZAGO_SECUNDARIO_DIAS_CALENDARIO)

#: [INTERPRETACIÓN] Cuántos días más atrás de la fecha del rezago puede
#: buscar el as-of si esa fecha no tiene entropía. Cero: sin ella, el
#: retorno se excluye (sección 2: no se rellena nada).
TOLERANCIA_ASOF_DIAS = 0

#: Horizontes de pronóstico, en barras del calendario del activo (D6).
HORIZONTES: tuple[int, ...] = (1, 5, 22)

#: Pruebas de Diebold–Mariano por activo: la familia que ajusta Holm. La
#: secundaria cuenta en el N (D6, D3), así que entra en la misma familia.
PRUEBAS_POR_ACTIVO = len(REZAGOS_DIAS_CALENDARIO) * len(HORIZONTES)

#: Alfa de Diebold–Mariano, unilateral, para la familia de cada activo.
ALFA_DIEBOLD_MARIANO = 0.05

#: [INTERPRETACIÓN] El Model Confidence Set: nivel, réplicas del bootstrap
#: de bloques móviles y semilla, fijados antes de mirar (sección 7).
NIVEL_MCS = 0.90
REPLICAS_MCS = 10_000
SEMILLA_MCS = 20261017

#: Barras con que arranca la ventana expansiva de estimación.
VENTANA_INICIAL_BARRAS = 504

#: Componentes del HAR (diario, semanal, mensual) en barras del calendario.
HAR_VENTANAS_CONTINUO: tuple[int, int, int] = (1, 7, 30)
HAR_VENTANAS_HABIL: tuple[int, int, int] = (1, 5, 22)


def ancho_newey_west(n_pronosticos: int) -> int:
    """⌊4 · (T/100)^(2/9)⌋: el ancho de banda de Newey–West para la varianza
    de la diferencia de pérdidas."""
    if n_pronosticos <= 0:
        raise ValueError(f"T tiene que ser positivo, recibido {n_pronosticos}")
    return math.floor(4 * (n_pronosticos / 100) ** (2 / 9))


def rezagos_hac(n_pronosticos: int, horizonte: int) -> int:
    """Los rezagos de Newey–West y el largo de bloque del MCS: el ancho de
    `ancho_newey_west`, o el horizonte si es mayor, porque los pronósticos
    de horizonte h se solapan."""
    if horizonte not in HORIZONTES:
        raise ValueError(f"horizonte {horizonte!r}: uno de {HORIZONTES}")
    return max(ancho_newey_west(n_pronosticos), horizonte)


def fecha_de_entropia(
    dia_retorno: date, entropia: Mapping[date, Optional[float]],
    *, rezago: int = REZAGO_ENTROPIA_DIAS_CALENDARIO,
) -> Optional[date]:
    """As-of hacia atrás sobre `dia_retorno − rezago días`, con tolerancia
    TOLERANCIA_ASOF_DIAS: la fecha de la entropía que usa el retorno de
    `dia_retorno`, o None si no hay ninguna utilizable y el retorno se
    excluye. Una fecha con valor None (`insufficient_events`) no tiene
    entropía. Nunca devuelve una fecha posterior a `dia_retorno − rezago`,
    y el rezago es uno de REZAGOS_DIAS_CALENDARIO: nunca el 0."""
    if isinstance(rezago, bool) or rezago not in REZAGOS_DIAS_CALENDARIO:
        raise ValueError(f"rezago {rezago!r}: uno de {REZAGOS_DIAS_CALENDARIO}")
    tope = dia_retorno - timedelta(days=rezago)
    for atras in range(TOLERANCIA_ASOF_DIAS + 1):
        d = tope - timedelta(days=atras)
        if entropia.get(d) is not None:
            return d
    return None
