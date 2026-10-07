"""
tests/test_deriv_sonda3_live.py
=================================
Sonda §0.A-3 v2, parte A: Deriv Options API (brief del Admin del
02-oct-2026). SOLO MIDE, para la candidata intradía (ruptura del rango de
apertura en M5/M15 sobre oro y BTC con multiplicadores ×100). No compra
nada, no toca pre-registros ni execution/, no migra deriv_ws.py.

══ LO QUE CONTESTA ══

  1. `contracts_for` de BTC, del oro y de frxEURUSD con TODOS los
     contract_type (duraciones, barreras, multiplier_range, stakes), y un
     escaneo de los símbolos de `active_symbols`: cuáles ofrecen vanillas,
     turbos y multiplicadores, marcando los sintéticos.
  2. `proposal` (cotización, nunca `buy`) en ws/public:
     a) MULTUP ×100 a 1, 1,5 y 2 USD por símbolo, y ×200 a 2 USD:
        `commission` y `limit_order.stop_out`. Si la comisión es
        proporcional al nocional o tiene un mínimo fijo, y si la distancia
        al stop-out ≈ 1/m − comisión.
     b) La misma MULTUP ×100 con `limit_order.stop_loss` a 0,3 % y 0,6 % del
        nocional: si la acepta, o el error literal.
     c) Si hay vanillas: VANILLALONGCALL a 1, 7, 30, 90 y 365 días, con
        strike ATM y +5 %: prima y payout por punto.
  3. UN OTP, solo para la cuenta que `GET /accounts` dice demo. Con él se
     abre `/ws/demo`, se repiten 1 y 2 para reportar diferencias, y se
     cierra. Sin cuenta demo confirmada, o si la URL del OTP no es la de
     `/ws/demo`, no se conecta y el informe dice por qué.
  4. `ticks_history` de BTC y del oro en M5 (300) y M15 (900): count 5000,
     end latest, paginando hacia atrás hasta vacía o error. Velas por
     página, primera y última época, si se respeta `end`, desalineadas.

══ FUENTES ══

Esquemas oficiales, github.com/deriv-com/deriv-api-schemas, commit 54e3538:
  · `POST /trading/v1/options/accounts/{accountId}/otp` (OpenAPI): exige
    `Deriv-App-ID` y scope `trade`; devuelve `data.url`, "a ready-to-use
    WebSocket URL with the OTP attached as a query parameter", válido 120
    segundos y una sola vez. La URL contiene el OTP: no va al informe.
  · `GET /trading/v1/options/ws/demo`: autentica con el `otp` de la query.
  · `proposal_request`: `limit_order` admite `stop_loss` y `take_profit`
    (montos), solo para MULTUP, MULTDOWN y ACCU. `proposal_response`:
    `limit_order.stop_out.value` es el precio ("pip-sized barrier value");
    `validation_params.stop_loss` trae el mínimo y el máximo del stop-loss;
    `display_number_of_contracts` es, en vanillas, el número implícito de
    contratos, que es el payout por punto.
  · `contracts_for_response`: `min_stake`/`max_stake` son "[Only for turbos
    options]"; para multiplicadores el stake sale de
    `proposal.validation_params.stake`.

[INTERPRETACIÓN] Lo que el brief no fija:
  · Nocional = stake × multiplicador. La comisión se compara contra él.
  · El stop-loss de 2b se pide sobre cada stake de 2a (1, 1,5 y 2 USD), con
    el monto redondeado a centavos.
  · La distancia al stop-out es (spot − stop_out.value) / spot. Se compara
    contra 1/m − comisión/nocional (comisión leída como monto) y contra
    1/m − comisión/100 (leída como porcentaje, que es lo que dice el
    esquema). El informe da las dos diferencias y la lectura más cercana,
    sin un umbral de "coincide". Con nocional 100 (stake 1 a ×100) las dos
    lecturas dan lo mismo; el informe la marca ambigua cuando los dos
    esperados están a menos de 2 pips entre sí.
  · Las vanillas se cotizan sobre los símbolos foco que las ofrezcan; la
    lista de todos los no sintéticos con vanillas está en el escaneo. El
    stake es el `default_stake` del ítem VANILLALONGCALL de `contracts_for`
    (o su `min_stake` si no trae default), el spot es el de la primera cotización MULTUP, y el strike se escribe
    en absoluto con los decimales de `pip_size`.
  · Con más de una cuenta demo activa, el OTP es para la primera en el orden
    de la respuesta. La moneda de las cotizaciones es la de esa cuenta.
  · Entre pedido y pedido del WS se espera PAUSA_ENTRE_PEDIDOS_S, porque el
    escaneo manda unos 90 `contracts_for` seguidos y Deriv no publica su
    límite por conexión.
  · M5/M15 se pagina con el `start` aprobado para la sonda 2 y un tope de
    MAX_PAGINAS_INTRADIA páginas, que se informa como corte si se toca.

══ CUÁNDO SE PONE ROJO ══

Igual que la sonda 2: solo por plomería (faltan DERIV_APP_ID o
DERIV_API_TOKEN), por una fuga (un secreto o el OTP en el informe) o si no
abre el WS público. Lo que conteste Deriv, incluido un error o una demo que
no se confirma, es el resultado y sale verde.
"""

from __future__ import annotations

import asyncio
import json
import math
from typing import Any, Callable, Optional

import pytest

from ingestion.sonda_instrumentos import CONTROL_POSITIVO, codigo, es_sintetico, seleccionar
from integracion_demo.otp import (  # noqa: F401 -- portados (brief 06-oct-2026 (3))
    BASE_REST,
    ENDPOINT_DEMO,
    NOTA_401,
    RUTA_CUENTAS,
    RUTA_OTP,
    CuentaNoDemoError,
    Getter,
    Poster,
    _get_httpx,
    _limpiador,
    _post_httpx,
    elegir_demo,
    emitir_otp_demo,
    leer_cuentas,
    motivo_para_no_conectar,
    otp_de,
)
from tests.test_deriv_endpoints_live import (
    ENDPOINT_PUBLICO_NUEVO,
    _http_de,
    texto_libre,
)
from tests.test_deriv_sonda2_live import _Canal, profundidad

