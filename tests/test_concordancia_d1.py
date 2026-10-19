"""
tests/test_concordancia_d1.py
===============================
tools/concordancia_d1.py (brief del Admin 14, 14b, puntos 2, 3 y 5).
Offline, con series sintéticas en un metrics/ temporal. Los instantes de
corte del fixture se calculan acá, sin llamar al código bajo prueba: una
mutante no puede mover a la vez el fixture y lo que se mide.
"""

from __future__ import annotations

import ast
import json
import math
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from ingestion.velas import _append, ruta_revisiones, ruta_serie
from ingestion.velas_intradia import ruta_calendario
from tools import concordancia_d1 as cd

D, H, M5 = 86400, 3600, 300
RAIZ = Path(__file__).resolve().parent.parent
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


#: El horario de Deriv como lo guarda velas_intradia (trading_times del
#: 14-oct-2026). `franja` no lee el evento.
HORARIO_DERIV = {"trading_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
                 "aperturas": ["00:00:00", "22:00:00"], "cierres": ["21:00:00", "23:59:59"],
                 "events": [{"dates": "Fridays", "descrip": "Closes early (at 20:55)"}]}


def _m5_del_oro(dias: list[date], *, deriv: bool, noche_del_viernes: float = 0.0) -> dict[int, float]:
    """Velas M5 ralas del oro; cierre = precio al terminar. De lunes a jueves:
    20:55, 22:00 y 23:55, y TwelveData también en la pausa (21:00). El
    viernes, Deriv hasta las 20:55; TwelveData sigue hasta las 23:55
    (desplazadas en `noche_del_viernes`, relativo) y cotiza el sábado, como
    en data desde 2025. Una vela al día siguiente del último cierra ese día."""
    m5 = {}
    for d in dias:
        if d.weekday() < 4:
            horas = [20 * H + 55 * 60, 22 * H, 23 * H + 55 * 60] + ([] if deriv else [21 * H])
        else:
            horas = [20 * H + 50 * 60, 20 * H + 55 * 60] + (
                [] if deriv else [21 * H, 22 * H, 23 * H + 55 * 60, D + 10 * H])
        noche = noche_del_viernes if d.weekday() == 4 else 0.0
        for s in horas:
            e = _ep(d) + s
            m5[e] = precio(e + M5) * (1 + (noche if 21 * H <= s < D else 0.0))
    m5[_ep(dias[-1]) + D] = precio(_ep(dias[-1]) + D + M5)
    return m5


def _mundo(*, corte_oro: int = 0, corte_btc: int = 0, noche_del_viernes: float = 0.0,
           horario: bool = True) -> None:
    """Las series de D1. BTC: TwelveData cierra cada fecha D en D+1 00:00 con
    corte 0, o en D hh:00 con corte hh (hh ≥ 12); Deriv cierra en D+1 00:00,
    con un ruido de 1e-7. El oro (brief 15): el M5 de las dos fuentes y el
    horario de Deriv. Deriv cierra con su última vela M5 con corte 0, o en
    D hh:00 con corte hh, con el mismo ruido. Su 1day de TwelveData sigue
    escrita (D1-x)."""
    def cierre_td(d: date, corte: int) -> float:
        return precio(_ep(d) + D if corte == 0 else _ep(d) + corte * H)
    dias = _dias()
    _escribir("td_BTCUSD", D, {_ep(d): cierre_td(d, corte_btc) for d in dias})
    _escribir("cryBTCUSD", D, {_ep(d): precio(_ep(d) + D) * (1 + 1e-7 * (-1) ** k)
                               for k, d in enumerate(dias)})
    _escribir("td_BTCUSD", H, _intradia(dias[-20:], H))
    oro = _dias(habiles=True)
    _escribir("td_XAUUSD", D, {_ep(d): cierre_td(d, 0) for d in oro})
    _escribir("td_XAUUSD", M5, _m5_del_oro(oro, deriv=False, noche_del_viernes=noche_del_viernes))
    _escribir("frxXAUUSD", M5, _m5_del_oro(oro, deriv=True))

    def cierre_deriv(d: date) -> float:
        if corte_oro:
            return precio(_ep(d) + corte_oro * H)
        return precio(_ep(d) + (21 * H if d.weekday() == 4 else D))
    _escribir("frxXAUUSD", D, {_ep(d): cierre_deriv(d) * (1 + 1e-7 * (-1) ** k)
                               for k, d in enumerate(oro)})
    if horario:
        _append(ruta_calendario(), [{"fecha_servidor": "2026-10-14", "sha256": "ab" * 32,
                                     "horario": HORARIO_DERIV, "crudo": "{}"}])


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
                             "SENSIBILIDAD XAU", "COMPUERTA"]
    assert bloques["PASO 1 BTC"]["decision"] == bloques["PASO 1 XAU"]["decision"] == "sigue"
    assert bloques["COMPUERTA"] == {"BTC": "verde", "XAU": "verde"}
    assert bloques["PASO 2 BTC"]["dias_comunes"] == N_DIAS
    assert bloques["PASO 2 XAU"]["dias_comunes"] == len(_dias(habiles=True))
    datos = bloques["DATOS"]["XAU"]
    assert datos["intradia_twelvedata"]["velas"] == len(_m5_del_oro(_dias(habiles=True), deriv=False))
    assert len(datos["deriv_86400"]["sha256"]) == 64 and datos["deriv_86400"]["sha256_revisiones"] is None


