"""
ingestion/source_registry.py
=============================
LA MEMORIA. Registro versionado de qué proveedor cubre qué activo, con qué
profundidad y verificado cuándo.

POR QUÉ EXISTE. `tools/provider_coverage.py` sondea y reporta a stdout: el
resultado se pierde al cerrar la terminal, así que cada sesión vuelve a
preguntar lo mismo o —peor— asume. Ese hueco produjo dos fallos reales y
concretos, los dos registrados acá con su entrada propia:
  · NVDA se dio por descargable hasta chocar con el muro premium de Alpha
    Vantage.
  · NIFTY50 se mantuvo en el alcance del proyecto meses después de dejar de
    tener proveedor.
Un sondeo sin memoria no es conocimiento del proyecto, es conocimiento de
una terminal.

QUÉ NO ES: no es `SourceInventory` (`ingestion/sources.py`). Los dos parecen
"qué fuentes hay" y contestan preguntas distintas, y confundirlos sería
volver a tener dos verdades sobre lo mismo:

  SourceInventory  → foto de RUNTIME: qué se puede construir AHORA con las
                     credenciales que hay. Se levanta llamando a la API.
  SourceRegistry   → memoria HISTÓRICA: qué se midió contra la API real, con
                     qué resultado y en qué fecha. Se lee de un archivo
                     versionado en git; este módulo NUNCA toca la red.

Se comparten los idioms a propósito, porque la disciplina es la misma:
`frozen=True` (una medición no se parchea: una medición nueva es una entrada
nueva con fecha nueva), `__len__` que cuenta solo lo USABLE, `__contains__`
que pregunta por cobertura real y no por si el nombre es conocido, y motivos
en texto accionable.

TRES ESTADOS QUE NO SON LO MISMO. Es la distinción central del registro, y
la razón por la que existe el tercero:
  VERIFICADO_CON_DATOS     se pidió y llegaron datos. Hay fechas y filas.
  VERIFICADO_SIN_COBERTURA se pidió y el proveedor NO lo tiene o no lo da
                           gratis. Es un hecho medido, no una suposición.
  NO_VERIFICADO            no se sabe. Un 403 de Tiingo es autenticación, no
                           cobertura; tratarlo como "sin cobertura" sería
                           inventar un hecho que nadie midió.

PROFUNDIDAD VERIFICADA ≠ PROFUNDIDAD DISPONIBLE. Las 5.000 filas de FX_DAILY
y los 30 puntos de TwelveData son TOPES DE LA PETICIÓN, no el fondo del
archivo histórico. Un registro que dijera "5.000 filas" a secas invitaría a
planificar sobre un número que no es el que se cree. Por eso cada entrada
declara `profundidad`: COMPLETA, TOPE_DE_PETICION o NO_MEDIDA.

NINGUNA ENTRADA SIN FECHA DE VERIFICACIÓN. Las cuotas y los muros de pago
cambian: un dato de cobertura sin fecha no se puede evaluar. `load_registry()`
lo valida al cargar y falla si falta — no es una convención, es un invariante.

SIN RED Y SIN CREDENCIALES. Este módulo lee el registro, no lo produce. No
llama a `load_secret()`: el único call site autorizado sigue siendo
`ingestion/sources.py`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional, Sequence

logger = logging.getLogger("spel.ingestion.source_registry")

#: El archivo de datos vive al lado del módulo y VERSIONADO EN GIT, a
#: propósito: es exactamente lo que `governance/persistence.py` describe como
#: stream CONFIG ("versionado, con historial de git, revisable en PRs"). Que
#: un cambio de cobertura se vea en el diff de un PR es el punto — es lo que
#: convierte al registro en memoria y no en otro archivo que se desactualiza
#: en silencio.
REGISTRY_PATH = Path(__file__).with_name("source_registry.json")

#: Versión del esquema del archivo. Si cambia la forma de las entradas, sube,
#: y el lector puede rechazar un archivo que no entiende en vez de leerlo mal.
SCHEMA_VERSION = 1


class CoverageState:
    """Los tres estados. Ver el docstring del módulo para por qué el tercero
    no es prescindible."""
    VERIFICADO_CON_DATOS = "VERIFICADO_CON_DATOS"
    VERIFICADO_SIN_COBERTURA = "VERIFICADO_SIN_COBERTURA"
    NO_VERIFICADO = "NO_VERIFICADO"

    @classmethod
    def todos(cls) -> frozenset[str]:
        return frozenset({
            cls.VERIFICADO_CON_DATOS, cls.VERIFICADO_SIN_COBERTURA,
            cls.NO_VERIFICADO,
        })


class DepthKind:
    """Qué se midió cuando se midió la profundidad."""
    #: La respuesta no chocó ningún tope: lo que llegó es la historia.
    COMPLETA = "COMPLETA"
    #: La respuesta llegó al tope de la petición. La historia real es MAYOR
    #: o igual a lo verificado, y sigue sin conocerse.
    TOPE_DE_PETICION = "TOPE_DE_PETICION"
    #: No se sondeó la profundidad.
    NO_MEDIDA = "NO_MEDIDA"

    @classmethod
    def todos(cls) -> frozenset[str]:
        return frozenset({cls.COMPLETA, cls.TOPE_DE_PETICION, cls.NO_MEDIDA})


class RegistryError(Exception):
    """El archivo del registro no cumple su propio contrato. Se lanza al
    cargar: un registro mal formado leído a medias es peor que uno ausente,
    porque parece que hay memoria y la memoria está mal."""


@dataclass(frozen=True)
class RegistryEntry:
    """
    Una medición de un par (activo, proveedor).

    `frozen=True` por el mismo motivo que `SourceInventory`: una medición no
    se parchea. Si algo cambió —una cuota, un muro de pago, un endpoint
    nuevo— eso es una medición NUEVA, con su fecha, y el diff del PR muestra
    qué cambió y cuándo.
    """
    activo: str
    proveedor: str
    estado: str
    verificado_el: date
    verificado_como: str
    endpoint: Optional[str] = None
    simbolo: Optional[str] = None
    parametros: dict[str, Any] = field(default_factory=dict)
    filas_verificadas: Optional[int] = None
    primera_fecha: Optional[str] = None
    ultima_fecha: Optional[str] = None
    profundidad: str = DepthKind.NO_MEDIDA
    columnas_del_contrato_faltantes: tuple[str, ...] = ()
    trae_volumen: Optional[bool] = None
    trae_ajustado: Optional[bool] = None
    cuota_documentada: str = ""
    advertencias: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """Si esta entrada representa una fuente de la que HOY se pueden
        traer datos. Solo el primer estado califica: los otros dos son
        información, no capacidad."""
        return self.estado == CoverageState.VERIFICADO_CON_DATOS

    @property
    def cumple_contrato_ohlcv(self) -> bool:
        """Si no le falta ninguna columna del contrato. Una fuente usable
        puede igual no servir para OHLCV — XAU en Alpha Vantage trae datos
        reales y solo `date,price`."""
        return self.usable and not self.columnas_del_contrato_faltantes

    @property
    def profundidad_es_un_piso(self) -> bool:
        """True cuando `filas_verificadas` es un TOPE DE PETICIÓN: la
        historia real es mayor o igual, y planificar con ese número como si
        fuera el total es el error que este campo existe para evitar."""
        return self.profundidad == DepthKind.TOPE_DE_PETICION

    def antiguedad_dias(self, hoy: date) -> int:
        return (hoy - self.verificado_el).days


@dataclass(frozen=True)
class SourceRegistry:
    """
    El registro completo, ya cargado y validado.

    Mismos idioms que `SourceInventory` (`ingestion/sources.py`), y por el
    mismo motivo — ver el docstring del módulo para en qué se diferencian.
    """
    entradas: tuple[RegistryEntry, ...] = ()
    proveedores: dict[str, dict[str, Any]] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        """Cuántas entradas USABLES hay — no cuántas hay en total. Misma
        regla que `SourceInventory.__len__`: `if not registro:` tiene que
        significar "no tengo ninguna cobertura confirmada", no "el archivo
        está vacío de información"."""
        return sum(1 for e in self.entradas if e.usable)

    def __contains__(self, activo: object) -> bool:
        """`"NVDA" in registro` pregunta si HAY cobertura verificada para ese
        activo, no si el activo aparece en el archivo. NVDA aparece —con dos
        entradas— y la respuesta depende de si alguna es usable."""
        return any(e.activo == activo and e.usable for e in self.entradas)

    # ── consultas ────────────────────────────────────────────────────────

    def for_asset(self, activo: str) -> tuple[RegistryEntry, ...]:
        """Todas las entradas de un activo, usables o no. Devuelve también
        las NO_VERIFICADO: saber que Tiingo está sin sondear para NVDA es
        justamente la información que evita darlo por perdido."""
        return tuple(e for e in self.entradas if e.activo == activo)

    def for_provider(self, proveedor: str) -> tuple[RegistryEntry, ...]:
        return tuple(e for e in self.entradas if e.proveedor == proveedor)

    def get(self, activo: str, proveedor: str) -> Optional[RegistryEntry]:
        for e in self.entradas:
            if e.activo == activo and e.proveedor == proveedor:
                return e
        return None

    def usables(self) -> tuple[RegistryEntry, ...]:
        return tuple(e for e in self.entradas if e.usable)

    def activos_cubiertos(self) -> tuple[str, ...]:
        return tuple(sorted({e.activo for e in self.entradas if e.usable}))

    def activos_sin_cobertura(self) -> tuple[str, ...]:
        """Activos que aparecen en el registro y no tienen NINGUNA entrada
        usable. Incluye los que solo tienen NO_VERIFICADO — que no es lo
        mismo que "no hay fuente", y por eso conviene mirar
        `sin_verificar()` antes de darlos por perdidos."""
        todos = {e.activo for e in self.entradas}
        return tuple(sorted(todos - set(self.activos_cubiertos())))

    def sin_verificar(self) -> tuple[RegistryEntry, ...]:
        """Los huecos de conocimiento. Cada uno es una pregunta abierta, no
        una respuesta negativa."""
        return tuple(e for e in self.entradas
                     if e.estado == CoverageState.NO_VERIFICADO)

    def desactualizadas(self, hoy: date, *, max_dias: int) -> tuple[RegistryEntry, ...]:
        """
        Entradas verificadas hace más de `max_dias`.

        Existe porque el registro guarda hechos con fecha de vencimiento: una
        cuota gratuita puede volverse premium sin aviso —le pasó a NVDA— y
        una entrada vieja se lee con la misma confianza que una fresca si
        nadie compara la fecha contra hoy.
        """
        return tuple(e for e in self.entradas
                     if e.antiguedad_dias(hoy) > max_dias)

    def log_summary(self) -> None:
        """Mismo patrón que `SourceInventory.log_summary()`. Acá no hay
        credenciales que filtrar —el registro nunca las contiene— pero se
        reporta igual en términos de presencia y motivo."""
        logger.info(
            "[registry] %d entrada(s) usable(s) sobre %d, cubriendo: %s",
            len(self), len(self.entradas),
            ", ".join(self.activos_cubiertos()) or "ninguna",
        )
        for e in self.entradas:
            if not e.usable:
                logger.info("[registry] %s/%s — %s: %s",
                            e.activo, e.proveedor, e.estado, e.verificado_como)


