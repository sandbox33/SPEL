"""
tests/test_deriv_cotizacion_real_live.py
==========================================
Sonda §0.A-3b, puntos 5c, 5d y 5e (brief del Admin del 05-oct-2026): los
tres canales de Deriv —público, demo y REAL— cotizando lo mismo, intercalado,
y el κ de costos por activo y canal que sale de ahí. 5c (la mecánica del
rango de apertura) corre acá porque usa ese κ.

ES EL ÚNICO ARCHIVO DEL REPO QUE PUEDE NOMBRAR EL CANAL REAL.
`tests/test_guarda_canal_real.py` falla si la ruta de ese canal aparece
como literal de código en cualquier otro lado.

══ LA AUTORIZACIÓN ══

De un solo uso, del Admin, registrada en decision-log.md (2026-10-05): UN
OTP para la cuenta cuyo `account_type` sea `real` según `GET /accounts`, y
UNA conexión al canal real SOLO PARA COTIZAR, entre el 05-oct-2026 00:00 y
el 09-oct-2026 23:59 UTC. Fuera de esa ventana el test se salta solo, y las
funciones que piden el OTP o abren la conexión lanzan.

══ LAS REGLAS DE 5e, EN CÓDIGO ══

  · `GET /accounts` tiene que mostrar EXACTAMENTE una cuenta real. Con 0 o
    con más de 1, la sonda se detiene antes del OTP.
  · Saldo: si `/accounts` lo expone (el esquema oficial lo declara
    obligatorio), tiene que ser 0 antes de pedir el OTP. Si no lo expone, el
    primer mensaje en el canal real es `balance`, y si es > 0 se cierra sin
    enviar nada más. Los dos caminos se informan.
  · Antes de conectar: esquema `wss`, host `api.derivws.com` y la ruta del
    canal real. Si algo no coincide, no se conecta.
  · Lista blanca propia: `time`, `balance`, `contracts_for` y `proposal`
    sin `subscribe`. Cualquier otro tipo —`buy`, `sell`, `cancel`,
    `portfolio`, `proposal_open_contract`, `transaction`…— lanza ANTES de
    enviarse. Además, cada mensaje solo puede llevar las claves que su
    esquema oficial declara (commit 54e3538), menos `subscribe` y
    `passthrough`: un `buy` escondido como clave extra de un `proposal`
    también lanza. Y la `proposal` solo puede ser MULTUP, lo único que 5d
    cotiza.
  · Una sola conexión, a lo sumo MAX_MENSAJES_REAL mensajes, cierre al
    terminar. La URL con el OTP nunca va al informe.

La función del OTP demo de la sonda §0.A-3 (`emitir_otp_demo`) y su test,
que impide el OTP para la cuenta real, no se tocan: esta es otra función.

══ 5d: LOS TRES CANALES, INTERCALADOS ══

Para cada combinación (BTC, oro y EUR/USD × MULTUP × stake {1, 2} ×
multiplicador {50, 100, 200}, más stake 1 a ×100 con stop-loss de 0,30 USD)
se cotiza en público, demo y real seguidos. Se informan `commission`,
`stop_out`, los límites de stake y de stop-loss, y si el multiplicador se
aceptó o el error literal. De ahí, la tabla de κ = comisión / nocional por
activo y canal.

[INTERPRETACIÓN] Lo que el brief no fija:
  · "Dentro del mismo minuto" es el mismo minuto de calendario UTC: una
    combinación no empieza en los últimos SEGUNDOS_DE_MARGEN de un minuto.
    El informe trae los segundos entre la primera y la última cotización de
    cada combinación.
  · La moneda de cada canal es la de su cuenta; el público usa la de demo.
  · κ por la regla del Admin (decision-log 2026-10-06): el máximo medido en
    real; si no hay medición real, el máximo entre público y demo, marcado
    como respaldo. 5c usa ESE κ. Hasta el 06-oct usaba "el mayor medido en
    5d" (el máximo de los tres canales), que resultó el del canal público:
    la regla nueva lo reemplaza.
  · 5c usa las velas M5 del canal público del último año.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import pytest

from governance.secrets import SecretKey, load_secret
from ingestion.adapters import DerivAdapter
from ingestion.deriv_ws import TIMEOUT_RESPUESTA_S
from ingestion.sonda_instrumentos import CONTROL_POSITIVO, codigo, seleccionar
from tests.test_deriv_endpoints_live import (
    _SOLO_EN_LIVE_TESTS,
    ENDPOINT_PUBLICO_NUEVO,
    _http_de,
    texto_libre,
)
from tests.test_deriv_sonda2_live import (
    BASE_REST,
    NOTA_401,
    Getter,
    _Canal,
    _get_httpx,
    _limpiador,
    enmascarar_id,
    enmascarar_ids,
    profundidad,
)
from tests.test_deriv_sonda3_live import (
    RUTA_OTP,
    Poster,
    _CanalPausado,
    _num,
    _post_httpx,
    elegir_demo,
    emitir_otp_demo,
    leer_cuentas,
    motivo_para_no_conectar,
    otp_de,
)
from tests.test_sonda3b_live import (
    ANCLAS_5C,
    DIAS_ULTIMO_ANIO,
    GRANULARIDAD_5C,
    _publicar,
    mecanica_rango,
    rangos_de_apertura,
)

#: El canal real. ÚNICO lugar del repo donde esta ruta puede ser un literal.
ENDPOINT_REAL = "wss://api.derivws.com/trading/v1/options/ws/real"

#: La ventana de la autorización, en UTC, extremos incluidos.
VENTANA_INICIO = datetime(2026, 10, 5, 0, 0, 0, tzinfo=timezone.utc)
VENTANA_FIN = datetime(2026, 10, 9, 23, 59, 59, tzinfo=timezone.utc)

#: Tope de mensajes en el canal real.
MAX_MENSAJES_REAL = 40

#: Las claves que cada mensaje puede llevar en el canal real: las de su
#: esquema oficial (deriv-api-schemas, commit 54e3538), sin `subscribe` ni
#: `passthrough`. `req_id` lo pone el canal.
CLAVES_PERMITIDAS_REAL: dict[str, frozenset[str]] = {
    "time": frozenset({"time", "req_id"}),
    "balance": frozenset({"balance", "req_id"}),
    "contracts_for": frozenset({"contracts_for", "req_id"}),
    "proposal": frozenset({
        "proposal", "amount", "barrier", "barrier2", "basis", "cancellation",
        "contract_type", "currency", "date_expiry", "duration", "duration_unit",
        "growth_rate", "limit_order", "multiplier", "payout_per_point",
        "selected_tick", "underlying_symbol", "req_id"}),
}

#: Lo único que 5d cotiza en el canal real.
CONTRATOS_PERMITIDOS_REAL: frozenset[str] = frozenset({"MULTUP"})

#: 5d.
STAKES_5D: tuple[float, ...] = (1.0, 2.0)
MULTIPLICADORES_5D: tuple[float, ...] = (50.0, 100.0, 200.0)
STOP_LOSS_5D = 0.30
STAKE_STOP_LOSS_5D = 1.0
MULT_STOP_LOSS_5D = 100.0

#: [INTERPRETACIÓN] Una combinación no empieza en los últimos segundos del
#: minuto.
SEGUNDOS_DE_MARGEN = 5


class MensajeRealNoPermitido(ValueError):
    """Un mensaje fuera de la lista blanca del canal real. Se lanza antes de
    tocar el socket."""


class TopeDeMensajesReal(RuntimeError):
    """Se alcanzó MAX_MENSAJES_REAL. Se lanza antes de tocar el socket."""


class FueraDeVentana(RuntimeError):
    """La autorización de un solo uso no cubre este momento."""


class UsoUnicoAgotado(RuntimeError):
    """Un segundo OTP real, o una segunda conexión real, en la misma sesión."""


def en_ventana(ahora: datetime) -> bool:
    return VENTANA_INICIO <= ahora <= VENTANA_FIN


def validar_mensaje_real(payload: dict) -> str:
    """El tipo del mensaje, si está permitido; si no, lanza."""
    if not payload:
        raise MensajeRealNoPermitido("mensaje vacío")
    tipo = next(iter(payload))
    permitidas = CLAVES_PERMITIDAS_REAL.get(tipo)
    if permitidas is None:
        raise MensajeRealNoPermitido(f"{tipo!r} no está en la lista blanca del canal real "
                                     f"({', '.join(sorted(CLAVES_PERMITIDAS_REAL))})")
    sobran = set(payload) - permitidas
    if sobran:
        raise MensajeRealNoPermitido(f"{tipo!r} con claves fuera de su esquema permitido: "
                                     f"{sorted(sobran)}")
    if tipo == "proposal" and payload.get("contract_type") not in CONTRATOS_PERMITIDOS_REAL:
        raise MensajeRealNoPermitido(f"proposal de {payload.get('contract_type')!r}: en el "
                                     f"canal real solo se cotiza MULTUP")
    return tipo


class CanalReal:
    """El canal real: lista blanca propia y tope de mensajes, los dos
    verificados ANTES de enviar."""

    def __init__(self, ws: Any, *, tope: int = MAX_MENSAJES_REAL) -> None:
        self.ws, self.tope = ws, tope
        self.enviados = 0

    async def pedir(self, payload: dict) -> tuple[dict, Optional[dict]]:
        validar_mensaje_real(payload)
        if self.enviados >= self.tope:
            raise TopeDeMensajesReal(f"{self.tope} mensajes: no se envía uno más")
        self.enviados += 1
        entrada: dict[str, Any] = {"ok": False}
        try:
            await self.ws.send(json.dumps({**payload, "req_id": self.enviados}))
            crudo = await asyncio.wait_for(self.ws.recv(), TIMEOUT_RESPUESTA_S)
        except Exception as exc:   # noqa: BLE001
            entrada["error"] = f"{type(exc).__name__}: {exc}"
            return entrada, None
        entrada["sha256"] = hashlib.sha256(crudo.encode("utf-8")).hexdigest()
        try:
            datos = json.loads(crudo)
        except json.JSONDecodeError as exc:
            entrada["error"] = f"no es JSON: {exc}"
            return entrada, None
        if datos.get("error"):
            entrada["error"] = {"code": datos["error"].get("code"),
                                "message": str(datos["error"].get("message"))}
            return entrada, None
        entrada["ok"] = True
        return entrada, datos


def cuenta_real_unica(cuentas: list[dict]) -> tuple[Optional[dict], Optional[str]]:
    reales = [c for c in cuentas if c.get("account_type") == "real"]
    if len(reales) != 1:
        return None, (f"GET /accounts muestra {len(reales)} cuentas con account_type real; "
                      f"se exige exactamente una")
    return reales[0], None


def motivo_para_no_conectar_real(url: str) -> Optional[str]:
    u, real = urlsplit(url), urlsplit(ENDPOINT_REAL)
    if (u.scheme, u.hostname, u.path.rstrip("/")) != (real.scheme, real.hostname, real.path):
        return (f"la URL del OTP no es la del canal real: "
                f"{u.scheme}://{u.hostname}{u.path}")
    if not otp_de(url):
        return "la URL del OTP no trae el parámetro `otp`"
    return None


class SesionReal:
    """Un OTP y una conexión, una vez cada uno. Las dos operaciones
    verifican la ventana."""

    def __init__(self, *, reloj: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.reloj = reloj
        self.otp_emitido = False
        self.conectado = False

    def _ventana(self) -> None:
        if not en_ventana(self.reloj()):
            raise FueraDeVentana(f"{self.reloj().isoformat()} fuera de "
                                 f"{VENTANA_INICIO.isoformat()} .. {VENTANA_FIN.isoformat()}")

    async def emitir_otp(self, cuenta: dict, *, token: str, app_id: str,
                         post: Poster = _post_httpx) -> tuple[dict, Optional[str]]:
        self._ventana()
        if cuenta.get("account_type") != "real":
            raise ValueError("emitir_otp de SesionReal es solo para la cuenta real")
        if self.otp_emitido:
            raise UsoUnicoAgotado("ya se emitió el OTP real de esta autorización")
        self.otp_emitido = True
        limpiar = _limpiador((token, app_id))
        informe: dict[str, Any] = {"ok": False, "cuenta": enmascarar_id(cuenta["account_id"])}
        try:
            status, _, cuerpo = await post(
                BASE_REST + RUTA_OTP.format(account_id=cuenta["account_id"]),
                {"Authorization": f"Bearer {token}", "Deriv-App-ID": app_id})
        except Exception as exc:   # noqa: BLE001
            informe["error"] = enmascarar_ids(limpiar(f"{type(exc).__name__}: {exc}"))
            return informe, None
        informe["http"] = status
        if status != 200:
            informe["error"] = enmascarar_ids(limpiar(cuerpo[:400]))
            if status == 401:
                informe["nota"] = NOTA_401
            return informe, None
        try:
            url = (json.loads(cuerpo).get("data") or {}).get("url")
        except (json.JSONDecodeError, AttributeError):
            url = None
        if not url:
            informe["error"] = "200 sin `data.url`"
            return informe, None
        informe["ok"] = True
        return informe, url

    def abrir(self, url: str, abrir: Callable[[str], Any]):
        self._ventana()
        if self.conectado:
            raise UsoUnicoAgotado("ya se abrió la conexión real de esta autorización")
        motivo = motivo_para_no_conectar_real(url)
        if motivo:
            raise ValueError(motivo)
        self.conectado = True
        return abrir(url)


# ═══ 5d ═══════════════════════════════════════════════════════════════════

def combinaciones(simbolos: list[str]) -> list[dict]:
    out = []
    for s in simbolos:
        for stake in STAKES_5D:
            for m in MULTIPLICADORES_5D:
                out.append({"simbolo": s, "stake": stake, "multiplicador": m, "stop_loss": None})
        out.append({"simbolo": s, "stake": STAKE_STOP_LOSS_5D,
                    "multiplicador": MULT_STOP_LOSS_5D, "stop_loss": STOP_LOSS_5D})
    return out


async def cotizar_en(canal: Any, combo: dict, moneda: str,
                     reloj: Callable[[], datetime]) -> dict:
    payload: dict[str, Any] = {
        "proposal": 1, "contract_type": "MULTUP", "basis": "stake", "amount": combo["stake"],
        "currency": moneda, "underlying_symbol": combo["simbolo"],
        "multiplier": combo["multiplicador"]}
    if combo["stop_loss"] is not None:
        payload["limit_order"] = {"stop_loss": combo["stop_loss"]}
    t = reloj()
    entrada, datos = await canal.pedir(payload)
    entrada["utc"] = t.isoformat()
    if datos is not None:
        prop = datos.get("proposal") or {}
        lo = prop.get("limit_order") or {}
        vp = prop.get("validation_params") or {}
        com = _num(prop.get("commission"))
        entrada.update(
            multiplicador_aceptado=True, commission=com,
            kappa=None if com is None else com / (combo["stake"] * combo["multiplicador"]),
            stop_out={k: (lo.get("stop_out") or {}).get(k)
                      for k in ("order_amount", "display_order_amount", "value")},
            stop_loss_devuelto=(lo.get("stop_loss") or {}).get("display_order_amount")
            if lo.get("stop_loss") else None,
            limites_stake=vp.get("stake"), limites_stop_loss=vp.get("stop_loss"))
    else:
        entrada["multiplicador_aceptado"] = False
    return entrada


async def esperar_margen(reloj: Callable[[], datetime], dormir) -> None:
    s = reloj().second + reloj().microsecond / 1e6
    if s >= 60 - SEGUNDOS_DE_MARGEN:
        await dormir(60 - s + 0.05)


async def comparar_canales(canales: dict[str, Any], monedas: dict[str, str],
                           simbolos: list[str], *,
                           reloj: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                           dormir=asyncio.sleep) -> list[dict]:
    """5d. Por combinación, los canales en orden: público, demo, real."""
    filas = []
    for combo in combinaciones(simbolos):
        await esperar_margen(reloj, dormir)
        fila: dict[str, Any] = {**combo, "canales": {}}
        for nombre in ("publico", "demo", "real"):
            if nombre not in canales:
                continue
            try:
                fila["canales"][nombre] = await cotizar_en(canales[nombre], combo,
                                                           monedas[nombre], reloj)
            except (MensajeRealNoPermitido, TopeDeMensajesReal) as exc:
                fila["canales"][nombre] = {"ok": False, "no_enviado": f"{type(exc).__name__}: {exc}"}
        momentos = [datetime.fromisoformat(c["utc"]) for c in fila["canales"].values() if "utc" in c]
        if momentos:
            fila["segundos_entre_canales"] = (max(momentos) - min(momentos)).total_seconds()
            fila["mismo_minuto_utc"] = len({m.replace(second=0, microsecond=0) for m in momentos}) == 1
        filas.append(fila)
    return filas


def tabla_kappa(filas: list[dict]) -> dict:
    """κ = comisión / nocional, máximo por activo y canal, y el κ de la
    regla del Admin, que es también el de 5c."""
    tabla: dict[str, dict[str, Optional[float]]] = {}
    for f in filas:
        for canal, c in f["canales"].items():
            k = c.get("kappa") if c.get("ok") else None
            if k is None:
                tabla.setdefault(f["simbolo"], {}).setdefault(canal, None)
                continue
            previo = tabla.setdefault(f["simbolo"], {}).get(canal)
            tabla[f["simbolo"]][canal] = k if previo is None else max(previo, k)
    out = {}
    for sim, por_canal in tabla.items():
        medidos = {c: k for c, k in por_canal.items() if k is not None}
        if medidos.get("real") is not None:
            regla, fuente = medidos["real"], "real"
        else:
            resto = [medidos[c] for c in ("publico", "demo") if c in medidos]
            regla, fuente = (max(resto), "respaldo") if resto else (None, None)
        out[sim] = {"por_canal": por_canal, "kappa": regla, "origen": fuente}
    return out


# ═══ La sonda entera ══════════════════════════════════════════════════════

async def sondear(*, token: str, app_id: str, abrir: Callable[[str], Any],
                  get: Getter = _get_httpx, post: Poster = _post_httpx,
                  reloj: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                  dormir=asyncio.sleep, pausa_s: float = 0.25) -> tuple[dict, tuple[str, ...]]:
    """Devuelve el informe y los secretos que no pueden aparecer en él."""
    secretos: list[str] = [token, app_id]
    informe: dict[str, Any] = {"sonda": "§0.A-3b, puntos 5c, 5d y 5e",
                               "corrida_utc": reloj().isoformat()}
    informe["cuentas"], cuentas = await leer_cuentas(token=token, app_id=app_id, get=get)
    demo = elegir_demo(cuentas)
    real, motivo_real = cuenta_real_unica(cuentas)
    monedas = {"publico": (demo or {}).get("currency"), "demo": (demo or {}).get("currency"),
               "real": (real or {}).get("currency")}
    informe["monedas"] = monedas
    inf_real: dict[str, Any] = {}
    informe["real"] = inf_real
    sesion = SesionReal(reloj=reloj)

    async with AsyncExitStack() as pila:
        canales: dict[str, Any] = {}
        pub: dict[str, Any] = {"handshake": None}
        informe["publico"] = pub
        try:
            ws = await pila.enter_async_context(abrir(ENDPOINT_PUBLICO_NUEVO))
            pub["handshake"] = {"ok": True}
            canales["publico"] = _CanalPausado(ws, pausa_s)
        except Exception as exc:   # noqa: BLE001
            pub["handshake"] = {"ok": False, "http": _http_de(exc),
                                "error": f"{type(exc).__name__}: {exc}"}
            return informe, tuple(secretos)
        entrada, datos = await canales["publico"].pedir({"active_symbols": "brief"})
        pub["active_symbols"] = {"ok": entrada["ok"], "sha256": entrada.get("sha256")}
        if datos is None:
            return informe, tuple(secretos)
        sel = seleccionar(datos.get("active_symbols") or [])
        btc, oro = [codigo(i)[0] for i in sel.btc], [codigo(i)[0] for i in sel.oro]
        foco = btc + oro + ([CONTROL_POSITIVO] if sel.control_presente else [])
        pub["active_symbols"].update(btc=btc, oro=oro, foco=foco)

        # Demo y real viven en su propia pila: se cierran apenas termina 5d.
        pila_canales = await pila.enter_async_context(AsyncExitStack())

        # Demo: UN OTP, con las reglas de la sonda §0.A-3.
        inf_demo: dict[str, Any] = {}
        informe["demo"] = inf_demo
        if demo is None:
            inf_demo["no_aplica"] = "GET /accounts no confirmó una cuenta demo activa"
        else:
            inf_demo["otp"], url = await emitir_otp_demo(demo, token=token, app_id=app_id, post=post)
            if url:
                secretos.append(otp_de(url) or "")
                motivo = motivo_para_no_conectar(url)
                if motivo:
                    inf_demo["no_conectado"] = motivo
                else:
                    try:
                        ws = await pila_canales.enter_async_context(abrir(url))
                        canales["demo"] = _CanalPausado(ws, 0)
                        inf_demo["handshake"] = {"ok": True}
                    except Exception as exc:   # noqa: BLE001
                        inf_demo["handshake"] = {"ok": False, "http": _http_de(exc),
                                                 "error": _limpiador(tuple(secretos))(f"{type(exc).__name__}: {exc}")}

        # Real: las reglas de 5e.
        await _abrir_real(pila_canales, sesion, real, motivo_real, inf_real, canales, secretos,
                          token=token, app_id=app_id, post=post, abrir=abrir)

        filas = await comparar_canales(canales, monedas, foco, reloj=reloj, dormir=dormir)
        informe["5d"] = filas
        if "real" in canales:
            inf_real["mensajes_enviados"] = canales["real"].enviados
        await pila_canales.aclose()
        if "real" in canales:
            inf_real["cerrado"] = True
        for nombre in ("demo", "real"):
            canales.pop(nombre, None)
        kappa = tabla_kappa(filas)
        informe["kappa"] = kappa

        # 5c, con las velas M5 del canal público del último año.
        informe["5c"] = {}
        desde = reloj().timestamp() - DIAS_ULTIMO_ANIO * 86400
        for clave, sims in (("oro", oro), ("btc", btc)):
            if len(sims) != 1:
                informe["5c"][clave] = {"no_aplica": f"se esperaba un símbolo, hay {sims}"}
                continue
            velas: dict[int, dict] = {}
            prof = await profundidad(canales["publico"], sims[0], GRANULARIDAD_5C,
                                     guardar_velas=velas)
            velas = {e: v for e, v in velas.items() if e >= desde}
            zona, h, m = ANCLAS_5C[clave]
            dias, faltan = rangos_de_apertura(velas, zona, h, m)
            informe["5c"][clave] = {"simbolo": sims[0], "ancla": f"{h:02d}:{m:02d} {zona}",
                                    "velas_m5": len(velas), "corte": prof["corte"],
                                    "dias_sin_las_tres_velas": faltan,
                                    **mecanica_rango(dias, (kappa.get(sims[0]) or {}).get("kappa"))}
    return informe, tuple(secretos)


async def _abrir_real(pila: AsyncExitStack, sesion: SesionReal, real: Optional[dict],
                      motivo_real: Optional[str], inf: dict, canales: dict,
                      secretos: list[str], *, token: str, app_id: str, post: Poster,
                      abrir: Callable[[str], Any]) -> None:
    """Las reglas de 5e, en orden. Cualquier condición que falla deja el
    canal real afuera y lo informa; ninguna lanza hacia la sonda."""
    if not en_ventana(sesion.reloj()):
        inf["no_aplica"] = "fuera de la ventana de la autorización"
        return
    if real is None:
        inf["detenida"] = motivo_real
        return
    if "balance" in real:
        inf["saldo"] = {"fuente": "GET /accounts", "es_cero": _num(real.get("balance")) == 0}
        if _num(real.get("balance")) != 0:
            inf["detenida"] = "saldo de la cuenta real distinto de 0: no se pidió el OTP"
            return
    inf["otp"], url = await sesion.emitir_otp(real, token=token, app_id=app_id, post=post)
    if not url:
        return
    secretos.append(otp_de(url) or "")
    try:
        cm = sesion.abrir(url, abrir)
    except ValueError as exc:
        inf["no_conectado"] = str(exc)
        return
    pila_real = await pila.enter_async_context(AsyncExitStack())
    try:
        ws = await pila_real.enter_async_context(cm)
    except Exception as exc:   # noqa: BLE001
        inf["handshake"] = {"ok": False, "http": _http_de(exc),
                            "error": _limpiador(tuple(secretos))(f"{type(exc).__name__}: {exc}")}
        return
    inf["handshake"] = {"ok": True}
    canal = CanalReal(ws)
    if "saldo" not in inf:
        entrada, datos = await canal.pedir({"balance": 1})
        saldo = _num(((datos or {}).get("balance") or {}).get("balance"))
        inf["saldo"] = {"fuente": "balance en el canal real", "es_cero": saldo == 0,
                        "ok": entrada["ok"]}
        if saldo != 0:
            inf["detenida"] = "saldo distinto de 0 (o ilegible): se cierra sin enviar nada más"
            inf["mensajes_enviados"] = canal.enviados
            await pila_real.aclose()
            inf["cerrado"] = True
            return
    canales["real"] = canal


@pytest.mark.live
@_SOLO_EN_LIVE_TESTS
async def test_live_sonda_3b_canales_y_rango(capsys):
    if not en_ventana(datetime.now(timezone.utc)):
        pytest.skip("fuera de la ventana de la autorización (05-oct 00:00 .. 09-oct 23:59 UTC)")
    app_id = load_secret(SecretKey.DERIV_APP_ID, required=False)
    token = load_secret(SecretKey.DERIV_API_TOKEN, required=False)
    assert app_id, "SPEL_EXPECT_SECRETS=1 pero DERIV_APP_ID no llegó al job"
    assert token, "SPEL_EXPECT_SECRETS=1 pero DERIV_API_TOKEN no llegó al job"
    informe, secretos = await sondear(token=token, app_id=app_id,
                                      abrir=DerivAdapter._default_connector)
    texto = texto_libre(informe)
    for secreto in secretos:
        if secreto:
            assert secreto not in texto, "un secreto llegó al informe: no se publica"
    _publicar(capsys, informe)
    assert informe["publico"]["handshake"]["ok"], "el WS público, registrado DISPONIBLE, no abrió"


# ═══ Offline ══════════════════════════════════════════════════════════════

from datetime import timedelta  # noqa: E402

from tests.deriv_falso import DerivFalso  # noqa: E402

_UTC = timezone.utc
_AHORA = datetime(2026, 10, 6, 10, 0, 10, tzinfo=_UTC)
_OTP_DEMO, _OTP_REAL = "otpDEMOq9", "otpREALq9"
_ENDPOINT_DEMO = ENDPOINT_PUBLICO_NUEVO.rsplit("/", 1)[0] + "/demo"
_ITEMS = [{"underlying_symbol": "cryBTCUSD", "market": "cryptocurrency"},
          {"underlying_symbol": "frxXAUUSD", "market": "commodities"},
          {"underlying_symbol": "frxEURUSD", "market": "forex"},
          {"underlying_symbol": "R_50", "market": "synthetic_index"}]


def _m5_historia(dias=10):
    fin = int(_AHORA.timestamp()) - int(_AHORA.timestamp()) % 300
    piso = fin - dias * 86400

    def th(p):
        tope = fin if p["end"] == "latest" else int(p["end"])
        ini = max(piso, tope - 4999 * 300)
        if tope < piso:
            return {"candles": []}
        return {"candles": [{"epoch": e, "open": 100.0, "high": 100.2, "low": 99.9,
                             "close": 100.0} for e in range(ini - ini % 300, tope + 1, 300)]}
    return th


class _Falso(DerivFalso):
    """DerivFalso con nombre: anota en `orden` cada proposal que recibe, y
    en `al_cerrar` lo que había pasado en el resto de los canales cuando se
    cerró."""

    def __init__(self, manejadores, nombre, orden):
        super().__init__(manejadores)
        self.nombre, self.orden, self.al_cerrar = nombre, orden, None

    def connector(self, uri):
        cm = super().connector(uri)
        falso = self

        class _CM:
            async def __aenter__(self):
                return await cm.__aenter__()

            async def __aexit__(self, *exc):
                falso.al_cerrar = list(falso.orden)
                return await cm.__aexit__(*exc)
        return _CM()


_ORDEN: list = []


def _fake(kappa: float, *, rechaza_x200=False, saldo=0.0, nombre="x", orden=None):
    orden = _ORDEN if orden is None else orden

    def proposal(p):
        orden.append(nombre)
        if p["multiplier"] == 200 and rechaza_x200 and p["underlying_symbol"] != "cryBTCUSD":
            return {"error": {"code": "ContractBuyValidationError",
                              "message": "Multiplier is not in acceptable range. Accepts 50,100,150,250,500."}}
        n = p["amount"] * p["multiplier"]
        com = round(kappa * n, 2)
        out = {"proposal": {"commission": com, "spot": 100.0,
                            "limit_order": {"stop_out": {"order_amount": -p["amount"],
                                                         "value": str(100 * (1 - (1 / p["multiplier"] - com / n)))}},
                            "validation_params": {"stake": {"min": "1.00", "max": "2000.00"},
                                                  "stop_loss": {"min": "0.10", "max": "2.00"}}}}
        if "limit_order" in p:
            out["proposal"]["limit_order"]["stop_loss"] = {"display_order_amount": f"-{p['limit_order']['stop_loss']:.2f}"}
        return out
    def ticks(p):
        orden.append(f"{nombre}:ticks_history")
        return _m5_historia()(p)
    return _Falso({"active_symbols": lambda p: {"active_symbols": _ITEMS},
                   "proposal": proposal, "ticks_history": ticks,
                   "balance": lambda p: {"balance": {"balance": saldo, "currency": "USD",
                                                     "loginid": "ROT12345678"}}}, nombre, orden)


def _cuentas(*, real=1, balance: Any = 0.0, con_balance=True):
    data = [{"account_id": "DOT90004580", "account_type": "demo", "status": "active",
             "currency": "USD", "balance": 10000}]
    for k in range(real):
        c = {"account_id": f"ROT1234567{k}", "account_type": "real", "status": "active",
             "currency": "USD"}
        if con_balance:
            c["balance"] = balance
        data.append(c)
    return {"data": data}


async def _correr(*, cuentas=None, pub=None, demo=None, real=None, ahora=_AHORA, url_real=None):
    orden: list = []
    pub = pub or _fake(0.0008, rechaza_x200=True, nombre="publico", orden=orden)
    demo = demo or _fake(0.0003, nombre="demo", orden=orden)
    real = real or _fake(0.0005, nombre="real", orden=orden)
    rest: list[tuple[str, str]] = []
    abiertos: list[str] = []

    async def get(u, h):
        rest.append(("GET", u))
        return 200, {}, json.dumps(cuentas or _cuentas())

    async def post(u, h):
        rest.append(("POST", u))
        es_real = "ROT" in u
        url = url_real if (es_real and url_real) else (
            f"{ENDPOINT_REAL}?otp={_OTP_REAL}" if es_real else f"{_ENDPOINT_DEMO}?otp={_OTP_DEMO}")
        return 200, {}, json.dumps({"data": {"url": url}})

    def abrir(u):
        abiertos.append(u)
        f = pub if u == ENDPOINT_PUBLICO_NUEVO else real if _OTP_REAL in u else demo
        return f.connector(u)

    async def dormir(s):
        pass
    informe, secretos = await sondear(token="TOKq7", app_id="APP31", abrir=abrir, get=get,
                                      post=post, reloj=lambda: ahora, dormir=dormir, pausa_s=0)
    return informe, secretos, rest, abiertos, pub, demo, real


# ── 5e: lista blanca, antes de enviar ────────────────────────────────────

class _WsQueAnota:
    def __init__(self):
        self.enviados = []

    async def send(self, texto):
        self.enviados.append(json.loads(texto))

    async def recv(self):
        p = self.enviados[-1]
        return json.dumps({"msg_type": next(iter(p)), "req_id": p["req_id"]})


_MULTUP = {"proposal": 1, "contract_type": "MULTUP", "basis": "stake", "amount": 1,
           "currency": "USD", "underlying_symbol": "cryBTCUSD", "multiplier": 100}


@pytest.mark.parametrize("payload", [
    {"time": 1}, {"balance": 1}, {"contracts_for": "cryBTCUSD"}, _MULTUP,
    {**_MULTUP, "limit_order": {"stop_loss": 0.3}},
])
async def test_la_lista_blanca_real_deja_pasar_lo_permitido(payload):
    ws = _WsQueAnota()
    entrada, _ = await CanalReal(ws).pedir(payload)
    assert entrada["ok"] and len(ws.enviados) == 1


@pytest.mark.parametrize("payload", [
    {"buy": "abc", "price": 1}, {"sell": 1, "price": 0}, {"cancel": 1},
    {"portfolio": 1}, {"proposal_open_contract": 1}, {"transaction": 1, "subscribe": 1},
    {"contract_update": 1}, {"ticks_history": "R_50", "end": "latest"}, {"active_symbols": "brief"},
    {**_MULTUP, "subscribe": 1}, {"balance": 1, "subscribe": 1},
    {**_MULTUP, "buy": 1}, {**_MULTUP, "passthrough": {}},
    {**_MULTUP, "contract_type": "MULTDOWN"}, {**_MULTUP, "contract_type": "CALL"},
    {"time": 1, "buy": 1}, {},
])
async def test_la_lista_blanca_real_lanza_antes_de_enviar(payload):
    ws = _WsQueAnota()
    canal = CanalReal(ws)
    with pytest.raises(MensajeRealNoPermitido):
        await canal.pedir(payload)
    assert ws.enviados == [] and canal.enviados == 0


async def test_el_tope_de_mensajes_lanza_antes_de_enviar_el_41():
    ws = _WsQueAnota()
    canal = CanalReal(ws)
    for _ in range(MAX_MENSAJES_REAL):
        await canal.pedir({"time": 1})
    with pytest.raises(TopeDeMensajesReal):
        await canal.pedir({"time": 1})
    assert len(ws.enviados) == MAX_MENSAJES_REAL == 40


@pytest.mark.parametrize("momento, dentro", [
    (datetime(2026, 10, 4, 23, 59, 59, tzinfo=_UTC), False),
    (datetime(2026, 10, 5, 0, 0, 0, tzinfo=_UTC), True),
    (datetime(2026, 10, 9, 23, 59, 59, tzinfo=_UTC), True),
    (datetime(2026, 10, 10, 0, 0, 0, tzinfo=_UTC), False),
])
def test_la_ventana(momento, dentro):
    assert en_ventana(momento) is dentro


def test_exactamente_una_cuenta_real():
    assert cuenta_real_unica(_cuentas()["data"])[0]["account_type"] == "real"
    assert cuenta_real_unica(_cuentas(real=0)["data"])[0] is None
    assert cuenta_real_unica(_cuentas(real=2)["data"])[0] is None
    assert "2 cuentas" in cuenta_real_unica(_cuentas(real=2)["data"])[1]


@pytest.mark.parametrize("url, conecta", [
    (f"{ENDPOINT_REAL}?otp=abc", True),
    (f"{_ENDPOINT_DEMO}?otp=abc", False),
    (f"{ENDPOINT_PUBLICO_NUEVO}?otp=abc", False),
    (f"{ENDPOINT_REAL.replace('api.derivws.com', 'otro.example')}?otp=abc", False),
    (f"{ENDPOINT_REAL.replace('wss:', 'ws:')}?otp=abc", False),
    (ENDPOINT_REAL, False),
])
def test_solo_se_conecta_al_canal_real_verificado(url, conecta):
    assert (motivo_para_no_conectar_real(url) is None) is conecta


async def _post_que_anota(llamadas):
    async def post(u, h):
        llamadas.append(u)
        return 200, {}, json.dumps({"data": {"url": f"{ENDPOINT_REAL}?otp=x"}})
    return post


async def test_la_sesion_real_es_de_un_solo_uso():
    llamadas: list = []
    post = await _post_que_anota(llamadas)
    s = SesionReal(reloj=lambda: _AHORA)
    real = _cuentas()["data"][1]
    _, url = await s.emitir_otp(real, token="T", app_id="A", post=post)
    with pytest.raises(UsoUnicoAgotado):
        await s.emitir_otp(real, token="T", app_id="A", post=post)
    assert len(llamadas) == 1
    s.abrir(url, lambda u: u)
    with pytest.raises(UsoUnicoAgotado):
        s.abrir(url, lambda u: u)


async def test_la_sesion_real_no_emite_ni_abre_fuera_de_la_ventana():
    llamadas: list = []
    post = await _post_que_anota(llamadas)
    s = SesionReal(reloj=lambda: datetime(2026, 10, 10, tzinfo=_UTC))
    with pytest.raises(FueraDeVentana):
        await s.emitir_otp(_cuentas()["data"][1], token="T", app_id="A", post=post)
    with pytest.raises(FueraDeVentana):
        s.abrir(f"{ENDPOINT_REAL}?otp=x", lambda u: u)
    assert llamadas == []


async def test_la_sesion_real_no_emite_para_una_cuenta_demo():
    llamadas: list = []
    post = await _post_que_anota(llamadas)
    with pytest.raises(ValueError):
        await SesionReal(reloj=lambda: _AHORA).emitir_otp(
            _cuentas()["data"][0], token="T", app_id="A", post=post)
    assert llamadas == []


def test_la_sesion_real_no_abre_otra_url():
    with pytest.raises(ValueError):
        SesionReal(reloj=lambda: _AHORA).abrir(f"{_ENDPOINT_DEMO}?otp=x", lambda u: u)


# ── 5d ───────────────────────────────────────────────────────────────────

def test_las_combinaciones_del_brief():
    c = combinaciones(["A"])
    assert [(x["stake"], x["multiplicador"], x["stop_loss"]) for x in c] == [
        (1.0, 50.0, None), (1.0, 100.0, None), (1.0, 200.0, None),
        (2.0, 50.0, None), (2.0, 100.0, None), (2.0, 200.0, None), (1.0, 100.0, 0.30)]


async def test_esperar_margen_al_final_del_minuto():
    dormido: list = []

    async def dormir(s):
        dormido.append(s)
    await esperar_margen(lambda: datetime(2026, 10, 6, 10, 0, 56, tzinfo=_UTC), dormir)
    assert dormido and 4 < dormido[0] < 4.2
    dormido.clear()
    await esperar_margen(lambda: datetime(2026, 10, 6, 10, 0, 54, tzinfo=_UTC), dormir)
    assert dormido == []


def test_tabla_kappa_y_la_regla_del_admin():
    def fila(sim, **canales):
        return {"simbolo": sim, "canales": {c: ({"ok": True, "kappa": k} if k is not None
                                                else {"ok": False}) for c, k in canales.items()}}
    filas = [fila("A", publico=0.0008, demo=0.0003, real=0.0005),
             fila("A", publico=0.00075, demo=0.0003, real=0.0006),
             fila("B", publico=0.0003, demo=0.0002, real=None)]
    t = tabla_kappa(filas)
    assert t["A"]["por_canal"] == {"publico": 0.0008, "demo": 0.0003, "real": 0.0006}
    assert t["A"]["kappa"] == 0.0006 and t["A"]["origen"] == "real", \
        "el máximo en real, aunque el público mida más (0,0008)"
    assert t["B"]["kappa"] == 0.0003 and t["B"]["origen"] == "respaldo"
    assert t["B"]["por_canal"]["real"] is None


# ── La sonda entera ──────────────────────────────────────────────────────

async def test_la_sonda_entera_con_los_tres_canales():
    informe, secretos, rest, abiertos, pub, demo, real = await _correr()
    assert rest == [("GET", BASE_REST + "/trading/v1/options/accounts"),
                    ("POST", BASE_REST + "/trading/v1/options/accounts/DOT90004580/otp"),
                    ("POST", BASE_REST + "/trading/v1/options/accounts/ROT12345670/otp")]
    assert abiertos == [ENDPOINT_PUBLICO_NUEVO, f"{_ENDPOINT_DEMO}?otp={_OTP_DEMO}",
                        f"{ENDPOINT_REAL}?otp={_OTP_REAL}"]
    filas = informe["5d"]
    assert len(filas) == 21 and all(set(f["canales"]) == {"publico", "demo", "real"} for f in filas)
    assert all(f["mismo_minuto_utc"] for f in filas)
    x200 = [f for f in filas if f["multiplicador"] == 200 and f["simbolo"] == "frxXAUUSD"]
    assert x200[0]["canales"]["publico"]["multiplicador_aceptado"] is False
    assert "Accepts 50,100,150,250,500" in x200[0]["canales"]["publico"]["error"]["message"]
    assert x200[0]["canales"]["demo"]["multiplicador_aceptado"] is True
    tipos_real = [next(iter(m)) for m in real.enviados]
    assert tipos_real == ["proposal"] * 21, "saldo leído de /accounts: sin mensaje balance"
    assert all(m["contract_type"] == "MULTUP" and "subscribe" not in m for m in real.enviados)
    assert informe["real"]["mensajes_enviados"] == 21 <= MAX_MENSAJES_REAL
    assert informe["real"]["saldo"] == {"fuente": "GET /accounts", "es_cero": True}
    assert informe["real"]["cerrado"] is True
    assert demo.cierres == 1 and real.cierres == 1, "demo y real se cierran"
    cotizaciones = [x for x in pub.orden if ":" not in x]
    assert cotizaciones == ["publico", "demo", "real"] * 21, "intercalado, en ese orden"
    assert not any(x.endswith("ticks_history") for x in demo.al_cerrar + real.al_cerrar), \
        "demo y real se cierran apenas termina 5d, antes de bajar las velas de 5c"
    assert {next(iter(m)) for m in demo.enviados} == {"proposal"}
    k = informe["kappa"]["frxXAUUSD"]
    assert k["origen"] == "real" and k["kappa"] == pytest.approx(0.0005, abs=1e-4)
    c = informe["5c"]["oro"]
    assert c["simbolo"] == "frxXAUUSD" and c["kappa"] == k["kappa"] and c["dias"] > 0, \
        "5c usa el κ de la regla: el real, no el máximo de los tres canales"
    assert informe["5c"]["btc"]["ancla"] == "09:30 America/New_York"
    texto = texto_libre(informe)
    for s in (_OTP_DEMO, _OTP_REAL, "TOKq7", "APP31"):
        assert s in secretos and s not in texto
    assert "ROT12345670" not in json.dumps(informe)


async def test_sin_saldo_en_accounts_el_primer_mensaje_real_es_balance():
    informe, _, _, _, _, _, real = await _correr(cuentas=_cuentas(con_balance=False))
    assert next(iter(real.enviados[0])) == "balance"
    assert informe["real"]["saldo"]["fuente"] == "balance en el canal real"
    assert informe["real"]["saldo"]["es_cero"] is True
    assert len(real.enviados) == 22


async def test_saldo_positivo_en_el_canal_real_cierra_sin_enviar_mas():
    orden: list = []
    informe, _, _, _, pub, _, real = await _correr(
        cuentas=_cuentas(con_balance=False),
        pub=_fake(0.0008, nombre="publico", orden=orden),
        demo=_fake(0.0003, nombre="demo", orden=orden),
        real=_fake(0.0005, saldo=12.5, nombre="real", orden=orden))
    assert [next(iter(m)) for m in real.enviados] == ["balance"]
    assert real.cierres == 1 and informe["real"]["cerrado"] is True
    assert real.al_cerrar == [], "se cerró en el acto, antes de cualquier cotización de 5d"
    assert "detenida" in informe["real"]
    assert all("real" not in f["canales"] for f in informe["5d"])
    assert informe["kappa"]["cryBTCUSD"]["origen"] == "respaldo"
    assert "12.5" not in json.dumps(informe["real"]), "el saldo no se publica, solo si es 0"


async def test_saldo_positivo_en_accounts_no_pide_el_otp_real():
    informe, _, rest, abiertos, *_ = await _correr(cuentas=_cuentas(balance=3.0))
    assert [r for r in rest if "ROT" in r[1]] == []
    assert len(abiertos) == 2 and "detenida" in informe["real"]


async def test_dos_cuentas_reales_detienen_sin_otp():
    informe, _, rest, abiertos, *_ = await _correr(cuentas=_cuentas(real=2))
    assert [r for r in rest if "ROT" in r[1]] == []
    assert "exactamente una" in informe["real"]["detenida"]


async def test_fuera_de_la_ventana_no_hay_canal_real():
    informe, _, rest, abiertos, *_ = await _correr(ahora=datetime(2026, 10, 10, 1, tzinfo=_UTC))
    assert [r for r in rest if "ROT" in r[1]] == []
    assert informe["real"] == {"no_aplica": "fuera de la ventana de la autorización"}


async def test_si_la_url_real_no_es_la_verificada_no_se_conecta():
    informe, _, _, abiertos, *_ = await _correr(url_real=f"{_ENDPOINT_DEMO}?otp={_OTP_REAL}")
    assert all(_OTP_REAL not in a for a in abiertos)
    assert "no es la del canal real" in informe["real"]["no_conectado"]


async def test_el_tope_real_se_informa_como_no_enviado():
    ws = _WsQueAnota()
    canal = CanalReal(ws, tope=2)
    filas = await comparar_canales({"real": canal}, {"real": "USD"}, ["cryBTCUSD"],
                                   reloj=lambda: _AHORA)
    assert len(ws.enviados) == 2
    assert "TopeDeMensajesReal" in filas[2]["canales"]["real"]["no_enviado"]