@pytest.mark.parametrize("corte_oro, corte_btc, detenidos, bloques_esperados", [
    (22, 0, ["XAU"], ["DATOS", "PASO 1 BTC", "PASO 1 XAU", "PASO 2 BTC", "DETENIDO XAU",
                      "COMPUERTA"]),
    (0, 22, ["BTC"], ["DATOS", "PASO 1 BTC", "PASO 1 XAU", "DETENIDO BTC", "PASO 2 XAU",
                      "SENSIBILIDAD XAU", "COMPUERTA"]),
])
def test_un_corte_que_no_es_00_utc_detiene_ese_activo_sin_su_compuerta(
        monkeypatch, corte_oro, corte_btc, detenidos, bloques_esperados):
    _mundo(corte_oro=corte_oro, corte_btc=corte_btc)
    paso_2 = cd.paso_2
    calendario_detenido = {"BTC": "continuo", "XAU": "habil"}[detenidos[0]]

    def espia(td, deriv, calendario, *a, **k):
        if calendario == calendario_detenido:
            raise AssertionError("se calculó la compuerta con el paso 1 detenido")
        return paso_2(td, deriv, calendario, *a, **k)
    monkeypatch.setattr(cd, "paso_2", espia)
    bloques = dict(cd.informe())
    assert list(bloques) == bloques_esperados
    assert bloques[f"DETENIDO {detenidos[0]}"]["motivos"] == bloques[f"PASO 1 {detenidos[0]}"]["motivos"]
    assert bloques["COMPUERTA"] == {"BTC": "verde", "XAU": "verde", detenidos[0]: "detenido"}
    assert bloques["PASO 1 BTC"]["ganadora"] == corte_btc


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


# ═══ Brief 15: la diaria desde el M5 (D1-o) y el paso 1 del oro ═══════════

def _semana_m5(viernes_hasta: int = 20 * H + 55 * 60) -> dict[int, float]:
    """Una semana de M5 del oro desde el lunes E0. Lunes y martes enteros,
    miércoles sin velas, jueves hasta las 13:55, viernes hasta las 20:55 (o
    `viernes_hasta`), y velas el sábado entero y el domingo a la noche, como
    cotiza TwelveData."""
    m5 = {}

    def dia(k: int, hasta: int):
        for e in range(E0 + k * D, E0 + k * D + hasta + 1, M5):
            m5[e] = precio(e + M5)
    dia(0, 23 * H + 55 * 60)
    dia(1, 23 * H + 55 * 60)
    dia(3, 13 * H + 55 * 60)
    dia(4, viernes_hasta)
    dia(5, 23 * H + 55 * 60)
    for e in range(E0 + 6 * D + 22 * H, E0 + 7 * D, M5):
        m5[e] = precio(e + M5)
    return m5


def test_la_diaria_desde_m5_es_la_ultima_vela_de_cada_dia_habil():
    d = cd.diarias_desde_m5(_semana_m5())
    assert list(d) == [E0, E0 + D, E0 + 3 * D, E0 + 4 * D], \
        "sin el miércoles, que no tiene velas, ni el sábado y el domingo"
    assert d[E0] == precio(E0 + D), "la de las 23:55 cierra a las 24:00"
    assert d[E0 + 3 * D] == precio(E0 + 3 * D + 14 * H), "no exige la de las 23:55"
    assert d[E0 + 4 * D] == precio(E0 + 4 * D + 21 * H), "el viernes, la de las 20:55"


def test_la_vela_de_las_00_00_es_del_dia_que_empieza():
    m5 = {E0 + D - M5: 1.0, E0 + D: 2.0, E0 + 2 * D: 3.0}
    assert cd.diarias_desde_m5(m5) == {E0: 1.0, E0 + D: 2.0}


