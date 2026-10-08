"""
ingestion/deriv_publico.py
==========================
Lectura del canal PÚBLICO nuevo de Deriv (`/ws/public`), sin cuenta ni
token. Existe porque `ingestion/deriv_ws.py` sigue hablando con la API
legacy, muerta desde el 01-oct-2026 (HTTP 520), y el brief del Admin del
06-oct-2026 (4), punto 1b, pide bajar las velas M5 del oro por el canal
nuevo.

PORTADO, NO REESCRITO, desde las sondas que ya lo usaron en vivo:
  · `CanalPublico` es `_Canal` de tests/test_deriv_sonda2_live.py (sonda
    §0.A-2) con dos parámetros: la lista blanca con la que valida (la de la
    sonda era la de deriv_ws, que la sonda sigue usando) y la pausa entre
    pedidos de `_CanalPausado` (tests/test_deriv_sonda3_live.py).
  · `profundidad` y `desalineadas` son las de la sonda §0.A-2, que la
    §0.A-3b usó para bajar las velas M5 y M15. Las sondas las importan de
    acá.

══ LISTA BLANCA ══

`MENSAJES_PUBLICOS`: `time`, `active_symbols`, `trading_times` y
`ticks_history`, sin `subscribe` ni `passthrough`. Es de solo lectura: nada
que cotice, abra o cierre un contrato. `trading_times` no está en la de
deriv_ws (que tiene siete mensajes y un test que los fija); el punto 2 del
brief la necesita para el calendario del oro.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Callable, Optional

from ingestion.adapters import DERIV_MAX_COUNT
from ingestion.deriv_ws import TIMEOUT_RESPUESTA_S, MensajeNoPermitidoError

#: El canal público nuevo, verificado DISPONIBLE (decision-log 2026-10-01).
ENDPOINT_PUBLICO = "wss://api.derivws.com/trading/v1/options/ws/public"

MENSAJES_PUBLICOS: frozenset[str] = frozenset({
    "time", "active_symbols", "trading_times", "ticks_history"})

#: Caracteres de una respuesta que no es JSON que van al informe.
_EXTRACTO = 400


def validar_publico(payload: dict) -> str:
    """El tipo del mensaje si pasa la lista blanca; si no, lanza sin enviar."""
    if not isinstance(payload, dict) or not payload:
        raise MensajeNoPermitidoError("mensaje vacío")
    nombre = next(iter(payload))
    if nombre not in MENSAJES_PUBLICOS:
        raise MensajeNoPermitidoError(
            f"{nombre!r} no está en la lista blanca del canal público "
            f"({', '.join(sorted(MENSAJES_PUBLICOS))}). No se envió.")
    if {"subscribe", "passthrough"} & set(payload):
        raise MensajeNoPermitidoError(f"{nombre} con subscribe o passthrough: no se envía")
    return nombre


class CanalPublico:
    """Un WS abierto, con el registro de cada mensaje. Cada payload pasa por
    `validar` antes de tocar el socket. `pedir` nunca lanza por lo que
    conteste Deriv: devuelve (entrada publicable, datos o None)."""

    def __init__(self, ws: Any, *, validar: Callable[[dict], str] = validar_publico,
                 pausa_s: float = 0.0, timeout_s: float = TIMEOUT_RESPUESTA_S) -> None:
        self.ws = ws
        self.req_id = 0
        self.validar = validar
        self.pausa_s = pausa_s
        self.timeout_s = timeout_s
        #: El texto de la última respuesta válida, para quien tenga que
        #: guardarla cruda. No va a `entrada`, que es publicable.
        self.ultimo_crudo: Optional[str] = None

    async def pedir(self, payload: dict) -> tuple[dict, Optional[dict]]:
        self.validar(payload)
        if self.pausa_s:
            await asyncio.sleep(self.pausa_s)
        self.req_id += 1
        entrada: dict[str, Any] = {"ok": False}
        try:
            await self.ws.send(json.dumps({**payload, "req_id": self.req_id}))
            crudo = await asyncio.wait_for(self.ws.recv(), self.timeout_s)
        except Exception as exc:   # noqa: BLE001
            entrada["error"] = f"{type(exc).__name__}: {exc}"
            return entrada, None
        entrada["sha256"] = hashlib.sha256(crudo.encode("utf-8")).hexdigest()
        try:
            datos = json.loads(crudo)
        except json.JSONDecodeError as exc:
            entrada["error"] = f"no es JSON: {exc}"
            entrada["extracto"] = crudo[:_EXTRACTO]
            return entrada, None
        if datos.get("error"):
            entrada["error"] = {"code": datos["error"].get("code"),
                                "message": str(datos["error"].get("message"))}
            return entrada, None
        entrada["ok"] = True
        self.ultimo_crudo = crudo
        return entrada, datos


def desalineadas(epochs: list[int], granularidad: int) -> int:
    return sum(1 for e in epochs if e % granularidad != 0)


async def profundidad(canal: Any, simbolo: str, granularidad: int, *,
                      max_paginas: Optional[int] = None,
                      guardar_velas: Optional[dict[int, dict]] = None) -> dict:
    """Pagina hacia atrás hasta vacía, error, o una página que no retrocede
    (la API repitiendo la misma ventana: sin este corte, el bucle no
    termina). No corta por página corta, a diferencia de
    `velas.descargar()`: con 256 velas por página, ese corte habría parado
    en la primera.

    `guardar_velas`, si se pasa, se llena con las velas alineadas, por época.

    `respeta_end` por página: si la última vela cae en o antes del `end`
    pedido. La sonda 2 mostró que en diario no lo respeta. `max_paginas` es
    un tope de seguridad para granularidades finas; tocarlo se informa como
    corte, no como fondo."""
    paginas: list[dict] = []
    fin: Any = "latest"
    inicio: Optional[int] = None
    epochs: set[int] = set()
    corte = ""
    while True:
        if max_paginas is not None and len(paginas) >= max_paginas:
            corte = f"tope de {max_paginas} páginas: la historia puede seguir"
            break
        payload: dict[str, Any] = {"ticks_history": simbolo, "end": str(fin),
                                   "count": DERIV_MAX_COUNT, "style": "candles",
                                   "granularity": granularidad}
        if inicio is not None:
            payload["start"] = inicio
        entrada, datos = await canal.pedir(payload)
        pagina: dict[str, Any] = {"end": str(fin), "start": inicio}
        if datos is None:
            pagina["error"] = entrada.get("error")
            paginas.append(pagina)
            corte = "error"
            break
        velas = datos.get("candles") or []
        pagina.update(n_velas=len(velas), sha256=entrada["sha256"])
        if not velas:
            paginas.append(pagina)
            corte = "respuesta vacía"
            break
        ep = [int(c["epoch"]) for c in velas]
        primera = min(ep)
        pagina.update(primera_epoch=primera, ultima_epoch=max(ep),
                      respeta_end=None if fin == "latest" else max(ep) <= int(fin),
                      desalineadas=desalineadas(ep, granularidad))
        paginas.append(pagina)
        previa = paginas[-2].get("primera_epoch") if len(paginas) > 1 else None
        epochs.update(ep)
        if guardar_velas is not None:
            guardar_velas.update({int(c["epoch"]): c for c in datos["candles"]
                          if int(c["epoch"]) % granularidad == 0})
        if previa is not None and primera >= previa:
            corte = "la página no retrocedió"
            break
        fin = primera - 1
        if fin <= 0:
            corte = "se llegó a la epoch 0"
            break
        inicio = max(0, fin - DERIV_MAX_COUNT * granularidad)
    return {
        "simbolo": simbolo,
        "corte": corte,
        "paginas": paginas,
        "velas_por_pagina": [p.get("n_velas") for p in paginas],
        "total": len(epochs),
        "total_alineadas": len(epochs) - desalineadas(sorted(epochs), granularidad),
        "primera_epoch": min(epochs) if epochs else None,
        "ultima_epoch": max(epochs) if epochs else None,
    }
