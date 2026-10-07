"""
integracion_demo/ejecucion.py
=============================
Ejecución en la cuenta demo de Deriv Multipliers, sin estrategia (brief del
Admin del 06-oct-2026 (3), punto 3c). Recibe una orden ya decidida, la
manda, la sigue y la cierra; registra cada evento en registro.py.

══ LISTA BLANCA PROPIA ══

Solo `proposal`, `buy`, `proposal_open_contract`, `contract_update`,
`sell`, `profit_table`, `statement` y `ping`. Cada mensaje solo puede
llevar las claves que su esquema oficial declara (github.com/deriv-com/
deriv-api-schemas, commit 54e3538), sin `subscribe` ni `passthrough`: un
`buy` escondido como clave extra de una `proposal` también se rechaza.
Todo se valida ANTES de tocar el socket; conexion.py lo vuelve a validar
justo antes de enviar.

══ buy ══

  · Solo con `parameters` (`buy: "1"`): con un id de proposal el stop-loss
    viajaría escondido en la cotización y acá no se podría verificar.
  · `parameters.contract_type` ∈ {MULTUP, MULTDOWN}.
  · `parameters.limit_order.stop_loss` numérico, finito y > 0: sin SL no
    hay orden.
  · Una orden que no pasa la validación NO se envía, y entra al registro
    como `no_enviada` con outcome `no_ejecutada` y el motivo.
  · Si la conexión se pierde después de enviar, el buy NO se reintenta:
    entra como `sin_respuesta` con outcome `desconocido`, y lo resuelve la
    reconciliación.

══ contract_update ══

Sin cierre parcial (decisión 1c). El trailing por contract_update solo
existe como regla de un pre-registro sellado: `actualizar_stop_loss` exige
el sha256 de ese pre-registro y solo acepta un stop-loss MENOR que el
confirmado (moverlo a favor reduce el riesgo, DG-7). Quitar el stop-loss
(`null`) se rechaza siempre.

══ TODA RESPUESTA CRUDA SE GUARDA ANTES DE PARSEARLA ══

conexion.py llama a `Registro.guardar_crudo` con cada texto recibido antes
de mirar su req_id. Las filas referencian ese sha256.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from integracion_demo.registro import (
    KAPPA_MODEL_VERSION,
    Evento,
    Outcome,
    Registro,
    deducir_outcome,
    derivados_de_cierre,
    num,
)

MENSAJES_PERMITIDOS: frozenset[str] = frozenset({
    "proposal", "buy", "proposal_open_contract", "contract_update", "sell",
    "profit_table", "statement", "ping"})

#: Las claves que cada mensaje puede llevar: las de su esquema oficial
#: (54e3538) menos `subscribe` y `passthrough`, y en `proposal` solo las
#: de un multiplicador.
CLAVES_PERMITIDAS: dict[str, frozenset[str]] = {
    "proposal": frozenset({"proposal", "amount", "basis", "contract_type", "currency",
                           "limit_order", "multiplier", "underlying_symbol", "req_id"}),
    "buy": frozenset({"buy", "parameters", "price", "req_id"}),
    "proposal_open_contract": frozenset({"proposal_open_contract", "contract_id", "req_id"}),
    "contract_update": frozenset({"contract_update", "contract_id", "limit_order", "req_id"}),
    "sell": frozenset({"sell", "price", "req_id"}),
    "profit_table": frozenset({"profit_table", "contract_type", "date_from", "date_to",
                               "description", "limit", "offset", "sort", "req_id"}),
    "statement": frozenset({"statement", "action_type", "date_from", "date_to",
                            "description", "limit", "offset", "req_id"}),
    "ping": frozenset({"ping", "req_id"}),
}

#: Las claves de `buy.parameters` que un multiplicador usa.
PARAMETROS_BUY: frozenset[str] = frozenset({
    "amount", "basis", "contract_type", "currency", "limit_order", "multiplier",
    "underlying_symbol"})

CONTRATOS_PERMITIDOS: frozenset[str] = frozenset({"MULTUP", "MULTDOWN"})


class MensajeNoPermitidoError(ValueError):
    """Un mensaje fuera de la lista blanca, o con claves que no van."""


class OrdenSinStopLossError(MensajeNoPermitidoError):
    """Un buy sin limit_order.stop_loss válido."""


class NoConectadoError(RuntimeError):
    """No hay conexión y no se pudo abrir una: el mensaje NO se envió."""


class ConexionPerdidaError(RuntimeError):
    """La conexión se cortó o no respondió después de intentar enviar: no se
    sabe si el mensaje llegó. Un buy en este estado no se reintenta."""


def _positivo(x: Any) -> bool:
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(x) and x > 0)


def _limit_order(lo: Any, donde: str) -> dict:
    if not isinstance(lo, dict) or not set(lo) <= {"stop_loss", "take_profit"}:
        raise MensajeNoPermitidoError(f"{donde}.limit_order: solo stop_loss y take_profit")
    return lo


def validar_mensaje(payload: Any) -> str:
    """El tipo del mensaje si pasa la lista blanca; si no, lanza sin enviar."""
    if not isinstance(payload, dict) or not payload:
        raise MensajeNoPermitidoError("mensaje vacío")
    nombre = next(iter(payload))
    if nombre not in MENSAJES_PERMITIDOS:
        raise MensajeNoPermitidoError(
            f"{nombre!r} no está en la lista blanca de integracion_demo "
            f"({', '.join(sorted(MENSAJES_PERMITIDOS))}). No se envió.")
    extra = set(payload) - CLAVES_PERMITIDAS[nombre]
    if extra:
        raise MensajeNoPermitidoError(f"{nombre} con claves fuera de su esquema: {sorted(extra)}")
    if nombre == "proposal":
        if payload.get("contract_type") not in CONTRATOS_PERMITIDOS:
            raise MensajeNoPermitidoError("proposal: solo MULTUP o MULTDOWN")
        if "limit_order" in payload:
            _limit_order(payload["limit_order"], "proposal")
    elif nombre == "buy":
        if payload["buy"] != "1" or not isinstance(payload.get("parameters"), dict):
            raise MensajeNoPermitidoError(
                'buy: solo con `parameters` y buy="1"; con un id de proposal el '
                'stop-loss no se puede verificar acá')
        p = payload["parameters"]
        if not set(p) <= PARAMETROS_BUY:
            raise MensajeNoPermitidoError(
                f"buy.parameters con claves que no van: {sorted(set(p) - PARAMETROS_BUY)}")
        if p.get("contract_type") not in CONTRATOS_PERMITIDOS:
            raise MensajeNoPermitidoError("buy: solo MULTUP o MULTDOWN")
        lo = p.get("limit_order")
        if not isinstance(lo, dict) or not _positivo(lo.get("stop_loss")):
            raise OrdenSinStopLossError("buy sin limit_order.stop_loss > 0: sin SL no hay orden")
        _limit_order(lo, "buy.parameters")
        if "take_profit" in lo and not _positivo(lo["take_profit"]):
            raise MensajeNoPermitidoError("buy: take_profit tiene que ser > 0")
        if not _positivo(p.get("amount")) or not _positivo(p.get("multiplier")):
            raise MensajeNoPermitidoError("buy: amount y multiplier > 0")
        if not _positivo(payload.get("price")):
            raise MensajeNoPermitidoError("buy: price > 0")
    elif nombre == "contract_update":
        lo = _limit_order(payload.get("limit_order"), "contract_update")
        if not _positivo(lo.get("stop_loss")):
            raise OrdenSinStopLossError(
                "contract_update sin stop_loss > 0: quitar el SL aumenta el riesgo")
        if "take_profit" in lo and lo["take_profit"] is not None and not _positivo(lo["take_profit"]):
            raise MensajeNoPermitidoError("contract_update: take_profit > 0 o null")
        if not _positivo(payload.get("contract_id")):
            raise MensajeNoPermitidoError("contract_update: contract_id")
    elif nombre == "sell":
        p = payload.get("price")
        if not _positivo(payload["sell"]) or not isinstance(payload["sell"], int):
            raise MensajeNoPermitidoError("sell: el contract_id, entero")
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or p < 0:
            raise MensajeNoPermitidoError("sell: price ≥ 0 (0 es a mercado)")
    elif nombre == "proposal_open_contract":
        if payload["proposal_open_contract"] != 1 or not _positivo(payload.get("contract_id")):
            raise MensajeNoPermitidoError("proposal_open_contract: 1 y un contract_id")
    elif payload[nombre] != 1:
        raise MensajeNoPermitidoError(f"{nombre}: el valor es 1")
    return nombre


# ═══ La orden y el contexto ═══════════════════════════════════════════════

@dataclass(frozen=True)
class Contexto:
    """Lo que va en cada fila y no cambia en la corrida."""
    experiment_id: str
    account_id: str          # ya enmascarado
    account_type: str
    ws_path: str
    currency: str
    git_commit_sha: Optional[str] = None
    preregistro_sha256: Optional[str] = None

    def __post_init__(self) -> None:
        if self.account_type != "demo":
            raise ValueError(f"account_type {self.account_type!r}: integracion_demo "
                             f"solo opera la cuenta demo")
        if not self.ws_path.endswith("/ws/demo"):
            raise ValueError(f"ws_path {self.ws_path!r}: solo /ws/demo")


@dataclass(frozen=True)
class Orden:
    underlying_symbol: str
    contract_type: str
    stake: float
    multiplier: float
    stop_loss: Optional[float]
    take_profit: Optional[float] = None


@dataclass
class Trade:
    trade_uuid: str
    orden: Orden
    quote_at_signal: Optional[float]
    t_senal_ms: Optional[int]
    contract_id: Optional[int] = None
    buy_transaction_id: Optional[int] = None
    t_envio_ms: Optional[int] = None
    t_ack_ms: Optional[int] = None
    estado: str = "nuevo"     # nuevo, abierto, cerrado, no_enviada, rechazada, desconocido
    venta_propia: Optional[dict] = None
    quote_at_exit_signal: Optional[float] = None
    ultimo_poc: Optional[dict] = None
    pocs: list[str] = field(default_factory=list)


def _reloj_ms() -> int:
    return int(time.time() * 1000)


class Ejecutor:
    """Una conexión (conexion.ConexionDemo o algo con `pedir(payload) ->
    (crudo, sha256)` y las excepciones NoConectadoError y
    ConexionPerdidaError), un Registro y un Contexto."""

    def __init__(self, conexion: Any, registro: Registro, contexto: Contexto, *,
                 reloj_ms: Callable[[], int] = _reloj_ms) -> None:
        self.conexion = conexion
        self.registro = registro
        self.contexto = contexto
        self.reloj_ms = reloj_ms

    # ── pedido genérico ──────────────────────────────────────────────────
    async def pedir(self, payload: dict) -> tuple[dict, str]:
        """Valida, envía y parsea. Lo crudo ya quedó guardado por la
        conexión. Lanza MensajeNoPermitidoError sin enviar."""
        validar_mensaje(payload)
        crudo, sha = await self.conexion.pedir(payload)
        return json.loads(crudo), sha

    def _fila(self, trade: Trade, evento: Evento, **campos: Any) -> dict:
        o, c = trade.orden, self.contexto
        base = dict(
            trade_uuid=trade.trade_uuid, contract_id=trade.contract_id,
            buy_transaction_id=trade.buy_transaction_id,
            experiment_id=c.experiment_id, preregistro_sha256=c.preregistro_sha256,
            git_commit_sha=c.git_commit_sha, kappa_model_version=KAPPA_MODEL_VERSION,
            account_id=c.account_id, account_type=c.account_type, ws_path=c.ws_path,
            underlying_symbol=o.underlying_symbol, contract_type=o.contract_type,
            stake=o.stake, multiplier=o.multiplier, nocional=o.stake * o.multiplier,
            currency=c.currency, sl_solicitado=o.stop_loss,
            quote_at_signal=trade.quote_at_signal, t_senal_ms=trade.t_senal_ms,
            t_envio_ms=trade.t_envio_ms, t_ack_ms=trade.t_ack_ms,
            latencia_ms=(trade.t_ack_ms - trade.t_envio_ms
                         if trade.t_ack_ms is not None and trade.t_envio_ms is not None
                         else None))
        base.update(campos)
        return self.registro.agregar(evento=evento.value, **base)

    # ── cotizar ──────────────────────────────────────────────────────────
    async def cotizar(self, orden: Orden) -> tuple[dict, str]:
        payload: dict[str, Any] = {
            "proposal": 1, "contract_type": orden.contract_type, "basis": "stake",
            "amount": orden.stake, "currency": self.contexto.currency,
            "underlying_symbol": orden.underlying_symbol, "multiplier": orden.multiplier}
        if orden.stop_loss is not None:
            payload["limit_order"] = {"stop_loss": orden.stop_loss}
        return await self.pedir(payload)

    # ── comprar ──────────────────────────────────────────────────────────
    def payload_de_compra(self, orden: Orden) -> dict:
        lo: dict[str, Any] = {"stop_loss": orden.stop_loss}
        if orden.take_profit is not None:
            lo["take_profit"] = orden.take_profit
        return {"buy": "1", "price": orden.stake, "parameters": {
            "contract_type": orden.contract_type, "basis": "stake", "amount": orden.stake,
            "currency": self.contexto.currency, "underlying_symbol": orden.underlying_symbol,
            "multiplier": orden.multiplier, "limit_order": lo}}

    async def comprar(self, orden: Orden, *, quote_at_signal: Optional[float] = None,
                      t_senal_ms: Optional[int] = None) -> Trade:
        """Manda UNA orden. Nunca lanza por lo que conteste Deriv ni por la
        red: el resultado queda en `trade.estado` y en el registro."""
        trade = Trade(trade_uuid=str(uuid.uuid4()), orden=orden,
                      quote_at_signal=quote_at_signal, t_senal_ms=t_senal_ms)
        payload = self.payload_de_compra(orden)
        try:
            validar_mensaje(payload)
        except MensajeNoPermitidoError as exc:
            trade.estado = "no_enviada"
            self._fila(trade, Evento.NO_ENVIADA, outcome=Outcome.NO_EJECUTADA.value,
                       evidencia=f"{type(exc).__name__}: {exc}")
            return trade
        self._fila(trade, Evento.ENVIO)
        trade.t_envio_ms = self.reloj_ms()
        try:
            crudo, sha = await self.conexion.pedir(payload)
        except NoConectadoError as exc:
            trade.estado = "no_enviada"
            self._fila(trade, Evento.NO_ENVIADA, outcome=Outcome.NO_EJECUTADA.value,
                       evidencia=f"sin conexión, no se envió: {exc}")
            return trade
        except ConexionPerdidaError as exc:
            trade.estado = "desconocido"
            self._fila(trade, Evento.SIN_RESPUESTA, outcome=Outcome.DESCONOCIDO.value,
                       evidencia=f"enviado sin respuesta, no se reintenta: {exc}")
            return trade
        trade.t_ack_ms = self.reloj_ms()
        try:
            datos = json.loads(crudo)
        except json.JSONDecodeError:
            trade.estado = "desconocido"
            self._fila(trade, Evento.SIN_RESPUESTA, outcome=Outcome.DESCONOCIDO.value,
                       evidencia="la respuesta del buy no es JSON", raw_sha256=sha)
            return trade
        if datos.get("error") or not isinstance(datos.get("buy"), dict):
            trade.estado = "rechazada"
            e = datos.get("error") or {}
            self._fila(trade, Evento.RECHAZO, outcome=Outcome.RECHAZADA.value,
                       evidencia=f"{e.get('code')}: {e.get('message')}", raw_sha256=sha)
            return trade
        b = datos["buy"]
        trade.contract_id, trade.buy_transaction_id = b.get("contract_id"), b.get("transaction_id")
        trade.estado = "abierto"
        self._fila(trade, Evento.COMPRA, purchase_time=b.get("purchase_time"), raw_sha256=sha)
        return trade

    # ── seguir y cerrar ──────────────────────────────────────────────────
    async def consultar(self, trade: Trade) -> Optional[dict]:
        """Un proposal_open_contract. None si no hubo respuesta usable."""
        try:
            datos, sha = await self.pedir({"proposal_open_contract": 1,
                                           "contract_id": trade.contract_id})
        except (ConexionPerdidaError, NoConectadoError, json.JSONDecodeError):
            return None
        poc = datos.get("proposal_open_contract")
        if datos.get("error") or not isinstance(poc, dict):
            return None
        if trade.ultimo_poc is None:
            lo = (poc.get("limit_order") or {}).get("stop_loss") or {}
            self._fila(trade, Evento.SEGUIMIENTO, raw_sha256=sha,
                       sl_confirmado=num(lo.get("order_amount")),
                       sl_precio=num(lo.get("value")), entry_spot=num(poc.get("entry_spot")),
                       purchase_time=poc.get("purchase_time"))
        trade.ultimo_poc = poc
        trade.pocs.append(sha)
        return poc

    async def vender(self, trade: Trade, *, motivo: str) -> bool:
        """sell a mercado. True si Deriv confirmó NUESTRA venta."""
        if motivo not in ("tiempo", "manual"):
            raise ValueError("motivo: tiempo o manual")
        trade.quote_at_exit_signal = num((trade.ultimo_poc or {}).get("current_spot"))
        try:
            datos, sha = await self.pedir({"sell": trade.contract_id, "price": 0})
        except (ConexionPerdidaError, NoConectadoError, json.JSONDecodeError) as exc:
            self._fila(trade, Evento.VENTA, evidencia=f"sell sin respuesta: {exc}")
            return False
        s = datos.get("sell")
        if datos.get("error") or not isinstance(s, dict):
            e = datos.get("error") or {}
            self._fila(trade, Evento.VENTA, raw_sha256=sha,
                       evidencia=f"sell rechazado: {e.get('code')}: {e.get('message')}")
            return False
        trade.venta_propia = {"motivo": motivo, "transaction_id": s.get("transaction_id"),
                              "raw_sha256": sha}
        self._fila(trade, Evento.VENTA, raw_sha256=sha, sell_transaction_id=s.get("transaction_id"),
                   evidencia=f"sell propio confirmado ({motivo})")
        return True

    def cerrar(self, trade: Trade, *, evidencia_extra: Optional[str] = None) -> dict:
        """La fila de cierre con lo último que se vio del contrato."""
        poc = trade.ultimo_poc or {"contract_type": trade.orden.contract_type}
        outcome, evidencia = deducir_outcome(poc, venta_propia=trade.venta_propia)
        if outcome is None:
            outcome, evidencia = Outcome.DESCONOCIDO.value, "no se observó el cierre del contrato"
        if evidencia_extra:
            evidencia = f"{evidencia}; {evidencia_extra}"
        d = derivados_de_cierre(poc, stake=trade.orden.stake, multiplier=trade.orden.multiplier,
                                quote_at_signal=trade.quote_at_signal,
                                quote_at_exit_signal=trade.quote_at_exit_signal,
                                outcome=outcome)
        trade.estado = "cerrado"
        return self._fila(trade, Evento.CIERRE, outcome=outcome, evidencia=evidencia,
                          raw_sha256=trade.pocs[-1] if trade.pocs else None, **d)

    async def seguir_hasta_cierre(self, trade: Trade, *, vender_a_los_s: float,
                                  cada_s: float, esperar_cierre_s: float,
                                  dormir: Callable[[float], Any] = asyncio.sleep,
                                  reloj_s: Callable[[], float] = time.monotonic) -> dict:
        """Consulta cada `cada_s`; si a los `vender_a_los_s` sigue abierto,
        vende (motivo tiempo) y espera el cierre hasta `esperar_cierre_s`
        más. Devuelve la fila de cierre."""
        if trade.estado != "abierto":
            raise ValueError(f"trade en estado {trade.estado!r}: no hay contrato que seguir")
        t0 = reloj_s()
        vendido = False
        while True:
            poc = await self.consultar(trade)
            if poc is not None and (poc.get("is_sold") or poc.get("status") in
                                    ("sold", "won", "lost", "cancelled")):
                return self.cerrar(trade)
            transcurrido = reloj_s() - t0
            if not vendido and transcurrido >= vender_a_los_s:
                vendido = True
                await self.vender(trade, motivo="tiempo")
                continue
            if vendido and transcurrido >= vender_a_los_s + esperar_cierre_s:
                return self.cerrar(trade, evidencia_extra=(
                    f"sin cierre observado {esperar_cierre_s:g} s después del sell"))
            await dormir(cada_s)

    # ── stop-loss a favor ────────────────────────────────────────────────
    async def actualizar_stop_loss(self, trade: Trade, nuevo: float, *,
                                   preregistro_sha256: str) -> bool:
        """Solo como regla de un pre-registro sellado, y solo a favor."""
        if not re.fullmatch(r"[0-9a-f]{64}", preregistro_sha256 or ""):
            raise MensajeNoPermitidoError(
                "contract_update solo como regla de un pre-registro sellado: falta su sha256")
        lo = ((trade.ultimo_poc or {}).get("limit_order") or {}).get("stop_loss") or {}
        actual = num(lo.get("order_amount"))
        if actual is None or not _positivo(nuevo) or nuevo >= abs(actual):
            raise MensajeNoPermitidoError(
                f"stop_loss {nuevo} no reduce el riesgo frente al confirmado {actual}")
        datos, sha = await self.pedir({"contract_update": 1, "contract_id": trade.contract_id,
                                       "limit_order": {"stop_loss": nuevo}})
        ok = not datos.get("error") and isinstance(datos.get("contract_update"), dict)
        sl = ((datos.get("contract_update") or {}).get("stop_loss") or {})
        self._fila(trade, Evento.ACTUALIZACION, raw_sha256=sha,
                   sl_confirmado=num(sl.get("order_amount")) if ok else None,
                   evidencia=("contract_update confirmado" if ok else
                              f"contract_update rechazado: {datos.get('error')}"))
        return ok

    # ── lectura para reconciliar ─────────────────────────────────────────
    async def profit_table(self, **filtros: Any) -> tuple[list[dict], str]:
        datos, sha = await self.pedir({"profit_table": 1, "description": 1, **filtros})
        return list((datos.get("profit_table") or {}).get("transactions") or []), sha

    async def statement(self, **filtros: Any) -> tuple[list[dict], str]:
        datos, sha = await self.pedir({"statement": 1, "description": 1, **filtros})
        return list((datos.get("statement") or {}).get("transactions") or []), sha
