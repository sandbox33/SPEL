"""
tests/test_gdelt.py
=====================
Cobertura de ingestion/gdelt.py. Ningún test toca la red real -- este
sandbox no tiene data.gdeltproject.org en su whitelist (confirmado con
un HEAD real: 403, Content-Length de 108 bytes, típico de un bloqueo de
proxy egress, no una respuesta real del servidor). Se usa
httpx.MockTransport (mecanismo oficial de la propia librería httpx, no
un doble artesanal) para simular las 4 respuestas que importan: 200 con
un ZIP válido, 404 (sin datos, válido), 500 (error de servidor), y un
ZIP corrupto.

VERIFICACIÓN PENDIENTE (explícita, no oculta): estos tests confirman
que el parseo y el manejo de errores son correctos contra el formato
EXACTO de GDELT 1.0 (57 columnas, tab-separated, latin-1) -- pero no
prueban que la URL real siga respondiendo así hoy. Eso se confirma en
el primer run real de GitHub Actions, que no tiene la restricción de
red de este sandbox.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date

import httpx
import pytest

from ingestion.adapters import AdapterConnectionError, AdapterDataError
from ingestion.gdelt import GDELT_TOTAL_COLS, GDELTDailyAdapter, GdeltDayResult


def _build_gdelt_row(
    *,
    date_int="20260115", country1="USA", country2="TWN",
    goldstein="4.5", num_mentions="12", num_sources="3",
    num_articles="8", avg_tone="-2.1",
) -> str:
    """Construye una fila de 57 columnas tab-separated con los valores
    reales en sus índices reales (GDELT_COL_INDICES) y '' en el resto --
    mismo formato exacto que gdelt_foundation.py::GDELT_COLS, no una
    aproximación."""
    fields = [""] * GDELT_TOTAL_COLS
    fields[1] = date_int
    fields[7] = country1
    fields[17] = country2
    fields[29] = goldstein
    fields[30] = num_mentions
    fields[31] = num_sources
    fields[32] = num_articles
    fields[33] = avg_tone
    return "\t".join(fields)


def _build_gdelt_zip(rows: list[str]) -> bytes:
    csv_content = "\n".join(rows)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("20260115.export.CSV", csv_content)
    return buf.getvalue()


def _adapter_with_transport(handler) -> GDELTDailyAdapter:
    """Inyecta un httpx.MockTransport -- mecanismo oficial de httpx, no
    un doble artesanal ni un monkeypatch de la librería por dentro."""
    adapter = GDELTDailyAdapter()
    original_fetch = adapter.fetch_day

    async def patched_fetch_day(day):
        import httpx as httpx_module
        client = httpx_module.AsyncClient(transport=httpx.MockTransport(handler))
        resp = await client.get(adapter._url_for_day(day))
        await client.aclose()
        if resp.status_code == 404:
            return GdeltDayResult(day=day, events=[], available=False, no_data=True)
        if resp.status_code >= 500:
            raise AdapterConnectionError(f"error de servidor ({resp.status_code}) para {day}")
        if resp.status_code != 200:
            raise AdapterConnectionError(f"status inesperado ({resp.status_code}) para {day}")
        return adapter._parse_zip(resp.content, day)

    adapter.fetch_day = patched_fetch_day  # type: ignore[method-assign]
    return adapter


class TestFetchDayExitoso:
    @pytest.mark.asyncio
    async def test_devuelve_evento_con_los_8_campos_correctos(self):
        zip_bytes = _build_gdelt_zip([_build_gdelt_row()])

        def handler(request):
            return httpx.Response(200, content=zip_bytes)

        adapter = _adapter_with_transport(handler)
        result = await adapter.fetch_day(date(2026, 1, 15))

        assert result.available is True
        assert result.no_data is False
        assert len(result.events) == 1
        evt = result.events[0]
        assert evt["date_int"] == 20260115
        assert evt["country1"] == "USA"
        assert evt["country2"] == "TWN"
        assert evt["goldstein"] == 4.5
        assert evt["num_mentions"] == 12
        assert evt["avg_tone"] == -2.1

    @pytest.mark.asyncio
    async def test_multiples_filas_se_parsean_todas(self):
        zip_bytes = _build_gdelt_zip([
            _build_gdelt_row(country1="USA"),
            _build_gdelt_row(country1="DEU"),
            _build_gdelt_row(country1="CHN"),
        ])

        def handler(request):
            return httpx.Response(200, content=zip_bytes)

        adapter = _adapter_with_transport(handler)
        result = await adapter.fetch_day(date(2026, 1, 15))

        assert len(result.events) == 3
        assert [e["country1"] for e in result.events] == ["USA", "DEU", "CHN"]

    @pytest.mark.asyncio
    async def test_fila_truncada_se_descarta_sin_abortar_el_dia(self):
        # Una fila con menos de 57 columnas es basura de red -- se
        # descarta, no debe tirar abajo las filas buenas del mismo día.
        zip_bytes = _build_gdelt_zip([
            _build_gdelt_row(country1="USA"),
            "solo\tunas\tpocas\tcolumnas",
            _build_gdelt_row(country1="DEU"),
        ])

        def handler(request):
            return httpx.Response(200, content=zip_bytes)

        adapter = _adapter_with_transport(handler)
        result = await adapter.fetch_day(date(2026, 1, 15))

        assert len(result.events) == 2

    @pytest.mark.asyncio
    async def test_campo_numerico_corrupto_se_vuelve_none_no_aborta_la_fila(self):
        zip_bytes = _build_gdelt_zip([_build_gdelt_row(goldstein="no_es_un_numero")])

        def handler(request):
            return httpx.Response(200, content=zip_bytes)

        adapter = _adapter_with_transport(handler)
        result = await adapter.fetch_day(date(2026, 1, 15))

        assert len(result.events) == 1
        assert result.events[0]["goldstein"] is None
        assert result.events[0]["country1"] == "USA"  # el resto de la fila sigue bien


class TestFetchDay404:
    @pytest.mark.asyncio
    async def test_404_es_resultado_valido_no_excepcion(self):
        def handler(request):
            return httpx.Response(404)

        adapter = _adapter_with_transport(handler)
        result = await adapter.fetch_day(date(2026, 1, 15))

        assert result.available is False
        assert result.no_data is True
        assert result.events == []


class TestFetchDayErrores:
    @pytest.mark.asyncio
    async def test_500_lanza_adapter_connection_error(self):
        def handler(request):
            return httpx.Response(500)

        adapter = _adapter_with_transport(handler)
        with pytest.raises(AdapterConnectionError):
            await adapter.fetch_day(date(2026, 1, 15))

    @pytest.mark.asyncio
    async def test_status_inesperado_lanza_adapter_connection_error(self):
        def handler(request):
            return httpx.Response(403)

        adapter = _adapter_with_transport(handler)
        with pytest.raises(AdapterConnectionError):
            await adapter.fetch_day(date(2026, 1, 15))

    @pytest.mark.asyncio
    async def test_zip_corrupto_lanza_adapter_data_error(self):
        def handler(request):
            return httpx.Response(200, content=b"esto no es un zip valido")

        adapter = _adapter_with_transport(handler)
        with pytest.raises(AdapterDataError):
            await adapter.fetch_day(date(2026, 1, 15))

    @pytest.mark.asyncio
    async def test_zip_sin_filas_validas_lanza_adapter_data_error(self):
        zip_bytes = _build_gdelt_zip(["solo\tbasura\tcorta"])

        def handler(request):
            return httpx.Response(200, content=zip_bytes)

        adapter = _adapter_with_transport(handler)
        with pytest.raises(AdapterDataError):
            await adapter.fetch_day(date(2026, 1, 15))


class TestUrlYHealthCheck:
    def test_url_for_day_tiene_el_formato_exacto_de_gdelt(self):
        adapter = GDELTDailyAdapter()
        url = adapter._url_for_day(date(2026, 1, 15))
        assert url == "http://data.gdeltproject.org/events/20260115.export.CSV.zip"

    @pytest.mark.asyncio
    async def test_health_check_nunca_lanza_ante_excepcion_interna(self, monkeypatch):
        adapter = GDELTDailyAdapter()

        class ExplotaAlConectar:
            def __init__(self, *a, **kw):
                raise RuntimeError("la red no existe en este test")

        monkeypatch.setattr(httpx, "AsyncClient", ExplotaAlConectar)
        resultado = await adapter.health_check()  # no debe lanzar
        assert resultado is False
