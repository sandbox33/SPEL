"""
tests/test_integracion_demo_ejecucion.py
==========================================
integracion_demo/otp.py, conexion.py y ejecucion.py contra el Deriv falso
(brief del Admin del 06-oct-2026 (3), punto 5). Offline: no hay red.

Lo que el brief pide probar, y dónde:
  · sin SL no hay buy ........................ test_sin_stop_loss_no_hay_buy*
  · cuenta real no recibe OTP ................ test_una_cuenta_*_no_recibe_otp
  · URL fuera de /ws/demo no conecta ......... test_una_url_fuera_de_ws_demo_*
  · r_multiple usa R_usd ..................... test_la_operacion_entera_*,
                                               y tests/test_integracion_demo_registro.py
  · outcome desconocido sin evidencia ........ test_*_es_desconocido*
  · la reconexión pide OTP nuevo ............. test_la_reconexion_pide_un_otp_nuevo
  · la lista blanca rechaza todo lo demás .... test_la_lista_blanca_*
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from integracion_demo import otp as otp_mod
from integracion_demo.conexion import ConexionDemo
from integracion_demo.ejecucion import (
    MENSAJES_PERMITIDOS,
    ConexionPerdidaError,
    Contexto,
    Ejecutor,
    MensajeNoPermitidoError,
    NoConectadoError,
    Orden,
    OrdenSinStopLossError,
    validar_mensaje,
)
from integracion_demo.otp import (
    ENDPOINT_DEMO,
    CuentaNoDemoError,
    OtpNoEmitidoError,
    UrlNoDemoError,
    url_demo_nueva,
)
from integracion_demo.registro import Registro, leer_crudo, leer_registro
from tests.deriv_falso import DerivFalso

_PATH_DEMO = "/trading/v1/options/ws/demo"
_CTX = Contexto(experiment_id="test", account_id="DOT********", account_type="demo",
                ws_path=_PATH_DEMO, currency="USD")
_ORDEN = Orden(underlying_symbol="frxXAUUSD", contract_type="MULTUP", stake=1.0,
               multiplier=100.0, stop_loss=0.10)
_REAL_PATH = ENDPOINT_DEMO.rsplit("/", 1)[0] + "/re" + "al"


# ═══ El Deriv falso, con contrato ═════════════════════════════════════════

class _Falso(DerivFalso):
    """DerivFalso con un contrato que vive: abierto hasta el sell, y que
    puede cortar el socket después de un mensaje dado."""

    def __init__(self, *, cortar: tuple[str, ...] = (), extra: dict | None = None,
                 cerrar_solo: bool = False):
        self.vendido = cerrar_solo
        self.cortar = list(cortar)
        self._cortado = False
        m = {"proposal": self._proposal, "buy": self._buy, "sell": self._sell,
             "proposal_open_contract": self._poc, "ping": lambda p: {"ping": "pong"},
             "profit_table": lambda p: {"profit_table": {"count": 0, "transactions": []}},
             "statement": lambda p: {"statement": {"count": 0, "transactions": []}},
             "contract_update": self._update}
        m.update(extra or {})
        super().__init__(m)

    @staticmethod
    def _proposal(p):
        return {"proposal": {"spot": 2399.90, "commission": "0.02",
                             "validation_params": {"stake": {"min": "1.00"}}}}

    @staticmethod
    def _buy(p):
        return {"buy": {"contract_id": 11, "transaction_id": 501, "buy_price": 1.0,
                        "purchase_time": 100, "start_time": 100, "balance_after": 9999.0,
                        "longcode": "x", "payout": 0, "shortcode": "MULTUP_X"}}

    def _sell(self, p):
        self.vendido = True
        return {"sell": {"contract_id": p["sell"], "transaction_id": 502, "sold_for": 1.03,
                         "balance_after": 10000.03, "reference_id": 501}}

    def _update(self, p):
        return {"contract_update": {"stop_loss": {"order_amount": -p["limit_order"]["stop_loss"],
                                                  "value": "2398.00"}}}

    def _poc(self, p):
        base = {"contract_id": p["contract_id"], "contract_type": "MULTUP",
                "underlying_symbol": "frxXAUUSD", "currency": "USD", "entry_spot": "2400.00",
                "commission": "0.02", "purchase_time": 100, "current_spot": 2401.30,
                "transaction_ids": {"buy": 501},
                "limit_order": {"stop_loss": {"order_amount": -0.10, "value": "2397.80"},
                                "stop_out": {"order_amount": -1.0, "value": "2376.48"}}}
        if not self.vendido:
            return {"proposal_open_contract": {**base, "status": "open", "is_sold": 0,
                                               "profit": "0.04"}}
        return {"proposal_open_contract": {
            **base, "status": "won", "is_sold": 1, "exit_spot": "2401.20", "profit": "0.03",
            "sell_time": 160, "transaction_ids": {"buy": 501, "sell": 502}}}

    async def send(self, texto: str) -> None:
        await super().send(texto)
        tipo = next(iter(json.loads(texto)))
        if tipo in self.cortar:
            self.cortar.remove(tipo)
            self._pendientes.clear()
            self._cortado = True

    async def recv(self) -> str:
        if self._cortado:
            self._cortado = False
            raise ConnectionError("el servidor cerró el socket")
        return await super().recv()


class _Otps:
    """url_nueva falsa: un OTP distinto por llamada."""

    def __init__(self, url=None):
        self.n = 0
        self.url = url

    async def __call__(self) -> str:
        self.n += 1
        return self.url or f"{ENDPOINT_DEMO}?otp=otp{self.n}"


def _armar(tmp_path: Path, falso: _Falso | None = None, *, url=None, **kw):
    falso = falso or _Falso()
    abiertas: list[str] = []

    def abrir(u):
        abiertas.append(u)
        return falso.connector(u)
    registro = Registro(tmp_path)
    otps = _Otps(url)
    conexion = ConexionDemo(otps, al_recibir=registro.guardar_crudo, abrir=abrir,
                            dormir=_sin_espera, **kw)
    return falso, abiertas, registro, otps, conexion


async def _sin_espera(s):
    return None


def _eventos(tmp_path: Path) -> list[str]:
    return [f["evento"] for f in leer_registro(tmp_path).filas]


# ═══ OTP solo para demo, URL solo /ws/demo ════════════════════════════════

_CUENTAS = [{"account_id": "ROT12345678", "account_type": "real", "status": "active"},
            {"account_id": "DOT90004580", "account_type": "demo", "status": "active"}]


def _post(url=None, status=200):
    vistos = []

    async def post(u, h):
        vistos.append(u)
        return status, {}, json.dumps({"data": {"url": url or f"{ENDPOINT_DEMO}?otp=zz"}})
    return post, vistos


@pytest.mark.parametrize("cuenta", [_CUENTAS[0], {**_CUENTAS[1], "account_type": None},
                                    {"account_id": "X1", "account_type": "Demo"}])
async def test_una_cuenta_que_no_es_demo_no_recibe_otp(cuenta):
    post, vistos = _post()
    with pytest.raises(CuentaNoDemoError):
        await url_demo_nueva(cuenta, token="T", app_id="A", post=post)
    assert vistos == []


async def test_una_cuenta_real_no_recibe_otp_ni_desde_la_conexion(tmp_path):
    post, vistos = _post()

    async def url_nueva():
        return await url_demo_nueva(_CUENTAS[0], token="T", app_id="A", post=post)
    abiertas = []
    c = ConexionDemo(url_nueva, al_recibir=Registro(tmp_path).guardar_crudo,
                     abrir=lambda u: abiertas.append(u), dormir=_sin_espera)
    with pytest.raises(CuentaNoDemoError):
        await c.__aenter__()
    assert vistos == [] and abiertas == [] and c.n_otps == 1, "se detiene sin reintentar"


async def test_la_cuenta_demo_recibe_su_url():
    post, vistos = _post()
    url = await url_demo_nueva(_CUENTAS[1], token="T", app_id="A", post=post)
    assert url == f"{ENDPOINT_DEMO}?otp=zz"
    assert vistos == [otp_mod.BASE_REST + "/trading/v1/options/accounts/DOT90004580/otp"]


@pytest.mark.parametrize("url", [
    f"{_REAL_PATH}?otp=zz",
    ENDPOINT_DEMO.replace("/demo", "/public") + "?otp=zz",
    ENDPOINT_DEMO.replace("api.derivws.com", "otro.example") + "?otp=zz",
    ENDPOINT_DEMO.replace("wss:", "ws:") + "?otp=zz",
    ENDPOINT_DEMO,
])
async def test_una_url_fuera_de_ws_demo_no_conecta(tmp_path, url):
    post, _ = _post(url)
    with pytest.raises(UrlNoDemoError):
        await url_demo_nueva(_CUENTAS[1], token="T", app_id="A", post=post)
    falso, abiertas, _, otps, conexion = _armar(tmp_path, url=url)
    with pytest.raises(UrlNoDemoError):
        await conexion.__aenter__()
    assert abiertas == [] and falso.conexiones == 0 and otps.n == 1


async def test_un_otp_rechazado_lanza_sin_ids_ni_otp():
    async def post(u, h):
        return 401, {}, '{"error":"DOT90004580 sin permiso", "url":"x?otp=SECRETO1"}'
    with pytest.raises(OtpNoEmitidoError) as e:
        await url_demo_nueva(_CUENTAS[1], token="T", app_id="A", post=post)
    assert "90004580" not in str(e.value)
    assert "SECRETO1" not in str(e.value) and "otp=***" in str(e.value)
    assert '"http": 401' in str(e.value)


# ═══ Conexión ═════════════════════════════════════════════════════════════

async def test_la_reconexion_pide_un_otp_nuevo(tmp_path):
    falso, abiertas, _, otps, conexion = _armar(tmp_path, _Falso(cortar=("ping",)))
    async with conexion as c:
        with pytest.raises(ConexionPerdidaError):
            await c.pedir({"ping": 1})
        assert not c.conectada
        crudo, _ = await c.pedir({"ping": 1})
    assert json.loads(crudo)["ping"] == "pong"
    assert abiertas == [f"{ENDPOINT_DEMO}?otp=otp1", f"{ENDPOINT_DEMO}?otp=otp2"]
    assert otps.n == 2 and c.n_conexiones == 2 and falso.conexiones == 2
    assert len(falso.de_tipo("ping")) == 2, "el ping cortado no se reintentó solo"


async def test_cada_intento_de_conexion_pide_su_otp_y_espera_con_backoff(tmp_path):
    esperas = []

    async def dormir(s):
        esperas.append(s)

    def abrir(u):
        raise OSError("sin red")
    otps = _Otps()
    c = ConexionDemo(otps, al_recibir=Registro(tmp_path).guardar_crudo, abrir=abrir,
                     dormir=dormir, esperas_s=(1.0, 2.0, 4.0))
    with pytest.raises(NoConectadoError):
        await c.__aenter__()
    assert esperas == [1.0, 2.0, 4.0]
    assert otps.n == 4 and c.n_otps == 4, "un OTP por intento"


async def test_la_conexion_se_recupera_en_un_reintento(tmp_path):
    falso = _Falso()
    llamadas = []

    def abrir(u):
        llamadas.append(u)
        if len(llamadas) == 1:
            raise OSError("sin red")
        return falso.connector(u)
    otps = _Otps()
    async with ConexionDemo(otps, al_recibir=Registro(tmp_path).guardar_crudo, abrir=abrir,
                            dormir=_sin_espera) as c:
        assert c.conectada
    assert llamadas == [f"{ENDPOINT_DEMO}?otp=otp1", f"{ENDPOINT_DEMO}?otp=otp2"]


async def test_la_respuesta_es_la_del_mismo_req_id(tmp_path):
    falso, _, registro, _, conexion = _armar(tmp_path)
    async with conexion as c:
        falso._pendientes.append(json.dumps({"msg_type": "ping", "req_id": 999}))
        falso._pendientes.append("no es json")
        crudo, sha = await c.pedir({"ping": 1})
    assert json.loads(crudo)["req_id"] == 1
    assert len(c.no_pedidos) == 2
    crudos = leer_crudo(tmp_path)
    assert "no es json" in crudos.values(), "lo crudo se guarda antes de parsear"
    assert crudos[sha] == crudo


async def test_sin_respuesta_a_tiempo_es_conexion_perdida(tmp_path):
    class _Mudo(_Falso):
        async def recv(self):
            await asyncio.sleep(1)
    _, _, _, _, conexion = _armar(tmp_path, _Mudo(), timeout_s=0.01)
    async with conexion as c:
        with pytest.raises(ConexionPerdidaError):
            await c.pedir({"ping": 1})
        assert not c.conectada


async def test_la_conexion_valida_antes_del_socket(tmp_path):
    falso, _, _, _, conexion = _armar(tmp_path)
    async with conexion as c:
        with pytest.raises(MensajeNoPermitidoError):
            await c.pedir({"active_symbols": "brief"})
    assert falso.enviados == []


async def test_el_ping_es_un_parametro(tmp_path):
    falso, _, _, _, conexion = _armar(tmp_path, intervalo_ping_s=0.01)
    async with conexion:
        await asyncio.sleep(0.08)
    assert len(falso.de_tipo("ping")) >= 2
    falso2, _, _, _, conexion2 = _armar(tmp_path / "b")
    async with conexion2:
        await asyncio.sleep(0.05)
    assert falso2.de_tipo("ping") == [], "sin intervalo no hay ping"


async def test_cerrar_termina_aunque_el_ping_se_trague_la_cancelacion(tmp_path):
    """En Python 3.11, asyncio.wait_for se traga una cancelación que llega
    con la respuesta ya lista. El ping puede no enterarse del cancel(), y
    el cierre igual tiene que terminar."""
    _, _, _, _, conexion = _armar(tmp_path, intervalo_ping_s=0.001)

    async def pedir_que_traga(payload):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            return "", ""
    conexion.pedir = pedir_que_traga
    await conexion.__aenter__()
    await asyncio.sleep(0.02)
    cierre = asyncio.ensure_future(conexion.__aexit__(None, None, None))
    await asyncio.wait({cierre}, timeout=2)
    termino = cierre.done()

    async def rompe(payload):                 # limpieza: que el ping termine igual
        raise RuntimeError("fin del test")
    conexion.pedir = rompe
    conexion._latido.cancel()
    await asyncio.wait({conexion._latido, cierre}, timeout=2)
    assert termino, "el cierre quedó esperando al ping para siempre"


async def test_cerrar_no_se_traga_la_cancelacion_de_quien_cierra(tmp_path):
    _, _, _, _, conexion = _armar(tmp_path)
    await conexion.__aenter__()
    fin = asyncio.Event()

    async def inmortal():
        while not fin.is_set():
            try:
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                pass
    conexion._latido = asyncio.ensure_future(inmortal())
    cierre = asyncio.ensure_future(conexion.__aexit__(None, None, None))
    await asyncio.sleep(0.02)
    cierre.cancel()
    await asyncio.wait({cierre}, timeout=1)
    fin.set()
    await conexion._latido
    assert cierre.cancelled(), "la cancelación de quien cierra se perdió"


async def test_medir_el_cierre_por_inactividad(tmp_path):
    class _Cierra(_Falso):
        async def recv(self):
            if self._pendientes:
                return self._pendientes.pop(0)
            await asyncio.sleep(0.05)
            raise ConnectionError("cerrado por inactividad")
    _, _, _, _, conexion = _armar(tmp_path, _Cierra())
    async with conexion as c:
        t = await c.medir_cierre_inactivo(5)
        assert not c.conectada
    assert 0.04 <= t < 1

    class _Quieto(_Falso):
        async def recv(self):
            await asyncio.sleep(10)
    _, _, _, _, conexion = _armar(tmp_path / "b", _Quieto())
    async with conexion as c:
        assert await c.medir_cierre_inactivo(0.05) is None
        assert c.conectada


async def test_el_socket_de_websockets_se_abre_sin_ping_de_protocolo(monkeypatch):
    import websockets

    from integracion_demo.conexion import abrir_websocket
    vistos = {}
    monkeypatch.setattr(websockets, "connect", lambda url, **kw: vistos.update(kw) or "cm")
    assert abrir_websocket(ENDPOINT_DEMO + "?otp=x") == "cm"
    assert vistos["ping_interval"] is None


# ═══ Lista blanca ═════════════════════════════════════════════════════════

@pytest.mark.parametrize("payload", [
    {"active_symbols": "brief"}, {"ticks_history": "R_50"}, {"authorize": "x"},
    {"balance": 1}, {"portfolio": 1}, {"transaction": 1}, {"cancel": 11},
    {"buy_contract_for_multiple_accounts": "1"}, {"sell_expired": 1},
    {"sell_contract_for_multiple_accounts": 1}, {"contracts_for": "frxXAUUSD"},
    {"topup_virtual": 1}, {"forget_all": "proposal"}, {"time": 1}, {},
    # Claves que no van en un mensaje que sí va:
    {"proposal": 1, "contract_type": "MULTUP", "buy": "1"},
    {"proposal": 1, "contract_type": "MULTUP", "subscribe": 1},
    {"proposal": 1, "contract_type": "CALL"},
    {"proposal": 1, "contract_type": "MULTUP", "limit_order": {"stop_out": 1}},
    {"proposal_open_contract": 1, "contract_id": 11, "subscribe": 1},
    {"proposal_open_contract": 1},
    {"ping": 1, "passthrough": {"a": 1}},
    {"sell": 11, "price": 0, "subscribe": 1},
    {"sell": "11", "price": 0}, {"sell": 11, "price": -1}, {"sell": 11},
    {"profit_table": 1, "subscribe": 1}, {"statement": 1, "passthrough": {}},
    {"statement": 0}, {"ping": 0},
    {"contract_update": 1, "contract_id": 11, "limit_order": {"stop_loss": None}},
    {"contract_update": 1, "contract_id": 11, "limit_order": {"take_profit": 1}},
    {"contract_update": 1, "contract_id": 11, "limit_order": {"stop_loss": 0.1,
                                                              "take_profit": -1}},
    {"contract_update": 1, "limit_order": {"stop_loss": 0.1}},
])
def test_la_lista_blanca_rechaza_todo_lo_demas(payload):
    with pytest.raises(MensajeNoPermitidoError):
        validar_mensaje(payload)


def test_la_lista_blanca_son_los_ocho_del_brief():
    assert MENSAJES_PERMITIDOS == {"proposal", "buy", "proposal_open_contract",
                                   "contract_update", "sell", "profit_table", "statement",
                                   "ping"}


@pytest.mark.parametrize("payload", [
    {"proposal": 1, "contract_type": "MULTDOWN", "amount": 1, "basis": "stake",
     "currency": "USD", "underlying_symbol": "frxXAUUSD", "multiplier": 100,
     "limit_order": {"stop_loss": 0.1}},
    {"proposal_open_contract": 1, "contract_id": 11},
    {"sell": 11, "price": 0},
    {"profit_table": 1, "description": 1, "limit": 5, "sort": "DESC"},
    {"statement": 1, "description": 1, "action_type": "buy"},
    {"ping": 1, "req_id": 3},
    {"contract_update": 1, "contract_id": 11,
     "limit_order": {"stop_loss": 0.05, "take_profit": None}},
])
def test_la_lista_blanca_deja_pasar_lo_que_va(payload):
    assert validar_mensaje(payload) == next(iter(payload))


async def test_la_lista_blanca_rechaza_sin_tocar_el_socket(tmp_path):
    falso, _, registro, _, conexion = _armar(tmp_path)
    async with conexion as c:
        e = Ejecutor(c, registro, _CTX)
        for payload in ({"balance": 1}, {"portfolio": 1}, {"cancel": 11}):
            with pytest.raises(MensajeNoPermitidoError):
                await e.pedir(payload)
    assert falso.enviados == []


# ═══ buy ══════════════════════════════════════════════════════════════════

def _buy(**cambios):
    p = {"contract_type": "MULTUP", "basis": "stake", "amount": 1.0, "currency": "USD",
         "underlying_symbol": "frxXAUUSD", "multiplier": 100.0,
         "limit_order": {"stop_loss": 0.10}}
    p.update(cambios)
    return {"buy": "1", "price": 1.0, "parameters": p}


@pytest.mark.parametrize("limit_order", [
    None, {}, {"take_profit": 1.0}, {"stop_loss": None}, {"stop_loss": 0},
    {"stop_loss": -0.1}, {"stop_loss": float("nan")}, {"stop_loss": float("inf")},
    {"stop_loss": True}, {"stop_loss": "0.10"},
])
def test_sin_stop_loss_no_hay_buy_en_la_lista_blanca(limit_order):
    payload = _buy(limit_order=limit_order)
    if limit_order is None:
        del payload["parameters"]["limit_order"]
    with pytest.raises(OrdenSinStopLossError):
        validar_mensaje(payload)


@pytest.mark.parametrize("payload", [
    {**_buy(), "buy": "a" * 40},
    {"buy": "1", "price": 1.0},
    _buy(contract_type="CALL"), _buy(contract_type="ACCU"),
    _buy(cancellation="5m"), _buy(duration=5),
    _buy(limit_order={"stop_loss": 0.1, "take_profit": 0}),
    _buy(limit_order={"stop_loss": 0.1, "stop_out": 1}),
    _buy(amount=0), _buy(multiplier=-100),
    {**_buy(), "price": 0}, {**_buy(), "subscribe": 1}, {**_buy(), "passthrough": {}},
])
def test_un_buy_que_no_es_un_multiplicador_con_sl_no_pasa(payload):
    with pytest.raises(MensajeNoPermitidoError):
        validar_mensaje(payload)


def test_un_buy_con_stop_loss_pasa():
    assert validar_mensaje(_buy()) == "buy"
    assert validar_mensaje(_buy(limit_order={"stop_loss": 0.1, "take_profit": 0.5})) == "buy"
    assert validar_mensaje(_buy(contract_type="MULTDOWN")) == "buy"


@pytest.mark.parametrize("sl", [None, 0.0, -0.1])
async def test_sin_stop_loss_no_hay_buy_y_la_orden_entra_igual(tmp_path, sl):
    falso, _, registro, _, conexion = _armar(tmp_path)
    async with conexion as c:
        trade = await Ejecutor(c, registro, _CTX).comprar(
            Orden("frxXAUUSD", "MULTUP", 1.0, 100.0, stop_loss=sl))
    assert falso.de_tipo("buy") == []
    assert trade.estado == "no_enviada"
    filas = leer_registro(tmp_path).filas
    assert [(f["evento"], f["outcome"]) for f in filas] == [("no_enviada", "no_ejecutada")]
    assert "OrdenSinStopLossError" in filas[0]["evidencia"]


async def test_un_buy_rechazado_entra_como_rechazada(tmp_path):
    rechazo = {"buy": lambda p: {"error": {"code": "InvalidStopLoss", "message": "bajo"}}}
    falso, _, registro, _, conexion = _armar(tmp_path, _Falso(extra=rechazo))
    async with conexion as c:
        trade = await Ejecutor(c, registro, _CTX).comprar(_ORDEN)
    assert trade.estado == "rechazada"
    filas = leer_registro(tmp_path).filas
    assert [f["evento"] for f in filas] == ["envio", "rechazo"]
    assert filas[1]["outcome"] == "rechazada" and "InvalidStopLoss" in filas[1]["evidencia"]
    assert filas[1]["raw_sha256"] in leer_crudo(tmp_path)


async def test_un_buy_sin_respuesta_es_desconocido_y_no_se_reintenta(tmp_path):
    falso, _, registro, otps, conexion = _armar(tmp_path, _Falso(cortar=("buy",)))
    async with conexion as c:
        e = Ejecutor(c, registro, _CTX)
        trade = await e.comprar(_ORDEN)
        await c.pedir({"ping": 1})
    assert trade.estado == "desconocido" and trade.contract_id is None
    assert len(falso.de_tipo("buy")) == 1, "nunca se reintenta un buy"
    filas = leer_registro(tmp_path).filas
    assert [(f["evento"], f["outcome"]) for f in filas] == [
        ("envio", None), ("sin_respuesta", "desconocido")]
    assert otps.n == 2, "el próximo pedido reconectó con otro OTP"


async def test_sin_conexion_la_orden_no_se_envia_y_entra(tmp_path):
    def abrir(u):
        raise OSError("sin red")
    registro = Registro(tmp_path)
    c = ConexionDemo(_Otps(), al_recibir=registro.guardar_crudo, abrir=abrir,
                     dormir=_sin_espera, esperas_s=(1.0,))
    trade = await Ejecutor(c, registro, _CTX).comprar(_ORDEN)
    assert trade.estado == "no_enviada"
    assert [(f["evento"], f["outcome"]) for f in leer_registro(tmp_path).filas] == [
        ("envio", None), ("no_enviada", "no_ejecutada")]


async def test_el_buy_va_con_parametros_y_el_stop_loss(tmp_path):
    falso, _, registro, _, conexion = _armar(tmp_path)
    async with conexion as c:
        trade = await Ejecutor(c, registro, _CTX).comprar(_ORDEN, quote_at_signal=2399.9)
    (b,) = falso.de_tipo("buy")
    assert b["buy"] == "1" and b["price"] == 1.0
    assert b["parameters"] == {"contract_type": "MULTUP", "basis": "stake", "amount": 1.0,
                               "currency": "USD", "underlying_symbol": "frxXAUUSD",
                               "multiplier": 100.0, "limit_order": {"stop_loss": 0.10}}
    assert trade.contract_id == 11 and trade.buy_transaction_id == 501
    envio, compra = leer_registro(tmp_path).filas
    assert envio["evento"] == "envio" and envio["t_envio_ms"] is None, "antes de enviar"
    assert envio["trade_uuid"] == compra["trade_uuid"] == trade.trade_uuid
    assert compra["latencia_ms"] == compra["t_ack_ms"] - compra["t_envio_ms"]
    assert compra["sl_solicitado"] == 0.10 and compra["nocional"] == 100.0


# ═══ La operación entera ══════════════════════════════════════════════════

def _reloj():
    t = [0.0]

    async def dormir(s):
        t[0] += s
    return dormir, (lambda: t[0])


async def test_la_operacion_entera_vende_a_los_60_s_y_cierra(tmp_path):
    falso, _, registro, _, conexion = _armar(tmp_path)
    dormir, reloj = _reloj()
    async with conexion as c:
        e = Ejecutor(c, registro, _CTX)
        trade = await e.comprar(_ORDEN, quote_at_signal=2399.90)
        cierre = await e.seguir_hasta_cierre(trade, vender_a_los_s=60, cada_s=5,
                                             esperar_cierre_s=30, dormir=dormir,
                                             reloj_s=reloj)
    assert len(falso.de_tipo("sell")) == 1 and falso.de_tipo("sell")[0] == {
        "sell": 11, "price": 0, "req_id": falso.de_tipo("sell")[0]["req_id"]}
    assert len(falso.de_tipo("proposal_open_contract")) == 14   # 0, 5, …, 60, y el cierre
    assert _eventos(tmp_path) == ["envio", "compra", "seguimiento", "venta", "cierre"]
    assert cierre["outcome"] == "sell_tiempo" and "502" in cierre["evidencia"]
    assert cierre["R_usd"] == pytest.approx(0.10)
    assert cierre["r_multiple"] == pytest.approx(0.03 / 0.10), "profit / R_usd, no / stake"
    assert cierre["profit_deriv"] == "0.03" and cierre["commission_cruda"] == "0.02"
    assert cierre["sell_transaction_id"] == 502 and cierre["contract_id"] == 11
    assert cierre["slip_entrada"] == pytest.approx(0.10)
    assert cierre["slip_salida"] == pytest.approx(2401.30 - 2401.20)
    assert leer_registro(tmp_path).integro


async def test_si_deriv_lo_cierra_antes_no_se_vende(tmp_path):
    falso, _, registro, _, conexion = _armar(tmp_path, _Falso(cerrar_solo=True))
    dormir, reloj = _reloj()
    async with conexion as c:
        e = Ejecutor(c, registro, _CTX)
        trade = await e.comprar(_ORDEN)
        cierre = await e.seguir_hasta_cierre(trade, vender_a_los_s=60, cada_s=5,
                                             esperar_cierre_s=30, dormir=dormir,
                                             reloj_s=reloj)
    assert falso.de_tipo("sell") == []
    assert cierre["outcome"] == "desconocido", "exit_spot sin cruzar ningún nivel"


async def test_sin_cierre_observado_es_desconocido(tmp_path):
    nunca = {"sell": lambda p: {"error": {"code": "SellNotAvailable", "message": "x"}}}
    falso, _, registro, _, conexion = _armar(tmp_path, _Falso(extra=nunca))
    dormir, reloj = _reloj()
    async with conexion as c:
        e = Ejecutor(c, registro, _CTX)
        trade = await e.comprar(_ORDEN)
        cierre = await e.seguir_hasta_cierre(trade, vender_a_los_s=60, cada_s=5,
                                             esperar_cierre_s=30, dormir=dormir,
                                             reloj_s=reloj)
    assert cierre["outcome"] == "desconocido"
    assert "no se observó el cierre" in cierre["evidencia"]
    # 0, 5, …, 60 (13), otra vez a los 60 tras el sell, y 65, …, 90 (6):
    # siguió mirando los 30 s antes de rendirse.
    assert len(falso.de_tipo("proposal_open_contract")) == 20
    assert "sin cierre observado 30 s" in cierre["evidencia"]
    venta = [f for f in leer_registro(tmp_path).filas if f["evento"] == "venta"]
    assert len(venta) == 1 and "SellNotAvailable" in venta[0]["evidencia"]


async def test_un_contrato_sin_contract_type_ni_simbolo_se_cierra_igual(tmp_path):
    class _Escueto(_Falso):
        def _poc(self, p):
            d = super()._poc(p)
            for k in ("contract_type", "underlying_symbol"):
                d["proposal_open_contract"].pop(k)
            return d
    _, _, registro, _, conexion = _armar(tmp_path, _Escueto())
    dormir, reloj = _reloj()
    async with conexion as c:
        e = Ejecutor(c, registro, _CTX)
        trade = await e.comprar(_ORDEN)
        cierre = await e.seguir_hasta_cierre(trade, vender_a_los_s=60, cada_s=5,
                                             esperar_cierre_s=30, dormir=dormir,
                                             reloj_s=reloj)
    assert cierre["outcome"] == "sell_tiempo"
    assert cierre["commission_modelada"] == pytest.approx(0.02), "κ del símbolo pedido"


async def test_no_se_sigue_un_trade_que_no_abrio(tmp_path):
    _, _, registro, _, conexion = _armar(tmp_path)
    async with conexion as c:
        e = Ejecutor(c, registro, _CTX)
        trade = await e.comprar(Orden("frxXAUUSD", "MULTUP", 1.0, 100.0, None))
        with pytest.raises(ValueError):
            await e.seguir_hasta_cierre(trade, vender_a_los_s=1, cada_s=1, esperar_cierre_s=1)


# ═══ contract_update ══════════════════════════════════════════════════════

_SHA = "a" * 64


async def _abierto(tmp_path):
    falso, _, registro, _, conexion = _armar(tmp_path)
    await conexion.__aenter__()
    e = Ejecutor(conexion, registro, _CTX)
    trade = await e.comprar(_ORDEN)
    await e.consultar(trade)
    return falso, e, trade


async def test_el_stop_loss_solo_se_mueve_a_favor_y_con_preregistro(tmp_path):
    falso, e, trade = await _abierto(tmp_path)
    with pytest.raises(MensajeNoPermitidoError, match="pre-registro"):
        await e.actualizar_stop_loss(trade, 0.05, preregistro_sha256="")
    with pytest.raises(MensajeNoPermitidoError, match="no reduce"):
        await e.actualizar_stop_loss(trade, 0.10, preregistro_sha256=_SHA)
    with pytest.raises(MensajeNoPermitidoError, match="no reduce"):
        await e.actualizar_stop_loss(trade, 0.20, preregistro_sha256=_SHA)
    assert falso.de_tipo("contract_update") == []
    assert await e.actualizar_stop_loss(trade, 0.05, preregistro_sha256=_SHA) is True
    (u,) = falso.de_tipo("contract_update")
    assert u["limit_order"] == {"stop_loss": 0.05}
    assert leer_registro(tmp_path).filas[-1]["sl_confirmado"] == -0.05


# ═══ Contexto ═════════════════════════════════════════════════════════════

@pytest.mark.parametrize("cambios", [{"account_type": "real"}, {"account_type": None},
                                     {"ws_path": _REAL_PATH}, {"ws_path": "/ws/public"}])
def test_el_contexto_solo_es_demo(cambios):
    base = dict(experiment_id="t", account_id="DOT********", account_type="demo",
                ws_path=_PATH_DEMO, currency="USD")
    base.update(cambios)
    with pytest.raises(ValueError):
        Contexto(**base)


async def test_profit_table_y_statement_devuelven_las_transacciones(tmp_path):
    _, _, registro, _, conexion = _armar(tmp_path)
    async with conexion as c:
        e = Ejecutor(c, registro, _CTX)
        pt, sha_pt = await e.profit_table(limit=10)
        st, sha_st = await e.statement(limit=10)
    assert pt == [] and st == []
    assert {sha_pt, sha_st} <= set(leer_crudo(tmp_path))
