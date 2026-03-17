# ══════════════════════════════════════════════════════════════════════════════
# spel_hud.py
# SPEL v22 — Tactical HUD Streamlit ("El Ojo de Dios")
# Dashboard de Ataque · Terminal de Ejecución · Calculadora Interactiva
#
# Autor  : Abraham Fuenmayor
# Versión: v22.0.0 · 04 Mar 2026
#
# DEPENDENCIAS:
#   Requeridas : streamlit · polars · numpy
#   Externas   : spel_backbone_engine (v22.0.0)
#
# USO:
#   streamlit run spel_hud.py
#   — O integrar render_tactical_hud() en streamlit_dashboard_v7.py como tab.
#
# INTEGRACIÓN CON DASHBOARD v7:
#   En streamlit_dashboard_v7.py, dentro de la función que define los tabs:
#       from spel_hud import render_tactical_hud
#       with tab_ojo_de_dios:
#           render_tactical_hud(backbone_output=ranking_result)
#
# PROHIBIDO:
#   pandas · yfinance · datetime.utcnow() · st.form (usar callbacks directos)
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import streamlit as st

# Imports del backbone — manejo defensivo para modo standalone
try:
    from spel_backbone_engine import (
        BackboneSignal,
        FilterStage,
        KellyResult,
        RankingResult,
        SignalDirection,
        StructuralLevels,
        backbone_from_config,
        risk_manager_kelly_micro,
        ACTIVOS_VALIDOS,
        LSTM_BASE_ACCURACY,
        TP_RR_RATIO,
        KELLY_FRACTION,
    )
    _BACKBONE_AVAILABLE = True
except ImportError as _e:
    _BACKBONE_AVAILABLE = False
    _IMPORT_ERROR = str(_e)

# ── Logging ───────────────────────────────────────────────────────────────────
_log = logging.getLogger("spel.hud")
if not _log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)


# ══════════════════════════════════════════════════════════════════════════════
# PALETA Y ESTILOS (inyectados como CSS inline — sin archivos externos)
# ══════════════════════════════════════════════════════════════════════════════

