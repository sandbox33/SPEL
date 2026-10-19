"""
tests/test_velas_d1.py
========================
ingestion/velas_d1.py (brief del Admin 14, 14b, punto 1). Offline: un
TwelveData falso con la interfaz de `fetch_pagina` y varias series, y el
Deriv falso de siempre, que en diario ignora `end` como la API real (sonda
§0.A-2).
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from ingestion import velas_d1 as vd
from ingestion import velas_intradia as vi
from ingestion.adapters import _TWELVEDATA_GRANULARITY_SECONDS, PaginaTwelveData
from ingestion.deriv_publico import ENDPOINT_PUBLICO
from ingestion.limitador import Limitador
from ingestion.velas import Calendario, leer_velas, ruta_revisiones, ruta_serie
from tests.deriv_falso import DerivFalso

D, H = 86400, 3600
_UTC = timezone.utc
#: Domingo 18-oct-2026, 06:00 UTC.
AHORA = int(datetime(2026, 10, 18, 6, tzinfo=_UTC).timestamp())
HOY = AHORA - AHORA % D

_VACIA_400 = ("400: No data is available on the specified dates. "
              "Try setting different start/end dates.")


@pytest.fixture(autouse=True)
def _drive(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(tmp_path))
    monkeypatch.setattr(vi.time, "time", lambda: AHORA)


def _vela(e: int, base: float = 100.0) -> dict:
    return {"epoch": e, "open": base, "high": base + 1, "low": base - 1, "close": base + 0.5}


class _TDFalso:
    """fetch_pagina sobre varias series fijas, por (símbolo, timeframe): las
    velas más recientes del rango, a lo sumo `n` y el `outputsize`, y solo
    las cerradas según `hora`, que hace de cabecera Date."""

    def __init__(self, series: dict, *, hora: int = AHORA, n: int = 5000):
        self.series = {k: sorted(v) for k, v in series.items()}
        self.hora, self.n = hora, n
        self.pedidos: list[dict] = []

    async def fetch_pagina(self, simbolo, tf, *, start_date=None, end_date=None, outputsize=5000):
        self.pedidos.append({"simbolo": simbolo, "tf": tf, "start_date": start_date,
                             "end_date": end_date, "outputsize": outputsize})
        g = _TWELVEDATA_GRANULARITY_SECONDS[tf]
        ini = vi._epoch_de(start_date) if start_date else 0
        fin = vi._epoch_de(end_date) if end_date else 10**12
        sel = [e for e in self.series.get((simbolo, tf), []) if ini <= e <= fin]
        sel = sel[-min(self.n, outputsize):]
        if not sel:
            return PaginaTwelveData(pd.DataFrame(), "sha-vacia", {}, True, 0, motivo=_VACIA_400)
        cerradas = [e for e in sel if e + g <= self.hora]
        df = pd.DataFrame({"timestamp": pd.to_datetime(cerradas, unit="s", utc=True),
                           **{k: [_vela(e)[k] for e in cerradas]
                              for k in ("open", "high", "low", "close")}})
        return PaginaTwelveData(df, f"sha-{simbolo}-{tf}-{fin}", {"api-credits-left": "7"},
                                False, len(sel) - len(cerradas), self.hora)


def _lim(tope=50):
    class _R:
        t = 0.0

    async def dormir(s):
        _R.t += s
    return Limitador(7, tope, reloj=lambda: _R.t, dormir=dormir)


def _deriv(por_simbolo: dict, *, ahora=AHORA):
    """ticks_history por símbolo; en diario ignora `end` y devuelve siempre
    la ventana más reciente, como la API (sonda §0.A-2)."""
    def th(p):
        sel = [e for e in por_simbolo.get(p["ticks_history"], []) if e <= ahora][-p["count"]:]
        return {"candles": [_vela(e, 200.0) for e in sel]}
    return DerivFalso({"time": lambda p: {"time": ahora}, "ticks_history": th})


def _desde(serie) -> int:
    return vi._epoch_de(serie.inicio)


def _historia():
    """Cinco días de cada diaria desde su inicio, y la muestra 1h entera."""
    btc = [_desde(vd.BTC_D1) + k * D for k in range(5)]
    oro = [_desde(vd.ORO_D1) + k * D for k in range(5)]
    m = vd.muestra_h1(AHORA)
    h1 = list(range(_desde(m), AHORA, H))   # la de las 06:00 todavía no cerró
    return {("BTCUSD", "1d"): btc, ("XAUUSD", "1d"): oro, ("BTCUSD", "1h"): h1}


def _diarias_deriv():
    """Un año de diarias hasta hoy, que no cerró: cripto todos los días y
    el oro de lunes a viernes."""
    return {"cryBTCUSD": [HOY - k * D for k in range(366)][::-1],
            "frxXAUUSD": [HOY - k * D for k in range(366)
                          if datetime.fromtimestamp(HOY - k * D, _UTC).weekday() < 5][::-1]}


# ═══ Las cinco series ═════════════════════════════════════════════════════

async def test_baja_las_cinco_series_y_las_escribe_donde_corresponde():
    hist, dv = _historia(), _diarias_deriv()
    td = _TDFalso(hist)
    abiertos = []
    d = _deriv(dv)

    def abrir(u):
        abiertos.append(u)
        return d.connector(u)
    bloques = await vd.ingerir(key="K", write=True, abrir=abrir, adapter=td,
                               limitador=_lim(), ahora=AHORA, pausa_s=0)
    assert [(b["fuente"], b["serie"]) for b in bloques] == [
        ("twelvedata", "td_BTCUSD"), ("twelvedata", "td_XAUUSD"), ("twelvedata", "td_BTCUSD"),
        ("deriv", "cryBTCUSD"), ("deriv", "frxXAUUSD")]
    assert leer_velas("td_BTCUSD", D)["epoch"].to_list() == hist[("BTCUSD", "1d")]
    assert leer_velas("td_XAUUSD", D)["epoch"].to_list() == hist[("XAUUSD", "1d")]
    assert leer_velas("td_BTCUSD", H)["epoch"].to_list() == hist[("BTCUSD", "1h")]
    # Las diarias de Deriv, sin la de hoy, que no cerró.
    assert leer_velas("cryBTCUSD", D)["epoch"].to_list() == dv["cryBTCUSD"][:-1]
    assert leer_velas("frxXAUUSD", D)["epoch"].to_list() == [e for e in dv["frxXAUUSD"] if e < HOY]
    assert abiertos == [ENDPOINT_PUBLICO, ENDPOINT_PUBLICO]
    assert not any(b.get("no_escrito") or b.get("error") for b in bloques)


async def test_twelvedata_pide_cada_serie_con_su_timeframe_y_su_inicio():
    td = _TDFalso(_historia())
    await vd.ingerir(key="K", write=True, abrir=_deriv(_diarias_deriv()).connector,
                     adapter=td, limitador=_lim(), ahora=AHORA, pausa_s=0)
    pedidos = [(p["simbolo"], p["tf"], p["start_date"]) for p in td.pedidos]
    assert pedidos == [("BTCUSD", "1d", "2017-08-28 00:00:00"),
                       ("XAUUSD", "1d", "1979-12-26 00:00:00"),
                       ("BTCUSD", "1h", vi._fmt(HOY - 60 * D))]


async def test_deriv_va_en_diario_sin_horario_y_con_el_calendario_de_cada_activo():
    d = _deriv(_diarias_deriv())
    bloques = await vd.ingerir(key="K", write=True, abrir=d.connector,
                               adapter=_TDFalso(_historia()), limitador=_lim(), ahora=AHORA, pausa_s=0)
    th = d.de_tipo("ticks_history")
    assert {p["granularity"] for p in th} == {D}
    assert {p["ticks_history"] for p in th} == {"cryBTCUSD", "frxXAUUSD"}
    assert not d.de_tipo("trading_times"), "el horario del oro lo guarda el M5, no D1"
    cripto, oro = bloques[3], bloques[4]
    assert cripto["dias_con_velas"] == 365 and oro["corte"] == "la página no retrocedió"
    assert (cripto["n_huecos"], cripto["historia_usable"]) == (0, 365)
    assert oro["n_huecos"] == 0 and oro["historia_usable"] == oro["dias_con_velas"]


async def test_un_fin_de_semana_es_hueco_en_cripto_y_no_en_el_oro():
    dv = _diarias_deriv()
    sabado = next(e for e in dv["cryBTCUSD"][-10:]
                  if datetime.fromtimestamp(e, _UTC).weekday() == 5)
    dv["cryBTCUSD"].remove(sabado)
    bloques = await vd.ingerir(key="K", write=True, abrir=_deriv(dv).connector,
                               adapter=_TDFalso(_historia()), limitador=_lim(), ahora=AHORA,
                               pausa_s=0)
    cripto, oro = bloques[3], bloques[4]
    assert cripto["n_huecos"] == 1 and cripto["historia_usable"] < 364
    assert oro["n_huecos"] == 0


def test_deriv_no_va_por_deriv_ws():
    fuente = Path(vd.__file__).read_text(encoding="utf-8")
    importados = {n.module for n in ast.walk(ast.parse(fuente)) if isinstance(n, ast.ImportFrom)}
    assert "ingestion.deriv_ws" not in importados
    assert "deriv_ws" not in {a.name for n in ast.walk(ast.parse(fuente))
                              if isinstance(n, ast.Import) for a in n.names}


def test_los_pares_de_d1():
    btc, oro = vd.ACTIVOS_D1
    assert (btc.activo, btc.td.serie, btc.deriv, btc.td.calendario) == \
        ("BTC", "td_BTCUSD", "cryBTCUSD", Calendario.CONTINUO)
    assert (oro.activo, oro.td.serie, oro.deriv, oro.td.calendario) == \
        ("XAU", "td_XAUUSD", "frxXAUUSD", Calendario.HABIL)
    assert (btc.intradia, btc.granularidad_intradia) == ("td_BTCUSD", H)
    assert (oro.intradia, oro.granularidad_intradia) == ("td_XAUUSD", 300)


# ═══ La muestra horaria ═══════════════════════════════════════════════════

def test_la_muestra_h1_son_60_dias_desde_las_00_utc():
    m = vd.muestra_h1(AHORA)
    assert (m.simbolo, m.timeframe, m.serie, m.granularidad) == ("BTCUSD", "1h", "td_BTCUSD", H)
    assert m.inicio == "2026-08-19 00:00:00"
    assert vd.muestra_h1(HOY).inicio == m.inicio == vd.muestra_h1(HOY + D - 1).inicio


async def test_la_muestra_h1_sigue_desde_lo_guardado_y_no_se_rehace():
    hist = _historia()
    await vd.ingerir(key="K", write=True, abrir=_deriv(_diarias_deriv()).connector,
                     adapter=_TDFalso(hist), limitador=_lim(), ahora=AHORA, pausa_s=0)
    ultima = hist[("BTCUSD", "1h")][-1]
    td = _TDFalso({**hist, ("BTCUSD", "1h"): hist[("BTCUSD", "1h")] + [ultima + H]},
                  hora=AHORA + 2 * H)
    await vd.ingerir(key="K", write=True, abrir=_deriv(_diarias_deriv()).connector,
                     adapter=td, limitador=_lim(), ahora=AHORA + 30 * D, pausa_s=0)
    assert [p["start_date"] for p in td.pedidos if p["tf"] == "1h"] == [vi._fmt(ultima + H)]
    assert leer_velas("td_BTCUSD", H)["epoch"].to_list()[-1] == ultima + H


# ═══ Créditos ═════════════════════════════════════════════════════════════

@pytest.mark.parametrize("velas, pedidos", [(1, 2), (5000, 2), (5001, 3), (10000, 3), (12288, 4)])
def test_pedidos_a_lo_sumo(velas, pedidos):
    assert vd.pedidos_a_lo_sumo(0, (velas - 1) * D, D) == pedidos


@pytest.mark.parametrize("desde, hasta", [(10 * D, 9 * D), (10**6 * D, 0)])
def test_pedidos_a_lo_sumo_sin_rango_es_la_vacia(desde, hasta):
    assert vd.pedidos_a_lo_sumo(desde, hasta, D) == 1


def test_el_plan_de_creditos_de_la_primera_corrida():
    plan = vd.plan_de_creditos(vd.series_td(AHORA), AHORA)
    assert [(f["serie"], f["timeframe"], f["desde"], f["pedidos_a_lo_sumo"])
            for f in plan["series"]] == [
        ("td_BTCUSD", "1d", "2017-08-28 00:00:00", 2),
        ("td_XAUUSD", "1d", "1979-12-26 00:00:00", 5),
        ("td_BTCUSD", "1h", "2026-08-19 00:00:00", 2)]
    assert plan["creditos_a_lo_sumo"] == 9 and plan["tope_de_la_corrida"] == vi.TOPE_CREDITOS_TD


async def test_el_plan_cuenta_desde_lo_guardado():
    await vd.ingerir(key="K", write=True, abrir=_deriv(_diarias_deriv()).connector,
                     adapter=_TDFalso(_historia()), limitador=_lim(), ahora=AHORA, pausa_s=0)
    plan = vd.plan_de_creditos(vd.series_td(AHORA), AHORA)
    ultima = _historia()[("XAUUSD", "1d")][-1]
    assert plan["series"][1]["desde"] == vi._fmt(ultima + D)
    assert plan["series"][2]["desde"] == vi._fmt(AHORA), "la 05:00 cerró a las 06:00"


def test_el_plan_sale_antes_del_primer_pedido(monkeypatch, capsys):
    monkeypatch.setattr(vd, "load_secret", lambda *a, **k: "K")
    visto = []

    async def ingerir(**kw):
        visto.append(capsys.readouterr().out)
        return []
    monkeypatch.setattr(vd, "ingerir", ingerir)
    assert vd.main([]) == 0
    assert visto[0].startswith("=== CRÉDITOS ESTIMADOS ===")
    assert json.loads(visto[0].splitlines()[1])["creditos_a_lo_sumo"] == 9


# ═══ Páginas ══════════════════════════════════════════════════════════════

async def test_cada_pagina_queda_con_su_sha256():
    bloques = await vd.ingerir(key="K", write=True, abrir=_deriv(_diarias_deriv()).connector,
                               adapter=_TDFalso(_historia()), limitador=_lim(), ahora=AHORA, pausa_s=0)
    td = vi._leer_filas(vd.ruta_paginas("td_XAUUSD", D))
    assert [p["sha256"] for p in td] == [p["sha256"] for p in bloques[1]["paginas"]]
    assert td[0]["fuente"] == "twelvedata" and td[0]["fecha_servidor"] == "2026-10-18"
    dr = vi._leer_filas(vd.ruta_paginas("frxXAUUSD", D))
    assert [p["sha256"] for p in dr] == [p["sha256"] for p in bloques[4]["detalle_paginas"]]
    assert [p["pagina"] for p in dr] == [0, 1] and dr[0]["fuente"] == "deriv"
    assert len(dr[0]["sha256"]) == 64
    assert bloques[4]["paginas_registradas"] == 2


async def test_sin_write_no_queda_nada():
    bloques = await vd.ingerir(key="K", write=False, abrir=_deriv(_diarias_deriv()).connector,
                               adapter=_TDFalso(_historia()), limitador=_lim(), ahora=AHORA, pausa_s=0)
    assert all(b["paginas_registradas"] == 0 for b in bloques)
    assert not ruta_serie("td_XAUUSD", D).exists() and not vd.ruta_paginas("td_XAUUSD", D).exists()


async def test_una_descarga_que_no_se_escribio_no_deja_paginas():
    # Con tope de 1 crédito, la diaria de BTC entra y la del oro no.
    hist = _historia()
    hist[("XAUUSD", "1d")] = [_desde(vd.ORO_D1) + k * D for k in range(12)]
    bloques = await vd.ingerir(key="K", write=True, abrir=_deriv(_diarias_deriv()).connector,
                               adapter=_TDFalso(hist, n=5), limitador=_lim(tope=2),
                               ahora=AHORA, pausa_s=0)
    assert bloques[0]["paginas_registradas"] == 1 and "no_escrito" in bloques[1]
    assert bloques[1]["paginas_registradas"] == 0
    assert not vd.ruta_paginas("td_XAUUSD", D).exists()


# ═══ Revisiones, errores, CLI ═════════════════════════════════════════════

async def test_una_revision_de_deriv_va_aparte_y_no_sobrescribe():
    dv = _diarias_deriv()
    await vd.ingerir(key="K", write=True, abrir=_deriv(dv).connector,
                     adapter=_TDFalso(_historia()), limitador=_lim(), ahora=AHORA, pausa_s=0)
    d = _deriv(dv)
    d.manejadores["ticks_history"] = lambda p: {"candles": [
        {**_vela(e, 200.0), "close": 200.75} for e in dv[p["ticks_history"]] if e <= AHORA]}
    await vd.ingerir(key="K", write=True, abrir=d.connector,
                     adapter=_TDFalso(_historia()), limitador=_lim(), ahora=AHORA, pausa_s=0)
    assert set(leer_velas("cryBTCUSD", D)["close"].to_list()) == {200.5}
    revisiones = vi._leer_filas(ruta_revisiones("cryBTCUSD", D))
    assert len(revisiones) == 365 and revisiones[0]["nueva"]["close"] == 200.75


async def test_un_deriv_que_falla_no_borra_lo_de_twelvedata():
    def abrir(u):
        raise ConnectionError("sin red")
    bloques = await vd.ingerir(key="K", write=True, abrir=abrir,
                               adapter=_TDFalso(_historia()), limitador=_lim(), ahora=AHORA, pausa_s=0)
    assert bloques[3]["error"].startswith("ConnectionError") and bloques[4]["error"]
    assert bloques[3]["paginas_registradas"] == 0
    assert leer_velas("td_BTCUSD", D).height == 5


def test_la_pausa_entre_pedidos_de_deriv_es_la_del_m5():
    import inspect
    assert inspect.signature(vd.ingerir).parameters["pausa_s"].default == vi.PAUSA_DERIV_S


def test_sin_key_sale_2(monkeypatch):
    monkeypatch.setattr(vd, "load_secret", lambda *a, **k: None)
    assert vd.main([]) == 2


def test_un_bloque_con_error_sale_1(monkeypatch):
    monkeypatch.setattr(vd, "load_secret", lambda *a, **k: "K")

    async def ingerir(**kw):
        return [{"fuente": "deriv", "serie": "cryBTCUSD", "error": "x"}]
    monkeypatch.setattr(vd, "ingerir", ingerir)
    assert vd.main([]) == 1


def test_la_key_no_llega_al_informe(monkeypatch):
    monkeypatch.setattr(vd, "load_secret", lambda *a, **k: "CLAVE-SECRETA")

    async def ingerir(**kw):
        return [{"fuente": "twelvedata", "serie": "td_BTCUSD", "x": "CLAVE-SECRETA"}]
    monkeypatch.setattr(vd, "ingerir", ingerir)
    with pytest.raises(AssertionError, match="la key"):
        vd.main([])


# ═══ Brief 15, punto 6: el M5 del oro al día, en la misma corrida ═════════

M5 = 300


def _deriv_por_gran(por_simbolo_gran: dict, *, ahora=AHORA):
    """ticks_history por (símbolo, granularidad), siempre la ventana más
    reciente, como la API."""
    def th(p):
        sel = [e for e in por_simbolo_gran.get((p["ticks_history"], p["granularity"]), [])
               if e <= ahora][-p["count"]:]
        return {"candles": [_vela(e, 300.0) for e in sel]}
    return DerivFalso({"time": lambda p: {"time": ahora}, "ticks_history": th})


def _m5(desde: int, n: int) -> list[int]:
    return [desde + k * M5 for k in range(n)]


def _con_m5_guardado():
    """Un M5 del oro ya guardado de las dos fuentes, que termina 2 h antes
    de AHORA, y lo que trae cada fuente: lo guardado y lo que sigue."""
    viejo = _m5(AHORA - 4 * H, 24)                 # hasta AHORA − 2 h
    nuevo = _m5(AHORA - 2 * H, 24)                 # la última cierra justo a AHORA
    for serie in ("td_XAUUSD", "frxXAUUSD"):
        vi._append(ruta_serie(serie, M5), [
            {"epoch": e, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5} for e in viejo])
    return viejo, nuevo


async def test_con_m5_el_oro_se_actualiza_despues_de_las_diarias_y_desde_lo_guardado():
    viejo, nuevo = _con_m5_guardado()
    td = _TDFalso({**_historia(), ("XAUUSD", "5m"): viejo + nuevo})
    dv = _diarias_deriv()
    d = _deriv_por_gran({("cryBTCUSD", D): dv["cryBTCUSD"], ("frxXAUUSD", D): dv["frxXAUUSD"],
                         ("frxXAUUSD", M5): viejo + nuevo})
    bloques = await vd.ingerir(key="K", write=True, abrir=d.connector, adapter=td,
                               limitador=_lim(), ahora=AHORA, pausa_s=0, con_m5=True)
    assert [(b["fuente"], b["serie"], b["granularidad"]) for b in bloques[-2:]] == [
        ("twelvedata", "td_XAUUSD", M5), ("deriv", "frxXAUUSD", M5)]
    assert (td.pedidos[-1]["tf"], td.pedidos[-1]["start_date"]) == ("5m", vi._fmt(viejo[-1] + M5))
    th = d.de_tipo("ticks_history")
    assert [p["granularity"] for p in th][-1] == M5 and th[0]["granularity"] == D, \
        "el M5 de Deriv va después de las diarias"
    assert not d.de_tipo("trading_times")
    for serie in ("td_XAUUSD", "frxXAUUSD"):
        assert leer_velas(serie, M5)["epoch"].to_list() == viejo + nuevo, serie
    assert not any(b.get("no_escrito") or b.get("error") for b in bloques)


async def test_sin_con_m5_la_corrida_no_toca_el_m5():
    _con_m5_guardado()
    td = _TDFalso(_historia())
    d = _deriv_por_gran({("cryBTCUSD", D): _diarias_deriv()["cryBTCUSD"],
                         ("frxXAUUSD", D): _diarias_deriv()["frxXAUUSD"]})
    bloques = await vd.ingerir(key="K", write=True, abrir=d.connector, adapter=td,
                               limitador=_lim(), ahora=AHORA, pausa_s=0)
    assert len(bloques) == 5 and "5m" not in {p["tf"] for p in td.pedidos}
    assert {p["granularity"] for p in d.de_tipo("ticks_history")} == {D}


async def test_un_m5_de_deriv_que_falla_no_borra_lo_demas():
    viejo, nuevo = _con_m5_guardado()
    dv = _diarias_deriv()
    d = _deriv_por_gran({("cryBTCUSD", D): dv["cryBTCUSD"], ("frxXAUUSD", D): dv["frxXAUUSD"]})
    abiertos = []

    def abrir(u):
        abiertos.append(u)
        if len(abiertos) == 3:
            raise ConnectionError("sin red")
        return d.connector(u)
    bloques = await vd.ingerir(key="K", write=True, abrir=abrir,
                               adapter=_TDFalso({**_historia(), ("XAUUSD", "5m"): viejo + nuevo}),
                               limitador=_lim(), ahora=AHORA, pausa_s=0, con_m5=True)
    assert bloques[-1]["error"].startswith("ConnectionError") and bloques[-1]["serie"] == "frxXAUUSD"
    assert leer_velas("td_XAUUSD", M5).height == len(viejo + nuevo)
    assert leer_velas("frxXAUUSD", D).height > 0


def test_main_activa_el_m5_y_publica_sus_creditos_antes_del_primer_pedido(monkeypatch, capsys):
    viejo, _ = _con_m5_guardado()
    monkeypatch.setattr(vd, "load_secret", lambda *a, **k: "K")
    visto = {}

    async def ingerir(**kw):
        visto.update(kw, salida=capsys.readouterr().out)
        return []
    monkeypatch.setattr(vd, "ingerir", ingerir)
    monkeypatch.setattr(vd.time, "time", lambda: AHORA)
    assert vd.main([]) == 0 and visto["con_m5"] is True
    lineas = visto["salida"].splitlines()
    i = lineas.index("=== CRÉDITOS ESTIMADOS M5 DEL ORO ===")
    plan = json.loads(lineas[i + 1])
    assert [(f["serie"], f["timeframe"], f["desde"]) for f in plan["series"]] == [
        ("td_XAUUSD", "5m", vi._fmt(viejo[-1] + M5))]
    assert plan["creditos_a_lo_sumo"] == 2


def test_el_titulo_de_cada_descarga_dice_su_granularidad(monkeypatch, capsys):
    monkeypatch.setattr(vd, "load_secret", lambda *a, **k: "K")

    async def ingerir(**kw):
        return [{"fuente": "twelvedata", "serie": "td_XAUUSD", "granularidad": 300},
                {"fuente": "twelvedata", "serie": "td_XAUUSD", "granularidad": 86400}]
    monkeypatch.setattr(vd, "ingerir", ingerir)
    vd.main([])
    titulos = [l for l in capsys.readouterr().out.splitlines() if l.startswith("=== DESCARGA")]
    assert titulos == ["=== DESCARGA TWELVEDATA td_XAUUSD 300 ===",
                       "=== DESCARGA TWELVEDATA td_XAUUSD 86400 ==="]
