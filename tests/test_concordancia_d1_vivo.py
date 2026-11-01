"""
tests/test_concordancia_d1_vivo.py
====================================
La compuerta de D1 con el paso 3, Deriv en vivo y el veredicto en data
(brief del Admin 16a, candado 3 y puntos 4 y 6). Offline: el Deriv falso
sirve las mismas velas que `_mundo()` dejó en archivos, y la corrida no
puede leerlas de ahí.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from ingestion.deriv_publico import CORTE_VENTANA
from ingestion.velas import leer_velas, ruta_serie
from ingestion.velas_d1 import ACTIVOS_D1
from ingestion.velas_intradia import CORTES_DE_FONDO, MAX_PAGINAS_DERIV, PAUSA_DERIV_S
from tests.deriv_falso import DerivFalso
from tests.test_concordancia_d1 import D, M5, _mundo
from tools import concordancia_d1 as cd
from tools import concordancia_d1_paso3 as p3
from tools.concordancia_xauusd import horario_vigente

#: Miércoles 8-jul-2026, 12:00 UTC: dos días después del último día de `_mundo()`.
AHORA = int(datetime(2026, 7, 8, 12, tzinfo=timezone.utc).timestamp())
WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "concordancia_d1.yml"


@pytest.fixture(autouse=True)
def _drive(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(tmp_path))
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def _deriv_de_lo_guardado(*, fallar: bool = False) -> DerivFalso:
    """ticks_history con las velas de Deriv que `_mundo()` guardó: las de
    [start, end], las `count` más recientes."""
    velas = {(s, g): {r["epoch"]: r for r in leer_velas(s, g).iter_rows(named=True)}
             for s, g in cd.series_de_deriv()}

    def th(p):
        fin = AHORA if p["end"] == "latest" else int(p["end"])
        serie = velas[(p["ticks_history"], p["granularity"])]
        sel = [e for e in sorted(serie) if (p.get("start") or 0) <= e <= fin][-p["count"]:]
        return {"candles": [serie[e] for e in sel]}

    def tiempo(p):
        return {"error": {"code": "RateLimit", "message": "más tarde"}} if fallar else {"time": AHORA}
    return DerivFalso({"time": tiempo, "ticks_history": th})


def _huellas(raiz: Path) -> dict[str, str]:
    return {str(p.relative_to(raiz)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((raiz / "metrics" / "velas").rglob("*")) if p.is_file()}


def test_en_vivo_guarda_el_informe_y_el_resumen_y_no_toca_las_velas(tmp_path, monkeypatch, capsys):
    _mundo()
    d = _deriv_de_lo_guardado()
    antes = _huellas(tmp_path)
    resumen = tmp_path / "resumen.md"
    monkeypatch.setenv("GITHUB_RUN_ID", "4242")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(resumen))
    assert cd.main(["--deriv-en-vivo", "--guardar"], abrir=d.connector) == 0
    ruta = tmp_path / "metrics" / "compuertas" / "concordancia_d1" / "2026-07-08_4242.json"
    inf = json.loads(ruta.read_text(encoding="utf-8"))
    assert inf["bloques"]["COMPUERTA"] == {"BTC": "verde", "XAU": "verde"}
    assert {"PASO 3 BTC", "PASO 3 XAU"} <= set(inf["bloques"])
    assert inf["atribucion"] == "Data provided by Twelve Data" and len(inf["archivos_twelvedata"]) == 3
    dr = inf["bloques"]["DATOS"]["BTC"]["deriv_86400"]
    assert dr["fuente"] == "Deriv en vivo, sin guardar" and "archivo" not in dr and "sha256" not in dr
    for b in ("PASO 2 BTC", "PASO 2 XAU", "SENSIBILIDAD XAU"):
        assert inf["bloques"][b]["informativo"]["revisiones_deriv"] == (
            "no medidas: Deriv llega en vivo y no se guarda"), "no cuenta cero sin haber mirado"
    assert resumen.read_text(encoding="utf-8") == cd.resumen_markdown(inf)
    assert _huellas(tmp_path) == antes, "las velas guardadas no cambian, ni aparecen otras"
    assert {next(iter(p)) for p in d.enviados} == {"time", "ticks_history"}
    assert "Informe: metrics/compuertas/concordancia_d1/2026-07-08_4242.json" in capsys.readouterr().out


def test_en_vivo_no_lee_las_velas_guardadas_de_deriv(monkeypatch):
    _mundo()
    d = _deriv_de_lo_guardado()
    leer, leidas = cd.leer_velas, []
    cierres = cd.cierres

    def espia_leer(serie, g):
        leidas.append(serie)
        return leer(serie, g)

    def espia_cierres(serie, g):
        leidas.append(serie)
        return cierres(serie, g)
    monkeypatch.setattr(cd, "leer_velas", espia_leer)
    monkeypatch.setattr(cd, "cierres", espia_cierres)
    assert cd.main(["--deriv-en-vivo"], abrir=d.connector) == 0
    assert "td_BTCUSD" in leidas, "la espía ve lo que se lee"
    assert not {"cryBTCUSD", "frxXAUUSD"} & set(leidas)


def test_si_falta_un_archivo_de_twelvedata_se_detiene_sin_pedir_ni_escribir(tmp_path, capsys):
    _mundo()
    ruta_serie("td_XAUUSD", M5).unlink()
    d = _deriv_de_lo_guardado()
    assert cd.main(["--deriv-en-vivo", "--guardar"], abrir=d.connector) == 2
    salida = capsys.readouterr().out
    assert salida.startswith("DETENIDO: faltan archivos de TwelveData")
    assert "metrics/velas/td_XAUUSD/300.jsonl" in salida
    assert d.conexiones == 0 and not (tmp_path / "metrics" / "compuertas").exists()


def test_sin_guardar_no_escribe_el_informe(tmp_path):
    _mundo()
    assert cd.main(["--deriv-en-vivo"], abrir=_deriv_de_lo_guardado().connector) == 0
    assert not (tmp_path / "metrics" / "compuertas").exists()


def test_un_paso_3_rojo_deja_roja_la_compuerta_aunque_el_paso_2_sea_verde(monkeypatch):
    _mundo()
    paso_3 = p3.paso_3

    def btc_rojo(*a, **k):
        r = paso_3(*a, **k)
        return {**r, "paso_3": "roja"} if a[-1] == "continuo" else r
    monkeypatch.setattr(p3, "paso_3", btc_rojo)
    bloques = dict(cd.informe())
    assert bloques["PASO 2 BTC"]["compuerta"] == "verde"
    assert bloques["COMPUERTA"] == {"BTC": "roja", "XAU": "verde"}


def test_si_deriv_no_responde_los_activos_se_detienen_sin_compuerta(monkeypatch):
    _mundo()
    d = _deriv_de_lo_guardado(fallar=True)
    vivo = __import__("asyncio").run(cd.velas_en_vivo(d.connector, cd.series_de_deriv()))
    bloques = dict(cd.informe(vivo))
    assert bloques["COMPUERTA"] == {"BTC": "detenido", "XAU": "detenido"}
    assert bloques["DETENIDO BTC"]["motivos"][0].startswith("Deriv no respondió")


def test_una_serie_de_deriv_incompleta_detiene_su_activo():
    _mundo()
    d = _deriv_de_lo_guardado()
    vivo = __import__("asyncio").run(cd.velas_en_vivo(d.connector, cd.series_de_deriv()))
    vivo["series"][("frxXAUUSD", M5)]["corte"] = "tope de 300 páginas: la historia puede seguir"
    bloques = dict(cd.informe(vivo))
    assert bloques["COMPUERTA"] == {"BTC": "verde", "XAU": "detenido"}
    assert "quedó incompleta" in bloques["DETENIDO XAU"]["motivos"][0]


def test_el_workflow_commitea_solo_el_informe_y_tambien_con_la_ingesta_apagada():
    pasos = yaml.safe_load(WF.read_text(encoding="utf-8"))["jobs"]["concordancia_d1"]["steps"]
    nombres = [s.get("name") for s in pasos]
    conc = pasos[nombres.index("Concordancia diaria D1")]
    commit = pasos[nombres.index("Commit del informe a data")]
    assert conc["run"] == "python tools/concordancia_d1.py --deriv-en-vivo --guardar"
    assert nombres.index("Commit del informe a data") == nombres.index("Concordancia diaria D1") + 1
    assert "git add metrics/compuertas/concordancia_d1\n" in commit["run"]
    assert "metrics/velas" not in commit["run"] and "add -A" not in commit["run"]
    assert "if" not in commit and commit["working-directory"] == "data-store"


@pytest.mark.parametrize("corte, entera", [
    *[(c, True) for c in sorted(CORTES_DE_FONDO)],
    (CORTE_VENTANA, True),
    ("error", False),
    ("tope de 300 páginas: la historia puede seguir", False),
    (None, False),                                   # la serie no llegó
])
def test_una_serie_en_vivo_esta_entera_solo_si_llego_al_fondo_o_a_la_ventana(corte, entera):
    vivo = {"series": {sg: {"corte": CORTE_VENTANA} for sg in cd.series_de_deriv()}}
    a = next(x for x in ACTIVOS_D1 if x.activo == "XAU")
    if corte is None:
        del vivo["series"][(a.deriv, M5)]
    else:
        vivo["series"][(a.deriv, M5)]["corte"] = corte
    motivos = cd._deriv_incompleto(a, vivo)
    assert (motivos == []) is entera
    assert cd._deriv_incompleto(next(x for x in ACTIVOS_D1 if x.activo == "BTC"), vivo) == []


def test_el_rango_del_oro_usa_la_misma_franja_que_su_cierre(monkeypatch):
    _mundo()
    vistos, rangos_desde_m5 = [], p3.rangos_desde_m5

    def espia(rangos_m5, horario=None):
        vistos.append(horario)
        return rangos_desde_m5(rangos_m5, horario)
    monkeypatch.setattr(p3, "rangos_desde_m5", espia)
    cd.informe()
    assert vistos == [horario_vigente()["horario"]] and vistos[0]


def test_en_vivo_pide_las_series_de_la_compuerta_con_la_pausa_y_el_tope_de_siempre(monkeypatch):
    _mundo()
    d, pedidos = _deriv_de_lo_guardado(), []
    velas_en_vivo = cd.velas_en_vivo

    async def espia(abrir, series, **k):
        pedidos.append((abrir, list(series), k))
        return await velas_en_vivo(abrir, series, **k)
    monkeypatch.setattr(cd, "velas_en_vivo", espia)
    monkeypatch.setattr(cd.DerivAdapter, "_default_connector", d.connector)
    assert cd.main(["--deriv-en-vivo"]) == 0
    assert pedidos == [(d.connector, [("cryBTCUSD", D), ("frxXAUUSD", D), ("frxXAUUSD", M5)],
                        {"pausa_s": PAUSA_DERIV_S, "max_paginas": MAX_PAGINAS_DERIV})]
    assert d.conexiones == 1


def test_el_paso_3_usa_los_mismos_cierres_que_el_paso_2(monkeypatch):
    """Los del oro, de la franja de Deriv (opción 2), no los de la
    sensibilidad de las 24:00."""
    _mundo()
    vistos, paso_3 = [], p3.paso_3

    def espia(*a, **k):
        vistos.append(a[0])
        return paso_3(*a, **k)
    monkeypatch.setattr(p3, "paso_3", espia)
    cd.informe()
    m5 = cd.cierres("td_XAUUSD", M5)
    con_franja = cd.diarias_desde_m5(m5, M5, horario_vigente()["horario"])
    assert con_franja != cd.diarias_desde_m5(m5, M5), "el mundo separa las dos reglas"
    assert vistos == [cd.cierres("td_BTCUSD", D), con_franja]
