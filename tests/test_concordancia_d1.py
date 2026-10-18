"""
tests/test_concordancia_d1.py
===============================
tools/concordancia_d1.py (brief del Admin 14, 14b, puntos 2, 3 y 5).
Offline, con series sintéticas en un metrics/ temporal. Los instantes de
corte del fixture se calculan acá, sin llamar al código bajo prueba: una
mutante no puede mover a la vez el fixture y lo que se mide.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone

import pytest

from ingestion.velas import _append, ruta_revisiones, ruta_serie
from tools import concordancia_d1 as cd

D, H, M5 = 86400, 3600, 300
_UTC = timezone.utc
#: Lunes 2-jun-2025.
INI = date(2025, 6, 2)
E0 = int(datetime(2025, 6, 2, tzinfo=_UTC).timestamp())
N_DIAS = 400


@pytest.fixture(autouse=True)
def _drive(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(tmp_path))


def _ep(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=_UTC).timestamp())


def precio(e: int) -> float:
    """Creciente: dos horas distintas difieren en más de 0,39 USD, y dos
    velas M5 en más de 0,03."""
    return 1000.0 + 0.5 * (e - E0) / H + 0.2 * math.sin(e / 7000)


def _dias(n: int = N_DIAS, *, habiles: bool = False, desde: date = INI) -> list[date]:
    out = [desde + timedelta(days=k) for k in range(n)]
    return [d for d in out if d.weekday() < 5] if habiles else out


def _intradia(dias: list[date], g: int) -> dict[int, float]:
    """Velas de 00:00 a medianoche de cada día; cierre = precio al terminar."""
    return {e: precio(e + g) for d in dias for e in range(_ep(d), _ep(d) + D, g)}


def _escribir(serie: str, g: int, cierres: dict[int, float]) -> None:
    _append(ruta_serie(serie, g), [{"epoch": e, "open": c, "high": c, "low": c, "close": c}
                                   for e, c in sorted(cierres.items())])


def _mundo(*, corte_oro: int = 0, corte_btc: int = 0) -> None:
    """Las seis series de D1. TwelveData cierra cada fecha D en D+1 00:00 con
    corte 0, o en D hh:00 con corte hh (hh ≥ 12); Deriv cierra en D+1 00:00,
    con un ruido de 1e-7."""
    def cierre_td(d: date, corte: int) -> float:
        return precio(_ep(d) + D if corte == 0 else _ep(d) + corte * H)
    for serie, deriv, habiles, corte, intradia, g in (
            ("td_BTCUSD", "cryBTCUSD", False, corte_btc, "td_BTCUSD", H),
            ("td_XAUUSD", "frxXAUUSD", True, corte_oro, "td_XAUUSD", M5)):
        dias = _dias(habiles=habiles)
        _escribir(serie, D, {_ep(d): cierre_td(d, corte) for d in dias})
        _escribir(deriv, D, {_ep(d): precio(_ep(d) + D) * (1 + 1e-7 * (-1) ** k)
                             for k, d in enumerate(dias)})
        _escribir(intradia, g, _intradia(dias[-20:] if not habiles else dias[-10:], g))


# ═══ Paso 1 ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("h, esperado", [(0, D), (1, D + H), (11, D + 11 * H), (12, 12 * H),
                                         (22, 22 * H), (23, 23 * H)])
def test_el_instante_de_corte_es_la_ventana_que_mas_se_superpone(h, esperado):
    assert cd.instante_de_corte(INI, h) == E0 + esperado


def test_el_cierre_al_corte_es_la_ultima_vela_que_termina_a_tiempo():
    t = E0 + D
    intradia = {t - M5: 1.0, t: 2.0, t - 2 * M5: 3.0}
    assert cd.cierre_al_corte(intradia, M5, t) == 1.0, "la que termina en t entra; la de t, no"
    del intradia[t - M5]
    assert cd.cierre_al_corte(intradia, M5, t) == 3.0, "un hueco: la anterior"


def test_el_cierre_al_corte_no_mira_mas_de_24_h_atras():
    t = E0 + 2 * D
    assert cd.cierre_al_corte({t - D: 5.0}, H, t) == 5.0, "la que empieza justo 24 h antes, sí"
    assert cd.cierre_al_corte({t - D - H: 5.0}, H, t) is None
    assert cd.cierre_al_corte({}, H, t) is None


def test_los_dias_comparables_necesitan_el_anterior_y_el_posterior_enteros():
    dias = _dias(10)
    intradia = _intradia(dias[2:7], H)          # del día 2 al 6, enteros
    diarias = {d: 1.0 for d in dias}
    assert cd.dias_comparables(diarias, intradia, H) == dias[3:6]
    assert cd.dias_comparables(diarias, {}, H) == []


def _paso_1_btc(cierre_td, n=20):
    dias = _dias(n)
    return cd.paso_1({_ep(d): cierre_td(d) for d in dias}, _intradia(dias, H), H)


def test_paso_1_con_corte_a_las_00_utc_sigue():
    p = _paso_1_btc(lambda d: precio(_ep(d) + D))
    assert (p["ganadora"], p["fraccion_ganadora"], p["decision"]) == (0, 1.0, "sigue")
    assert p["dias_comparables"] == 18 and p["motivos"] == []
    assert all(f["fraccion"] == 0 for f in p["por_hora"] if f["hora_utc"] != 0)


def test_paso_1_con_otro_corte_se_detiene():
    p = _paso_1_btc(lambda d: precio(_ep(d) + 22 * H))
    assert (p["ganadora"], p["decision"]) == (22, "detenido")
    assert p["motivos"] == ["la h ganadora es 22:00 UTC, no 00:00"]


def test_paso_1_con_la_fecha_del_dia_en_que_cierra_se_detiene_y_lo_muestra():
    """Corte a las 00:00, pero la barra fechada por el día en que termina:
    ninguna h llega, y la fracción con la otra fecha lo deja a la vista."""
    p = _paso_1_btc(lambda d: precio(_ep(d)))
    assert p["decision"] == "detenido" and p["fraccion_ganadora"] < 0.95
    h0 = p["por_hora"][0]
    assert h0["fraccion"] == 0 and h0["fraccion_con_la_otra_fecha"] == 1.0


@pytest.mark.parametrize("malos, decision", [(0, "sigue"), (1, "sigue"), (2, "detenido")])
def test_paso_1_el_95_por_ciento(malos, decision):
    """20 días comparables: 19 son el 95 % justo."""
    dias = _dias(22)
    fuera = set(dias[2:2 + malos])
    p = cd.paso_1({_ep(d): precio(_ep(d) + D) + (0.5 if d in fuera else 0) for d in dias},
                  _intradia(dias, H), H)
    assert p["dias_comparables"] == 20 and p["decision"] == decision
    assert p["por_hora"][0]["coinciden"] == 20 - malos


@pytest.mark.parametrize("delta, coincide", [(0.0099, True), (-0.0099, True),
                                             (0.0111, False), (-0.0111, False)])
def test_paso_1_la_tolerancia_es_de_un_centavo(delta, coincide):
    dias = _dias(22)
    p = cd.paso_1({_ep(d): precio(_ep(d) + D) + delta for d in dias}, _intradia(dias, H), H)
    assert p["por_hora"][0]["coinciden"] == (20 if coincide else 0)


def test_paso_1_la_tolerancia_incluye_el_centavo_justo():
    """0,01 − 0 es 0,01 exacto en binario: el borde se prueba sin redondeo."""
    t = E0 + D
    assert cd._coincide_el_cierre({t - H: 0.01}, H, t, 0.0) is True
    assert cd._coincide_el_cierre({t - H: 0.02}, H, t, 0.0) is False
    assert cd._coincide_el_cierre({}, H, t, 0.0) is None


def test_paso_1_un_dia_sin_vela_en_la_ventana_no_coincide():
    dias = _dias(22)
    intradia = _intradia(dias, H)
    for e in range(_ep(dias[5]), _ep(dias[6]), H):
        del intradia[e]
    p = cd.paso_1({_ep(d): precio(_ep(d) + D) for d in dias}, intradia, H)
    h0 = p["por_hora"][0]
    assert (h0["dias"], h0["coinciden"], h0["sin_cierre"]) == (20, 19, 1)


def test_paso_1_un_empate_en_el_maximo_se_detiene():
    dias = _dias(22)
    intradia = {e: 1000.0 for e in _intradia(dias, H)}       # precio plano
    p = cd.paso_1({_ep(d): 1000.0 for d in dias}, intradia, H)
    assert p["ganadora"] is None and p["decision"] == "detenido"
    assert p["motivos"][0].startswith("empate en el máximo")


def test_paso_1_sin_dias_se_detiene():
    p = cd.paso_1({E0: 1.0}, {}, H)
    assert p["decision"] == "detenido" and p["motivos"] == ["no hay días comparables"]


def _por_hora(coinciden: dict[int, int], dias: int = 20) -> list[dict]:
    return [{"hora_utc": h, "dias": dias, "coinciden": coinciden.get(h, 0)} for h in range(24)]


@pytest.mark.parametrize("coinciden, ganadora, decision", [
    ({0: 19, 1: 3}, 0, "sigue"),
    ({0: 18}, 0, "detenido"),
    ({22: 20, 0: 1}, 22, "detenido"),
    ({0: 20, 5: 20}, None, "detenido"),
])
def test_decidir_corte(coinciden, ganadora, decision):
    r = cd.decidir_corte(_por_hora(coinciden))
    assert (r["ganadora"], r["decision"]) == (ganadora, decision)


def test_decidir_corte_dice_todos_los_motivos():
    r = cd.decidir_corte(_por_hora({22: 18}))
    assert len(r["motivos"]) == 2 and r["fraccion_ganadora"] == 0.9


# ═══ Paso 2 ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("r_td, r_deriv, tolerancia, esperado", [
    (0.5, 0.75, 0.25, True),             # |Δ| = tolerancia, exacto en binario
    (0.5, 0.75, 0.2499, False),
    (-0.5, -0.75, 0.25, True),
    (0.01, -0.01, 1.0, False),           # signo opuesto
    (0.00005, -0.00005, 1.0, True),      # los dos bajo 1 pb
    (0.00005, -0.0002, 1.0, False),      # uno solo bajo 1 pb
    (0.0001, -0.0001, 1.0, False),       # 1 pb justo no es "menos de 1 pb"
    (0.0, 0.0002, 1.0, False),           # un 0 no es del signo de un positivo
    (0.0, 0.00009, 1.0, True),
    (0.0, 0.0, 0.0, True),
])
def test_la_regla_de_coincidencia(r_td, r_deriv, tolerancia, esperado):
    assert cd.coincide(r_td, r_deriv, tolerancia) is esperado


def _cierres(r: list[float], dias: list[date], c0: float = 100.0) -> dict[int, float]:
    out, c = {_ep(dias[0]): c0}, c0
    for d, x in zip(dias[1:], r):
        c *= math.exp(x)
        out[_ep(d)] = c
    return out


def _r_deriv(n: int) -> list[float]:
    """|r| entre 0,010 y 0,014, de signo alternado: mediana 0,012."""
    return [0.01 * (1 + (k % 5) / 10) * (-1) ** k for k in range(n)]


def _paso_2(r_td, r_dr, dias, calendario="continuo", **kw):
    return cd.paso_2(_cierres(r_td, dias), _cierres(r_dr, dias), calendario, **kw)


@pytest.mark.parametrize("opuestos, compuerta", [(25, "verde"), (26, "roja")])
def test_la_compuerta_pide_el_90_por_ciento(opuestos, compuerta):
    dias = _dias(251)                     # 250 pares
    r_dr = _r_deriv(250)
    r_td = [-x if k < opuestos else x for k, x in enumerate(r_dr)]
    p = _paso_2(r_td, r_dr, dias)
    assert (p["dias_comunes"], p["pares"], p["coinciden"]) == (251, 250, 250 - opuestos)
    assert p["compuerta"] == compuerta


@pytest.mark.parametrize("n, compuerta", [(250, "verde"), (249, "roja")])
def test_la_compuerta_pide_250_dias_comunes(n, compuerta):
    dias = _dias(n)
    r = _r_deriv(n - 1)
    p = _paso_2(r, r, dias)
    assert p["fraccion"] == 1.0 and p["compuerta"] == compuerta
    if compuerta == "roja":
        assert p["motivos"] == ["249 días comunes, menos que 250"]


def test_la_tolerancia_es_un_quinto_de_la_mediana_de_deriv():
    dias = _dias(301)
    r_dr = [0.01 * (-1) ** k for k in range(300)]
    p = _paso_2([1.19 * x for x in r_dr], r_dr, dias)
    assert p["mediana_abs_r_deriv"] == pytest.approx(0.01)
    assert p["tolerancia"] == pytest.approx(0.002) and p["coinciden"] == 300
    # Con la mediana de TwelveData (0,0121) la tolerancia alcanzaría; con la de Deriv, no.
    p = _paso_2([1.21 * x for x in r_dr], r_dr, dias)
    assert p["coinciden"] == 0 and p["compuerta"] == "roja"


def test_la_tolerancia_sale_de_la_mediana_y_no_de_la_media():
    """|r_Deriv| de 0,01 en dos de cada tres días y de 0,04 en el resto:
    mediana 0,01 (tolerancia 0,002), media 0,02 (sería 0,004)."""
    dias = _dias(301)
    r_dr = [(0.04 if k % 3 == 2 else 0.01) * (-1) ** k for k in range(300)]
    r_td = [x + (0.003 if abs(x) < 0.02 else 0.0) for x in r_dr]
    p = _paso_2(r_td, r_dr, dias)
    assert p["mediana_abs_r_deriv"] == pytest.approx(0.01)
    assert p["coinciden"] == 100, "solo los de 0,04, que no se movieron"


def test_un_hueco_saca_el_par_pero_no_el_dia_comun():
    dias = _dias(262)
    r = _r_deriv(261)
    td = _cierres(r, dias)
    del td[_ep(dias[100])]                           # falta en TwelveData
    p = cd.paso_2(td, _cierres(r, dias), "continuo")
    assert (p["dias_comunes"], p["pares"], p["pares_fuera_por_hueco"]) == (261, 259, 2)
    assert p["fraccion"] == 1.0 and p["compuerta"] == "verde"
    assert p["fraccion_sobre_dias_comunes"] == pytest.approx(259 / 261)


def test_en_el_oro_el_lunes_sigue_al_viernes_y_el_fin_de_semana_queda_fuera():
    dias = _dias(380, habiles=True)
    r = _r_deriv(len(dias) - 1)
    td, dr = _cierres(r, dias), _cierres(r, dias)
    domingo = _ep(next(d for d in _dias(14, desde=dias[100]) if d.weekday() == 6))
    td[domingo], dr[domingo] = 1e6, 1.0              # un domingo en las dos, disparatado
    p = cd.paso_2(td, dr, "habil")
    assert p["dias_comunes"] == len(dias) and p["pares"] == len(dias) - 1
    assert p["fuera_del_calendario"] == {"twelvedata": 1, "deriv": 1}
    assert p["coinciden"] == p["pares"], "ningún lunes queda fuera por el fin de semana"


def test_el_informativo():
    dias = _dias(261)
    r_dr = _r_deriv(260)
    r_td = [-x if k < 3 else x * 1.05 for k, x in enumerate(r_dr)]
    r_td[3] = 0.0                                  # un r nulo no es de signo opuesto
    revisiones = [(_ep(dias[10]), 100.0, 100.5), (_ep(dias[11]), 100.0, 100.0),
                  (_ep(dias[-1]), 100.0, 100.5),           # el último día común, dentro
                  (_ep(dias[0]) - D, 100.0, 120.0)]        # el día antes, fuera
    p = _paso_2(r_td, r_dr, dias, revisiones=revisiones)
    i = p["informativo"]
    assert i["dias_de_signo_opuesto"] == 3
    assert -1 < i["correlacion_r"] < 1
    assert i["abs_r_td_menos_r_deriv"]["50"] == pytest.approx(0.05 * 0.012, rel=0.1)
    assert i["revisiones_deriv"] == {"en_la_ventana": 3, "con_otro_cierre": 2,
                                     "abs_dcierre_relativo": {"50": pytest.approx(0.005),
                                                              "90": pytest.approx(0.005)},
                                     "abs_dcierre_relativo_max": pytest.approx(0.005)}


def test_sin_pares_es_roja():
    p = cd.paso_2({E0: 1.0}, {E0: 1.0}, "continuo")
    assert p["compuerta"] == "roja" and p["motivos"] == ["no hay pares"]


# ═══ Informe ══════════════════════════════════════════════════════════════

def test_con_los_dos_cortes_a_las_00_utc_hay_compuerta():
    _mundo()
    bloques = dict(cd.informe())
    assert list(bloques) == ["DATOS", "PASO 1 BTC", "PASO 1 XAU", "PASO 2 BTC", "PASO 2 XAU",
                             "COMPUERTA"]
    assert bloques["PASO 1 BTC"]["decision"] == bloques["PASO 1 XAU"]["decision"] == "sigue"
    assert bloques["COMPUERTA"] == {"BTC": "verde", "XAU": "verde"}
    assert bloques["PASO 2 BTC"]["dias_comunes"] == N_DIAS
    assert bloques["PASO 2 XAU"]["dias_comunes"] == len(_dias(habiles=True))
    datos = bloques["DATOS"]["XAU"]
    assert datos["intradia_twelvedata"]["velas"] == 10 * 288
    assert len(datos["deriv_86400"]["sha256"]) == 64 and datos["deriv_86400"]["sha256_revisiones"] is None


@pytest.mark.parametrize("corte_oro, corte_btc, detenidos", [(22, 0, ["XAU"]), (0, 22, ["BTC"])])
def test_un_corte_que_no_es_00_utc_detiene_todo_sin_compuerta(monkeypatch, corte_oro, corte_btc,
                                                              detenidos):
    _mundo(corte_oro=corte_oro, corte_btc=corte_btc)

    def espia(*a, **k):
        raise AssertionError("se calculó la compuerta con el paso 1 detenido")
    monkeypatch.setattr(cd, "paso_2", espia)
    bloques = dict(cd.informe())
    assert list(bloques) == ["DATOS", "PASO 1 BTC", "PASO 1 XAU", "DETENIDO"]
    assert list(bloques["DETENIDO"]["motivos"]) == detenidos
    assert bloques[f"PASO 1 {detenidos[0]}"]["ganadora"] == 22


def test_las_revisiones_de_deriv_entran_al_informe():
    _mundo()
    e = _ep(_dias()[-5])
    _append(ruta_revisiones("cryBTCUSD", D), [{
        "epoch": e, "guardada": {"epoch": e, "open": 1.0, "high": 1.0, "low": 1.0, "close": 100.0},
        "nueva": {"epoch": e, "open": 1.0, "high": 1.0, "low": 1.0, "close": 101.0},
        "detectada": "2026-10-19"}])
    bloques = dict(cd.informe())
    rev = bloques["PASO 2 BTC"]["informativo"]["revisiones_deriv"]
    assert rev["en_la_ventana"] == 1 and rev["abs_dcierre_relativo_max"] == pytest.approx(0.01)
    assert bloques["DATOS"]["BTC"]["deriv_86400"]["sha256_revisiones"]


def test_main_publica_cada_bloque_y_sale_0(capsys):
    _mundo()
    assert cd.main([]) == 0
    salida = capsys.readouterr().out.splitlines()
    titulos = [l for l in salida if l.startswith("=== ")]
    assert titulos[-1] == "=== COMPUERTA ==="
    assert json.loads(salida[-1]) == {"BTC": "verde", "XAU": "verde"}