_CSS = """
<style>
/* ── Base terminal oscuro ─────────────────────────────────────────────── */
[data-testid="stAppViewContainer"] { background: #0a0a0f; }
[data-testid="stSidebar"]          { background: #0d0d14; border-right: 1px solid #1e1e30; }
[data-testid="block-container"]    { padding-top: 1.2rem; }

/* ── Tipografía ───────────────────────────────────────────────────────── */
html, body, [class*="css"] {
    font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace;
    color: #c8d0e0;
}

/* ── Banner ALFA ──────────────────────────────────────────────────────── */
.spel-alfa-banner {
    background: linear-gradient(135deg, #0f0f1a 0%, #1a0a0a 100%);
    border: 2px solid #ff3333;
    border-radius: 8px;
    padding: 1.4rem 1.8rem;
    margin-bottom: 1.2rem;
    box-shadow: 0 0 28px rgba(255, 51, 51, 0.35), inset 0 0 20px rgba(255,51,51,0.04);
    animation: pulse-border 3s ease-in-out infinite;
}
@keyframes pulse-border {
    0%, 100% { box-shadow: 0 0 28px rgba(255, 51, 51, 0.35); }
    50%       { box-shadow: 0 0 48px rgba(255, 51, 51, 0.65); }
}
.spel-alfa-title {
    font-size: 1.75rem;
    font-weight: 900;
    color: #ff4444;
    letter-spacing: 0.06em;
    text-shadow: 0 0 18px rgba(255,68,68,0.8);
    margin: 0 0 0.25rem 0;
}
.spel-dir-long  { color: #00e676; font-size: 1.3rem; font-weight: 700; }
.spel-dir-short { color: #ff4444; font-size: 1.3rem; font-weight: 700; }
.spel-dir-flat  { color: #888;    font-size: 1.3rem; font-weight: 700; }
.spel-score     {
    color: #ffd740;
    font-size: 1.0rem;
    letter-spacing: 0.04em;
    margin-top: 0.3rem;
}

/* ── Tarjetas de métricas ─────────────────────────────────────────────── */
.spel-card {
    background: #0f0f1c;
    border: 1px solid #1e2040;
    border-radius: 6px;
    padding: 1rem 1.2rem;
    margin-bottom: 0.7rem;
}
.spel-card-title {
    font-size: 0.68rem;
    color: #5566aa;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    margin-bottom: 0.3rem;
}
.spel-card-value {
    font-size: 1.35rem;
    font-weight: 700;
    color: #e0e8ff;
}
.spel-card-sub {
    font-size: 0.75rem;
    color: #667;
    margin-top: 0.1rem;
}

/* ── Tabla de ejecución ───────────────────────────────────────────────── */
.spel-exec-table {
    width: 100%;
    border-collapse: collapse;
    margin: 0.6rem 0 1rem 0;
    font-size: 0.88rem;
}
.spel-exec-table th {
    background: #111128;
    color: #4466bb;
    text-transform: uppercase;
    font-size: 0.65rem;
    letter-spacing: 0.1em;
    padding: 0.5rem 0.8rem;
    text-align: left;
    border-bottom: 1px solid #1e2040;
}
.spel-exec-table td {
    padding: 0.55rem 0.8rem;
    border-bottom: 1px solid #12121f;
    color: #c8d0e0;
    font-weight: 600;
}
.spel-exec-table .price-entry { color: #e0e8ff; }
.spel-exec-table .price-sl    { color: #ff6666; }
.spel-exec-table .price-tp    { color: #66ff99; }

/* ── Bloque Kelly / Calculadora ───────────────────────────────────────── */
.spel-kelly-block {
    background: #0d0d1a;
    border: 1px solid #2a2a4a;
    border-left: 4px solid #ffd740;
    border-radius: 6px;
    padding: 1rem 1.2rem;
    margin: 0.5rem 0 1rem 0;
}
.spel-leverage-display {
    font-size: 2.8rem;
    font-weight: 900;
    color: #ffd740;
    text-shadow: 0 0 20px rgba(255,215,64,0.7);
    letter-spacing: 0.04em;
    line-height: 1;
    margin: 0.4rem 0;
}
.spel-leverage-label {
    font-size: 0.72rem;
    color: #887700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
}

/* ── Log de justificación cuantitativa ───────────────────────────────── */
.spel-quant-log {
    background: #080810;
    border: 1px solid #1a1a30;
    border-left: 3px solid #3344bb;
    border-radius: 4px;
    padding: 0.8rem 1rem;
    font-size: 0.80rem;
    color: #8899cc;
    line-height: 1.7;
    font-family: 'JetBrains Mono', monospace;
    margin-top: 0.5rem;
}
.spel-quant-key   { color: #4466dd; }
.spel-quant-value { color: #aabbee; font-weight: 600; }
.spel-quant-alert { color: #ff9944; }
.spel-quant-ok    { color: #44dd88; }

/* ── Ranking multi-activo ─────────────────────────────────────────────── */
.spel-rank-row {
    display: flex;
    align-items: center;
    gap: 0.8rem;
    padding: 0.45rem 0.6rem;
    border-radius: 4px;
    margin-bottom: 0.3rem;
    background: #0d0d1a;
    border: 1px solid #181830;
}
.spel-rank-pos   { color: #3344aa; font-size: 0.75rem; min-width: 1.2rem; }
.spel-rank-name  { font-weight: 700; font-size: 0.92rem; min-width: 5rem; color: #c8d0e0; }
.spel-rank-score { font-size: 0.82rem; color: #8899cc; flex: 1; }
.spel-rank-alpha {
    background: #1a0808;
    border-color: #ff3333 !important;
    box-shadow: 0 0 12px rgba(255,51,51,0.2);
}

/* ── Separador ────────────────────────────────────────────────────────── */
.spel-sep {
    border: none;
    border-top: 1px solid #1a1a2a;
    margin: 1rem 0;
}

/* ── Badge de warning ─────────────────────────────────────────────────── */
.spel-warn {
    background: #1a1000;
    border: 1px solid #554400;
    border-left: 4px solid #ffaa00;
    border-radius: 4px;
    padding: 0.6rem 0.9rem;
    font-size: 0.80rem;
    color: #ccaa44;
    margin: 0.5rem 0;
}
.spel-error {
    background: #1a0800;
    border: 1px solid #552200;
    border-left: 4px solid #ff6600;
    border-radius: 4px;
    padding: 0.6rem 0.9rem;
    font-size: 0.80rem;
    color: #ff8844;
    margin: 0.5rem 0;
}
</style>
"""


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS DE RENDERIZADO (funciones puras, sin estado)
# ══════════════════════════════════════════════════════════════════════════════

