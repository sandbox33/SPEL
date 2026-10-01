"""
core/preregistro_h2.py
========================
Los números de `research/preregistro_h2.md`, en código, y la regla que une
la entropía con cada retorno.

El documento es la fuente y no se modifica después de fusionarse;
`tests/test_preregistro_h2.py` verifica que cada constante de acá esté
escrita en él, que no cambie y que no tenga voseo. El código de evaluación
—los modelos HAR, la pérdida, el test de Diebold–Mariano— va en otro PR,
después: acá solo está lo que el pre-registro fija antes de mirar.

La unión entropía-retorno sí está acá, porque es la regla que impide usar
una entropía que no estaba publicada (sección 3): se fija antes de mirar y
no recibe precios.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Mapping, Optional

#: El retorno del día t usa la entropía de la fecha t − 2, en días de
#: calendario (decisión del Admin del 29-sep-2026, sección 3). No es un
#: corrimiento de barras.
REZAGO_ENTROPIA_DIAS_CALENDARIO = 2

#: [INTERPRETACIÓN] Cuántos días más atrás de t − 2 puede buscar el as-of
#: si t − 2 no tiene entropía. Cero: sin entropía en t − 2, el retorno se
#: excluye (sección 2: no se rellena nada).
TOLERANCIA_ASOF_DIAS = 0

#: Alfa del test de Diebold–Mariano, unilateral.
ALFA_DIEBOLD_MARIANO = 0.05

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


def fecha_de_entropia(
    dia_retorno: date, entropia: Mapping[date, Optional[float]],
) -> Optional[date]:
    """As-of hacia atrás sobre `dia_retorno − 2 días`, con tolerancia
    TOLERANCIA_ASOF_DIAS: la fecha de la entropía que usa el retorno de
    `dia_retorno`, o None si no hay ninguna utilizable y el retorno se
    excluye. Una fecha con valor None (`insufficient_events`) no tiene
    entropía. Nunca devuelve una fecha posterior a `dia_retorno − 2`."""
    tope = dia_retorno - timedelta(days=REZAGO_ENTROPIA_DIAS_CALENDARIO)
    for atras in range(TOLERANCIA_ASOF_DIAS + 1):
        d = tope - timedelta(days=atras)
        if entropia.get(d) is not None:
            return d
    return None