def test_el_ultimo_dia_de_la_serie_no_tiene_cierre():
    """La serie puede terminar a mitad del día: su diaria todavía no cerró.
    Una vela del día siguiente la cierra."""
    m5 = {E0 + 23 * H + 55 * 60: 1.0, E0 + D + 18 * H: 2.0}
    assert cd.diarias_desde_m5(m5) == {E0: 1.0}
    assert cd.diarias_desde_m5({**m5, E0 + 2 * D: 3.0}) == {E0: 1.0, E0 + D: 2.0}
    assert cd.diarias_desde_m5({}) == {}


def test_con_el_horario_el_viernes_cierra_con_deriv_y_no_con_la_noche():
    """TwelveData sigue el viernes hasta las 23:55 y cotiza el sábado. Con el
    horario de Deriv, el viernes cierra con la vela que termina a las 21:00
    (desde el primer cierre del día es fin de semana) y el sábado no cuenta
    ni para cerrar el viernes: la serie sigue después. De lunes a jueves, la
    pausa de las 21:00 queda fuera y la última es la de las 23:55, la misma
    que sin el horario."""
    m5 = _semana_m5(viernes_hasta=23 * H + 55 * 60)
    viernes = E0 + 4 * D
    con, sin = cd.diarias_desde_m5(m5, horario=HORARIO_DERIV), cd.diarias_desde_m5(m5)
    assert list(con) == list(sin) == [E0, E0 + D, E0 + 3 * D, viernes]
    assert con[viernes] == precio(viernes + 21 * H)
    assert sin[viernes] == precio(viernes + D)
    assert con[E0] == sin[E0] == precio(E0 + D)
    assert con[E0 + 3 * D] == sin[E0 + 3 * D] == precio(E0 + 3 * D + 14 * H)


def test_con_el_horario_un_dia_sin_velas_dentro_no_tiene_cierre():
    """Un lunes con velas solo en la pausa de Deriv: sin el horario cierra con
    la de las 21:55; con el horario no tiene cierre."""
    m5 = {E0 + 21 * H + k * M5: float(k) for k in range(12)} | {E0 + D: 99.0}
    assert cd.diarias_desde_m5(m5) == {E0: 11.0}
    assert cd.diarias_desde_m5(m5, horario=HORARIO_DERIV) == {}


def test_la_diaria_desde_m5_no_escribe_nada(tmp_path):
    cd.diarias_desde_m5(_semana_m5())
    assert list(tmp_path.rglob("*")) == []


def _deriv_igual_a_su_m5(m5: dict[int, float]) -> dict[int, float]:
    """Las barras 86400 de Deriv que cierran con su última vela M5,
    calculadas acá: una mutante no mueve a la vez esto y lo que se mide."""
    ultima = {}
    for e in m5:
        d = e - e % D
        if datetime.fromtimestamp(d, _UTC).weekday() < 5 and e > ultima.get(d, -1):
            ultima[d] = e
    return {d: m5[e] for d, e in ultima.items()}


def _semanas_m5(n: int) -> dict[int, float]:
    m5 = {}
    for s in range(n):
        for e, c in _semana_m5().items():
            m5[e + s * 7 * D] = c + s
    return m5


def test_paso_1_del_oro_con_el_cierre_de_deriv_igual_a_su_m5_sigue():
    m5 = _semanas_m5(5)
    p = cd.paso_1_deriv(_deriv_igual_a_su_m5(m5), m5)
    assert (p["dias"], p["coinciden"], p["decision"], p["motivos"]) == (20, 20, "sigue", [])


@pytest.mark.parametrize("malos, decision", [(0, "sigue"), (1, "sigue"), (2, "detenido")])
def test_paso_1_del_oro_el_95_por_ciento(malos, decision):
    """20 días con las dos: 19 son el 95 % justo."""
    m5 = _semanas_m5(5)
    diarias = _deriv_igual_a_su_m5(m5)
    for d in sorted(diarias)[:malos]:
        diarias[d] += 0.5
    p = cd.paso_1_deriv(diarias, m5)
    assert (p["dias"], p["coinciden"], p["decision"]) == (20, 20 - malos, decision)


@pytest.mark.parametrize("cierre, coincide", [(0.01, True), (-0.01, True), (0.02, False)])
def test_paso_1_del_oro_la_tolerancia_incluye_el_centavo_justo(cierre, coincide):
    """0,01 − 0 es 0,01 exacto en binario."""
    p = cd.paso_1_deriv({E0: cierre}, {E0 + D - M5: 0.0, E0 + D: 9.0})
    assert p["coinciden"] == int(coincide)