def _direction_html(direction: "SignalDirection") -> str:
    """Retorna el span HTML de dirección con color LONG/SHORT/FLAT."""
    if direction.value == "LONG":
        return f'<span class="spel-dir-long">▲ LONG</span>'
    elif direction.value == "SHORT":
        return f'<span class="spel-dir-short">▼ SHORT</span>'
    return f'<span class="spel-dir-flat">◼ FLAT</span>'


def _score_bar_html(score: float) -> str:
    """Renderiza una barra de progreso ASCII para el Natural Score."""
    filled  = int(score * 20)
    bar     = "█" * filled + "░" * (20 - filled)
    color   = "#ff4444" if score >= 0.75 else ("#ffd740" if score >= 0.50 else "#3355aa")
    return f'<span style="color:{color}; font-size:0.78rem">[{bar}] {score:.3f}</span>'


def _hurst_label(hurst: float) -> str:
    """Etiqueta legible del régimen de Hurst."""
    if hurst >= 0.65:   return "Trend Fuerte ↑"
    if hurst >= 0.55:   return "Trend Débil ↑"
    if hurst >= 0.45:   return "Random Walk ∅"
    if hurst >= 0.35:   return "Revertiente ↓"
    return "Anti-Persistente ↓↓"


def _anomaly_badge(anomaly_type: str, godel: bool) -> str:
    """Badge HTML para el tipo de anomalía."""
    godel_tag = " ⚡ GÖDEL" if godel else ""
    color_map = {
        "GODEL_ALIGNMENT":  "#ff4444",
        "DUAL_SPILLOVER":   "#ff8844",
        "REGIME_CHANGE":    "#ffaa00",
        "SPILLOVER_GOV":    "#44aaff",
        "SPILLOVER_BUS":    "#44ccff",
        "TREND_REGIME":     "#44ff88",
        "REVERTING_REGIME": "#aa44ff",
        "COMPOSITE_ALERT":  "#8899cc",
        "NONE":             "#445566",
    }
    color = color_map.get(anomaly_type, "#667799")
    return (
        f'<span style="background:{color}20; border:1px solid {color}; '
        f'border-radius:3px; padding:0.15rem 0.45rem; font-size:0.72rem; '
        f'color:{color}; font-weight:700;">{anomaly_type}{godel_tag}</span>'
    )


def _format_price(price: float, activo: str) -> str:
    """Formatea precio según el activo (BTC/XAU con más decimales)."""
    if activo in ("BTC",):
        return f"{price:,.2f}"
    if activo in ("XAU",):
        return f"{price:,.3f}"
    return f"{price:,.4f}"


# ══════════════════════════════════════════════════════════════════════════════
# RENDER_TACTICAL_HUD — Función principal de renderizado
# ══════════════════════════════════════════════════════════════════════════════

