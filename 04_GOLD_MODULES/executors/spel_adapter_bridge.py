# ── spel_adapter_bridge.py ───────────────────────────────────────────────────
# RESPONSABILIDAD: Ser el único punto de contacto entre el Dashboard/Pipeline
# y el mundo exterior. Implementa:
#
#   1. VÍNCULO SAGRADO: Toda consulta GDELT pasa por BigQueryGDELTAdapter.
#      Toda consulta OHLCV pasa por la cadena AV → Tiingo → ParquetCache.
#      Cualquier acceso directo (yfinance, capa_b_bigquery) es una ANOMALÍA.
#
#   2. MODO DEGRADADO ELEGANTE: Si un adapter falla, retorna un
#      SensorPlaceholder que el Dashboard renderiza como widget de diagnóstico
#      — nunca una gráfica vacía sin explicación.
#
#   3. z_params INYECTADOS: Los parámetros de normalización se leen de
#      SPEL_META_RUNTIME (RAM) y se inyectan en la llamada de inferencia.
#      El pipeline nunca vuelve a tocar el disco para leer z_params.
#
# Regla 24 — todo acceso externo pasa por un BaseSensorAdapter.
# Abraham Fuenmayor · Sprint 6 · 02 Mar 2026
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

# Importar el singleton de z_params (cargado en Celda 1)
try:
    from spel_meta_guardian import SPEL_META_RUNTIME, get_z_params
except ImportError:
    SPEL_META_RUNTIME = None
    def get_z_params(activo: str) -> dict:  # type: ignore
        raise RuntimeError("spel_meta_guardian no disponible — importar primero.")

# Importar los adapters canónicos
try:
    from base_adapter import (
        SPELAdapterChain,
        BigQueryGDELTAdapter,
        AlphaVantageAdapter,
        TiingoAdapter,
        ParquetCacheAdapter,
        AdapterResult,
    )
    _ADAPTERS_DISPONIBLES = True
except ImportError:
    _ADAPTERS_DISPONIBLES = False


# ═══════════════════════════════════════════════════════════════════════════════
# 1. PLACEHOLDER DE SEGURIDAD — Modo Degradado Elegante
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SensorPlaceholder:
    """
    Objeto que el Dashboard inyecta cuando un Adapter falla.
    Contiene toda la información necesaria para mostrar un widget de diagnóstico
    en lugar de una gráfica vacía o un crash silencioso.
    """
    sensor_nombre: str          # Ej. "BigQueryGDELT", "AlphaVantage"
    activo: str                 # Ej. "NVDA", "BTC"
    razon_fallo: str            # Mensaje de error legible por el operador
    timestamp_fallo: str = field(
        default_factory=lambda: datetime.utcnow().isoformat(timespec="seconds") + "Z"
    )
    codigo_diagnostico: str = "SENSOR_OFFLINE"
    es_degradado: bool = True

    # Datos ficticios seguros para que el Score Engine no crashe
    data_vacia: dict = field(default_factory=dict)

    def to_streamlit_widget(self) -> str:
        """
        Retorna el texto de aviso para st.warning() en el Dashboard.
        El dashboard llamará a esto cuando reciba un SensorPlaceholder.
        """
        return (
            f"⚠️ **SENSOR CAÍDO**: `{self.sensor_nombre}` — Activo: `{self.activo}`\n\n"
            f"**Razón**: {self.razon_fallo}\n\n"
            f"**Desde**: {self.timestamp_fallo} UTC\n\n"
            f"**Código**: `{self.codigo_diagnostico}`\n\n"
            "El sistema está operando en **Modo Degradado**. "
            "Los demás sensores continúan activos. "
            "Revisar `shared_volumes/logs/adapters_audit.json` para diagnóstico completo."
        )

    def to_score_engine_dict(self) -> dict:
        """
        Retorna un dict compatible con el Score Engine que no causa KeyError.
        Todos los valores son NaN/None seguros para que el Score penalice correctamente.
        """
        return {
            "status": "OFFLINE",
            "sensor": self.sensor_nombre,
            "activo": self.activo,
            "goldstein_geo": None,
            "n_events_ohlcv": None,
            "vitality_tesla": None,
            "mass_panic_index": None,
            "fear_momentum": None,
            "degradado": True,
            "razon": self.razon_fallo,
        }