# ══════════════════════════════════════════════════════════════════════════
#  Carga y validación
# ══════════════════════════════════════════════════════════════════════════

def _exigir(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise RegistryError(mensaje)


def _entrada_desde_dict(bruto: dict, indice: int) -> RegistryEntry:
    donde = f"entrada #{indice}"
    for campo in ("activo", "proveedor", "estado", "verificado_el",
                  "verificado_como"):
        _exigir(bruto.get(campo) not in (None, ""),
                f"{donde}: falta el campo obligatorio '{campo}'.")

    activo, proveedor = bruto["activo"], bruto["proveedor"]
    donde = f"{activo}/{proveedor}"

    estado = bruto["estado"]
    _exigir(estado in CoverageState.todos(),
            f"{donde}: estado '{estado}' desconocido. "
            f"Válidos: {sorted(CoverageState.todos())}.")

    profundidad = bruto.get("profundidad", DepthKind.NO_MEDIDA)
    _exigir(profundidad in DepthKind.todos(),
            f"{donde}: profundidad '{profundidad}' desconocida. "
            f"Válidas: {sorted(DepthKind.todos())}.")

    try:
        verificado_el = date.fromisoformat(bruto["verificado_el"])
    except (TypeError, ValueError) as exc:
        raise RegistryError(
            f"{donde}: 'verificado_el' no es una fecha ISO (YYYY-MM-DD): "
            f"{bruto['verificado_el']!r}. Un dato de cobertura sin fecha "
            f"legible no se puede evaluar."
        ) from exc

    # Coherencia: si dice que llegaron datos, tiene que haber algo que
    # mostrar. Un VERIFICADO_CON_DATOS sin filas es una contradicción que el
    # registro no debe poder expresar.
    if estado == CoverageState.VERIFICADO_CON_DATOS:
        _exigir(bruto.get("filas_verificadas") not in (None, 0),
                f"{donde}: estado VERIFICADO_CON_DATOS pero sin "
                f"'filas_verificadas'. Si no llegaron datos, el estado es "
                f"VERIFICADO_SIN_COBERTURA o NO_VERIFICADO.")

    return RegistryEntry(
        activo=activo, proveedor=proveedor, estado=estado,
        verificado_el=verificado_el,
        verificado_como=bruto["verificado_como"],
        endpoint=bruto.get("endpoint"),
        simbolo=bruto.get("simbolo"),
        parametros=dict(bruto.get("parametros") or {}),
        filas_verificadas=bruto.get("filas_verificadas"),
        primera_fecha=bruto.get("primera_fecha"),
        ultima_fecha=bruto.get("ultima_fecha"),
        profundidad=profundidad,
        columnas_del_contrato_faltantes=tuple(
            bruto.get("columnas_del_contrato_faltantes") or ()),
        trae_volumen=bruto.get("trae_volumen"),
        trae_ajustado=bruto.get("trae_ajustado"),
        cuota_documentada=bruto.get("cuota_documentada", ""),
        advertencias=tuple(bruto.get("advertencias") or ()),
    )


def load_registry(path: Optional[Path] = None) -> SourceRegistry:
    """
    Carga y VALIDA el registro. Sin red, sin credenciales.

    Falla con `RegistryError` si el archivo no cumple su contrato — un
    registro mal formado leído a medias es peor que uno ausente, porque
    parece que hay memoria y la memoria está mal.
    """
    ruta = path or REGISTRY_PATH
    if not ruta.is_file():
        raise RegistryError(
            f"No existe el archivo del registro: {ruta}. Este módulo LEE el "
            f"registro, no lo produce — si falta, hay que restaurarlo desde "
            f"git, no regenerarlo adivinando."
        )

    try:
        bruto = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RegistryError(f"{ruta} no es JSON válido: {exc}") from exc

    version = bruto.get("schema_version")
    _exigir(version == SCHEMA_VERSION,
            f"{ruta}: schema_version {version!r} != {SCHEMA_VERSION}. "
            f"El lector no interpreta versiones que no conoce en vez de "
            f"leerlas mal.")

    entradas_brutas = bruto.get("entradas")
    _exigir(isinstance(entradas_brutas, list),
            f"{ruta}: 'entradas' debe ser una lista.")

    entradas = tuple(_entrada_desde_dict(e, i)
                     for i, e in enumerate(entradas_brutas))

    vistos: set[tuple[str, str]] = set()
    for e in entradas:
        clave = (e.activo, e.proveedor)
        _exigir(clave not in vistos,
                f"{e.activo}/{e.proveedor}: par duplicado. Una medición "
                f"nueva REEMPLAZA a la vieja en su entrada; dos entradas "
                f"del mismo par son dos verdades sobre lo mismo.")
        vistos.add(clave)

    return SourceRegistry(
        entradas=entradas,
        proveedores=dict(bruto.get("proveedores") or {}),
        meta=dict(bruto.get("meta") or {}),
    )


def render_text(registro: SourceRegistry, *, hoy: Optional[date] = None) -> str:
    """Vista legible del registro. Pensada para leerse de un vistazo y para
    que un cambio de cobertura salte en el diff de un PR."""
    out = [
        "═══ REGISTRO DE COBERTURA POR FUENTE ═══",
        f"{len(registro)} entrada(s) usable(s) sobre {len(registro.entradas)}.",
        f"Activos con cobertura verificada: "
        f"{', '.join(registro.activos_cubiertos()) or 'ninguno'}",
        f"Activos sin cobertura usable: "
        f"{', '.join(registro.activos_sin_cobertura()) or 'ninguno'}",
        "",
        "activo    proveedor      estado                    filas  profundidad"
        "        verificado   falta del contrato",
    ]
    for e in sorted(registro.entradas, key=lambda x: (x.activo, x.proveedor)):
        out.append(
            f"{e.activo:<9} {e.proveedor:<14} {e.estado:<24} "
            f"{str(e.filas_verificadas or '-'):>6}  {e.profundidad:<18} "
            f"{e.verificado_el}   "
            f"{','.join(e.columnas_del_contrato_faltantes) or '-'}"
        )
    if hoy is not None:
        viejas = registro.desactualizadas(hoy, max_dias=90)
        if viejas:
            out.append("")
            out.append(f"⚠️  {len(viejas)} entrada(s) con más de 90 días. "
                       f"Las cuotas y los muros de pago cambian.")
    return "\n".join(out)


__all__ = [
    "CoverageState", "DepthKind", "REGISTRY_PATH", "RegistryEntry",
    "RegistryError", "SCHEMA_VERSION", "SourceRegistry", "load_registry",
    "render_text",
]
