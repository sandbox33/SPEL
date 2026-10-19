"""
ingestion/velas_d1.py
=======================
Las velas diarias de D1 y las de Deriv contra las que se validan, más la
muestra horaria de BTC del paso 1 (brief del Admin 14, 14b, punto 1):

    metrics/velas/td_BTCUSD/86400.jsonl    TwelveData BTC/USD 1day, toda la historia
    metrics/velas/td_XAUUSD/86400.jsonl    TwelveData XAU/USD 1day, toda la historia
    metrics/velas/td_BTCUSD/3600.jsonl     TwelveData BTC/USD 1h, timezone=UTC, 60 días
    metrics/velas/cryBTCUSD/86400.jsonl    Deriv por /ws/public, los ~365 días que da
    metrics/velas/frxXAUUSD/86400.jsonl    ídem
    metrics/velas/<serie>/paginas_<granularidad>.jsonl   el sha256 de cada página

No hay una descarga nueva: es la de ingestion/velas_intradia.py, con la
serie como parámetro. TwelveData pagina con `fetch_pagina`, el Limitador,
los reintentos y la regla de fondo de siempre; Deriv, con `profundidad` de
ingestion/deriv_publico.py por /ws/public (no deriv_ws.py, brief 14b). La
escritura es la de velas.py: append puro, solo velas cerradas por la hora
del servidor, y una vela que vuelve distinta va a `revisiones_<gran>.jsonl`
sin sobrescribir nada. Solo se escribe una descarga contigua.

══ LAS DIARIAS DE DERIV VAN DONDE LAS GUARDA velas.py ══

`metrics/velas/<símbolo>/86400.jsonl` es la serie diaria de Deriv de ese
símbolo, la escriba quien la escriba: es un solo concepto. velas.yml, que la
escribe por la API legacy, tiene el cron apagado (D8-r). Si alguna vez
vuelve a correr, agrega lo que falte con la misma validación, y lo que
vuelva distinto queda como revisión.

══ EL SHA256 DE CADA PÁGINA ══

Por serie, una línea por página en `paginas_<gran>.jsonl`: la fecha del
servidor, la fuente, el número de página y lo que el paginador anotó de
ella (sha256 del cuerpo crudo, filas, rango). Append puro, y solo cuando se
escribieron las velas: una descarga que no se escribió no deja páginas.

══ LOS CRÉDITOS, ANTES DE PEDIR ══

`plan_de_creditos` acota los pedidos de cada serie de TwelveData antes del
primero: una página por cada TWELVEDATA_MAX_OUTPUTSIZE velas que podría
tener el rango, contado como calendario continuo, más la página vacía que
puede cerrar la descarga. El Limitador cuenta un crédito por pedido; los
reintentos también gastan, y se cuentan contra el mismo tope por corrida
(TOPE_CREDITOS_TD).

══ EL M5 DEL ORO, AL DÍA (brief del Admin 15, punto 6) ══

Con `con_m5`, que activa `main`, la corrida actualiza también el M5 del oro
de TwelveData y de Deriv, con ingerir_td e ingerir_deriv de
velas_intradia.py y sus series por defecto: incremental, desde la última
vela guardada, y sin el trading_times. Va después de las diarias: así todo
día con barra diaria cerrada queda entero en el M5. Sus créditos se publican
aparte, también antes del primer pedido. Sin `con_m5`, `ingerir` es la de
siempre.

══ LA MUESTRA HORARIA ══

La primera corrida baja 60 días de 1h de BTC/USD desde las 00:00 UTC
(MUESTRA_H1_DIAS). Las siguientes siguen desde la última vela guardada,
como cualquier serie: la muestra crece y no se rehace.

Uso:
    python ingestion/velas_d1.py            # baja y reporta, no escribe
    python ingestion/velas_d1.py --write
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from governance.secrets import SecretKey, load_secret  # noqa: E402
from ingestion.adapters import (  # noqa: E402
    _TWELVEDATA_GRANULARITY_SECONDS,
    TWELVEDATA_MAX_OUTPUTSIZE,
    DerivAdapter,
    TwelveDataAdapter,
)
from ingestion.limitador import Limitador  # noqa: E402
from ingestion.velas import (  # noqa: E402
    GRANULARIDAD_DIARIA,
    Calendario,
    ReporteSimbolo,
    _append,
    _dia,
    ruta_serie,
)
from ingestion.velas_intradia import (  # noqa: E402
    GRANULARIDAD_M5,
    LLAMADAS_POR_MINUTO_TD,
    ORO_M5,
    PAUSA_DERIV_S,
    SERIE_TD,
    SIMBOLO_DERIV,
    SIMBOLO_TD,
    TOPE_CREDITOS_TD,
    SerieTD,
    _fmt,
    bloque_datos,
    inicio_de_la_descarga,
    ingerir_deriv,
    ingerir_td,
    publicar,
)

TIMEFRAME_D1 = "1d"
TIMEFRAME_H1 = "1h"
GRANULARIDAD_H1: int = _TWELVEDATA_GRANULARITY_SECONDS[TIMEFRAME_H1]
SIMBOLO_TD_BTC = "BTCUSD"
SERIE_TD_BTC = "td_BTCUSD"
SIMBOLO_DERIV_BTC = "cryBTCUSD"

#: Desde el earliest_timestamp diario de cada símbolo (D1-p).
BTC_D1 = SerieTD(SIMBOLO_TD_BTC, TIMEFRAME_D1, SERIE_TD_BTC, GRANULARIDAD_DIARIA,
                 Calendario.CONTINUO, "2017-08-28 00:00:00")
ORO_D1 = SerieTD(SIMBOLO_TD, TIMEFRAME_D1, SERIE_TD, GRANULARIDAD_DIARIA,
                 Calendario.HABIL, "1979-12-26 00:00:00")

#: Brief 14b: la muestra de 1h de BTC, 60 días.
MUESTRA_H1_DIAS = 60


@dataclass(frozen=True)
class ActivoD1:
    """Un activo de D1: su diaria de TwelveData, su símbolo de Deriv y la
    serie intradía de TwelveData (en UTC) con que se mide la hora de corte."""
    activo: str
    td: SerieTD
    deriv: str
    intradia: str
    granularidad_intradia: int


#: Los dos pares del brief 14b, en el orden del informe.
ACTIVOS_D1: tuple[ActivoD1, ...] = (
    ActivoD1("BTC", BTC_D1, SIMBOLO_DERIV_BTC, SERIE_TD_BTC, GRANULARIDAD_H1),
    ActivoD1("XAU", ORO_D1, SIMBOLO_DERIV, SERIE_TD, GRANULARIDAD_M5),
)


def muestra_h1(ahora: int) -> SerieTD:
    """La serie 1h de BTC/USD, con inicio MUESTRA_H1_DIAS antes de las 00:00
    UTC del día de `ahora`. Solo cuenta si no hay nada guardado."""
    hoy = ahora - ahora % GRANULARIDAD_DIARIA
    return SerieTD(SIMBOLO_TD_BTC, TIMEFRAME_H1, SERIE_TD_BTC, GRANULARIDAD_H1,
                   Calendario.CONTINUO, _fmt(hoy - MUESTRA_H1_DIAS * GRANULARIDAD_DIARIA))


def series_td(ahora: int) -> tuple[SerieTD, ...]:
    return (BTC_D1, ORO_D1, muestra_h1(ahora))


# ═══ Créditos ═════════════════════════════════════════════════════════════

def pedidos_a_lo_sumo(desde: int, hasta: int, granularidad: int,
                      outputsize: int = TWELVEDATA_MAX_OUTPUTSIZE) -> int:
    """Una página por cada `outputsize` velas que caben en [desde, hasta]
    como calendario continuo, más la vacía que puede cerrar la descarga."""
    velas = max(0, (hasta - desde) // granularidad + 1)
    return -(-velas // outputsize) + 1


def plan_de_creditos(series: Sequence[SerieTD], ahora: int,
                     outputsize: int = TWELVEDATA_MAX_OUTPUTSIZE) -> dict:
    filas = []
    for s in series:
        desde = inicio_de_la_descarga(s)
        filas.append({"serie": s.serie, "timeframe": s.timeframe, "desde": _fmt(desde),
                      "pedidos_a_lo_sumo": pedidos_a_lo_sumo(desde, ahora, s.granularidad,
                                                             outputsize)})
    return {"series": filas, "creditos_a_lo_sumo": sum(f["pedidos_a_lo_sumo"] for f in filas),
            "tope_de_la_corrida": TOPE_CREDITOS_TD,
            "nota": "un crédito por pedido; los reintentos también gastan y cuentan "
                    "contra el mismo tope"}


# ═══ Páginas ══════════════════════════════════════════════════════════════

def ruta_paginas(serie: str, granularidad: int) -> Path:
    return ruta_serie(serie, granularidad).with_name(f"paginas_{granularidad}.jsonl")


def registrar_paginas(fuente: str, serie: str, granularidad: int, res: dict,
                      paginas: Sequence[dict], *, write: bool) -> int:
    """Agrega una línea por página si la descarga se escribió. Devuelve
    cuántas agregó."""
    if not write or res.get("no_escrito"):
        return 0
    fecha = _dia(res["hora_servidor"]).isoformat() if res.get("hora_servidor") else None
    _append(ruta_paginas(serie, granularidad),
            [{"fecha_servidor": fecha, "fuente": fuente, "pagina": i, **p}
             for i, p in enumerate(paginas)])
    return len(paginas)


# ═══ Ingesta ══════════════════════════════════════════════════════════════

def bloque(fuente: str, rep: ReporteSimbolo, res: dict, granularidad: int) -> dict:
    """El bloque del informe del M5, más los huecos según el calendario del
    activo y la historia usable: en la compuerta, un hueco saca un par."""
    p = rep.profundidad
    return {**bloque_datos(fuente, rep, res, granularidad), "granularidad": granularidad,
            "n_huecos": len(p.huecos) if p else 0, "historia_usable": p.historia_usable if p else 0}


async def ingerir(*, key: str, write: bool,
                  abrir: Callable[[str], Any] = DerivAdapter._default_connector,
                  adapter: Optional[TwelveDataAdapter] = None,
                  limitador: Optional[Limitador] = None,
                  ahora: Optional[int] = None,
                  pausa_s: float = PAUSA_DERIV_S,
                  con_m5: bool = False) -> list[dict]:
    # Un solo limitador para las tres series de TwelveData y sus reintentos.
    lim = limitador or Limitador(LLAMADAS_POR_MINUTO_TD, TOPE_CREDITOS_TD)
    td = adapter or TwelveDataAdapter(api_key=key, timeout_s=30.0, limitador=lim)
    ahora = int(time.time()) if ahora is None else ahora
    bloques = []
    for serie in series_td(ahora):
        rep, res = await ingerir_td(td, write=write, limitador=lim, serie=serie)
        res["paginas_registradas"] = registrar_paginas(
            "twelvedata", serie.serie, serie.granularidad, res, res.get("paginas") or [],
            write=write)
        bloques.append(bloque("twelvedata", rep, res, serie.granularidad))
    for a in ACTIVOS_D1:
        try:
            rep, res = await ingerir_deriv(abrir, write=write, pausa_s=pausa_s, simbolo=a.deriv,
                                           granularidad=GRANULARIDAD_DIARIA,
                                           calendario=a.td.calendario, con_horario=False,
                                           detalle_paginas=True)
        except Exception as exc:   # noqa: BLE001 -- se informa; lo demás ya quedó
            rep, res = ReporteSimbolo(a.deriv, a.td.calendario), {
                "error": f"{type(exc).__name__}: {exc}"}
        res["paginas_registradas"] = registrar_paginas(
            "deriv", a.deriv, GRANULARIDAD_DIARIA, res, res.get("detalle_paginas") or [],
            write=write)
        bloques.append(bloque("deriv", rep, res, GRANULARIDAD_DIARIA))
    if con_m5:
        bloques += await ingerir_m5_oro(td, abrir, write=write, limitador=lim, pausa_s=pausa_s)
    return bloques


async def ingerir_m5_oro(td: TwelveDataAdapter, abrir: Callable[[str], Any], *, write: bool,
                         limitador: Limitador, pausa_s: float) -> list[dict]:
    """El M5 del oro de TwelveData y de Deriv, incremental, con las series
    por defecto de velas_intradia y sin el trading_times."""
    rep, res = await ingerir_td(td, write=write, limitador=limitador)
    bloques = [bloque("twelvedata", rep, res, GRANULARIDAD_M5)]
    try:
        rep, res = await ingerir_deriv(abrir, write=write, pausa_s=pausa_s, con_horario=False)
    except Exception as exc:   # noqa: BLE001 -- se informa; lo demás ya quedó
        rep, res = ReporteSimbolo(SIMBOLO_DERIV, Calendario.HABIL), {
            "error": f"{type(exc).__name__}: {exc}"}
    bloques.append(bloque("deriv", rep, res, GRANULARIDAD_M5))
    return bloques


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="velas_d1",
                                description="Velas diarias de D1: TwelveData y Deriv.")
    p.add_argument("--write", action="store_true", help="Escribe en metrics/.")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    key = load_secret(SecretKey.TWELVEDATA_API_KEY, required=False)
    if not key:
        print(f"ERROR: falta {SecretKey.TWELVEDATA_API_KEY}.", file=sys.stderr)
        return 2
    ahora = int(time.time())
    publicar("CRÉDITOS ESTIMADOS", plan_de_creditos(series_td(ahora), ahora))
    publicar("CRÉDITOS ESTIMADOS M5 DEL ORO", plan_de_creditos((ORO_M5,), ahora))
    bloques = asyncio.run(ingerir(key=key, write=args.write, ahora=ahora, con_m5=True))
    for b in bloques:
        texto = json.dumps(b, ensure_ascii=False)
        assert key not in texto, "la key llegó al informe: no se publica"
        publicar(f"DESCARGA {b['fuente'].upper()} {b['serie']} {b.get('granularidad', '')}".rstrip(), b)
    malos = [b for b in bloques if b.get("n_invalidas") or b.get("no_escrito") or b.get("error")]
    return 1 if malos else 0


if __name__ == "__main__":
    raise SystemExit(main())