def _crear_placeholder(sensor: str, activo: str, exc: Exception) -> SensorPlaceholder:
    """Factory de placeholders — centraliza la creación."""
    return SensorPlaceholder(
        sensor_nombre=sensor,
        activo=activo,
        razon_fallo=f"{type(exc).__name__}: {str(exc)[:200]}",
        codigo_diagnostico="ADAPTER_EXCEPTION",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. VÍNCULO SAGRADO — La única puerta de entrada a datos externos
# ═══════════════════════════════════════════════════════════════════════════════

class SPELDataBridge:
    """
    Intermediario único entre el Dashboard/Pipeline y los adapters.
    
    REGLA: El Dashboard/Pipeline NUNCA importa directamente yfinance,
    capa_b_bigquery, o BigQuery. Toda consulta pasa por aquí.

    Uso en Dashboard (main_ui.py):
        bridge = SPELDataBridge(bq_client=bq_client, av_key=AV_KEY)
        resultado_gdelt = bridge.fetch_gdelt("NVDA", lookback_dias=63)
        resultado_ohlcv = bridge.fetch_ohlcv("BTC", lookback_dias=21)

        if isinstance(resultado_gdelt, SensorPlaceholder):
            st.warning(resultado_gdelt.to_streamlit_widget())
        else:
            # renderizar gráfica normal
    """

    def __init__(
        self,
        bq_client: Any = None,
        av_key: str = "",
        tiingo_key: str = "",
        parquet_base: str = "",
    ):
        self._bq_client = bq_client
        self._av_key = av_key
        self._tiingo_key = tiingo_key
        self._parquet_base = parquet_base
        self._z_params_cache: dict[str, dict] = {}  # carga lazy desde RAM
        self._inicializado = False

    def inicializar(self) -> None:
        """
        Pre-carga z_params de todos los activos conocidos desde SPEL_META_RUNTIME.
        Se llama UNA VEZ al arranque del Dashboard — nunca más se lee del disco.
        """
        activos = ["NVDA", "BTC", "XAU", "NIFTY50"]
        for activo in activos:
            try:
                self._z_params_cache[activo] = get_z_params(activo)
            except (KeyError, RuntimeError):
                self._z_params_cache[activo] = {}
        self._inicializado = True
        print(f"✅  SPELDataBridge inicializado — z_params en RAM: {list(self._z_params_cache.keys())}")

    def get_z_params_cached(self, activo: str) -> dict:
        """z_params desde RAM. Nunca toca el disco. Error claro si no inicializado."""
        if not self._inicializado:
            raise RuntimeError("SPELDataBridge.inicializar() no fue llamado.")
        return self._z_params_cache.get(activo, {})

    # ── Vínculo GDELT ─────────────────────────────────────────────────────────
    def fetch_gdelt(
        self,
        activo: str,
        lookback_dias: int = 63,
        gcp_project: str = "spel-dashboard",
    ) -> "AdapterResult | SensorPlaceholder":
        """
        ÚNICA ruta permitida para datos GDELT.
        Si BigQueryGDELTAdapter falla → retorna SensorPlaceholder, no excepción.
        
        ANOMALÍA si el Dashboard llama directamente a SPELBigQueryExtractor o
        a bigquery.Client() sin pasar por aquí.
        """
        if not _ADAPTERS_DISPONIBLES:
            return SensorPlaceholder(
                sensor_nombre="BigQueryGDELT",
                activo=activo,
                razon_fallo="base_adapter.py no disponible en el entorno.",
                codigo_diagnostico="MODULE_NOT_FOUND",
            )
        try:
            adapter = BigQueryGDELTAdapter(
                bq_client=self._bq_client,
                project_id=gcp_project,
            )
            resultado: AdapterResult = adapter.fetch(activo, lookback_dias=lookback_dias)
            if resultado.is_degraded:
                return SensorPlaceholder(
                    sensor_nombre="BigQueryGDELT",
                    activo=activo,
                    razon_fallo=resultado.error_message or "Adapter retornó is_degraded=True",
                    codigo_diagnostico="ADAPTER_DEGRADED",
                )
            return resultado
        except Exception as exc:
            return _crear_placeholder("BigQueryGDELT", activo, exc)

    # ── Vínculo OHLCV ─────────────────────────────────────────────────────────
    def fetch_ohlcv(
        self,
        activo: str,
        lookback_dias: int = 63,
    ) -> "AdapterResult | SensorPlaceholder":
        """
        ÚNICA ruta permitida para datos OHLCV.
        Sigue la jerarquía canónica: AlphaVantage → Tiingo → ParquetCache.
        
        ANOMALÍA si cualquier módulo llama directamente a yfinance.
        yfinance está BLOQUEADO en Colab — toda llamada produce df vacío silencioso.
        """
        if not _ADAPTERS_DISPONIBLES:
            return SensorPlaceholder(
                sensor_nombre="OHLCV_Chain",
                activo=activo,
                razon_fallo="base_adapter.py no disponible en el entorno.",
                codigo_diagnostico="MODULE_NOT_FOUND",
            )
        try:
            chain = SPELAdapterChain.build_ohlcv_chain(
                av_key=self._av_key,
                tiingo_key=self._tiingo_key,
                parquet_base=self._parquet_base,
            )
            resultado: AdapterResult = chain.fetch(activo, lookback_dias=lookback_dias)
            if resultado.is_degraded:
                # ParquetCache fue el último recurso y también falló
                return SensorPlaceholder(
                    sensor_nombre="OHLCV_Chain",
                    activo=activo,
                    razon_fallo=resultado.error_message or "Toda la cadena OHLCV falló",
                    codigo_diagnostico="FULL_CHAIN_DEGRADED",
                )
            return resultado
        except Exception as exc:
            return _crear_placeholder("OHLCV_Chain", activo, exc)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. PARCHE PARA main_ui.py (Dashboard) — reemplaza capa_b_bigquery
# ═══════════════════════════════════════════════════════════════════════════════

def adaptar_gdelt_para_score_engine(
    resultado: "AdapterResult | SensorPlaceholder",
) -> dict:
    """
    Convierte un AdapterResult de BigQueryGDELTAdapter al dict que consume
    el Score de Oro Engine (que antes recibía el dict de SPELBigQueryExtractor).

    Esta función es el puente de migración Paso 4 del Plan de Consolidación:
    reemplaza la llamada a capa_b_bigquery.SPELBigQueryExtractor.extraer()
    en el dashboard por bridge.fetch_gdelt() → adaptar_gdelt_para_score_engine().

    Si el resultado es un SensorPlaceholder (sensor caído), retorna el dict
    seguro que el Score Engine penaliza correctamente sin crashear.
    """
    if isinstance(resultado, SensorPlaceholder):
        return resultado.to_score_engine_dict()

    # Es un AdapterResult válido con .data como DataFrame de Polars
    df = resultado.data
    if df is None or len(df) == 0:
        return SensorPlaceholder(
            sensor_nombre="BigQueryGDELT",
            activo="desconocido",
            razon_fallo="AdapterResult retornó DataFrame vacío",
        ).to_score_engine_dict()

    # Mapear columnas del DataFrame Polars al dict que espera el Score Engine
    try:
        ultima_fila = df.row(-1, named=True)
        return {
            "status": "LIVE",
            "goldstein_geo": ultima_fila.get("goldstein_geo", None),
            "n_events_ohlcv": ultima_fila.get("n_events_ohlcv", None),
            "vitality_tesla": ultima_fila.get("vitality_tesla", None),
            "mass_panic_index": ultima_fila.get("mass_panic_index", None),
            "fear_momentum": ultima_fila.get("fear_momentum", None),
            "degradado": False,
        }
    except Exception as exc:
        return SensorPlaceholder(
            sensor_nombre="BigQueryGDELT",
            activo="desconocido",
            razon_fallo=f"Error al parsear DataFrame: {exc}",
        ).to_score_engine_dict()


# ═══════════════════════════════════════════════════════════════════════════════
# 4. DETECTOR DE ANOMALÍAS — Guardia de imports
# ═══════════════════════════════════════════════════════════════════════════════

def verificar_no_hay_anomalias_en_runtime() -> None:
    """
    Detecta si módulos prohibidos están importados en el runtime actual.
    Llamar al inicio del Dashboard para confirmar el Vínculo Sagrado está activo.
    """
    import sys
    anomalias = []

    modulos_prohibidos = {
        "yfinance": "yfinance está BLOQUEADO en Colab — usar AlphaVantageAdapter",
        "modules.capa_b_bigquery": "capa_b_bigquery DEPRECADO — usar base_adapter.BigQueryGDELTAdapter",
    }

    for modulo, mensaje in modulos_prohibidos.items():
        if modulo in sys.modules:
            anomalias.append(f"  🔴 ANOMALÍA: `{modulo}` importado — {mensaje}")

    print("\n  🛡️   Verificación de Vínculo Sagrado:")
    if anomalias:
        for a in anomalias:
            print(a)
        print("  ⛔  Resolver anomalías antes de continuar (Sprint 6 Pasos 3 y 4).")
    else:
        print("  ✅  Sin anomalías — Vínculo Sagrado íntegro.")
