"""
tests/test_reintentos.py
==========================
Reintentos con Retry-After en la capa de ingesta (issue #39; brief del
Admin del 14-oct-2026 (9), tarea C). Offline: el transporte es un
httpx.MockTransport, la espera y el reloj son falsos. Ningún test duerme.

El criterio viejo, que estos tests separan del nuevo: `fetch_pagina` no
reintentaba nada. Un 429 era un `AdapterConnectionError` en la primera
llamada, y la descarga de velas cortaba en "error".
"""

from __future__ import annotations

import json
import logging
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from ingestion import reintentos as r
from ingestion import velas_intradia as vi
from ingestion.adapters import (
    AdapterAuthError,
    AdapterConnectionError,
    TwelveDataAdapter,
)
from ingestion.limitador import Limitador
from ingestion.velas import ruta_serie
from tests.test_twelvedata_pagina import DATE, RESPUESTA_400_SIN_DATOS, XAU_5MIN

KEY = "clave-falsa-de-reintentos"
#: DATE (la cabecera Date del servidor) en epoch.
AHORA = datetime(2026, 10, 6, 10, 12, 30, tzinfo=timezone.utc).timestamp()


@pytest.fixture(autouse=True)
def _drive(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(tmp_path))


# ═══ Las piezas de un transporte falso ════════════════════════════════════

def _ok():
    return httpx.Response(200, json=XAU_5MIN, headers={"Date": DATE})


def _error(code, status=200, **cabeceras):
    return httpx.Response(status, json={"code": code, "message": "m", "status": "error"},
                          headers={"Date": DATE, **cabeceras})


def _html(status, **cabeceras):
    return httpx.Response(status, content=b"<html>Bad Gateway</html>",
                          headers={"Date": DATE, **cabeceras})


class _Lim:
    """Un limitador falso: cuenta los turnos y puede negarse."""

    def __init__(self, creditos=100):
        self.creditos = creditos
        self.turnos = 0

    async def turno(self):
        if self.turnos >= self.creditos:
            return False
        self.turnos += 1
        return True


class _Mundo:
    """Respuestas en orden (una respuesta o una excepción de httpx por
    llamada), las esperas y las llamadas que hubo."""

    def __init__(self, *respuestas, limitador="falso", reloj=lambda: 0.0):
        self.respuestas = list(respuestas)
        self.llamadas = 0
        self.esperas: list[float] = []
        self.lim = _Lim() if limitador == "falso" else limitador

        def handler(req: httpx.Request) -> httpx.Response:
            self.llamadas += 1
            resp = self.respuestas.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp

        async def dormir(s):
            self.esperas.append(s)
        self.adapter = TwelveDataAdapter(
            api_key=KEY, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            limitador=self.lim, dormir=dormir, reloj=reloj)

    async def pagina(self):
        return await self.adapter.fetch_pagina("XAUUSD", "5m")


# ═══ Retry-After ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("valor, esperado", [
    ("120", 120.0), (" 7 ", 7.0), ("0", 0.0),
    (format_datetime(datetime.fromtimestamp(AHORA + 30, tz=timezone.utc), usegmt=True), 30.0),
    (format_datetime(datetime.fromtimestamp(AHORA - 30, tz=timezone.utc), usegmt=True), 0.0),
    ("Wed, 21 Oct 2026 07:28:00", None),     # fecha sin zona
    ("-5", None), ("1.5", None), ("mañana", None), ("", None), (None, None),
])
def test_retry_after_en_segundos_o_fecha_http(valor, esperado):
    assert r.segundos_de_retry_after(valor, ahora_epoch=AHORA) == esperado


def test_la_espera_por_defecto():
    assert r.espera_de(r.Transitorio("x", es_429=True, retry_after_s=7.0), 1) == 7.0
    assert r.espera_de(r.Transitorio("x", retry_after_s=0.0), 3) == 0.0
    assert r.espera_de(r.Transitorio("x", es_429=True), 1) == 60.0
    assert [r.espera_de(r.Transitorio("x"), n) for n in range(1, 7)] == [2, 4, 8, 16, 30, 30]


@pytest.mark.parametrize("status, sale", [
    (429, True), (500, True), (502, True), (503, True), (504, True),
    (400, False), (401, False), (403, False), (404, False), (501, False), (505, False),
    ("429", False), (None, False), (200, False),
])
def test_que_status_se_reintenta(status, sale):
    assert r.es_status_reintentable(status) is sale


# ═══ 429 ══════════════════════════════════════════════════════════════════

async def test_429_con_retry_after_espera_eso_y_devuelve_la_pagina():
    m = _Mundo(_error(429, status=429, **{"Retry-After": "7"}), _ok())
    p = await m.pagina()
    assert len(p.velas) == 2 and not p.vacia
    assert m.esperas == [7.0] and m.llamadas == 2 and m.lim.turnos == 1