def render_tactical_hud(
    backbone_output: "RankingResult",
    show_ranking:    bool = True,
    show_all_assets: bool = False,
) -> None:
    """
    Renderiza el HUD Táctico de SPEL v22 — "El Ojo de Dios".

    Componentes:
    ─────────────────────────────────────────────────────────────────────────
    1. Banner ALFA DETECTADO  — activo, dirección, Natural Score.
    2. Matemática de Ejecución (Broker Mode) — Entry / SL / TP exactos.
    3. Calculadora Interactiva de Micro-Capital — Kelly + Apalancamiento.
    4. Justificación Cuantitativa — log Hurst / TE / Anomalía / Gödel.
    5. Ranking Multi-Activo — comparación NVDA / BTC / XAU / NIFTY50.

    Parámetros
    ----------
    backbone_output : RankingResult — Salida de SPELBackbone.dynamic_ranking().
    show_ranking    : bool — Mostrar la sección de ranking multi-activo.
    show_all_assets : bool — Expandir detalles de todos los activos (debug).

    Raises
    ------
    No lanza excepciones — errores se muestran como alertas en el UI.
    """
    if not _BACKBONE_AVAILABLE:
        st.markdown(
            f'<div class="spel-error">⚠️ spel_backbone_engine no disponible: '
            f'{_IMPORT_ERROR}</div>',
            unsafe_allow_html=True,
        )
        return

    # Inyectar CSS institucional (una sola vez por sesión)
    st.markdown(_CSS, unsafe_allow_html=True)

    alpha    = backbone_output.alpha_signal
    activo   = backbone_output.alpha_activo
    levels   = alpha.levels
    kelly    = alpha.kelly

    ts_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # ══════════════════════════════════════════════════════════════════════════
    # PANEL 1 — BANNER ALFA DETECTADO
    # ══════════════════════════════════════════════════════════════════════════

    flat_flag = (alpha.direction.value == "FLAT")
    banner_color = "#880000" if flat_flag else "#ff1111"

    if flat_flag:
        banner_html = f"""
        <div class="spel-alfa-banner" style="border-color:#554400;">
            <div class="spel-alfa-title" style="color:#ffaa00;">
                ⚠️ SIN SEÑAL ALFA — MERCADO EN RANDOM WALK
            </div>
            <div style="color:#887700; font-size:0.85rem; margin-top:0.4rem;">
                Hurst={alpha.hurst:.3f} ∈ [0.45, 0.55] · Filtro Bayesiano Activo
            </div>
            <div class="spel-score" style="margin-top:0.5rem;">
                {_score_bar_html(0.0)}
            </div>
        </div>
        """
    else:
        dir_html = _direction_html(alpha.direction)
        banner_html = f"""
        <div class="spel-alfa-banner">
            <div class="spel-alfa-title">
                🔥 ALFA DETECTADO: {activo}
            </div>
            <div style="margin-top: 0.4rem;">
                {dir_html}
                &nbsp;&nbsp;
                {_anomaly_badge(alpha.anomaly_type, alpha.godel_signal)}
            </div>
            <div class="spel-score" style="margin-top: 0.6rem;">
                Natural Score&nbsp;&nbsp;{_score_bar_html(alpha.natural_score)}
            </div>
            <div style="color:#334466; font-size:0.70rem; margin-top:0.5rem;">
                {ts_str} · posterior={alpha.posterior:.4f} · prior={alpha.prior_accuracy:.3f}
            </div>
        </div>
        """

    st.markdown(banner_html, unsafe_allow_html=True)

    if flat_flag:
        st.markdown(
            '<div class="spel-warn">El activo de mayor score está en zona de Random Walk '
            '(Hurst ∈ [0.45, 0.55]). El sistema no emite dirección operativa. '
            'Espera un cambio de régimen.</div>',
            unsafe_allow_html=True,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # PANEL 2 — MATEMÁTICA DE EJECUCIÓN (solo si hay señal y niveles)
    # ══════════════════════════════════════════════════════════════════════════

    if not flat_flag and levels is not None:
        st.markdown("##### 🎯 Matemática de Ejecución — Broker Mode", unsafe_allow_html=False)

        entry_fmt = _format_price(levels.entry_price, activo)
        sl_fmt    = _format_price(levels.stop_loss,   activo)
        tp_fmt    = _format_price(levels.take_profit,  activo)
        atr_fmt   = _format_price(levels.atr14,        activo)
        fib_fmt   = _format_price(levels.fib_21_level, activo)

        exec_table_html = f"""
        <table class="spel-exec-table">
            <thead>
                <tr>
                    <th>Parámetro</th>
                    <th>Valor</th>
                    <th>Notas</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td>📍 Precio de Entrada</td>
                    <td class="price-entry">{entry_fmt}</td>
                    <td>Close de última vela procesada</td>
                </tr>
                <tr>
                    <td>🛑 Stop Loss Estructural</td>
                    <td class="price-sl">{sl_fmt}</td>
                    <td>fib_lag_21 ± {levels.atr14:.4f} × 1.5 ATR14</td>
                </tr>
                <tr>
                    <td>🎯 Take Profit</td>
                    <td class="price-tp">{tp_fmt}</td>
                    <td>R:R = {levels.rr_ratio:.2f}x mínimo 2.5x</td>
                </tr>
                <tr>
                    <td>📐 ATR14 (Wilder)</td>
                    <td>{atr_fmt}</td>
                    <td>Volatilidad promedio 14 períodos</td>
                </tr>
                <tr>
                    <td>🌀 Fibonacci lag_21</td>
                    <td>{fib_fmt}</td>
                    <td>Nivel de liquidez institucional de referencia</td>
                </tr>
                <tr>
                    <td>⚖️ Riesgo por unidad</td>
                    <td style="color:#ff8888;">{_format_price(levels.risk_per_unit, activo)}</td>
                    <td>|Entry − SL| absoluto</td>
                </tr>
            </tbody>
        </table>
        """
        st.markdown(exec_table_html, unsafe_allow_html=True)

    elif not flat_flag and levels is None:
        st.markdown(
            '<div class="spel-warn">⚠️ Niveles estructurales no calculados — '
            'revisar datos OHLCV del parquet canónico (posible gap de datos).</div>',
            unsafe_allow_html=True,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # PANEL 3 — CALCULADORA INTERACTIVA DE MICRO-CAPITAL
    # ══════════════════════════════════════════════════════════════════════════

    st.markdown('<hr class="spel-sep">', unsafe_allow_html=True)
    st.markdown("##### 💰 Calculadora de Micro-Capital — Kelly Fraccional", unsafe_allow_html=False)

    col_input, col_result = st.columns([1, 2])

    with col_input:
        capital_input = st.number_input(
            label     = "Capital disponible en broker ($)",
            min_value = 1.0,
            max_value = 100_000.0,
            value     = float(kelly.capital if kelly else 10.0),
            step      = 5.0,
            format    = "%.2f",
            key       = "spel_capital_input",
            help      = "Ingresa el capital real disponible en tu cuenta (Binance/Forex). "
                        "El sistema recalcula el apalancamiento instantáneamente.",
        )

    # Recalcular Kelly con el capital ingresado interactivamente
    kelly_live: Optional[KellyResult] = None
    kelly_error: str = ""

    if not flat_flag and levels is not None and capital_input > 0:
        try:
            kelly_live = risk_manager_kelly_micro(
                capital        = capital_input,
                natural_score  = alpha.natural_score,
                win_loss_ratio = levels.rr_ratio,
                risk_per_unit  = levels.risk_per_unit,
                entry_price    = levels.entry_price,
            )
        except Exception as exc:
            kelly_error = str(exc)
            _log.error("render_tactical_hud: error Kelly live — %s", exc)

    with col_result:
        if kelly_live is not None:
            lev_color = "#ff4444" if kelly_live.leverage_suggested >= 50 else \
                        "#ffd740" if kelly_live.leverage_suggested >= 20 else "#44dd88"

            kelly_block_html = f"""
            <div class="spel-kelly-block">
                <div class="spel-leverage-label">Apalancamiento Exacto Sugerido</div>
                <div class="spel-leverage-display" style="color:{lev_color};">
                    {kelly_live.leverage_suggested}×
                </div>
                <div style="display:flex; gap:2rem; margin-top:0.7rem; font-size:0.80rem; color:#8899cc;">
                    <div>
                        <div style="color:#445566; font-size:0.65rem; text-transform:uppercase;">En riesgo ($)</div>
                        <div style="color:#ffaa55; font-weight:700;">${kelly_live.risk_amount:.4f}</div>
                    </div>
                    <div>
                        <div style="color:#445566; font-size:0.65rem; text-transform:uppercase;">Pérdida máx si SL ($)</div>
                        <div style="color:#ff6666; font-weight:700;">${kelly_live.max_loss_usd:.4f}</div>
                    </div>
                    <div>
                        <div style="color:#445566; font-size:0.65rem; text-transform:uppercase;">Posición nominal ($)</div>
                        <div style="color:#aabbee; font-weight:700;">${kelly_live.position_size:.2f}</div>
                    </div>
                    <div>
                        <div style="color:#445566; font-size:0.65rem; text-transform:uppercase;">Kelly f*</div>
                        <div style="color:#88aaee; font-weight:700;">{kelly_live.kelly_fractional:.4f}</div>
                    </div>
                </div>
                {f'<div class="spel-warn" style="margin-top:0.6rem; font-size:0.75rem;">{kelly_live.note}</div>' if kelly_live.note else ''}
            </div>
            """
            st.markdown(kelly_block_html, unsafe_allow_html=True)

        elif kelly_error:
            st.markdown(
                f'<div class="spel-error">Error calculando Kelly: {kelly_error}</div>',
                unsafe_allow_html=True,
            )
        elif flat_flag:
            st.markdown(
                '<div class="spel-warn">Sin señal operativa — calculadora inactiva.</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="spel-warn">Niveles estructurales no disponibles — '
                'calculadora requiere Stop Loss calculado.</div>',
                unsafe_allow_html=True,
            )

    # ══════════════════════════════════════════════════════════════════════════
    # PANEL 4 — JUSTIFICACIÓN CUANTITATIVA (Log de razonamiento)
    # ══════════════════════════════════════════════════════════════════════════

    st.markdown('<hr class="spel-sep">', unsafe_allow_html=True)
    st.markdown("##### 🧮 Justificación Cuantitativa", unsafe_allow_html=False)

    hurst_label   = _hurst_label(alpha.hurst)
    dom_te        = max(alpha.te_gov, alpha.te_bus)
    dom_actor     = "GOV" if alpha.te_gov >= alpha.te_bus else "BUS"
    spillover_tag = "✅ ACTIVO" if (alpha.te_gov >= 0.05 or alpha.te_bus >= 0.05) else "❌ AUSENTE"
    filter_color  = "#ff4444" if alpha.filter_stage.value == "REJECT_RW" else \
                    "#ffaa00" if "PENALIZED" in alpha.filter_stage.value else "#44dd88"
    godel_tag     = "✅ ACTIVA" if alpha.godel_signal else "❌ INACTIVA"

    quant_log_html = f"""
    <div class="spel-quant-log">
        <span class="spel-quant-key">Activo        </span>: <span class="spel-quant-value">{activo}</span><br>
        <span class="spel-quant-key">Hurst         </span>: <span class="spel-quant-value">{alpha.hurst:.4f}</span>
            &nbsp;→&nbsp;<span class="{'spel-quant-ok' if alpha.hurst >= 0.55 else 'spel-quant-alert'}">{hurst_label}</span><br>
        <span class="spel-quant-key">Régimen       </span>: <span class="spel-quant-value">{alpha.market_regime}</span><br>
        <span class="spel-quant-key">TE_GOV        </span>: <span class="spel-quant-value">{alpha.te_gov:.5f} bits</span><br>
        <span class="spel-quant-key">TE_BUS        </span>: <span class="spel-quant-value">{alpha.te_bus:.5f} bits</span><br>
        <span class="spel-quant-key">TE_DOM ({dom_actor})  </span>: <span class="spel-quant-value">{dom_te:.5f} bits</span>
            &nbsp;&nbsp;<span class="{'spel-quant-ok' if dom_te >= 0.05 else 'spel-quant-alert'}">{spillover_tag}</span><br>
        <span class="spel-quant-key">Anomalía      </span>: <span class="spel-quant-value">{alpha.anomaly_type}</span>
            &nbsp;(score={alpha.anomaly_score:.4f})<br>
        <span class="spel-quant-key">Cond. Gödel   </span>: <span class="{'spel-quant-ok' if alpha.godel_signal else 'spel-quant-alert'}">{godel_tag}</span><br>
        <span class="spel-quant-key">Tamiz Bayes   </span>: prior={alpha.prior_accuracy:.3f}
            · lik={alpha.likelihood:.3f} · post={alpha.posterior:.4f}<br>
        <span class="spel-quant-key">Score Natural </span>: <span class="spel-quant-value">{alpha.natural_score:.4f}</span><br>
        <span class="spel-quant-key">Filtro Etapa  </span>:
            <span style="color:{filter_color}; font-weight:700;">{alpha.filter_stage.value}</span><br>
        <span class="spel-quant-key">Dirección     </span>: <span class="spel-quant-value">{alpha.direction.value}</span>
    </div>
    """
    st.markdown(quant_log_html, unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # PANEL 5 — RANKING MULTI-ACTIVO
    # ══════════════════════════════════════════════════════════════════════════

    if show_ranking and backbone_output.ranked_scores:
        st.markdown('<hr class="spel-sep">', unsafe_allow_html=True)
        st.markdown("##### 📊 Ranking Dinámico Multi-Activo", unsafe_allow_html=False)

        ranking_items_html = ""
        for idx, (a, score) in enumerate(backbone_output.ranked_scores, start=1):
            is_alpha   = (a == backbone_output.alpha_activo)
            alpha_cls  = "spel-rank-alpha" if is_alpha else ""
            alpha_icon = "🔥 " if is_alpha else f"#{idx} "

            # Dirección del activo
            sig_a = backbone_output.all_signals.get(a)
            dir_a = sig_a.direction.value if sig_a else "—"
            dir_color = "#00e676" if dir_a == "LONG" else \
                        "#ff4444" if dir_a == "SHORT" else "#556677"

            bar_filled = int(score * 16)
            bar = "█" * bar_filled + "░" * (16 - bar_filled)

            ranking_items_html += f"""
            <div class="spel-rank-row {alpha_cls}">
                <span class="spel-rank-pos">{alpha_icon}</span>
                <span class="spel-rank-name">{a}</span>
                <span class="spel-rank-score">[{bar}] {score:.4f}</span>
                <span style="color:{dir_color}; font-size:0.80rem; font-weight:700; min-width:4rem;">{dir_a}</span>
            </div>
            """

        st.markdown(ranking_items_html, unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # PANEL 6 (OPCIONAL) — DETALLES DE TODOS LOS ACTIVOS (modo debug)
    # ══════════════════════════════════════════════════════════════════════════

    if show_all_assets and backbone_output.all_signals:
        with st.expander("🔬 Detalles Extendidos — Todos los Activos", expanded=False):
            for a, sig in backbone_output.all_signals.items():
                col1, col2, col3, col4 = st.columns(4)
                col1.metric(label=f"{a} — Score",   value=f"{sig.natural_score:.4f}")
                col2.metric(label="Hurst",           value=f"{sig.hurst:.3f}")
                col3.metric(label="TE_GOV",          value=f"{sig.te_gov:.4f}")
                col4.metric(label="Dirección",       value=sig.direction.value)


# ══════════════════════════════════════════════════════════════════════════════
# MODO STANDALONE — Demo con datos sintéticos si se ejecuta directamente
# ══════════════════════════════════════════════════════════════════════════════

def _build_demo_backbone_output() -> "RankingResult":
    """
    Construye un RankingResult de demostración con datos sintéticos
    cuando no hay un pipeline real disponible.

    NOTA: Solo para demo/desarrollo — en producción siempre usar
          SPELBackbone.dynamic_ranking() con datos reales.
    """
    import polars as pl
    from spel_backbone_engine import (
        BackboneSignal, FilterStage, KellyResult, RankingResult,
        SignalDirection, StructuralLevels,
    )
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc)

    # ── Señal NVDA (ALFA sintético) ───────────────────────────────────────────
    levels_nvda = StructuralLevels(
        activo        = "NVDA",
        entry_price   = 875.40,
        stop_loss     = 848.12,
        take_profit   = 943.58,
        atr14         = 18.19,
        fib_21_level  = 855.30,
        direction     = SignalDirection.LONG,
        risk_per_unit = 27.28,
        rr_ratio      = 2.5,
    )
    kelly_nvda = KellyResult(
        capital            = 50.0,
        natural_score      = 0.7814,
        win_loss_ratio     = 2.5,
        kelly_full         = 0.1940,
        kelly_fractional   = 0.0485,
        risk_amount        = 2.425,
        position_size      = 50.0 * 5,
        leverage_suggested = 5,
        max_loss_usd       = 7.51,
        note               = "",
    )
    signal_nvda = BackboneSignal(
        activo         = "NVDA",
        ts_generated   = ts,
        direction      = SignalDirection.LONG,
        natural_score  = 0.7814,
        filter_stage   = FilterStage.PASS_ALL,
        hurst          = 0.6823,
        te_gov         = 0.1243,
        te_bus         = 0.0871,
        anomaly_type   = "GODEL_ALIGNMENT",
        godel_signal   = True,
        market_regime  = "TRENDING_STRONG",
        levels         = levels_nvda,
        kelly          = kelly_nvda,
        prior_accuracy = 0.550,
        likelihood     = 0.920,
        posterior      = 0.9082,
        anomaly_score  = 0.8410,
    )

    # ── Señal BTC (segunda) ───────────────────────────────────────────────────
    signal_btc = BackboneSignal(
        activo         = "BTC",
        ts_generated   = ts,
        direction      = SignalDirection.LONG,
        natural_score  = 0.5120,
        filter_stage   = FilterStage.PENALIZED_TE,
        hurst          = 0.5912,
        te_gov         = 0.0312,
        te_bus         = 0.0421,
        anomaly_type   = "TREND_REGIME",
        godel_signal   = False,
        market_regime  = "TRENDING_WEAK",
        levels         = None,
        kelly          = None,
        prior_accuracy = 0.528,
        likelihood     = 0.165,
        posterior      = 0.5450,
        anomaly_score  = 0.5120,
    )

    # ── Señal XAU (tercera — FLAT) ────────────────────────────────────────────
    signal_xau = BackboneSignal(
        activo         = "XAU",
        ts_generated   = ts,
        direction      = SignalDirection.FLAT,
        natural_score  = 0.0,
        filter_stage   = FilterStage.REJECT_RW,
        hurst          = 0.4981,
        te_gov         = 0.0201,
        te_bus         = 0.0188,
        anomaly_type   = "NONE",
        godel_signal   = False,
        market_regime  = "RANDOM_WALK",
        levels         = None,
        kelly          = None,
        prior_accuracy = 0.547,
        likelihood     = 0.0,
        posterior      = 0.0,
        anomaly_score  = 0.1240,
    )

    # ── Señal NIFTY50 (cuarta) ────────────────────────────────────────────────
    signal_nifty = BackboneSignal(
        activo         = "NIFTY50",
        ts_generated   = ts,
        direction      = SignalDirection.SHORT,
        natural_score  = 0.6102,
        filter_stage   = FilterStage.PASS_ALL,
        hurst          = 0.4021,
        te_gov         = 0.0882,
        te_bus         = 0.0541,
        anomaly_type   = "DUAL_SPILLOVER",
        godel_signal   = False,
        market_regime  = "REVERTING_WEAK",
        levels         = None,
        kelly          = None,
        prior_accuracy = 0.625,
        likelihood     = 0.800,
        posterior      = 0.7881,
        anomaly_score  = 0.7200,
    )

    return RankingResult(
        alpha_activo  = "NVDA",
        alpha_signal  = signal_nvda,
        all_signals   = {
            "NVDA":    signal_nvda,
            "BTC":     signal_btc,
            "XAU":     signal_xau,
            "NIFTY50": signal_nifty,
        },
        ranked_scores = [
            ("NVDA",    0.7814),
            ("NIFTY50", 0.6102),
            ("BTC",     0.5120),
            ("XAU",     0.0),
        ],
    )


def _main_standalone() -> None:
    """Punto de entrada Streamlit en modo standalone (demo)."""
    st.set_page_config(
        page_title    = "SPEL v22 — Ojo de Dios",
        page_icon     = "🔥",
        layout        = "wide",
        initial_sidebar_state = "collapsed",
    )

    st.markdown(
        '<div style="color:#334; font-size:0.72rem; margin-bottom:0.5rem;">'
        '⚠️ MODO DEMO — datos sintéticos · Para producción conectar SPELBackbone.dynamic_ranking()'
        '</div>',
        unsafe_allow_html=True,
    )

    if not _BACKBONE_AVAILABLE:
        st.error(f"spel_backbone_engine.py no encontrado: {_IMPORT_ERROR}")
        return

    demo_output = _build_demo_backbone_output()
    render_tactical_hud(
        backbone_output = demo_output,
        show_ranking    = True,
        show_all_assets = True,
    )


if __name__ == "__main__":
    _main_standalone()
