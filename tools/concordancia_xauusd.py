"""
tools/concordancia_xauusd.py
============================
¿El feed de TwelveData ve el mismo rango de apertura del oro, y la misma
primera ruptura, que Deriv? Brief del Admin del 06-oct-2026 (4), puntos 2,
3 y 4.

══ LO QUE ESTE MÓDULO NO HACE (punto 0) ══

No calcula ningún desenlace: ni P&L, ni R, ni aciertos, ni el precio
después de la entrada. Por día y por fuente lee las tres velas del rango y,
después, vela a vela hasta la PRIMERA ruptura, y ahí se detiene. Las velas
de cada serie solo se leen en `rangos_de_apertura`, `rango_del_dia`,
`primera_ruptura` y `velas_hasta`; las tres últimas paran en la vela de
ruptura. tests/test_guarda_desenlaces.py lo verifica por AST, y
tests/test_concordancia_xauusd.py con una serie espía.

══ DEFINICIONES (punto 3) ══

  · Ancla: 08:00 Europe/London, con zoneinfo (horario de verano incluido).
  · Rango: las 3 velas M5 desde el ancla. high y low del rango; d = (high −
    low) / open de la primera vela.
  · Primera ruptura: el primer CIERRE M5 fuera del rango (arriba: +1,
    abajo: −1) entre el fin del rango y las 12:00 de Londres; su época es
    la de esa vela.
  · Compuerta, fijada ANTES de medir (UMBRAL_COMPUERTA): misma dirección Y
    misma vela en ≥ 90 % de los días con ruptura en alguna de las dos
    fuentes.

[INTERPRETACIÓN] Lo que el brief no fija:
  · "Entre el fin del rango y las 12:00": velas que empiezan en el fin del
    rango y CIERRAN a las 12:00 a más tardar (la última empieza 11:55).
  · Una vela que falta en la ventana se salta: una fuente puede romper en
    una vela que en la otra no existe, y eso se mide como discordancia.
  · |Δhigh| y |Δlow| en fracción de d: |Δ| / (high − low) del rango de
    Deriv, que es donde se ejecuta. Equivale a |Δ|/open/d.
  · "Una fuente rompe y la otra no": sobre los días con ruptura en alguna,
    la misma base que la compuerta. Se informa también sobre todos los
    días comunes.
  · Los 20 peores días: primero los de dirección distinta o ruptura en una
    sola fuente, después los de misma dirección y otra vela; dentro de cada
    grupo, por distancia entre las velas de ruptura y por el mayor |Δ| en
    fracción de d. Cada fuente lista sus velas desde el ancla hasta SU
    vela de ruptura (o hasta las 12:00 si no rompió).

══ CALENDARIO (punto 2) ══

TwelveData se filtra a las velas en que Deriv cotiza frxXAUUSD, con el
horario de `trading_times` (ingestion/velas_intradia.py lo guarda crudo con
su sha256). Las que quedan fuera se cuentan por franja:
  · fin_de_semana: un día que no está en `trading_days`; viernes desde el
    primer cierre del día (el
    horario de una fecha hábil tiene una pausa, y el viernes ese cierre es
    el del fin de semana); domingo antes de la primera apertura.
  · pausa_diaria: un día hábil, fuera de los tramos open/close.
  · feriado: dentro del horario, en un día en que Deriv no tiene NINGUNA
    vela (solo dentro del tramo de fechas que cubre Deriv).
El horario es el de la fecha en que se pidió: se aplica a todos los días.

Uso:
    python tools/concordancia_xauusd.py      # lee metrics/ y publica el informe
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.velas import leer_velas, ruta_serie  # noqa: E402
from ingestion.velas_intradia import (  # noqa: E402
    GRANULARIDAD_M5,
    SERIE_TD,
    SIMBOLO_DERIV,
    _leer_filas,
    publicar,
    ruta_calendario,
    sha256_de_archivo,
)

ZONA_ANCLA = "Europe/London"
HORA_ANCLA, MINUTO_ANCLA = 8, 0
HORA_LIMITE = 12
VELAS_DEL_RANGO = 3

#: Fijada antes de medir (brief 06-oct-2026 (4), punto 3).
UMBRAL_COMPUERTA = 0.90
PERCENTILES: tuple[int, ...] = (50, 90, 99)
N_PEORES = 20

_DIAS_SEMANA = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


# ═══ Lectores de velas: los ÚNICOS que tocan precios ══════════════════════

def rangos_de_apertura(velas_m5: dict[int, dict], zona: str, hora: int, minuto: int,
                       *, n: int = VELAS_DEL_RANGO) -> tuple[list[dict], int]:
    """Portado de la sonda §0.A-3b (5c), que la importa de acá. Por cada día
    local entre la primera y la última vela: el rango de las n velas M5
    que empiezan en el ancla. Lee SOLO esas n velas: ni un precio de
    después. Devuelve (días con rango, días sin las n velas). Un ancla fuera
    de la serie (antes de la primera vela, o con la tercera vela después de
    la última) no cuenta como faltante."""
    if not velas_m5:
        return [], 0
    tz = ZoneInfo(zona)
    desde = datetime.fromtimestamp(min(velas_m5), tz).date()
    hasta = datetime.fromtimestamp(max(velas_m5), tz).date()
    primera, ultima = min(velas_m5), max(velas_m5)
    dias, faltan = [], 0
    d = desde
    while d <= hasta:
        ancla = int(datetime(d.year, d.month, d.day, hora, minuto, tzinfo=tz).timestamp())
        epocas = [ancla + k * GRANULARIDAD_M5 for k in range(n)]
        if ancla < primera or epocas[-1] > ultima:
            pass   # fuera de la serie: ni día ni faltante
        elif all(e in velas_m5 for e in epocas):
            vs = [velas_m5[e] for e in epocas]
            alto = max(float(v["high"]) for v in vs)
            bajo = min(float(v["low"]) for v in vs)
            precio = float(vs[0]["open"])
            dias.append({"fecha": d.isoformat(), "ancla": ancla,
                         "rango_fraccion": (alto - bajo) / precio})
        else:
            faltan += 1
        d += timedelta(days=1)
    return dias, faltan


def rango_del_dia(velas: dict[int, dict], ancla: int, *,
                  n: int = VELAS_DEL_RANGO) -> Optional[dict]:
    """alto, bajo, apertura (open de la primera vela) y d del rango; None si
    falta una de las n velas. Lee solo esas n."""
    epocas = [ancla + k * GRANULARIDAD_M5 for k in range(n)]
    if not all(e in velas for e in epocas):
        return None
    vs = [velas[e] for e in epocas]
    alto = max(float(v["high"]) for v in vs)
    bajo = min(float(v["low"]) for v in vs)
    apertura = float(vs[0]["open"])
    return {"alto": alto, "bajo": bajo, "apertura": apertura, "d": (alto - bajo) / apertura}


def primera_ruptura(velas: dict[int, dict], ancla: int, limite: int, alto: float,
                    bajo: float, *, n: int = VELAS_DEL_RANGO) -> Optional[dict]:
    """La primera vela M5 que cierra fuera de [bajo, alto], entre el fin del
    rango y `limite` (la vela tiene que cerrar a más tardar en `limite`).
    Devuelve {"direccion": ±1, "epoch"} o None, y deja de leer en ella."""
    e = ancla + n * GRANULARIDAD_M5
    while e + GRANULARIDAD_M5 <= limite:
        if e in velas:
            cierre = float(velas[e]["close"])
            if cierre > alto:
                return {"direccion": 1, "epoch": e}
            if cierre < bajo:
                return {"direccion": -1, "epoch": e}
        e += GRANULARIDAD_M5
    return None


def velas_hasta(velas: dict[int, dict], ancla: int, tope: int) -> list[dict]:
    """Las velas desde el ancla hasta la época `tope` inclusive, para listar
    un día. Quien llama pasa como tope la vela de ruptura de ESA fuente (o
    la última de la ventana si no rompió)."""
    out = []
    e = ancla
    while e <= tope:
        if e in velas:
            out.append({"epoch": e, **{k: velas[e][k] for k in ("open", "high", "low", "close")}})
        e += GRANULARIDAD_M5
    return out


# ═══ Días ═════════════════════════════════════════════════════════════════

def ancla_y_limite(d: date) -> tuple[int, int]:
    tz = ZoneInfo(ZONA_ANCLA)
    ancla = int(datetime(d.year, d.month, d.day, HORA_ANCLA, MINUTO_ANCLA, tzinfo=tz).timestamp())
    limite = int(datetime(d.year, d.month, d.day, HORA_LIMITE, 0, tzinfo=tz).timestamp())
    return ancla, limite


def dias_locales(epocas: Sequence[int]) -> list[date]:
    """Los días de Londres entre la primera y la última época."""
    if not epocas:
        return []
    tz = ZoneInfo(ZONA_ANCLA)
    d, hasta = (datetime.fromtimestamp(min(epocas), tz).date(),
                datetime.fromtimestamp(max(epocas), tz).date())
    out = []
    while d <= hasta:
        out.append(d)
        d += timedelta(days=1)
    return out


def dia_de_fuente(velas: dict[int, dict], d: date) -> dict:
    """Rango y primera ruptura de una fuente en un día."""
    ancla, limite = ancla_y_limite(d)
    r = rango_del_dia(velas, ancla)
    if r is None:
        return {"rango": None, "ruptura": None, "ancla": ancla, "limite": limite}
    return {"rango": r, "ruptura": primera_ruptura(velas, ancla, limite, r["alto"], r["bajo"]),
            "ancla": ancla, "limite": limite}


# ═══ Calendario ═══════════════════════════════════════════════════════════

def _segundos(hhmmss: Any) -> Optional[int]:
    try:
        h, m, s = (int(x) for x in str(hhmmss).split(":"))
    except ValueError:
        return None
    return h * 3600 + m * 60 + s


def tramos(horario: dict) -> list[tuple[int, int]]:
    """Los tramos [apertura, cierre) del día en segundos UTC. Un cierre a
    las 23:59:59 es el fin del día."""
    out = []
    for a, c in zip(horario.get("aperturas") or [], horario.get("cierres") or []):
        sa, sc = _segundos(a), _segundos(c)
        if sa is None or sc is None:
            continue
        out.append((sa, 86400 if sc >= 86399 else sc))
    return out


def franja(epoch: int, horario: dict) -> Optional[str]:
    """None si Deriv cotiza a esa hora según el horario; si no, la franja:
    fin_de_semana o pausa_diaria."""
    t = datetime.fromtimestamp(epoch, tz=timezone.utc)
    dia = _DIAS_SEMANA[t.weekday()]
    seg = t.hour * 3600 + t.minute * 60 + t.second
    tr = tramos(horario)
    habiles = horario.get("trading_days") or list(_DIAS_SEMANA[:5])
    if dia not in habiles:
        return "fin_de_semana"
    if any(a <= seg and seg + GRANULARIDAD_M5 <= c for a, c in tr):
        return None
    cierres_del_dia = [c for _, c in tr if c < 86400]
    if dia == "Fri" and cierres_del_dia and seg >= min(cierres_del_dia):
        return "fin_de_semana"
    if dia == "Sun" and tr and seg < min(a for a, _ in tr):
        return "fin_de_semana"
    return "pausa_diaria"


def filtrar_por_calendario(epocas_td: Sequence[int], horario: Optional[dict],
                           epocas_deriv: Sequence[int]) -> dict:
    """Las épocas de TwelveData en que Deriv cotiza, y las de fuera por
    franja. Solo sobre el tramo de fechas UTC que cubre Deriv: fuera de él
    no hay con qué decidir un feriado."""
    if not epocas_deriv:
        return {"aplica": False, "motivo": "sin velas de Deriv"}
    if not horario:
        return {"aplica": False, "motivo": "sin horario de Deriv para frxXAUUSD"}
    dias_deriv = {datetime.fromtimestamp(e, tz=timezone.utc).date() for e in epocas_deriv}
    lo, hi = min(dias_deriv), max(dias_deriv)
    dentro: list[int] = []
    fuera: dict[str, int] = {"fin_de_semana": 0, "pausa_diaria": 0, "feriado": 0}
    feriados: set[str] = set()
    en_tramo = 0
    for e in epocas_td:
        d = datetime.fromtimestamp(e, tz=timezone.utc).date()
        if not lo <= d <= hi:
            continue
        en_tramo += 1
        f = franja(e, horario)
        if f is None and d not in dias_deriv:
            f = "feriado"
            feriados.add(d.isoformat())
        if f is None:
            dentro.append(e)
        else:
            fuera[f] += 1
    return {"aplica": True, "tramo_utc": [lo.isoformat(), hi.isoformat()],
            "velas_td_en_el_tramo": en_tramo, "velas_deriv": len(epocas_deriv),
            "velas_td_dentro": len(dentro), "velas_td_fuera": sum(fuera.values()),
            "fuera_por_franja": fuera, "feriados": sorted(feriados), "epocas_dentro": dentro}


# ═══ Concordancia ═════════════════════════════════════════════════════════

def _pct(xs: list[float]) -> dict[str, Optional[float]]:
    if not xs:
        return {str(p): None for p in PERCENTILES}
    return {str(p): float(np.percentile(xs, p)) for p in PERCENTILES}


def _gravedad(td: Optional[dict], dr: Optional[dict]) -> int:
    """2: rompe una sola o en direcciones opuestas; 1: misma dirección, otra
    vela; 0: igual."""
    if (td is None) != (dr is None) or (td and dr and td["direccion"] != dr["direccion"]):
        return 2
    if td and dr and td["epoch"] != dr["epoch"]:
        return 1
    return 0


def concordancia(td: dict[int, dict], deriv: dict[int, dict]) -> dict:
    """Métricas sobre los días en que las dos fuentes tienen rango."""
    filas = []
    for d in dias_locales(list(deriv)):
        a, b = dia_de_fuente(td, d), dia_de_fuente(deriv, d)
        if a["rango"] is None or b["rango"] is None:
            continue
        ancho = b["rango"]["alto"] - b["rango"]["bajo"]
        dh = abs(a["rango"]["alto"] - b["rango"]["alto"])
        dl = abs(a["rango"]["bajo"] - b["rango"]["bajo"])
        filas.append({"fecha": d.isoformat(), "td": a, "deriv": b, "dh": dh, "dl": dl,
                      "dh_d": dh / ancho if ancho > 0 else None,
                      "dl_d": dl / ancho if ancho > 0 else None,
                      "gravedad": _gravedad(a["ruptura"], b["ruptura"])})
    con_ruptura = [f for f in filas if f["td"]["ruptura"] or f["deriv"]["ruptura"]]
    iguales = [f for f in con_ruptura if f["gravedad"] == 0]
    misma_dir = [f for f in con_ruptura if f["td"]["ruptura"] and f["deriv"]["ruptura"]
                 and f["td"]["ruptura"]["direccion"] == f["deriv"]["ruptura"]["direccion"]]
    una_sola = [f for f in con_ruptura if (f["td"]["ruptura"] is None) != (f["deriv"]["ruptura"] is None)]
    n = len(con_ruptura)
    frac = (len(iguales) / n) if n else None
    out = {
        "dias_comunes": len(filas), "dias_con_ruptura_en_alguna": n,
        "dias_sin_ruptura_en_ninguna": len(filas) - n,
        "abs_dhigh": _pct([f["dh"] for f in filas]), "abs_dlow": _pct([f["dl"] for f in filas]),
        "abs_dhigh_en_fraccion_de_d": _pct([f["dh_d"] for f in filas if f["dh_d"] is not None]),
        "abs_dlow_en_fraccion_de_d": _pct([f["dl_d"] for f in filas if f["dl_d"] is not None]),
        "pct_misma_direccion_y_vela": None if frac is None else 100 * frac,
        "pct_misma_direccion": 100 * len(misma_dir) / n if n else None,
        "pct_una_rompe_y_la_otra_no": 100 * len(una_sola) / n if n else None,
        "pct_una_rompe_y_la_otra_no_sobre_dias_comunes":
            100 * len(una_sola) / len(filas) if filas else None,
        "umbral_compuerta_pct": 100 * UMBRAL_COMPUERTA,
    }
    out["compuerta"] = ("sin datos" if frac is None
                        else "verde" if frac >= UMBRAL_COMPUERTA else "roja")
    if out["compuerta"] == "roja":
        out["peores_dias"] = peores_dias(filas, td, deriv)
    return out


def _listado(velas: dict[int, dict], dia: dict) -> list[dict]:
    r = dia["ruptura"]
    tope = r["epoch"] if r else dia["limite"] - GRANULARIDAD_M5
    return velas_hasta(velas, dia["ancla"], tope)


def peores_dias(filas: list[dict], td: dict[int, dict], deriv: dict[int, dict],
                n: int = N_PEORES) -> list[dict]:
    malos = [f for f in filas if f["gravedad"] > 0]

    def clave(f):
        a, b = f["td"]["ruptura"], f["deriv"]["ruptura"]
        distancia = abs(a["epoch"] - b["epoch"]) if a and b else float("inf")
        return (f["gravedad"], distancia, max(f["dh_d"] or 0, f["dl_d"] or 0))
    out = []
    for f in sorted(malos, key=clave, reverse=True)[:n]:
        out.append({"fecha": f["fecha"],
                    "td": {"rango": f["td"]["rango"], "ruptura": f["td"]["ruptura"],
                           "velas": _listado(td, f["td"])},
                    "deriv": {"rango": f["deriv"]["rango"], "ruptura": f["deriv"]["ruptura"],
                              "velas": _listado(deriv, f["deriv"])}})
    return out


# ═══ Informe ══════════════════════════════════════════════════════════════

def _serie(simbolo: str) -> dict[int, dict]:
    df = leer_velas(simbolo, GRANULARIDAD_M5)
    return {int(r["epoch"]): r for r in df.iter_rows(named=True)}


def informe() -> list[tuple[str, dict]]:
    td, deriv = _serie(SERIE_TD), _serie(SIMBOLO_DERIV)
    calendario = _leer_filas(ruta_calendario())
    ultimo = calendario[-1] if calendario else None
    filtro = filtrar_por_calendario(sorted(td), (ultimo or {}).get("horario"), sorted(deriv))
    dentro = set(filtro.pop("epocas_dentro", []))
    td_filtrado = {e: v for e, v in td.items() if e in dentro} if filtro.get("aplica") else td

    def _fecha(e):
        return datetime.fromtimestamp(e, tz=timezone.utc).isoformat() if e else None
    datos = {s: {"archivo": str(ruta_serie(s, GRANULARIDAD_M5)), "velas": len(v),
                 "sha256": sha256_de_archivo(ruta_serie(s, GRANULARIDAD_M5)),
                 "primera": _fecha(min(v) if v else None), "ultima": _fecha(max(v) if v else None)}
             for s, v in ((SERIE_TD, td), (SIMBOLO_DERIV, deriv))}
    cal = {"archivo": str(ruta_calendario()), "sha256_archivo": sha256_de_archivo(ruta_calendario()),
           "fecha_del_horario": (ultimo or {}).get("fecha_servidor"),
           "sha256_trading_times": (ultimo or {}).get("sha256"),
           "horario": (ultimo or {}).get("horario"), **filtro}
    conc = concordancia(td_filtrado, deriv)
    peores = conc.pop("peores_dias", None)
    compuerta = {"compuerta": conc["compuerta"], "umbral_pct": conc["umbral_compuerta_pct"],
                 "pct_misma_direccion_y_vela": conc["pct_misma_direccion_y_vela"],
                 "dias_con_ruptura_en_alguna": conc["dias_con_ruptura_en_alguna"]}
    if peores is not None:
        compuerta["peores_dias"] = peores
    return [("DATOS", datos), ("CALENDARIO", cal), ("CONCORDANCIA", conc), ("COMPUERTA", compuerta)]


def main(argv: Optional[Sequence[str]] = None) -> int:
    argparse.ArgumentParser(prog="concordancia_xauusd",
                            description="Concordancia del rango de apertura del oro.").parse_args(argv)
    for titulo, bloque in informe():
        publicar(titulo, bloque)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
