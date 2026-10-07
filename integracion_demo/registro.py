"""
integracion_demo/registro.py
============================
El registro de la ejecución demo: una línea JSONL por evento de contrato,
append-only, encadenada por hashes (brief del Admin del 06-oct-2026 (3),
punto 3d, y decisiones 1a, 1b y 1d).

══ PORTA EL PATRÓN DE core/trade_ledger.py; NO LO IMPORTA ══

trade_ledger.py está congelado (acta del 21-sep-2026) y su condición de
reversión no se cumple: los multiplicadores de Deriv cobran una comisión
única, no taker/maker ni funding (decisión 1a). Lo que se trae de ahí es el
patrón, citado línea por línea donde se usa:
  · Nunca se filtra, borra ni reescribe una fila (trade_ledger.py:14-47).
  · El outcome es explícito, y una orden rechazada o no enviada también
    entra (trade_ledger.py:28-31).
  · Escribir en el fallback local lanza, salvo pedido explícito
    (trade_ledger.py:49-61, 92-96, 223-224, 235-241).
  · `open("a")` + `json.dumps(..., ensure_ascii=False)` + "\\n"
    (trade_ledger.py:243-247).
  · La lectura devuelve las filas Y sus descartes: líneas corruptas con su
    número y duplicados contados (trade_ledger.py:182-216, 282-311).

Diferencias, y por qué:
  · La deduplicación es por `row_hash`, no por `trade_id`
    (trade_ledger.py:304): acá un trade tiene varias filas, una por evento.
    Lo que se descarta es una línea repetida byte a byte.
  · Agregar LEE la cola del archivo (trade_ledger.py:229-230 dice que
    append no lee): la cadena necesita el `row_hash` y el `seq` de la última
    fila. Si el archivo ya está roto (líneas corruptas o cadena rota), no se
    abre para escribir: una orden cuya fila no se puede encadenar no se
    manda.
  · Formato JSONL, sin Parquet ni pyarrow (decisión 1b).

══ LA CADENA ══

  row_hash = sha256(prev_hash + JSON canónico de la fila sin row_hash)

con JSON canónico = claves ordenadas, sin espacios, UTF-8. La primera fila
encadena desde HASH_INICIAL. `verificar_cadena` detecta una fila editada
(su hash no coincide) o borrada del medio (la siguiente no encadena, y el
`seq` salta). Lo que la cadena sola NO detecta es que se borre la ÚLTIMA
fila: para eso está `Registro.cabeza` (seq y row_hash de la última fila),
que el informe publica y contra la que se compara después.

══ LAS RESPUESTAS CRUDAS VAN APARTE ══

`crudo.jsonl`, una línea por respuesta de Deriv, con su sha256, escrita
ANTES de parsearla. La fila del registro la referencia por `raw_sha256`.
Lo crudo trae el account_id sin tapar (Deriv lo manda en
proposal_open_contract): es un archivo local, no un informe.

══ OUTCOME ══

El de las filas terminales es uno de `Outcome`, con su evidencia. Nunca se
inventa: si no se deduce, es `desconocido` (punto 3d). Las filas de eventos
intermedios (envío, compra confirmada, seguimiento, venta enviada) llevan
outcome None: todavía no hay un resultado que nombrar.
[INTERPRETACIÓN] El brief lista los campos y no los eventos; `evento` y
`evidencia` son las dos columnas que se agregan a su lista. `evidencia` es
la del outcome ("con su evidencia", punto 3d) y, en una fila de
reconciliación, la de la marca ok o discrepancia.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Optional

from governance.persistence import (
    LOCAL_FALLBACK_DRIVE_ROOT,
    PersistenceStream,
    drive_root,
    stream_path,
)
from ingestion.kappa_deriv import KAPPA_DERIV

SCHEMA_VERSION = 1

#: El prev_hash de la primera fila.
HASH_INICIAL = "0" * 64

ARCHIVO_REGISTRO = "registro.jsonl"
ARCHIVO_CRUDO = "crudo.jsonl"

#: [INTERPRETACIÓN] Subdirectorio dentro del stream TRADE_LEDGER de Drive.
#: No es un stream nuevo: tests/test_persistence.py fija el conjunto exacto
#: de streams, y agregar uno exige editarlo.
SUBDIRECTORIO_DEMO = "demo"

#: Versión del modelo de κ con que se calcula `commission_modelada`.
KAPPA_MODEL_VERSION = "kappa_deriv@decision-log-2026-10-06"


class Outcome(str, Enum):
    SL = "sl"
    TP = "tp"
    STOP_OUT = "stop_out"
    SELL_MANUAL = "sell_manual"
    SELL_TIEMPO = "sell_tiempo"
    CANCELACION = "cancelacion"
    NO_EJECUTADA = "no_ejecutada"
    RECHAZADA = "rechazada"
    DESCONOCIDO = "desconocido"


class Reconciliado(str, Enum):
    PENDIENTE = "pendiente"
    OK = "ok"
    DISCREPANCIA = "discrepancia"


class Evento(str, Enum):
    ENVIO = "envio"                   # antes de mandar el buy
    COMPRA = "compra"                 # respuesta del buy
    SEGUIMIENTO = "seguimiento"       # proposal_open_contract con el contrato abierto
    ACTUALIZACION = "actualizacion"   # contract_update confirmado
    VENTA = "venta"                   # respuesta del sell
    CIERRE = "cierre"                 # el contrato terminó
    RECHAZO = "rechazo"               # el buy volvió con error
    NO_ENVIADA = "no_enviada"         # la orden no salió
    SIN_RESPUESTA = "sin_respuesta"   # se mandó y no hubo ack
    RECONCILIACION = "reconciliacion"


#: Eventos que cierran un trade: llevan outcome obligatorio.
EVENTOS_TERMINALES: frozenset[Evento] = frozenset({
    Evento.CIERRE, Evento.RECHAZO, Evento.NO_ENVIADA, Evento.SIN_RESPUESTA})

#: Los campos de una fila, en el orden del brief (punto 3d), más `evento` y
#: `evidencia`.
CAMPOS: tuple[str, ...] = (
    "seq", "evento", "trade_uuid", "contract_id", "buy_transaction_id",
    "sell_transaction_id", "schema_version",
    "experiment_id", "preregistro_sha256", "git_commit_sha", "kappa_model_version",
    "account_id", "account_type", "ws_path",
    "underlying_symbol", "contract_type", "stake", "multiplier", "nocional", "currency",
    "sl_solicitado", "sl_confirmado", "tp_confirmado",
    "quote_at_signal", "entry_spot", "exit_spot", "sl_precio",
    "slip_entrada", "slip_salida",
    "commission_cruda", "commission_usd", "commission_modelada", "cost_ratio",
    "profit_deriv", "pnl_recalculado", "R_usd", "r_multiple",
    "outcome", "evidencia",
    "t_senal_ms", "t_envio_ms", "t_ack_ms", "purchase_time", "sell_time", "latencia_ms",
    "raw_sha256", "prev_hash", "row_hash", "reconciliado",
)

#: Lo que pone el registro, no quien llama.
_CAMPOS_DEL_REGISTRO = frozenset({"seq", "schema_version", "prev_hash", "row_hash"})


class RegistroEnFallbackError(RuntimeError):
    """Portado de trade_ledger.py:92-96. Se intentó escribir el registro en
    el fallback local de drive_root() sin pedirlo explícitamente."""


class RegistroRotoError(RuntimeError):
    """El archivo tiene líneas corruptas o la cadena no cierra: no se
    agrega nada encima."""


# ═══ La cadena ════════════════════════════════════════════════════════════

def canonico(fila: dict) -> str:
    return json.dumps(fila, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False)


def hash_de_fila(prev_hash: str, fila: dict) -> str:
    sin_hash = {k: v for k, v in fila.items() if k != "row_hash"}
    return hashlib.sha256((prev_hash + canonico(sin_hash)).encode("utf-8")).hexdigest()


def verificar_cadena(filas: list[dict]) -> list[str]:
    """Los problemas de la cadena, en orden; vacía si cierra. Detecta una
    fila editada (hash), una borrada del medio (prev_hash y seq) y una
    insertada. No detecta que falte la última: eso se compara contra la
    cabeza publicada."""
    problemas = []
    prev, seq = HASH_INICIAL, 0
    for i, f in enumerate(filas):
        if f.get("prev_hash") != prev:
            problemas.append(f"fila {i + 1} (seq {f.get('seq')}): prev_hash no encadena")
        if f.get("seq") != seq + 1:
            problemas.append(f"fila {i + 1}: seq {f.get('seq')} después de {seq}")
        if hash_de_fila(f.get("prev_hash", ""), f) != f.get("row_hash"):
            problemas.append(f"fila {i + 1} (seq {f.get('seq')}): row_hash no coincide")
        prev = f.get("row_hash")
        seq = f["seq"] if isinstance(f.get("seq"), int) else seq + 1
    return problemas


# ═══ Lectura ══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class RegistroLeido:
    """Portado de LedgerReadResult (trade_ledger.py:182-216): las filas y
    los descartes en el valor de retorno, no en un log."""
    filas: list[dict]
    #: Números de línea (1-based) que no se pudieron parsear.
    lineas_corruptas: tuple[int, ...]
    #: Líneas idénticas a una anterior (mismo row_hash), descartadas.
    n_deduplicadas: int
    #: Lo que `verificar_cadena` encontró sobre las filas deduplicadas.
    problemas_cadena: tuple[str, ...] = ()

    @property
    def n_lineas_corruptas(self) -> int:
        return len(self.lineas_corruptas)

    @property
    def integro(self) -> bool:
        return not self.lineas_corruptas and not self.problemas_cadena


def leer_registro(directorio: Path) -> RegistroLeido:
    """Lee el registro entero en el orden en que se escribió. Archivo
    inexistente: registro vacío, no un error (trade_ledger.py:282-285)."""
    path = Path(directorio) / ARCHIVO_REGISTRO
    if not path.exists():
        return RegistroLeido(filas=[], lineas_corruptas=(), n_deduplicadas=0)
    filas: list[dict] = []
    vistos: set[str] = set()
    corruptas: list[int] = []
    duplicadas = 0
    with path.open("r", encoding="utf-8") as f:
        for lineno, linea in enumerate(f, start=1):
            linea = linea.strip()
            if not linea:
                continue
            # trade_ledger.py:295-302: una línea ilegible se saltea con su
            # número y no aborta la lectura de las demás.
            try:
                fila = json.loads(linea)
                if not isinstance(fila, dict) or not isinstance(fila.get("row_hash"), str):
                    raise ValueError("no es una fila del registro")
            except (json.JSONDecodeError, ValueError):
                corruptas.append(lineno)
                continue
            # trade_ledger.py:304 deduplica por trade_id; acá por row_hash.
            if fila["row_hash"] in vistos:
                duplicadas += 1
                continue
            vistos.add(fila["row_hash"])
            filas.append(fila)
    return RegistroLeido(filas=filas, lineas_corruptas=tuple(corruptas),
                         n_deduplicadas=duplicadas,
                         problemas_cadena=tuple(verificar_cadena(filas)))


def leer_crudo(directorio: Path) -> dict[str, str]:
    """sha256 -> texto crudo, de todas las respuestas guardadas."""
    path = Path(directorio) / ARCHIVO_CRUDO
    out: dict[str, str] = {}
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8") as f:
        for linea in f:
            try:
                d = json.loads(linea)
                out[d["sha256"]] = d["texto"]
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    return out


# ═══ Escritura ════════════════════════════════════════════════════════════

def directorio_por_defecto() -> Path:
    return Path(stream_path(PersistenceStream.TRADE_LEDGER)) / SUBDIRECTORIO_DEMO


def _esta_en_fallback() -> bool:
    # trade_ledger.py:223-224
    return drive_root() == LOCAL_FALLBACK_DRIVE_ROOT


def _agregar_linea(path: Path, texto: str) -> None:
    # trade_ledger.py:243-247, más fsync: una fila que quedó en un buffer
    # cuando se cortó el proceso es una orden sin registro.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(texto)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())


def _validar_valor(campo: str, v: Any) -> None:
    if isinstance(v, float) and not math.isfinite(v):
        raise ValueError(f"{campo}={v!r}: el registro no guarda NaN ni infinitos")


class Registro:
    """El registro abierto para agregar. Al abrirlo lee el archivo y se
    niega a seguir si no está íntegro."""

    def __init__(self, directorio: Optional[Path] = None, *,
                 permitir_fallback: bool = False) -> None:
        if directorio is None:
            # trade_ledger.py:235-241: el fallback local no lo lee nadie.
            if _esta_en_fallback() and not permitir_fallback:
                raise RegistroEnFallbackError(
                    f"drive_root() resolvió a {LOCAL_FALLBACK_DRIVE_ROOT}, el fallback "
                    f"de desarrollo: nadie lo lee y nada lo respalda. Define "
                    f"SPEL_DRIVE_ROOT, pasa un directorio explícito, o "
                    f"permitir_fallback=True para un registro descartable.")
            directorio = directorio_por_defecto()
        self.directorio = Path(directorio)
        leido = leer_registro(self.directorio)
        if not leido.integro:
            raise RegistroRotoError(
                f"{self.directorio / ARCHIVO_REGISTRO}: líneas corruptas "
                f"{list(leido.lineas_corruptas)}, cadena {list(leido.problemas_cadena)}. "
                f"No se agrega nada encima.")
        ultima = leido.filas[-1] if leido.filas else None
        self._seq = ultima["seq"] if ultima else 0
        self._prev = ultima["row_hash"] if ultima else HASH_INICIAL

    @property
    def cabeza(self) -> tuple[int, str]:
        """(seq, row_hash) de la última fila: lo que se publica para que
        borrar la última fila también se note."""
        return self._seq, self._prev

    def guardar_crudo(self, texto: str, t_local_ms: int) -> str:
        """Guarda una respuesta cruda y devuelve su sha256. Se llama ANTES
        de parsearla."""
        sha = hashlib.sha256(texto.encode("utf-8")).hexdigest()
        _agregar_linea(self.directorio / ARCHIVO_CRUDO,
                       json.dumps({"sha256": sha, "t_local_ms": t_local_ms, "texto": texto},
                                  ensure_ascii=False))
        return sha

    def agregar(self, **campos: Any) -> dict:
        """Agrega una fila. Los campos que no se pasan quedan en None; un
        campo fuera de CAMPOS, un outcome fuera de Outcome, una fila
        terminal sin outcome, o un account_id con dígitos, lanzan sin
        escribir."""
        fuera = set(campos) - set(CAMPOS)
        if fuera:
            raise ValueError(f"campos fuera del esquema: {sorted(fuera)}")
        propios = set(campos) & _CAMPOS_DEL_REGISTRO
        if propios:
            raise ValueError(f"{sorted(propios)} los pone el registro")
        evento = Evento(campos.get("evento"))
        outcome = campos.get("outcome")
        if outcome is not None:
            outcome = Outcome(outcome).value
        if evento in EVENTOS_TERMINALES and outcome is None:
            raise ValueError(f"evento {evento.value!r} sin outcome: si no se deduce, "
                             f"es {Outcome.DESCONOCIDO.value!r}")
        if outcome is not None and not campos.get("evidencia"):
            raise ValueError("un outcome sin evidencia no entra")
        reconciliado = Reconciliado(campos.get("reconciliado") or Reconciliado.PENDIENTE).value
        cuenta = campos.get("account_id")
        if cuenta is not None and re.search(r"\d", str(cuenta)):
            raise ValueError("account_id sin enmascarar: el registro guarda solo el prefijo")
        if not campos.get("trade_uuid"):
            raise ValueError("toda fila lleva el trade_uuid, creado antes de enviar")
        for k, v in campos.items():
            _validar_valor(k, v)
        fila = {c: campos.get(c) for c in CAMPOS}
        fila.update(evento=evento.value, outcome=outcome, reconciliado=reconciliado,
                    seq=self._seq + 1, schema_version=SCHEMA_VERSION, prev_hash=self._prev)
        fila["row_hash"] = hash_de_fila(self._prev, fila)
        _agregar_linea(self.directorio / ARCHIVO_REGISTRO,
                       json.dumps(fila, ensure_ascii=False, allow_nan=False))
        self._seq, self._prev = fila["seq"], fila["row_hash"]
        return fila


def contar_por_outcome(filas: Iterable[dict]) -> dict[str, int]:
    """Portado de trade_ledger.py:325-332: todos los outcomes presentes
    aunque valgan cero. Solo cuenta filas terminales."""
    conteo = {o.value: 0 for o in Outcome}
    for f in filas:
        if f.get("evento") in {e.value for e in EVENTOS_TERMINALES}:
            conteo[f["outcome"]] = conteo.get(f["outcome"], 0) + 1
    return conteo


# ═══ Derivados de un contrato cerrado ═════════════════════════════════════

def num(x: Any) -> Optional[float]:
    """float de un número o de un string numérico; None si no hay."""
    if isinstance(x, bool) or x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def direccion(contract_type: str) -> int:
    if contract_type == "MULTUP":
        return 1
    if contract_type == "MULTDOWN":
        return -1
    raise ValueError(f"{contract_type!r}: solo MULTUP o MULTDOWN")


def _nivel(poc: dict, orden: str) -> Optional[float]:
    return num(((poc.get("limit_order") or {}).get(orden) or {}).get("value"))


def _monto(poc: dict, orden: str) -> Optional[float]:
    o = (poc.get("limit_order") or {}).get(orden) or {}
    v = num(o.get("order_amount"))
    return v if v is not None else num(o.get("display_order_amount"))


def deducir_outcome(poc: dict, *, venta_propia: Optional[dict] = None
                    ) -> tuple[Optional[str], Optional[str]]:
    """(outcome, evidencia) de un proposal_open_contract. (None, None) si el
    contrato sigue abierto.

    `venta_propia` es {"motivo": "tiempo"|"manual", "transaction_id": ...,
    "raw_sha256": ...} cuando NUESTRO sell volvió confirmado: entonces es
    sell_tiempo o sell_manual. Si no, se mira el exit_spot contra los
    niveles que Deriv mismo devolvió (stop_loss, stop_out, take_profit) y
    entra el ÚNICO que cruzó; ninguno o más de uno es `desconocido`."""
    if poc.get("status") == "cancelled":
        return Outcome.CANCELACION.value, "status=cancelled"
    if not poc.get("is_sold") and poc.get("status") not in ("sold", "won", "lost"):
        return None, None
    if venta_propia is not None:
        o = Outcome.SELL_TIEMPO if venta_propia.get("motivo") == "tiempo" else Outcome.SELL_MANUAL
        return o.value, (f"sell propio confirmado: transaction_id "
                         f"{venta_propia.get('transaction_id')}, raw "
                         f"{venta_propia.get('raw_sha256')}")
    salida = num(poc.get("exit_spot"))
    try:
        d = direccion(str(poc.get("contract_type")))
    except ValueError:
        return Outcome.DESCONOCIDO.value, f"contract_type {poc.get('contract_type')!r}"
    if salida is None:
        return Outcome.DESCONOCIDO.value, "sin exit_spot"
    cruzados = []
    for orden, outcome, en_contra in (("stop_loss", Outcome.SL, True),
                                      ("stop_out", Outcome.STOP_OUT, True),
                                      ("take_profit", Outcome.TP, False)):
        nivel = _nivel(poc, orden)
        if nivel is None:
            continue
        cruzo = d * (salida - nivel) <= 0 if en_contra else d * (salida - nivel) >= 0
        if cruzo:
            cruzados.append((outcome, orden, nivel))
    if len(cruzados) == 1:
        o, orden, nivel = cruzados[0]
        return o.value, f"exit_spot {salida} cruzó {orden}.value {nivel}"
    if not cruzados:
        return Outcome.DESCONOCIDO.value, f"exit_spot {salida} no cruzó ningún nivel devuelto"
    return Outcome.DESCONOCIDO.value, ("exit_spot " + str(salida) + " cruzó varios: "
                                       + ", ".join(f"{o}={n}" for _, o, n in cruzados))


def derivados_de_cierre(poc: dict, *, stake: float, multiplier: float,
                        quote_at_signal: Optional[float],
                        quote_at_exit_signal: Optional[float],
                        outcome: Optional[str]) -> dict:
    """Los campos calculados de una fila de cierre. Lo que falta en el poc
    queda en None: el parser tolera claves faltantes (punto 6)."""
    d = direccion(str(poc.get("contract_type")))
    nocional = stake * multiplier
    entrada, salida = num(poc.get("entry_spot")), num(poc.get("exit_spot"))
    sl_confirmado = _monto(poc, "stop_loss")
    R_usd = abs(sl_confirmado) if sl_confirmado else None
    profit = num(poc.get("profit"))
    com_cruda = poc.get("commission")
    moneda = poc.get("currency")
    com_usd = num(com_cruda) if moneda == "USD" else None
    kappa = KAPPA_DERIV.get(str(poc.get("underlying_symbol")))
    com_modelada = kappa * nocional if kappa is not None else None
    nivel_esperado = {Outcome.SL.value: _nivel(poc, "stop_loss"),
                      Outcome.STOP_OUT.value: _nivel(poc, "stop_out"),
                      Outcome.TP.value: _nivel(poc, "take_profit"),
                      Outcome.SELL_TIEMPO.value: quote_at_exit_signal,
                      Outcome.SELL_MANUAL.value: quote_at_exit_signal}.get(outcome)
    return {
        "nocional": nocional,
        "entry_spot": entrada, "exit_spot": salida,
        "sl_confirmado": sl_confirmado, "tp_confirmado": _monto(poc, "take_profit"),
        "sl_precio": _nivel(poc, "stop_loss"),
        # Positivo = en contra. Entrada: contra la cotización de la señal.
        # Salida: contra el nivel que debía cerrarlo (o la cotización con
        # que se decidió vender).
        "slip_entrada": (d * (entrada - quote_at_signal)
                         if entrada is not None and quote_at_signal is not None else None),
        "slip_salida": (d * (nivel_esperado - salida)
                        if salida is not None and nivel_esperado is not None else None),
        "commission_cruda": None if com_cruda is None else str(com_cruda),
        "commission_usd": com_usd,
        "commission_modelada": com_modelada,
        "cost_ratio": (com_usd / com_modelada
                       if com_usd is not None and com_modelada else None),
        "profit_deriv": None if poc.get("profit") is None else str(poc.get("profit")),
        "pnl_recalculado": (d * (salida - entrada) / entrada * nocional - com_usd
                            if None not in (entrada, salida, com_usd) and entrada else None),
        "R_usd": R_usd,
        "r_multiple": profit / R_usd if profit is not None and R_usd else None,
        "purchase_time": poc.get("purchase_time"), "sell_time": poc.get("sell_time"),
        "sell_transaction_id": (poc.get("transaction_ids") or {}).get("sell"),
    }
