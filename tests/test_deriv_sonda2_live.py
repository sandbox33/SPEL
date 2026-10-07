"""
tests/test_deriv_sonda2_live.py
=================================
Sonda §0.A-2 (addendum del 29-sep-2026, ítem 6). Mide lo que hace falta
saber ANTES de migrar `deriv_ws.py` al WS público nuevo. Solo lectura, el
mismo patrón live que la §0.A (tests/test_deriv_endpoints_live.py): corre
en live-tests.yml, publica un informe JSON en el log del job y no escribe
nada en ningún lado.

══ LO QUE CONTESTA ══

  a) PROFUNDIDAD. `ticks_history` diario (count 5000, end latest) sobre el
     BTC y el oro que elige `seleccionar()` de `active_symbols` por mercado,
     sin símbolo fijo. Después pagina hacia atrás (end = primera_epoch − 1,
     start explícito) hasta una respuesta vacía o un error. Por página: las
     velas, la primera y la última epoch. En total: velas, velas alineadas y
     primera epoch. La §0.A vio que count 5000 devolvió 256 velas, 365 días
     justos hacia atrás desde la hora de la corrida.
  b) ALINEACIÓN. Cuántas velas de cada página tienen una epoch que no es
     múltiplo de la granularidad. En la §0.A, la primera vela (1759337176)
     no lo era.
  c) `contracts_for` de BTC, del oro y de frxEURUSD (el control positivo de
     sonda_instrumentos): si hay MULTUP, el `multiplier_range`, y los
     límites de stake que traiga cada ítem MULTUP.
  d) `proposal` MULTUP, que es una COTIZACIÓN, no una compra, en ws/public
     con `underlying_symbol`: ¿responde o exige cuenta?
  e) REST, solo GET: `/trading/v1/options/accounts` con DERIV_API_TOKEN
     como Bearer y `Deriv-App-ID`. ¿Autentica? Solo el `account_type` de
     cada cuenta, con el ID enmascarado. Y
     `/trading/v1/options/legacy/migration-status`. Ningún OTP, ningún POST,
     y un solo pedido por ruta: sin reintentos. Con las credenciales del
     01-oct (app PAT "SPEL TRADER", token con scope `trade`) se espera que
     autentique. Si no, el informe trae el código HTTP y el cuerpo con los
     IDs tapados; un 401 lleva la nota de posible vencimiento del token,
     cuya fecha es DESCONOCIDA (decision-log 2026-10-01). Si Deriv expone
     una fecha de vencimiento en un header o en el cuerpo, va al informe.

══ FUENTES ══

Esquemas oficiales, github.com/deriv-com/deriv-api-schemas, release
`production_v20260901_0` (commit 54e3538):
  · `ticks_history_request`: `end` es string (`latest` o epoch), `start`
    es entero; ninguno de los dos tiene tope documentado.
  · `proposal_request`: requiere `proposal` (1), `contract_type`,
    `currency` y `underlying_symbol`. `auth_required: 0`.
  · `contracts_for_response`: `multiplier_range`, `default_stake`, y
    `min_stake`/`max_stake` "[Only for turbos options]". Para MULTUP el
    stake mínimo real sale de `proposal.validation_params` (ver
    sonda_instrumentos), y se reporta si aparece.
  · `get_accounts_request` y `legacy_migration_status_request`: headers
    `Authorization: Bearer` y `Deriv-App-ID`. `get_accounts_response`:
    `data[].account_type` ∈ {demo, real}.
  · `rest-api-openapi.json` declara `/accounts` con GET y POST; el POST
    CREA una cuenta y acá no se usa.

[INTERPRETACIÓN] El host REST. El OpenAPI no declara `servers`. Se usa
`https://api.derivws.com` porque `/trading/v1/options/ws/public` está en
el MISMO OpenAPI que `/accounts`, y la §0.A verificó que ese path responde
en `api.derivws.com`.

[INTERPRETACIÓN] El `start` de cada página. El addendum pide "start
explícito" sin dar el valor. Se usa `end − DERIV_MAX_COUNT × granularidad`
(acotado a 0): la ventana que cubriría el count pedido, con las constantes
que ya existen.

[INTERPRETACIÓN] La moneda de la `proposal`. El esquema la exige, y la
regla de sonda_instrumentos es que no se escribe a mano. Sale de la cuenta
DEMO que devuelve `/accounts` (e). Si (e) no autentica o no hay cuenta
demo, la `proposal` no se manda, y el informe dice por qué.

══ CUÁNDO SE PONE ROJO ══

Solo por plomería o por fuga: falta DERIV_APP_ID o DERIV_API_TOKEN en el
job, aparece un secreto en el informe (se buscan en los strings, no en los
hashes ni en las épocas, donde un App ID numérico podría aparecer por
azar), o el handshake del WS público (que el registro da por DISPONIBLE)
falla. Todo lo que Deriv conteste, incluido
un error, es el resultado de la sonda y va al informe sin poner el job en
rojo. Si falta el token, la parte WS se corre igual y el job falla al final.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Awaitable, Callable, Optional

import httpx
import pytest

from ingestion.adapters import DERIV_MAX_COUNT
from ingestion.deriv_publico import CanalPublico, desalineadas, profundidad  # noqa: F401
from ingestion.deriv_ws import TIMEOUT_RESPUESTA_S, nombre_del_mensaje
from ingestion.sonda_instrumentos import (
    CONTROL_POSITIVO,
    codigo,
    resumir_contratos,
    seleccionar,
)
from core.preregistro_h1 import REJILLA_H1, historia_requerida
from ingestion.velas import GRANULARIDAD_DIARIA
from tests.test_deriv_endpoints_live import (
    _EXTRACTO,
    ENDPOINT_PUBLICO_NUEVO,
    _http_de,
    texto_libre,
)

#: [INTERPRETACIÓN] Ver el docstring: el host del ws/public verificado.
BASE_REST = "https://api.derivws.com"

#: Las ÚNICAS rutas REST que la sonda puede pedir, y solo con GET.
RUTAS_REST_PERMITIDAS: frozenset[str] = frozenset({
    "/trading/v1/options/accounts",
    "/trading/v1/options/legacy/migration-status",
})

#: El umbral del addendum, ítem 8: si la historia diaria de BTC no llega a
#: esto ni paginando, se detiene todo y decide el Admin. Es
#: `historia_requerida(320)` de H3 (320 + 756, core/preregistro_h3.py en el
#: PR #33, que todavía no está en esta rama).
UMBRAL_ADMIN_VELAS_BTC = 1076

#: Los tres umbrales que el Admin aprobó el 01-oct para leer el informe. Los
#: de H1 salen de su módulo, que sí está en esta rama.
UMBRALES_VELAS_BTC: dict[str, int] = {
    "H1 mínimo": historia_requerida(min(REJILLA_H1)),
    "H3 completo (condición de parada, ítem 8)": UMBRAL_ADMIN_VELAS_BTC,
    "H1 completo": historia_requerida(max(REJILLA_H1)),
}

#: Qué dice el informe ante un 401 en la parte autenticada. El token no
#: tiene fecha de vencimiento conocida (decision-log 2026-10-01): un 401 se
#: lee como posible vencimiento, no como un fallo del código.
NOTA_401 = ("posible vencimiento del DERIV_API_TOKEN: su fecha de vencimiento es "
            "DESCONOCIDA (decision-log 2026-10-01). No es un fallo de código.")

#: GET async: (url, headers) -> (status HTTP, headers de la respuesta, cuerpo).
Getter = Callable[[str, dict], Awaitable[tuple[int, dict, str]]]


def enmascarar_id(cuenta: str) -> str:
    """Los dígitos de un account_id, tapados. Queda el prefijo de letras,
    que dice la clase de cuenta y no la cuenta."""
    return re.sub(r"\d", "*", str(cuenta))


def enmascarar_ids(texto: str) -> str:
    """Toda tira de 4 o más dígitos, tapada: así se ve un account_id o un
    loginid dentro de un mensaje de error. Lo que queda (códigos, palabras)
    dice por qué falló."""
    return re.sub(r"\d{4,}", lambda m: "*" * len(m.group()), texto)


#: Headers con "expir" en el nombre que NO hablan del token. `Expires` es la
#: cabecera HTTP de caché (RFC 9111 §5.3): en la sonda 2 vino con una fecha
#: de 2016, que es la forma de decir "no cachear" (decision-log 2026-10-05).
CABECERAS_HTTP_DE_CACHE: frozenset[str] = frozenset({"expires"})


def vencimiento_expuesto(headers: dict, datos: Any) -> dict:
    """Todo header o clave JSON cuyo nombre mencione una expiración, con su
    valor, salvo las cabeceras de caché. El OpenAPI oficial (54e3538) no
    documenta ninguno para estas dos rutas; si Deriv manda uno igual, el
    informe lo muestra."""
    out: dict[str, Any] = {}
    for nombre, valor in (headers or {}).items():
        if nombre.lower() in CABECERAS_HTTP_DE_CACHE:
            continue
        if "expir" in nombre.lower():
            out[f"header:{nombre}"] = valor

    def recorrer(x: Any, ruta: str) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                if "expir" in str(k).lower():
                    out[f"json:{ruta}{k}"] = v
                recorrer(v, f"{ruta}{k}.")
        elif isinstance(x, list):
            for v in x:
                recorrer(v, f"{ruta}[].")
    recorrer(datos, "")
    return out


def _limpiador(secretos: tuple[str, ...]) -> Callable[[str], str]:
    def limpiar(texto: str) -> str:
        for s in secretos:
            if s:
                texto = texto.replace(s, "***")
        return texto
    return limpiar


# ═══ e) REST ══════════════════════════════════════════════════════════════

async def _get_httpx(url: str, headers: dict) -> tuple[int, dict, str]:
    async with httpx.AsyncClient(timeout=TIMEOUT_RESPUESTA_S) as cliente:
        r = await cliente.get(url, headers=headers)
        return r.status_code, dict(r.headers), r.text


async def sondear_rest(*, token: str, app_id: str, get: Getter = _get_httpx) -> dict:
    """Las dos rutas de solo lectura, UN pedido cada una: sin reintentos.
    Nunca lanza. Devuelve el informe publicable y, aparte, la moneda de la
    primera cuenta demo activa (no es un secreto; la usa la `proposal` de
    d)."""
    limpiar = _limpiador((token, app_id))
    headers = {"Authorization": f"Bearer {token}", "Deriv-App-ID": app_id}
    informe: dict[str, Any] = {"base": BASE_REST}

    async def pedir(ruta: str) -> tuple[dict, Optional[Any]]:
        if ruta not in RUTAS_REST_PERMITIDAS:
            raise ValueError(f"{ruta}: fuera de las rutas REST de solo lectura")
        entrada: dict[str, Any] = {"ok": False}
        informe[ruta] = entrada
        try:
            status, cabeceras, cuerpo = await get(BASE_REST + ruta, headers)
        except Exception as exc:   # noqa: BLE001 -- se reporta, no se oculta
            entrada["error"] = limpiar(f"{type(exc).__name__}: {exc}")
            return entrada, None
        entrada["http"] = status
        entrada["sha256"] = hashlib.sha256(cuerpo.encode("utf-8")).hexdigest()
        try:
            datos = json.loads(cuerpo)
        except json.JSONDecodeError:
            datos = None
        entrada["vencimiento_expuesto"] = vencimiento_expuesto(cabeceras, datos) or None
        if status != 200:
            # El cuerpo de un rechazo sí va (recortado, limpio y con los IDs
            # tapados): es lo que dice POR QUÉ no autenticó. El de un 200 de
            # /accounts no va nunca: trae IDs y saldos.
            entrada["error"] = enmascarar_ids(limpiar(cuerpo[:_EXTRACTO]))
            if status == 401:
                entrada["nota"] = NOTA_401
            return entrada, None
        entrada["ok"] = True
        return entrada, datos

    moneda_demo: Optional[str] = None
    entrada, datos = await pedir("/trading/v1/options/accounts")
    if datos is not None:
        cuentas = datos.get("data") if isinstance(datos, dict) else None
        if not isinstance(cuentas, list):
            entrada["ok"] = False
            entrada["error"] = "200 sin `data` como lista"
        else:
            entrada["cuentas"] = [{"account_id": enmascarar_id(c.get("account_id", "")),
                                   "account_type": c.get("account_type")}
                                  for c in cuentas]
            demo = [c for c in cuentas if c.get("account_type") == "demo"
                    and c.get("status") == "active" and c.get("currency")]
            moneda_demo = demo[0]["currency"] if demo else None

    entrada, datos = await pedir("/trading/v1/options/legacy/migration-status")
    if datos is not None:
        entrada["status"] = datos.get("status") if isinstance(datos, dict) else None

    informe["autentico"] = all(informe[r]["ok"] for r in sorted(RUTAS_REST_PERMITIDAS))
    return {"informe": informe, "moneda_demo": moneda_demo}


# ═══ a)–d) WS público ═════════════════════════════════════════════════════

class _Canal(CanalPublico):
    """Un WS abierto, con el registro de cada mensaje. Cada payload pasa por
    `nombre_del_mensaje()`, la lista blanca de solo lectura de deriv_ws, que
    rechaza `proposal` con `subscribe` y todo lo que abra o cierre un
    contrato. El cuerpo se portó a ingestion/deriv_publico.py (brief del
    Admin del 06-oct-2026 (4)); acá queda la lista blanca de la sonda, leída
    en cada llamada para que un test pueda espiarla."""

    def __init__(self, ws: Any) -> None:
        super().__init__(ws, validar=lambda payload: nombre_del_mensaje(payload))


def _limites_multup(cf: dict) -> list[dict]:
    claves = ("multiplier_range", "default_stake", "min_stake", "max_stake",
              "cancellation_range")
    return [{k: c.get(k) for k in claves if k in c}
            for c in (cf.get("available") or []) if c.get("contract_type") == "MULTUP"]


async def contratos_y_cotizacion(canal: _Canal, simbolo: str,
                                 moneda: Optional[str]) -> dict:
    """c) y d) para un símbolo."""
    out: dict[str, Any] = {"simbolo": simbolo}
    entrada, datos = await canal.pedir({"contracts_for": simbolo})
    out["contracts_for"] = entrada
    if datos is None:
        return out
    cf = datos.get("contracts_for") or {}
    r = resumir_contratos(cf)
    entrada.update(multup=r.multup, multdown=r.multdown,
                   multiplier_range=r.multiplicadores,
                   limites_multup=_limites_multup(cf))
    if not r.multup:
        out["proposal"] = {"sin_enviar": "el instrumento no ofrece MULTUP"}
    elif not moneda:
        out["proposal"] = {"sin_enviar": "sin moneda de una cuenta demo (ver e): "
                                         "no se escribe una a mano"}
    elif r.default_stake is None or not r.multiplicadores:
        out["proposal"] = {"sin_enviar": "contracts_for sin default_stake o "
                                         "multiplier_range para MULTUP"}
    else:
        payload = {"proposal": 1, "contract_type": "MULTUP", "basis": "stake",
                   "amount": r.default_stake, "currency": moneda,
                   "underlying_symbol": simbolo, "multiplier": r.multiplicadores[0]}
        entrada, datos = await canal.pedir(payload)
        entrada["enviado"] = {k: payload[k] for k in ("amount", "currency", "multiplier")}
        if datos is not None:
            prop = datos.get("proposal") or {}
            stake = (prop.get("validation_params") or {}).get("stake")
            entrada.update(validation_params_stake=stake,
                           commission=prop.get("commission"))
        out["proposal"] = entrada
    return out


async def sondear_ws_publico(*, abrir: Callable[[str], Any],
                             moneda: Optional[str]) -> dict:
    """a)–d) sobre ENDPOINT_PUBLICO_NUEVO. Nunca lanza."""
    informe: dict[str, Any] = {"endpoint": ENDPOINT_PUBLICO_NUEVO, "handshake": None}
    try:
        async with abrir(ENDPOINT_PUBLICO_NUEVO) as ws:
            informe["handshake"] = {"ok": True}
            canal = _Canal(ws)
            informe["time"], _ = await canal.pedir({"time": 1})
            entrada, datos = await canal.pedir({"active_symbols": "brief"})
            informe["active_symbols"] = entrada
            if datos is None:
                return informe
            items = datos.get("active_symbols") or []
            sel = seleccionar(items)
            entrada.update(n_simbolos=len(items), mercados=sel.mercados_vistos,
                           control_presente=sel.control_presente,
                           btc=[codigo(i)[0] for i in sel.btc],
                           oro=[codigo(i)[0] for i in sel.oro])
            elegidos = [codigo(i)[0] for i in sel.btc + sel.oro]
            informe["profundidad"] = [await profundidad(canal, s, GRANULARIDAD_DIARIA)
                                      for s in elegidos]
            con_control = elegidos + ([CONTROL_POSITIVO] if sel.control_presente else [])
            informe["contratos"] = [await contratos_y_cotizacion(canal, s, moneda)
                                    for s in con_control]
    except Exception as exc:   # noqa: BLE001
        clave = "handshake" if informe["handshake"] is None else "error_de_conexion"
        informe[clave] = {"ok": False, "http": _http_de(exc),
                          "error": f"{type(exc).__name__}: {exc}"}
    return informe


def veredicto_umbral(ws: dict) -> dict:
    """Ítem 8 y los tres umbrales aprobados: ¿la historia diaria de cada BTC
    llega? Se cuentan las velas ALINEADAS: las desalineadas se descartan en
    la migración. `detener` es la condición del ítem 8 (H3 completo)."""
    btc = set((ws.get("active_symbols") or {}).get("btc") or [])
    return {p["simbolo"]: {
                "total_alineadas": p["total_alineadas"],
                "alcanza": {nombre: p["total_alineadas"] >= u
                            for nombre, u in UMBRALES_VELAS_BTC.items()},
                "detener": p["total_alineadas"] < UMBRAL_ADMIN_VELAS_BTC}
            for p in ws.get("profundidad") or [] if p["simbolo"] in btc}


# ═══ La sonda real: retirada ═════════════════════════════════════════════
#
# Corrió el 01-oct (run 36915129174) y sus resultados están en el
# decision-log del 05-oct. La entrada `live` se retiró para que el job no
# repita pedidos ya registrados; las funciones quedan porque la sonda §0.A-3
# y la §0.A-3b las reutilizan.


# ═══ La sonda misma, offline ══════════════════════════════════════════════

from tests.deriv_falso import DerivFalso  # noqa: E402

_DIA = GRANULARIDAD_DIARIA
_AHORA = 1_790_873_176          # la `time` de la §0.A: no es múltiplo de 86400
_VENTANA = 365 * _DIA           # lo que la §0.A vio por página


def _historia(piso: int, *, ignora_end: bool = False):
    """ticks_history falso: velas diarias alineadas desde `piso`, más una
    primera vela desalineada por página (como la de la §0.A), dentro de
    una ventana de 365 días que termina en `end`."""
    def manejar(p: dict) -> dict:
        fin = _AHORA if ignora_end or p["end"] == "latest" else int(p["end"])
        desde = max(piso, fin - _VENTANA, p.get("start") or 0)
        if desde > fin:
            return {"candles": []}
        alineadas = list(range(-(-desde // _DIA) * _DIA, fin + 1, _DIA))
        epochs = ([desde] if desde % _DIA else []) + alineadas
        return {"candles": [{"epoch": e, "open": 1, "high": 1, "low": 1, "close": 1}
                            for e in epochs]}
    return manejar


def _contratos(*, multup: bool = True):
    def manejar(p: dict) -> dict:
        disp = [{"contract_type": "CALL"}]
        if multup:
            disp.append({"contract_type": "MULTUP", "multiplier_range": [100, 50, 200],
                         "default_stake": 10, "min_stake": None, "max_stake": None})
        return {"contracts_for": {"available": disp}}
    return manejar


def _deriv(**extra) -> DerivFalso:
    base = {
        "time": lambda p: {"time": _AHORA},
        "active_symbols": lambda p: {"active_symbols": [
            {"underlying_symbol": "R_50", "market": "synthetic_index"},
            {"underlying_symbol": "BTCVOL", "market": "synthetic_index"},
            {"underlying_symbol": "frxEURUSD", "market": "forex"},
            {"underlying_symbol": "cryBTCUSD", "market": "cryptocurrency"},
            {"underlying_symbol": "frxXAUUSD", "market": "commodities"}]},
        "ticks_history": _historia(_AHORA - 4 * _VENTANA),
        "contracts_for": _contratos(),
        "proposal": lambda p: {"proposal": {"commission": 0.1,
                                            "validation_params": {"stake": {"min": "1", "max": "2000"}}}},
    }
    base.update(extra)
    return DerivFalso(base)


async def _profundidad(d: DerivFalso) -> dict:
    async with d.connector("wss://x") as ws:
        return await profundidad(_Canal(ws), "cryBTCUSD", _DIA)


async def test_pagina_hacia_atras_hasta_la_respuesta_vacia():
    d = _deriv()
    p = await _profundidad(d)
    pedidos = d.de_tipo("ticks_history")
    assert p["corte"] == "respuesta vacía"
    assert pedidos[0]["end"] == "latest" and "start" not in pedidos[0]
    assert pedidos[0]["count"] == DERIV_MAX_COUNT
    for previa, pedido in zip(p["paginas"], pedidos[1:]):
        fin = previa["primera_epoch"] - 1
        assert pedido["end"] == str(fin), "end = primera_epoch − 1, como string"
        assert pedido["start"] == fin - DERIV_MAX_COUNT * _DIA
    assert p["velas_por_pagina"][-1] == 0
    assert len(p["paginas"]) == 5, "el piso cierra la cuarta ventana; la quinta, vacía"
    assert p["primera_epoch"] == _AHORA - 4 * _VENTANA


async def test_informa_si_cada_pagina_respeta_el_end():
    p = await _profundidad(_deriv())
    assert p["paginas"][0]["respeta_end"] is None, "latest no es un end que respetar"
    assert all(pg["respeta_end"] is True for pg in p["paginas"][1:-1])
    p = await _profundidad(_deriv(ticks_history=_historia(0, ignora_end=True)))
    assert p["paginas"][1]["respeta_end"] is False
    assert p["ultima_epoch"] == _AHORA - _AHORA % _DIA


async def test_profundidad_puede_devolver_las_velas_alineadas():
    velas: dict = {}
    d = _deriv()
    async with d.connector("wss://x") as ws:
        p = await profundidad(_Canal(ws), "cryBTCUSD", _DIA, guardar_velas=velas)
    assert len(velas) == p["total_alineadas"]
    assert all(e % _DIA == 0 for e in velas)
    assert velas[p["ultima_epoch"]]["epoch"] == p["ultima_epoch"]


async def test_el_tope_de_paginas_corta_y_lo_dice():
    d = _deriv()
    async with d.connector("wss://x") as ws:
        p = await profundidad(_Canal(ws), "cryBTCUSD", _DIA, max_paginas=2)
    assert len(p["paginas"]) == 2 and len(d.de_tipo("ticks_history")) == 2
    assert p["corte"].startswith("tope de 2 páginas")


async def test_no_corta_por_pagina_corta():
    """A diferencia de velas.descargar(): 256 velas < 5000 no es el fondo."""
    p = await _profundidad(_deriv())
    assert p["velas_por_pagina"][0] < DERIV_MAX_COUNT
    assert len(p["paginas"]) > 1


async def test_cuenta_las_desalineadas_por_pagina():
    p = await _profundidad(_deriv())
    assert p["paginas"][0]["desalineadas"] == 1
    assert p["paginas"][0]["primera_epoch"] % _DIA != 0
    assert p["total"] - p["total_alineadas"] == sum(
        pg.get("desalineadas", 0) for pg in p["paginas"])


def test_desalineadas_cuenta_las_que_no_son_multiplo():
    assert desalineadas([0, _DIA, _DIA + 1, 3 * _DIA, 7], _DIA) == 2


async def test_corta_si_la_pagina_no_retrocede():
    p = await _profundidad(_deriv(ticks_history=_historia(0, ignora_end=True)))
    assert p["corte"] == "la página no retrocedió"
    assert len(p["paginas"]) == 2


async def test_corta_por_error_y_lo_registra():
    llamadas = []

    def falla_la_segunda(pedido):
        llamadas.append(pedido)
        if len(llamadas) == 2:
            return {"error": {"code": "InvalidStartEnd", "message": "no"}}
        return _historia(0)(pedido)
    p = await _profundidad(_deriv(ticks_history=falla_la_segunda))
    assert p["corte"] == "error"
    assert p["paginas"][-1]["error"]["code"] == "InvalidStartEnd"
    assert p["total"] == p["velas_por_pagina"][0]


async def test_corta_en_la_epoch_cero_sin_pedir_un_end_invalido():
    """El esquema de ticks_history exige `end` con el patrón
    ^(latest|[0-9]{1,10})$: un end negativo sería un pedido mal formado."""
    d = _deriv(ticks_history=_historia(0))
    p = await _profundidad(d)
    assert p["corte"] == "se llegó a la epoch 0"
    assert p["primera_epoch"] == 0
    for pedido in d.de_tipo("ticks_history"):
        assert re.fullmatch(r"latest|[0-9]{1,10}", pedido["end"]), pedido["end"]


async def _cotizar(d: DerivFalso, moneda):
    async with d.connector("wss://x") as ws:
        return await contratos_y_cotizacion(_Canal(ws), "cryBTCUSD", moneda)


async def test_la_proposal_es_multup_con_underlying_symbol_y_sin_subscribe():
    d = _deriv()
    out = await _cotizar(d, "USD")
    [p] = d.de_tipo("proposal")
    assert p["underlying_symbol"] == "cryBTCUSD" and "symbol" not in p
    assert p["contract_type"] == "MULTUP" and p["proposal"] == 1
    assert "subscribe" not in p
    assert p["multiplier"] == 50, "el multiplicador más bajo del rango"
    assert p["amount"] == 10 and p["basis"] == "stake" and p["currency"] == "USD"
    assert out["proposal"]["ok"] is True
    assert out["proposal"]["validation_params_stake"] == {"min": "1", "max": "2000"}
    assert out["contracts_for"]["multiplier_range"] == [50.0, 100.0, 200.0]
    assert out["contracts_for"]["limites_multup"][0]["default_stake"] == 10


async def test_sin_moneda_no_se_manda_la_proposal():
    d = _deriv()
    out = await _cotizar(d, None)
    assert d.de_tipo("proposal") == []
    assert "moneda" in out["proposal"]["sin_enviar"]


async def test_sin_multup_no_se_manda_la_proposal():
    d = _deriv(contracts_for=_contratos(multup=False))
    out = await _cotizar(d, "USD")
    assert d.de_tipo("proposal") == []
    assert out["contracts_for"]["multup"] is False


async def test_una_proposal_rechazada_queda_registrada():
    d = _deriv(proposal=lambda p: {"error": {"code": "AuthorizationRequired",
                                             "message": "Please log in."}})
    out = await _cotizar(d, "USD")
    assert out["proposal"]["ok"] is False
    assert out["proposal"]["error"]["code"] == "AuthorizationRequired"


async def test_la_sonda_ws_elige_por_mercado_y_suma_el_control():
    d = _deriv()
    ws = await sondear_ws_publico(abrir=d.connector, moneda="USD")
    assert d.uri == ENDPOINT_PUBLICO_NUEVO
    assert ws["active_symbols"]["btc"] == ["cryBTCUSD"]
    assert ws["active_symbols"]["oro"] == ["frxXAUUSD"]
    assert [p["simbolo"] for p in ws["profundidad"]] == ["cryBTCUSD", "frxXAUUSD"]
    assert [c["simbolo"] for c in ws["contratos"]] == ["cryBTCUSD", "frxXAUUSD", "frxEURUSD"]
    pedidos = {p["ticks_history"] for p in d.de_tipo("ticks_history")}
    assert pedidos == {"cryBTCUSD", "frxXAUUSD"}, "ningún sintético, aunque diga BTC"


async def test_la_sonda_ws_solo_manda_mensajes_de_lectura(monkeypatch):
    import tests.test_deriv_sonda2_live as mod
    vistos = []
    original = mod.nombre_del_mensaje
    monkeypatch.setattr(mod, "nombre_del_mensaje",
                        lambda payload: vistos.append(payload) or original(payload))
    d = _deriv()
    await sondear_ws_publico(abrir=d.connector, moneda="USD")
    assert vistos == d.enviados_sin_req_id(), "cada payload pasó por la lista blanca"
    assert {next(iter(p)) for p in d.enviados} == {
        "time", "active_symbols", "ticks_history", "contracts_for", "proposal"}


async def test_un_handshake_rechazado_no_lanza():
    import websockets
    from websockets.datastructures import Headers
    from websockets.http11 import Response

    def rechaza(url):
        raise websockets.exceptions.InvalidStatus(Response(520, "x", Headers(), b""))
    ws = await sondear_ws_publico(abrir=rechaza, moneda=None)
    assert ws["handshake"] == {"ok": False, "http": 520, "error": ws["handshake"]["error"]}


# ── e) REST ──────────────────────────────────────────────────────────────

_CUENTAS = {"data": [
    {"account_id": "DOT90004580", "balance": 10000, "currency": "USD", "group": "row",
     "status": "active", "account_type": "demo"},
    {"account_id": "ROT12345678", "balance": 37.5, "currency": "EUR", "group": "row",
     "status": "active", "account_type": "real"}],
    "meta": {"endpoint": "/accounts", "method": "GET", "timing": 1}}


def _getter(respuestas: dict[str, tuple]):
    """respuestas[ruta] = (status, cuerpo) o (status, cuerpo, headers)."""
    vistos: list[tuple[str, dict]] = []

    async def get(url: str, headers: dict) -> tuple[int, dict, str]:
        vistos.append((url, headers))
        status, cuerpo, *cab = respuestas[url.removeprefix(BASE_REST)]
        return (status, cab[0] if cab else {},
                cuerpo if isinstance(cuerpo, str) else json.dumps(cuerpo))
    return get, vistos


async def test_rest_pide_solo_las_dos_rutas_con_bearer_y_app_id():
    get, vistos = _getter({"/trading/v1/options/accounts": (200, _CUENTAS),
                           "/trading/v1/options/legacy/migration-status": (200, {"status": "complete"})})
    r = await sondear_rest(token="TOK", app_id="APP", get=get)
    assert [u for u, _ in vistos] == [BASE_REST + ruta for ruta in (
        "/trading/v1/options/accounts", "/trading/v1/options/legacy/migration-status")]
    assert {u.removeprefix(BASE_REST) for u, _ in vistos} == RUTAS_REST_PERMITIDAS
    for _, h in vistos:
        assert h == {"Authorization": "Bearer TOK", "Deriv-App-ID": "APP"}
    assert r["informe"]["/trading/v1/options/legacy/migration-status"]["status"] == "complete"


async def test_rest_publica_solo_account_type_con_el_id_enmascarado():
    get, _ = _getter({"/trading/v1/options/accounts": (200, _CUENTAS),
                      "/trading/v1/options/legacy/migration-status": (200, {"status": "pending"})})
    r = await sondear_rest(token="TOK", app_id="APP", get=get)
    ep = r["informe"]["/trading/v1/options/accounts"]
    assert ep["cuentas"] == [{"account_id": "DOT********", "account_type": "demo"},
                             {"account_id": "ROT********", "account_type": "real"}]
    texto = json.dumps(r["informe"])
    for dato in ("90004580", "12345678", "10000", "37.5", "row", "EUR"):
        assert dato not in texto, dato
    assert r["moneda_demo"] == "USD", "la de la cuenta DEMO, no la de la real"


async def test_rest_sin_cuenta_demo_no_da_moneda():
    solo_real = {"data": [_CUENTAS["data"][1]]}
    get, _ = _getter({"/trading/v1/options/accounts": (200, solo_real),
                      "/trading/v1/options/legacy/migration-status": (200, {"status": "complete"})})
    assert (await sondear_rest(token="TOK", app_id="APP", get=get))["moneda_demo"] is None


async def test_rest_un_rechazo_registra_el_motivo_sin_secretos():
    get, _ = _getter({"/trading/v1/options/accounts": (401, '{"error": "bad token q7Xk9 for 31337app"}'),
                      "/trading/v1/options/legacy/migration-status": (403, "forbidden")})
    r = await sondear_rest(token="q7Xk9", app_id="31337app", get=get)
    ep = r["informe"]["/trading/v1/options/accounts"]
    assert ep["ok"] is False and ep["http"] == 401
    assert "q7Xk9" not in json.dumps(r) and "31337app" not in json.dumps(r)
    assert "bad token" in ep["error"]
    assert r["moneda_demo"] is None


async def test_rest_una_excepcion_de_red_no_lanza():
    async def get(url, headers):
        raise httpx.ConnectError("sin red hacia TOK")
    r = await sondear_rest(token="TOK", app_id="APP", get=get)
    ep = r["informe"]["/trading/v1/options/accounts"]
    assert ep["ok"] is False and "ConnectError" in ep["error"] and "TOK" not in ep["error"]


def test_enmascarar_no_deja_digitos():
    assert enmascarar_id("DOT90004580") == "DOT********"
    assert not re.search(r"\d", enmascarar_id("CR1234VRTC99"))


def test_texto_libre_ve_los_strings_y_no_los_hashes_ni_las_epocas():
    informe = {"endpoint": "wss://x?app_id=31337", "a": {"sha256": "ab31337cd"},
               "paginas": [{"end": "1790031337", "start": 1790031337,
                            "error": "falla 31337"}]}
    t = texto_libre(informe)
    assert "app_id=31337" in t and "falla 31337" in t
    assert "ab31337cd" not in t and "1790031337" not in t


def test_los_tres_umbrales_aprobados():
    assert list(UMBRALES_VELAS_BTC.values()) == [1058, 1076, 2608]


@pytest.mark.parametrize("alineadas, alcanza, detener", [
    (2608, [True, True, True], False),
    (2607, [True, True, False], False),
    (1076, [True, True, False], False),
    (1075, [True, False, False], True),
    (1058, [True, False, False], True),
    (1057, [False, False, False], True),
])
def test_veredicto_por_umbral_solo_mira_btc(alineadas, alcanza, detener):
    ws = {"active_symbols": {"btc": ["cryBTCUSD"]}, "profundidad": [
        {"simbolo": "cryBTCUSD", "total_alineadas": alineadas},
        {"simbolo": "frxXAUUSD", "total_alineadas": 5000}]}
    v = veredicto_umbral(ws)
    assert list(v) == ["cryBTCUSD"]
    assert list(v["cryBTCUSD"]["alcanza"].values()) == alcanza
    assert v["cryBTCUSD"]["detener"] is detener
    assert v["cryBTCUSD"]["total_alineadas"] == alineadas


async def test_rest_un_401_se_reporta_como_posible_vencimiento_sin_reintentar():
    cuerpo = '{"error": {"code": "InvalidToken", "message": "token for CR90004580 expired"}}'
    get, vistos = _getter({"/trading/v1/options/accounts": (401, cuerpo),
                           "/trading/v1/options/legacy/migration-status": (401, cuerpo)})
    r = await sondear_rest(token="TOK", app_id="APP", get=get)
    ep = r["informe"]["/trading/v1/options/accounts"]
    assert ep["http"] == 401 and ep["nota"] == NOTA_401
    assert "CR********" in ep["error"] and "90004580" not in json.dumps(r)
    assert "InvalidToken" in ep["error"]
    assert len(vistos) == 2, "un pedido por ruta: sin reintentos"
    assert r["informe"]["autentico"] is False


async def test_rest_un_403_no_lleva_la_nota_de_vencimiento():
    get, _ = _getter({"/trading/v1/options/accounts": (403, "forbidden"),
                      "/trading/v1/options/legacy/migration-status": (200, {"status": "complete"})})
    r = await sondear_rest(token="TOK", app_id="APP", get=get)
    assert "nota" not in r["informe"]["/trading/v1/options/accounts"]
    assert r["informe"]["autentico"] is False


async def test_rest_con_las_dos_rutas_en_200_autentico():
    get, _ = _getter({"/trading/v1/options/accounts": (200, _CUENTAS),
                      "/trading/v1/options/legacy/migration-status": (200, {"status": "complete"})})
    r = await sondear_rest(token="TOK", app_id="APP", get=get)
    assert r["informe"]["autentico"] is True
    assert r["informe"]["/trading/v1/options/accounts"]["vencimiento_expuesto"] is None


async def test_rest_reporta_un_vencimiento_si_deriv_lo_expone():
    con_fecha = {**_CUENTAS, "meta": {**_CUENTAS["meta"], "token_expires_at": 1798761600}}
    get, _ = _getter({
        "/trading/v1/options/accounts": (200, con_fecha),
        "/trading/v1/options/legacy/migration-status": (
            200, {"status": "complete"}, {"X-Token-Expiry": "2027-01-01"})})
    r = await sondear_rest(token="TOK", app_id="APP", get=get)
    assert r["informe"]["/trading/v1/options/accounts"]["vencimiento_expuesto"] == {
        "json:meta.token_expires_at": 1798761600}
    assert r["informe"]["/trading/v1/options/legacy/migration-status"]["vencimiento_expuesto"] == {
        "header:X-Token-Expiry": "2027-01-01"}


@pytest.mark.parametrize("nombre", ["expires", "Expires", "EXPIRES"])
def test_la_cabecera_http_expires_no_es_el_vencimiento_del_token(nombre):
    """Lo que la sonda 2 reportó como `header:expires` era la cabecera de
    caché, con fecha de 2016."""
    assert vencimiento_expuesto({nombre: "Mon, 03 Oct 2016 19:33:52 GMT",
                                 "Cache-Control": "no-store"}, {}) == {}
    assert vencimiento_expuesto({nombre: "x", "X-Token-Expiry": "2027-01-01"}, {}) == {
        "header:X-Token-Expiry": "2027-01-01"}


def test_vencimiento_expuesto_recorre_listas_y_no_inventa():
    assert vencimiento_expuesto({}, {"data": [{"a": 1}]}) == {}
    assert vencimiento_expuesto({}, {"data": [{"expires": 5}]}) == {"json:data.[].expires": 5}


def test_enmascarar_ids_tapa_tiras_largas_de_digitos():
    assert enmascarar_ids("CR90004580 y 401 y VRTC1234") == "CR******** y 401 y VRTC****"


def test_el_umbral_es_el_de_h3_con_la_rejilla_completa():
    """1.076 = 320 + 756: historia_requerida(320) de core/preregistro_h3.py
    (PR #33, todavía no en esta rama). Si H3 cambia, esto se revisa."""
    assert UMBRAL_ADMIN_VELAS_BTC == 320 + 756
