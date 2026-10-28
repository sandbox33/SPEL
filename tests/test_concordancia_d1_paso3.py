"""
tests/test_concordancia_d1_paso3.py
=====================================
tools/concordancia_d1_paso3.py (brief del Admin 16a, puntos 1 a 3): el rango
y la señal del paso 3. Offline, con series sintéticas: ningún test lee
`metrics/velas/` reales (candado 1).
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

import pytest

from ingestion.velas import _append, leer_velas, ruta_serie
from tools import concordancia_d1 as cd
from tools import concordancia_d1_paso3 as p3

D, H, M5 = 86400, 3600, 300
_UTC = timezone.utc
#: Lunes 2-jun-2025.
INI = date(2025, 6, 2)

HORARIO_DERIV = {"trading_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
                 "aperturas": ["00:00:00", "22:00:00"], "cierres": ["21:00:00", "23:59:59"]}


@pytest.fixture(autouse=True)
def _drive(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(tmp_path))


def _ep(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=_UTC).timestamp())


def _dias(n: int, desde: date = INI) -> list[date]:
    return [desde + timedelta(days=k) for k in range(n)]


# ═══ Las constantes ═══════════════════════════════════════════════════════

def test_las_constantes_del_paso_3_valen_lo_que_aprobo_el_admin():
    """Brief 16a, puntos 2 y 3 (aprobadas el 26-oct)."""
    assert (p3.TOL_RANGO, p3.UMBRAL_RANGO_PCT, p3.UMBRAL_SENAL_PCT) == (0.10, 90, 90)
    assert p3.LOOKBACKS_SENAL == (10, 20, 40) and p3.MIN_DIAS_CON_RUPTURA == 10


# ═══ Rango ════════════════════════════════════════════════════════════════

def _rangos(dias: list[date], hl) -> dict[int, tuple[float, float]]:
    return {_ep(d): hl(k) for k, d in enumerate(dias)}


@pytest.mark.parametrize("dh, dl, coincide", [
    (1.0, 0.0, True),            # 0,1 × 10 = 1,0 exacto: el borde entra
    (-1.0, 0.0, True),
    (0.0, 1.0, True),
    (1.0000001, 0.0, False),
    (0.0, -1.0000001, False),
    (1.0000001, 1.0, False),     # el máximo no coincide: el día no coincide
])
def test_maximo_y_minimo_coinciden_hasta_un_decimo_del_rango_de_deriv(dh, dl, coincide):
    dias = _dias(1)
    dr = _rangos(dias, lambda k: (110.0, 100.0))                  # rango 10
    td = _rangos(dias, lambda k: (110.0 + dh, 100.0 + dl))
    r = p3.paso_3_rango(td, dr, dias)
    assert r["coinciden_los_dos"] == int(coincide) and r["dias_evaluados"] == 1


def test_el_maximo_y_el_minimo_se_cuentan_por_separado():
    dias = _dias(3)
    dr = _rangos(dias, lambda k: (110.0, 100.0))
    td = _rangos(dias, lambda k: [(110.0, 100.0), (112.0, 100.0), (110.0, 98.0)][k])
    r = p3.paso_3_rango(td, dr, dias)
    assert (r["coincide_maximo"], r["coincide_minimo"], r["coinciden_los_dos"]) == (2, 2, 1)


def test_un_dia_con_rango_cero_en_deriv_queda_fuera_y_se_cuenta():
    dias = _dias(11)
    dr = _rangos(dias, lambda k: (105.0, 105.0) if k == 4 else (110.0, 100.0))
    td = _rangos(dias, lambda k: (200.0, 50.0) if k == 4 else (110.0, 100.0))
    r = p3.paso_3_rango(td, dr, dias)
    assert (r["dias_comunes"], r["dias_rango_cero"], r["dias_evaluados"]) == (11, 1, 10)
    assert r["fechas_rango_cero"] == [dias[4].isoformat()]
    assert r["coinciden_los_dos"] == 10 and r["rango"] == "verde"


@pytest.mark.parametrize("malos, rango", [(0, "verde"), (1, "verde"), (2, "roja")])
def test_el_rango_pide_el_90_por_ciento(malos, rango):
    """10 días evaluados: 9 son el 90 % justo."""
    dias = _dias(10)
    dr = _rangos(dias, lambda k: (110.0, 100.0))
    td = _rangos(dias, lambda k: (120.0, 100.0) if k < malos else (110.0, 100.0))
    r = p3.paso_3_rango(td, dr, dias)
    assert r["rango"] == rango and r["coinciden_los_dos"] == 10 - malos
    assert r["motivos"] == ([] if rango == "verde" else
                            ["coinciden máximo y mínimo en 8 de 10 días, menos que 90 %"])


@pytest.mark.parametrize("malos, rango", [(10, "verde"), (11, "roja")])
def test_el_90_por_ciento_no_es_el_89(malos, rango):
    """100 días: 89 coincidentes no llegan."""
    dias = _dias(100)
    dr = _rangos(dias, lambda k: (110.0, 100.0))
    td = _rangos(dias, lambda k: (120.0, 100.0) if k < malos else (110.0, 100.0))
    assert p3.paso_3_rango(td, dr, dias)["rango"] == rango


def test_sin_dias_con_rango_el_rango_es_rojo():
    dias = _dias(3)
    r = p3.paso_3_rango(_rangos(dias, lambda k: (1.0, 1.0)), _rangos(dias, lambda k: (1.0, 1.0)), dias)
    assert r["rango"] == "roja" and r["motivos"] == ["no hay días con rango en Deriv"]
    assert r["fraccion"] is None and r["dias_rango_cero"] == 3


def test_un_dia_comun_sin_maximo_y_minimo_es_un_error():
    dias = _dias(2)
    with pytest.raises(ValueError, match="sin máximo y mínimo"):
        p3.paso_3_rango(_rangos(dias[:1], lambda k: (2.0, 1.0)),
                        _rangos(dias, lambda k: (2.0, 1.0)), dias)


def test_el_informativo_del_rango():
    dias = _dias(4)
    dr = _rangos(dias, lambda k: (110.0, 100.0))
    td = _rangos(dias, lambda k: (110.0 + k, 100.0))
    i = p3.paso_3_rango(td, dr, dias)["informativo"]
    assert i["abs_dmaximo_en_fraccion_del_rango"]["50"] == pytest.approx(0.15)
    assert i["abs_dminimo_en_fraccion_del_rango"] == {"50": 0.0, "90": 0.0}


# ═══ Señal ════════════════════════════════════════════════════════════════

def test_la_ruptura_es_estricta_y_mira_los_l_dias_anteriores():
    dias = _dias(4)
    maximos = dict(zip(dias, [10.0, 12.0, 11.0, 0.0]))
    cierres = dict(zip(dias, [0.0, 0.0, 12.0, 12.5]))
    assert p3.rupturas(cierres, maximos, dias, 2) == {dias[3]}, "12 no supera 12; 12,5 sí"
    assert p3.rupturas(cierres, maximos, dias, 1) == {dias[3]}
    assert p3.rupturas(cierres, maximos, dias, 3) == {dias[3]}
    assert p3.rupturas(cierres, maximos, dias, 4) == set(), "sin L días antes no hay ruptura"
    tres = _dias(3)
    assert p3.rupturas(dict(zip(tres, [0.0, 0.0, 15.0])), dict(zip(tres, [20.0, 10.0, 0.0])),
                       tres, 1) == {tres[2]}, "con L = 1 no mira el máximo de dos días antes"


def test_cada_fuente_rompe_contra_sus_propios_maximos():
    """Los mismos cierres; los máximos de TwelveData, 5 más arriba: TwelveData
    no rompe nunca y Deriv rompe todos los días."""
    dias = _dias(60)
    c, hl = _tendencia(60, dias)
    td_alto = {e: (h + 5.0, l) for e, (h, l) in hl.items()}
    s = p3.paso_3_senal(c, td_alto, c, hl, dias)
    assert [(f["solo_twelvedata"], f["solo_deriv"], f["ruptura_en_las_dos"])
            for f in s["por_lookback"]] == [(0, 50, 0), (0, 40, 0), (0, 20, 0)]
    assert s["senal"] == "roja"


def test_la_ventana_son_los_dias_comunes_y_no_las_fechas():
    """Un día que no es común no entra en los L anteriores: con L = 1, el
    día 3 se compara con el día 1, aunque el día 2 exista en la serie."""
    dias = _dias(4)
    comunes = [dias[0], dias[1], dias[3]]
    maximos = dict(zip(dias, [10.0, 10.0, 99.0, 0.0]))
    cierres = dict(zip(dias, [0.0, 0.0, 0.0, 11.0]))
    assert p3.rupturas(cierres, maximos, comunes, 1) == {dias[3]}
    assert p3.rupturas(cierres, maximos, dias, 1) == set()


def _tendencia(n: int, dias: list[date]) -> tuple[dict, dict]:
    """Cierres y máximos que suben: cada día rompe."""
    c = {_ep(d): 100.0 + k for k, d in enumerate(dias[:n])}
    hl = {_ep(d): (100.0 + k + 0.5, 99.0 + k) for k, d in enumerate(dias[:n])}
    return c, hl


def test_con_las_dos_fuentes_iguales_la_senal_es_verde_en_cada_l():
    dias = _dias(60)
    c, hl = _tendencia(60, dias)
    s = p3.paso_3_senal(c, hl, c, hl, dias)
    assert s["senal"] == "verde"
    assert [(f["lookback"], f["dias_evaluados"], f["ruptura_en_alguna"], f["fraccion"])
            for f in s["por_lookback"]] == [(10, 50, 50, 1.0), (20, 40, 40, 1.0), (40, 20, 20, 1.0)]


def _con_rupturas(dias: list[date], rompe: set[int]) -> tuple[dict, dict]:
    """Plano en 100 (máximo 100, cierre 99) salvo los índices de `rompe`, que
    cierran en 101 y vuelven: el máximo de esos días queda en 100, así cada
    ruptura no tapa a la siguiente."""
    c = {_ep(d): (101.0 if k in rompe else 99.0) for k, d in enumerate(dias)}
    hl = {_ep(d): (100.0, 98.0) for d in dias}
    return c, hl


@pytest.mark.parametrize("en_td, en_dr, estado", [
    (10, 10, "verde"),          # 10 de 10
    (10, 9, "verde"),           # 9 de 10: el 90 % justo
    (10, 8, "roja"),            # 8 de 10
    (9, 9, "sin potencia"),     # 9 días con ruptura en alguna: menos de 10
])
def test_cada_l_pide_el_90_por_ciento_con_al_menos_10_dias_de_ruptura(en_td, en_dr, estado):
    dias = _dias(80)
    indices = list(range(45, 45 + en_td))           # después del L = 40
    c_td, hl = _con_rupturas(dias, set(indices))
    c_dr, _ = _con_rupturas(dias, set(indices[:en_dr]))
    s = p3.paso_3_senal(c_td, hl, c_dr, hl, dias)
    assert {f["estado"] for f in s["por_lookback"]} == {estado}
    f = s["por_lookback"][0]
    assert (f["ruptura_en_alguna"], f["ruptura_en_las_dos"], f["solo_twelvedata"],
            f["solo_deriv"]) == (en_td, en_dr, en_td - en_dr, 0)
    assert s["senal"] == estado


def test_una_ruptura_de_una_sola_fuente_cuenta_en_alguna_y_no_en_las_dos():
    """5 días rompen solo en TwelveData, 5 solo en Deriv y 10 en las dos."""
    dias = _dias(90)
    c_td, hl = _con_rupturas(dias, set(range(45, 60)))
    c_dr, _ = _con_rupturas(dias, set(range(50, 65)))
    f = p3.paso_3_senal(c_td, hl, c_dr, hl, dias)["por_lookback"][0]
    assert (f["ruptura_en_alguna"], f["ruptura_en_las_dos"], f["solo_twelvedata"],
            f["solo_deriv"], f["estado"]) == (20, 10, 5, 5, "roja")


def test_una_l_roja_basta_aunque_otra_sea_verde():
    """Con L = 10, 20 de 22 días (90,9 %); con L = 20, que no ve los días 10 a
    19, 10 de 12 (83,3 %)."""
    dias = _dias(70)
    c_td, hl = _con_rupturas(dias, set(range(10, 32)))
    c_dr, _ = _con_rupturas(dias, set(range(10, 30)))
    s = p3.paso_3_senal(c_td, hl, c_dr, hl, dias)
    assert [f["estado"] for f in s["por_lookback"]] == ["verde", "roja", "sin potencia"]
    assert s["senal"] == "roja"


def test_una_l_sin_potencia_no_decide_y_una_roja_si():
    """Las rupturas caen entre el día 20 y el 39: con L = 40 no hay ninguna."""
    dias = _dias(70)
    c_td, hl = _con_rupturas(dias, set(range(20, 40)))
    s = p3.paso_3_senal(c_td, hl, c_td, hl, dias)
    assert [f["estado"] for f in s["por_lookback"]] == ["verde", "verde", "sin potencia"]
    assert s["senal"] == "verde"
    c_dr, _ = _con_rupturas(dias, set(range(20, 30)))         # 10 de 20
    s = p3.paso_3_senal(c_td, hl, c_dr, hl, dias)
    assert [f["estado"] for f in s["por_lookback"]] == ["roja", "roja", "sin potencia"]
    assert s["senal"] == "roja"


def test_si_ninguna_l_decide_la_senal_queda_sin_potencia():
    dias = _dias(30)
    c, hl = _con_rupturas(dias, set())
    s = p3.paso_3_senal(c, hl, c, hl, dias)
    assert [f["estado"] for f in s["por_lookback"]] == ["sin potencia"] * 3
    assert s["senal"] == "sin potencia"


# ═══ El oro desde el M5 ═══════════════════════════════════════════════════

def _semana_m5() -> dict[int, tuple[float, float]]:
    """Lunes a viernes de M5 desde el lunes INI, hasta las 23:55, y una vela
    el sábado que deja cerrado el viernes. Máximo = 100 + hora, mínimo = 50 +
    hora; la vela del viernes a las 22:00 tiene un máximo de 999 y un mínimo
    de 1."""
    m5 = {}
    for k in range(5):
        for e in range(_ep(INI) + k * D, _ep(INI) + (k + 1) * D, M5):
            hora = (e % D) / H
            m5[e] = (100.0 + hora, 50.0 + hora)
    viernes_noche = _ep(INI) + 4 * D + 22 * H
    m5[viernes_noche] = (999.0, 1.0)
    m5[_ep(INI) + 5 * D] = (7.0, 6.0)
    return m5


def test_el_rango_del_oro_usa_las_mismas_velas_que_su_cierre():
    m5 = _semana_m5()
    for horario in (None, HORARIO_DERIV):
        r = p3.rangos_desde_m5(m5, horario)
        c = cd.diarias_desde_m5({e: 0.0 for e in m5}, horario=horario)
        assert list(r) == list(c), "los mismos días que el cierre"
    viernes = _ep(INI) + 4 * D
    con, sin = p3.rangos_desde_m5(m5, HORARIO_DERIV), p3.rangos_desde_m5(m5)
    assert sin[viernes] == (999.0, 1.0), "sin el horario entra la noche del viernes"
    assert con[viernes] == (100.0 + 20 + 55 / 60, 50.0), "con el horario, hasta la que termina a las 21:00"
    assert con[_ep(INI)] == (100.0 + 23 + 55 / 60, 50.0), "de lunes a jueves, el día entero"


def test_el_rango_del_oro_no_toma_el_dia_abierto():
    m5 = {_ep(INI) + 10 * H: (2.0, 1.0), _ep(INI) + D + 10 * H: (4.0, 3.0)}
    assert p3.rangos_desde_m5(m5) == {_ep(INI): (2.0, 1.0)}
    assert p3.rangos_desde_m5({}) == {}


# ═══ El paso 3 y la compuerta del activo ══════════════════════════════════

def test_los_dias_comunes_son_los_del_paso_2():
    """En el oro: un fin de semana en las dos fuentes y un hueco en cada una."""
    dias = [d for d in _dias(400) if d.weekday() < 5]
    td = {_ep(d): 100.0 + k for k, d in enumerate(dias) if k != 30}
    dr = {_ep(d): 100.0 + k for k, d in enumerate(dias) if k != 77}
    sabado = _ep(dias[10] + timedelta(days=(5 - dias[10].weekday())))
    td[sabado] = dr[sabado] = 1.0
    comunes = p3.dias_comunes(td, dr, "habil")
    assert len(comunes) == cd.paso_2(td, dr, "habil")["dias_comunes"] == len(dias) - 2


def _activo(n: int, *, desplazar_maximo: float = 0.0, desplazar_minimo: float = 0.0, rompe=None):
    dias = _dias(n)
    if rompe is None:
        c, hl = _tendencia(n, dias)
    else:
        c, hl = _con_rupturas(dias, rompe)
    td_hl = {e: (h + desplazar_maximo, l + desplazar_minimo) for e, (h, l) in hl.items()}
    return c, td_hl, c, hl


def test_el_paso_3_es_verde_si_el_rango_y_la_senal_lo_son():
    p = p3.paso_3(*_activo(60), "continuo")
    assert (p["rango"]["rango"], p["senal"]["senal"], p["paso_3"]) == ("verde", "verde", "verde")
    assert p["ventana"] == [INI.isoformat(), (INI + timedelta(days=59)).isoformat()]


def test_un_rango_rojo_hace_rojo_el_paso_3_aunque_la_senal_sea_verde():
    """Los mínimos de TwelveData, 5 más abajo: la señal, que mira cierres y
    máximos, es la misma."""
    p = p3.paso_3(*_activo(60, desplazar_minimo=-5.0), "continuo")
    assert (p["rango"]["rango"], p["senal"]["senal"], p["paso_3"]) == ("roja", "verde", "roja")


def test_una_senal_roja_hace_rojo_el_paso_3_aunque_el_rango_sea_verde():
    c, hl = _con_rupturas(_dias(70), set(range(20, 40)))
    c_dr, _ = _con_rupturas(_dias(70), set(range(20, 30)))
    p = p3.paso_3(c, hl, c_dr, hl, "continuo")
    assert (p["rango"]["rango"], p["senal"]["senal"], p["paso_3"]) == ("verde", "roja", "roja")


def test_una_senal_sin_potencia_no_deja_verde_el_paso_3():
    p = p3.paso_3(*_activo(30, rompe=set()), "continuo")
    assert (p["rango"]["rango"], p["senal"]["senal"], p["paso_3"]) == (
        "verde", "sin potencia", "sin potencia")


@pytest.mark.parametrize("paso_2, paso_3, compuerta", [
    ("verde", "verde", "verde"),
    ("verde", "roja", "roja"),
    ("verde", "sin potencia", "sin potencia"),
    ("roja", "verde", "roja"),
    ("roja", "roja", "roja"),
    ("roja", "sin potencia", "roja"),
])
def test_un_activo_queda_verde_solo_con_el_paso_2_y_el_paso_3(paso_2, paso_3, compuerta):
    assert p3.compuerta_del_activo(paso_2, paso_3) == compuerta


# ═══ Lectores ═════════════════════════════════════════════════════════════

def test_rangos_lee_el_maximo_y_el_minimo_de_una_serie_leida():
    e = _ep(INI)
    _append(ruta_serie("td_PRUEBA", D), [{"epoch": e, "open": 2.0, "high": 3.0, "low": 1.0,
                                          "close": 2.5}])
    assert p3.rangos(leer_velas("td_PRUEBA", D)) == {e: (3.0, 1.0)}
    assert p3.rangos(leer_velas("td_NADA", D)) == {}


def test_rangos_de_filas_lee_el_maximo_y_el_minimo_de_lo_que_llega():
    e = _ep(INI)
    filas = {e: {"epoch": e, "open": 2.0, "high": 3.0, "low": 1.0, "close": 2.5}}
    assert p3.rangos_de_filas(filas) == {e: (3.0, 1.0)}


def test_nada_de_esto_escribe(tmp_path):
    dias = _dias(60)
    c, hl = _tendencia(60, dias)
    p3.paso_3(c, hl, c, hl, "continuo")
    p3.rangos_desde_m5(_semana_m5(), HORARIO_DERIV)
    assert list(tmp_path.rglob("*")) == []


def test_el_rango_es_finito_aunque_un_maximo_sea_enorme():
    dias = _dias(1)
    r = p3.paso_3_rango(_rangos(dias, lambda k: (1e300, 1.0)),
                        _rangos(dias, lambda k: (2.0, 1.0)), dias)
    assert r["coinciden_los_dos"] == 0
    assert math.isfinite(r["informativo"]["abs_dmaximo_en_fraccion_del_rango"]["50"])
