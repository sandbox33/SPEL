"""
integracion_demo/otp.py
=======================
OTP para la cuenta demo de Deriv, y nada más (brief del Admin del
06-oct-2026 (3), punto 3a).

PORTADO, NO REESCRITO. Cada función sale tal cual de la sonda donde se
midió en vivo; las sondas ahora la importan de acá:
  · De tests/test_deriv_sonda3_live.py (sonda §0.A-3, run 37318228891):
    RUTA_OTP, RUTA_CUENTAS, ENDPOINT_DEMO, Poster, _post_httpx, _headers,
    leer_cuentas, elegir_demo (el filtro por account_type), CuentaNoDemoError,
    emitir_otp_demo, otp_de y motivo_para_no_conectar.
  · De tests/test_deriv_sonda2_live.py (sonda §0.A-2), que la sonda 3
    importaba: BASE_REST, NOTA_401, Getter, _get_httpx, el enmascarado
    (enmascarar_id, enmascarar_ids) y _limpiador.
La única diferencia es ENDPOINT_DEMO: la sonda 3 lo derivaba del canal
público de tests/test_deriv_endpoints_live.py, y este paquete no importa
de tests/. Queda escrito entero; el test de la sonda 3 que lo compara con
el público sigue verificando que es el mismo.

Lo nuevo es `url_demo_nueva`: emite UN OTP y devuelve la URL solo si es la
de /ws/demo con su `otp`; si no, lanza. La conexión la llama una vez por
cada conexión (un OTP sirve una sola vez, 120 s, según el OpenAPI oficial,
github.com/deriv-com/deriv-api-schemas, commit 54e3538).

Esquemas: `GET /trading/v1/options/accounts` y
`POST /trading/v1/options/accounts/{accountId}/otp`, con Bearer y el header
`Deriv-App-ID`. El OTP viaja dentro de la URL: la URL nunca va a un
informe ni a un log.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import parse_qs, urlsplit

import httpx

from ingestion.deriv_ws import TIMEOUT_RESPUESTA_S

#: El host REST de la Options API nueva, el mismo del ws/public verificado.
BASE_REST = "https://api.derivws.com"

RUTA_OTP = "/trading/v1/options/accounts/{account_id}/otp"
RUTA_CUENTAS = "/trading/v1/options/accounts"

#: El canal demo. La única URL a la que este paquete se conecta.
ENDPOINT_DEMO = "wss://api.derivws.com/trading/v1/options/ws/demo"

#: Qué dice el informe ante un 401 en la parte autenticada. El token no
#: tiene fecha de vencimiento conocida (decision-log 2026-10-01): un 401 se
#: lee como posible vencimiento, no como un fallo del código.
NOTA_401 = ("posible vencimiento del DERIV_API_TOKEN: su fecha de vencimiento es "
            "DESCONOCIDA (decision-log 2026-10-01). No es un fallo de código.")

#: GET async: (url, headers) -> (status HTTP, headers de la respuesta, cuerpo).
Getter = Callable[[str, dict], Awaitable[tuple[int, dict, str]]]

#: POST async: (url, headers) -> (status, headers de la respuesta, cuerpo).
Poster = Callable[[str, dict], Awaitable[tuple[int, dict, str]]]


# ═══ Enmascarado ══════════════════════════════════════════════════════════

def enmascarar_id(cuenta: str) -> str:
    """Los dígitos de un account_id, tapados. Queda el prefijo de letras,
    que dice la clase de cuenta y no la cuenta."""
    return re.sub(r"\d", "*", str(cuenta))


def enmascarar_ids(texto: str) -> str:
    """Toda tira de 4 o más dígitos, tapada: así se ve un account_id o un
    loginid dentro de un mensaje de error. Lo que queda (códigos, palabras)
    dice por qué falló."""
    return re.sub(r"\d{4,}", lambda m: "*" * len(m.group()), texto)


def _limpiador(secretos: tuple[str, ...]) -> Callable[[str], str]:
    def limpiar(texto: str) -> str:
        for s in secretos:
            if s:
                texto = texto.replace(s, "***")
        return texto
    return limpiar


# ═══ REST ═════════════════════════════════════════════════════════════════

async def _get_httpx(url: str, headers: dict) -> tuple[int, dict, str]:
    async with httpx.AsyncClient(timeout=TIMEOUT_RESPUESTA_S) as cliente:
        r = await cliente.get(url, headers=headers)
        return r.status_code, dict(r.headers), r.text


async def _post_httpx(url: str, headers: dict) -> tuple[int, dict, str]:
    async with httpx.AsyncClient(timeout=TIMEOUT_RESPUESTA_S) as cliente:
        r = await cliente.post(url, headers=headers)
        return r.status_code, dict(r.headers), r.text


def _headers(token: str, app_id: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Deriv-App-ID": app_id}


async def leer_cuentas(*, token: str, app_id: str, get: Getter = _get_httpx
                       ) -> tuple[dict, list[dict]]:
    """GET /accounts, un solo pedido. Devuelve el informe publicable (IDs
    tapados) y las cuentas crudas, que no salen de quien llama."""
    limpiar = _limpiador((token, app_id))
    informe: dict[str, Any] = {"ok": False}
    try:
        status, _, cuerpo = await get(BASE_REST + RUTA_CUENTAS, _headers(token, app_id))
    except Exception as exc:   # noqa: BLE001
        informe["error"] = limpiar(f"{type(exc).__name__}: {exc}")
        return informe, []
    informe["http"] = status
    informe["sha256"] = hashlib.sha256(cuerpo.encode("utf-8")).hexdigest()
    if status != 200:
        informe["error"] = enmascarar_ids(limpiar(cuerpo[:400]))
        if status == 401:
            informe["nota"] = NOTA_401
        return informe, []
    try:
        cuentas = json.loads(cuerpo).get("data")
    except (json.JSONDecodeError, AttributeError):
        cuentas = None
    if not isinstance(cuentas, list):
        informe["error"] = "200 sin `data` como lista"
        return informe, []
    informe["ok"] = True
    informe["cuentas"] = [{"account_id": enmascarar_id(c.get("account_id", "")),
                           "account_type": c.get("account_type"), "status": c.get("status")}
                          for c in cuentas]
    return informe, cuentas


def elegir_demo(cuentas: list[dict]) -> Optional[dict]:
    """La primera cuenta demo activa, o None. Nada que no diga demo."""
    demo = [c for c in cuentas if c.get("account_type") == "demo"
            and c.get("status") == "active" and c.get("account_id")]
    return demo[0] if demo else None


class CuentaNoDemoError(RuntimeError):
    """Se pidió un OTP para una cuenta que /accounts no dice demo."""


async def emitir_otp_demo(cuenta: dict, *, token: str, app_id: str,
                          post: Poster = _post_httpx) -> tuple[dict, Optional[str]]:
    """UN OTP para la cuenta demo. Lanza, sin tocar la red, si la cuenta no
    es demo: la guarda no depende de quien llama. Devuelve el informe y la
    URL, que no va al informe porque lleva el OTP."""
    if cuenta.get("account_type") != "demo":
        raise CuentaNoDemoError(f"account_type {cuenta.get('account_type')!r}: "
                                f"solo se emite OTP para demo")
    limpiar = _limpiador((token, app_id))
    ruta = RUTA_OTP.format(account_id=cuenta["account_id"])
    informe: dict[str, Any] = {"ok": False,
                               "cuenta": enmascarar_id(cuenta["account_id"])}
    try:
        status, _, cuerpo = await post(BASE_REST + ruta, _headers(token, app_id))
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


def otp_de(url: str) -> Optional[str]:
    return (parse_qs(urlsplit(url).query).get("otp") or [None])[0]


def motivo_para_no_conectar(url: str) -> Optional[str]:
    """None si la URL es la de /ws/demo, con OTP; si no, por qué no se
    conecta. Lo único que se compara es esquema, host y ruta."""
    u, demo = urlsplit(url), urlsplit(ENDPOINT_DEMO)
    if (u.scheme, u.hostname, u.path.rstrip("/")) != (demo.scheme, demo.hostname, demo.path):
        return (f"la URL del OTP no es la de /ws/demo: {u.scheme}://{u.hostname}{u.path} "
                f"(se esperaba {demo.scheme}://{demo.hostname}{demo.path})")
    if not otp_de(url):
        return "la URL del OTP no trae el parámetro `otp`"
    return None


# ═══ Lo nuevo: un OTP por conexión ════════════════════════════════════════

class OtpNoEmitidoError(RuntimeError):
    """Deriv no devolvió una URL con OTP. El mensaje es publicable: lleva
    el informe de `emitir_otp_demo`, con los IDs tapados."""


class UrlNoDemoError(RuntimeError):
    """La URL del OTP no es la de /ws/demo, o no trae `otp`."""


async def url_demo_nueva(cuenta: dict, *, token: str, app_id: str,
                         post: Poster = _post_httpx) -> str:
    """Un OTP nuevo para la cuenta demo y la URL con la que se conecta.
    Lanza CuentaNoDemoError (sin tocar la red), OtpNoEmitidoError o
    UrlNoDemoError. El mensaje de UrlNoDemoError no lleva la URL: lleva
    esquema, host y ruta, que no son el OTP."""
    informe, url = await emitir_otp_demo(cuenta, token=token, app_id=app_id, post=post)
    if url is None:
        raise OtpNoEmitidoError(json.dumps(informe, ensure_ascii=False))
    motivo = motivo_para_no_conectar(url)
    if motivo:
        raise UrlNoDemoError(motivo)
    return url