#: Familias de contratos que el brief pide listar.
FAMILIAS: dict[str, frozenset[str]] = {
    "vanillas": frozenset({"VANILLALONGCALL", "VANILLALONGPUT"}),
    "turbos": frozenset({"TURBOSLONG", "TURBOSSHORT"}),
    "multiplicadores": frozenset({"MULTUP", "MULTDOWN"}),
}

#: 2a: stakes en USD a ×100, y el stake del ×200.
STAKES_X100: tuple[float, ...] = (1.0, 1.5, 2.0)
STAKE_X200 = 2.0

#: 2b: stop-loss como fracción del nocional.
FRACCIONES_STOP_LOSS: tuple[float, ...] = (0.003, 0.006)

#: 2c: plazos de las vanillas, en días, y los strikes relativos al spot.
DIAS_VANILLAS: tuple[int, ...] = (1, 7, 30, 90, 365)
STRIKES_VANILLAS: dict[str, float] = {"ATM": 1.0, "+5%": 1.05}

#: 4: granularidades intradía, en segundos.
GRANULARIDADES_INTRADIA: tuple[int, ...] = (300, 900)

#: [INTERPRETACIÓN] Tope de páginas por serie intradía: 300 páginas de 5000
#: velas M5 son más de 14 años.
MAX_PAGINAS_INTRADIA = 300

#: [INTERPRETACIÓN] Pausa entre pedidos del WS, en segundos.
PAUSA_ENTRE_PEDIDOS_S = 0.25

#: Redondeo de las comisiones (centavos): cada cotización puede estar hasta
#: medio centavo lejos de la proporción exacta.
_TOLERANCIA_COMISION_USD = 0.01

class _CanalPausado(_Canal):
    """El canal de la sonda 2 con una pausa antes de cada pedido. La lista
    blanca de deriv_ws sigue en `_Canal.pedir`."""

    def __init__(self, ws: Any, pausa_s: float = PAUSA_ENTRE_PEDIDOS_S) -> None:
        super().__init__(ws)
        self.pausa_s = pausa_s

    async def pedir(self, payload: dict) -> tuple[dict, Optional[dict]]:
        if self.pausa_s:
            await asyncio.sleep(self.pausa_s)
        return await super().pedir(payload)


# ═══ 1. Contratos ═════════════════════════════════════════════════════════

def familias_de(tipos: set[str]) -> dict[str, bool]:
    return {f: bool(tipos & ct) for f, ct in FAMILIAS.items()}


def detalle_contratos(cf: dict) -> list[dict]:
    """Cada ítem de `available` entero, salvo lo que repite el símbolo."""
    fuera = {"underlying_symbol", "market", "submarket"}
    return [{k: v for k, v in item.items() if k not in fuera}
            for item in (cf.get("available") or [])]


async def escanear(canal: _Canal, items: list[dict], foco: set[str]) -> dict:
    """`contracts_for` de cada símbolo de `active_symbols`. Para los foco
    guarda el detalle completo; para el resto, los tipos y las familias."""
    simbolos: list[dict] = []
    detalle: dict[str, Any] = {}
    for it in sorted(items, key=lambda i: codigo(i)[0]):
        sim = codigo(it)[0]
        entrada, datos = await canal.pedir({"contracts_for": sim})
        fila: dict[str, Any] = {"simbolo": sim, "mercado": it.get("market"),
                                "sintetico": es_sintetico(it)}
        if datos is None:
            fila["error"] = entrada.get("error")
        else:
            cf = datos.get("contracts_for") or {}
            tipos = {c.get("contract_type") for c in cf.get("available") or []} - {None}
            fila.update(tipos=sorted(tipos), familias=familias_de(tipos))
            if sim in foco:
                detalle[sim] = {"sha256": entrada["sha256"],
                                "contratos": detalle_contratos(cf)}
        simbolos.append(fila)
    resumen = {f: {"no_sinteticos": [s["simbolo"] for s in simbolos
                                     if s.get("familias", {}).get(f) and not s["sintetico"]],
                   "sinteticos": [s["simbolo"] for s in simbolos
                                  if s.get("familias", {}).get(f) and s["sintetico"]]}
               for f in FAMILIAS}
    return {"simbolos": simbolos, "por_familia": resumen, "detalle_foco": detalle}


# ═══ 2. Cotizaciones ══════════════════════════════════════════════════════

