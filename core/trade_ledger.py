"""
core/trade_ledger.py
======================
El registro de trades. Una línea JSON por trade, append-only, con el
desglose de costos completo que produce `core/execution_costs.py`.

Vive en `core/` y no en `ingestion/` porque su productor es
`core/execution_costs.py` -- misma lógica por la que `gdelt_series.py` vive
al lado de su productor en `ingestion/`. El patrón de escritura es el de
`gdelt_series.append_day()`, portado y no reinventado: append puro por
línea, deduplicación al LEER y no al escribir, y una lista vacía cuando el
archivo todavía no existe.

══ LA REGLA QUE DEFINE ESTE ARCHIVO ══

NUNCA SE FILTRA, NUNCA SE BORRA, NUNCA SE REESCRIBE.

Perdedoras, breakeven, fills fallidos y señales que se descartaron sin
operar entran IGUAL. Si una corrida produce 200 trades y 140 pierden, el
archivo tiene 200 líneas. No 60.

No es prolijidad, es la única defensa contra el sesgo de supervivencia. Un
ledger que solo guarda lo que se ejecutó no permite calcular qué fracción
de las señales se descartó, y un ledger que solo guarda ganadoras produce
una curva de equity que sube siempre. El sesgo no se ve en el archivo
filtrado -- se ve en que el archivo filtrado ya no puede refutarlo.

De ahí sale que `outcome` sea un campo EXPLÍCITO y nunca una ausencia. Una
fila sin outcome hay que interpretarla, y una fila que hay que interpretar
la interpreta distinto cada quien la lea. `Outcome.NO_EJECUTADA` y
`Outcome.FILL_FALLIDO` son estados de primera clase, no huecos.

`append_trade()` NO valida que el trade sea nuevo, NO ordena y NO rechaza
nada. Deduplicar al escribir obligaría a leer el archivo entero en cada
append -- el mismo razonamiento que documenta `gdelt_series.append_day()` --
y, peor, pondría a la ruta de escritura en posición de decidir qué entra.
La ruta de escritura no decide: escribe.

══ NUNCA ESCRIBE AL FALLBACK EN SILENCIO ══

`drive_root()` cae a `.spel_drive_stream` cuando no hay `SPEL_DRIVE_ROOT` ni
Colab (ver `governance/persistence.py`). Ese directorio es para desarrollo
local y para un CI que solo corre tests: nadie lo lee después y nada lo
respalda.

Escribir un ledger ahí sería perder el registro de una corrida entera sin
un solo mensaje de error -- el trabajo parece haber funcionado y el archivo
queda en un directorio que el próximo `rm -rf` del runner se lleva. Por eso
`append_trade()` LANZA (`LedgerEnFallbackError`) si detecta el fallback, y
hay que pedir explícitamente `permitir_fallback=True` para escribir ahí. Los
tests lo piden; una corrida que pretende persistir de verdad, no.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.execution_costs import Lado, Outcome, ResultadoTrade
from governance.persistence import (
    LOCAL_FALLBACK_DRIVE_ROOT,
    PersistenceStream,
    drive_root,
    stream_path,
)

logger = logging.getLogger("spel.core.trade_ledger")

#: Un solo archivo para todos los activos, a diferencia de gdelt_series que
#: usa uno por activo. El motivo es el caso de uso: una serie GDELT se lee
#: por activo (nadie quiere los eventos de XAU cuando calcula el score de
#: BTC), y el ledger se lee ENTERO -- la curva de equity, el conteo de
#: ganadoras y el drawdown son de la cuenta, no de un activo. Partirlo por
#: activo obligaría a reunirlo y reordenarlo en cada lectura.
LEDGER_FILENAME = "trades.jsonl"


class LedgerEnFallbackError(RuntimeError):
    """Se intentó escribir el ledger en el fallback local. Ver el docstring
    del módulo: el peor resultado posible no es el error, es la corrida que
    parece haber funcionado y dejó el registro en un directorio que nadie
    va a leer."""


@dataclass(frozen=True)
class TradeLedgerEntry:
    """
    Plana a propósito: una línea JSON sin anidar, legible a mano y
    cargable a un DataFrame sin normalizar nada. `DesgloseCostos` se
    aplana en sus cinco componentes en vez de viajar como sub-objeto --
    el desglose tiene que estar ENTERO en cada línea (ver
    `core/execution_costs.py`: un neto sin desglose no se puede auditar),
    y anidarlo solo agregaría un nivel a cada consulta.

    `trade_id` es la clave de deduplicación. La elige quien llama: este
    módulo no la genera porque generarla acá (un uuid, un hash del
    contenido) haría que dos registros del MISMO trade tuvieran ids
    distintos, y la deduplicación dejaría de funcionar justo en el caso
    para el que existe -- un reintento tras un corte.
    """
    trade_id: str
    asset: str
    ts_entrada: str          # ISO 8601
    ts_salida: str
    ts_cargo_entrada: str    # ISO 8601 -- el instante en que se cargó el costo
    lado: str
    precio_entrada: float
    precio_salida: float
    nocional: float
    fraccion_completada: float
    bruto: float
    neto: float
    outcome: str
    fee_entrada: float
    fee_salida: float
    spread: float
    slippage: float
    funding: float
    costo_total: float
    periodos_funding: int


def build_entry(
    *,
    trade_id: str,
    asset: str,
    lado: Lado,
    ts_entrada: datetime,
    ts_salida: datetime,
    precio_entrada: float,
    precio_salida: float,
    nocional: float,
    resultado: ResultadoTrade,
) -> TradeLedgerEntry:
    """Arma la fila desde un `ResultadoTrade`. Existe para que ningún
    llamador tenga que aplanar el desglose a mano -- dos aplanados distintos
    producirían dos esquemas de línea en el mismo archivo."""
    if not trade_id:
        raise ValueError(
            "trade_id vacío. Es la clave de deduplicación: sin él, un "
            "reintento tras un corte escribe el trade dos veces y las dos "
            "cuentan.")
    d = resultado.desglose
    return TradeLedgerEntry(
        trade_id=trade_id,
        asset=asset,
        ts_entrada=ts_entrada.isoformat(),
        ts_salida=ts_salida.isoformat(),
        ts_cargo_entrada=resultado.ts_cargo_entrada.isoformat(),
        lado=lado.value,
        precio_entrada=precio_entrada,
        precio_salida=precio_salida,
        nocional=nocional,
        fraccion_completada=resultado.fraccion_completada,
        bruto=resultado.bruto,
        neto=resultado.neto,
        outcome=resultado.outcome.value,
        fee_entrada=d.fee_entrada,
        fee_salida=d.fee_salida,
        spread=d.spread,
        slippage=d.slippage,
        funding=d.funding,
        costo_total=d.total,
        periodos_funding=resultado.periodos_funding,
    )


def _ledger_file_path() -> Path:
    return Path(stream_path(PersistenceStream.TRADE_LEDGER)) / LEDGER_FILENAME


def _esta_en_fallback() -> bool:
    return drive_root() == LOCAL_FALLBACK_DRIVE_ROOT


def append_trade(entry: TradeLedgerEntry, *, permitir_fallback: bool = False) -> None:
    """
    Agrega un trade al ledger. Append PURO: no lee el archivo, no ordena, no
    deduplica y no rechaza nada -- ver el docstring del módulo.

    Lanza `LedgerEnFallbackError` si `drive_root()` resolvió al fallback
    local y no se pidió `permitir_fallback=True`.
    """
    if _esta_en_fallback() and not permitir_fallback:
        raise LedgerEnFallbackError(
            f"drive_root() resolvió a {LOCAL_FALLBACK_DRIVE_ROOT}, que es el "
            f"fallback de desarrollo: nadie lo lee y nada lo respalda. "
            f"Escribir el ledger ahí perdería la corrida entera sin un solo "
            f"error. Definí SPEL_DRIVE_ROOT, o pasá permitir_fallback=True "
            f"si de verdad querés un ledger descartable.")

    path = _ledger_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry), ensure_ascii=False))
        f.write("\n")
    logger.debug("trade_ledger: %s (%s) agregado", entry.trade_id, entry.outcome)


def read_ledger(
    *,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> list[TradeLedgerEntry]:
    """
    Lee el ledger completo, en el orden en que se escribió.

    DEDUPLICACIÓN POR `trade_id`, última ocurrencia gana -- mismo contrato
    que `gdelt_series.read_series()` y por el mismo motivo: un reprocesamiento
    corrige, no empeora.

    NO FILTRA POR OUTCOME, y nunca va a hacerlo. `since`/`until` filtran por
    `ts_entrada` porque una ventana temporal es una pregunta sobre CUÁNDO se
    operó; filtrar por resultado sería una pregunta sobre QUÉ pasó, y esa la
    contesta quien analiza, con todas las filas a la vista. Una línea de
    filtrado acá reintroduciría el sesgo de supervivencia en el único lugar
    donde nadie la buscaría.

    Líneas corruptas se saltean con un warning y no abortan la lectura de
    las demás -- misma degradación parcial que `read_series()`.

    Devuelve lista vacía si el archivo no existe: un ledger sin trades
    todavía es un estado válido, no un error.
    """
    path = _ledger_file_path()
    if not path.exists():
        return []

    por_id: dict[str, TradeLedgerEntry] = {}
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = TradeLedgerEntry(**json.loads(line))
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.warning(
                    "trade_ledger: línea %d corrupta en %s, se saltea: %s",
                    lineno, path, e)
                continue
            por_id[entry.trade_id] = entry   # última ocurrencia gana

    entries = list(por_id.values())
    if since is not None:
        entries = [e for e in entries
                   if datetime.fromisoformat(e.ts_entrada) >= since]
    if until is not None:
        entries = [e for e in entries
                   if datetime.fromisoformat(e.ts_entrada) <= until]
    return entries


def contar_por_outcome(entries: list[TradeLedgerEntry]) -> dict[str, int]:
    """Conteo por outcome, con TODOS los outcomes presentes aunque valgan
    cero. Un dict al que le falta la clave `perdedora` se lee como "no hubo
    perdedoras" o como "no se midió", y son cosas distintas."""
    conteo = {o.value: 0 for o in Outcome}
    for e in entries:
        conteo[e.outcome] = conteo.get(e.outcome, 0) + 1
    return conteo
