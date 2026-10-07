"""
ingestion/limitador.py
======================
Límite de llamadas a una API con cuota por minuto y por créditos.

PORTADO, NO REESCRITO, de `Limitador` en tests/test_fuentes_sonda3_live.py
(sonda §0.A-3 v2, punto 5g de la §0.A-3b), que es el que respetó la cuota
de TwelveData en las dos sondas. La ingesta intradía del oro (brief del
Admin del 06-oct-2026 (4), punto 1a) lo usa con el tope por minuto de la
3b. La sonda lo importa de acá con sus valores de entonces.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable


class Limitador:
    """A lo sumo `por_minuto` llamadas en cualquier ventana de 60 s, y no
    más de `tope_creditos` en total, a 1 crédito por llamada."""

    def __init__(self, por_minuto: int, tope_creditos: int, *,
                 reloj: Callable[[], float] = time.monotonic,
                 dormir: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.por_minuto, self.tope = por_minuto, tope_creditos
        self.reloj, self.dormir = reloj, dormir
        self.marcas: list[float] = []
        self.creditos = 0

    async def turno(self) -> bool:
        """True si se puede llamar (y lo cuenta); False si se agotó el tope."""
        if self.creditos >= self.tope:
            return False
        ahora = self.reloj()
        recientes = [t for t in self.marcas if ahora - t < 60]
        if len(recientes) >= self.por_minuto:
            await self.dormir(60 - (ahora - recientes[0]))
        self.marcas.append(self.reloj())
        self.creditos += 1
        return True