def test_paso_1_del_oro_solo_cuenta_los_dias_con_las_dos():
    m5 = _semana_m5()
    diarias = _deriv_igual_a_su_m5(m5)
    diarias[E0 + 2 * D] = 1.0                 # el miércoles no tiene M5
    del diarias[E0 + D]                       # el martes no tiene barra diaria
    p = cd.paso_1_deriv(diarias, m5)
    assert (p["dias"], p["coinciden"]) == (3, 3)
    assert (p["primer_dia"], p["ultimo_dia"]) == ("2025-06-02", "2025-06-06")


def test_paso_1_del_oro_sin_dias_se_detiene():
    p = cd.paso_1_deriv({E0: 1.0}, {E0 + 2 * D: 1.0})
    assert p["decision"] == "detenido" and p["fraccion"] is None
    assert p["motivos"] == ["no hay días con barra diaria y velas M5 de Deriv"]


# ═══ Brief 15: el informe por activo (D1-i), el oro desde el M5 (D1-o) ════

def test_con_los_dos_detenidos_no_hay_ninguna_compuerta(monkeypatch):
    _mundo(corte_oro=22, corte_btc=22)

    def espia(*a, **k):
        raise AssertionError("se calculó una compuerta con el paso 1 detenido")
    monkeypatch.setattr(cd, "paso_2", espia)
    bloques = dict(cd.informe())
    assert list(bloques) == ["DATOS", "PASO 1 BTC", "PASO 1 XAU", "DETENIDO BTC", "DETENIDO XAU",
                             "COMPUERTA"]
    assert bloques["COMPUERTA"] == {"BTC": "detenido", "XAU": "detenido"}


def test_el_veredicto_de_cada_activo_es_el_suyo(monkeypatch):
    """Un oro rojo no toca el verde de BTC, ni al revés."""
    _mundo()
    paso_2 = cd.paso_2

    def oro_rojo(td, deriv, calendario, *a, **k):
        p = paso_2(td, deriv, calendario, *a, **k)
        return {**p, "compuerta": "roja"} if calendario == "habil" else p
    monkeypatch.setattr(cd, "paso_2", oro_rojo)
    assert dict(cd.informe())["COMPUERTA"] == {"BTC": "verde", "XAU": "roja"}


def test_sin_horario_el_oro_se_detiene_y_btc_sigue(monkeypatch):
    _mundo(horario=False)
    paso_2 = cd.paso_2

    def espia(td, deriv, calendario, *a, **k):
        assert calendario != "habil", "la compuerta del oro, sin horario"
        return paso_2(td, deriv, calendario, *a, **k)
    monkeypatch.setattr(cd, "paso_2", espia)
    bloques = dict(cd.informe())
    assert bloques["PASO 1 XAU"]["decision"] == "sigue"
    assert bloques["DETENIDO XAU"]["motivos"][0].startswith("sin horario de Deriv")
    assert bloques["COMPUERTA"] == {"BTC": "verde", "XAU": "detenido"}
    assert bloques["DATOS"]["XAU"]["horario_deriv"]["horario"] is None


def test_la_compuerta_del_oro_usa_el_horario_y_la_sensibilidad_las_24(monkeypatch):
    """Las diarias de TwelveData que llegan a cada compuerta del oro, leídas
    en el viernes: con el horario, la vela que termina a las 21:00; en la
    sensibilidad, la de las 24:00."""
    _mundo()
    paso_2, vistas = cd.paso_2, []

    def espia(td, deriv, calendario, *a, **k):
        if calendario == "habil":
            vistas.append(td)
        return paso_2(td, deriv, calendario, *a, **k)
    monkeypatch.setattr(cd, "paso_2", espia)
    bloques = dict(cd.informe())
    viernes = next(d for d in _dias(habiles=True) if d.weekday() == 4)
    con, sin = vistas
    assert con[_ep(viernes)] == precio(_ep(viernes) + 21 * H)
    assert sin[_ep(viernes)] == precio(_ep(viernes) + D)
    assert bloques["PASO 2 XAU"]["diaria_de_twelvedata"].endswith("dentro del horario de Deriv")
    assert bloques["SENSIBILIDAD XAU"]["el_veredicto_cambia"] is False


def test_si_la_noche_del_viernes_cambia_el_veredicto_lo_dice_y_manda_el_horario():
    """La noche del viernes de TwelveData, un 5 % arriba: con la regla de
    24:00 el r del viernes y el del lunes no coinciden (40 % de los pares);
    con el horario no entra. La compuerta del oro es la del horario."""
    _mundo(noche_del_viernes=0.05)
    bloques = dict(cd.informe())
    assert bloques["PASO 2 XAU"]["compuerta"] == "verde"
    s = bloques["SENSIBILIDAD XAU"]
    assert s["compuerta"] == "roja" and s["el_veredicto_cambia"] is True
    assert s["fraccion"] == pytest.approx(0.6, abs=0.01)
    assert bloques["COMPUERTA"] == {"BTC": "verde", "XAU": "verde"}


