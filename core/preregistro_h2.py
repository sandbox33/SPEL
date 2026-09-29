"""
core/preregistro_h2.py
========================
Los números de `research/preregistro_h2.md`, en código. Nada más.

El documento es la fuente y no se modifica después de fusionarse;
`tests/test_preregistro_h2.py` verifica que cada constante de acá esté
escrita en él, que no cambie y que no tenga voseo. El código de evaluación
—los modelos HAR, la pérdida, el test de Diebold–Mariano— va en otro PR,
después: acá solo está lo que el pre-registro fija antes de mirar.
"""

from __future__ import annotations

import math

#: Rezago de la entropía en barras: el del brief. [PENDIENTE DEL ADMIN] en
#: el documento (sección 3): GDELT publica el día t−1 durante el día t.
REZAGO_ENTROPIA_BARRAS = 1

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
