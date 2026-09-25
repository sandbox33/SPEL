"""
tests/deriv_falso.py
======================
Un Deriv falso para los tests de deriv_ws, la sonda y las velas. No toca la
red: se inyecta por el parámetro `connector=`, igual que en
test_provider_coverage.py.

Responde por TIPO DE MENSAJE (la primera clave del payload), devuelve el
`req_id` y el `msg_type` como la API real, y registra todo lo enviado. Un
tipo sin manejador responde con un error de Deriv, así un test que olvida
uno falla en voz alta en vez de colgarse.
"""

from __future__ import annotations

import json
from typing import Callable


class DerivFalso:
    def __init__(self, manejadores: dict[str, Callable[[dict], dict]]):
        self.manejadores = manejadores
        self.enviados: list[dict] = []
        self.conexiones = 0
        self._pendientes: list[str] = []

    # ── lo que ve el código bajo prueba ──────────────────────────────────
    def connector(self, uri: str):
        falso = self

        class _CM:
            async def __aenter__(self):
                falso.conexiones += 1
                falso.uri = uri
                return falso

            async def __aexit__(self, *exc):
                return False

        return _CM()

    async def send(self, texto: str) -> None:
        payload = json.loads(texto)
        self.enviados.append(payload)
        nombre = next(iter(payload))
        manejador = self.manejadores.get(nombre)
        if manejador is None:
            cuerpo = {"error": {"code": "UnrecognisedRequest",
                                "message": f"sin manejador para {nombre}"}}
        else:
            cuerpo = manejador(payload)
        respuesta = {"msg_type": nombre, "req_id": payload.get("req_id"), **cuerpo}
        self._pendientes.append(json.dumps(respuesta))

    async def recv(self) -> str:
        return self._pendientes.pop(0)

    # ── ayudas ───────────────────────────────────────────────────────────
    def de_tipo(self, nombre: str) -> list[dict]:
        return [p for p in self.enviados if next(iter(p)) == nombre]
