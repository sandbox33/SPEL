"""
tests/test_sonda3b_live.py
============================
Sonda §0.A-3b (brief del Admin del 05-oct-2026), puntos 5a, 5b y 5c. SOLO
MIDE. Los puntos 5d y 5e (los tres canales de Deriv, con la cotización en la
cuenta real) están en `tests/test_deriv_cotizacion_real_live.py`, que es el
único archivo que puede nombrar el canal real; 5c corre ahí porque necesita
el κ que mide 5d.

══ LO QUE CONTESTA ══

  5a. Profundidad intradía de TwelveData, con timezone=UTC: BTC/USD y
      XAU/USD en 5min y 15min. Primero /earliest_timestamp; después una
      página de 5000 con end_date en 2018-06-30, 2021-06-30 y 2024-06-30.
      Si hay datos, la primera fecha y si las marcas están en UTC.
  5b. Diferencia de base contra Deriv en velas M15 del último año, las dos
      en UTC: para cada vela común, la diferencia de close, high y low en %
      del precio (percentiles 50, 90 y 99); la correlación de los retornos
      M15; y el desfase que maximiza la correlación (debería ser 0).
  5c. Rango de apertura, solo mecánica: SIN retornos ni resultados de
      operaciones, sobre las velas M5 de Deriv del último año. Oro: ancla a
      las 08:00 de Londres; BTC: a las 09:30 de Nueva York (zoneinfo).
      Rango = máximo − mínimo de las 3 primeras velas M5. Percentiles
      10/25/50/75/90 del rango en % del precio, la fracción de días con
      rango ≤ 0,6 × la distancia de stop-out a ×50 y a ×100, y el costo en
      fracción de R = κ/(d+κ).

══ FUENTES ══

  · La distancia de stop-out es 1/m − κ, con κ = comisión / nocional:
    verificada en la sonda §0.A-3 v2 (decision-log 2026-10-05, d).
  · TwelveData: `time_series` acepta `start_date`, `end_date`, `outputsize`
    y `timezone`, y su `meta` trae `exchange_timezone`. La página que pasa
    el fondo devuelve 404 "Data not found" (decision-log 2026-10-05, f).

[INTERPRETACIÓN] Lo que el brief no fija:
  · "Las marcas están en UTC" se mide con lo que se puede medir sin otra
    fuente: la `meta.exchange_timezone` que devuelve la API, que ninguna
    vela pase el `end_date` pedido (con UTC, una vela "del futuro" es la
    firma del corrimiento visto el 05-oct) y que los minutos estén en la
    grilla del intervalo. 5b es la prueba fuerte, contra Deriv.
  · El "último año" son los 365 días anteriores a la corrida, que es lo que
    Deriv entrega.
  · 5b: diferencia = (TwelveData − Deriv) / Deriv × 100, en valor absoluto
    para los percentiles; se informa además la mediana con signo. Los
    retornos son logarítmicos de cierre a cierre entre velas consecutivas
    (900 s) de cada serie. El desfase se busca en ±96 velas (±24 h), que
    cubre el corrimiento de unas 11 h visto en el intradía del oro.
    Percentiles con el método lineal de numpy.
  · 5c: el precio de referencia del rango es el `open` de la primera vela.
    Un día sin las tres velas del ancla no entra, y se cuenta. En BTC entran
    todos los días de la semana: el mercado no cierra. d en κ/(d+κ) es el
    rango del día en fracción del precio (el stop del otro lado del rango),
    y se informan sus percentiles.
  · TwelveData: 7 llamadas por minuto, debajo de las 8 del brief, para
    dejarle una al test live del adapter, que corre después en el mismo job;
    tope de 60 créditos.

══ CUÁNDO SE PONE ROJO ══

Solo por plomería (falta una credencial que el workflow declara) o por una
fuga (una key en el informe). Lo que conteste la API es el resultado.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from governance.secrets import SecretKey, load_secret
from ingestion.adapters import DerivAdapter
from ingestion.sonda_instrumentos import codigo, seleccionar
from tests.test_deriv_endpoints_live import (
    _SOLO_EN_LIVE_TESTS,
    ENDPOINT_PUBLICO_NUEVO,
    _http_de,
    texto_libre,
)
from tests.test_deriv_sonda2_live import profundidad
from tests.test_deriv_sonda3_live import _CanalPausado
from tests.test_fuentes_sonda3_live import (
    OUTPUTSIZE_TWELVEDATA,
    TIMEZONE_TWELVEDATA,
    TOPE_CREDITOS_TWELVEDATA,
    GetHttp,
    Limitador,
    _get_httpx,
    pedir_twelvedata,
)

#: 5a.
SIMBOLOS_TD: tuple[str, ...] = ("BTC/USD", "XAU/USD")
INTERVALOS_5A: tuple[str, ...] = ("5min", "15min")
END_DATES_5A: tuple[str, ...] = ("2018-06-30", "2021-06-30", "2024-06-30")

#: [INTERPRETACIÓN] 7 por minuto: ver el docstring.
LLAMADAS_POR_MINUTO_3B = 7

#: 5b.
INTERVALO_5B = "15min"
GRANULARIDAD_5B = 900
DIAS_ULTIMO_ANIO = 365
DESFASE_MAXIMO_VELAS = 96
PERCENTILES_5B: tuple[int, ...] = (50, 90, 99)

#: 5c.
GRANULARIDAD_5C = 300
VELAS_DEL_RANGO = 3
ANCLAS_5C: dict[str, tuple[str, int, int]] = {
    "oro": ("Europe/London", 8, 0),
    "btc": ("America/New_York", 9, 30),
}
MULTIPLICADORES_5C: tuple[int, ...] = (50, 100)
FRACCION_DEL_STOP_OUT = 0.6
PERCENTILES_5C: tuple[int, ...] = (10, 25, 50, 75, 90)

_MINUTOS = {"5min": 5, "15min": 15}


def _publicar(capsys, informe: dict) -> None:
    """El informe en UNA línea: el log de la sonda 3 se leyó cortado porque
    los informes indentados ocupaban miles de líneas."""
    with capsys.disabled():
        print("\n=== SONDA §0.A-3b ===")
        print(json.dumps(informe, ensure_ascii=False, separators=(",", ":")))


def _epoch_utc(dt_texto: str) -> int:
    """'2026-10-05 13:30:00' (o solo la fecha), leído en UTC."""
    formato = "%Y-%m-%d %H:%M:%S" if " " in dt_texto else "%Y-%m-%d"
    return int(datetime.strptime(dt_texto, formato).replace(tzinfo=timezone.utc).timestamp())


# ═══ 5a ═══════════════════════════════════════════════════════════════════

def chequeo_utc(valores: list[dict], intervalo: str, *, end_date: Optional[str],
                meta: dict) -> dict:
    """Lo medible sin otra fuente: la zona que declara la API, que ninguna
    vela pase el end_date, y la grilla de minutos."""
    fechas = [v["datetime"] for v in valores if v.get("datetime")]
    if not fechas:
        return {"meta_exchange_timezone": meta.get("exchange_timezone")}
    tope = None
    if end_date:
        tope = _epoch_utc(end_date) + (86400 - 1 if " " not in end_date else 0)
    paso = _MINUTOS.get(intervalo)
    return {
        "meta_exchange_timezone": meta.get("exchange_timezone"),
        "ninguna_pasa_el_end_date": None if tope is None else max(map(_epoch_utc, fechas)) <= tope,
        "en_la_grilla": None if not paso else all(
            int(f[14:16]) % paso == 0 for f in fechas if len(f) >= 16),
    }


async def pagina_5a(simbolo: str, intervalo: str, end_date: str, *, key: str,
                    limitador: Limitador, get: GetHttp) -> dict:
    params = {"symbol": simbolo, "interval": intervalo, "outputsize": OUTPUTSIZE_TWELVEDATA,
              "end_date": end_date, "timezone": TIMEZONE_TWELVEDATA}
    entrada, datos = await pedir_twelvedata("time_series", params, key=key,
                                            limitador=limitador, get=get)
    out: dict[str, Any] = {"simbolo": simbolo, "intervalo": intervalo, "end_date": end_date,
                           "ok": entrada["ok"], "sha256": entrada.get("sha256"),
                           "error": entrada.get("error")}
    if datos is None:
        out["hay_datos"] = False
        return out
    valores = datos.get("values") or []
    fechas = sorted(v["datetime"] for v in valores if v.get("datetime"))
    out.update(hay_datos=bool(valores), filas=len(valores),
               primera_fecha=fechas[0] if fechas else None,
               ultima_fecha=fechas[-1] if fechas else None,
               utc=chequeo_utc(valores, intervalo, end_date=end_date,
                               meta=datos.get("meta") or {}))
    return out


async def sondear_5a(*, key: str, limitador: Limitador, get: GetHttp = _get_httpx) -> dict:
    out: dict[str, Any] = {"earliest_timestamp": [], "paginas": []}
    for s in SIMBOLOS_TD:
        for i in INTERVALOS_5A:
            entrada, datos = await pedir_twelvedata(
                "earliest_timestamp", {"symbol": s, "interval": i,
                                       "timezone": TIMEZONE_TWELVEDATA},
                key=key, limitador=limitador, get=get)
            out["earliest_timestamp"].append({
                "simbolo": s, "intervalo": i, "ok": entrada["ok"],
                "sha256": entrada.get("sha256"), "error": entrada.get("error"),
                "datetime": (datos or {}).get("datetime"),
                "unix_time": (datos or {}).get("unix_time")})
            for fin in END_DATES_5A:
                out["paginas"].append(await pagina_5a(s, i, fin, key=key,
                                                      limitador=limitador, get=get))
    return out


# ═══ 5b ═══════════════════════════════════════════════════════════════════

async def serie_td_ultimo_anio(simbolo: str, *, key: str, limitador: Limitador,
                               get: GetHttp, ahora: datetime) -> tuple[dict[int, dict], dict]:
    """Velas M15 de TwelveData de los últimos 365 días, en UTC, paginando
    hacia atrás con end_date. Devuelve (época → vela, resumen)."""
    inicio = ahora - timedelta(days=DIAS_ULTIMO_ANIO)
    fin = ahora.strftime("%Y-%m-%d %H:%M:%S")
    velas: dict[int, dict] = {}
    paginas: list[dict] = []
    corte = ""
    while True:
        params = {"symbol": simbolo, "interval": INTERVALO_5B,
                  "outputsize": OUTPUTSIZE_TWELVEDATA, "timezone": TIMEZONE_TWELVEDATA,
                  "start_date": inicio.strftime("%Y-%m-%d %H:%M:%S"), "end_date": fin}
        entrada, datos = await pedir_twelvedata("time_series", params, key=key,
                                                limitador=limitador, get=get)
        paginas.append({"end_date": fin, "sha256": entrada.get("sha256"),
                        "error": entrada.get("error")})
        if datos is None:
            corte = "error"
            break
        valores = datos.get("values") or []
        paginas[-1]["filas"] = len(valores)
        nuevas = 0
        for v in valores:
            e = _epoch_utc(v["datetime"])
            if e not in velas:
                nuevas += 1
            velas[e] = {k: float(v[k]) for k in ("open", "high", "low", "close")}
        if not valores or not nuevas:
            corte = "sin velas nuevas"
            break
        primera = min(_epoch_utc(v["datetime"]) for v in valores)
        if primera <= inicio.timestamp():
            corte = "se llegó al inicio del año"
            break
        fin = datetime.fromtimestamp(primera - 60, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return velas, {"paginas": paginas, "corte": corte, "velas": len(velas)}


def _pct(xs, ps) -> dict[str, Optional[float]]:
    if len(xs) == 0:
        return {str(p): None for p in ps}
    return {str(p): float(np.percentile(xs, p)) for p in ps}


def _retornos(velas: dict[int, dict], paso: int) -> dict[int, float]:
    return {e: math.log(v["close"] / velas[e - paso]["close"])
            for e, v in velas.items() if e - paso in velas
            and velas[e - paso]["close"] > 0 and v["close"] > 0}


def _corr(a: dict[int, float], b: dict[int, float], desfase_s: int) -> tuple[Optional[float], int]:
    comunes = [e for e in a if e + desfase_s in b]
    if len(comunes) < 3:
        return None, len(comunes)
    x = np.array([a[e] for e in comunes])
    y = np.array([b[e + desfase_s] for e in comunes])
    if x.std() == 0 or y.std() == 0:
        return None, len(comunes)
    return float(np.corrcoef(x, y)[0, 1]), len(comunes)


def comparar_bases(deriv: dict[int, dict], td: dict[int, dict], *,
                   paso: int = GRANULARIDAD_5B,
                   desfase_maximo: int = DESFASE_MAXIMO_VELAS) -> dict:
    """5b. Velas comunes por época (las dos en UTC)."""
    comunes = sorted(set(deriv) & set(td))
    out: dict[str, Any] = {"velas_deriv": len(deriv), "velas_twelvedata": len(td),
                           "velas_comunes": len(comunes)}
    for campo in ("close", "high", "low"):
        dif = np.array([(td[e][campo] - deriv[e][campo]) / deriv[e][campo] * 100
                        for e in comunes if deriv[e][campo]])
        out[f"diferencia_{campo}_pct"] = {
            "percentiles_abs": _pct(np.abs(dif), PERCENTILES_5B),
            "mediana_con_signo": float(np.median(dif)) if len(dif) else None}
    rd, rt = _retornos(deriv, paso), _retornos(td, paso)
    perfil = {k: _corr(rd, rt, k * paso)[0] for k in range(-desfase_maximo, desfase_maximo + 1)}
    validos = {k: c for k, c in perfil.items() if c is not None}
    mejor = max(validos, key=validos.get) if validos else None
    out.update(correlacion_retornos=perfil.get(0), retornos_comunes=_corr(rd, rt, 0)[1],
               desfase_de_maxima_correlacion_velas=mejor,
               correlacion_maxima=validos.get(mejor) if mejor is not None else None)
    return out


# ═══ 5c ═══════════════════════════════════════════════════════════════════

def rangos_de_apertura(velas_m5: dict[int, dict], zona: str, hora: int, minuto: int,
                       *, n: int = VELAS_DEL_RANGO) -> tuple[list[dict], int]:
    """Por cada día local entre la primera y la última vela: el rango de las
    n velas M5 que empiezan en el ancla. Lee SOLO esas n velas: ni un
    precio de después. Devuelve (días con rango, días sin las n velas). Un
    ancla fuera de la serie (antes de la primera vela, o con la tercera vela
    después de la última) no cuenta como faltante."""
    if not velas_m5:
        return [], 0
    tz = ZoneInfo(zona)
    desde = datetime.fromtimestamp(min(velas_m5), tz).date()
    hasta = datetime.fromtimestamp(max(velas_m5), tz).date()
    primera, ultima = min(velas_m5), max(velas_m5)
    dias, faltan = [], 0
    d = desde
    while d <= hasta:
        ancla = int(datetime(d.year, d.month, d.day, hora, minuto, tzinfo=tz).timestamp())
        epocas = [ancla + k * GRANULARIDAD_5C for k in range(n)]
        if ancla < primera or epocas[-1] > ultima:
            pass   # fuera de la serie: ni día ni faltante
        elif all(e in velas_m5 for e in epocas):
            vs = [velas_m5[e] for e in epocas]
            alto = max(float(v["high"]) for v in vs)
            bajo = min(float(v["low"]) for v in vs)
            precio = float(vs[0]["open"])
            dias.append({"fecha": d.isoformat(), "ancla": ancla,
                         "rango_fraccion": (alto - bajo) / precio})
        else:
            faltan += 1
        d += timedelta(days=1)
    return dias, faltan


def distancia_stop_out(m: int, kappa: float) -> float:
    """1/m − κ (decision-log 2026-10-05, d)."""
    return 1 / m - kappa


def mecanica_rango(dias: list[dict], kappa: Optional[float]) -> dict:
    """5c. Solo estadísticas del rango: nada de lo que pasó después."""
    rangos = np.array([d["rango_fraccion"] for d in dias])
    out: dict[str, Any] = {"dias": len(dias),
                           "rango_pct": _pct(rangos * 100, PERCENTILES_5C)}
    if kappa is None:
        out["kappa"] = None
        out["sin_kappa"] = "no hubo κ medido en 5d para este activo"
        return out
    out["kappa"] = kappa
    out["fraccion_de_dias_con_rango_bajo_el_umbral"] = {
        f"x{m}": (float(np.mean(rangos <= FRACCION_DEL_STOP_OUT * distancia_stop_out(m, kappa)))
                  if len(rangos) else None)
        for m in MULTIPLICADORES_5C}
    out["umbral_pct"] = {f"x{m}": FRACCION_DEL_STOP_OUT * distancia_stop_out(m, kappa) * 100
                         for m in MULTIPLICADORES_5C}
    out["costo_en_fraccion_de_R"] = _pct(kappa / (rangos + kappa), PERCENTILES_5C)
    return out


# ═══ La sonda real de 5a y 5b ═════════════════════════════════════════════

async def sondear_5a_5b(*, key: str, abrir, get: GetHttp = _get_httpx,
                        limitador: Optional[Limitador] = None,
                        ahora: Optional[datetime] = None, pausa_s: float = 0.25) -> dict:
    lim = limitador or Limitador(LLAMADAS_POR_MINUTO_3B, TOPE_CREDITOS_TWELVEDATA)
    momento = ahora or datetime.now(timezone.utc)
    informe: dict[str, Any] = {"sonda": "§0.A-3b, puntos 5a y 5b",
                               "corrida_utc": momento.isoformat()}
    informe["5a"] = await sondear_5a(key=key, limitador=lim, get=get)
    td: dict[str, tuple[dict, dict]] = {}
    for s in SIMBOLOS_TD:
        td[s] = await serie_td_ultimo_anio(s, key=key, limitador=lim, get=get, ahora=momento)
    informe["creditos_usados"] = lim.creditos

    deriv: dict[str, Any] = {"handshake": None}
    informe["5b"] = {"deriv": deriv}
    try:
        async with abrir(ENDPOINT_PUBLICO_NUEVO) as ws:
            deriv["handshake"] = {"ok": True}
            canal = _CanalPausado(ws, pausa_s)
            entrada, datos = await canal.pedir({"active_symbols": "brief"})
            if datos is None:
                deriv["active_symbols"] = entrada
                return informe
            sel = seleccionar(datos.get("active_symbols") or [])
            pares = {"BTC/USD": [codigo(i)[0] for i in sel.btc],
                     "XAU/USD": [codigo(i)[0] for i in sel.oro]}
            for s_td, sims in pares.items():
                if len(sims) != 1:
                    informe["5b"][s_td] = {"no_aplica": f"se esperaba un símbolo de Deriv, hay {sims}"}
                    continue
                velas: dict[int, dict] = {}
                prof = await profundidad(canal, sims[0], GRANULARIDAD_5B, guardar_velas=velas)
                desde = momento.timestamp() - DIAS_ULTIMO_ANIO * 86400
                dv = {e: {k: float(v[k]) for k in ("open", "high", "low", "close")}
                      for e, v in velas.items() if e >= desde}
                tv = {e: v for e, v in td[s_td][0].items() if e >= desde}
                informe["5b"][s_td] = {"deriv_simbolo": sims[0], "deriv_corte": prof["corte"],
                                       "twelvedata": td[s_td][1],
                                       **comparar_bases(dv, tv)}
    except Exception as exc:   # noqa: BLE001
        clave = "handshake" if deriv["handshake"] is None else "error_de_conexion"
        deriv[clave] = {"ok": False, "http": _http_de(exc), "error": f"{type(exc).__name__}: {exc}"}
    return informe


@pytest.mark.live
@_SOLO_EN_LIVE_TESTS
async def test_live_sonda_3b_twelvedata_y_base(capsys):
    key = load_secret(SecretKey.TWELVEDATA_API_KEY, required=False)
    assert key, "SPEL_EXPECT_SECRETS=1 pero TWELVEDATA_API_KEY no llegó al job"
    informe = await sondear_5a_5b(key=key, abrir=DerivAdapter._default_connector)
    assert key not in texto_libre(informe), "la key llegó al informe: no se publica"
    _publicar(capsys, informe)


# ═══ Offline ══════════════════════════════════════════════════════════════

from tests.deriv_falso import DerivFalso  # noqa: E402

_UTC = timezone.utc


class _Reloj:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    async def dormir(self, s):
        self.t += s


def _lim(por_minuto=LLAMADAS_POR_MINUTO_3B, tope=TOPE_CREDITOS_TWELVEDATA):
    r = _Reloj()
    return Limitador(por_minuto, tope, reloj=r, dormir=r.dormir)


def _td_intradia(*, desfase_h: int = 0, piso: str = "2019-01-01 00:00:00",
                 hoy: str = "2026-10-05 13:30:00", exchange_tz: str = "UTC"):
    """TwelveData falso: velas cada `intervalo` desde `piso`, en UTC más
    `desfase_h` horas (el defecto visto en XAU/USD)."""
    vistos = []

    async def get(url, params, headers):
        vistos.append(dict(params))
        if url.endswith("earliest_timestamp"):
            return 200, {}, json.dumps({"datetime": piso, "unix_time": _epoch_utc(piso)})
        paso = _MINUTOS[params["interval"]] * 60
        fin = min(_epoch_utc(params["end_date"]) + (0 if " " in params["end_date"] else 86399),
                  _epoch_utc(hoy))
        ini = max(_epoch_utc(params.get("start_date", piso)), _epoch_utc(piso))
        fin -= fin % paso
        if fin < ini:
            return 404, {}, json.dumps({"code": 404, "message": "Data not found", "status": "error"})
        epocas = list(range(fin, ini - 1, -paso))[:params["outputsize"]]
        vals = [{"datetime": datetime.fromtimestamp(e + desfase_h * 3600, _UTC).strftime("%Y-%m-%d %H:%M:%S"),
                 "open": "100", "high": "101", "low": "99", "close": f"{100 + math.sin(e / 9000):.6f}"}
                for e in epocas]
        return 200, {}, json.dumps({"meta": {"exchange_timezone": exchange_tz}, "values": vals})
    return get, vistos


# ── 5a ───────────────────────────────────────────────────────────────────

async def test_5a_pide_earliest_y_tres_end_dates_por_simbolo_e_intervalo():
    get, vistos = _td_intradia()
    out = await sondear_5a(key="K", limitador=_lim(), get=get)
    assert len(out["earliest_timestamp"]) == 4 and len(out["paginas"]) == 12
    assert all(v["timezone"] == "UTC" for v in vistos)
    pags = [(p["simbolo"], p["intervalo"], p["end_date"]) for p in out["paginas"]]
    assert pags == [(s, i, f) for s in ("BTC/USD", "XAU/USD") for i in ("5min", "15min")
                    for f in ("2018-06-30", "2021-06-30", "2024-06-30")]
    p2018 = out["paginas"][0]
    assert p2018["hay_datos"] is False and p2018["error"]["code"] == 404, "antes del piso"
    p2021 = out["paginas"][1]
    assert p2021["hay_datos"] is True and p2021["filas"] == 5000
    assert p2021["ultima_fecha"] == "2021-06-30 23:55:00"
    assert p2021["utc"] == {"meta_exchange_timezone": "UTC", "ninguna_pasa_el_end_date": True,
                            "en_la_grilla": True}
    assert all(v["outputsize"] == 5000 for v in vistos if "end_date" in v)


async def test_5a_detecta_velas_que_pasan_el_end_date():
    get, _ = _td_intradia(desfase_h=11, exchange_tz="Australia/Sydney")
    p = await pagina_5a("XAU/USD", "5min", "2024-06-30", key="K", limitador=_lim(), get=get)
    assert p["utc"]["ninguna_pasa_el_end_date"] is False
    assert p["utc"]["meta_exchange_timezone"] == "Australia/Sydney"


def test_chequeo_utc_grilla():
    vals = [{"datetime": "2024-06-30 10:07:00"}]
    assert chequeo_utc(vals, "5min", end_date="2024-06-30", meta={})["en_la_grilla"] is False
    assert chequeo_utc([], "5min", end_date="2024-06-30", meta={"exchange_timezone": "UTC"}) == {
        "meta_exchange_timezone": "UTC"}


# ── 5b ───────────────────────────────────────────────────────────────────

def _serie(n=600, paso=900, inicio=1_790_000_100 - 1_790_000_100 % 900, desfase=0, ruido=0.0):
    rng = np.random.default_rng(7)
    precios = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, n + desfase + 10)))
    return {inicio + k * paso: {"open": precios[k + desfase], "high": precios[k + desfase] * 1.001,
                                "low": precios[k + desfase] * 0.999,
                                "close": precios[k + desfase] * (1 + ruido)}
            for k in range(n)}


def test_5b_misma_serie_sin_desfase():
    d = _serie()
    r = comparar_bases(d, dict(d))
    assert r["velas_comunes"] == 600
    assert r["diferencia_close_pct"]["percentiles_abs"]["99"] == pytest.approx(0)
    assert r["correlacion_retornos"] == pytest.approx(1)
    assert r["desfase_de_maxima_correlacion_velas"] == 0


def test_5b_detecta_el_desfase_y_la_diferencia_de_base():
    d = _serie()
    t = _serie(desfase=3, ruido=0.001)   # TwelveData adelantada 3 velas y 0,1 % más cara
    r = comparar_bases(d, t, desfase_maximo=10)
    assert r["desfase_de_maxima_correlacion_velas"] == -3
    assert r["correlacion_maxima"] == pytest.approx(1)
    assert r["correlacion_retornos"] < 0.5
    assert r["diferencia_close_pct"]["mediana_con_signo"] != 0


def test_5b_el_desfase_se_busca_en_24_horas():
    """El corrimiento visto en el oro fue de unas 11 h: 44 velas M15. Con
    los parámetros por defecto, 5b tiene que encontrarlo."""
    d = _serie(n=800)
    t = _serie(n=800, desfase=44)
    r = comparar_bases(d, t)
    assert r["desfase_de_maxima_correlacion_velas"] == -44
    assert DESFASE_MAXIMO_VELAS * GRANULARIDAD_5B == 24 * 3600


def test_5b_diferencias_en_porcentaje_y_percentiles():
    d = {0: {"open": 1, "high": 1, "low": 1, "close": 100.0},
         900: {"open": 1, "high": 1, "low": 1, "close": 200.0}}
    t = {0: {"open": 1, "high": 1, "low": 1, "close": 101.0},
         900: {"open": 1, "high": 1, "low": 1, "close": 198.0}, 1800: {"open": 1, "high": 1, "low": 1, "close": 1}}
    r = comparar_bases(d, t, desfase_maximo=1)
    assert r["velas_comunes"] == 2
    assert r["diferencia_close_pct"]["percentiles_abs"]["50"] == pytest.approx(1.0)
    assert r["diferencia_close_pct"]["mediana_con_signo"] == pytest.approx(0.0)
    assert r["correlacion_retornos"] is None, "un solo retorno común no alcanza"


async def test_5b_twelvedata_pagina_el_ultimo_anio():
    get, vistos = _td_intradia(hoy="2026-10-05 13:30:00")
    ahora = datetime(2026, 10, 5, 13, 30, tzinfo=_UTC)
    velas, res = await serie_td_ultimo_anio("BTC/USD", key="K", limitador=_lim(), get=get,
                                            ahora=ahora)
    assert res["corte"] == "se llegó al inicio del año"
    assert len(velas) == 365 * 96 + 1
    assert all(v["timezone"] == "UTC" and v["interval"] == "15min" for v in vistos)
    assert len(vistos) == math.ceil((365 * 96 + 1) / 5000)
    assert vistos[1]["end_date"] == datetime.fromtimestamp(
        _epoch_utc("2026-10-05 13:30:00") - 4999 * 900 - 60, _UTC).strftime("%Y-%m-%d %H:%M:%S")


# ── 5c ───────────────────────────────────────────────────────────────────

def _m5(desde: datetime, dias: int, *, sin_fines_de_semana=False):
    out = {}
    e0 = int(desde.timestamp())
    for k in range(dias * 288):
        e = e0 + k * 300
        if sin_fines_de_semana and datetime.fromtimestamp(e, _UTC).weekday() >= 5:
            continue
        out[e] = {"epoch": e, "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0}
    return out


def test_5c_las_anclas_del_brief():
    assert ANCLAS_5C == {"oro": ("Europe/London", 8, 0), "btc": ("America/New_York", 9, 30)}
    assert VELAS_DEL_RANGO == 3 and GRANULARIDAD_5C == 300


def test_5c_ancla_de_londres_respeta_el_horario_de_verano():
    velas = _m5(datetime(2026, 3, 27, tzinfo=_UTC), 5)
    dias, faltan = rangos_de_apertura(velas, "Europe/London", 8, 0)
    por_fecha = {d["fecha"]: d["ancla"] for d in dias}
    # 27-mar (GMT): 08:00 Londres = 08:00 UTC; 30-mar (BST): 07:00 UTC.
    assert por_fecha["2026-03-27"] == int(datetime(2026, 3, 27, 8, tzinfo=_UTC).timestamp())
    assert por_fecha["2026-03-30"] == int(datetime(2026, 3, 30, 7, tzinfo=_UTC).timestamp())


def test_5c_ancla_de_nueva_york():
    velas = _m5(datetime(2026, 3, 6, tzinfo=_UTC), 4)
    dias, _ = rangos_de_apertura(velas, "America/New_York", 9, 30)
    por_fecha = {d["fecha"]: d["ancla"] for d in dias}
    # 6-mar (EST): 14:30 UTC; 9-mar (EDT): 13:30 UTC.
    assert por_fecha["2026-03-06"] == int(datetime(2026, 3, 6, 14, 30, tzinfo=_UTC).timestamp())
    assert por_fecha["2026-03-09"] == int(datetime(2026, 3, 9, 13, 30, tzinfo=_UTC).timestamp())


def test_5c_el_rango_son_las_tres_velas_y_nada_mas():
    velas = _m5(datetime(2026, 6, 1, tzinfo=_UTC), 2)
    ancla = int(datetime(2026, 6, 1, 7, tzinfo=_UTC).timestamp())   # 08:00 BST
    velas[ancla] = {"epoch": ancla, "open": 200.0, "high": 202.0, "low": 199.0, "close": 200}
    velas[ancla + 300] = {"epoch": ancla + 300, "open": 200, "high": 203.0, "low": 199.5, "close": 200}
    velas[ancla + 600] = {"epoch": ancla + 600, "open": 250, "high": 201.0, "low": 198.0, "close": 200}
    velas[ancla + 900] = {"epoch": ancla + 900, "open": 1, "high": 9999.0, "low": 0.001, "close": 1}
    velas[ancla - 300] = {"epoch": ancla - 300, "open": 1, "high": 9999.0, "low": 0.001, "close": 1}
    dias, _ = rangos_de_apertura(velas, "Europe/London", 8, 0)
    d = next(x for x in dias if x["fecha"] == "2026-06-01")
    assert d["rango_fraccion"] == pytest.approx((203.0 - 198.0) / 200.0)


def test_5c_un_dia_sin_las_tres_velas_no_entra_y_se_cuenta():
    velas = _m5(datetime(2026, 6, 1, tzinfo=_UTC), 9, sin_fines_de_semana=True)
    dias, faltan = rangos_de_apertura(velas, "Europe/London", 8, 0)
    assert {d["fecha"] for d in dias}.isdisjoint({"2026-06-06", "2026-06-07"})
    assert faltan == 2, "el sábado y el domingo; el 10-jun queda fuera de la serie"
    assert len(dias) == 7


def test_5c_estadisticas_con_kappa():
    dias = [{"rango_fraccion": r} for r in (0.001, 0.002, 0.003, 0.004, 0.02)]
    r = mecanica_rango(dias, 0.0003)
    d50, d100 = 1 / 50 - 0.0003, 1 / 100 - 0.0003
    assert r["umbral_pct"]["x100"] == pytest.approx(0.6 * d100 * 100)
    assert r["fraccion_de_dias_con_rango_bajo_el_umbral"]["x100"] == pytest.approx(
        np.mean([x <= 0.6 * d100 for x in (0.001, 0.002, 0.003, 0.004, 0.02)]))
    assert r["fraccion_de_dias_con_rango_bajo_el_umbral"]["x50"] == pytest.approx(
        np.mean([x <= 0.6 * d50 for x in (0.001, 0.002, 0.003, 0.004, 0.02)]))
    assert r["costo_en_fraccion_de_R"]["50"] == pytest.approx(0.0003 / (0.003 + 0.0003))
    assert r["rango_pct"]["50"] == pytest.approx(0.3)
    assert set(r["rango_pct"]) == {"10", "25", "50", "75", "90"}


def test_5c_sin_kappa_lo_dice():
    r = mecanica_rango([{"rango_fraccion": 0.01}], None)
    assert r["kappa"] is None and "sin_kappa" in r and "costo_en_fraccion_de_R" not in r


def test_5c_no_calcula_retornos_ni_resultados():
    """'Solo mecánica': las funciones de 5c no reciben ni producen nada que
    se parezca a un retorno o a un resultado de operación."""
    import inspect
    fuente = inspect.getsource(rangos_de_apertura) + inspect.getsource(mecanica_rango)
    for prohibido in ("close", "retorno", "pnl", "ganancia", "_retornos"):
        assert prohibido not in fuente, prohibido


# ── 5a + 5b, la sonda entera ─────────────────────────────────────────────

def _deriv_m15(desde: int, n: int):
    def th(p):
        fin = int(p["end"]) if p["end"] != "latest" else desde + (n - 1) * 900
        ini = max(desde, fin - 4999 * 900)
        if fin < desde:
            return {"candles": []}
        return {"candles": [{"epoch": e, "open": 100, "high": 101, "low": 99,
                             "close": 100 + math.sin(e / 9000)}
                            for e in range(ini - ini % 900, fin + 1, 900)]}
    return DerivFalso({
        "active_symbols": lambda p: {"active_symbols": [
            {"underlying_symbol": "cryBTCUSD", "market": "cryptocurrency"},
            {"underlying_symbol": "frxXAUUSD", "market": "commodities"},
            {"underlying_symbol": "frxEURUSD", "market": "forex"}]},
        "ticks_history": th})


async def test_la_sonda_5a_5b_entera():
    ahora = datetime(2026, 10, 5, 13, 30, tzinfo=_UTC)
    get, vistos = _td_intradia(hoy="2026-10-05 13:30:00")
    d = _deriv_m15(int(ahora.timestamp()) - 365 * 86400, 365 * 96)
    inf = await sondear_5a_5b(key="Kq7", abrir=d.connector, get=get, limitador=_lim(),
                              ahora=ahora, pausa_s=0)
    assert inf["creditos_usados"] == len(vistos) <= TOPE_CREDITOS_TWELVEDATA
    b = inf["5b"]["BTC/USD"]
    assert b["deriv_simbolo"] == "cryBTCUSD"
    assert b["velas_comunes"] > 30000
    assert b["desfase_de_maxima_correlacion_velas"] == 0
    assert b["correlacion_retornos"] == pytest.approx(1)
    assert "Kq7" not in texto_libre(inf)
    assert {next(iter(m)) for m in d.enviados} == {"active_symbols", "ticks_history"}


async def test_la_sonda_5a_5b_respeta_el_limitador_por_minuto():
    r = _Reloj()
    lim = Limitador(LLAMADAS_POR_MINUTO_3B, TOPE_CREDITOS_TWELVEDATA, reloj=r, dormir=r.dormir)
    get, vistos = _td_intradia()
    await sondear_5a(key="K", limitador=lim, get=get)
    assert len(vistos) == 16
    assert r.t >= 120, "16 llamadas a 7 por minuto necesitan al menos dos esperas"