async def test_429_en_el_cuerpo_con_http_200_tambien_se_reintenta():
    m = _Mundo(_error(429), _ok())
    await m.pagina()
    assert m.esperas == [60.0] and m.llamadas == 2


async def test_429_con_retry_after_en_fecha_usa_la_hora_del_servidor():
    """El reloj local dice otra cosa: manda la cabecera Date."""
    tras = format_datetime(datetime.fromtimestamp(AHORA + 20, tz=timezone.utc), usegmt=True)
    m = _Mundo(_error(429, status=429, **{"Retry-After": tras}), _ok(), reloj=lambda: 0.0)
    await m.pagina()
    assert m.esperas == [20.0]


async def test_429_con_retry_after_en_fecha_sin_date_usa_el_reloj():
    tras = format_datetime(datetime.fromtimestamp(AHORA + 20, tz=timezone.utc), usegmt=True)
    sin_date = httpx.Response(429, json={"code": 429, "status": "error"},
                              headers={"Retry-After": tras})
    m = _Mundo(sin_date, _ok(), reloj=lambda: AHORA + 5)
    await m.pagina()
    assert m.esperas == [15.0]


async def test_429_persistente_es_error_explicito_tras_el_tope():
    m = _Mundo(*[_error(429, status=429)] * 4)
    with pytest.raises(AdapterConnectionError, match="sin éxito tras 3 reintentos") as e:
        await m.pagina()
    assert "HTTP 429" in str(e.value) and "180 s de espera" in str(e.value)
    assert m.llamadas == 4 and m.esperas == [60.0] * 3 and m.lim.turnos == 3


async def test_429_persistente_no_escribe_velas(monkeypatch):
    monkeypatch.setattr(vi.time, "time", lambda: AHORA)
    m = _Mundo(*[_error(429, status=429)] * 4)
    lim = Limitador(100, 50, reloj=lambda: 0.0, dormir=_nada)
    m.adapter._limitador = lim
    _, res = await vi.ingerir_td(m.adapter, write=True, limitador=lim)
    assert res["corte"] == "error" and res["completo"] is False
    assert "sin éxito tras 3 reintentos" in res["paginas"][-1]["error"]
    assert not ruta_serie("td_XAUUSD", 300).exists()
    # 1 crédito de la página y 3 de los reintentos, en el mismo limitador.
    assert lim.creditos == 4 and res["creditos_usados"] == 4


async def _nada(s):
    return None


# ═══ 5xx y red ════════════════════════════════════════════════════════════

@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_un_5xx_se_reintenta_aunque_el_cuerpo_no_sea_json(status):
    m = _Mundo(_html(status), _ok())
    p = await m.pagina()
    assert len(p.velas) == 2 and m.esperas == [2.0]


async def test_un_5xx_en_el_cuerpo_con_http_200_se_reintenta():
    m = _Mundo(_error(503), _ok())
    await m.pagina()
    assert m.esperas == [2.0]


async def test_el_backoff_crece_y_esta_acotado():
    m = _Mundo(_html(503), _html(503), _html(503), _ok())
    await m.pagina()
    assert m.esperas == [2.0, 4.0, 8.0] and m.llamadas == 4


async def test_un_5xx_con_retry_after_espera_eso():
    m = _Mundo(_html(503, **{"Retry-After": "11"}), _ok())
    await m.pagina()
    assert m.esperas == [11.0]


@pytest.mark.parametrize("exc", [httpx.ConnectError("x"), httpx.ReadTimeout("x"),
                                 httpx.ConnectTimeout("x"), httpx.ReadError("x"),
                                 httpx.RemoteProtocolError("x")])
async def test_un_corte_de_red_se_reintenta(exc):
    m = _Mundo(exc, _ok())
    await m.pagina()
    assert m.esperas == [2.0] and m.llamadas == 2


@pytest.mark.parametrize("exc", [httpx.UnsupportedProtocol("x"), httpx.LocalProtocolError("x")])
async def test_un_error_de_red_que_no_es_transitorio_no_se_reintenta(exc):
    m = _Mundo(exc, _ok())
    with pytest.raises(AdapterConnectionError):
        await m.pagina()
    assert m.llamadas == 1 and m.esperas == [] and m.lim.turnos == 0


# ═══ Lo que NO se reintenta ═══════════════════════════════════════════════

async def test_el_400_del_fondo_no_se_reintenta_y_sigue_siendo_pagina_vacia():
    m = _Mundo(httpx.Response(400, json=RESPUESTA_400_SIN_DATOS, headers={"Date": DATE}))
    p = await m.pagina()
    assert p.vacia and p.motivo.startswith("400: No data is available")
    assert m.llamadas == 1 and m.esperas == [] and m.lim.turnos == 0


