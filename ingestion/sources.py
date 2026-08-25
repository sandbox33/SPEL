"""
ingestion/sources.py
=====================
EL PUNTO DE COMPOSICIÓN. Único lugar del código de producción donde las
credenciales se resuelven y los adapters se construyen de verdad.

POR QUÉ ESTE ARCHIVO EXISTE. La auditoría del 18 ago dejó registrado en
`ESTADO.md` un hueco que no era "falta un adapter" sino algo más incómodo:
**no había ningún lugar donde algo se armara y corriera**. Verificado por
grep en las tres mitades: nada instanciaba un adapter fuera de tests,
`load_secret()` no se llamaba desde producción, y ningún workflow inyectaba
secretos. Las piezas existían y estaban probadas; nadie las conectaba. Este
módulo es la primera de esas tres mitades, y el workflow `live-tests.yml`
es la tercera.

LA REGLA QUE SOSTIENE EL DISEÑO: los adapters reciben credenciales por
constructor y NUNCA leen el entorno (ver `TwelveDataAdapter.__init__`). Eso
los hace testeables sin ensuciar `os.environ`, pero deja una pregunta
abierta: ¿quién las lee entonces? Acá. Un solo lugar. Si mañana hay tres
lugares que llaman a `load_secret()`, "¿por qué no arrancó tal fuente?"
vuelve a ser una búsqueda en vez de una lectura. Hay un test de
arquitectura (`tests/test_sources.py`) que verifica con AST que
`ingestion/adapters.py` no importe `governance.secrets`.

UNA FUENTE SIN CREDENCIAL NO ES UN ERROR — es una capacidad ausente, y esa
distinción es la razón de ser de `SourceInventory`. Levantar el inventario
NUNCA lanza por una credencial que falta: devuelve qué se pudo construir y,
para lo que no, POR QUÉ, nombrando la variable exacta. Un motor que se cae
entero al arrancar porque falta una clave opcional es peor que uno que
arranca degradado y lo dice — y "lo dice" tiene que ser accionable, no un
`False` en un dict.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from governance.secrets import SecretKey, load_secret
from ingestion.adapters import BaseAdapter, DerivAdapter, TwelveDataAdapter

logger = logging.getLogger("spel.ingestion.sources")


@dataclass(frozen=True)
class SourceInventory:
    """
    Qué fuentes de precio hay disponibles AHORA, y por qué faltan las que
    faltan.

    `frozen=True` a propósito: el inventario es una foto del momento en que
    se levantó, no un registro mutable al que se le agregan fuentes después.
    Si cambian las credenciales, se vuelve a llamar a `build_price_sources()`
    y se obtiene una foto nueva — no se parchea la vieja, que es como se
    llega a dos partes del sistema creyendo cosas distintas sobre qué hay
    conectado.

    disponibles: nombre de fuente -> adapter ya construido y usable.
    ausentes:    nombre de fuente -> motivo, EN TEXTO ACCIONABLE. El motivo
                 nombra la variable de entorno exacta que falta, porque un
                 "no disponible" que no dice qué configurar obliga a leer
                 este archivo para averiguarlo.
    """

    disponibles: dict[str, BaseAdapter] = field(default_factory=dict)
    ausentes: dict[str, str] = field(default_factory=dict)

    def __len__(self) -> int:
        """Cuántas fuentes USABLES hay. Las ausentes no cuentan: `if not
        inventario:` tiene que significar 'no puedo traer un precio', no
        'el inventario está vacío de información'."""
        return len(self.disponibles)

    def __contains__(self, nombre: object) -> bool:
        """`"deriv" in inventario` pregunta por disponibilidad real, no por
        si la fuente es conocida. Una fuente sin credencial responde False."""
        return nombre in self.disponibles

    def log_summary(self) -> None:
        """Reporta PRESENCIA, nunca valores — misma regla que
        `governance.secrets.secrets_status_report()`. Acá no se loguea ni un
        prefijo ni los últimos cuatro caracteres de ninguna credencial: un
        secreto parcial sigue siendo un secreto filtrado, y en un log de CI
        queda para siempre."""
        logger.info(
            "[sources] %d fuente(s) de precio disponible(s): %s",
            len(self.disponibles),
            ", ".join(sorted(self.disponibles)) or "ninguna",
        )
        for nombre, motivo in sorted(self.ausentes.items()):
            logger.info("[sources] '%s' no disponible — %s", nombre, motivo)


def _motivo_faltan(*claves: str) -> str:
    """Texto accionable a partir de las variables que faltan. Se nombra la
    variable, nunca su valor (que justamente no existe si estamos acá)."""
    if len(claves) == 1:
        return f"falta {claves[0]}"
    return f"faltan {', '.join(claves)}"


def build_price_sources(*, timeout_s: float = 10.0) -> SourceInventory:
    """
    Levanta el inventario de fuentes de precio a partir de las credenciales
    que de verdad estén configuradas.

    NUNCA lanza por una credencial ausente. Las únicas excepciones que
    pueden salir de acá son bugs reales de construcción de un adapter, y
    esas SÍ deben propagarse: un `ValueError` porque se le pasó una key
    vacía a un adapter es un error de este archivo, no una capacidad
    ausente, y taparlo con un `except` genérico lo convertiría en "esa
    fuente no estaba disponible" — un fallo silencioso, que es exactamente
    lo que `ingestion/` existe para no producir.

    timeout_s se propaga a todos los adapters: un timeout distinto por
    fuente es una decisión de operación, no un default por proveedor, y
    hasta que exista un motivo medido para diferenciarlos, uno solo.
    """
    disponibles: dict[str, BaseAdapter] = {}
    ausentes: dict[str, str] = {}

    # ── TwelveData: una sola credencial ──────────────────────────────────
    td_key = load_secret(SecretKey.TWELVEDATA_API_KEY, required=False)
    if td_key:
        disponibles["twelvedata"] = TwelveDataAdapter(
            api_key=td_key, timeout_s=timeout_s,
        )
    else:
        ausentes["twelvedata"] = _motivo_faltan(SecretKey.TWELVEDATA_API_KEY)

    # ── Deriv: DOS credenciales, y hacen falta las dos ───────────────────
    # Con una sola no se construye nada. El motivo dice CUÁL falta, no un
    # "credenciales incompletas" que obliga a adivinar entre las dos —
    # tener el token y que falte el app_id es un caso real y frecuente
    # (el token se rota, el app_id se olvida en el otro entorno).
    deriv_token = load_secret(SecretKey.DERIV_API_TOKEN, required=False)
    deriv_app_id = load_secret(SecretKey.DERIV_APP_ID, required=False)
    faltantes = tuple(
        clave for clave, valor in (
            (SecretKey.DERIV_API_TOKEN, deriv_token),
            (SecretKey.DERIV_APP_ID, deriv_app_id),
        ) if not valor
    )
    if faltantes:
        ausentes["deriv"] = _motivo_faltan(*faltantes)
    else:
        disponibles["deriv"] = DerivAdapter(
            api_token=deriv_token, app_id=deriv_app_id, timeout_s=timeout_s,
        )

    return SourceInventory(disponibles=disponibles, ausentes=ausentes)
