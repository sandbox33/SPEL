# ── SPEL · MÓDULOS CANÓNICOS: CAPA A · CAPA B · SCORE ENGINE · CORRELACIÓN ──
# Módulo: spel_modules.py
# Proyecto: Socio-Political Entropy Loss (SPEL) · Dashboard v7
# Autor: Abraham Fuenmayor · v1.0 · 28 Feb 2026
#
# INSTRUCCIÓN DE MANTENIMIENTO:
#   Este archivo consolida el código de las funciones canónicas definidas en
#   SPEC_BLACKBOX_DASHBOARD_V7.md (Secciones 1, 2, 3, 5 y 6).
#   Cada función viene de la spec y está marcada con su sección de origen.
#   NO modificar las firmas públicas — el orquestador del dashboard las consume.
#
#   Regla 19: Volume Profile es inválido si >50% de las últimas 10 velas = 0.
#   Regla 17: APIs rotas deben documentarse como "no bloqueante" antes de
#             añadir nuevos tabs al dashboard.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
import polars as pl
import requests

from datetime import datetime, timedelta
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════════
# CAPA A — MICROESTRUCTURA DE MERCADO (Spec Sección 1 · "The Map")
# ═══════════════════════════════════════════════════════════════════════════════

def calcular_volume_profile(df: pl.DataFrame, ventana: int = 100, bins: int = 50) -> dict:
    """
    Calcula VAH, VAL y POC sobre los últimos N registros del parquet.
    Requiere columnas: high, low, close, volume (parquet v4 canónico).

    Regla 19: AUDITORIA DE VOLUMEN OBLIGATORIA.
    Si >50% de las últimas 10 velas tienen volumen=0, el resultado lleva
    'volumen_invalido': True y el Score Engine debe bloquear la Capa A.
    """
    # ── Auditoría de volumen (Regla 19) ──────────────────────────────────────
    vol_reciente = df["volume"].tail(10).to_numpy().astype(float)
    pct_cero     = (vol_reciente == 0).mean()
    if pct_cero > 0.5:
        return {
            "POC": 0.0, "VAH": 0.0, "VAL": 0.0,
            "volumen_total": 0.0, "value_area_pct": 0.0,
            "volumen_invalido": True,
            "razon": f"REGLA 19: {pct_cero*100:.0f}% de velas recientes con volumen=0. VP inválido.",
        }

    datos        = df.tail(ventana).to_pandas()
    precio_min   = datos["low"].min()
    precio_max   = datos["high"].max()
    niveles      = np.linspace(precio_min, precio_max, bins)
    vol_por_nivel = np.zeros(bins - 1)

    for _, fila in datos.iterrows():
        mascara = (niveles[:-1] >= fila["low"]) & (niveles[1:] <= fila["high"])
        if mascara.sum() > 0:
            vol_por_nivel[mascara] += fila["volume"] / mascara.sum()

    poc_idx       = np.argmax(vol_por_nivel)
    poc           = (niveles[poc_idx] + niveles[poc_idx + 1]) / 2
    total_vol     = vol_por_nivel.sum()
    objetivo_70   = total_vol * 0.70
    izq = der     = poc_idx
    vol_acumulado = vol_por_nivel[poc_idx]

    while vol_acumulado < objetivo_70 and (izq > 0 or der < len(vol_por_nivel) - 1):
        expandir_der = der < len(vol_por_nivel) - 1 and (
            izq == 0 or vol_por_nivel[der + 1] >= vol_por_nivel[izq - 1]
        )
        if expandir_der:
            der += 1
            vol_acumulado += vol_por_nivel[der]
        else:
            izq -= 1
            vol_acumulado += vol_por_nivel[izq]

    return {
        "POC":            round(poc, 4),
        "VAH":            round(niveles[der + 1], 4),
        "VAL":            round(niveles[izq], 4),
        "volumen_total":  round(total_vol, 2),
        "value_area_pct": round(vol_acumulado / total_vol * 100, 1),
        "volumen_invalido": False,
        "niveles_raw":    niveles.tolist(),     # para renderizado del histograma
        "vol_raw":        vol_por_nivel.tolist(),
    }


