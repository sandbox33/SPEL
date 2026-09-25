"""
ingestion/deriv_ws.py
=======================
Sesión de SOLO LECTURA contra la API WebSocket de Deriv. Es el único lugar
del repo, fuera del adapter OHLCV, que abre una conexión con Deriv, y existe
para que tres reglas del Brief H1-A no dependan de la disciplina de cada
llamador:

  1. EL ENTORNO ES OBLIGATORIO Y NO TIENE DEFAULT. `abrir_sesion("demo", ...)`
     o `abrir_sesion("real", ...)`: no hay tercera opción ni valor implícito.
     `real` exige ADEMÁS la variable de entorno `SPEL_DERIV_PERMITIR_REAL=1`,
     que no es el token: tener un token de cuenta real a mano no alcanza para
     usarlo. La comprobación corre ANTES de abrir la conexión.

  2. LISTA BLANCA DE MENSAJES. Solo se envían los siete de
     `MENSAJES_PERMITIDOS`. Cualquier otro -- en particular los que abren,
     cierran o cancelan un contrato -- se rechaza antes de tocar el socket.
     `tests/test_deriv_ws.py` además recorre el código del repo por AST y
     falla si aparece el nombre de alguno de esos tres mensajes como
     literal.

  3. CADA RESPUESTA CRUDA LLEVA SU SHA256. La sonda de instrumentos guarda
     los hashes junto a lo que extrae, para que cualquier número derivado se
     pueda rastrear hasta el byte que lo originó.

══ LA API LEGACY NO SEPARA DEMO Y REAL POR URL ══

Este módulo usa el endpoint de `DERIV_WS_ENDPOINT` (el mismo que
`DerivAdapter`): uno solo para las dos cuentas. Lo que decide la cuenta es
el token. Por eso, cuando hay token, la sesión llama a `authorize` y
verifica `authorize.is_virtual` -- documentado como "1 or 0, indicating
whether the account is a virtual-money account" -- contra el entorno
pedido. Un token de cuenta real con `entorno="demo"` corta la sesión.

Sin token no hay cuenta, y los siete mensajes son públicos (`auth_required:
0` en los esquemas oficiales). En ese caso el entorno se valida igual -- la
regla 1 no depende de que haya token --, pero no hay cuenta que comparar.

La API nueva de opciones separa `/options/ws/demo` y `/options/ws/real` y
autentica con un OTP de un solo uso. No se usa todavía: ver
`decision-log.md`, 2026-09-25.

══ `proposal` ES SOLO COTIZACIÓN ══

El brief pide `proposal` "con subscribe: 0". El esquema oficial de
`proposal` admite `subscribe` solo con el valor 1 (enum: [1]), así que
mandar un 0 podría rechazarse por validación. Lo equivalente, y lo que se
hace, es NO mandar el campo: sin él no hay stream, hay una cotización. Una
`proposal` con `subscribe` presente se rechaza acá.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
from dataclasses import dataclass
from typing import Any, Literal, Optional, get_args

from ingestion.adapters import (
    DERIV_WS_ENDPOINT,
    AdapterDataError,
    DerivAdapter,
    raise_if_deriv_error,
)

Entorno = Literal["demo", "real"]

#: La variable que hay que poner a "1" para que `entorno="real"` se acepte.
#: Es distinta del token a propósito: el token dice QUÉ cuenta, esto dice
#: que alguien decidió usar esa cuenta desde este proceso.
PERMISO_REAL_ENV_VAR = "SPEL_DERIV_PERMITIR_REAL"

#: Los únicos mensajes que esta sesión envía. Todo lo que no está acá se
#: rechaza antes de tocar el socket.
MENSAJES_PERMITIDOS: frozenset[str] = frozenset({
    "active_symbols", "contracts_for", "proposal", "ticks_history",
    "time", "authorize", "ping",
})

#: Segundos de espera por respuesta: el default de `DerivAdapter.__init__`.
#: Se LEE de la firma, no se repite el literal -- dos copias del mismo número
#: son la clase de duplicación que este repo ya encontró separándose sola
#: (ver N_MINIMO_PARCIAL en tools/calibrar_umbral_entropia.py).
TIMEOUT_RESPUESTA_S: float = inspect.signature(DerivAdapter.__init__).parameters["timeout_s"].default


class EntornoNoPermitidoError(RuntimeError):
    """`entorno="real"` sin el permiso explícito, o un entorno desconocido."""


class EntornoNoCoincideError(RuntimeError):
    """El token autoriza una cuenta de otro entorno que el pedido."""


class MensajeNoPermitidoError(ValueError):
    """Un mensaje fuera de la lista blanca."""


def verificar_entorno(entorno: str) -> Entorno:
    """Valida el entorno. Se llama ANTES de abrir cualquier conexión."""
    if entorno not in get_args(Entorno):
        raise EntornoNoPermitidoError(
            f"entorno {entorno!r} no existe: tiene que ser 'demo' o 'real', "
            f"explícito y sin default.")
    if entorno == "real" and os.environ.get(PERMISO_REAL_ENV_VAR) != "1":
        raise EntornoNoPermitidoError(
            f"entorno='real' exige {PERMISO_REAL_ENV_VAR}=1, además del token. "
            f"No se abrió ninguna conexión.")
    return entorno  # type: ignore[return-value]


def nombre_del_mensaje(payload: dict) -> str:
    """El tipo de un mensaje de Deriv es su PRIMERA clave (`{"ticks_history":
    "cryBTCUSD", ...}`). Se valida contra la lista blanca."""
    if not payload:
        raise MensajeNoPermitidoError("mensaje vacío")
    nombre = next(iter(payload))
    if nombre not in MENSAJES_PERMITIDOS:
        raise MensajeNoPermitidoError(
            f"{nombre!r} no está en la lista blanca de solo lectura "
            f"({', '.join(sorted(MENSAJES_PERMITIDOS))}). No se envió.")
    if nombre == "proposal" and "subscribe" in payload:
        raise MensajeNoPermitidoError(
            "proposal con `subscribe`: esta sesión solo cotiza, no abre "
            "streams. Se omite el campo (ver el docstring del módulo).")
    return nombre


@dataclass(frozen=True)
class Respuesta:
    mensaje: str
    datos: dict
    crudo: str
    sha256: str


class SesionDeriv:
    """Usar con `async with abrir_sesion(...)`. No se construye directo."""

    def __init__(self, entorno: Entorno, *, app_id: str, token: Optional[str],
                 connector: Any, timeout_s: float) -> None:
        self.entorno = entorno
        self._uri = DERIV_WS_ENDPOINT.format(app_id=app_id)
        self._token = token
        self._abrir = connector or DerivAdapter._default_connector
        self._timeout_s = timeout_s
        self._cm = None
        self._ws = None
        self._req_id = 0
        #: Lo que devolvió `authorize`, si hubo token.
        self.cuenta: Optional[dict] = None

    async def __aenter__(self) -> "SesionDeriv":
        self._cm = self._abrir(self._uri)
        self._ws = await self._cm.__aenter__()
        try:
            if self._token:
                r = await self.pedir({"authorize": self._token})
                self.cuenta = r.datos.get("authorize") or {}
                virtual = self.cuenta.get("is_virtual")
                esperado = 1 if self.entorno == "demo" else 0
                if virtual != esperado:
                    raise EntornoNoCoincideError(
                        f"el token autoriza una cuenta con is_virtual="
                        f"{virtual!r} y el entorno pedido es "
                        f"{self.entorno!r}. Se cierra la sesión.")
        except BaseException:
            await self._cm.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        await self._cm.__aexit__(*exc)

    async def pedir(self, payload: dict) -> Respuesta:
        nombre = nombre_del_mensaje(payload)
        self._req_id += 1
        enviado = {**payload, "req_id": self._req_id}
        await self._ws.send(json.dumps(enviado))
        crudo = await asyncio.wait_for(self._ws.recv(), self._timeout_s)
        try:
            datos = json.loads(crudo)
        except json.JSONDecodeError as exc:
            raise AdapterDataError(f"[deriv:{nombre}] respuesta no es JSON: {exc}") from exc
        # El token nunca va al contexto de un error: `authorize` se nombra,
        # su argumento no.
        raise_if_deriv_error(datos, context=nombre)
        if datos.get("req_id") != self._req_id or datos.get("msg_type") != nombre:
            raise AdapterDataError(
                f"[deriv:{nombre}] respuesta desparejada: req_id "
                f"{datos.get('req_id')!r} (esperado {self._req_id}), msg_type "
                f"{datos.get('msg_type')!r}")
        return Respuesta(nombre, datos, crudo,
                         hashlib.sha256(crudo.encode("utf-8")).hexdigest())


def abrir_sesion(
    entorno: Entorno,
    *,
    app_id: str,
    token: Optional[str] = None,
    connector: Any = None,
    timeout_s: float = TIMEOUT_RESPUESTA_S,
) -> SesionDeriv:
    """Valida el entorno AL LLAMAR, no al entrar al `async with`: con
    `entorno="real"` sin permiso, esto lanza sin haber creado ni el objeto
    que abriría la conexión."""
    verificar_entorno(entorno)
    if not app_id:
        raise ValueError("abrir_sesion requiere app_id no vacío")
    return SesionDeriv(entorno, app_id=app_id, token=token,
                       connector=connector, timeout_s=timeout_s)
