"""
tests/test_concordancia_xauusd.py
===================================
tools/concordancia_xauusd.py (brief del Admin del 06-oct-2026 (4), puntos
2, 3 y 5): ancla con horario de verano, rango, ventana de ruptura, filtro de
calendario, métricas, compuerta, y que no se lea ninguna vela después de la
de ruptura. Offline, con velas sintéticas.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

import pytest

from tools import concordancia_xauusd as cx
from tools.concordancia_xauusd import (
    UMBRAL_COMPUERTA,
    ancla_y_limite,
    concordancia,
    dia_de_fuente,
    filtrar_por_calendario,
    franja,
    primera_ruptura,
    rango_del_dia,
    tramos,
)

M5 = 300
_UTC = timezone.utc
BASE = 2650.0


def _utc(*a) -> int:
    return int(datetime(*a, tzinfo=_UTC).timestamp())


def _vela(e, o=BASE, h=None, l=None, c=None):
    c = o if c is None else c
    return {"epoch": e, "open": o, "high": max(o, c) + 0.2 if h is None else h,
            "low": min(o, c) - 0.2 if l is None else l, "close": c}


def _dia(velas: dict, d: date, *, ruptura: tuple[int, int] | None = None,
         sin: set[int] = frozenset(), despues: int = 6, rango=(BASE + 1, BASE - 1)):
    """Un día: 2 velas antes del ancla, el rango (alto/bajo), la ventana
    hasta las 12:00 con cierres adentro, y `ruptura` = (índice desde el fin
    del rango, dirección). `despues` velas más allá de las 12:00."""
    ancla, limite = ancla_y_limite(d)
    alto, bajo = rango
    e = ancla - 2 * M5
    while e < limite + despues * M5:
        k = (e - ancla) // M5
        if e in sin:
            pass
        elif 0 <= k < 3:
            velas[e] = _vela(e, BASE, h=alto if k == 1 else BASE + 0.5,
                             l=bajo if k == 2 else BASE - 0.5)
        else:
            velas[e] = _vela(e)
        e += M5
    if ruptura is not None:
        i, direccion = ruptura
        e = ancla + (3 + i) * M5
        velas[e] = _vela(e, BASE, c=alto + 0.5 if direccion > 0 else bajo - 0.5)
    return velas


# ═══ Ancla, rango y ventana ═══════════════════════════════════════════════

def test_el_ancla_respeta_el_horario_de_verano():
    assert ancla_y_limite(date(2026, 3, 27)) == (_utc(2026, 3, 27, 8), _utc(2026, 3, 27, 12))
    assert ancla_y_limite(date(2026, 3, 30)) == (_utc(2026, 3, 30, 7), _utc(2026, 3, 30, 11))
    assert ancla_y_limite(date(2026, 10, 23)) == (_utc(2026, 10, 23, 7), _utc(2026, 10, 23, 11))
    assert ancla_y_limite(date(2026, 10, 26)) == (_utc(2026, 10, 26, 8), _utc(2026, 10, 26, 12))


def test_las_constantes_del_brief():
    assert (cx.ZONA_ANCLA, cx.HORA_ANCLA, cx.MINUTO_ANCLA, cx.HORA_LIMITE, cx.VELAS_DEL_RANGO) == (
        "Europe/London", 8, 0, 12, 3)
    assert UMBRAL_COMPUERTA == 0.90 and cx.N_PEORES == 20
    assert cx.PERCENTILES == (50, 90, 99)


def test_el_rango_son_las_tres_velas_desde_el_ancla():
    d = date(2026, 6, 1)
    v = _dia({}, d, rango=(BASE + 3, BASE - 2))
    ancla, _ = ancla_y_limite(d)
    v[ancla - M5] = _vela(ancla - M5, h=9999, l=1)        # antes: no cuenta
    v[ancla + 3 * M5] = _vela(ancla + 3 * M5, h=9999, l=1)  # después: no cuenta
    r = rango_del_dia(v, ancla)
    assert r == {"alto": BASE + 3, "bajo": BASE - 2, "apertura": BASE, "d": 5 / BASE}


def test_sin_una_vela_del_rango_no_hay_rango():
    d = date(2026, 6, 1)
    ancla, _ = ancla_y_limite(d)
    assert rango_del_dia(_dia({}, d, sin={ancla + 2 * M5}), ancla) is None


@pytest.mark.parametrize("i, direccion", [(0, 1), (0, -1), (5, 1), (40, -1)])
def test_la_primera_ruptura_y_su_direccion(i, direccion):
    d = date(2026, 6, 1)
    v = _dia({}, d, ruptura=(i, direccion))
    ancla, _ = ancla_y_limite(d)
    r = dia_de_fuente(v, d)
    assert r["ruptura"] == {"direccion": direccion, "epoch": ancla + (3 + i) * M5}


def test_gana_la_primera_aunque_despues_rompa_al_otro_lado():
    d = date(2026, 6, 1)
    v = _dia({}, d, ruptura=(10, -1))
    ancla, _ = ancla_y_limite(d)
    e = ancla + 3 * M5 + 4 * M5
    v[e] = _vela(e, c=BASE + 5)
    assert dia_de_fuente(v, d)["ruptura"] == {"direccion": 1, "epoch": e}


def test_un_cierre_igual_al_borde_no_es_ruptura():
    d = date(2026, 6, 1)
    v = _dia({}, d)
    ancla, _ = ancla_y_limite(d)
    e = ancla + 3 * M5
    v[e] = _vela(e, c=BASE + 1)          # = alto
    v[e + M5] = _vela(e + M5, c=BASE - 1)  # = bajo
    assert dia_de_fuente(v, d)["ruptura"] is None


def test_la_ventana_termina_con_la_vela_que_cierra_a_las_12():
    d = date(2026, 6, 1)                     # BST: 12:00 Londres = 11:00 UTC
    ancla, limite = ancla_y_limite(d)
    ultima = limite - M5                     # 11:55 Londres
    v = _dia({}, d)
    v[ultima] = _vela(ultima, c=BASE + 5)
    assert dia_de_fuente(v, d)["ruptura"] == {"direccion": 1, "epoch": ultima}
    v = _dia({}, d)
    v[limite] = _vela(limite, c=BASE + 5)    # empieza a las 12:00: fuera
    assert dia_de_fuente(v, d)["ruptura"] is None


def test_la_ventana_empieza_al_terminar_el_rango():
    d = date(2026, 6, 1)
    ancla, limite = ancla_y_limite(d)
    v = _dia({}, d, ruptura=(0, 1))
    assert primera_ruptura(v, ancla, limite, BASE + 1, BASE - 1)["epoch"] == ancla + 3 * M5


def test_una_vela_que_falta_en_la_ventana_se_salta():
    d = date(2026, 6, 1)
    ancla, _ = ancla_y_limite(d)
    v = _dia({}, d, ruptura=(3, 1), sin={ancla + 4 * M5})
    assert dia_de_fuente(v, d)["ruptura"]["epoch"] == ancla + 6 * M5


# ═══ No se lee nada después de la vela de ruptura ═════════════════════════

class _Espia(dict):
    """Un dict que anota cada vela que alguien lee (no las claves)."""

    def __init__(self, *a):
        super().__init__(*a)
        self.leidas: list[int] = []

    def __getitem__(self, k):
        self.leidas.append(k)
        return super().__getitem__(k)

    def get(self, k, default=None):
        self.leidas.append(k)
        return super().get(k, default)

    def values(self):
        raise AssertionError("se recorrieron todas las velas")

    def items(self):
        raise AssertionError("se recorrieron todas las velas")


def _dos_fuentes(n_dias=5):
    td, dr = {}, {}
    for i in range(n_dias):
        d = date(2026, 6, 1 + i)
        _dia(td, d, ruptura=(i * 3, 1 if i % 2 else -1))
        _dia(dr, d, ruptura=(i * 3 + (1 if i == 2 else 0), 1 if i % 2 else -1))
    _dia(td, date(2026, 6, 8))     # un día sin ruptura en ninguna
    _dia(dr, date(2026, 6, 8))
    return td, dr


def test_no_se_lee_ninguna_vela_posterior_a_la_ruptura():
    td, dr = _dos_fuentes()
    espias = _Espia(td), _Espia(dr)
    concordancia(*espias)
    for serie, espia in zip((td, dr), espias):
        for i in list(range(5)) + [7]:
            d = date(2026, 6, 1 + i)
            ancla, limite = ancla_y_limite(d)
            r = dia_de_fuente(serie, d)["ruptura"]
            tope = r["epoch"] if r else limite - M5
            leidas_del_dia = [e for e in espia.leidas if ancla - 3600 <= e <= limite + 3600]
            assert leidas_del_dia and max(leidas_del_dia) <= tope, (d, max(leidas_del_dia), tope)


def test_envenenar_lo_posterior_a_la_ruptura_no_cambia_nada():
    td, dr = _dos_fuentes()
    antes = concordancia(td, dr)
    for serie in (td, dr):
        for i in list(range(5)) + [7]:
            d = date(2026, 6, 1 + i)
            ancla, limite = ancla_y_limite(d)
            r = dia_de_fuente(serie, d)["ruptura"]
            tope = r["epoch"] if r else limite - M5
            for e in list(serie):
                if tope < e < ancla + 86400 - 4 * 3600:
                    serie[e] = {"epoch": e, **{k: math.nan for k in ("open", "high", "low", "close")}}
    assert concordancia(td, dr) == antes


def test_los_peores_dias_listan_cada_fuente_hasta_su_ruptura():
    td, dr = {}, {}
    d = date(2026, 6, 2)
    _dia(td, d, ruptura=(2, 1))
    _dia(dr, d, ruptura=(9, -1))
    espias = _Espia(td), _Espia(dr)
    c = concordancia(*espias)
    assert c["compuerta"] == "roja"
    (p,) = c["peores_dias"]
    ancla, _ = ancla_y_limite(d)
    assert p["td"]["velas"][-1]["epoch"] == ancla + 5 * M5
    assert p["deriv"]["velas"][-1]["epoch"] == ancla + 12 * M5
    assert p["td"]["velas"][0]["epoch"] == ancla
    assert max(espias[0].leidas) == ancla + 5 * M5
    assert max(espias[1].leidas) == ancla + 12 * M5


# ═══ Métricas y compuerta ═════════════════════════════════════════════════

def _serie_de_dias(rupturas: list):
    v = {}
    for i, r in enumerate(rupturas):
        _dia(v, date(2026, 5, 4) + (date(2026, 5, 5) - date(2026, 5, 4)) * i, ruptura=r)
    return v


def test_fuentes_identicas_dan_cien_y_verde():
    td = _serie_de_dias([(1, 1), (2, -1), None, (5, 1)])
    c = concordancia(dict(td), dict(td))
    assert c["dias_comunes"] == 4 and c["dias_con_ruptura_en_alguna"] == 3
    assert c["pct_misma_direccion_y_vela"] == 100 and c["compuerta"] == "verde"
    assert c["abs_dhigh"]["99"] == 0 and c["pct_una_rompe_y_la_otra_no"] == 0
    assert "peores_dias" not in c


def test_la_compuerta_es_noventa_por_ciento_inclusive():
    base = [(1, 1)] * 10
    otra = [(1, 1)] * 9 + [(2, 1)]                     # 9 de 10: 90 %
    c = concordancia(_serie_de_dias(otra), _serie_de_dias(base))
    assert c["pct_misma_direccion_y_vela"] == pytest.approx(90) and c["compuerta"] == "verde"
    otra = [(1, 1)] * 8 + [(2, 1), (1, -1)]           # 8 de 10
    c = concordancia(_serie_de_dias(otra), _serie_de_dias(base))
    assert c["compuerta"] == "roja" and len(c["peores_dias"]) == 2
    assert c["pct_misma_direccion"] == pytest.approx(90)


def test_una_rompe_y_la_otra_no():
    td = _serie_de_dias([(1, 1), None, (3, -1), None])
    dr = _serie_de_dias([(1, 1), (2, 1), (3, -1), None])
    c = concordancia(td, dr)
    assert c["dias_con_ruptura_en_alguna"] == 3
    assert c["pct_una_rompe_y_la_otra_no"] == pytest.approx(100 / 3)
    assert c["pct_una_rompe_y_la_otra_no_sobre_dias_comunes"] == pytest.approx(25)


def test_los_peores_van_primero_y_son_veinte_como_mucho():
    base = [(1, 1)] * 30
    otra = [(5, 1)] * 8 + [(1, -1)] * 22                # otra vela primero en fecha
    c = concordancia(_serie_de_dias(otra), _serie_de_dias(base))
    assert len(c["peores_dias"]) == 20
    assert all(p["td"]["ruptura"]["direccion"] == -1 for p in c["peores_dias"])


def test_deltas_en_precio_y_en_fraccion_de_d():
    d = date(2026, 6, 1)
    td = _dia({}, d, rango=(BASE + 1.5, BASE - 1))
    dr = _dia({}, d, rango=(BASE + 1, BASE - 1))
    c = concordancia(td, dr)
    assert c["abs_dhigh"]["50"] == pytest.approx(0.5) and c["abs_dlow"]["50"] == pytest.approx(0)
    assert c["abs_dhigh_en_fraccion_de_d"]["50"] == pytest.approx(0.5 / 2)


def test_un_dia_sin_rango_en_una_fuente_no_es_comun():
    d1, d2 = date(2026, 6, 1), date(2026, 6, 2)
    td = _dia(_dia({}, d1, ruptura=(1, 1)), d2, ruptura=(1, 1))
    a2, _ = ancla_y_limite(d2)
    dr = _dia(_dia({}, d1, ruptura=(1, 1)), d2, ruptura=(1, 1), sin={a2})
    assert concordancia(td, dr)["dias_comunes"] == 1


def test_sin_dias_comunes_la_compuerta_no_se_inventa():
    assert concordancia({}, {})["compuerta"] == "sin datos"


# ═══ Calendario ═══════════════════════════════════════════════════════════

HORARIO = {"trading_days": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri"],
           "aperturas": ["00:00:00", "22:00:00"], "cierres": ["21:00:00", "23:59:59"]}


def test_tramos_cierra_el_dia_a_las_24():
    assert tramos(HORARIO) == [(0, 21 * 3600), (22 * 3600, 86400)]
    assert tramos({"aperturas": ["--"], "cierres": ["--"]}) == []


@pytest.mark.parametrize("cuando, esperado", [
    ((2026, 10, 7, 10, 0), None),                 # miércoles
    ((2026, 10, 7, 21, 0), "pausa_diaria"),
    ((2026, 10, 7, 20, 55), None),                # cierra 21:00
    ((2026, 10, 7, 22, 0), None),
    ((2026, 10, 10, 12, 0), "fin_de_semana"),     # sábado
    ((2026, 10, 9, 21, 30), "fin_de_semana"),     # viernes después del cierre
    ((2026, 10, 11, 21, 0), "fin_de_semana"),     # domingo antes de abrir
])
def test_franja(cuando, esperado):
    horario = {**HORARIO, "aperturas": ["00:00:00", "22:00:00"]}
    if cuando[:3] == (2026, 10, 11):
        horario = {**HORARIO, "aperturas": ["22:00:00"], "cierres": ["23:59:59"]}
    assert franja(_utc(*cuando), horario) == esperado


def test_un_sabado_en_trading_days_cotiza():
    con_sabado = {**HORARIO, "trading_days": HORARIO["trading_days"] + ["Sat"]}
    assert franja(_utc(2026, 10, 10, 12, 0), con_sabado) is None


def test_una_vela_que_cruza_un_cierre_queda_fuera():
    raro = {**HORARIO, "cierres": ["20:58:00", "23:59:59"]}
    assert franja(_utc(2026, 10, 7, 20, 55), raro) == "pausa_diaria"
    assert franja(_utc(2026, 10, 7, 20, 50), raro) is None


def test_un_dia_fuera_de_trading_days_es_fin_de_semana():
    assert franja(_utc(2026, 10, 11, 23, 0), {**HORARIO, "trading_days": ["Mon"]}) == "fin_de_semana"


def test_filtro_cuenta_por_franja_y_detecta_feriados():
    deriv = [_utc(2026, 10, 5, 10), _utc(2026, 10, 7, 10), _utc(2026, 10, 9, 10)]
    td = [_utc(2026, 10, 5, 10),        # dentro
          _utc(2026, 10, 5, 21, 10),    # pausa
          _utc(2026, 10, 6, 10),        # martes sin velas de Deriv: feriado
          _utc(2026, 10, 7, 10),        # dentro
          _utc(2026, 10, 9, 10),        # dentro
          _utc(2026, 10, 2, 10),        # antes del tramo de Deriv: no cuenta
          _utc(2026, 10, 10, 10)]       # después del tramo: no cuenta
    f = filtrar_por_calendario(td, HORARIO, deriv)
    assert f["velas_td_en_el_tramo"] == 5
    assert f["velas_td_dentro"] == 3
    assert f["fuera_por_franja"] == {"fin_de_semana": 0, "pausa_diaria": 1, "feriado": 1}
    assert f["feriados"] == ["2026-10-06"]
    assert sorted(f["epocas_dentro"]) == [td[0], td[3], td[4]]


def test_filtro_sin_horario_o_sin_deriv_no_aplica():
    assert filtrar_por_calendario([1], None, [1])["aplica"] is False
    assert filtrar_por_calendario([1], HORARIO, [])["aplica"] is False


# ═══ El informe entero ════════════════════════════════════════════════════

def test_el_informe_lee_metrics_y_publica_cuatro_bloques(tmp_path, monkeypatch, capsys):
    import json

    from ingestion.velas import _append, ruta_serie
    from ingestion.velas_intradia import ruta_calendario
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(tmp_path))
    td = _serie_de_dias([(1, 1), (2, -1), (4, 1)])
    dr = _serie_de_dias([(1, 1), (2, -1), (6, 1)])
    for nombre, serie in (("td_XAUUSD", td), ("frxXAUUSD", dr)):
        _append(ruta_serie(nombre, M5), [{"epoch": e, **{k: float(v[k]) for k in
                                         ("open", "high", "low", "close")}}
                                        for e, v in sorted(serie.items())])
    horario = {"trading_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
               "aperturas": ["00:00:00"], "cierres": ["23:59:59"]}
    _append(ruta_calendario(), [{"fecha_servidor": "2026-10-07", "sha256": "ab" * 32,
                                 "horario": horario, "crudo": "{}"}])
    assert cx.main([]) == 0
    lineas = capsys.readouterr().out.strip().splitlines()
    assert [l for l in lineas if l.startswith("===")] == [
        "=== DATOS ===", "=== CALENDARIO ===", "=== CONCORDANCIA ===", "=== COMPUERTA ==="]
    bloques = [json.loads(l) for l in lineas if not l.startswith("===")]
    datos, cal, conc, comp = bloques
    assert datos["td_XAUUSD"]["velas"] == len(td) and datos["frxXAUUSD"]["sha256"]
    assert cal["sha256_trading_times"] == "ab" * 32 and cal["aplica"] is True
    assert "epocas_dentro" not in cal
    assert conc["dias_comunes"] == 3
    assert comp["compuerta"] == "roja" and len(comp["peores_dias"]) == 1
    assert comp["pct_misma_direccion_y_vela"] == pytest.approx(200 / 3)


def test_el_informe_filtra_twelvedata_antes_de_comparar(tmp_path, monkeypatch, capsys):
    """Una ruptura de TwelveData en una hora en que Deriv no cotiza no
    cuenta: el filtro se aplica antes de la concordancia."""
    import json

    from ingestion.velas import _append, ruta_serie
    from ingestion.velas_intradia import ruta_calendario
    monkeypatch.setenv("SPEL_DRIVE_ROOT", str(tmp_path))
    d = date(2026, 6, 1)                          # lunes, BST: ancla 07:00 UTC
    ancla, _ = ancla_y_limite(d)
    dr = _dia({}, d, ruptura=(6, 1))
    td = _dia({}, d, ruptura=(6, 1))
    pausa = ancla + 4 * M5                        # 07:20 UTC
    td[pausa] = _vela(pausa, c=BASE - 5)          # rompe abajo antes, en la pausa
    for nombre, serie in (("td_XAUUSD", td), ("frxXAUUSD", dr)):
        _append(ruta_serie(nombre, M5), [{"epoch": e, **{k: float(v[k]) for k in
                                         ("open", "high", "low", "close")}}
                                        for e, v in sorted(serie.items())])
    horario = {"trading_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
               "aperturas": ["00:00:00", "07:25:00"], "cierres": ["07:20:00", "23:59:59"]}
    _append(ruta_calendario(), [{"fecha_servidor": "2026-06-01", "sha256": "cd" * 32,
                                 "horario": horario, "crudo": "{}"}])
    cx.main([])
    lineas = [l for l in capsys.readouterr().out.splitlines() if not l.startswith("===")]
    cal, comp = json.loads(lineas[1]), json.loads(lineas[3])
    assert cal["fuera_por_franja"]["pausa_diaria"] == 1
    assert comp["compuerta"] == "verde" and comp["pct_misma_direccion_y_vela"] == 100
