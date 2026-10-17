"""
integracion_demo/reconciliar.py
===============================
Cruza el registro con `profit_table` y `statement` de Deriv (brief del
Admin del 06-oct-2026 (3), punto 3e) y la comisión cotizada con la del
contrato (DB-1 y DB-2, brief 9-R2). Agrega una fila nueva de evento
`reconciliacion` por trade terminado: nunca edita una fila (el patrón de
core/trade_ledger.py:14-47).

══ LAS TRES MARCAS DE LA FILA (DB-2) ══

  · reconciliado_profit ∈ {ok, discrepancia}: lo de abajo, "QUÉ SE
    COMPARA", con su tolerancia.
  · reconciliado_comision:
      ok              la cotizada (fila de `envío`) y `commission_cruda`
                      (fila de cierre) difieren ≤ TOLERANCIA_USD, en Decimal.
      discrepancia    difieren más.
      no_verificable  falta alguna de las dos, o no es un número. Un
                      registro de SCHEMA_VERSION 1 no tiene la cotizada: no
                      se rellena, queda no_verificable.
      no_aplica       no hubo contrato: orden sin contract_id y sin compra
                      cerca en `statement`.
  · reconciliado (global): ok solo si profit = ok y comisión ∈ {ok,
    no_aplica}; discrepancia si cualquiera es discrepancia; no_verificable
    en cualquier otro caso. no_verificable NUNCA es ok.

La comisión del contrato llega como string ("0.02") y la cotizada como
float (0.02, guardada como "0.02"): las dos se pasan a Decimal desde su
texto antes de restar, para que la frontera de medio centavo sea exacta.

══ QUÉ SE COMPARA ══

Con contract_id (el contrato existió):
  · profit_table tiene ese contract_id; su buy_price es el stake; su
    sell_price − buy_price es el profit_deriv del registro.
  · statement tiene un `buy` con ese contract_id, de monto −stake y con el
    buy_transaction_id del registro; y un `sell` con el sell_transaction_id
    del registro, de monto igual al sell_price de profit_table.
Sin contract_id (rechazada, no enviada, o enviada sin respuesta):
  · Ninguna compra del statement cae dentro de VENTANA_S alrededor del
    envío. Si cae alguna, es discrepancia y la evidencia trae su contract_id:
    una orden que el registro cree perdida pudo haberse ejecutado.

[INTERPRETACIÓN] Lo que el brief no fija:
  · Montos iguales = a menos de TOLERANCIA_USD (medio centavo): Deriv
    devuelve centavos y la resta de dos floats no es exacta.
  · La comisión no aparece en profit_table ni en statement (esquemas
    oficiales, commit 54e3538): se contrasta contra la cotizada (DB-1).
  · Una orden sin contract_id CON una compra cerca en statement: el
    contrato pudo existir y su comisión no se ve, así que la comisión es
    no_verificable (el global ya es discrepancia por el profit).
  · El reloj del envío es local (ms) y el de statement es del servidor (s):
    por eso la ventana es ancha.
  · Un trade ya marcado `ok` no se vuelve a reconciliar; uno en
    `discrepancia` o `no_verificable` sí, y suma otra fila. Un `ok` escrito
    antes de DB-2 (sin `reconciliado_comision`) no cuenta como ok: se
    reconcilia de nuevo, en una fila nueva.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from integracion_demo.registro import (
    EVENTOS_TERMINALES,
    Evento,
    Reconciliado,
    ReconciliadoComision,
    Registro,
    leer_registro,
    num,
)

TOLERANCIA_USD = 0.005
VENTANA_S = 120


def _igual(a: Optional[float], b: Optional[float]) -> bool:
    return a is not None and b is not None and abs(a - b) <= TOLERANCIA_USD


def _con_contrato(fila: dict, pt: list[dict], st: list[dict]) -> list[str]:
    """Las discrepancias de un trade con contract_id; vacía si cuadra."""
    cid = fila["contract_id"]
    stake = num(fila.get("stake"))
    out: list[str] = []
    en_pt = [t for t in pt if t.get("contract_id") == cid]
    if len(en_pt) != 1:
        out.append(f"profit_table: {len(en_pt)} filas con contract_id {cid}")
        sell_price = None
    else:
        t = en_pt[0]
        compra, sell_price = num(t.get("buy_price")), num(t.get("sell_price"))
        if not _igual(compra, stake):
            out.append(f"profit_table.buy_price {compra} ≠ stake {stake}")
        profit = num(fila.get("profit_deriv"))
        if compra is None or sell_price is None or not _igual(sell_price - compra, profit):
            out.append(f"profit_table sell_price − buy_price ({sell_price} − {compra}) "
                       f"≠ profit_deriv {profit}")
    compras = [t for t in st if t.get("action_type") == "buy" and t.get("contract_id") == cid]
    if len(compras) != 1:
        out.append(f"statement: {len(compras)} compras con contract_id {cid}")
    else:
        c = compras[0]
        if c.get("transaction_id") != fila.get("buy_transaction_id"):
            out.append(f"statement buy transaction_id {c.get('transaction_id')} ≠ "
                       f"buy_transaction_id {fila.get('buy_transaction_id')}")
        if not _igual(num(c.get("amount")), -stake if stake is not None else None):
            out.append(f"statement buy amount {c.get('amount')} ≠ −stake {stake}")
    ventas = [t for t in st if t.get("action_type") == "sell" and t.get("contract_id") == cid]
    if len(ventas) != 1:
        out.append(f"statement: {len(ventas)} ventas con contract_id {cid}")
    else:
        v = ventas[0]
        if v.get("transaction_id") != fila.get("sell_transaction_id"):
            out.append(f"statement sell transaction_id {v.get('transaction_id')} ≠ "
                       f"sell_transaction_id {fila.get('sell_transaction_id')}")
        if not _igual(num(v.get("amount")), sell_price):
            out.append(f"statement sell amount {v.get('amount')} ≠ "
                       f"profit_table.sell_price {sell_price}")
    return out


def _decimal(v: Any) -> Optional[Decimal]:
    """Decimal desde el texto del valor; None si falta o no es un número
    finito."""
    if v is None or isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v).strip())
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def marca_comision(envio: Optional[dict], terminal: dict,
                   problemas_sin_contrato: list[str]) -> tuple[str, dict]:
    """reconciliado_comision de un trade, y lo comparado (DB-2)."""
    if terminal.get("contract_id") is None:
        if problemas_sin_contrato:
            return ReconciliadoComision.NO_VERIFICABLE.value, {
                "motivo": "sin contract_id pero con una compra cerca: su comisión no se ve"}
        return ReconciliadoComision.NO_APLICA.value, {"motivo": "no hubo contrato"}
    cotizada = (envio or {}).get("commission_cotizada")
    contrato = terminal.get("commission_cruda")
    detalle: dict[str, Any] = {"cotizada": cotizada, "contrato": contrato}
    a, b = _decimal(cotizada), _decimal(contrato)
    if a is None or b is None:
        return ReconciliadoComision.NO_VERIFICABLE.value, detalle
    diferencia = abs(a - b)
    detalle["diferencia"] = str(diferencia)
    if diferencia <= Decimal(str(TOLERANCIA_USD)):
        return ReconciliadoComision.OK.value, detalle
    return ReconciliadoComision.DISCREPANCIA.value, detalle


def marca_global(profit: str, comision: str) -> str:
    if Reconciliado.DISCREPANCIA.value in (profit, comision):
        return Reconciliado.DISCREPANCIA.value
    if profit == Reconciliado.OK.value and comision in (ReconciliadoComision.OK.value,
                                                        ReconciliadoComision.NO_APLICA.value):
        return Reconciliado.OK.value
    return Reconciliado.NO_VERIFICABLE.value


def _sin_contrato(fila: dict, st: list[dict]) -> list[str]:
    """Una orden sin contract_id no puede tener una compra cerca de su envío."""
    t_envio = num(fila.get("t_envio_ms"))
    if t_envio is None:
        # No salió nunca: lo único que puede contradecirla es una compra sin
        # registro, y sin hora de envío no hay ventana donde buscarla.
        return []
    centro = t_envio / 1000
    cerca = [t for t in st if t.get("action_type") == "buy"
             and num(t.get("transaction_time")) is not None
             and abs(num(t.get("transaction_time")) - centro) <= VENTANA_S]
    return [f"statement: compra contract_id {t.get('contract_id')} a "
            f"{num(t.get('transaction_time')) - centro:+.0f} s del envío" for t in cerca]


def reconciliar(registro: Registro, *, profit_table: list[dict], statement: list[dict],
                raw_profit_table: Optional[str] = None,
                raw_statement: Optional[str] = None) -> list[dict]:
    """Agrega una fila `reconciliacion` por cada trade terminado que no esté
    ya en `ok`. Devuelve las filas agregadas."""
    filas = leer_registro(registro.directorio).filas
    ultima_terminal: dict[str, dict] = {}
    envio: dict[str, dict] = {}
    ok_previo: set[str] = set()
    terminales = {e.value for e in EVENTOS_TERMINALES}
    for f in filas:
        if f["evento"] in terminales:
            ultima_terminal[f["trade_uuid"]] = f
        elif f["evento"] == Evento.ENVIO.value:
            envio.setdefault(f["trade_uuid"], f)
        elif f["evento"] == Evento.RECONCILIACION.value:
            # Un ok anterior a DB-2 no traía la marca de la comisión: no cuenta.
            if (f["reconciliado"] == Reconciliado.OK.value
                    and f.get("reconciliado_comision") is not None):
                ok_previo.add(f["trade_uuid"])
            else:
                ok_previo.discard(f["trade_uuid"])
    nuevas = []
    for uuid, f in ultima_terminal.items():
        if uuid in ok_previo:
            continue
        if f.get("contract_id") is not None:
            problemas = _con_contrato(f, profit_table, statement)
            sin_contrato: list[str] = []
        else:
            problemas = sin_contrato = _sin_contrato(f, statement)
        profit = (Reconciliado.DISCREPANCIA if problemas else Reconciliado.OK).value
        comision, detalle = marca_comision(envio.get(uuid), f, sin_contrato)
        evidencia: dict[str, Any] = {
            "problemas": problemas, "fila_terminal": f["seq"],
            "fila_envio": (envio.get(uuid) or {}).get("seq"), "comision": detalle,
            "profit_table_sha256": raw_profit_table, "statement_sha256": raw_statement}
        nuevas.append(registro.agregar(
            evento=Evento.RECONCILIACION.value, trade_uuid=uuid,
            contract_id=f.get("contract_id"), experiment_id=f.get("experiment_id"),
            account_id=f.get("account_id"), account_type=f.get("account_type"),
            ws_path=f.get("ws_path"), raw_sha256=raw_profit_table,
            reconciliado=marca_global(profit, comision), reconciliado_profit=profit,
            reconciliado_comision=comision,
            evidencia=json.dumps(evidencia, ensure_ascii=False, sort_keys=True)))
    return nuevas


def conteo_de_marcas(directorio: Any) -> dict[str, int]:
    """Por trade terminado, la marca global `reconciliado` de su ÚLTIMA fila
    de reconciliación, contada: ok, discrepancia, no_verificable, y
    sin_reconciliar si todavía no tiene ninguna. De acá salen los dos
    conteos de la condición d) de DG-3 (governance/paso_a_real.py, D2 y
    DB-1). Un trade abierto no cuenta."""
    filas = leer_registro(directorio).filas
    terminales = {e.value for e in EVENTOS_TERMINALES}
    terminados: set[str] = set()
    ultima: dict[str, str] = {}
    for f in filas:
        if f["evento"] in terminales:
            terminados.add(f["trade_uuid"])
        elif f["evento"] == Evento.RECONCILIACION.value:
            ultima[f["trade_uuid"]] = f["reconciliado"]
    conteo = {Reconciliado.OK.value: 0, Reconciliado.DISCREPANCIA.value: 0,
              Reconciliado.NO_VERIFICABLE.value: 0, "sin_reconciliar": 0}
    for uuid in terminados:
        marca = ultima.get(uuid, "sin_reconciliar")
        conteo[marca if marca in conteo else "sin_reconciliar"] += 1
    return conteo