def detectar_fakeout(precio_actual: float, vp: dict, df: pl.DataFrame) -> bool:
    """
    Regla Anti-Trampa (Spec Sección 1 · Capa A).
    Fakeout si precio supera VAH/VAL pero el volumen de la vela de ruptura
    es inferior al volumen promedio de las últimas 10 velas.
    """
    vol_reciente = df["volume"].tail(10).to_numpy().astype(float)
    vol_promedio = vol_reciente.mean()
    vol_ultima   = float(df["volume"][-1])
    ruptura      = precio_actual > vp["VAH"] or precio_actual < vp["VAL"]
    return ruptura and (vol_ultima < vol_promedio)


# ── Visualización: Velas de Intención (Spec Sección 4 · UI/UX) ───────────────

def crear_grafico_velas_intencion(
    df: pl.DataFrame,
    vp: dict,
    score_resultado: dict,
    activo: str = "",
) -> go.Figure:
    """
    Gráfico de velas japonesas con colores de intención SPEL.
    Superpone entropy_shannon como línea secundaria en eje derecho.
    Marca activaciones Gödel con triángulos rojos.

    Sprint 2 — Tab OHLCV + Entropía (Regla 18 OBLIGATORIO).
    """
    datos     = df.tail(100).to_pandas()
    score     = score_resultado["score"]
    direccion = score_resultado.get("direccion")
    fakeout   = score_resultado.get("fakeout", False)
    godel     = score_resultado.get("componentes", {}).get("godel", 0) > 0

    # ── Paleta de color por estado (Spec Sección 4 · tabla de colores) ───────
    if fakeout:
        c_alza = c_baja = "rgba(255,255,0,0.8)"
    elif score >= 90 and direccion == "CALL":
        c_alza, c_baja = "#00FF88", "#007744"
    elif score >= 90 and direccion == "PUT":
        c_alza, c_baja = "#FF00CC", "#880066"
    elif godel and direccion == "CALL":
        c_alza, c_baja = "#00CCFF", "#006688"
    elif godel and direccion == "PUT":
        c_alza, c_baja = "#FF6600", "#882200"
    else:
        c_alza, c_baja = "#888888", "#444444"

    fig = go.Figure()

    # Velas japonesas
    fig.add_trace(go.Candlestick(
        x=datos.index,
        open=datos["open"], high=datos["high"],
        low=datos["low"],   close=datos["close"],
        increasing_line_color=c_alza,
        decreasing_line_color=c_baja,
        name="OHLCV",
    ))

    # Entropía Shannon (eje secundario — Regla 18 OBLIGATORIO)
    if "entropy_shannon" in datos.columns:
        fig.add_trace(go.Scatter(
            x=datos.index, y=datos["entropy_shannon"],
            mode="lines", name="Entropía Shannon", yaxis="y2",
            line=dict(color="#FFFFFF", width=1, dash="dot"),
            opacity=0.85,
        ))
        p90 = score_resultado.get("p90", None)
        if p90:
            fig.add_hline(
                y=p90, line_dash="dash", line_color="#FF4444",
                annotation_text=f"P90 Gödel = {p90:.4f}", yref="y2",
                annotation_position="right",
            )

        # Triángulos en activaciones Gödel
        mask_godel = datos["entropy_shannon"] >= (p90 or 1.19)
        if mask_godel.any():
            fig.add_trace(go.Scatter(
                x=datos.index[mask_godel],
                y=datos["low"][mask_godel] * 0.995,
                mode="markers",
                marker=dict(symbol="triangle-up", size=8, color="#FF4444"),
                name="Gödel Activo", yaxis="y",
            ))

    # Niveles de Subasta (Spec Sección 4)
    if not vp.get("volumen_invalido", False):
        fig.add_hline(y=vp["VAH"], line_color="#00FF88", line_dash="solid",
                      annotation_text=f"VAH {vp['VAH']:.4f}", annotation_position="right")
        fig.add_hline(y=vp["POC"], line_color="#FFFF00", line_dash="dot",
                      annotation_text=f"POC {vp['POC']:.4f}", annotation_position="right")
        fig.add_hline(y=vp["VAL"], line_color="#FF00CC", line_dash="solid",
                      annotation_text=f"VAL {vp['VAL']:.4f}", annotation_position="right")

    titulo = (
        f"🕯️ Velas de Intención SPEL · {activo} · "
        f"Score: {score}/100 · {score_resultado.get('veredicto', '')}"
    )
    fig.update_layout(
        template="plotly_dark",
        title=titulo,
        xaxis_rangeslider_visible=False,
        yaxis2=dict(overlaying="y", side="right", title="Entropía Shannon", showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(r=120),
    )
    return fig


def crear_grafico_volume_profile(vp: dict, df: pl.DataFrame) -> go.Figure:
    """
    Histograma horizontal de Volume Profile con marcas VAH/POC/VAL.
    Sprint 3 — Tab Volume Profile.
    """
    fig = go.Figure()

    if vp.get("volumen_invalido", False):
        fig.add_annotation(
            text="⚠️ VOLUME PROFILE INVÁLIDO (Regla 19)<br>Fuente de datos no reporta volumen real.",
            x=0.5, y=0.5, showarrow=False, font=dict(size=16, color="#FFFF00"),
            xref="paper", yref="paper",
        )
        return fig

    niveles = np.array(vp["niveles_raw"])
    vol     = np.array(vp["vol_raw"])
    centros = (niveles[:-1] + niveles[1:]) / 2

    # Normalizar volumen para color relativo
    vol_norm = vol / (vol.max() + 1e-9)
    colores  = [f"rgba(0,{int(200*v+55)},{int(255*(1-v))},0.7)" for v in vol_norm]

    fig.add_trace(go.Bar(
        x=vol, y=centros, orientation="h",
        marker_color=colores,
        name="Volumen por nivel",
    ))

    # Líneas VAH / POC / VAL
    for nivel, color, label in [
        (vp["VAH"], "#00FF88", f"VAH {vp['VAH']:.4f}"),
        (vp["POC"], "#FFFF00", f"POC {vp['POC']:.4f}"),
        (vp["VAL"], "#FF00CC", f"VAL {vp['VAL']:.4f}"),
    ]:
        fig.add_hline(y=nivel, line_color=color, line_dash="dash",
                      annotation_text=label, annotation_position="right")

    fig.update_layout(
        template="plotly_dark",
        title=f"📊 Volume Profile · Area de Valor = {vp['value_area_pct']}%",
        xaxis_title="Volumen Acumulado",
        yaxis_title="Precio",
    )
    return fig


# ═══════════════════════════════════════════════════════════════════════════════
# CAPA B — ENTROPÍA GEOPOLÍTICA (Spec Sección 1 · "The Fuel")
# ═══════════════════════════════════════════════════════════════════════════════

# Mapeo canónico activo → términos GDELT (Spec Sección 1 · Capa B)
_TERMINOS_GDELT = {
    "XAU":    "gold+OR+federal+reserve+OR+inflation",
    "BTC":    "bitcoin+OR+crypto+OR+blockchain",
    "NVDA":   "nvidia+OR+semiconductor+OR+AI+chips",
    "NIFTY50":"india+OR+nifty+OR+sensex",
    # ── EXPANSIÓN NIVEL 7: añadir términos para nuevos activos ───────────
    # "ETH":  "ethereum+OR+crypto+OR+defi",
    # "EURUSD":"euro+OR+ecb+OR+european+central+bank",
    # "SPY":  "sp500+OR+federal+reserve+OR+wall+street",
}

# Histórico de referencia para Event Shock (calibrar con SPEL_META.json)
_GDELT_MEDIA_HISTORICA = 120.0
_GDELT_STD_HISTORICA   = 45.0


def extraer_metricas_gdelt(activo: str = "XAU", ventana_horas: int = 24, timeout: int = 15) -> dict:
    """
    Extrae Event Shock Index, Goldstein Scale y Tone Variance de GDELT GKG.

    Bug #40 activo: timeout ngrok = 5s. Usar timeout=30 en producción Railway.
    Bug #39 activo: newsdata 401 — fuente secundaria inactiva, GDELT es primaria.
    Regla 17: documentado como "no bloqueante con justificación escrita".
    """
    query_tema = _TERMINOS_GDELT.get(activo, "market+OR+economy")
    ahora_utc  = datetime.utcnow()  # SIEMPRE utcnow() — Regla de Reloj Spec Sección 1
    inicio     = (ahora_utc - timedelta(hours=ventana_horas)).strftime("%Y%m%d%H%M%S")
    fin        = ahora_utc.strftime("%Y%m%d%H%M%S")

    url = (
        f"https://api.gdeltproject.org/api/v2/doc/doc?"
        f"query={query_tema}&mode=artlist&maxrecords=250"
        f"&startdatetime={inicio}&enddatetime={fin}&format=json"
    )

    try:
        r        = requests.get(url, timeout=timeout)
        r.raise_for_status()
        datos    = r.json()
        articulos = datos.get("articles", [])

        if not articulos:
            return {"status": "NO_DATA", "event_shock": 0.0, "goldstein": 0.0, "tone_variance": 0.0, "n_eventos": 0}

        tonos        = [float(a.get("tone", 0)) for a in articulos if a.get("tone") is not None]
        n_eventos    = len(articulos)
        event_shock  = (n_eventos - _GDELT_MEDIA_HISTORICA) / (_GDELT_STD_HISTORICA + 1e-9)
        goldstein    = float(np.mean(tonos)) if tonos else 0.0
        tone_var     = float(np.std(tonos))  if len(tonos) > 1 else 0.0

        return {
            "status":           "LIVE",
            "timestamp_utc":    ahora_utc.isoformat(),
            "activo":           activo,
            "n_eventos":        n_eventos,
            "event_shock":      round(event_shock, 3),
            "goldstein":        round(goldstein, 3),
            "tone_variance":    round(tone_var, 3),
        }

    except Exception as e:
        return {
            "status": "OFFLINE", "error": str(e),
            "event_shock": 0.0, "goldstein": 0.0, "tone_variance": 0.0, "n_eventos": 0,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# SCORE ENGINE — DECISIÓN OPERACIONAL (Spec Sección 2 · "The Decision Engine")
# ═══════════════════════════════════════════════════════════════════════════════

def calcular_score_de_oro(
    precio_actual: float,
    vp: dict,
    gdelt: dict,
    lstm_output: dict,
) -> dict:
    """
    Calcula el Score de Oro (0–100) y emite veredicto operacional.

    vp          : resultado de calcular_volume_profile()
    gdelt       : resultado de extraer_metricas_gdelt()
    lstm_output : resultado de SPELInferenceEngine.inferir()
    """
    score             = 0
    razon             = []
    direccion         = None
    nivel_entrada     = None
    fakeout_detectado = False

    # Si el volume profile es inválido, bloquear Capa A (Regla 19)
    if vp.get("volumen_invalido", False):
        razon.append("⚠️ CAPA A BLOQUEADA: Volume Profile inválido (Regla 19) → 0pts")
    else:
        # ── COMPONENTE A: Ruptura de Subasta (30 puntos) ──────────────────────
        vah, val, poc = vp["VAH"], vp["VAL"], vp["POC"]
        if precio_actual > vah:
            score        += 30
            direccion     = "CALL"
            nivel_entrada = vah
            razon.append(f"Precio ${precio_actual:.4f} SOBRE VAH ${vah:.4f} → CALL +30pts")
        elif precio_actual < val:
            score        += 30
            direccion     = "PUT"
            nivel_entrada = val
            razon.append(f"Precio ${precio_actual:.4f} BAJO VAL ${val:.4f} → PUT +30pts")
        else:
            razon.append(f"Precio DENTRO Value Area [${val:.4f}–${vah:.4f}] → RANGO 0pts")

    # ── COMPONENTE B: Validación Gödel / Entropía (40 puntos) ────────────────
    entropy      = lstm_output.get("entropy_shannon", 0.0)
    p90          = lstm_output.get("p90_threshold", 1.19)
    godel_activo = lstm_output.get("godel_activo", False)
    if godel_activo:
        score += 40
        razon.append(f"Gödel ACTIVO: entropy={entropy:.4f} ≥ P90={p90:.4f} → +40pts")
    else:
        razon.append(f"Gödel INACTIVO: entropy={entropy:.4f} < P90={p90:.4f} → 0pts")

    # ── COMPONENTE C: Confirmación GDELT (30 puntos) ──────────────────────────
    event_shock = gdelt.get("event_shock", 0.0)
    if gdelt.get("status") == "LIVE" and event_shock >= 2.0:
        score += 30
        razon.append(f"Event Shock={event_shock:.2f} ≥ 2.0 → GDELT CONFIRMA +30pts")
    elif gdelt.get("status") == "LIVE":
        razon.append(f"Event Shock={event_shock:.2f} < 2.0 → Ruido bajo → 0pts")
    else:
        razon.append("GDELT OFFLINE → Combustible geopolítico no verificado → 0pts")

    # ── FILTRO DE FAKEOUT ─────────────────────────────────────────────────────
    if score >= 30 and event_shock < 0.5:
        fakeout_detectado = True
        razon.append("⚠️ FALSA RUPTURA PROBABLE: entropía baja pese a ruptura de precio")

    # ── FACTOR GÖDEL DE ALTA VOLATILIDAD ─────────────────────────────────────
    modo_cautela = godel_activo and score >= 90 and event_shock > 3.0

    # ── PENALIZACIÓN POR LSTM STALE (Spec Sección 3) ─────────────────────────
    if lstm_output.get("status") == "STALE":
        score = max(0, score - 20)
        razon.append("⚠️ LSTM STALE: parquet >2h sin actualizar → -20pts de confianza")

    # ── VEREDICTO ─────────────────────────────────────────────────────────────
    if fakeout_detectado:
        veredicto, emoji = "⚠️ FALSA RUPTURA — NO OPERAR", "⚠️"
    elif score >= 90:
        if modo_cautela:
            veredicto, emoji = "🟠 OPERAR CON CAUTELA — ALTA VOLATILIDAD (Factor Gödel)", "🟠"
        else:
            veredicto, emoji = "🟢 TRADE DE ORO — ALTA CERTEZA", "🟢"
    elif 70 <= score < 90:
        veredicto, emoji = "🟡 TRADE EN DESARROLLO — PRECAUCIÓN", "🟡"
    else:
        veredicto, emoji = "⚪ NO OPERAR — EQUILIBRIO DE MERCADO", "⚪"

    return {
        "score":        score,
        "veredicto":    veredicto,
        "emoji":        emoji,
        "direccion":    direccion,
        "nivel_entrada":nivel_entrada,
        "objetivo_tp":  vp.get("POC"),
        "fakeout":      fakeout_detectado,
        "modo_cautela": modo_cautela,
        "p90":          p90,
        "razon":        razon,
        "componentes": {
            "subasta": 30 if score >= 30 and not vp.get("volumen_invalido") else 0,
            "godel":   40 if godel_activo else 0,
            "gdelt":   30 if event_shock >= 2.0 else 0,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SISTEMA DE AUTO-AUDITORÍA (Spec Sección 3 · "The Pre-Flight Checklist")
# ═══════════════════════════════════════════════════════════════════════════════

def ejecutar_auto_auditoria_spel(
    df_ohlcv: pl.DataFrame,
    engine_status: str,
    gdelt_data: dict,
) -> dict:
    """
    Diagnóstico de 4 niveles: Conexión · Datos · Lógica de Entropía · Volumen.
    Spec Sección 3 — implementación canónica completa.
    """
    checks = {
        "infraestructura":    {"ok": False, "mensaje": ""},
        "frescura_datos":     {"ok": False, "mensaje": ""},
        "coherencia_entropica":{"ok": False, "mensaje": ""},
        "volumen_valido":     {"ok": False, "mensaje": ""},
    }

    # NIVEL 1: Infraestructura
    gdelt_vivo  = gdelt_data.get("status") == "LIVE"
    modelo_vivo = engine_status == "LIVE"
    if modelo_vivo and gdelt_vivo:
        checks["infraestructura"] = {"ok": True,
            "mensaje": f"✅ Motor LSTM: LIVE | GDELT: LIVE ({gdelt_data.get('n_eventos', 0)} eventos)"}
    elif modelo_vivo:
        checks["infraestructura"] = {"ok": False,
            "mensaje": "⚠️ Motor LSTM: LIVE | GDELT: OFFLINE → Sin combustible geopolítico"}
    else:
        checks["infraestructura"] = {"ok": False,
            "mensaje": f"❌ Motor LSTM: {engine_status} | GDELT: {gdelt_data.get('status', 'UNKNOWN')}"}

    # NIVEL 2: Frescura de datos
    try:
        ultima     = df_ohlcv.sort("date")["date"][-1]
        if hasattr(ultima, "replace"):
            ultima = ultima.replace(tzinfo=None)
        delta_h    = (datetime.utcnow() - ultima).total_seconds() / 3600
        umbral     = 26
        checks["frescura_datos"] = {
            "ok": delta_h < umbral,
            "mensaje": (
                f"✅ Último dato: hace {delta_h:.1f}h (≤ {umbral}h aceptable)" if delta_h < umbral
                else f"❌ Datos VIEJOS: hace {delta_h:.1f}h → Parquet desactualizado. NO OPERAR."
            ),
        }
    except Exception as e:
        checks["frescura_datos"] = {"ok": False, "mensaje": f"❌ Error leyendo fecha del parquet: {e}"}

    # NIVEL 3: Coherencia entropía / precio
    try:
        vol_precio   = float(df_ohlcv["close"].tail(5).to_numpy().astype(float).std())
        event_shock  = gdelt_data.get("event_shock", 0.0)
        umbral_vol   = 0.0001
        if event_shock > 3.0 and vol_precio < umbral_vol:
            checks["coherencia_entropica"] = {"ok": False,
                "mensaje": f"⚠️ ANOMALÍA DE SENSOR: Event_Shock={event_shock:.2f} alto "
                           f"pero precio casi inmóvil (σ={vol_precio:.6f}). Posible API rota."}
        else:
            checks["coherencia_entropica"] = {"ok": True,
                "mensaje": f"✅ Coherencia OK: Event_Shock={event_shock:.2f}, σ_precio={vol_precio:.6f}"}
    except Exception as e:
        checks["coherencia_entropica"] = {"ok": False, "mensaje": f"❌ Error calculando coherencia: {e}"}

    # NIVEL 4: Volumen válido (Regla 19)
    try:
        vol_rec  = df_ohlcv["volume"].tail(10).to_numpy().astype(float)
        pct_cero = (vol_rec == 0).mean()
        vol_prom = vol_rec.mean()
        if pct_cero > 0.5:
            checks["volumen_valido"] = {"ok": False,
                "mensaje": f"❌ VOLUMEN INVÁLIDO (Regla 19): {pct_cero*100:.0f}% de velas = 0. VAH/VAL inválidos."}
        elif vol_prom < 1.0:
            checks["volumen_valido"] = {"ok": False,
                "mensaje": f"⚠️ VOLUMEN SOSPECHOSO: promedio={vol_prom:.4f}. Verificar fuente."}
        else:
            checks["volumen_valido"] = {"ok": True,
                "mensaje": f"✅ Volumen válido: promedio={vol_prom:.1f}, ceros={pct_cero*100:.0f}%"}
    except Exception as e:
        checks["volumen_valido"] = {"ok": False, "mensaje": f"❌ Error verificando volumen: {e}"}

    todos_ok = all(v["ok"] for v in checks.values())
    return {
        "checks":              checks,
        "sistema_operable":    todos_ok,
        "timestamp_auditoria": datetime.utcnow().isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CORRELACIÓN SISTÉMICA (Spec Sección 6 · "Gestión de Datos")
# ═══════════════════════════════════════════════════════════════════════════════

def detectar_correlacion_sistemica(scores: dict) -> dict:
    """
    Si múltiples activos tienen Score de Oro simultáneo, es crisis sistémica.
    scores: {"XAU": resultado_score, "BTC": resultado_score, ...}

    EXPANSIÓN NIVEL 12: integrar aquí correlation matrix dinámica multi-activo.
    """
    activos_señal = [a for a, s in scores.items() if s.get("score", 0) >= 70]
    n             = len(activos_señal)

    if n >= 3:
        tipo        = "CRISIS_SISTEMICA"
        descripcion = f"⚠️ {n} activos con señal simultánea → Evento macro global"
    elif n == 2:
        tipo        = "CORRELACION_ALTA"
        descripcion = f"🔶 {activos_señal} correlacionados → Posible propagación sectorial"
    elif n == 1:
        tipo        = "IDIOSINCRATICO"
        descripcion = f"✅ Solo {activos_señal[0]} en señal → Evento específico del activo"
    else:
        tipo        = "MERCADO_EN_PAZ"
        descripcion = "⚪ Sin señales activas → Equilibrio general"

    return {
        "tipo":           tipo,
        "descripcion":    descripcion,
        "activos_activos":activos_señal,
        "n_activos":      n,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MOTOR DE ALERTAS — TELEGRAM (Spec Sección 5 · "Webhooks")
# ═══════════════════════════════════════════════════════════════════════════════

def emitir_alerta_trade_de_oro(
    activo: str,
    score_resultado: dict,
    gdelt: dict,
    token_telegram: str,
    chat_id: str,
) -> bool:
    """
    Dispara alerta Telegram solo cuando Score ≥ 90 y no hay fakeout.
    Sprint 5. Retorna True si la alerta se envió, False si fue suprimida.
    """
    score   = score_resultado.get("score", 0)
    fakeout = score_resultado.get("fakeout", False)

    if score < 90 or fakeout:
        return False

    direccion   = score_resultado.get("direccion", "N/A")
    entrada     = score_resultado.get("nivel_entrada", "N/A")
    tp          = score_resultado.get("objetivo_tp", "N/A")
    event_shock = gdelt.get("event_shock", 0)
    modo        = "⚠️ CON CAUTELA" if score_resultado.get("modo_cautela") else "✅ ALTA CERTEZA"
    emoji_dir   = "🟢 CALL" if direccion == "CALL" else "🔴 PUT"

    mensaje = (
        f"🛰️ *SPEL TRADE DE ORO*\n\n"
        f"*Activo:* {activo}\n"
        f"*Dirección:* {emoji_dir}\n"
        f"*Score:* {score}/100 — {modo}\n"
        f"*Entrada:* ${entrada}\n"
        f"*TP (POC):* ${tp}\n"
        f"*Event Shock GDELT:* {event_shock:.2f}σ\n"
        f"_UTC: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}_"
    )

    try:
        url = f"https://api.telegram.org/bot{token_telegram}/sendMessage"
        r   = requests.post(
            url,
            json={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"[ALERTAS] ❌ Error enviando alerta Telegram: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# PARQUET PIPELINE — ACTUALIZACIÓN DIARIA (Spec Sección 6)
# ═══════════════════════════════════════════════════════════════════════════════

def actualizar_parquet_diario(activo: str, datos_nuevos: dict, ruta_parquet) -> bool:
    """
    Append de nuevos datos al parquet existente sin reescribir.
    Ejecutar a las 18:00 UTC (cierre mercado US).
    Validaciones: no duplicar fechas, mantener 30 cols canónicas.
    """
    from pathlib import Path
    ruta = Path(ruta_parquet)

    df_existente = pl.read_parquet(str(ruta)).sort("date")
    ultima_fecha = df_existente["date"][-1]
    nueva_fecha  = datos_nuevos.get("date")

    if nueva_fecha <= ultima_fecha:
        print(f"[PARQUET] SKIP {activo}: fecha {nueva_fecha} ya existe.")
        return False

    df_nuevo      = pl.DataFrame([datos_nuevos])
    df_actualizado = pl.concat([df_existente, df_nuevo]).sort("date")
    df_actualizado.write_parquet(str(ruta))
    print(f"[PARQUET] ✅ {activo}: {len(df_actualizado)} filas totales.")
    return True
