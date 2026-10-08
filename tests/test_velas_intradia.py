"""
tests/test_velas_intradia.py
==============================
ingestion/velas_intradia.py, deriv_publico.py y limitador.py (brief del
Admin del 06-oct-2026 (4), punto 1). Offline: un TwelveData falso con la
interfaz de `fetch_pagina` y el Deriv falso de siempre.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from ingestion import velas_intradia as vi
from ingestion.adapters import AdapterConnectionError, PaginaTwelveData
from ingestion.deriv_publico import (
    ENDPOINT_PUBLICO,
    MENSAJES_PUBLICOS,
    CanalPublico,
    validar_publico,
)
from ingestion.deriv_ws import MensajeNoPermitidoError
from ingestion.limitador import Limitador
from ingestion.velas import leer_velas, ruta_revisiones, ruta_serie
from tests.deriv_falso import DerivFalso

M5 = 300
_UTC = timezone.utc
#: Lunes 2026-10-05 08:00 UTC.
T0 = int(datetime(2026, 10, 5, 8, tzinfo=_UTC).timestamp())


@pytest.fixture(autouse=True)
def _drive(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(tmp_path))


def _vela(e: int, base: float = 2650.0) -> dict:
    return {"epoch": e, "open": base, "high": base + 1, "low": base - 1, "close": base + 0.5}


class _TDFalso:
    """La interfaz de TwelveDataAdapter.fetch_pagina sobre una serie fija.
    Devuelve las `n` velas más recientes del rango, como la API."""

    def __init__(self, epocas, *, hora, n=4):
        self.epocas = sorted(epocas)
        self.hora, self.n = hora, n
        self.pedidos: list[dict] = []

    async def fetch_pagina(self, simbolo, tf, *, start_date=None, end_date=None, outputsize=5000):
        self.pedidos.append({"simbolo": simbolo, "tf": tf, "start_date": start_date,
                             "end_date": end_date})
        ini = vi._epoch_de(start_date) if start_date else 0
        fin = vi._epoch_de(end_date) if end_date else 10**12
        sel = [e for e in self.epocas if ini <= e <= fin][-self.n:]
        if not sel:
            return PaginaTwelveData(pd.DataFrame(), "sha-vacia", {}, True, 0)
        cerradas = [e for e in sel if e + M5 <= self.hora]
        df = pd.DataFrame({"timestamp": pd.to_datetime(cerradas, unit="s", utc=True),
                           **{k: [_vela(e)[k] for e in cerradas]
                              for k in ("open", "high", "low", "close")}})
        return PaginaTwelveData(df, f"sha-{fin}", {"api-credits-left": "700"}, False,
                                len(sel) - len(cerradas), self.hora)


def _lim(tope=50):
    class _R:
        t = 0.0

    async def dormir(s):
        _R.t += s
    return Limitador(7, tope, reloj=lambda: _R.t, dormir=dormir)


# ═══ TwelveData ═══════════════════════════════════════════════════════════

async def test_td_primera_corrida_pagina_hasta_el_inicio_y_escribe(monkeypatch):
    inicio = vi._epoch_de(vi.INICIO_TD)
    epocas = [inicio + 70 * 60 + k * M5 for k in range(10)]   # desde 01:10
    td = _TDFalso(epocas, hora=epocas[-1] + 2 * M5)
    monkeypatch.setattr(vi.time, "time", lambda: epocas[-1] + 3 * M5)
    rep, res = await vi.ingerir_td(td, write=True, limitador=_lim())
    assert td.pedidos[0]["start_date"] == "2020-03-16 00:00:00"
    assert all(p["start_date"] == "2020-03-16 00:00:00" for p in td.pedidos)
    assert res["completo"] is True and rep.nuevas == 10
    assert [p["end_date"] for p in td.pedidos][1] == vi._fmt(epocas[6] - 60), \
        "la página siguiente termina 60 s antes de la primera vela de la anterior"
    assert leer_velas("td_XAUUSD", M5)["epoch"].to_list() == epocas
    assert res["llamadas"] == len(td.pedidos) and res["creditos_usados"] == len(td.pedidos)
    assert res["creditos_api"] == {"api-credits-left": "700"}


async def test_td_lo_abierto_por_la_hora_del_servidor_no_entra(monkeypatch):
    inicio = vi._epoch_de(vi.INICIO_TD)
    epocas = [inicio + k * M5 for k in range(6)]
    td = _TDFalso(epocas, hora=epocas[-1] + 100)     # la última no cerró
    monkeypatch.setattr(vi.time, "time", lambda: epocas[-1] + 10 * M5)
    rep, res = await vi.ingerir_td(td, write=True, limitador=_lim())
    assert rep.nuevas == 5 and res["abiertas_descartadas"] == 1


async def test_td_cortada_por_el_tope_no_escribe(monkeypatch):
    inicio = vi._epoch_de(vi.INICIO_TD)
    epocas = [inicio + k * M5 for k in range(20)]
    td = _TDFalso(epocas, hora=epocas[-1] + M5)
    monkeypatch.setattr(vi.time, "time", lambda: epocas[-1] + M5)
    rep, res = await vi.ingerir_td(td, write=True, limitador=_lim(tope=2))
    assert res["completo"] is False and "no_escrito" in res
    assert "tope de 2" in res["corte"] and len(td.pedidos) == 2
    assert not ruta_serie("td_XAUUSD", M5).exists()


async def test_td_un_error_no_escribe(monkeypatch):
    class _Roto(_TDFalso):
        async def fetch_pagina(self, *a, **k):
            if self.pedidos:
                raise AdapterConnectionError("[twelvedata] timeout")
            return await super().fetch_pagina(*a, **k)
    inicio = vi._epoch_de(vi.INICIO_TD)
    td = _Roto([inicio + k * M5 for k in range(12)], hora=inicio + 13 * M5)
    monkeypatch.setattr(vi.time, "time", lambda: inicio + 13 * M5)
    _, res = await vi.ingerir_td(td, write=True, limitador=_lim())
    assert res["corte"] == "error" and not ruta_serie("td_XAUUSD", M5).exists()


async def test_td_segunda_corrida_pide_desde_la_ultima_y_agrega(monkeypatch):
    inicio = vi._epoch_de(vi.INICIO_TD)
    epocas = [inicio + k * M5 for k in range(8)]
    monkeypatch.setattr(vi.time, "time", lambda: epocas[-1] + 10 * M5)
    await vi.ingerir_td(_TDFalso(epocas[:5], hora=epocas[4] + M5), write=True, limitador=_lim())
    td = _TDFalso(epocas, hora=epocas[-1] + M5)
    rep, res = await vi.ingerir_td(td, write=True, limitador=_lim())
    assert td.pedidos[0]["start_date"] == vi._fmt(epocas[5])
    assert rep.nuevas == 3 and res["completo"] is True
    assert leer_velas("td_XAUUSD", M5)["epoch"].to_list() == epocas


async def test_td_sin_nada_nuevo_es_completa_y_no_escribe_nada(monkeypatch):
    inicio = vi._epoch_de(vi.INICIO_TD)
    epocas = [inicio + k * M5 for k in range(4)]
    monkeypatch.setattr(vi.time, "time", lambda: epocas[-1] + 10 * M5)
    await vi.ingerir_td(_TDFalso(epocas, hora=epocas[-1] + M5), write=True, limitador=_lim())
    antes = ruta_serie("td_XAUUSD", M5).read_bytes()
    rep, res = await vi.ingerir_td(_TDFalso(epocas, hora=epocas[-1] + M5), write=True,
                                   limitador=_lim())
    assert res["completo"] is True and rep.nuevas == 0
    assert ruta_serie("td_XAUUSD", M5).read_bytes() == antes


async def test_td_sin_write_no_escribe(monkeypatch):
    inicio = vi._epoch_de(vi.INICIO_TD)
    epocas = [inicio + k * M5 for k in range(4)]
    monkeypatch.setattr(vi.time, "time", lambda: epocas[-1] + 10 * M5)
    rep, _ = await vi.ingerir_td(_TDFalso(epocas, hora=epocas[-1] + M5), write=False,
                                 limitador=_lim())
    assert rep.nuevas == 4 and not ruta_serie("td_XAUUSD", M5).exists()


def test_el_tope_por_minuto_es_el_de_la_3b():
    from tests.test_sonda3b_live import LLAMADAS_POR_MINUTO_3B
    assert vi.LLAMADAS_POR_MINUTO_TD == LLAMADAS_POR_MINUTO_3B == 7
    assert vi.INICIO_TD == "2020-03-16 00:00:00"


# ═══ Deriv ════════════════════════════════════════════════════════════════

_TT = {"trading_times": {"markets": [{"name": "Commodities", "submarkets": [
    {"name": "Metals", "symbols": [
        {"underlying_symbol": "frxXAGUSD", "trading_days": ["Mon"], "times": {}},
        {"underlying_symbol": "frxXAUUSD", "name": "Gold/USD",
         "trading_days": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri"],
         "times": {"open": ["00:00:00", "22:00:00"], "close": ["21:00:00", "23:59:59"],
                   "settlement": "23:59:59"},
         "events": [{"dates": "Fridays", "descrip": "Closes early (at 21:00)"}]}]}]}]}}


def _deriv(epocas, *, ahora, tt=_TT, error_en_pagina=None):
    paginas = [0]

    def th(p):
        paginas[0] += 1
        if error_en_pagina and paginas[0] == error_en_pagina:
            return {"error": {"code": "RateLimit", "message": "x"}}
        fin = ahora if p["end"] == "latest" else int(p["end"])
        ini = int(p.get("start", 0))
        sel = [e for e in epocas if ini <= e <= fin][-p["count"]:]
        return {"candles": [_vela(e) for e in sel]}
    return DerivFalso({"time": lambda p: {"time": ahora},
                       "trading_times": lambda p: tt, "ticks_history": th})


async def test_deriv_baja_escribe_y_registra_el_calendario(monkeypatch):
    epocas = [T0 + k * M5 for k in range(12)]
    d = _deriv(epocas, ahora=epocas[-1] + 100)        # la última, abierta
    abiertos = []

    def abrir(u):
        abiertos.append(u)
        return d.connector(u)
    rep, res = await vi.ingerir_deriv(abrir, write=True, pausa_s=0)
    assert abiertos == [ENDPOINT_PUBLICO]
    assert {next(iter(m)) for m in d.enviados} == {"time", "trading_times", "ticks_history"}
    assert d.de_tipo("trading_times")[0]["trading_times"] == "2026-10-05"
    assert rep.nuevas == 11 and rep.abiertas_descartadas == 1 and res["contigua"] is True
    assert leer_velas("frxXAUUSD", M5)["epoch"].to_list() == epocas[:-1]
    (linea,) = vi._leer_filas(vi.ruta_calendario())
    assert linea["horario"]["trading_days"][0] == "Sun"
    assert linea["horario"]["aperturas"] == ["00:00:00", "22:00:00"]
    assert linea["sha256"] == res["calendario"]["sha256"]
    import hashlib
    assert hashlib.sha256(linea["crudo"].encode()).hexdigest() == linea["sha256"]


async def test_deriv_cortada_la_primera_vez_no_escribe():
    epocas = [T0 + k * M5 for k in range(12)]
    d = _deriv(epocas, ahora=epocas[-1] + M5, error_en_pagina=2)
    rep, res = await vi.ingerir_deriv(d.connector, write=True, pausa_s=0)
    # la primera página trae todo (12 < 5000) y la segunda falla: no llegó al fondo
    assert res["contigua"] is False and "no_escrito" in res
    assert not ruta_serie("frxXAUUSD", M5).exists()


async def test_deriv_incremental_contigua_agrega():
    epocas = [T0 + k * M5 for k in range(12)]
    await vi.ingerir_deriv(_deriv(epocas[:6], ahora=epocas[5] + M5).connector, write=True,
                           pausa_s=0)
    rep, res = await vi.ingerir_deriv(_deriv(epocas, ahora=epocas[-1] + M5).connector,
                                      write=True, pausa_s=0)
    assert res["contigua"] is True and rep.nuevas == 6
    assert leer_velas("frxXAUUSD", M5)["epoch"].to_list() == epocas


async def test_deriv_incremental_con_hueco_no_escribe():
    epocas = [T0 + k * M5 for k in range(12)]
    await vi.ingerir_deriv(_deriv(epocas[:3], ahora=epocas[2] + M5).connector, write=True,
                           pausa_s=0)
    d = _deriv(epocas[6:], ahora=epocas[-1] + M5, error_en_pagina=2)
    rep, res = await vi.ingerir_deriv(d.connector, write=True, pausa_s=0)
    assert res["contigua"] is False
    assert leer_velas("frxXAUUSD", M5)["epoch"].to_list() == epocas[:3]


async def test_deriv_una_revision_no_sobrescribe():
    epocas = [T0 + k * M5 for k in range(4)]
    await vi.ingerir_deriv(_deriv(epocas, ahora=epocas[-1] + M5).connector, write=True,
                           pausa_s=0)
    d = _deriv(epocas, ahora=epocas[-1] + M5)
    d.manejadores["ticks_history"] = lambda p: {"candles": [
        {**_vela(e), "close": 2650.9} for e in epocas if e <= (
            epocas[-1] + M5 if p["end"] == "latest" else int(p["end"]))]}
    rep, _ = await vi.ingerir_deriv(d.connector, write=True, pausa_s=0)
    assert rep.revisiones_nuevas == 4
    assert leer_velas("frxXAUUSD", M5)["close"].to_list() == [2650.5] * 4
    assert len(vi._leer_filas(ruta_revisiones("frxXAUUSD", M5))) == 4


def test_horario_de():
    h = vi.horario_de(_TT)
    assert h["mercado"] == "Commodities" and h["submercado"] == "Metals"
    assert h["cierres"] == ["21:00:00", "23:59:59"] and h["events"][0]["descrip"].startswith("Closes")
    assert vi.horario_de(_TT, "frxXPTUSD") is None
    assert vi.horario_de({}) is None


# ═══ Canal público ════════════════════════════════════════════════════════

@pytest.mark.parametrize("payload", [
    {"proposal": 1}, {"buy": "1"}, {"contracts_for": "frxXAUUSD"}, {"authorize": "x"},
    {"ticks_history": "frxXAUUSD", "subscribe": 1}, {"time": 1, "passthrough": {}}, {}])
def test_la_lista_blanca_publica_rechaza(payload):
    with pytest.raises(MensajeNoPermitidoError):
        validar_publico(payload)


def test_la_lista_blanca_publica_son_cuatro_de_lectura():
    assert MENSAJES_PUBLICOS == {"time", "active_symbols", "trading_times", "ticks_history"}


async def test_el_canal_no_toca_el_socket_si_rechaza():
    d = DerivFalso({})
    canal = CanalPublico(await d.connector("wss://x").__aenter__())
    with pytest.raises(MensajeNoPermitidoError):
        await canal.pedir({"proposal": 1})
    assert d.enviados == []


async def test_el_canal_guarda_el_crudo_aparte_y_no_en_la_entrada():
    d = DerivFalso({"time": lambda p: {"time": 5}})
    canal = CanalPublico(await d.connector("wss://x").__aenter__())
    entrada, datos = await canal.pedir({"time": 1})
    assert datos["time"] == 5 and "crudo" not in entrada
    assert json.loads(canal.ultimo_crudo)["time"] == 5


# ═══ CLI e informe ════════════════════════════════════════════════════════

def test_sin_key_sale_2(monkeypatch, capsys):
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
    monkeypatch.setattr(vi, "load_secret", lambda *a, **k: None)
    assert vi.main([]) == 2


async def test_el_informe_trae_sha256_y_fechas(monkeypatch):
    inicio = vi._epoch_de(vi.INICIO_TD)
    epocas = [inicio + k * M5 for k in range(4)]
    monkeypatch.setattr(vi.time, "time", lambda: epocas[-1] + 10 * M5)
    dv = [T0 + k * M5 for k in range(3)]
    bloques = await vi.ingerir(key="K", write=True,
                               abrir=_deriv(dv, ahora=dv[-1] + M5).connector,
                               adapter=_TDFalso(epocas, hora=epocas[-1] + M5),
                               limitador=_lim())
    td, dr = bloques
    import hashlib
    assert td["sha256"] == hashlib.sha256(ruta_serie("td_XAUUSD", M5).read_bytes()).hexdigest()
    assert td["primera_fecha"] == "2020-03-16" and dr["primera_fecha"] == "2026-10-05"
    assert dr["calendario"]["horario"]["trading_days"][-1] == "Fri"