@pytest.mark.parametrize("code, status, excepcion", [
    (400, 400, AdapterConnectionError),
    (401, 401, AdapterAuthError),
    (403, 403, AdapterConnectionError),
    (501, 501, AdapterConnectionError),
    ("429", 200, AdapterConnectionError),
])
async def test_estos_errores_no_se_reintentan(code, status, excepcion):
    m = _Mundo(_error(code, status=status), _ok())
    with pytest.raises(excepcion):
        await m.pagina()
    assert m.llamadas == 1 and m.esperas == [] and m.lim.turnos == 0


async def test_el_404_del_fondo_no_se_reintenta():
    m = _Mundo(httpx.Response(404, json={"code": 404, "message": "Data not found",
                                         "status": "error"}, headers={"Date": DATE}))
    assert (await m.pagina()).vacia and m.llamadas == 1


# ═══ Topes ════════════════════════════════════════════════════════════════

async def test_un_retry_after_que_pasa_el_tope_no_se_espera():
    m = _Mundo(_error(429, status=429, **{"Retry-After": "181"}), _ok())
    with pytest.raises(AdapterConnectionError, match="tope de 180 s"):
        await m.pagina()
    assert m.esperas == [] and m.llamadas == 1 and m.lim.turnos == 0


async def test_la_espera_total_se_acumula():
    m = _Mundo(_error(429, status=429, **{"Retry-After": "100"}),
               _error(429, status=429, **{"Retry-After": "80"}),
               _error(429, status=429, **{"Retry-After": "81"}), _ok())
    with pytest.raises(AdapterConnectionError, match="tope de 180 s"):
        await m.pagina()
    assert m.esperas == [100.0, 80.0] and m.llamadas == 3


async def test_justo_en_el_tope_todavia_se_espera():
    m = _Mundo(_error(429, status=429, **{"Retry-After": "180"}), _ok())
    await m.pagina()
    assert m.esperas == [180.0]


async def test_sin_credito_en_el_limitador_no_se_reintenta():
    m = _Mundo(_error(429, status=429, **{"Retry-After": "1"}), _ok(), limitador=_Lim(0))
    with pytest.raises(AdapterConnectionError, match="limitador no tiene crédito"):
        await m.pagina()
    assert m.esperas == [] and m.llamadas == 1


async def test_cada_reintento_consume_un_credito_del_limitador():
    lim = Limitador(100, 10, reloj=lambda: 0.0, dormir=_nada)
    m = _Mundo(_html(502), _html(502), _ok(), limitador=lim)
    await m.pagina()
    assert lim.creditos == 2


async def test_el_tope_por_minuto_del_limitador_tambien_frena_los_reintentos():
    reloj = [0.0]
    pausas = []

    async def dormir(s):
        pausas.append(s)
        reloj[0] += s
    lim = Limitador(1, 10, reloj=lambda: reloj[0], dormir=dormir)
    await lim.turno()                        # la página, como en descargar_td
    m = _Mundo(_html(503), _ok(), limitador=lim)
    await m.pagina()
    assert pausas == [60.0] and m.esperas == [2.0]


# ═══ El criterio viejo, separado del nuevo ════════════════════════════════

async def test_sin_limitador_no_hay_reintentos():
    """Lo de antes, intacto: sin limitador no hay con qué contar créditos,
    así que un 429 es error en la primera llamada."""
    respuestas = [_error(429, status=429, **{"Retry-After": "1"}), _ok()]
    llamadas = []

    def handler(req):
        llamadas.append(req)
        return respuestas.pop(0)
    adapter = TwelveDataAdapter(api_key=KEY, client=httpx.AsyncClient(
        transport=httpx.MockTransport(handler)))
    with pytest.raises(AdapterConnectionError, match="429"):
        await adapter.fetch_pagina("XAUUSD", "5m")
    assert len(llamadas) == 1


async def test_con_limitador_el_mismo_429_se_recupera():
    m = _Mundo(_error(429, status=429, **{"Retry-After": "1"}), _ok())
    assert len((await m.pagina()).velas) == 2


# ═══ El log ═══════════════════════════════════════════════════════════════

async def test_cada_reintento_deja_un_log_estructurado_sin_la_key(caplog):
    caplog.set_level(logging.WARNING, logger="ingestion.reintentos")
    m = _Mundo(_error(429, status=429, **{"Retry-After": "7"}), _html(503), _ok())
    await m.pagina()
    lineas = [rec.getMessage() for rec in caplog.records if rec.name == "ingestion.reintentos"]
    assert len(lineas) == 2
    datos = [json.loads(linea.split(" ", 1)[1]) for linea in lineas]
    assert datos[0] == {"fuente": "twelvedata", "reintento": 1, "de": 3, "motivo": "HTTP 429",
                        "espera_s": 7.0, "origen_espera": "retry-after", "esperado_s": 0.0}
    assert datos[1]["origen_espera"] == "backoff" and datos[1]["esperado_s"] == 7.0
    assert all(KEY not in linea for linea in lineas)
