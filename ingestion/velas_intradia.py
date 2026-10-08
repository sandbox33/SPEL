"""
ingestion/velas_intradia.py
===========================
Velas M5 del oro de dos fuentes, persistidas en la rama `data` (brief del
Admin del 06-oct-2026 (4), punto 1):

    metrics/velas/td_XAUUSD/300.jsonl     TwelveData XAU/USD 5min, desde 2020-03-16
    metrics/velas/frxXAUUSD/300.jsonl     Deriv frxXAUUSD M5, los 365 días que da
    metrics/calendario/frxXAUUSD/trading_times.jsonl   el horario de Deriv, crudo

══ PORTADO ══

  · Escritura: `ingestion/velas.py` tal cual (`_ingerir_simbolo`): append
    puro, solo velas cerradas, validación vela a vela, revisiones aparte,
    las anteriores a la última guardada no se insertan. No se reescribe.
  · TwelveData: `TwelveDataAdapter.fetch_pagina` (timezone=UTC) con la
    paginación de la sonda §0.A-3b (`serie_td_ultimo_anio`): hacia atrás con
    `end_date` = primera vela de la página anterior − 60 s, y `start_date`
    fijo. El tope por minuto es el de la 3b (7) con `ingestion/limitador.py`.
  · Deriv: `ingestion/deriv_publico.py` (`profundidad` y el canal de las
    sondas §0.A-2 y §0.A-3b) por `/ws/public`. No usa `deriv_ws.py`, que
    sigue en la API legacy.

══ SOLO SE ESCRIBE UNA DESCARGA CONTIGUA ══

Se pagina hacia atrás desde la última vela cerrada. Si la descarga se corta
antes de llegar a lo ya guardado (o, la primera vez, al fondo de la
serie), lo bajado es un bloque suelto: escribirlo dejaría un hueco que el
append puro no puede llenar después. Entonces no se escribe nada de esa
fuente, y el informe lo dice.

══ CUÁNDO UNA PÁGINA VACÍA DE TWELVEDATA ES EL FONDO ══

Brief del Admin del 07-oct-2026 (6). El fondo de la paginación llega como
una página vacía (el 400 "No data is available on the specified dates", o
el 404 "data not found"), pero una página vacía también puede ser espuria a
mitad de la historia. Darla por el fondo dejaría un hueco. Por eso cierra la
descarga como completa SOLO si:
  · la página anterior trajo menos filas que el `outputsize` (llegó al
    inicio de la historia), contando las filas que devolvió la API ANTES de
    sacar la vela abierta; o
  · el `end_date` pedido ya es <= `desde`.
Si no, es un error y no se escribe nada. Una vacía en el PRIMER pedido (no
hay página anterior) cierra como completa sin velas: no hay nada bajado que
pueda quedar suelto (decisión del Admin del 08-oct-2026).

══ CIERRE DE VELA ══

Con la hora del servidor de cada fuente: la `time` de Deriv, y la
cabecera Date de cada respuesta de TwelveData (la paginación descarta lo
abierto con la de su página; la escritura vuelve a mirar con la más
reciente). Nunca con el reloj del runner.

[INTERPRETACIÓN] Lo que el brief no fija:
  · Inicio de TwelveData: 2020-03-16 00:00:00 UTC, el día del
    `earliest_timestamp` de la 3b (01:10).
  · Tope total de créditos por corrida: TOPE_CREDITOS_TD. La historia
    completa son unas 95 páginas de 5000; el tope deja margen y corta antes
    de vaciar la cuota diaria si algo pagina de más.
  · El horario de Deriv se pide para la fecha del servidor (`trading_times`
    con esa fecha) y se agrega crudo, con su sha256, una línea por corrida.

Uso:
    python ingestion/velas_intradia.py            # reporta, no escribe
    python ingestion/velas_intradia.py --write
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from governance.persistence import PersistenceStream, stream_path  # noqa: E402
from governance.secrets import SecretKey, load_secret  # noqa: E402
from ingestion.adapters import (  # noqa: E402
    TWELVEDATA_MAX_OUTPUTSIZE,
    AdapterException,
    DerivAdapter,
    TwelveDataAdapter,
)
from ingestion.deriv_publico import ENDPOINT_PUBLICO, CanalPublico, profundidad  # noqa: E402
from ingestion.limitador import Limitador  # noqa: E402
from ingestion.velas import (  # noqa: E402
    Calendario,
    ReporteSimbolo,
    _append,
    _dia,
    _ingerir_simbolo,
    _leer_filas,
    ruta_serie,
)

GRANULARIDAD_M5 = 300
SIMBOLO_TD = "XAUUSD"
TIMEFRAME_TD = "5m"
SERIE_TD = "td_XAUUSD"
SIMBOLO_DERIV = "frxXAUUSD"

#: [INTERPRETACIÓN] El día del earliest_timestamp de XAU/USD 5min (3b, f).
INICIO_TD = "2020-03-16 00:00:00"

#: El tope por minuto de la sonda §0.A-3b.
LLAMADAS_POR_MINUTO_TD = 7
#: [INTERPRETACIÓN] Ver el docstring.
TOPE_CREDITOS_TD = 300

#: La pausa entre pedidos del WS de la sonda §0.A-3.
PAUSA_DERIV_S = 0.25
#: El tope de páginas de la sonda §0.A-3 para M5 (más de 14 años).
MAX_PAGINAS_DERIV = 300

#: Cortes de `profundidad` que significan "se llegó al fondo".
CORTES_DE_FONDO: frozenset[str] = frozenset({
    "respuesta vacía", "la página no retrocedió", "se llegó a la epoch 0"})


def ruta_calendario(simbolo: str = SIMBOLO_DERIV) -> Path:
    return (Path(stream_path(PersistenceStream.METRICS)) / "calendario" / simbolo
            / "trading_times.jsonl")


def _fmt(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _epoch_de(texto: str) -> int:
    return int(datetime.strptime(texto, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


def sha256_de_archivo(ruta: Path) -> Optional[str]:
    return hashlib.sha256(ruta.read_bytes()).hexdigest() if ruta.exists() else None


def _ultima_guardada(serie: str) -> Optional[int]:
    filas = _leer_filas(ruta_serie(serie, GRANULARIDAD_M5))
    return max(f["epoch"] for f in filas) if filas else None


# ═══ TwelveData ═══════════════════════════════════════════════════════════

def vacia_cierra_la_descarga(filas_previa: Optional[int], end_date: str, desde: int,
                             outputsize: int = TWELVEDATA_MAX_OUTPUTSIZE) -> Optional[str]:
    """Por qué una página vacía es el fondo de la serie, o None si no lo es
    (y entonces la descarga es un error). Ver el docstring del módulo.
    `filas_previa` son las filas que DEVOLVIÓ la API en la página anterior
    (abiertas incluidas); None si no hubo página anterior."""
    if filas_previa is None:
        return "la primera página vino vacía: no hay velas nuevas desde la última guardada"
    if filas_previa < outputsize:
        return (f"la página anterior trajo {filas_previa} filas, menos que el outputsize "
                f"({outputsize}): se llegó al inicio de la historia")
    if _epoch_de(end_date) <= desde:
        return "el end_date pedido ya es <= desde"
    return None


async def descargar_td(adapter: TwelveDataAdapter, *, desde: int, hasta: int,
                       limitador: Limitador,
                       outputsize: int = TWELVEDATA_MAX_OUTPUTSIZE) -> tuple[list[dict], dict]:
    """Pagina hacia atrás desde `hasta` hasta `desde`, como la 3b: las dos
    fechas en cada pedido. `hasta` es solo el borde del primer pedido; qué
    vela cerró lo decide la cabecera Date. Devuelve (velas crudas, resumen).
    `resumen["completo"]` dice si se llegó a `desde` (o al fondo)."""
    velas: dict[int, dict] = {}
    paginas: list[dict] = []
    filas_previa: Optional[int] = None
    fin = _fmt(hasta)
    res: dict[str, Any] = {"desde": _fmt(desde), "completo": False, "hora_servidor": None,
                           "abiertas_descartadas": 0, "creditos_api": None}
    while True:
        if not await limitador.turno():
            res["corte"] = f"tope de {limitador.tope} créditos: la descarga quedó incompleta"
            break
        try:
            p = await adapter.fetch_pagina(SIMBOLO_TD, TIMEFRAME_TD, start_date=_fmt(desde),
                                           end_date=fin, outputsize=outputsize)
        except AdapterException as exc:
            paginas.append({"end_date": fin, "error": f"{type(exc).__name__}: {exc}"})
            res["corte"] = "error"
            break
        paginas.append({"end_date": fin, "sha256": p.sha256, "filas": len(p.velas),
                        "devueltas": p.filas_devueltas,
                        **({"vacia": p.motivo} if p.vacia else {})})
        res["creditos_api"] = p.creditos or res["creditos_api"]
        res["abiertas_descartadas"] += p.abiertas_descartadas
        if p.hora_servidor is not None:
            res["hora_servidor"] = max(res["hora_servidor"] or 0, p.hora_servidor)
        if p.vacia:
            por_que = vacia_cierra_la_descarga(filas_previa, fin, desde, outputsize)
            if por_que is None:
                res["corte"] = (f"página vacía ({p.motivo}) después de una página llena "
                                f"({filas_previa} filas): puede ser espuria, y darla por el "
                                f"fondo dejaría un hueco. Error.")
            else:
                res.update(corte=f"{p.motivo}; {por_que}", completo=True)
            break
        epocas = [int(t.timestamp()) for t in p.velas["timestamp"]] if len(p.velas) else []
        nuevas = 0
        for e, (_, fila) in zip(epocas, p.velas.iterrows()):
            nuevas += e not in velas
            velas[e] = {"epoch": e, **{k: float(fila[k]) for k in ("open", "high", "low", "close")}}
        if not epocas or not nuevas:
            res.update(corte="sin velas nuevas", completo=True)
            break
        primera = min(epocas)
        if primera <= desde:
            res.update(corte="se llegó al inicio pedido", completo=True)
            break
        filas_previa = p.filas_devueltas
        fin = _fmt(primera - 60)
    res.update(llamadas=len(paginas), paginas=paginas, velas=len(velas))
    return list(velas.values()), res


async def ingerir_td(adapter: TwelveDataAdapter, *, write: bool,
                     limitador: Optional[Limitador] = None,
                     outputsize: int = TWELVEDATA_MAX_OUTPUTSIZE) -> tuple[ReporteSimbolo, dict]:
    lim = limitador or Limitador(LLAMADAS_POR_MINUTO_TD, TOPE_CREDITOS_TD)
    ultima = _ultima_guardada(SERIE_TD)
    desde = ultima + GRANULARIDAD_M5 if ultima is not None else _epoch_de(INICIO_TD)
    crudas, res = await descargar_td(adapter, desde=desde, hasta=int(time.time()),
                                     limitador=lim, outputsize=outputsize)
    rep = ReporteSimbolo(SERIE_TD, Calendario.HABIL, corte=res.get("corte", ""))
    res["creditos_usados"] = lim.creditos
    if not res["completo"]:
        rep.recibidas = len(crudas)
        res["no_escrito"] = ("la descarga no llegó a lo ya guardado ni al inicio: "
                             "escribirla dejaría un hueco. No se escribió nada.")
        return rep, res
    ahora = res["hora_servidor"] or 0
    _ingerir_simbolo(rep, crudas, ahora_servidor=ahora, granularidad=GRANULARIDAD_M5,
                     write=write, fecha_servidor=_dia(ahora).isoformat() if ahora else "")
    return rep, res


# ═══ Deriv ════════════════════════════════════════════════════════════════

def horario_de(datos: dict, simbolo: str = SIMBOLO_DERIV) -> Optional[dict]:
    """El horario de un símbolo dentro de una respuesta de `trading_times`:
    días, aperturas (`times.open`), cierres (`times.close`) y eventos, tal
    cual vienen. None si no está."""
    for m in (datos.get("trading_times") or {}).get("markets") or []:
        for sm in m.get("submarkets") or []:
            for s in sm.get("symbols") or []:
                if s.get("underlying_symbol", s.get("symbol")) == simbolo:
                    t = s.get("times") or {}
                    return {"mercado": m.get("name"), "submercado": sm.get("name"),
                            "trading_days": s.get("trading_days"), "aperturas": t.get("open"),
                            "cierres": t.get("close"), "settlement": t.get("settlement"),
                            "events": s.get("events")}
    return None


async def ingerir_deriv(abrir: Callable[[str], Any], *, write: bool,
                        pausa_s: float = PAUSA_DERIV_S) -> tuple[ReporteSimbolo, dict]:
    rep = ReporteSimbolo(SIMBOLO_DERIV, Calendario.HABIL)
    res: dict[str, Any] = {"endpoint": ENDPOINT_PUBLICO}
    velas: dict[int, dict] = {}
    async with abrir(ENDPOINT_PUBLICO) as ws:
        canal = CanalPublico(ws, pausa_s=pausa_s)
        e, d = await canal.pedir({"time": 1})
        if d is None:
            res["error"] = e.get("error")
            return rep, res
        ahora = int(d["time"])
        fecha = _dia(ahora).isoformat()
        res["hora_servidor"] = ahora
        e, d = await canal.pedir({"trading_times": fecha})
        cal: dict[str, Any] = {"fecha": fecha, "sha256": e.get("sha256"), "error": e.get("error")}
        if d is not None:
            cal["horario"] = horario_de(d)
            if write:
                _append(ruta_calendario(), [{"fecha_servidor": fecha, "sha256": e["sha256"],
                                             "horario": cal["horario"],
                                             "crudo": canal.ultimo_crudo}])
        res["calendario"] = cal
        prof = await profundidad(canal, SIMBOLO_DERIV, GRANULARIDAD_M5,
                                 max_paginas=MAX_PAGINAS_DERIV, guardar_velas=velas)
    rep.corte = prof["corte"]
    res["paginas"] = len(prof["paginas"])
    res["primera_epoch"], res["ultima_epoch"] = prof["primera_epoch"], prof["ultima_epoch"]
    ultima = _ultima_guardada(SIMBOLO_DERIV)
    contigua = (prof["corte"] in CORTES_DE_FONDO if ultima is None
                else bool(velas) and min(velas) <= ultima + GRANULARIDAD_M5)
    res["contigua"] = contigua
    crudas = [{"epoch": int(c["epoch"]), **{k: c.get(k) for k in ("open", "high", "low", "close")}}
              for c in velas.values()]
    if not contigua:
        rep.recibidas = len(crudas)
        res["no_escrito"] = (f"la descarga cortó por {prof['corte']!r} sin llegar a lo ya "
                             f"guardado ni al fondo: escribirla dejaría un hueco. No se escribió.")
        return rep, res
    _ingerir_simbolo(rep, crudas, ahora_servidor=ahora, granularidad=GRANULARIDAD_M5,
                     write=write, fecha_servidor=fecha)
    return rep, res


# ═══ Informe y CLI ════════════════════════════════════════════════════════

def bloque_datos(fuente: str, rep: ReporteSimbolo, res: dict) -> dict:
    """Un bloque del informe: conteos, rango de fechas y sha256 del archivo."""
    p = rep.profundidad
    ruta = ruta_serie(rep.simbolo, GRANULARIDAD_M5)
    return {"fuente": fuente, "serie": rep.simbolo, "archivo": str(ruta),
            "sha256": sha256_de_archivo(ruta), "corte": rep.corte, "recibidas": rep.recibidas,
            "nuevas": rep.nuevas, "abiertas_descartadas": rep.abiertas_descartadas,
            "invalidas": rep.invalidas[:20], "n_invalidas": len(rep.invalidas),
            "revisiones_nuevas": rep.revisiones_nuevas, "revisiones_total": rep.revisiones_total,
            "anteriores_sin_escribir": rep.anteriores_sin_escribir,
            "dias_con_velas": p.barras_totales if p else 0,
            "primera_fecha": p.primera if p else None, "ultima_fecha": p.ultima if p else None,
            **res}


def publicar(titulo: str, bloque: dict) -> None:
    """Una línea por bloque, como la 3b."""
    print(f"=== {titulo} ===")
    print(json.dumps(bloque, ensure_ascii=False, separators=(",", ":")))


async def ingerir(*, key: str, write: bool, abrir: Callable[[str], Any] = DerivAdapter._default_connector,
                  adapter: Optional[TwelveDataAdapter] = None,
                  limitador: Optional[Limitador] = None) -> list[dict]:
    td = adapter or TwelveDataAdapter(api_key=key, timeout_s=30.0)
    bloques = []
    rep, res = await ingerir_td(td, write=write, limitador=limitador)
    bloques.append(bloque_datos("twelvedata", rep, res))
    try:
        rep, res = await ingerir_deriv(abrir, write=write)
    except Exception as exc:   # noqa: BLE001 -- se informa; lo de TwelveData ya quedó
        rep, res = ReporteSimbolo(SIMBOLO_DERIV, Calendario.HABIL), {
            "error": f"{type(exc).__name__}: {exc}"}
    bloques.append(bloque_datos("deriv", rep, res))
    return bloques


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="velas_intradia",
                                description="Velas M5 del oro: TwelveData y Deriv.")
    p.add_argument("--write", action="store_true", help="Escribe en metrics/.")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    key = load_secret(SecretKey.TWELVEDATA_API_KEY, required=False)
    if not key:
        print(f"ERROR: falta {SecretKey.TWELVEDATA_API_KEY}.", file=sys.stderr)
        return 2
    bloques = asyncio.run(ingerir(key=key, write=args.write))
    for b in bloques:
        texto = json.dumps(b, ensure_ascii=False)
        assert key not in texto, "la key llegó al informe: no se publica"
        publicar(f"DESCARGA {b['fuente'].upper()}", b)
    malos = [b for b in bloques if b.get("n_invalidas") or b.get("no_escrito") or b.get("error")]
    return 1 if malos else 0


if __name__ == "__main__":
    raise SystemExit(main())
