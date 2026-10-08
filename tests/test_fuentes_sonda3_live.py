"""
tests/test_fuentes_sonda3_live.py
===================================
Sonda §0.A-3 v2, partes B y C (brief del Admin del 02-oct-2026): fuentes
externas de precios para la candidata intradía. SOLO MIDE.

══ RETIRADA DEL JOB LIVE EL 05-OCT ══

La sonda corrió el 05-oct (run 37318228891) y sus resultados están en el
decision-log de esa fecha. Las entradas `live` se retiraron: volver a
correrlas gastaría créditos de TwelveData que la sonda §0.A-3b necesita. Las
funciones y sus tests offline quedan, porque la 3b las reutiliza. La parte
de cTrader se retiró del todo (decisión del Admin del 05-oct). Desde el
06-oct (decision-log de esa fecha), toda llamada INTRADÍA a TwelveData lleva
`timezone=UTC` y 1d no la lleva, porque es una barra de solo fecha: el
intradía de XAU/USD llegó sin zona con unas 11 horas de corrimiento. Es la
misma regla de `TwelveDataAdapter`, y `zona_twelvedata()` la lee de ahí.

══ LO QUE CONTESTA ══

  5. TwelveData: BTC/USD y XAU/USD en 1day, 15min y 5min, outputsize 5000,
     más /earliest_timestamp. La serie 1day se pagina hacia atrás con
     `end_date` hasta una respuesta vacía. Respeta 8 llamadas por minuto y
     un tope de 60 créditos. Filas, primera fecha, columnas OHLC, y el
     error literal si el plan no cubre XAU/USD.
  6. Alpha Vantage: UNA llamada a FX_DAILY (EUR/USD, full), una a
     DIGITAL_CURRENCY_DAILY (BTC) y una a GOLD_SILVER_HISTORY (daily).
     Campos (¿OHLC o solo cierre?) y primera fecha. Sin el secret
     ALPHAVANTAGE_API_KEY, "no aplica".

══ CREDENCIALES ══

La key de TwelveData va en el header `Authorization: apikey …`, como en
`TwelveDataAdapter` (ingestion/adapters.py), nunca en la URL. Alpha Vantage
solo la acepta como parámetro `apikey`, así que todo texto que llegue al
informe pasa por el limpiador.

[INTERPRETACIÓN] Lo que el brief no fija:
  · Cada llamada a TwelveData cuesta 1 crédito. El informe trae además
    los headers de créditos que devuelva la API, si los devuelve.
  · La página siguiente de 1day pide `end_date` = el día anterior a la
    primera fila de la página previa.
  · Entre llamadas a Alpha Vantage se esperan PAUSA_ALPHAVANTAGE_S
    segundos: tres llamadas, separadas de sobra para cualquier límite por
    minuto del plan gratuito, que acá no se verificó.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, timedelta
from typing import Any, Awaitable, Callable, Optional

import httpx
import pytest

from tests.test_deriv_endpoints_live import texto_libre

from ingestion.adapters import (
    _INTERVALOS_SIN_TIMEZONE,
    _TWELVEDATA_INTERVALS,
    TWELVEDATA_ENDPOINT,
)
from ingestion.limitador import Limitador as _LimitadorBase
from tests.test_deriv_sonda2_live import _limpiador

#: 5. Símbolos e intervalos de TwelveData.
SIMBOLOS_TWELVEDATA: tuple[str, ...] = ("BTC/USD", "XAU/USD")
INTERVALOS_TWELVEDATA: tuple[str, ...] = ("1day", "15min", "5min")
OUTPUTSIZE_TWELVEDATA = 5000
LLAMADAS_POR_MINUTO_TWELVEDATA = 8
TOPE_CREDITOS_TWELVEDATA = 60
BASE_TWELVEDATA = TWELVEDATA_ENDPOINT.rsplit("/", 1)[0]

#: La zona de toda llamada INTRADÍA a TwelveData (decision-log 2026-10-06).
TIMEZONE_TWELVEDATA = "UTC"

#: Los intervalos de TwelveData que NO llevan zona: los del adapter, en el
#: vocabulario del proveedor ("1d" → "1day"). Se leen del adapter, no se
#: copian: la regla es una sola.
INTERVALOS_SIN_ZONA: frozenset[str] = frozenset(
    _TWELVEDATA_INTERVALS[t] for t in _INTERVALOS_SIN_TIMEZONE)


def zona_twelvedata(intervalo: str) -> dict[str, str]:
    """{"timezone": "UTC"} para un intervalo intradía; {} para 1day."""
    return {} if intervalo in INTERVALOS_SIN_ZONA else {"timezone": TIMEZONE_TWELVEDATA}

#: 6. Alpha Vantage: las tres llamadas del brief.
ENDPOINT_ALPHAVANTAGE = "https://www.alphavantage.co/query"
LLAMADAS_ALPHAVANTAGE: dict[str, dict[str, str]] = {
    "FX_DAILY EUR/USD": {"function": "FX_DAILY", "from_symbol": "EUR",
                         "to_symbol": "USD", "outputsize": "full"},
    "DIGITAL_CURRENCY_DAILY BTC": {"function": "DIGITAL_CURRENCY_DAILY",
                                   "symbol": "BTC", "market": "USD"},
    "GOLD_SILVER_HISTORY GOLD daily": {"function": "GOLD_SILVER_HISTORY",
                                       "symbol": "GOLD", "interval": "daily"},
}
#: [INTERPRETACIÓN] Ver el docstring.
PAUSA_ALPHAVANTAGE_S = 15.0

_EXTRACTO = 400

#: GET async: (url, params, headers) -> (status, headers, cuerpo).
GetHttp = Callable[[str, dict, dict], Awaitable[tuple[int, dict, str]]]


async def _get_httpx(url: str, params: dict, headers: dict) -> tuple[int, dict, str]:
    async with httpx.AsyncClient(timeout=30.0) as cliente:
        r = await cliente.get(url, params=params, headers=headers)
        return r.status_code, dict(r.headers), r.text


# ═══ 5. TwelveData ════════════════════════════════════════════════════════

class Limitador(_LimitadorBase):
    """El de ingestion/limitador.py, adonde se portó (brief del Admin del
    06-oct-2026 (4)), con los valores de esta sonda por defecto."""

    def __init__(self, por_minuto: int = LLAMADAS_POR_MINUTO_TWELVEDATA,
                 tope_creditos: int = TOPE_CREDITOS_TWELVEDATA, **kw) -> None:
        super().__init__(por_minuto, tope_creditos, **kw)


def resumir_valores(valores: list[dict]) -> dict:
    if not valores:
        return {"filas": 0}
    fechas = sorted(v.get("datetime") for v in valores if v.get("datetime"))
    columnas = sorted(valores[0])
    return {"filas": len(valores), "primera_fecha": fechas[0] if fechas else None,
            "ultima_fecha": fechas[-1] if fechas else None, "columnas": columnas,
            "ohlc": all(c in columnas for c in ("open", "high", "low", "close"))}


async def pedir_twelvedata(ruta: str, params: dict, *, key: str, limitador: Limitador,
                           get: GetHttp) -> tuple[dict, Optional[dict]]:
    limpiar = _limpiador((key,))
    entrada: dict[str, Any] = {"ok": False, "params": params}
    if not await limitador.turno():
        entrada["error"] = f"no se pidió: tope de {limitador.tope} créditos alcanzado"
        return entrada, None
    try:
        status, cab, cuerpo = await get(f"{BASE_TWELVEDATA}/{ruta}", params,
                                        {"Authorization": f"apikey {key}"})
    except Exception as exc:   # noqa: BLE001
        entrada["error"] = limpiar(f"{type(exc).__name__}: {exc}")
        return entrada, None
    entrada.update(http=status, sha256=hashlib.sha256(cuerpo.encode("utf-8")).hexdigest())
    creditos = {k: v for k, v in cab.items() if "credit" in k.lower()}
    if creditos:
        entrada["creditos_api"] = creditos
    try:
        datos = json.loads(cuerpo)
    except json.JSONDecodeError:
        entrada["error"] = limpiar(cuerpo[:_EXTRACTO])
        return entrada, None
    if not isinstance(datos, dict) or datos.get("status") == "error" or status != 200:
        d = datos if isinstance(datos, dict) else {}
        entrada["error"] = {"code": d.get("code", status),
                            "message": limpiar(str(d.get("message", cuerpo[:_EXTRACTO])))}
        return entrada, None
    entrada["ok"] = True
    return entrada, datos


async def serie_twelvedata(simbolo: str, intervalo: str, *, key: str,
                           limitador: Limitador, get: GetHttp) -> dict:
    """Una serie. 1day pagina hacia atrás con end_date; el resto, una página."""
    paginas: list[dict] = []
    fechas: set[str] = set()
    columnas: Optional[list[str]] = None
    end_date: Optional[str] = None
    corte = ""
    while True:
        params = {"symbol": simbolo, "interval": intervalo,
                  "outputsize": OUTPUTSIZE_TWELVEDATA, **zona_twelvedata(intervalo)}
        if end_date:
            params["end_date"] = end_date
        entrada, datos = await pedir_twelvedata("time_series", params, key=key,
                                                limitador=limitador, get=get)
        if datos is None:
            paginas.append(entrada)
            corte = "error"
            break
        valores = datos.get("values") or []
        entrada.update(resumir_valores(valores))
        paginas.append(entrada)
        if not valores:
            corte = "respuesta vacía"
            break
        columnas = columnas or entrada["columnas"]
        nuevas = {v["datetime"] for v in valores} - fechas
        fechas |= {v["datetime"] for v in valores}
        if intervalo != "1day":
            corte = "una página (solo 1day se pagina)"
            break
        if not nuevas:
            corte = "la página no retrocedió"
            break
        end_date = (date.fromisoformat(entrada["primera_fecha"][:10])
                    - timedelta(days=1)).isoformat()
    orden = sorted(fechas)
    return {"simbolo": simbolo, "intervalo": intervalo, "corte": corte,
            "paginas": paginas, "filas": len(fechas),
            "primera_fecha": orden[0] if orden else None,
            "ultima_fecha": orden[-1] if orden else None, "columnas": columnas,
            "ohlc": bool(columnas) and all(c in columnas for c in ("open", "high", "low", "close"))}


async def sondear_twelvedata(*, key: str, get: GetHttp = _get_httpx,
                             limitador: Optional[Limitador] = None) -> dict:
    lim = limitador or Limitador()
    out: dict[str, Any] = {"base": BASE_TWELVEDATA, "earliest_timestamp": {}, "series": []}
    for s in SIMBOLOS_TWELVEDATA:
        entrada, datos = await pedir_twelvedata(
            "earliest_timestamp", {"symbol": s, "interval": "1day",
                                   **zona_twelvedata("1day")}, key=key,
            limitador=lim, get=get)
        if datos is not None:
            entrada.update(datetime=datos.get("datetime"), unix_time=datos.get("unix_time"))
        out["earliest_timestamp"][s] = entrada
        for i in INTERVALOS_TWELVEDATA:
            out["series"].append(await serie_twelvedata(s, i, key=key, limitador=lim, get=get))
    out["creditos_usados"] = lim.creditos
    return out


# ═══ 6. Alpha Vantage ═════════════════════════════════════════════════════

def _filas_alphavantage(datos: dict) -> tuple[Optional[str], list[tuple[str, dict]]]:
    """(clave de la serie, [(fecha, fila)]). Acepta las dos formas de AV: un
    dict fecha → campos, o una lista de filas con `date`."""
    for clave, valor in datos.items():
        if isinstance(valor, dict) and valor and all(isinstance(v, dict) for v in valor.values()):
            return clave, sorted(valor.items())
        if isinstance(valor, list) and valor and isinstance(valor[0], dict) and "date" in valor[0]:
            return clave, sorted((str(f.get("date")), f) for f in valor)
    return None, []


def resumir_alphavantage(datos: Any) -> dict:
    if not isinstance(datos, dict):
        return {"ok": False, "error": "la respuesta no es un objeto JSON"}
    avisos = {k: datos[k] for k in ("Information", "Note", "Error Message") if k in datos}
    clave, filas = _filas_alphavantage(datos)
    if not filas:
        return {"ok": False, "avisos": avisos or None, "claves": sorted(datos)}
    campos = sorted(filas[0][1])
    nombres = " ".join(campos).lower()
    return {"ok": True, "avisos": avisos or None, "serie": clave, "filas": len(filas),
            "primera_fecha": filas[0][0], "ultima_fecha": filas[-1][0], "campos": campos,
            "ohlc": all(c in nombres for c in ("open", "high", "low", "close")),
            "solo_cierre_o_precio": not any(c in nombres for c in ("open", "high", "low"))}


async def sondear_alphavantage(*, key: Optional[str], get: GetHttp = _get_httpx,
                               dormir: Callable[[float], Awaitable[None]] = asyncio.sleep) -> dict:
    if not key:
        return {"no_aplica": "no existe el secret ALPHAVANTAGE_API_KEY en el job"}
    limpiar = _limpiador((key,))
    out: dict[str, Any] = {}
    for i, (nombre, params) in enumerate(LLAMADAS_ALPHAVANTAGE.items()):
        if i:
            await dormir(PAUSA_ALPHAVANTAGE_S)
        entrada: dict[str, Any] = {"params": params}
        try:
            status, _, cuerpo = await get(ENDPOINT_ALPHAVANTAGE,
                                          {**params, "datatype": "json", "apikey": key}, {})
        except Exception as exc:   # noqa: BLE001
            out[nombre] = {**entrada, "ok": False,
                           "error": limpiar(f"{type(exc).__name__}: {exc}")}
            continue
        entrada.update(http=status, sha256=hashlib.sha256(cuerpo.encode("utf-8")).hexdigest())
        try:
            entrada.update(resumir_alphavantage(json.loads(cuerpo)))
        except json.JSONDecodeError:
            entrada.update(ok=False, error=limpiar(cuerpo[:_EXTRACTO]))
        out[nombre] = entrada
    return out


def _publicar(capsys, informe: dict) -> None:
    """Al log del job siempre, como las sondas de Deriv, con su propio
    encabezado."""
    with capsys.disabled():
        print("\n=== SONDA §0.A-3: FUENTE EXTERNA ===")
        print(json.dumps(informe, indent=2, ensure_ascii=False))


# ═══ Offline ══════════════════════════════════════════════════════════════

class _Reloj:
    def __init__(self):
        self.t = 1000.0
        self.dormido: list[float] = []

    def __call__(self):
        return self.t

    async def dormir(self, s):
        self.dormido.append(s)
        self.t += s


def _lim(**kw):
    r = _Reloj()
    return Limitador(reloj=r, dormir=r.dormir, **kw), r


async def test_el_limitador_espera_al_noveno_pedido_del_minuto():
    lim, r = _lim()
    for _ in range(8):
        assert await lim.turno()
        r.t += 1
    assert r.dormido == []
    assert await lim.turno()
    assert r.dormido == [pytest.approx(52.0)], "60 s desde el primero, que fue hace 8"
    assert lim.creditos == 9


async def test_el_limitador_corta_en_el_tope_de_creditos():
    lim, r = _lim(tope_creditos=3)
    assert [await lim.turno() for _ in range(5)] == [True, True, True, False, False]
    assert lim.creditos == 3


def _valores(desde: date, n: int) -> list[dict]:
    return [{"datetime": (desde + timedelta(days=k)).isoformat(), "open": "1", "high": "1",
             "low": "1", "close": "1"} for k in range(n)]


def _twelvedata(*, piso=date(2014, 1, 1), hoy=date(2026, 10, 1), xau_error=False,
                vacio_como_error=True):
    vistos: list[tuple[str, dict, dict]] = []

    async def get(url, params, headers):
        vistos.append((url, dict(params), headers))
        if xau_error and params["symbol"] == "XAU/USD":
            return 200, {}, json.dumps({"code": 404, "status": "error", "message":
                                        "**symbol** XAU/USD is available exclusively with pro plan"})
        if url.endswith("earliest_timestamp"):
            return 200, {"api-credits-used": "1"}, json.dumps(
                {"datetime": piso.isoformat(), "unix_time": 1388534400})
        fin = date.fromisoformat(params["end_date"]) if "end_date" in params else hoy
        desde = max(piso, fin - timedelta(days=OUTPUTSIZE_TWELVEDATA - 1))
        if fin < piso:
            if vacio_como_error:
                return 200, {}, json.dumps({"code": 400, "status": "error", "message":
                                            "No data is available on the specified dates."})
            return 200, {}, json.dumps({"meta": {}, "values": [], "status": "ok"})
        vals = _valores(desde, (fin - desde).days + 1)
        return 200, {}, json.dumps({"meta": {"symbol": params["symbol"]},
                                    "values": list(reversed(vals)), "status": "ok"})
    return get, vistos


async def test_twelvedata_pagina_1day_hacia_atras_hasta_el_fondo():
    piso, hoy = date(2010, 1, 1), date(2026, 10, 1)
    get, vistos = _twelvedata(piso=piso, hoy=hoy)
    lim, _ = _lim()
    s = await serie_twelvedata("BTC/USD", "1day", key="K", limitador=lim, get=get)
    primera_pag1 = hoy - timedelta(days=OUTPUTSIZE_TWELVEDATA - 1)
    assert [v[1].get("end_date") for v in vistos] == [
        None, (primera_pag1 - timedelta(days=1)).isoformat(),
        (piso - timedelta(days=1)).isoformat()]
    assert [p.get("filas") for p in s["paginas"][:2]] == [
        OUTPUTSIZE_TWELVEDATA, (primera_pag1 - piso).days]
    assert s["corte"] == "error"
    assert s["paginas"][-1]["error"]["message"] == "No data is available on the specified dates."
    assert s["filas"] == (hoy - piso).days + 1
    assert s["primera_fecha"] == "2010-01-01" and s["ohlc"] is True
    assert all(v[1]["outputsize"] == 5000 for v in vistos)


async def test_twelvedata_corta_en_la_respuesta_vacia():
    get, _ = _twelvedata(vacio_como_error=False)
    lim, _ = _lim()
    s = await serie_twelvedata("BTC/USD", "1day", key="K", limitador=lim, get=get)
    assert s["corte"] == "respuesta vacía"


async def test_twelvedata_corta_si_la_pagina_no_retrocede():
    async def get(url, params, headers):
        return 200, {}, json.dumps({"values": _valores(date(2026, 1, 1), 3)})
    lim, _ = _lim()
    s = await serie_twelvedata("BTC/USD", "1day", key="K", limitador=lim, get=get)
    assert s["corte"] == "la página no retrocedió" and len(s["paginas"]) == 2


async def test_twelvedata_intradia_es_una_pagina():
    get, vistos = _twelvedata()
    lim, _ = _lim()
    s = await serie_twelvedata("BTC/USD", "5min", key="K", limitador=lim, get=get)
    assert len(vistos) == 1 and s["corte"].startswith("una página")


async def test_twelvedata_la_key_va_en_el_header_y_no_en_el_informe():
    get, vistos = _twelvedata()
    lim, _ = _lim()
    inf = await sondear_twelvedata(key="Kq7secreta", get=get, limitador=lim)
    assert all(h == {"Authorization": "apikey Kq7secreta"} for _, _, h in vistos)
    assert all("Kq7secreta" not in json.dumps(p) for _, p, _ in vistos)
    assert "Kq7secreta" not in texto_libre(inf)


async def test_twelvedata_cubre_los_dos_simbolos_tres_intervalos_y_earliest():
    get, vistos = _twelvedata()
    lim, _ = _lim()
    inf = await sondear_twelvedata(key="K", get=get, limitador=lim)
    assert [(s["simbolo"], s["intervalo"]) for s in inf["series"]] == [
        (s, i) for s in ("BTC/USD", "XAU/USD") for i in ("1day", "15min", "5min")]
    assert set(inf["earliest_timestamp"]) == {"BTC/USD", "XAU/USD"}
    assert inf["earliest_timestamp"]["BTC/USD"]["datetime"] == "2014-01-01"
    assert inf["earliest_timestamp"]["BTC/USD"]["creditos_api"] == {"api-credits-used": "1"}
    assert inf["creditos_usados"] == len(vistos) <= TOPE_CREDITOS_TWELVEDATA


async def test_twelvedata_el_error_del_plan_queda_literal():
    get, _ = _twelvedata(xau_error=True)
    lim, _ = _lim()
    inf = await sondear_twelvedata(key="K", get=get, limitador=lim)
    xau = [s for s in inf["series"] if s["simbolo"] == "XAU/USD"]
    assert all(s["corte"] == "error" for s in xau)
    assert xau[0]["paginas"][0]["error"] == {
        "code": 404, "message": "**symbol** XAU/USD is available exclusively with pro plan"}
    assert inf["earliest_timestamp"]["XAU/USD"]["ok"] is False


async def test_twelvedata_sin_creditos_no_pide():
    get, vistos = _twelvedata()
    lim, _ = _lim(tope_creditos=2)
    inf = await sondear_twelvedata(key="K", get=get, limitador=lim)
    assert len(vistos) == 2
    assert "tope de 2 créditos" in inf["series"][1]["paginas"][0]["error"]


async def test_twelvedata_una_excepcion_de_red_no_lanza_ni_filtra_la_key():
    async def get(url, params, headers):
        raise httpx.ConnectError("fallo con Kq7secreta")
    lim, _ = _lim()
    e, d = await pedir_twelvedata("time_series", {}, key="Kq7secreta", limitador=lim, get=get)
    assert d is None and "Kq7secreta" not in e["error"] and "ConnectError" in e["error"]


# ── Alpha Vantage ────────────────────────────────────────────────────────

_FX = {"Meta Data": {"1. Information": "Forex Daily Prices"},
       "Time Series FX (Daily)": {"2026-10-01": {"1. open": "1.1", "2. high": "1.2",
                                                 "3. low": "1.0", "4. close": "1.15"},
                                  "2003-12-01": {"1. open": "1.1", "2. high": "1.2",
                                                 "3. low": "1.0", "4. close": "1.15"}}}
_ORO = {"nominal": "USD", "data": [{"date": "2026-10-01", "price": "2400"},
                                   {"date": "1968-01-02", "price": "35"}]}


def _av(respuestas):
    vistos = []

    async def get(url, params, headers):
        vistos.append(dict(params))
        cuerpo = respuestas[params["function"]]
        return 200, {}, cuerpo if isinstance(cuerpo, str) else json.dumps(cuerpo)
    return get, vistos


async def _sin_dormir(s):
    _sin_dormir.pausas.append(s)
_sin_dormir.pausas = []


async def test_alphavantage_tres_llamadas_y_sus_campos():
    _sin_dormir.pausas.clear()
    get, vistos = _av({"FX_DAILY": _FX, "DIGITAL_CURRENCY_DAILY": {"Information": "premium"},
                       "GOLD_SILVER_HISTORY": _ORO})
    inf = await sondear_alphavantage(key="AVk9", get=get, dormir=_sin_dormir)
    assert [v["function"] for v in vistos] == ["FX_DAILY", "DIGITAL_CURRENCY_DAILY",
                                              "GOLD_SILVER_HISTORY"]
    assert vistos[0]["outputsize"] == "full" and vistos[2]["interval"] == "daily"
    assert all(v["apikey"] == "AVk9" and v["datatype"] == "json" for v in vistos)
    assert _sin_dormir.pausas == [PAUSA_ALPHAVANTAGE_S] * 2
    fx = inf["FX_DAILY EUR/USD"]
    assert fx["ohlc"] is True and fx["primera_fecha"] == "2003-12-01" and fx["filas"] == 2
    oro = inf["GOLD_SILVER_HISTORY GOLD daily"]
    assert oro["ohlc"] is False and oro["solo_cierre_o_precio"] is True
    assert oro["campos"] == ["date", "price"] and oro["primera_fecha"] == "1968-01-02"
    btc = inf["DIGITAL_CURRENCY_DAILY BTC"]
    assert btc["ok"] is False and btc["avisos"] == {"Information": "premium"}
    assert "AVk9" not in texto_libre(inf)


async def test_alphavantage_sin_secret_no_aplica():
    get, vistos = _av({})
    inf = await sondear_alphavantage(key=None, get=get)
    assert "no_aplica" in inf and vistos == []


async def test_alphavantage_un_error_de_red_no_filtra_la_key():
    async def get(url, params, headers):
        raise httpx.ConnectError(f"GET {url}?apikey={params['apikey']}")
    inf = await sondear_alphavantage(key="AVk9", get=get, dormir=_sin_dormir)
    assert all(not v["ok"] for v in inf.values())
    assert "AVk9" not in json.dumps(inf)


def test_alphavantage_cuerpo_que_no_es_objeto():
    assert resumir_alphavantage([1, 2])["ok"] is False


def test_el_intradia_lleva_timezone_utc_y_1day_no():
    """La regla del 06-oct: intradía con zona; 1day sin ella."""
    async def correr():
        get, vistos = _twelvedata()
        lim, _ = _lim()
        await sondear_twelvedata(key="K", get=get, limitador=lim)
        return vistos
    vistos = asyncio.run(correr())
    diarios = [p for _, p, _ in vistos if p["interval"] == "1day"]
    intradia = [p for _, p, _ in vistos if p["interval"] != "1day"]
    assert diarios and intradia
    assert all("timezone" not in p for p in diarios)
    assert all(p["timezone"] == "UTC" for p in intradia)


@pytest.mark.parametrize("intervalo, zona", [
    ("1day", {}), ("5min", {"timezone": "UTC"}), ("15min", {"timezone": "UTC"}),
    ("1h", {"timezone": "UTC"}),
])
def test_zona_twelvedata(intervalo, zona):
    assert zona_twelvedata(intervalo) == zona
    assert INTERVALOS_SIN_ZONA == {"1day"}


def test_el_workflow_ya_no_pasa_ctrader_ni_alphavantage():
    """cTrader se retiró (decisión del Admin del 05-oct) y la sonda de Alpha
    Vantage ya no corre: sus secrets no tienen por qué entrar al job."""
    import yaml
    from pathlib import Path
    wf = yaml.safe_load((Path(__file__).resolve().parent.parent / ".github" / "workflows"
                         / "live-tests.yml").read_text(encoding="utf-8"))
    env = wf["jobs"]["live"]["env"]
    assert not any("CTRADER" in k or "CTRADER" in str(v) for k, v in env.items())
    assert "ALPHAVANTAGE_API_KEY" not in env
