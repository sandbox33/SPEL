"""
tests/test_deriv_endpoints_live.py
====================================
Sonda de endpoints de Deriv (Brief final v3, PR-0 §0.A). Contesta UNA
pregunta antes de seguir con el PR #31: ¿el endpoint legacy que usa todo el
código responde, o hay que migrar?

Solo lectura y sin token. Para cada endpoint: handshake, `time`,
`active_symbols`, una página diaria de `ticks_history` de un símbolo NO
sintético sacado de `active_symbols` (ninguno fijo en el código) y
`contracts_for` sobre ese mismo símbolo. El informe va al log del job como
JSON, pase lo que pase: el código HTTP o el error de cada mensaje y un
extracto de la respuesta cruda. Sin ningún secreto: el `app_id` se enmascara.

══ FUENTES DE CADA ENDPOINT ══

LEGACY -- `wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}`
  El de `DERIV_WS_ENDPOINT` (ingestion/adapters.py). Formato de mensaje: los
  esquemas `config/v3/*/send.json` de github.com/deriv-com/deriv-api-docs,
  leídos el 25-sep-2026.

NUEVO, WS PÚBLICO -- `wss://api.derivws.com/trading/v1/options/ws/public`
  Confirmado el 29-sep-2026 en el repo OFICIAL de esquemas de Deriv,
  github.com/deriv-com/deriv-api-schemas, release `production_v20260901_0`
  (commit 54e3538, 7-sep-2026):
    · `rest-api-openapi.json` declara `GET /trading/v1/options/ws/public`,
      "Open a public WebSocket connection (no authentication)": "No
      authentication or OTP is required", y en este canal no están las
      acciones de cuenta.
    · `ws_public_request.schema.json`: "No authentication, OTP, or query
      parameters are required." Por eso esta URL no lleva `app_id`.
    · El host sale del ejemplo oficial de `websocket_response.schema.json`
      (`wss://api.derivws.com/trading/v1/options/ws/demo?otp=...`); el
      OpenAPI no declara `servers`.
    · Mensajes: `time_request`, `active_symbols_request`,
      `ticks_history_request` y `contracts_for_request` usan las mismas
      claves que la legacy (`{"time": 1}`, `{"active_symbols": "brief"}`,
      ...). La respuesta de `active_symbols` nombra el código
      `underlying_symbol`, no `symbol`: se aceptan los dos.
  `developers.deriv.com/docs/options/ws-public/` confirma la misma URL según
  el buscador, pero está bloqueado desde el sandbox y no se leyó.

══ POR QUÉ SE CONSTRUYE ESTO ANTES QUE NADA ══

Según la documentación actual de Deriv, las rutas legacy "solo sirven
historial", y un PR público de terceros (nuchukwuma/trading-bot#4) reporta
que "every handshake now gets HTTP 520". Nada de eso se verificó: el
sandbox no llega a Deriv (el proxy devuelve 403). Esta sonda corre en el
runner de GitHub, que sí llega.

══ CÓMO SE CORRE ══

Actions -> SPEL Live Tests -> Run workflow, eligiendo la rama del PR #31.
Los dos tests `live` se saltean fuera de ahí: exigen SPEL_EXPECT_SECRETS=1,
que solo pone live-tests.yml. Ahí, sin DERIV_APP_ID, el de la legacy FALLA
en vez de saltearse: es una falla de plomería, no una ausencia esperada.

Los tests de abajo sin marca `live` prueban la sonda misma contra un Deriv
falso, offline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from typing import Any, Callable, Optional

import pytest
import websockets
from websockets.datastructures import Headers
from websockets.http11 import Response

from ingestion.adapters import DERIV_MAX_COUNT, DERIV_WS_ENDPOINT, DerivAdapter
from ingestion.deriv_publico import ENDPOINT_PUBLICO
from ingestion.deriv_ws import TIMEOUT_RESPUESTA_S, nombre_del_mensaje
from ingestion.sonda_instrumentos import codigo, es_sintetico
from ingestion.source_registry import EndpointState, load_registry
from tests.deriv_falso import DerivFalso

#: Portado a ingestion/deriv_publico.py (brief del Admin del 06-oct-2026 (4)).
ENDPOINT_PUBLICO_NUEVO = ENDPOINT_PUBLICO

#: Caracteres de la respuesta cruda que van al informe por mensaje.
_EXTRACTO = 400


def _http_de(exc: BaseException) -> Optional[int]:
    """El código HTTP de un handshake rechazado. websockets >= 14 lo pone en
    `exc.response.status_code` (InvalidStatus); las versiones viejas, en
    `exc.status_code` (InvalidStatusCode)."""
    respuesta = getattr(exc, "response", None)
    return getattr(respuesta, "status_code", None) or getattr(exc, "status_code", None)


def elegir_simbolo(items: list[dict]) -> Optional[str]:
    """El primer símbolo NO sintético en orden alfabético. Sale de la
    respuesta, no del código: ningún símbolo fijo."""
    reales = sorted(codigo(i)[0] for i in items if not es_sintetico(i))
    return reales[0] if reales else None


async def sondear_endpoint(
    url: str, *, abrir: Callable[[str], Any], secretos: tuple[str, ...] = (),
) -> dict:
    """Recorre handshake y los cuatro mensajes. Nunca lanza: todo lo que
    pasa queda en el informe, que es lo que el Admin necesita leer."""
    def limpiar(texto: str) -> str:
        for s in secretos:
            if s:
                texto = texto.replace(s, "***")
        return texto

    informe: dict[str, Any] = {"endpoint": limpiar(url), "handshake": None,
                               "mensajes": {}, "simbolo": None}
    try:
        async with abrir(url) as ws:
            informe["handshake"] = {"ok": True, "http": 101}
            req_id = 0

            async def pedir(nombre: str, payload: dict) -> Optional[dict]:
                nonlocal req_id
                nombre_del_mensaje(payload)   # la lista blanca de solo lectura
                req_id += 1
                entrada: dict[str, Any] = {"ok": False}
                informe["mensajes"][nombre] = entrada
                try:
                    await ws.send(json.dumps({**payload, "req_id": req_id}))
                    crudo = await asyncio.wait_for(ws.recv(), TIMEOUT_RESPUESTA_S)
                except Exception as exc:   # noqa: BLE001 -- se reporta, no se oculta
                    entrada["error"] = limpiar(f"{type(exc).__name__}: {exc}")
                    return None
                entrada["sha256"] = hashlib.sha256(crudo.encode("utf-8")).hexdigest()
                entrada["extracto"] = limpiar(crudo[:_EXTRACTO])
                try:
                    datos = json.loads(crudo)
                except json.JSONDecodeError as exc:
                    entrada["error"] = f"no es JSON: {exc}"
                    return None
                entrada["msg_type"] = datos.get("msg_type")
                if datos.get("error"):
                    entrada["error"] = {"code": datos["error"].get("code"),
                                        "message": limpiar(str(datos["error"].get("message")))}
                    return None
                entrada["ok"] = True
                return datos

            if await pedir("time", {"time": 1}) is None:
                return informe
            a = await pedir("active_symbols", {"active_symbols": "brief"})
            if a is None:
                return informe
            items = a.get("active_symbols") or []
            informe["mensajes"]["active_symbols"]["n_simbolos"] = len(items)
            simbolo = elegir_simbolo(items)
            informe["simbolo"] = simbolo
            if simbolo is None:
                informe["mensajes"]["active_symbols"]["ok"] = False
                informe["mensajes"]["active_symbols"]["error"] = "ningún símbolo no sintético"
                return informe
            h = await pedir("ticks_history", {
                "ticks_history": simbolo, "adjust_start_time": 1, "end": "latest",
                "count": DERIV_MAX_COUNT, "style": "candles", "granularity": 86400})
            if h is not None:
                velas = h.get("candles") or []
                informe["mensajes"]["ticks_history"].update(
                    n_velas=len(velas),
                    primera_epoch=velas[0].get("epoch") if velas else None,
                    ultima_epoch=velas[-1].get("epoch") if velas else None)
            c = await pedir("contracts_for", {"contracts_for": simbolo})
            if c is not None:
                disp = (c.get("contracts_for") or {}).get("available") or []
                informe["mensajes"]["contracts_for"]["tipos"] = sorted(
                    {d.get("contract_type") for d in disp if d.get("contract_type")})
    except Exception as exc:   # noqa: BLE001
        if informe["handshake"] is None:
            informe["handshake"] = {"ok": False, "http": _http_de(exc),
                                    "error": limpiar(f"{type(exc).__name__}: {exc}")}
        else:
            informe["error_de_conexion"] = limpiar(f"{type(exc).__name__}: {exc}")
    return informe


def respondio_a_todo(informe: dict) -> bool:
    """Los cuatro mensajes respondieron sin error. No se mira el handshake
    aparte: sin handshake no se envía ningún mensaje, así que chequearlo
    además era una condición muerta (un mutante que la quitaba sobrevivía)."""
    esperados = ("time", "active_symbols", "ticks_history", "contracts_for")
    return all(informe["mensajes"].get(m, {}).get("ok") for m in esperados)


#: Claves cuyo valor es un hash o una época: dígitos y hexadecimales que no
#: salen de ningún secreto y en los que un App ID numérico corto podría
#: aparecer por azar, poniendo el job en rojo sin fuga.
_CLAVES_SIN_TEXTO = frozenset({"sha256", "end"})


def texto_libre(x: Any) -> str:
    """Los strings del informe donde podría filtrarse un secreto: todos,
    salvo los valores de _CLAVES_SIN_TEXTO. Los números no entran: ningún
    secreto llega al informe como número."""
    partes: list[str] = []

    def recorrer(v: Any, clave: Optional[str]) -> None:
        if isinstance(v, dict):
            for k, w in v.items():
                partes.append(str(k))
                recorrer(w, k)
        elif isinstance(v, list):
            for w in v:
                recorrer(w, clave)
        elif isinstance(v, str) and clave not in _CLAVES_SIN_TEXTO:
            partes.append(v)
    recorrer(x, None)
    return "\n".join(partes)


def estado_medido(informe: dict) -> str:
    """El estado de endpoint (EndpointState) que dice el informe: disponible
    si respondió a los cuatro mensajes, no disponible en cualquier otro caso.
    Un endpoint que responde a medias no sirve para la ingesta."""
    return (EndpointState.DISPONIBLE if respondio_a_todo(informe)
            else EndpointState.NO_DISPONIBLE)


def _publicar(capsys, informe: dict) -> None:
    """Al log del job SIEMPRE, no solo si el test falla: pytest captura la
    salida de los que pasan, y el informe es el entregable en los dos casos."""
    with capsys.disabled():
        print("\n=== SONDA DE ENDPOINT DERIV ===")
        print(json.dumps(informe, indent=2, ensure_ascii=False))


_SOLO_EN_LIVE_TESTS = pytest.mark.skipif(
    os.environ.get("SPEL_EXPECT_SECRETS") != "1",
    reason="solo corre en live-tests.yml (SPEL_EXPECT_SECRETS=1): toca la red de Deriv",
)


# ═══ Las dos sondas reales ════════════════════════════════════════════════

@pytest.mark.live
@_SOLO_EN_LIVE_TESTS
async def test_live_endpoint_legacy(capsys):
    """REGISTRA el estado de la legacy, ya no exige que responda: desde el
    2026-10-01 devuelve HTTP 520 (decision-log), y un test que falla todas
    las corridas por un hecho ya registrado deja el job rojo para siempre,
    con lo que el rojo deja de significar algo. Se publica el estado medido
    junto al del registro; si difieren, hay que actualizar el registro, pero
    eso lo decide quien lee el informe, no este test.

    Se retira con la migración de `deriv_ws.py` a ws/public: con ningún
    código abriendo la legacy, no queda nada que medir."""
    app_id = os.environ.get("DERIV_APP_ID")
    assert app_id, ("SPEL_EXPECT_SECRETS=1 pero DERIV_APP_ID no llegó al job: "
                    "revisar el env: de live-tests.yml y el nombre del secret.")
    url = DERIV_WS_ENDPOINT.format(app_id=app_id)
    informe = await sondear_endpoint(url, abrir=DerivAdapter._default_connector,
                                     secretos=(app_id,))
    informe["estado_medido"] = estado_medido(informe)
    informe["estado_registrado"] = load_registry().estado_de_endpoint(
        "deriv", url.split("?")[0])
    _publicar(capsys, informe)
    assert app_id not in texto_libre(informe)


@pytest.mark.live
@_SOLO_EN_LIVE_TESTS
async def test_live_endpoint_publico_nuevo(capsys):
    informe = await sondear_endpoint(ENDPOINT_PUBLICO_NUEVO,
                                     abrir=DerivAdapter._default_connector,
                                     secretos=(os.environ.get("DERIV_APP_ID") or "",))
    _publicar(capsys, informe)
    assert respondio_a_todo(informe), "el WS público nuevo NO respondió a todo: ver el informe"


# ═══ La sonda misma, offline ══════════════════════════════════════════════

def _deriv_que_responde(**extra):
    base = {
        "time": lambda p: {"time": 1_790_000_000},
        "active_symbols": lambda p: {"active_symbols": [
            {"underlying_symbol": "R_50", "market": "synthetic_index"},
            {"underlying_symbol": "frxEURUSD", "market": "forex"},
            {"underlying_symbol": "cryBTCUSD", "market": "cryptocurrency"}]},
        "ticks_history": lambda p: {"candles": [
            {"epoch": 1_789_900_000 - 86400 * k, "open": 1.0, "high": 1.1,
             "low": 0.9, "close": 1.05} for k in (2, 1, 0)]},
        "contracts_for": lambda p: {"contracts_for": {"available": [
            {"contract_type": "MULTUP"}, {"contract_type": "CALL"}]}},
    }
    base.update(extra)
    return DerivFalso(base)


async def test_la_sonda_recorre_los_cuatro_mensajes_con_un_simbolo_de_la_api():
    d = _deriv_que_responde()
    informe = await sondear_endpoint("wss://x?app_id=SECRETO", abrir=d.connector,
                                     secretos=("SECRETO",))
    assert respondio_a_todo(informe)
    assert informe["simbolo"] == "cryBTCUSD", "el primero no sintético, en orden"
    assert d.de_tipo("ticks_history")[0]["ticks_history"] == "cryBTCUSD"
    assert d.de_tipo("contracts_for")[0]["contracts_for"] == "cryBTCUSD"
    assert informe["mensajes"]["ticks_history"]["n_velas"] == 3
    assert informe["mensajes"]["contracts_for"]["tipos"] == ["CALL", "MULTUP"]


async def test_el_app_id_no_aparece_en_el_informe():
    d = _deriv_que_responde(time=lambda p: {"error": {
        "code": "InvalidAppID", "message": "app_id SECRETO no vale"}})
    informe = await sondear_endpoint("wss://x?app_id=SECRETO", abrir=d.connector,
                                     secretos=("SECRETO",))
    assert "SECRETO" not in json.dumps(informe)
    assert informe["endpoint"] == "wss://x?app_id=***"


async def test_un_error_de_deriv_queda_registrado_con_su_codigo():
    d = _deriv_que_responde(contracts_for=lambda p: {"error": {
        "code": "MarketIsClosed", "message": "cerrado"}})
    informe = await sondear_endpoint("wss://x", abrir=d.connector)
    assert not respondio_a_todo(informe)
    assert informe["mensajes"]["contracts_for"]["error"]["code"] == "MarketIsClosed"
    assert informe["mensajes"]["ticks_history"]["ok"] is True


async def test_un_handshake_rechazado_registra_el_codigo_http():
    """Lo que reporta la fuente secundaria: HTTP 520 en el handshake. Se
    construye con la excepción REAL de websockets, no con un doble."""
    def rechaza(url):
        raise websockets.exceptions.InvalidStatus(
            Response(520, "Origin Error", Headers(), b""))
    informe = await sondear_endpoint("wss://x", abrir=rechaza)
    assert informe["handshake"]["ok"] is False
    assert informe["handshake"]["http"] == 520
    assert informe["mensajes"] == {}
    assert not respondio_a_todo(informe)


async def test_sin_simbolo_no_sintetico_no_se_pide_historia():
    d = _deriv_que_responde(active_symbols=lambda p: {"active_symbols": [
        {"symbol": "R_50", "market": "synthetic_index"}]})
    informe = await sondear_endpoint("wss://x", abrir=d.connector)
    assert informe["simbolo"] is None
    assert d.de_tipo("ticks_history") == []
    assert not respondio_a_todo(informe)


def test_cada_mensaje_pasa_por_la_lista_blanca(monkeypatch):
    """No alcanza con ver qué salió: cada mensaje tiene que pasar por
    nombre_del_mensaje(), que es lo que rechazaría uno de orden."""
    import tests.test_deriv_endpoints_live as mod
    vistos = []
    original = mod.nombre_del_mensaje
    monkeypatch.setattr(mod, "nombre_del_mensaje",
                        lambda payload: vistos.append(next(iter(payload))) or original(payload))
    asyncio.run(sondear_endpoint("wss://x", abrir=_deriv_que_responde().connector))
    assert vistos == ["time", "active_symbols", "ticks_history", "contracts_for"]


def test_la_sonda_solo_manda_mensajes_de_la_lista_blanca():
    """Los cuatro pasan por nombre_del_mensaje(): si alguien agrega un
    quinto que no está en la lista blanca, esto falla antes de la red."""
    d = _deriv_que_responde()
    asyncio.run(sondear_endpoint("wss://x", abrir=d.connector))
    assert {next(iter(p)) for p in d.enviados} == {
        "time", "active_symbols", "ticks_history", "contracts_for"}


async def test_el_estado_medido_sale_del_informe():
    ok = await sondear_endpoint("wss://x", abrir=_deriv_que_responde().connector)
    assert estado_medido(ok) == EndpointState.DISPONIBLE

    def rechaza(url):
        raise websockets.exceptions.InvalidStatus(
            Response(520, "Origin Error", Headers(), b""))
    muerto = await sondear_endpoint("wss://x", abrir=rechaza)
    assert estado_medido(muerto) == EndpointState.NO_DISPONIBLE

    a_medias = await sondear_endpoint("wss://x", abrir=_deriv_que_responde(
        contracts_for=lambda p: {"error": {"code": "X", "message": "x"}}).connector)
    assert estado_medido(a_medias) == EndpointState.NO_DISPONIBLE


def test_la_sonda_de_la_legacy_no_exige_que_responda():
    """Ítem f del addendum del 29-sep: el job live tiene que poder salir
    verde con la legacy muerta. Se mira el código del test, porque fuera de
    live-tests.yml no corre."""
    import ast
    import inspect
    fuente = inspect.getsource(test_live_endpoint_legacy)
    llamadas = {n.func.id for n in ast.walk(ast.parse(fuente))
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    asserts = [ast.unparse(n.test) for n in ast.walk(ast.parse(fuente))
               if isinstance(n, ast.Assert)]
    assert "estado_medido" in llamadas
    assert not any("respondio_a_todo" in a or "estado_medido" in a for a in asserts), asserts
