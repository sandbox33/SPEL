"""
tests/test_twelvedata_timezone.py
===================================
La regla de zona horaria de TwelveData (decision-log 2026-10-06): toda
llamada INTRADÍA lleva `timezone=UTC`; 1d no la lleva, porque es una barra
de solo fecha. La sonda §0.A-3 v2 mostró por qué importa: sin la zona, el
intradía de XAU/USD llegó con unas 11 horas de corrimiento.

`tests/test_twelvedata_adapter.py` ya lo fija para 1d y 1h. Esto lo fija
para CADA timeframe que el adapter soporta, incluidos 5m y 15m, que son los
de la candidata intradía: sin esto, un 15m sin zona pasaba. No se editó el
test existente.
"""

from __future__ import annotations

import httpx
import pytest

from ingestion.adapters import TwelveDataAdapter
from tests.test_twelvedata_adapter import RESPUESTA_BTCUSD_1H_SINTETICA, adapter_con


async def _params_enviados(timeframe: str) -> dict:
    captura: list[httpx.Request] = []
    try:
        await adapter_con(RESPUESTA_BTCUSD_1H_SINTETICA, captura=captura).fetch_ohlcv(
            "BTCUSD", timeframe, 1)
    except Exception:   # noqa: BLE001 -- la respuesta es de 1h; lo que se mira es el pedido
        pass
    assert len(captura) == 1
    return dict(captura[0].url.params)


@pytest.mark.parametrize("timeframe", sorted(TwelveDataAdapter.SUPPORTED_TIMEFRAMES - {"1d"}))
async def test_todo_timeframe_intradia_manda_timezone_utc(timeframe):
    assert (await _params_enviados(timeframe)).get("timezone") == "UTC"


async def test_1d_no_manda_timezone():
    assert "timezone" not in await _params_enviados("1d")


def test_los_timeframes_cubren_los_de_la_candidata_intradia():
    assert {"5m", "15m"} <= TwelveDataAdapter.SUPPORTED_TIMEFRAMES
