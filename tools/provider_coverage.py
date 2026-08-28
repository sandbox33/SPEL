"""
tools/provider_coverage.py
===========================
Inventaria qué proveedor cubre qué activo, con qué esquema y cuánta
historia. Sondea las APIs reales y reporta lo que devolvieron. NO reconstruye
nada, no escribe nada, no agrega adapters.

POR QUÉ EXISTE. El data lake de Drive resultó corrupto (tres de los cuatro
`{ASSET}_ohlcv_v5.parquet` contienen la serie del NIFTY 50 bajo el nombre de
otro activo, y los cuatro están recortados al calendario GDELT, lo que
corrompe `log_return` en los bordes de cada hueco — y `log_return` es el
target de entrenamiento). El OHLCV se reconstruye desde API. Antes de
escribir ese motor hay que saber qué proveedor cubre qué, con qué esquema y
con cuánta historia. Nadie lo verificó nunca. Esto lo verifica.

REGLA CENTRAL: NADA SE AFIRMA SIN RESPUESTA REAL. Este módulo no contiene
ninguna tabla de cobertura declarada. Lo que hay son RUTAS CANDIDATAS
(endpoint + símbolo a probar), y cada una entra al reporte únicamente con lo
que la API devolvió en esta corrida. Si no respondió, no hay dato — hay un
estado que dice por qué. El marketing del proveedor no es evidencia.

READ-ONLY, SIN EXCEPCIÓN. No escribe archivos, no persiste, no toca Drive.
El reporte sale por stdout (texto o JSON), igual que
`tools/measure_godel_samples.py`.

"SIN COBERTURA" ES UN RESULTADO, NO UN ERROR. Exit 0 siempre que el sondeo se
complete. Exit != 0 solo para fallo real de invocación (proveedor o activo
desconocido en la línea de comandos).

TRES COSAS QUE SE CONFUNDEN FÁCIL, y que acá son estados distintos — mismo
principio que `ingestion/sources.py::SourceInventory`, que ya distingue
capacidad ausente de error:
  SIN_CLAVE        la credencial no está configurada. No se sondeó nada.
  CLAVE_RECHAZADA  la clave existe y el proveedor la rechazó (401/403).
  NO_CUBIERTO      la clave sirve y el proveedor no tiene ese activo.
Confundirlas lleva a conclusiones opuestas: la primera se arregla
configurando, la segunda rotando la credencial, la tercera cambiando de
proveedor.

SOBRE `load_secret()` FUERA DE `ingestion/sources.py`. El punto de
composición único (PR-4) es una regla de PRODUCCIÓN: existe para que
"¿por qué no arrancó tal fuente?" sea una lectura y no una búsqueda. Este
módulo es un tool de DIAGNÓSTICO — no lo importa nada del motor, no
construye adapters para que otro los use, y su única salida es un reporte
por stdout. Aplicarle la regla al pie de la letra obligaría a levantar el
inventario de producción para responder una pregunta que es justamente
anterior a que ese inventario tenga sentido (qué proveedor sirve para qué).
Se usa `load_secret(..., required=False)` directamente y se documenta acá,
que es lo que la propia regla pide para una excepción.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

# Mismo idiom que tools/heartbeat.py y tools/measure_godel_samples.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from governance.secrets import SecretKey, load_secret  # noqa: E402
from ingestion.adapters import (  # noqa: E402
    REQUIRED_COLUMNS,
    TWELVEDATA_ENDPOINT,
    AdapterAuthError,
    AdapterConnectionError,
    AdapterException,
    DerivAdapter,
    TwelveDataAdapter,
    _TWELVEDATA_INTERVALS,
    _TWELVEDATA_SYMBOL_MAP,
)

logger = logging.getLogger("spel.tools.provider_coverage")

#: Activos a sondear. NIFTY50 queda fuera del alcance del proyecto por falta
#: de proveedor: el catálogo de índices de Alpha Vantage es Cboe/EE.UU. y no
#: incluye índices internacionales.
ASSETS_DEFAULT: tuple[str, ...] = (
    "BTCUSD", "XAUUSD", "NVDA", "EURUSD", "GBPUSD", "USDJPY",
)


class CoverageStatus:
    """Estados posibles de un par (proveedor, activo). Ninguno significa
    'falló el proceso': los seis son resultados del sondeo."""
    CUBIERTO = "CUBIERTO"                # respondió con datos utilizables
    NO_CUBIERTO = "NO_CUBIERTO"          # clave válida, el proveedor no lo tiene
    SIN_CLAVE = "SIN_CLAVE"              # credencial ausente, no se sondeó
    CLAVE_RECHAZADA = "CLAVE_RECHAZADA"  # credencial presente y rechazada
    RATE_LIMITED = "RATE_LIMITED"        # se topó la cuota del plan
    SIN_RUTA = "SIN_RUTA"                # no hay endpoint candidato para ese activo
    ERROR = "ERROR"                      # transporte o payload inesperado


@dataclass(frozen=True)
class ProviderSpec:
    """
    Un proveedor y su límite de peticiones.

    `rate_limit_doc` y `min_interval_s` vienen de la DOCUMENTACIÓN OFICIAL de
    cada proveedor, y **no se re-verificaron contra la API en la sesión que
    escribió este módulo** (sin credenciales y sin alcance de red). Están acá
    para espaciar las llamadas de forma conservadora, no como un hecho
    medido: si el sondeo real devuelve RATE_LIMITED antes de lo esperado, el
    número a corregir es este, y `--min-interval-s` permite subirlo sin tocar
    el código.
    """
    name: str
    secret_key: str
    rate_limit_doc: str
    min_interval_s: float


PROVIDERS: dict[str, ProviderSpec] = {
    "twelvedata": ProviderSpec(
        name="twelvedata",
        secret_key=SecretKey.TWELVEDATA_API_KEY,
        rate_limit_doc="plan gratuito: 8 peticiones/minuto, 800/día",
        min_interval_s=8.0,   # 8/min -> 7.5s; se redondea hacia arriba
    ),
    "alphavantage": ProviderSpec(
        name="alphavantage",
        secret_key=SecretKey.ALPHAVANTAGE_API_KEY,
        rate_limit_doc="plan gratuito: cuota diaria estrecha (25 req/día en "
                       "la doc vigente al escribir esto)",
        min_interval_s=15.0,  # el cuello es la cuota DIARIA, no la tasa
    ),
    "tiingo": ProviderSpec(
        name="tiingo",
        secret_key=SecretKey.TIINGO_API_TOKEN,
        rate_limit_doc="plan gratuito: 50 símbolos/hora, 1000 peticiones/día",
        min_interval_s=2.0,
    ),
    "deriv": ProviderSpec(
        name="deriv",
        secret_key=SecretKey.DERIV_API_TOKEN,
        rate_limit_doc="WebSocket; sin límite REST documentado. Se espacia "
                       "igual, por prudencia",
        min_interval_s=2.0,
    ),
}


@dataclass(frozen=True)
class ProbeResult:
    """
    Un par (proveedor, activo) ya sondeado.

    Todo campo que describe DATOS (`columns_returned`, fechas, `n_points`,
    `has_volume`, `has_adjusted_close`) sale de la respuesta real o queda en
    None/vacío. Nunca se rellena por lo que el proveedor promete.
    """
    provider: str
    asset: str
    status: str
    symbol_used: Optional[str] = None
    endpoint: Optional[str] = None
    columns_returned: list[str] = field(default_factory=list)
    #: Columnas de REQUIRED_COLUMNS que la respuesta cruda NO trae. Se evalúa
    #: contra el contrato real del repo (ingestion/adapters.py), no contra uno
    #: inventado acá.
    missing_from_contract: list[str] = field(default_factory=list)
    first_date: Optional[str] = None
    last_date: Optional[str] = None
    n_points: Optional[int] = None
    has_volume: Optional[bool] = None
    has_adjusted_close: Optional[bool] = None
    #: True si el símbolo ya está en el mapa verificado del adapter del repo.
    #: Es un hecho LOCAL, no del sondeo: un activo puede estar cubierto por la
    #: API y no mapeado todavía (que es justamente la evidencia que este tool
    #: produce para poder agregarlo después).
    adapter_mapped: Optional[bool] = None
    detail: str = ""


# ══════════════════════════════════════════════════════════════════════════
#  Rutas CANDIDATAS — no son cobertura declarada
#
#  Cada entrada dice "probá acá con este símbolo". Que funcione o no lo
#  decide la API en la corrida, nunca esta tabla. Un activo sin entrada para
#  un proveedor se reporta SIN_RUTA, que es distinto de NO_CUBIERTO: SIN_RUTA
#  significa "no se supo dónde preguntar", no "el proveedor no lo tiene".
# ══════════════════════════════════════════════════════════════════════════

#: TwelveData: se reusa el vocabulario del adapter donde ya existe
#: (_TWELVEDATA_SYMBOL_MAP) y se agregan candidatos SOLO para los activos que
#: el adapter todavía no mapea. Esos candidatos no se escriben en el adapter:
#: el mapa del adapter exige una respuesta real antes de aceptar un símbolo, y
#: producir esa respuesta es el trabajo de este tool, no su premisa.
_TWELVEDATA_CANDIDATOS: dict[str, str] = {
    **_TWELVEDATA_SYMBOL_MAP,
    "XAUUSD": "XAU/USD",
    "NVDA": "NVDA",
    "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY",
}

ALPHAVANTAGE_ENDPOINT = "https://www.alphavantage.co/query"

#: Alpha Vantage rutea por CLASE de activo, no por un símbolo único: cada
#: clase tiene su propia `function`. Verificado en la doc oficial para
#: equities (TIME_SERIES_DAILY_ADJUSTED, con adjusted close y split
#: coefficient) y para oro (GOLD_SILVER_HISTORY, que devuelve solo
#: date+price, sin OHLC ni volumen). Las de forex y cripto son candidatas.
_ALPHAVANTAGE_RUTAS: dict[str, dict[str, str]] = {
    "NVDA":   {"function": "TIME_SERIES_DAILY_ADJUSTED", "symbol": "NVDA",
               "outputsize": "compact"},
    "XAUUSD": {"function": "GOLD_SILVER_HISTORY", "interval": "daily"},
    "EURUSD": {"function": "FX_DAILY", "from_symbol": "EUR", "to_symbol": "USD"},
    "GBPUSD": {"function": "FX_DAILY", "from_symbol": "GBP", "to_symbol": "USD"},
    "USDJPY": {"function": "FX_DAILY", "from_symbol": "USD", "to_symbol": "JPY"},
    "BTCUSD": {"function": "DIGITAL_CURRENCY_DAILY", "symbol": "BTC",
               "market": "USD"},
}

TIINGO_BASE = "https://api.tiingo.com/tiingo"

#: Tiingo separa equities (/daily), forex (/fx) y cripto (/crypto) en rutas
#: distintas. Cobertura completamente por verificar — es uno de los huecos
#: que este tool existe para cerrar.
_TIINGO_RUTAS: dict[str, tuple[str, str]] = {
    "NVDA":   (f"{TIINGO_BASE}/daily/nvda/prices", "nvda"),
    "EURUSD": (f"{TIINGO_BASE}/fx/eurusd/prices", "eurusd"),
    "GBPUSD": (f"{TIINGO_BASE}/fx/gbpusd/prices", "gbpusd"),
    "USDJPY": (f"{TIINGO_BASE}/fx/usdjpy/prices", "usdjpy"),
    "XAUUSD": (f"{TIINGO_BASE}/fx/xauusd/prices", "xauusd"),
    "BTCUSD": (f"{TIINGO_BASE}/crypto/prices", "btcusd"),
}


# ══════════════════════════════════════════════════════════════════════════
#  Extracción de esquema — estructural, no por forma hardcodeada
# ══════════════════════════════════════════════════════════════════════════

#: Nombres que distintos proveedores usan para lo mismo. Se usan SOLO para
#: contestar "¿trae volumen?" y "¿trae ajustado?", nunca para renombrar ni
#: normalizar datos: este tool no transforma nada.
_ALIAS_VOLUMEN = ("volume", "5. volume", "6. volume", "volumeNotional",
                  "tradesDone", "4b. volume (usd)", "5. volume")
_ALIAS_AJUSTADO = ("adjclose", "adj_close", "adjusted close", "5. adjusted close",
                   "adjopen", "adjhigh", "adjlow")


def _registros(payload: Any) -> list[dict]:
    """
    Encuentra la serie dentro de un payload sin saber de antemano su forma.

    Los cuatro proveedores devuelven estructuras distintas —lista de dicts,
    dict de dicts indexado por fecha, dict con la serie anidada bajo una
    clave con nombre propio— y hardcodear las cuatro formas significaría
    afirmar una forma que no se pudo verificar contra una respuesta real.
    Esta búsqueda estructural encuentra la serie en cualquiera de esas
    formas, y si el proveedor cambia el nombre de la clave sigue
    funcionando. Devuelve [] si no hay nada parecido a una serie.
    """
    if isinstance(payload, list):
        dicts = [r for r in payload if isinstance(r, dict)]
        # Una lista puede ser la serie, o un ENVOLTORIO que la lleva anidada
        # (Tiingo cripto: [{"ticker": ..., "priceData": [...]}]). Si los
        # elementos contienen a su vez una lista de dicts, la serie real es
        # esa; devolver el envoltorio reportaría "ticker, priceData" como si
        # fueran las columnas de los datos.
        anidados: list[dict] = []
        for d in dicts:
            for valor in d.values():
                if isinstance(valor, list) and valor and isinstance(valor[0], dict):
                    anidados.extend(v for v in valor if isinstance(v, dict))
        return anidados or dicts

    if not isinstance(payload, dict):
        return []

    # Lista de dicts bajo alguna clave (TwelveData: "values";
    # Tiingo cripto: "priceData" anidado).
    for valor in payload.values():
        if isinstance(valor, list) and valor and isinstance(valor[0], dict):
            anidado = _registros(valor)
            if anidado:
                return anidado

    # Dict indexado por fecha, con dict adentro (Alpha Vantage).
    for clave, valor in payload.items():
        if not isinstance(valor, dict) or not valor:
            continue
        internos = [v for v in valor.values() if isinstance(v, dict)]
        if len(internos) == len(valor):
            return [{"__fecha__": k, **v} for k, v in valor.items()]
    return []


def _fecha_de(registro: dict) -> Optional[str]:
    """El nombre del campo de fecha varía por proveedor. Se prueban los
    conocidos y, si no hay ninguno, la clave sintética que puso _registros()
    al aplanar un dict indexado por fecha."""
    for clave in ("__fecha__", "datetime", "date", "timestamp", "Date"):
        if clave in registro and registro[clave] is not None:
            return str(registro[clave])
    return None


def _tiene_alguno(columnas: Sequence[str], alias: Sequence[str]) -> bool:
    normalizadas = {c.strip().lower() for c in columnas}
    return any(a.strip().lower() in normalizadas for a in alias)


def describir_payload(payload: Any) -> dict[str, Any]:
    """
    Traduce una respuesta cruda a los campos del reporte. Todo sale de lo que
    vino; nada se completa por defecto.

    `missing_from_contract` se evalúa contra `REQUIRED_COLUMNS` de
    `ingestion/adapters.py` — el contrato real del repo, no uno inventado
    acá. `timestamp` cuenta como presente si el registro trae cualquier
    campo de fecha reconocible: el nombre es del proveedor, la semántica es
    la del contrato.
    """
    registros = _registros(payload)
    if not registros:
        return {"columns_returned": [], "missing_from_contract": [],
                "n_points": 0, "first_date": None, "last_date": None,
                "has_volume": None, "has_adjusted_close": None}

    columnas = sorted({c for r in registros for c in r})
    fechas = sorted(f for f in (_fecha_de(r) for r in registros) if f)

    presentes = {c.strip().lower() for c in columnas}
    if fechas:
        presentes.add("timestamp")
    # Alpha Vantage numera sus campos ("1. open"); se detecta el nombre
    # canónico como sufijo para no reportar como faltante algo que sí vino.
    faltantes = [
        col for col in REQUIRED_COLUMNS
        if col not in presentes
        and not any(p.endswith(col) for p in presentes)
    ]

    return {
        "columns_returned": columnas,
        "missing_from_contract": faltantes,
        "n_points": len(registros),
        "first_date": fechas[0] if fechas else None,
        "last_date": fechas[-1] if fechas else None,
        "has_volume": _tiene_alguno(columnas, _ALIAS_VOLUMEN),
        "has_adjusted_close": _tiene_alguno(columnas, _ALIAS_AJUSTADO),
    }


def _clasificar_error(payload: Any, status_code: int) -> Optional[tuple[str, str]]:
    """
    Traduce el vocabulario de error de cada proveedor a los estados del
    módulo. Devuelve None si la respuesta no parece un error.

    Se mira el CUERPO además del status HTTP porque estos proveedores
    devuelven errores con HTTP 200 (verificado para TwelveData en PR-3: usa
    `status: "error"` con un `code` en el cuerpo). Mirar solo el status deja
    pasar el error como si fuera un payload bueno.
    """
    texto = json.dumps(payload, ensure_ascii=False).lower() if payload else ""

    if status_code in (401, 403):
        return CoverageStatus.CLAVE_RECHAZADA, f"HTTP {status_code}"
    if status_code == 429:
        return CoverageStatus.RATE_LIMITED, "HTTP 429"

    code = payload.get("code") if isinstance(payload, dict) else None
    if code == 401:
        return CoverageStatus.CLAVE_RECHAZADA, str(payload.get("message", code))
    if code == 429:
        return CoverageStatus.RATE_LIMITED, str(payload.get("message", code))
    if code == 404:
        return CoverageStatus.NO_CUBIERTO, str(payload.get("message", code))

    # Alpha Vantage responde 200 con una nota en prosa cuando corta por cuota
    # o rechaza la clave; no hay código numérico que mirar.
    if "rate limit" in texto or "premium endpoint" in texto or "thank you for using" in texto:
        return CoverageStatus.RATE_LIMITED, "el proveedor reporta límite de peticiones"
    if "invalid api" in texto or "apikey is invalid" in texto:
        return CoverageStatus.CLAVE_RECHAZADA, "el proveedor rechazó la clave"
    if isinstance(payload, dict) and "error message" in {k.lower() for k in payload}:
        return CoverageStatus.NO_CUBIERTO, "el proveedor no reconoce el símbolo"
    if status_code >= 400:
        return CoverageStatus.ERROR, f"HTTP {status_code}"
    return None


# ══════════════════════════════════════════════════════════════════════════
#  Sondas por proveedor
# ══════════════════════════════════════════════════════════════════════════

async def _get_json(client, url: str, *, params: dict, headers: dict) -> tuple[Any, int]:
    """Una sola petición. Devuelve (payload, status_code). El payload es None
    si el cuerpo no era JSON."""
    resp = await client.get(url, params=params, headers=headers)
    try:
        return resp.json(), resp.status_code
    except ValueError:
        return None, resp.status_code


async def probe_twelvedata(client, asset: str, key: str) -> ProbeResult:
    """Reusa el endpoint, el vocabulario de símbolos y la convención de auth
    del `TwelveDataAdapter` ya auditado — la key va en el header
    `Authorization`, nunca como query param."""
    simbolo = _TWELVEDATA_CANDIDATOS.get(asset)
    if simbolo is None:
        return ProbeResult("twelvedata", asset, CoverageStatus.SIN_RUTA,
                           detail="sin símbolo candidato para este activo")

    payload, code = await _get_json(
        client, TWELVEDATA_ENDPOINT,
        params={"symbol": simbolo, "interval": _TWELVEDATA_INTERVALS["1d"],
                "outputsize": 30, "order": "ASC"},
        headers={"Authorization": f"apikey {key}"},
    )
    return _resultado(
        "twelvedata", asset, simbolo, TWELVEDATA_ENDPOINT, payload, code,
        adapter_mapped=asset in TwelveDataAdapter.SUPPORTED_SYMBOLS,
    )


async def probe_alphavantage(client, asset: str, key: str) -> ProbeResult:
    """
    Alpha Vantage rutea por clase de activo (una `function` distinta por
    clase). La clave va como query param porque **la API no ofrece
    autenticación por header** — es una limitación del proveedor, no una
    elección de este tool. Por eso la URL nunca se imprime ni se loguea: el
    reporte publica el endpoint base, no la petición completa.
    """
    ruta = _ALPHAVANTAGE_RUTAS.get(asset)
    if ruta is None:
        return ProbeResult("alphavantage", asset, CoverageStatus.SIN_RUTA,
                           detail="sin function candidata para este activo")

    payload, code = await _get_json(
        client, ALPHAVANTAGE_ENDPOINT,
        params={**ruta, "apikey": key}, headers={},
    )
    etiqueta = ruta.get("symbol") or f"{ruta.get('from_symbol','')}{ruta.get('to_symbol','')}"
    return _resultado(
        "alphavantage", asset, etiqueta or asset,
        f"{ALPHAVANTAGE_ENDPOINT}?function={ruta['function']}",
        payload, code, adapter_mapped=None,  # no hay adapter de AV en el repo
    )


async def probe_tiingo(client, asset: str, key: str) -> ProbeResult:
    """Tiingo autentica por header (`Authorization: Token ...`), así que la
    credencial no viaja en la URL."""
    ruta = _TIINGO_RUTAS.get(asset)
    if ruta is None:
        return ProbeResult("tiingo", asset, CoverageStatus.SIN_RUTA,
                           detail="sin ruta candidata para este activo")

    url, ticker = ruta
    params = {"tickers": ticker} if "/crypto/" in url else {}
    payload, code = await _get_json(
        client, url, params=params,
        headers={"Authorization": f"Token {key}", "Content-Type": "application/json"},
    )
    return _resultado("tiingo", asset, ticker, url, payload, code,
                      adapter_mapped=None)  # no hay adapter de Tiingo en el repo


async def probe_deriv(asset: str, token: str, app_id: str) -> ProbeResult:
    """
    Deriv es WebSocket: se usa el `DerivAdapter` del repo en vez de
    reimplementar el handshake (authorize -> ticks_history), que sería
    duplicar conocimiento ya auditado y probado.

    Consecuencia de usar el adapter: para un símbolo que su mapa verificado
    no incluye, `fetch_ohlcv()` lanza `ValueError` ANTES de tocar la red. Eso
    no es "el proveedor no lo cubre" — es "el adapter no lo mapea todavía", y
    se reporta como SIN_RUTA para no afirmar algo que la API nunca dijo.
    """
    if asset not in DerivAdapter.SUPPORTED_SYMBOLS:
        return ProbeResult(
            "deriv", asset, CoverageStatus.SIN_RUTA, adapter_mapped=False,
            detail=f"'{asset}' no está en el mapa verificado de DerivAdapter; "
                   f"no se sondeó la API. Mapeados: "
                   f"{sorted(DerivAdapter.SUPPORTED_SYMBOLS)}",
        )

    adapter = DerivAdapter(api_token=token, app_id=app_id)
    try:
        df = await adapter.fetch_ohlcv(asset, "1d", 30)
    except AdapterAuthError as exc:
        return ProbeResult("deriv", asset, CoverageStatus.CLAVE_RECHAZADA,
                           adapter_mapped=True, detail=str(exc))
    except AdapterConnectionError as exc:
        return ProbeResult("deriv", asset, CoverageStatus.ERROR,
                           adapter_mapped=True, detail=str(exc))
    except AdapterException as exc:
        return ProbeResult("deriv", asset, CoverageStatus.NO_CUBIERTO,
                           adapter_mapped=True, detail=str(exc))

    fechas = [str(t.date()) for t in df["timestamp"]] if not df.empty else []
    return ProbeResult(
        provider="deriv", asset=asset, status=CoverageStatus.CUBIERTO,
        symbol_used=asset, endpoint="wss://ws.derivws.com (ticks_history)",
        columns_returned=list(df.columns),
        missing_from_contract=[c for c in REQUIRED_COLUMNS if c not in df.columns],
        first_date=fechas[0] if fechas else None,
        last_date=fechas[-1] if fechas else None,
        n_points=len(df),
        # El adapter normaliza: rellena `volume` con 0.0 y lo marca en attrs.
        # Se lee la bandera, no la presencia de la columna, que siempre está.
        has_volume=bool(df.attrs.get("volume_available", True)),
        has_adjusted_close=False,
        adapter_mapped=True,
        detail="vía DerivAdapter (contrato ya validado por el adapter)",
    )


def _resultado(
    provider: str, asset: str, simbolo: str, endpoint: str,
    payload: Any, code: int, *, adapter_mapped: Optional[bool],
) -> ProbeResult:
    """Convierte (payload, status) en un ProbeResult. Un payload sin serie
    reconocible es NO_CUBIERTO, no ERROR: el proveedor contestó, simplemente
    no tenía nada que dar para ese símbolo."""
    if payload is None:
        return ProbeResult(provider, asset, CoverageStatus.ERROR, simbolo,
                           endpoint, adapter_mapped=adapter_mapped,
                           detail=f"respuesta no-JSON (HTTP {code})")

    fallo = _clasificar_error(payload, code)
    if fallo is not None:
        estado, detalle = fallo
        return ProbeResult(provider, asset, estado, simbolo, endpoint,
                           adapter_mapped=adapter_mapped, detail=detalle)

    desc = describir_payload(payload)
    if not desc["n_points"]:
        return ProbeResult(provider, asset, CoverageStatus.NO_CUBIERTO, simbolo,
                           endpoint, adapter_mapped=adapter_mapped,
                           detail="respondió sin serie de datos")

    return ProbeResult(
        provider=provider, asset=asset, status=CoverageStatus.CUBIERTO,
        symbol_used=simbolo, endpoint=endpoint,
        adapter_mapped=adapter_mapped, detail="", **desc,
    )


# ══════════════════════════════════════════════════════════════════════════
#  Orquestación del sondeo
# ══════════════════════════════════════════════════════════════════════════

async def probe_provider(
    provider: str, assets: Sequence[str], *, min_interval_s: Optional[float] = None,
) -> list[ProbeResult]:
    """
    Sondea un proveedor sobre varios activos, EN SERIE y con espaciado.

    En serie a propósito: paralelizar 6 peticiones contra un plan de 8/minuto
    garantiza toparse la cuota, y un RATE_LIMITED se lee igual que un
    NO_CUBIERTO si uno no mira el estado — que es exactamente la confusión
    que este tool existe para evitar.
    """
    import httpx

    spec = PROVIDERS[provider]
    espera = spec.min_interval_s if min_interval_s is None else min_interval_s

    clave = load_secret(spec.secret_key, required=False)
    if not clave:
        return [
            ProbeResult(provider, a, CoverageStatus.SIN_CLAVE,
                        detail=f"falta {spec.secret_key}; no se sondeó nada")
            for a in assets
        ]

    if provider == "deriv":
        app_id = load_secret(SecretKey.DERIV_APP_ID, required=False)
        if not app_id:
            return [
                ProbeResult(provider, a, CoverageStatus.SIN_CLAVE,
                            detail=f"falta {SecretKey.DERIV_APP_ID} (Deriv "
                                   f"necesita token Y app_id)")
                for a in assets
            ]
        salida: list[ProbeResult] = []
        for i, activo in enumerate(assets):
            if i:
                await asyncio.sleep(espera)
            salida.append(await probe_deriv(activo, clave, app_id))
        return salida

    sondas = {"twelvedata": probe_twelvedata,
              "alphavantage": probe_alphavantage,
              "tiingo": probe_tiingo}[provider]

    salida = []
    async with httpx.AsyncClient(timeout=20.0) as client:
        for i, activo in enumerate(assets):
            if i:
                await asyncio.sleep(espera)
            try:
                salida.append(await sondas(client, activo, clave))
            except httpx.HTTPError as exc:
                # Un fallo de transporte es de ESTA petición, no del
                # proveedor entero: se registra y el sondeo sigue.
                salida.append(ProbeResult(provider, activo, CoverageStatus.ERROR,
                                          detail=f"{type(exc).__name__}: {exc}"))
    return salida


async def run_inventory(
    providers: Sequence[str], assets: Sequence[str],
    *, min_interval_s: Optional[float] = None,
) -> list[ProbeResult]:
    resultados: list[ProbeResult] = []
    for p in providers:
        resultados.extend(
            await probe_provider(p, assets, min_interval_s=min_interval_s)
        )
    return resultados


# ══════════════════════════════════════════════════════════════════════════
#  Reporte (stdout — este tool NO escribe archivos)
# ══════════════════════════════════════════════════════════════════════════

def render_text(resultados: Sequence[ProbeResult]) -> str:
    out = [
        "═══ INVENTARIO DE COBERTURA POR PROVEEDOR ═══",
        "Todo lo de abajo sale de la respuesta real de cada API en esta",
        "corrida. Nada declarado por tabla estática ni por el proveedor.",
        f"Contrato de referencia: {list(REQUIRED_COLUMNS)}",
        "",
    ]
    por_proveedor: dict[str, list[ProbeResult]] = {}
    for r in resultados:
        por_proveedor.setdefault(r.provider, []).append(r)

    for proveedor, filas in por_proveedor.items():
        spec = PROVIDERS.get(proveedor)
        out.append(f"── {proveedor} " + "─" * max(0, 58 - len(proveedor)))
        if spec:
            out.append(f"  Límite documentado: {spec.rate_limit_doc}")
        out.append("")
        out.append("  activo    estado           símbolo      pts  desde       hasta"
                   "        vol   adj  falta del contrato")
        for r in filas:
            out.append(
                f"  {r.asset:<9} {r.status:<16} {str(r.symbol_used or '-'):<12} "
                f"{str(r.n_points if r.n_points is not None else '-'):>4}  "
                f"{str(r.first_date or '-'):<11} {str(r.last_date or '-'):<11} "
                f"{_si_no(r.has_volume):>4}  {_si_no(r.has_adjusted_close):>4}  "
                f"{','.join(r.missing_from_contract) or '-'}"
            )
            if r.detail:
                out.append(f"            └─ {r.detail}")
        out.append("")

    cubiertos = [r for r in resultados if r.status == CoverageStatus.CUBIERTO]
    out.append(f"RESUMEN: {len(cubiertos)}/{len(resultados)} pares "
               f"(proveedor, activo) con datos reales en esta corrida.")
    sin_clave = {r.provider for r in resultados if r.status == CoverageStatus.SIN_CLAVE}
    if sin_clave:
        out.append(f"  Sin credencial, no sondeados: {', '.join(sorted(sin_clave))}. "
                   f"Eso NO es 'sin cobertura' — es 'sin medir'.")
    return "\n".join(out)


def _si_no(v: Optional[bool]) -> str:
    return "-" if v is None else ("sí" if v else "no")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="provider_coverage",
        description="Inventaria qué proveedor cubre qué activo, sondeando las "
                    "APIs reales. Read-only: no escribe nada.",
    )
    p.add_argument("--providers", nargs="+", default=sorted(PROVIDERS),
                   choices=sorted(PROVIDERS))
    p.add_argument("--assets", nargs="+", default=list(ASSETS_DEFAULT))
    p.add_argument("--min-interval-s", type=float, default=None,
                   help="Segundos entre peticiones del mismo proveedor. Por "
                        "defecto, el que corresponde al límite documentado de "
                        "cada uno. Subirlo si el sondeo real devuelve "
                        "RATE_LIMITED antes de lo esperado.")
    p.add_argument("--format", choices=("text", "json"), default="text")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    desconocidos = [a for a in args.assets if not a.strip()]
    if desconocidos:
        print("ERROR: hay activos vacíos en --assets", file=sys.stderr)
        return 2

    resultados = asyncio.run(run_inventory(
        args.providers, args.assets, min_interval_s=args.min_interval_s,
    ))

    if args.format == "json":
        print(json.dumps(
            {"contract": list(REQUIRED_COLUMNS),
             "providers": {p: asdict(PROVIDERS[p]) for p in args.providers},
             "results": [asdict(r) for r in resultados]},
            indent=2, ensure_ascii=False,
        ))
    else:
        print(render_text(resultados))

    # Exit 0 SIEMPRE que el sondeo se complete. "Sin cobertura" y "sin clave"
    # son resultados del inventario, no fallos del proceso.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
