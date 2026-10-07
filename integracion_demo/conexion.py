"""
integracion_demo/conexion.py
============================
Una conexión persistente a /ws/demo (brief del Admin del 06-oct-2026 (3),
punto 3b).

  · UN OTP NUEVO POR CONEXIÓN. Cada vez que hay que abrir el socket —la
    primera, después de un corte, o en cada intento de reconexión— se llama
    a `url_nueva`, que emite un OTP (otp.url_demo_nueva). Un OTP de Deriv
    sirve una sola vez y 120 s; nunca se guarda una URL para reusarla.
  · SOLO /ws/demo. La URL se vuelve a comprobar con
    otp.motivo_para_no_conectar justo antes de abrir: si no es la de demo,
    se detiene sin reintentar.
  · RECONEXIÓN CON BACKOFF. Si abrir falla, se espera ESPERAS_S[i] y se
    pide otro OTP. Agotados los intentos: NoConectadoError (nada se envió).
  · UN PEDIDO A LA VEZ. Un lock serializa envío y respuesta; la respuesta
    es la del mismo req_id. Lo que llegue con otro req_id se guarda crudo y
    se cuenta en `no_pedidos`, no se parsea como respuesta.
  · NINGÚN MENSAJE SE REINTENTA. Si el socket se corta o no responde
    después de enviar, ConexionPerdidaError y el socket se descarta; el
    próximo pedido reconecta. Quien llama decide (un buy no se reintenta).
  · TODA RESPUESTA CRUDA SE GUARDA ANTES DE PARSEARLA: `al_recibir(texto,
    t_local_ms)` corre con cada texto recibido, antes de json.loads.
  · Cada mensaje pasa por ejecucion.validar_mensaje antes del socket.

══ EL PING ══

Deriv no documenta cuánto tarda en cerrar un socket inactivo, así que el
intervalo del `ping` de aplicación es un parámetro (`intervalo_ping_s`,
None = sin ping) y se mide en la parte B (`medir_cierre_inactivo`). Por la
misma razón el socket se abre con `ping_interval=None` de la librería
websockets: con su ping de protocolo cada 20 s, el cierre por inactividad
no se vería nunca.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Optional

from ingestion.deriv_ws import TIMEOUT_RESPUESTA_S
from integracion_demo.ejecucion import (
    ConexionPerdidaError,
    NoConectadoError,
    validar_mensaje,
)
from integracion_demo.otp import (
    CuentaNoDemoError,
    UrlNoDemoError,
    motivo_para_no_conectar,
)

#: [INTERPRETACIÓN] Esperas entre intentos de conexión, en segundos: 5
#: intentos más el primero, 31 s en total. Deriv no publica un límite de
#: OTPs por minuto.
ESPERAS_S: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)


def abrir_websocket(url: str) -> Any:
    import websockets
    return websockets.connect(url, ping_interval=None, open_timeout=TIMEOUT_RESPUESTA_S)


def _reloj_ms() -> int:
    return int(time.time() * 1000)


class ConexionDemo:
    """Usar con `async with ConexionDemo(...) as c:`."""

    def __init__(self, url_nueva: Callable[[], Awaitable[str]], *,
                 al_recibir: Callable[[str, int], str],
                 abrir: Callable[[str], Any] = abrir_websocket,
                 intervalo_ping_s: Optional[float] = None,
                 timeout_s: float = TIMEOUT_RESPUESTA_S,
                 esperas_s: tuple[float, ...] = ESPERAS_S,
                 dormir: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                 reloj_ms: Callable[[], int] = _reloj_ms) -> None:
        self._url_nueva = url_nueva
        self._al_recibir = al_recibir
        self._abrir = abrir
        self.intervalo_ping_s = intervalo_ping_s
        self._timeout_s = timeout_s
        self._esperas_s = esperas_s
        self._dormir = dormir
        self._reloj_ms = reloj_ms
        self._lock = asyncio.Lock()
        self._cm: Any = None
        self._ws: Any = None
        self._req_id = 0
        self._latido: Optional[asyncio.Task] = None
        #: Se prende al cerrar: el ping sale de su bucle aunque la
        #: cancelación se pierda (ver `__aexit__`).
        self._cerrando = False
        #: Cuántos OTP se pidieron y cuántas conexiones se abrieron.
        self.n_otps = 0
        self.n_conexiones = 0
        #: Lo que pasó con el socket, para el informe (sin URLs).
        self.historial: list[dict] = []
        #: sha256 de los mensajes recibidos que no respondían a un pedido.
        self.no_pedidos: list[str] = []

    # ── abrir y cerrar ───────────────────────────────────────────────────
    async def __aenter__(self) -> "ConexionDemo":
        await self._conectar()
        if self.intervalo_ping_s:
            self._latido = asyncio.create_task(self._latir())
        return self

    async def __aexit__(self, *exc: Any) -> None:
        # En Python 3.11, `asyncio.wait_for` se traga una cancelación que
        # llega cuando la respuesta ya está (corregido en 3.12): el ping
        # puede no enterarse del `cancel()`. Por eso, además, la bandera.
        # Y se espera con `asyncio.wait`, que no lanza lo de la tarea ni se
        # traga la cancelación de quien cierra.
        self._cerrando = True
        if self._latido is not None:
            self._latido.cancel()
            await asyncio.wait({self._latido})
        await self._descartar("cierre pedido")

    @property
    def conectada(self) -> bool:
        return self._ws is not None

    async def _conectar(self) -> None:
        ultimo: Optional[BaseException] = None
        for intento, espera in enumerate((0.0, *self._esperas_s)):
            if espera:
                await self._dormir(espera)
            try:
                self.n_otps += 1
                url = await self._url_nueva()
                motivo = motivo_para_no_conectar(url)
                if motivo:
                    raise UrlNoDemoError(motivo)
                cm = self._abrir(url)
                ws = await cm.__aenter__()
            except (CuentaNoDemoError, UrlNoDemoError):
                self.historial.append({"evento": "detenida", "intento": intento + 1})
                raise
            except Exception as exc:   # noqa: BLE001 -- se reintenta con otro OTP
                ultimo = exc
                self.historial.append({"evento": "fallo_al_conectar", "intento": intento + 1,
                                       "error": type(exc).__name__})
                continue
            self._cm, self._ws = cm, ws
            self.n_conexiones += 1
            self.historial.append({"evento": "conectada", "intento": intento + 1,
                                   "t_ms": self._reloj_ms()})
            return
        raise NoConectadoError(f"{len(self._esperas_s) + 1} intentos sin conexión "
                               f"(último: {type(ultimo).__name__})")

    async def _descartar(self, motivo: str) -> None:
        cm, self._cm, self._ws = self._cm, None, None
        if cm is None:
            return
        self.historial.append({"evento": "descartada", "motivo": motivo, "t_ms": self._reloj_ms()})
        try:
            await cm.__aexit__(None, None, None)
        except Exception:   # noqa: BLE001 -- ya estaba cerrándose
            pass

    # ── pedir ────────────────────────────────────────────────────────────
    async def pedir(self, payload: dict) -> tuple[str, str]:
        """(texto crudo, sha256) de la respuesta con el mismo req_id."""
        validar_mensaje(payload)
        async with self._lock:
            if self._ws is None:
                await self._conectar()
            self._req_id += 1
            req_id = self._req_id
            texto = json.dumps({**payload, "req_id": req_id})
            limite = time.monotonic() + self._timeout_s
            try:
                await self._ws.send(texto)
                while True:
                    restante = limite - time.monotonic()
                    if restante <= 0:
                        raise asyncio.TimeoutError()
                    crudo = await asyncio.wait_for(self._ws.recv(), restante)
                    sha = self._al_recibir(crudo, self._reloj_ms())
                    try:
                        datos = json.loads(crudo)
                    except json.JSONDecodeError:
                        self.no_pedidos.append(sha)
                        continue
                    if isinstance(datos, dict) and datos.get("req_id") == req_id:
                        return crudo, sha
                    self.no_pedidos.append(sha)
            except Exception as exc:   # noqa: BLE001
                await self._descartar(f"{type(exc).__name__} esperando req_id {req_id}")
                raise ConexionPerdidaError(
                    f"{next(iter(payload))} (req_id {req_id}): {type(exc).__name__}") from exc

    async def _latir(self) -> None:
        while not self._cerrando:
            await asyncio.sleep(self.intervalo_ping_s)
            if self._lock.locked():
                continue
            try:
                await self.pedir({"ping": 1})
            except (ConexionPerdidaError, NoConectadoError):
                pass

    # ── medir ────────────────────────────────────────────────────────────
    async def medir_cierre_inactivo(self, maximo_s: float) -> Optional[float]:
        """Deja el socket sin enviar nada hasta `maximo_s` y devuelve en
        cuántos segundos lo cerró el servidor, o None si no lo cerró. Lo
        que llegue mientras tanto se guarda crudo."""
        async with self._lock:
            if self._ws is None:
                raise NoConectadoError("no hay socket que medir")
            t0 = time.monotonic()
            while True:
                restante = maximo_s - (time.monotonic() - t0)
                if restante <= 0:
                    return None
                try:
                    crudo = await asyncio.wait_for(self._ws.recv(), restante)
                except asyncio.TimeoutError:
                    return None
                except Exception:   # noqa: BLE001 -- el servidor cerró
                    transcurrido = time.monotonic() - t0
                    await self._descartar("cierre por inactividad")
                    return transcurrido
                self.no_pedidos.append(self._al_recibir(crudo, self._reloj_ms()))
