"""
tests/test_deriv_en_vivo.py
=============================
ingestion/deriv_publico.py: `profundidad` con ventana y `velas_en_vivo`
(brief del Admin 16a, punto 6). Offline, con el Deriv falso de siempre: un
ticks_history que respeta `start`, `end` y `count`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ingestion.adapters import DERIV_MAX_COUNT
from ingestion.deriv_publico import (
    CORTE_VENTANA,
    ENDPOINT_PUBLICO,
    VENTANA_EN_VIVO_DIAS,
    CanalPublico,
    profundidad,
    velas_en_vivo,
)
from tests.deriv_falso import DerivFalso

D, M5 = 86400, 300
#: Miércoles 28-oct-2026, 12:00 UTC.
AHORA = int(datetime(2026, 10, 28, 12, tzinfo=timezone.utc).timestamp())
HOY = AHORA - AHORA % D


@pytest.fixture(autouse=True)
def _drive(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(tmp_path))


def _vela(e: int) -> dict:
    return {"epoch": e, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5}


def _deriv(historias: dict, *, ahora: int = AHORA, fallar_time: bool = False) -> DerivFalso:
    """ticks_history por (símbolo, granularidad): las velas de [start, end],
    las `count` más recientes; `latest` es la hora del servidor."""
    def th(p):
        fin = ahora if p["end"] == "latest" else int(p["end"])
        sel = [e for e in historias.get((p["ticks_history"], p["granularity"]), [])
               if (p.get("start") or 0) <= e <= fin][-p["count"]:]
        return {"candles": [historias.get("velas", {}).get(e, _vela(e)) for e in sel]}

    def tiempo(p):
        if fallar_time:
            return {"error": {"code": "RateLimit", "message": "más tarde"}}
        return {"time": ahora}
    return DerivFalso({"time": tiempo, "ticks_history": th})


def _diarias(dias: int) -> list[int]:
    """Las diarias hasta hoy inclusive: la de hoy todavía no cerró."""
    return [HOY - k * D for k in range(dias)][::-1]


# ═══ profundidad con ventana ══════════════════════════════════════════════

async def test_la_ventana_corta_la_paginacion_y_ningun_start_es_anterior():
    """M5 de 40 días; ventana de 30, más que una página de 5000 velas (17,4
    días): se pagina hasta el inicio, y no antes."""
    m5 = list(range(HOY - 40 * D, AHORA, M5))
    d = _deriv({("frxXAUUSD", M5): m5})
    desde = AHORA - 30 * D
    velas: dict = {}
    async with d.connector("wss://x") as ws:
        p = await profundidad(CanalPublico(ws), "frxXAUUSD", M5, guardar_velas=velas, desde=desde)
    assert p["corte"] == CORTE_VENTANA
    pedidos = d.de_tipo("ticks_history")
    assert len(pedidos) >= 2 and "start" not in pedidos[0]
    assert all(q["start"] >= desde for q in pedidos[1:]) and pedidos[-1]["start"] == desde
    assert all(q["count"] == DERIV_MAX_COUNT for q in pedidos)
    assert min(velas) == desde, "la última página llega hasta el inicio"


async def test_una_pagina_que_empieza_justo_despues_del_inicio_no_corta():
    """Si la primera vela de una página es la de un segundo después del inicio
    (una vela parcial, desalineada), falta pedir la que abre justo en el
    inicio: el corte es por `end` anterior al inicio, no igual."""
    desde = AHORA - 2 * D

    def th(p):
        if p["end"] == "latest":
            return {"candles": [_vela(desde + 1)] + [_vela(e) for e in range(desde + M5, AHORA, M5)]}
        return {"candles": [_vela(desde)] if int(p["end"]) >= desde >= p.get("start", 0) else []}
    d = DerivFalso({"ticks_history": th})
    velas: dict = {}
    async with d.connector("wss://x") as ws:
        await profundidad(CanalPublico(ws), "frxXAUUSD", M5, guardar_velas=velas, desde=desde)
    assert min(velas) == desde


async def test_sin_ventana_profundidad_no_cambia():
    m5 = list(range(HOY - 40 * D, AHORA, M5))
    d = _deriv({("frxXAUUSD", M5): m5})
    async with d.connector("wss://x") as ws:
        p = await profundidad(CanalPublico(ws), "frxXAUUSD", M5)
    assert p["corte"] == "respuesta vacía" and p["primera_epoch"] == m5[0]


# ═══ velas_en_vivo ════════════════════════════════════════════════════════

async def test_pide_la_hora_y_cada_serie_en_una_sola_conexion():
    d = _deriv({("cryBTCUSD", D): _diarias(30), ("frxXAUUSD", D): _diarias(30)})
    r = await velas_en_vivo(d.connector, [("cryBTCUSD", D), ("frxXAUUSD", D)])
    assert d.conexiones == 1 and d.uri == ENDPOINT_PUBLICO
    assert [next(iter(p)) for p in d.enviados][0] == "time"
    assert {next(iter(p)) for p in d.enviados} == {"time", "ticks_history"}
    assert r["hora_servidor"] == AHORA and r["ventana_dias"] == VENTANA_EN_VIVO_DIAS
    assert list(r["series"]) == [("cryBTCUSD", D), ("frxXAUUSD", D)]


async def test_solo_cerradas_y_validas():
    """La de hoy no cerró; una vela incoherente queda fuera y se cuenta."""
    diarias = _diarias(10)
    mala = {**_vela(diarias[3]), "high": 1.0}
    d = _deriv({("cryBTCUSD", D): diarias, "velas": {diarias[3]: mala}})
    s = (await velas_en_vivo(d.connector, [("cryBTCUSD", D)]))["series"][("cryBTCUSD", D)]
    assert sorted(s["velas"]) == diarias[:3] + diarias[4:9]
    assert (s["abiertas_descartadas"], s["n_invalidas"]) == (1, 1)
    assert s["velas"][diarias[0]] == _vela(diarias[0])


async def test_la_ventana_es_de_365_dias_segun_la_hora_del_servidor():
    """Una historia de 400 días: lo anterior a la ventana no entra. La
    ventana empieza hace 365 días a las 12:00, así que la diaria de ese día
    queda fuera, y la de hoy no cerró: 35 antes, 364 cerradas y 1 abierta."""
    d = _deriv({("cryBTCUSD", D): _diarias(400)})
    r = await velas_en_vivo(d.connector, [("cryBTCUSD", D)])
    s = r["series"][("cryBTCUSD", D)]
    assert r["desde"] == AHORA - 365 * D
    assert min(s["velas"]) == HOY - 364 * D and len(s["velas"]) == 364
    assert (s["antes_de_la_ventana"], s["abiertas_descartadas"]) == (35, 1)
    assert s["corte"] == CORTE_VENTANA


async def test_una_ventana_mas_corta_pagina_el_m5():
    m5 = list(range(HOY - 4 * D, AHORA, M5))
    d = _deriv({("frxXAUUSD", M5): m5})
    r = await velas_en_vivo(d.connector, [("frxXAUUSD", M5)], ventana_dias=2)
    s = r["series"][("frxXAUUSD", M5)]
    assert min(s["velas"]) == AHORA - 2 * D, "la vela que abre justo en el inicio entra"
    assert max(s["velas"]) == AHORA - M5, "la que termina justo ahora está cerrada"
    assert s["paginas"] == len(s["sha256_paginas"]) >= 1


async def test_un_error_de_la_hora_no_pide_nada_mas():
    d = _deriv({("cryBTCUSD", D): _diarias(10)}, fallar_time=True)
    r = await velas_en_vivo(d.connector, [("cryBTCUSD", D)])
    assert r["error"]["code"] == "RateLimit" and r["series"] == {}
    assert d.de_tipo("ticks_history") == []


async def test_no_escribe_nada(tmp_path):
    d = _deriv({("cryBTCUSD", D): _diarias(30), ("frxXAUUSD", M5): list(range(HOY - D, AHORA, M5))})
    await velas_en_vivo(d.connector, [("cryBTCUSD", D), ("frxXAUUSD", M5)])
    assert list(tmp_path.rglob("*")) == []