def _num(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


async def cotizar_multup(canal: _Canal, simbolo: str, moneda: str, *, stake: float,
                         multiplicador: float, stop_loss: Optional[float] = None) -> dict:
    payload: dict[str, Any] = {
        "proposal": 1, "contract_type": "MULTUP", "basis": "stake", "amount": stake,
        "currency": moneda, "underlying_symbol": simbolo, "multiplier": multiplicador}
    if stop_loss is not None:
        payload["limit_order"] = {"stop_loss": stop_loss}
    entrada, datos = await canal.pedir(payload)
    entrada["enviado"] = {"stake": stake, "multiplicador": multiplicador,
                          "nocional": stake * multiplicador, "stop_loss": stop_loss}
    if datos is not None:
        prop = datos.get("proposal") or {}
        lo = prop.get("limit_order") or {}
        vp = prop.get("validation_params") or {}
        entrada.update(
            commission=_num(prop.get("commission")), spot=_num(prop.get("spot")),
            stop_out={k: (lo.get("stop_out") or {}).get(k)
                      for k in ("order_amount", "display_order_amount", "value")},
            stop_loss_devuelto={k: (lo.get("stop_loss") or {}).get(k)
                                for k in ("order_amount", "display_order_amount", "value")}
            if lo.get("stop_loss") else None,
            validation_stop_loss=vp.get("stop_loss"), validation_stake=vp.get("stake"))
    return entrada


def analizar_comision(cotizaciones: list[dict]) -> dict:
    """¿La comisión es proporcional al nocional, o tiene un mínimo fijo?
    La fracción de referencia es la del nocional más grande, el que menos
    probablemente toque un mínimo."""
    pares = [(c["enviado"]["nocional"], c["commission"]) for c in cotizaciones
             if c.get("ok") and c.get("commission") is not None]
    if len(pares) < 2:
        return {"veredicto": "sin datos suficientes", "pares": pares}
    n_ref, c_ref = max(pares)
    f_ref = c_ref / n_ref
    fuera = [(n, c) for n, c in pares
             if abs(c - f_ref * n) > _TOLERANCIA_COMISION_USD + 1e-9]
    salida = {"fraccion_del_nocional": {str(n): c / n for n, c in pares},
              "fraccion_de_referencia": f_ref, "pares": pares}
    if not fuera:
        return {**salida, "veredicto": "proporcional al nocional"}
    valores = {c for _, c in fuera}
    if len(valores) == 1 and all(c > f_ref * n for n, c in fuera):
        return {**salida, "veredicto": "proporcional con mínimo fijo",
                "minimo_observado": valores.pop()}
    return {**salida, "veredicto": "indeterminado"}


def verificar_stop_out(cot: dict, pip: Optional[float]) -> dict:
    """Distancia relativa al stop-out contra 1/m − comisión, con las dos
    lecturas de la unidad de `commission`."""
    spot, valor = cot.get("spot"), _num((cot.get("stop_out") or {}).get("value"))
    com = cot.get("commission")
    m, nocional = cot["enviado"]["multiplicador"], cot["enviado"]["nocional"]
    if not cot.get("ok") or not spot or valor is None or com is None:
        return {"verificable": False}
    distancia = (spot - valor) / spot
    esperados = {"monto": 1 / m - com / nocional, "porcentaje": 1 / m - com / 100}
    diferencia = {k: distancia - e for k, e in esperados.items()}
    cercana = min(diferencia, key=lambda k: abs(diferencia[k]))
    return {"verificable": True, "distancia": distancia, "esperado": esperados,
            "diferencia": diferencia,
            "error_relativo": abs(diferencia[cercana]) / esperados[cercana],
            "lectura_mas_cercana": cercana,
            "ambiguo": abs(esperados["monto"] - esperados["porcentaje"]) < 2 * (pip or 0) / spot
                       or esperados["monto"] == esperados["porcentaje"]}


def decimales_de(pip: Optional[float]) -> int:
    if not pip or pip >= 1:
        return 0
    return max(0, round(-math.log10(pip)))


async def cotizar_vanillas(canal: _Canal, simbolo: str, moneda: str, *,
                           stake: float, spot: float, pip: Optional[float]) -> list[dict]:
    out = []
    dec = decimales_de(pip)
    for dias in DIAS_VANILLAS:
        for nombre, factor in STRIKES_VANILLAS.items():
            barrera = f"{spot * factor:.{dec}f}"
            entrada, datos = await canal.pedir({
                "proposal": 1, "contract_type": "VANILLALONGCALL", "basis": "stake",
                "amount": stake, "currency": moneda, "underlying_symbol": simbolo,
                "duration": dias, "duration_unit": "d", "barrier": barrera})
            entrada["enviado"] = {"dias": dias, "strike": nombre, "barrera": barrera,
                                  "stake": stake}
            if datos is not None:
                prop = datos.get("proposal") or {}
                entrada.update(prima=_num(prop.get("ask_price")),
                               payout_por_punto=prop.get("display_number_of_contracts"),
                               barrier_choices=prop.get("barrier_choices"))
            out.append(entrada)
    return out


async def cotizar_simbolo(canal: _Canal, simbolo: str, moneda: str, *,
                          pip: Optional[float], contratos: list[dict]) -> dict:
    """2a, 2b y 2c para un símbolo."""
    a = [await cotizar_multup(canal, simbolo, moneda, stake=s, multiplicador=100.0)
         for s in STAKES_X100]
    a.append(await cotizar_multup(canal, simbolo, moneda, stake=STAKE_X200,
                                  multiplicador=200.0))
    b = [await cotizar_multup(canal, simbolo, moneda, stake=s, multiplicador=100.0,
                              stop_loss=round(f * s * 100.0, 2))
         for s in STAKES_X100 for f in FRACCIONES_STOP_LOSS]
    out: dict[str, Any] = {
        "multup": a, "comision": analizar_comision(a),
        "stop_out": [verificar_stop_out(c, pip) for c in a], "stop_loss": b}
    vanilla = [c for c in contratos if c.get("contract_type") == "VANILLALONGCALL"]
    spot = next((c["spot"] for c in a if c.get("spot")), None)
    stake_v = (_num(vanilla[0].get("default_stake")) or _num(vanilla[0].get("min_stake"))
               if vanilla else None)
    if not vanilla:
        out["vanillas"] = {"no_aplica": "el símbolo no ofrece VANILLALONGCALL"}
    elif spot is None or stake_v is None:
        out["vanillas"] = {"no_aplica": "sin spot de la cotización MULTUP o sin "
                                        "default_stake de la vanilla"}
    else:
        out["vanillas"] = await cotizar_vanillas(canal, simbolo, moneda, stake=stake_v,
                                                 spot=spot, pip=pip)
    return out


# ═══ El recorrido de un canal: 1 y 2 ══════════════════════════════════════

async def recorrer_canal(canal: _Canal, *, moneda: Optional[str]) -> dict:
    """active_symbols, el escaneo (1) y las cotizaciones (2). Nunca lanza
    por lo que conteste Deriv."""
    out: dict[str, Any] = {}
    entrada, datos = await canal.pedir({"active_symbols": "brief"})
    out["active_symbols"] = entrada
    if datos is None:
        return out
    items = datos.get("active_symbols") or []
    sel = seleccionar(items)
    foco = [codigo(i)[0] for i in sel.btc + sel.oro]
    if sel.control_presente:
        foco.append(CONTROL_POSITIVO)
    entrada.update(n_simbolos=len(items), btc=[codigo(i)[0] for i in sel.btc],
                   oro=[codigo(i)[0] for i in sel.oro], foco=foco)
    out["contratos"] = await escanear(canal, items, set(foco))
    if not moneda:
        out["cotizaciones"] = {"no_aplica": "sin la moneda de una cuenta demo: no "
                                            "se escribe una a mano"}
        return out
    pips = {codigo(i)[0]: _num(i.get("pip_size")) for i in items}
    out["cotizaciones"] = {}
    for sim in foco:
        contratos = (out["contratos"]["detalle_foco"].get(sim) or {}).get("contratos") or []
        out["cotizaciones"][sim] = await cotizar_simbolo(
            canal, sim, moneda, pip=pips.get(sim), contratos=contratos)
    return out


async def intradia(canal: _Canal, simbolos: list[str]) -> list[dict]:
    """4. M5 y M15 de cada símbolo."""
    return [{**(await profundidad(canal, s, g, max_paginas=MAX_PAGINAS_INTRADIA)),
             "granularidad": g}
            for s in simbolos for g in GRANULARIDADES_INTRADIA]


# ═══ 3. Cuenta demo, OTP y /ws/demo ═══════════════════════════════════════

# ═══ Diferencias entre ws/public y ws/demo ════════════════════════════════

#: Campos de `contracts_for` que cambian con el spot: en la sonda del 05-oct
#: fueron toda la diferencia de contratos entre los canales.
CAMPOS_QUE_SIGUEN_AL_SPOT: frozenset[str] = frozenset({
    "barrier", "high_barrier", "low_barrier", "barrier_choices",
    "available_barriers", "expired_barriers"})


def _error_code(e: Any) -> Any:
    return e.get("code") if isinstance(e, dict) else e


def huella(canal: dict) -> dict[str, Any]:
    """Lo comparable de un recorrido, sin lo que cambia con el spot."""
    h: dict[str, Any] = {}
    contratos = canal.get("contratos") or {}
    for s in contratos.get("simbolos") or []:
        h[f"escaneo:{s['simbolo']}"] = s.get("tipos") or _error_code(s.get("error"))
    for sim, d in (contratos.get("detalle_foco") or {}).items():
        h[f"contratos:{sim}"] = sorted(
            json.dumps({k: v for k, v in c.items() if k not in CAMPOS_QUE_SIGUEN_AL_SPOT},
                       sort_keys=True)
            for c in d["contratos"])
    for sim, c in (canal.get("cotizaciones") or {}).items():
        if not isinstance(c, dict) or "multup" not in c:
            continue
        for q in c["multup"] + c["stop_loss"]:
            e = q["enviado"]
            h[f"proposal:{sim}:x{e['multiplicador']:g}:{e['stake']:g}:sl{e['stop_loss']}"] = (
                q.get("ok"), _error_code(q.get("error")), q.get("commission"))
        for q in c["vanillas"] if isinstance(c["vanillas"], list) else []:
            e = q["enviado"]
            h[f"vanilla:{sim}:{e['dias']}d:{e['strike']}"] = (q.get("ok"), _error_code(q.get("error")))
    return h


def diferencias(publico: dict, demo: dict) -> list[dict]:
    hp, hd = huella(publico), huella(demo)
    return [{"clave": k, "publico": hp.get(k), "demo": hd.get(k)}
            for k in sorted(set(hp) | set(hd)) if hp.get(k) != hd.get(k)]


# ═══ La sonda entera ══════════════════════════════════════════════════════

async def sondear(*, token: str, app_id: str, abrir: Callable[[str], Any],
                  get: Getter = _get_httpx, post: Poster = _post_httpx,
                  pausa_s: float = PAUSA_ENTRE_PEDIDOS_S) -> tuple[dict, tuple[str, ...]]:
    """Devuelve el informe y los secretos que no pueden aparecer en él (el
    token, el App ID y el OTP, si se emitió)."""
    secretos: list[str] = [token, app_id]
    informe: dict[str, Any] = {"sonda": "§0.A-3 v2, parte A"}
    informe["cuentas"], cuentas = await leer_cuentas(token=token, app_id=app_id, get=get)
    demo = elegir_demo(cuentas)
    moneda = demo.get("currency") if demo else None
    informe["moneda"] = moneda

    publico: dict[str, Any] = {"endpoint": ENDPOINT_PUBLICO_NUEVO, "handshake": None}
    informe["ws_publico"] = publico
    try:
        async with abrir(ENDPOINT_PUBLICO_NUEVO) as ws:
            publico["handshake"] = {"ok": True}
            canal = _CanalPausado(ws, pausa_s)
            publico.update(await recorrer_canal(canal, moneda=moneda))
            simbolos = ((publico.get("active_symbols") or {}).get("btc") or []) + \
                       ((publico.get("active_symbols") or {}).get("oro") or [])
            publico["intradia"] = await intradia(canal, simbolos)
    except Exception as exc:   # noqa: BLE001
        clave = "handshake" if publico["handshake"] is None else "error_de_conexion"
        publico[clave] = {"ok": False, "http": _http_de(exc),
                          "error": f"{type(exc).__name__}: {exc}"}

    demo_inf: dict[str, Any] = {"endpoint": ENDPOINT_DEMO}
    informe["ws_demo"] = demo_inf
    if demo is None:
        demo_inf["no_aplica"] = ("GET /accounts no confirmó una cuenta demo activa: no "
                                 "se emitió OTP ni se abrió /ws/demo")
        return informe, tuple(secretos)
    demo_inf["otp"], url = await emitir_otp_demo(demo, token=token, app_id=app_id, post=post)
    if url is None:
        return informe, tuple(secretos)
    secretos.append(otp_de(url) or "")
    motivo = motivo_para_no_conectar(url)
    if motivo:
        demo_inf["no_conectado"] = motivo
        return informe, tuple(secretos)
    demo_inf["handshake"] = None
    try:
        async with abrir(url) as ws:
            demo_inf["handshake"] = {"ok": True}
            demo_inf.update(await recorrer_canal(_CanalPausado(ws, pausa_s), moneda=moneda))
    except Exception as exc:   # noqa: BLE001
        clave = "handshake" if demo_inf["handshake"] is None else "error_de_conexion"
        demo_inf[clave] = {"ok": False, "http": _http_de(exc),
                           "error": _limpiador(tuple(secretos))(f"{type(exc).__name__}: {exc}")}
        return informe, tuple(secretos)
    informe["diferencias_publico_demo"] = diferencias(publico, demo_inf)
    return informe, tuple(secretos)


# La entrada `live` se retiró el 05-oct: la sonda corrió ese día (run
# 37318228891) y sus resultados están en el decision-log. Volver a correrla
# pediría un segundo OTP demo en el mismo job que la sonda §0.A-3b.


# ═══ La sonda misma, offline ══════════════════════════════════════════════

from tests.deriv_falso import DerivFalso  # noqa: E402
from tests.test_deriv_sonda2_live import _AHORA, _historia  # noqa: E402

_SPOT = {"cryBTCUSD": 60000.0, "frxXAUUSD": 2400.0, "frxEURUSD": 1.1}
_PIP = {"cryBTCUSD": 0.01, "frxXAUUSD": 0.01, "frxEURUSD": 0.00001}

_ITEMS = [
    {"underlying_symbol": "R_50", "market": "synthetic_index", "pip_size": 0.01},
    {"underlying_symbol": "BTCVOL", "market": "synthetic_index", "pip_size": 0.01},
    {"underlying_symbol": "frxEURUSD", "market": "forex", "pip_size": 0.00001},
    {"underlying_symbol": "frxGBPUSD", "market": "forex", "pip_size": 0.00001},
    {"underlying_symbol": "cryBTCUSD", "market": "cryptocurrency", "pip_size": 0.01},
    {"underlying_symbol": "frxXAUUSD", "market": "commodities", "pip_size": 0.01},
]

_TIPOS = {
    "R_50": ["MULTUP", "MULTDOWN", "VANILLALONGCALL", "TURBOSLONG", "CALL"],
    "BTCVOL": ["ACCU"],
    "frxEURUSD": ["MULTUP", "MULTDOWN", "VANILLALONGCALL", "VANILLALONGPUT", "CALL"],
    "frxGBPUSD": ["CALL", "PUT", "TURBOSLONG", "TURBOSSHORT"],
    "cryBTCUSD": ["MULTUP", "MULTDOWN"],
    "frxXAUUSD": ["MULTUP", "MULTDOWN", "VANILLALONGCALL"],
}


def _contracts_for(tipos=_TIPOS):
    def manejar(p):
        sim = p["contracts_for"]
        disp = []
        for t in tipos[sim]:
            item = {"contract_type": t, "underlying_symbol": sim, "market": "x",
                    "min_contract_duration": "1d", "max_contract_duration": "365d"}
            if t.startswith("MULT"):
                item.update(multiplier_range=[100, 200, 300, 500, 800], default_stake=2)
            if t.startswith("VANILLA"):
                item.update(default_stake=10, barrier_choices=["1", "2"])
            disp.append(item)
        return {"contracts_for": {"available": disp}}
    return manejar


def _comision(sim, nocional):
    """BTC con mínimo de 0,10; el resto proporcional."""
    if sim == "cryBTCUSD":
        return max(0.10, round(0.0007 * nocional, 2))
    return round(0.0002 * nocional, 2)


def _proposal(p):
    sim = p["underlying_symbol"]
    if p["contract_type"].startswith("VANILLA"):
        return {"proposal": {"ask_price": p["amount"], "display_number_of_contracts": "0.5",
                             "spot": _SPOT[sim]}}
    if (p.get("limit_order") or {}).get("stop_loss", 1) < 0.5:
        return {"error": {"code": "InvalidStopLoss",
                          "message": "Please enter a stop loss amount that's higher than 0.50."}}
    m, n = p["multiplier"], p["amount"] * p["multiplier"]
    com = _comision(sim, n)
    out = {"proposal": {"commission": com, "spot": _SPOT[sim],
                        "limit_order": {"stop_out": {
                            "order_amount": -p["amount"],
                            "value": f"{_SPOT[sim] * (1 - (1 / m - com / n)):.5f}"}},
                        "validation_params": {"stake": {"min": "1.00", "max": "500.00"},
                                              "stop_loss": {"min": "0.50", "max": "100"}}}}
    if "limit_order" in p:
        out["proposal"]["limit_order"]["stop_loss"] = {"order_amount": -p["limit_order"]["stop_loss"]}
    return out


def _deriv(**extra) -> DerivFalso:
    base = {"active_symbols": lambda p: {"active_symbols": _ITEMS},
            "contracts_for": _contracts_for(), "proposal": _proposal,
            "ticks_history": _historia(_AHORA - 3 * 365 * 86400, ignora_end=True)}
    base.update(extra)
    return DerivFalso(base)


_CUENTAS = {"data": [
    {"account_id": "ROT12345678", "account_type": "real", "status": "active", "currency": "USD"},
    {"account_id": "DOT90004580", "account_type": "demo", "status": "active", "currency": "USD"},
    {"account_id": "DOT90009999", "account_type": "demo", "status": "active", "currency": "EUR"}]}
_OTP = "otpQ9zSECRETO"


def _rest(cuentas=_CUENTAS, *, url=None, status_otp=200):
    vistos: list[tuple[str, str]] = []

    async def get(u, h):
        vistos.append(("GET", u))
        return 200, {}, json.dumps(cuentas)

    async def post(u, h):
        vistos.append(("POST", u))
        return status_otp, {}, json.dumps({"data": {"url": url or f"{ENDPOINT_DEMO}?otp={_OTP}"}})
    return get, post, vistos


def _abridor(publico: DerivFalso, demo: DerivFalso):
    abiertos: list[str] = []

    def abrir(url):
        abiertos.append(url)
        return (publico if url == ENDPOINT_PUBLICO_NUEVO else demo).connector(url)
    return abrir, abiertos


async def _correr(publico=None, demo=None, cuentas=_CUENTAS, url=None):
    publico, demo = publico or _deriv(), demo or _deriv()
    get, post, vistos = _rest(cuentas, url=url)
    abrir, abiertos = _abridor(publico, demo)
    informe, secretos = await sondear(token="TOKq7", app_id="APP31", abrir=abrir,
                                      get=get, post=post, pausa_s=0)
    return informe, secretos, vistos, abiertos, publico, demo


async def _canal(d: DerivFalso) -> _Canal:
    return _CanalPausado(await d.connector("wss://x").__aenter__(), 0)


# ── 1. escaneo ───────────────────────────────────────────────────────────

async def test_el_escaneo_marca_familias_y_sinteticos():
    d = _deriv()
    esc = await escanear(await _canal(d), _ITEMS, {"cryBTCUSD"})
    assert len(d.de_tipo("contracts_for")) == len(_ITEMS)
    fila = {s["simbolo"]: s for s in esc["simbolos"]}
    assert fila["R_50"]["sintetico"] is True and fila["frxEURUSD"]["sintetico"] is False
    assert fila["frxGBPUSD"]["familias"] == {"vanillas": False, "turbos": True,
                                             "multiplicadores": False}
    assert esc["por_familia"]["vanillas"] == {"no_sinteticos": ["frxEURUSD", "frxXAUUSD"],
                                              "sinteticos": ["R_50"]}
    assert esc["por_familia"]["turbos"]["no_sinteticos"] == ["frxGBPUSD"]
    assert list(esc["detalle_foco"]) == ["cryBTCUSD"]
    tipos = [c["contract_type"] for c in esc["detalle_foco"]["cryBTCUSD"]["contratos"]]
    assert tipos == ["MULTUP", "MULTDOWN"], "todos los contract_type del foco"
    c = esc["detalle_foco"]["cryBTCUSD"]["contratos"][0]
    assert c["multiplier_range"] == [100, 200, 300, 500, 800]
    assert c["min_contract_duration"] == "1d" and "underlying_symbol" not in c


async def test_un_error_de_contracts_for_queda_en_su_fila():
    d = _deriv(contracts_for=lambda p: {"error": {"code": "InvalidSymbol", "message": "x"}})
    esc = await escanear(await _canal(d), _ITEMS[:1], set())
    assert esc["simbolos"][0]["error"]["code"] == "InvalidSymbol"


# ── 2a. comisión y stop-out ──────────────────────────────────────────────

def _cot(stake, m, com, ok=True):
    return {"ok": ok, "commission": com, "enviado": {"stake": stake, "multiplicador": m,
                                                     "nocional": stake * m}}


def test_comision_proporcional():
    a = [_cot(1, 100, 0.02), _cot(1.5, 100, 0.03), _cot(2, 100, 0.04), _cot(2, 200, 0.08)]
    r = analizar_comision(a)
    assert r["veredicto"] == "proporcional al nocional"
    assert r["fraccion_de_referencia"] == pytest.approx(0.0002)


def test_comision_con_minimo_fijo():
    a = [_cot(1, 100, 0.10), _cot(1.5, 100, 0.11), _cot(2, 100, 0.14), _cot(2, 200, 0.28)]
    r = analizar_comision(a)
    assert r["veredicto"] == "proporcional con mínimo fijo"
    assert r["minimo_observado"] == 0.10


def test_comision_indeterminada_y_sin_datos():
    a = [_cot(1, 100, 0.30), _cot(1.5, 100, 0.02), _cot(2, 100, 0.14), _cot(2, 200, 0.28)]
    assert analizar_comision(a)["veredicto"] == "indeterminado"
    assert analizar_comision([_cot(2, 100, 0.14), _cot(1, 100, None)])["veredicto"] == \
        "sin datos suficientes"
    assert analizar_comision([_cot(2, 100, 0.1, ok=False), _cot(1, 100, 0.1, ok=False)]
                             )["veredicto"] == "sin datos suficientes"


def _con_stop_out(stake, m, com, spot, distancia):
    c = _cot(stake, m, com)
    c.update(spot=spot, stop_out={"value": str(spot * (1 - distancia))})
    return c


def test_stop_out_cercano_a_la_lectura_de_monto():
    c = _con_stop_out(2, 100, 0.14, 60000.0, 1 / 100 - 0.14 / 200)
    v = verificar_stop_out(c, 0.01)
    assert v["lectura_mas_cercana"] == "monto" and v["ambiguo"] is False
    assert v["distancia"] == pytest.approx(0.0093)
    assert v["diferencia"]["monto"] == pytest.approx(0, abs=1e-12)
    assert v["diferencia"]["porcentaje"] == pytest.approx(0.0007)
    assert v["error_relativo"] == pytest.approx(0, abs=1e-9)


def test_stop_out_cercano_a_la_lectura_de_porcentaje():
    c = _con_stop_out(2, 100, 0.5, 60000.0, 1 / 100 - 0.5 / 100)
    v = verificar_stop_out(c, 0.01)
    assert v["lectura_mas_cercana"] == "porcentaje" and v["ambiguo"] is False


def test_con_nocional_100_las_dos_lecturas_son_la_misma():
    c = _con_stop_out(1, 100, 0.07, 60000.0, 1 / 100 - 0.07 / 100)
    assert verificar_stop_out(c, 0.01)["ambiguo"] is True


def test_ambiguo_si_los_esperados_estan_a_menos_de_dos_pips():
    # nocional 150, comisión 0,001: los esperados difieren en 3,3e-6; 2 pips
    # de 0,01 sobre 2.400 son 8,3e-6.
    c = _con_stop_out(1.5, 100, 0.001, 2400.0, 0.0099)
    assert verificar_stop_out(c, 0.01)["ambiguo"] is True
    assert verificar_stop_out(c, 0.000001)["ambiguo"] is False


def test_stop_out_lejos_informa_el_error_relativo():
    c = _con_stop_out(2, 100, 0.14, 60000.0, 0.02)
    v = verificar_stop_out(c, 0.01)
    assert v["error_relativo"] > 1


def test_stop_out_no_verificable_sin_datos():
    assert verificar_stop_out(_cot(2, 100, 0.1, ok=False), 0.01) == {"verificable": False}
    c = _cot(2, 100, 0.1)
    c.update(spot=100.0, stop_out={"value": None})
    assert verificar_stop_out(c, 0.01) == {"verificable": False}


# ── 2. cotizaciones, contra el Deriv falso ───────────────────────────────

async def test_cotizaciones_de_un_simbolo():
    d = _deriv()
    contratos = detalle_contratos(_contracts_for()({"contracts_for": "frxXAUUSD"})["contracts_for"])
    r = await cotizar_simbolo(await _canal(d), "frxXAUUSD", "USD", pip=0.01, contratos=contratos)
    props = d.de_tipo("proposal")
    assert all("subscribe" not in p and p["proposal"] == 1 for p in props)
    assert all(p["underlying_symbol"] == "frxXAUUSD" and "symbol" not in p for p in props)
    mult = [p for p in props if p["contract_type"] == "MULTUP"]
    assert [(p["amount"], p["multiplier"], (p.get("limit_order") or {}).get("stop_loss"))
            for p in mult] == [
        (1.0, 100.0, None), (1.5, 100.0, None), (2.0, 100.0, None), (2.0, 200.0, None),
        (1.0, 100.0, 0.3), (1.0, 100.0, 0.6), (1.5, 100.0, 0.45), (1.5, 100.0, 0.9),
        (2.0, 100.0, 0.6), (2.0, 100.0, 1.2)]
    assert r["comision"]["veredicto"] == "proporcional al nocional"
    assert [v["lectura_mas_cercana"] for v in r["stop_out"]][1:] == ["monto"] * 3
    assert r["stop_out"][0]["ambiguo"] is True, "stake 1 a ×100: nocional 100"
    rechazado = r["stop_loss"][0]
    assert rechazado["ok"] is False and rechazado["error"] == {
        "code": "InvalidStopLoss",
        "message": "Please enter a stop loss amount that's higher than 0.50."}
    aceptado = r["stop_loss"][1]
    assert aceptado["ok"] is True and aceptado["stop_loss_devuelto"]["order_amount"] == -0.6
    assert r["multup"][0]["validation_stop_loss"] == {"min": "0.50", "max": "100"}
    assert r["multup"][0]["stop_out"]["order_amount"] == -1.0


async def test_vanillas_a_cinco_plazos_y_dos_strikes():
    d = _deriv()
    contratos = detalle_contratos(_contracts_for()({"contracts_for": "frxXAUUSD"})["contracts_for"])
    r = await cotizar_simbolo(await _canal(d), "frxXAUUSD", "USD", pip=0.01, contratos=contratos)
    v = [p for p in d.de_tipo("proposal") if p["contract_type"] == "VANILLALONGCALL"]
    assert [(p["duration"], p["duration_unit"], p["barrier"]) for p in v] == [
        (dias, "d", b) for dias in (1, 7, 30, 90, 365) for b in ("2400.00", "2520.00")]
    assert all(p["amount"] == 10 for p in v), "el default_stake de la vanilla"
    assert r["vanillas"][0]["prima"] == 10 and r["vanillas"][0]["payout_por_punto"] == "0.5"
    assert r["vanillas"][1]["enviado"]["strike"] == "+5%"


async def test_sin_vanillas_no_se_cotizan():
    d = _deriv()
    contratos = detalle_contratos(_contracts_for()({"contracts_for": "cryBTCUSD"})["contracts_for"])
    r = await cotizar_simbolo(await _canal(d), "cryBTCUSD", "USD", pip=0.01, contratos=contratos)
    assert "no_aplica" in r["vanillas"]
    assert all(p["contract_type"] == "MULTUP" for p in d.de_tipo("proposal"))
    assert r["comision"]["veredicto"] == "proporcional con mínimo fijo"


@pytest.mark.parametrize("pip, dec", [(0.01, 2), (0.00001, 5), (0.001, 3), (1, 0), (None, 0)])
def test_decimales_del_pip(pip, dec):
    assert decimales_de(pip) == dec


# ── 3. cuenta demo y OTP ─────────────────────────────────────────────────

def test_elegir_demo():
    assert elegir_demo(_CUENTAS["data"])["account_id"] == "DOT90004580"
    assert elegir_demo([_CUENTAS["data"][0]]) is None
    assert elegir_demo([{**_CUENTAS["data"][1], "status": "inactive"}]) is None
    assert elegir_demo([]) is None


async def test_el_otp_no_se_pide_para_una_cuenta_real():
    llamadas = []

    async def post(u, h):
        llamadas.append(u)
        return 200, {}, "{}"
    with pytest.raises(CuentaNoDemoError):
        await emitir_otp_demo(_CUENTAS["data"][0], token="T", app_id="A", post=post)
    assert llamadas == []


async def test_el_otp_va_a_la_ruta_de_la_cuenta_demo_con_los_headers():
    vistos = []

    async def post(u, h):
        vistos.append((u, h))
        return 200, {}, json.dumps({"data": {"url": f"{ENDPOINT_DEMO}?otp={_OTP}"}})
    inf, url = await emitir_otp_demo(_CUENTAS["data"][1], token="T", app_id="A", post=post)
    assert vistos == [(BASE_REST + "/trading/v1/options/accounts/DOT90004580/otp",
                       {"Authorization": "Bearer T", "Deriv-App-ID": "A"})]
    assert url.endswith(_OTP) and _OTP not in json.dumps(inf)
    assert inf == {"ok": True, "cuenta": "DOT********", "http": 200}


async def test_un_otp_rechazado_queda_registrado_sin_ids():
    async def post(u, h):
        return 401, {}, '{"errors":[{"code":"Unauthorized","message":"DOT90004580"}]}'
    inf, url = await emitir_otp_demo(_CUENTAS["data"][1], token="T", app_id="A", post=post)
    assert url is None and inf["http"] == 401 and inf["nota"] == NOTA_401
    assert "90004580" not in json.dumps(inf)


@pytest.mark.parametrize("url, conecta", [
    (f"{ENDPOINT_DEMO}?otp=abc", True),
    (f"{ENDPOINT_DEMO}/?otp=abc", True),
    (f"{ENDPOINT_DEMO.rsplit('/', 1)[0]}/re" + f"al?otp=abc", False),
    (f"{ENDPOINT_PUBLICO_NUEVO}?otp=abc", False),
    (f"{ENDPOINT_DEMO.replace('api.derivws.com', 'otro.example')}?otp=abc", False),
    (f"{ENDPOINT_DEMO.replace('wss:', 'ws:')}?otp=abc", False),
    (ENDPOINT_DEMO, False),
])
def test_solo_se_conecta_a_ws_demo_con_otp(url, conecta):
    assert (motivo_para_no_conectar(url) is None) is conecta


# ── La sonda entera ──────────────────────────────────────────────────────

async def test_la_sonda_entera_con_demo():
    informe, secretos, vistos, abiertos, pub, demo = await _correr()
    assert vistos == [("GET", BASE_REST + RUTA_CUENTAS),
                      ("POST", BASE_REST + "/trading/v1/options/accounts/DOT90004580/otp")], \
        "un GET y UN OTP, para la primera demo activa"
    assert abiertos == [ENDPOINT_PUBLICO_NUEVO, f"{ENDPOINT_DEMO}?otp={_OTP}"]
    assert _OTP in secretos and "TOKq7" in secretos and "APP31" in secretos
    texto = texto_libre(informe)
    assert _OTP not in texto and "TOKq7" not in texto and "APP31" not in texto
    assert informe["moneda"] == "USD"
    assert informe["cuentas"]["cuentas"][1] == {"account_id": "DOT********",
                                                "account_type": "demo", "status": "active"}
    p = informe["ws_publico"]
    assert p["active_symbols"]["foco"] == ["cryBTCUSD", "frxXAUUSD", "frxEURUSD"]
    assert set(p["cotizaciones"]) == {"cryBTCUSD", "frxXAUUSD", "frxEURUSD"}
    assert [(x["simbolo"], x["granularidad"]) for x in p["intradia"]] == [
        ("cryBTCUSD", 300), ("cryBTCUSD", 900), ("frxXAUUSD", 300), ("frxXAUUSD", 900)]
    assert "intradia" not in informe["ws_demo"], "el punto 4 no se repite en demo"
    assert set(informe["ws_demo"]["cotizaciones"]) == set(p["cotizaciones"])
    assert informe["diferencias_publico_demo"] == []
    tipos = {next(iter(m)) for m in pub.enviados + demo.enviados}
    assert tipos == {"active_symbols", "contracts_for", "proposal", "ticks_history"}, \
        "nada de buy, ni de nada que no sea lectura"


async def test_las_diferencias_entre_public_y_demo():
    otros = {**_TIPOS, "cryBTCUSD": ["MULTUP"]}
    informe, *_ = await _correr(demo=_deriv(contracts_for=_contracts_for(otros)))
    claves = {d["clave"] for d in informe["diferencias_publico_demo"]}
    assert "escaneo:cryBTCUSD" in claves and "contratos:cryBTCUSD" in claves
    assert not any(k.startswith("escaneo:frxXAUUSD") for k in claves)


def test_las_barreras_que_siguen_al_spot_no_son_diferencias():
    c = {"contract_type": "CALL", "barrier": "4185.90", "min_contract_duration": "1d"}
    pub = {"contratos": {"detalle_foco": {"X": {"contratos": [c]}}}}
    dem = {"contratos": {"detalle_foco": {"X": {"contratos": [{**c, "barrier": "4182.91"}]}}}}
    assert diferencias(pub, dem) == []
    dem["contratos"]["detalle_foco"]["X"]["contratos"][0]["min_contract_duration"] = "5m"
    assert [d["clave"] for d in diferencias(pub, dem)] == ["contratos:X"]


def test_las_diferencias_ignoran_el_spot_y_ven_la_comision():
    q = {"ok": True, "commission": 0.1, "spot": 1.0, "error": None,
         "enviado": {"multiplicador": 100.0, "stake": 1.0, "stop_loss": None}}
    pub = {"cotizaciones": {"X": {"multup": [q], "stop_loss": [], "vanillas": {"no_aplica": ""}}}}
    dem = {"cotizaciones": {"X": {"multup": [{**q, "spot": 2.0}], "stop_loss": [],
                                  "vanillas": {"no_aplica": ""}}}}
    assert diferencias(pub, dem) == []
    dem["cotizaciones"]["X"]["multup"][0]["commission"] = 0.2
    assert [d["clave"] for d in diferencias(pub, dem)] == ["proposal:X:x100:1:slNone"]


async def test_sin_cuenta_demo_no_hay_otp_ni_cotizaciones():
    solo_real = {"data": [_CUENTAS["data"][0]]}
    informe, _, vistos, abiertos, pub, _ = await _correr(cuentas=solo_real)
    assert [v[0] for v in vistos] == ["GET"], "ningún POST"
    assert abiertos == [ENDPOINT_PUBLICO_NUEVO]
    assert "no_aplica" in informe["ws_demo"]
    assert "no_aplica" in informe["ws_publico"]["cotizaciones"]
    assert pub.de_tipo("proposal") == []
    assert informe["ws_publico"]["contratos"]["simbolos"], "el escaneo corre igual"


async def test_si_la_url_del_otp_no_es_ws_demo_no_se_conecta():
    otra = f"{ENDPOINT_DEMO.rsplit('/', 1)[0]}/re" + f"al?otp={_OTP}"
    informe, secretos, _, abiertos, *_ = await _correr(url=otra)
    assert abiertos == [ENDPOINT_PUBLICO_NUEVO]
    assert "no es la de /ws/demo" in informe["ws_demo"]["no_conectado"]
    assert _OTP in secretos and _OTP not in texto_libre(informe)


async def test_un_401_en_cuentas_se_reporta_como_posible_vencimiento():
    async def get(u, h):
        return 401, {}, '{"error":"expired for DOT90004580"}'
    inf, cuentas = await leer_cuentas(token="T", app_id="A", get=get)
    assert cuentas == [] and inf["nota"] == NOTA_401 and "90004580" not in json.dumps(inf)


async def test_el_canal_pausa_antes_de_cada_pedido(monkeypatch):
    pausas = []

    async def dormir(s):
        pausas.append(s)
    monkeypatch.setattr(asyncio, "sleep", dormir)
    d = _deriv()
    canal = _CanalPausado(await d.connector("wss://x").__aenter__(), 0.25)
    await canal.pedir({"active_symbols": "brief"})
    await canal.pedir({"contracts_for": "R_50"})
    assert pausas == [0.25, 0.25]


async def test_el_canal_pausado_sigue_pasando_por_la_lista_blanca():
    from ingestion.deriv_ws import MensajeNoPermitidoError
    canal = await _canal(_deriv())
    with pytest.raises(MensajeNoPermitidoError):
        await canal.pedir({"buy": 1, "price": 1})
    with pytest.raises(MensajeNoPermitidoError):
        await canal.pedir({"proposal": 1, "subscribe": 1})


def test_el_canal_demo_se_deriva_del_publico():
    assert ENDPOINT_DEMO == ENDPOINT_PUBLICO_NUEVO.replace("/ws/public", "/ws/demo")
