"""
tests/test_ensayos.py
=======================
El registro único de ensayos (D3; brief del Admin 10, punto 3):
governance/ensayos.py y governance/ensayos.jsonl.

El criterio viejo, que estos tests separan del nuevo: ENSAYOS_PREVIOS_FALLIDOS
era un 3 escrito a mano en core/preregistro_h1.py. Ahora se deriva del
registro, y el registro es la única fuente del N.

Y desde P3 (brief del Admin 11–13-B): todo lo que contaba sumaba al N del
DSR. Ahora suma solo lo que además es una estrategia; los ensayos de H2
cuentan en el N de su familia.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

import core.preregistro_h1 as pre
from governance import ensayos as en

RAIZ = Path(__file__).resolve().parent.parent

#: El sha256 de las 4 líneas sembradas el 17-oct-2026. El registro es
#: append-only: lo que se agregue va DESPUÉS y este prefijo no cambia.
SEMBRADO_SHA256 = "f1da972256c572fbb552f655f41778e587d4b52f3d98ad3c770c72c1a6201878"
N_SEMBRADAS = 4


def _entrada(**cambios):
    base = {"id": "nuevo", "hipotesis": "H1", "fecha": "2026-10-20",
            "preregistro_sha256": "a" * 64, "activo": "BTC", "estado": "fallido",
            "cuenta_en_n": True, "fuente": "decision-log 2026-10-20", "nota": ""}
    base.update(cambios)
    return base


@pytest.fixture
def registro(tmp_path):
    ruta = tmp_path / "ensayos.jsonl"
    ruta.write_bytes(en.RUTA_ENSAYOS.read_bytes())
    return ruta


# ═══ Lo sembrado ═══════════════════════════════════════════════════════════

def test_lo_sembrado_no_cambia():
    lineas = en.RUTA_ENSAYOS.read_bytes().splitlines(keepends=True)
    assert len(lineas) >= N_SEMBRADAS
    assert hashlib.sha256(b"".join(lineas[:N_SEMBRADAS])).hexdigest() == SEMBRADO_SHA256


def test_los_tres_previos_fallidos_y_la_concordancia():
    por_id = {e["id"]: e for e in en.leer()}
    previos = ("previo-mascara-godel", "previo-proxy-transfer-entropy", "previo-ema-20-63")
    for i in previos:
        e = por_id[i]
        assert (e["hipotesis"], e["estado"], e["cuenta_en_n"]) == (
            en.HIPOTESIS_PREVIA_A_LA_SERIE_H, "fallido", True)
        assert e["preregistro_sha256"] is None and "decision-log" in e["fuente"]
    conc = por_id["concordancia-orb-oro"]
    assert conc["cuenta_en_n"] is False and conc["estado"] == "compuerta_roja"
    assert "no cuenta en el N" in conc["nota"] and "62,74 %" in conc["fuente"]


def test_la_concordancia_del_oro_no_suma_al_n():
    assert "concordancia-orb-oro" not in {e["id"] for e in en.que_cuentan()}
    sembradas = en.leer()[:N_SEMBRADAS]
    assert [e["id"] for e in sembradas if e["cuenta_en_n"]] == [
        "previo-mascara-godel", "previo-proxy-transfer-entropy", "previo-ema-20-63"]


# ═══ ENSAYOS_PREVIOS_FALLIDOS se deriva del registro ═══════════════════════

def test_ensayos_previos_fallidos_vale_3():
    assert en.previos_fallidos() == 3
    assert pre.ENSAYOS_PREVIOS_FALLIDOS == 3
    assert pre.N_INGENUO == 10


def test_ensayos_previos_fallidos_no_es_un_literal():
    """Se deriva: core/preregistro_h1.py lo asigna desde
    governance.ensayos.previos_fallidos(), no con un número."""
    arbol = ast.parse((RAIZ / "core" / "preregistro_h1.py").read_text(encoding="utf-8"))
    asignacion = next(n for n in arbol.body if isinstance(n, ast.Assign)
                      and any(getattr(t, "id", None) == "ENSAYOS_PREVIOS_FALLIDOS"
                              for t in n.targets))
    assert isinstance(asignacion.value, ast.Call)
    assert getattr(asignacion.value.func, "id", None) == "previos_fallidos"


def test_previos_fallidos_cuenta_lo_que_dice_el_registro(registro):
    assert en.previos_fallidos(registro) == 3
    en.agregar(_entrada(id="otro-previo", hipotesis=en.HIPOTESIS_PREVIA_A_LA_SERIE_H,
                        preregistro_sha256=None), registro)
    assert en.previos_fallidos(registro) == 4


@pytest.mark.parametrize("cambios", [
    {"hipotesis": "H1"},                                        # de la Serie H: no es previo
    {"hipotesis": en.HIPOTESIS_PREVIA_A_LA_SERIE_H, "estado": "aprobado"},
    {"hipotesis": en.HIPOTESIS_PREVIA_A_LA_SERIE_H, "cuenta_en_n": False},
])
def test_lo_que_no_es_un_previo_fallido_que_cuenta_no_suma(registro, cambios):
    en.agregar(_entrada(**cambios), registro)
    assert en.previos_fallidos(registro) == 3


def test_que_cuentan_es_la_unica_fuente_del_n(registro):
    antes = len(en.que_cuentan(registro))
    en.agregar(_entrada(id="h1-lookback-10", hipotesis="H1"), registro)
    assert [e["id"] for e in en.que_cuentan(registro)][-1] == "h1-lookback-10"
    assert len(en.que_cuentan(registro)) == antes + 1


# ═══ P3: qué es estrategia (brief del Admin 11–13-B) ═══════════════════════

def test_p3_las_estrategias_son_los_previos_h1_y_h3_y_h2_no():
    assert en.HIPOTESIS_DE_ESTRATEGIA == {en.HIPOTESIS_PREVIA_A_LA_SERIE_H, "H1", "H3"}
    assert en.HIPOTESIS_QUE_NO_SON_ESTRATEGIA == {"H2"}
    assert not en.HIPOTESIS_DE_ESTRATEGIA & en.HIPOTESIS_QUE_NO_SON_ESTRATEGIA


@pytest.mark.parametrize("hipotesis, suma", [
    (en.HIPOTESIS_PREVIA_A_LA_SERIE_H, True), ("H1", True), ("H3", True), ("H2", False)])
def test_al_n_del_dsr_suman_solo_las_estrategias(registro, hipotesis, suma):
    """El criterio viejo: todo lo que contaba sumaba al N del DSR, y un
    ensayo de H2 también. Con P3, H2 cuenta y no suma al DSR."""
    en.agregar(_entrada(id="ensayo-nuevo", hipotesis=hipotesis), registro)
    assert "ensayo-nuevo" in {e["id"] for e in en.que_cuentan(registro)}
    assert ("ensayo-nuevo" in {e["id"] for e in en.que_cuentan_en_el_dsr(registro)}) is suma


def test_una_estrategia_que_no_cuenta_no_suma(registro):
    en.agregar(_entrada(id="h1-que-no-cuenta", cuenta_en_n=False), registro)
    assert "h1-que-no-cuenta" not in {e["id"] for e in en.que_cuentan_en_el_dsr(registro)}


def test_una_hipotesis_sin_clasificar_que_cuenta_hace_fallar_el_conteo(registro):
    """Nadie entra ni sale del N por olvido: H4 tiene que declararse antes."""
    en.agregar(_entrada(id="h4-algo", hipotesis="H4"), registro)
    with pytest.raises(ValueError, match="sin clasificar"):
        en.que_cuentan_en_el_dsr(registro)


def test_una_sin_clasificar_que_no_cuenta_no_se_mira(registro):
    """Como la concordancia del oro: H-ORB-oro no está clasificada, y su
    línea no cuenta."""
    en.agregar(_entrada(id="h4-sin-contar", hipotesis="H4", cuenta_en_n=False), registro)
    assert en.que_cuentan_en_el_dsr(registro)
    with pytest.raises(ValueError, match="sin clasificar"):
        en.es_estrategia({"hipotesis": "H-ORB-oro"})


def test_en_el_registro_lo_que_cuenta_suma_al_dsr_salvo_h2():
    del_dsr = {e["id"] for e in en.que_cuentan_en_el_dsr()}
    assert {"previo-mascara-godel", "previo-proxy-transfer-entropy", "previo-ema-20-63"} <= del_dsr
    for e in en.que_cuentan():
        assert (e["id"] in del_dsr) is (e["hipotesis"] != "H2"), e["id"]


# ═══ Append-only ═══════════════════════════════════════════════════════════

def test_agregar_solo_agrega_al_final(registro):
    antes = registro.read_bytes()
    en.agregar(_entrada(), registro)
    despues = registro.read_bytes()
    assert despues.startswith(antes) and despues.count(b"\n") == antes.count(b"\n") + 1
    assert json.loads(despues.splitlines()[-1]) == _entrada()
    assert list(json.loads(despues.splitlines()[-1])) == list(en.CAMPOS_ENSAYO)


def test_un_id_repetido_no_entra(registro):
    antes = registro.read_bytes()
    with pytest.raises(ValueError, match="append-only"):
        en.agregar(_entrada(id="previo-ema-20-63"), registro)
    assert registro.read_bytes() == antes


def test_un_id_repetido_en_el_archivo_no_se_lee(registro):
    with registro.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_entrada(id="previo-mascara-godel")) + "\n")
    with pytest.raises(ValueError, match="id repetido"):
        en.leer(registro)


@pytest.mark.parametrize("cambios, fragmento", [
    ({"estado": "pausado"}, "estado"),
    ({"cuenta_en_n": 1}, "cuenta_en_n"),
    ({"fecha": "20-10-2026"}, "fecha"),
    ({"fecha": None}, "fecha"),
    ({"preregistro_sha256": "abc"}, "preregistro_sha256"),
    ({"preregistro_sha256": "A" * 64}, "preregistro_sha256"),
    ({"id": " "}, "id"),
    ({"hipotesis": ""}, "hipotesis"),
    ({"activo": None}, "activo"),
    ({"fuente": ""}, "fuente"),
    ({"nota": None}, "nota"),
])
def test_una_entrada_invalida_no_entra(registro, cambios, fragmento):
    antes = registro.read_bytes()
    with pytest.raises(ValueError, match=fragmento):
        en.agregar(_entrada(**cambios), registro)
    assert registro.read_bytes() == antes


def test_un_campo_de_mas_o_de_menos_no_entra(registro):
    sin_nota = {k: v for k, v in _entrada().items() if k != "nota"}
    with pytest.raises(ValueError, match="faltan"):
        en.agregar(sin_nota, registro)
    with pytest.raises(ValueError, match="sobran"):
        en.agregar({**_entrada(), "pnl": 3.0}, registro)


def test_una_linea_rota_dice_donde(registro):
    n = len(registro.read_bytes().splitlines())
    with registro.open("a", encoding="utf-8") as f:
        f.write("{no es json\n")
    with pytest.raises(ValueError, match=rf"ensayos\.jsonl:{n + 1}\b"):
        en.leer(registro)


# ═══ D11-u ═════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("dias, se_puede", [(0, False), (263, False), (755, False),
                                            (756, True), (2000, True)])
def test_h_orb_oro_se_reabre_con_756_dias_habiles(dias, se_puede):
    assert en.DIAS_HABILES_M5_DERIV_PARA_REABRIR_H_ORB_ORO == 756
    assert en.h_orb_oro_se_puede_reabrir(dias) is se_puede


@pytest.mark.parametrize("dias", [-1, 756.0, True, "756"])
def test_el_conteo_de_dias_es_un_entero(dias):
    with pytest.raises(ValueError):
        en.h_orb_oro_se_puede_reabrir(dias)
