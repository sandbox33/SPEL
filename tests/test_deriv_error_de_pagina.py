"""
tests/test_deriv_error_de_pagina.py
=====================================
Brief del Admin 16a-fix, punto 1: cuando una página de Deriv corta en
"error", el informe dice cuál fue (code y message) y qué página era (end y
start). Sin cambiar reglas. Offline, con un Deriv falso que responde error en
la página N.
"""

from __future__ import annotations

import asyncio

import pytest

from ingestion.deriv_publico import error_de_pagina, velas_en_vivo
from ingestion.velas import leer_velas
from tests.deriv_falso import DerivFalso
from tests.test_concordancia_d1 import M5, _mundo
from tests.test_concordancia_d1_vivo import AHORA
from tools import concordancia_d1 as cd

D = 86400
ERROR = {"code": "RateLimit", "message": "Has superado el límite de pedidos."}


@pytest.fixture(autouse=True)
def _drive(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(tmp_path))


def _vela(e: int) -> dict:
    return {"epoch": e, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}


def _deriv_que_falla(m5: list[int], *, en_pagina: int, error=ERROR) -> DerivFalso:
    """ticks_history de un M5 continuo que, en la página `en_pagina` del
    M5, contesta `error`."""
    paginas = {"n": 0}

    def th(p):
        if p["granularity"] != M5:
            return {"candles": [_vela(e) for e in range(AHORA - 30 * D - AHORA % D, AHORA - D, D)]}
        paginas["n"] += 1
        if paginas["n"] == en_pagina:
            return {"error": error}
        fin = AHORA if p["end"] == "latest" else int(p["end"])
        sel = [e for e in m5 if (p.get("start") or 0) <= e <= fin][-p["count"]:]
        return {"candles": [_vela(e) for e in sel]}
    return DerivFalso({"time": lambda p: {"time": AHORA}, "ticks_history": th})


def _m5(dias: int) -> list[int]:
    return list(range(AHORA - dias * D - AHORA % M5, AHORA - M5, M5))


def test_la_serie_lleva_el_error_y_la_pagina_que_fallo():
    d = _deriv_que_falla(_m5(60), en_pagina=3)
    vivo = asyncio.run(velas_en_vivo(d.connector, [("frxXAUUSD", D), ("frxXAUUSD", M5)]))
    s = vivo["series"][("frxXAUUSD", M5)]
    assert s["corte"] == "error" and s["paginas"] == 3
    assert s["error_pagina"] == ERROR
    pedido = [p for p in d.de_tipo("ticks_history") if p["granularity"] == M5][2]
    assert s["pagina_fallida"] == {"numero": 3, "end": pedido["end"], "start": pedido.get("start")}
    assert vivo["series"][("frxXAUUSD", D)]["error_pagina"] is None
    assert vivo["series"][("frxXAUUSD", D)]["pagina_fallida"] is None


def test_un_error_del_canal_sin_code_queda_como_message():
    assert error_de_pagina("TimeoutError: ") == {"code": None, "message": "TimeoutError: "}
    assert error_de_pagina({"code": "X", "message": "y", "otro": 1}) == {"code": "X", "message": "y"}
    assert error_de_pagina(None) == {"code": None, "message": None}


def test_el_informe_publica_el_error_en_datos_y_en_el_motivo_de_detenido():
    """El M5 del oro de `_mundo()` cabe en una página, y esa falla."""
    _mundo()
    m5 = sorted(leer_velas("frxXAUUSD", M5)["epoch"].to_list())
    d = _deriv_que_falla(m5, en_pagina=1)
    vivo = asyncio.run(velas_en_vivo(d.connector, cd.series_de_deriv()))
    bloques = dict(cd.informe(vivo))
    datos = bloques["DATOS"]["XAU"]["deriv_m5"]
    assert datos["error_pagina"] == ERROR
    assert datos["pagina_fallida"] == {"numero": 1, "end": "latest", "start": None}
    assert bloques["DATOS"]["XAU"]["deriv_86400"]["error_pagina"] is None
    motivo = bloques["DETENIDO XAU"]["motivos"][0]
    assert "quedó incompleta: 'error'" in motivo
    assert "'RateLimit'" in motivo and "'Has superado el límite de pedidos.'" in motivo
    assert bloques["COMPUERTA"]["XAU"] == "detenido"
