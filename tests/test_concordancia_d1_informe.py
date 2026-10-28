"""
tests/test_concordancia_d1_informe.py
=======================================
El veredicto de la compuerta en data (brief del Admin 16a, puntos 4 y 6):
qué archivos de TwelveData lee y cuáles faltan, el informe con su sha256 y
la atribución, append-only, y el resumen para $GITHUB_STEP_SUMMARY. Offline,
en un metrics/ temporal.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from ingestion.velas import _append, ruta_serie
from tools import concordancia_d1 as cd

D, H, M5 = 86400, 3600, 300
TD = ("metrics/velas/td_BTCUSD/86400.jsonl", "metrics/velas/td_BTCUSD/3600.jsonl",
      "metrics/velas/td_XAUUSD/300.jsonl")


@pytest.fixture(autouse=True)
def _drive(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(tmp_path))


def _escribir(serie: str, g: int) -> None:
    _append(ruta_serie(serie, g), [{"epoch": 0, "open": 1.0, "high": 1.0, "low": 1.0,
                                    "close": 1.0}])


def test_la_compuerta_lee_la_1day_y_la_1h_de_btc_y_el_m5_del_oro():
    """La 1day del oro no está: D1-x."""
    assert cd.archivos_twelvedata() == [("td_BTCUSD", D), ("td_BTCUSD", H), ("td_XAUUSD", M5)]


def test_dice_que_archivos_de_twelvedata_faltan():
    assert cd.faltan_archivos_twelvedata() == list(TD)
    _escribir("td_BTCUSD", D)
    _escribir("td_XAUUSD", D)                        # la 1day del oro no cuenta
    assert cd.faltan_archivos_twelvedata() == list(TD[1:])
    _escribir("td_BTCUSD", H)
    _escribir("td_XAUUSD", M5)
    assert cd.faltan_archivos_twelvedata() == []


def test_el_informe_lleva_el_sha256_de_cada_archivo_y_la_atribucion(tmp_path):
    for s, g in cd.archivos_twelvedata():
        _escribir(s, g)
    bloques = [("PASO 1 BTC", {"decision": "sigue"}), ("COMPUERTA", {"BTC": "verde"})]
    inf = cd.informe_para_data(bloques, fecha="2026-10-30", run_id="123")
    assert list(inf) == ["fecha", "run_id", "atribucion", "archivos_twelvedata", "bloques"]
    assert inf["atribucion"] == "Data provided by Twelve Data"
    assert list(inf["archivos_twelvedata"]) == list(TD)
    for rel, sha in inf["archivos_twelvedata"].items():
        assert sha == hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    assert inf["bloques"] == dict(bloques)


def test_un_archivo_que_falta_queda_sin_sha256():
    inf = cd.informe_para_data([], fecha="2026-10-30", run_id="1")
    assert set(inf["archivos_twelvedata"].values()) == {None}


def test_el_informe_va_a_compuertas_y_no_se_reescribe(tmp_path):
    inf = cd.informe_para_data([("COMPUERTA", {"BTC": "verde"})], fecha="2026-10-30", run_id="77")
    ruta = cd.guardar_informe(inf)
    assert ruta == tmp_path / "metrics" / "compuertas" / "concordancia_d1" / "2026-10-30_77.json"
    assert json.loads(ruta.read_text(encoding="utf-8")) == inf
    antes = ruta.read_bytes()
    with pytest.raises(FileExistsError, match="no se reescribe"):
        cd.guardar_informe({**inf, "bloques": {"COMPUERTA": {"BTC": "roja"}}})
    assert ruta.read_bytes() == antes
    otra = cd.guardar_informe({**inf, "run_id": "78"})
    assert otra.name == "2026-10-30_78.json" and ruta.exists()
    assert not (tmp_path / "metrics" / "velas").exists(), "no escribe velas"


def test_el_resumen_dice_la_compuerta_los_archivos_y_la_atribucion():
    for s, g in cd.archivos_twelvedata():
        _escribir(s, g)
    inf = cd.informe_para_data([("COMPUERTA", {"BTC": "verde", "XAU": "roja"})],
                               fecha="2026-10-30", run_id="9")
    md = cd.resumen_markdown(inf)
    assert md.startswith("## Concordancia diaria D1 — 2026-10-30, corrida 9")
    assert "| BTC | verde |" in md and "| XAU | roja |" in md
    for rel, sha in inf["archivos_twelvedata"].items():
        assert f"| `{rel}` | `{sha}` |" in md
    assert "\nData provided by Twelve Data\n" in md
    assert json.dumps(inf, ensure_ascii=False, indent=1) in md, "el mismo informe, entero"