# ═══ Brief 15, D1-x: la 1day del oro de TwelveData sigue en data, sin leerse ═

def test_el_oro_no_lee_su_1day_de_twelvedata(monkeypatch):
    _mundo()
    cierres, leidas = cd.cierres, []

    def espia(serie, granularidad):
        leidas.append((serie, granularidad))
        return cierres(serie, granularidad)
    monkeypatch.setattr(cd, "cierres", espia)
    bloques = dict(cd.informe())
    assert ("td_BTCUSD", D) in leidas, "la espía ve lo que se lee"
    assert ("td_XAUUSD", D) not in leidas
    assert sorted(bloques["DATOS"]["XAU"]) == ["deriv_86400", "deriv_m5", "horario_deriv",
                                               "intradia_twelvedata"]


def test_con_la_1day_del_oro_ilegible_ninguna_herramienta_cambia():
    """La 1day sigue en data: se reemplaza por algo que no es JSON, y ni la
    concordancia diaria ni la del ORB (las dos herramientas que leen velas)
    cambian una línea."""
    from tools import concordancia_xauusd as cx
    _mundo()
    antes = (cd.informe(), cx.informe())
    ruta_serie("td_XAUUSD", D).write_text("esto no es una vela\n", encoding="utf-8")
    assert (cd.informe(), cx.informe()) == antes


def test_solo_esas_dos_herramientas_leen_velas():
    """Si una herramienta nueva lee velas, entra en el test de arriba."""
    leen = sorted(p.name for p in (RAIZ / "tools").glob("*.py")
                  if any(t in p.read_text(encoding="utf-8") for t in ("leer_velas", "ruta_serie")))
    assert leen == ["concordancia_d1.py", "concordancia_xauusd.py"]


# ═══ Brief 15: la regla del paso 2 es la de 41cf853 ═══════════════════════

#: El commit que fijó la compuerta antes de bajar un precio (decision-log
#: del 18-oct, brief 14b). El brief 15 la deja sin tocar.
COMMIT_DE_LA_COMPUERTA = "41cf853"
REGLA_DEL_PASO_2 = ("coincide", "paso_2", "r_por_dia", "dia_anterior", "del_calendario")
CONSTANTES_DE_LA_COMPUERTA = ("TOLERANCIA_CIERRE_USD", "UMBRAL_CORTE", "UMBRAL_PLANO",
                              "FACTOR_MEDIANA", "UMBRAL_COMPUERTA", "MIN_DIAS_COMUNES")


def _definiciones(fuente: str) -> dict[str, str]:
    """nombre -> texto de cada función y asignación de nivel superior."""
    out = {}
    for n in ast.parse(fuente).body:
        if isinstance(n, ast.FunctionDef):
            out[n.name] = ast.get_source_segment(fuente, n)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            objetivo = n.targets[0] if isinstance(n, ast.Assign) else n.target
            out[objetivo.id] = ast.get_source_segment(fuente, n)
    return out


def test_la_regla_del_paso_2_es_la_de_41cf853():
    r = subprocess.run(["git", "show", f"{COMMIT_DE_LA_COMPUERTA}:tools/concordancia_d1.py"],
                       cwd=RAIZ, capture_output=True, text=True)
    assert r.returncode == 0, f"sin {COMMIT_DE_LA_COMPUERTA} en la historia: {r.stderr.strip()}"
    vieja = _definiciones(r.stdout)
    nueva = _definiciones((RAIZ / "tools" / "concordancia_d1.py").read_text(encoding="utf-8"))
    for nombre in REGLA_DEL_PASO_2 + CONSTANTES_DE_LA_COMPUERTA:
        assert nueva[nombre] == vieja[nombre], f"{nombre} cambió desde {COMMIT_DE_LA_COMPUERTA}"


def test_las_constantes_de_la_compuerta_valen_lo_que_fijo_41cf853():
    assert (cd.TOLERANCIA_CIERRE_USD, cd.UMBRAL_CORTE, cd.UMBRAL_PLANO, cd.FACTOR_MEDIANA,
            cd.UMBRAL_COMPUERTA, cd.MIN_DIAS_COMUNES) == (0.01, 0.95, 1e-4, 0.2, 0.90, 250)
