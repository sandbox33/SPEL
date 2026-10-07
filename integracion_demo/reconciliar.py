"""
integracion_demo/reconciliar.py
===============================
Cruza el registro con `profit_table` y `statement` de Deriv (brief del
Admin del 06-oct-2026 (3), punto 3e). Marca cada trade terminado como `ok`
o `discrepancia` agregando una fila nueva de evento `reconciliacion`:
nunca edita una fila (el patrón de core/trade_ledger.py:14-47).

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
    oficiales, commit 54e3538): no se reconcilia, y la evidencia lo dice.
  · El reloj del envío es local (ms) y el de statement es del servidor (s):
    por eso la ventana es ancha.
  · Un trade ya marcado `ok` no se vuelve a reconciliar; uno en
    `discrepancia` sí, y suma otra fila.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from integracion_demo.registro import (
    EVENTOS_TERMINALES,
    Evento,
    Reconciliado,
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
    marca: dict[str, str] = {}
    terminales = {e.value for e in EVENTOS_TERMINALES}
    for f in filas:
        if f["evento"] in terminales:
            ultima_terminal[f["trade_uuid"]] = f
        elif f["evento"] == Evento.RECONCILIACION.value:
            marca[f["trade_uuid"]] = f["reconciliado"]
    nuevas = []
    for uuid, f in ultima_terminal.items():
        if marca.get(uuid) == Reconciliado.OK.value:
            continue
        if f.get("contract_id") is not None:
            problemas = _con_contrato(f, profit_table, statement)
        else:
            problemas = _sin_contrato(f, statement)
        evidencia: dict[str, Any] = {
            "problemas": problemas, "fila_terminal": f["seq"],
            "profit_table_sha256": raw_profit_table, "statement_sha256": raw_statement,
            "nota": "la comisión no aparece en profit_table ni en statement: no se reconcilia"}
        nuevas.append(registro.agregar(
            evento=Evento.RECONCILIACION.value, trade_uuid=uuid,
            contract_id=f.get("contract_id"), experiment_id=f.get("experiment_id"),
            account_id=f.get("account_id"), account_type=f.get("account_type"),
            ws_path=f.get("ws_path"), raw_sha256=raw_profit_table,
            reconciliado=(Reconciliado.DISCREPANCIA if problemas else Reconciliado.OK).value,
            evidencia=json.dumps(evidencia, ensure_ascii=False, sort_keys=True)))
    return nuevas
