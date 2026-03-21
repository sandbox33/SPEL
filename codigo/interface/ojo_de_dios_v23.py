# ══════════════════════════════════════════════════════════════════════════════
# ojo_de_dios_v23.py
# SPEL v23 — Ojo de Dios · Dashboard de Comando Puro
#
# Autor  : Abraham Fuenmayor
# Versión: v23.0.0 · 04 Mar 2026
#
# PRINCIPIO CARDINAL:
#   Este dashboard NO CALCULA NADA. Es un lector puro del estado producido
#   por el Orquestador (spel_orchestrator_v9.py). Toda la lógica cuantitativa
#   reside en el pipeline: Harvester → MathEngine → Backbone → state JSON.
#   Si el estado no existe → aviso de espera. Nunca un crash.
#
# TABS:
#   Tab 1 · Radar de Guerra   — PyDeck 3D · HUD Táctico (spel_hud.py)
#   Tab 2 · Alpha Signal      — BackboneSignal detalle + niveles estructurales
#   Tab 3 · Portfolio Ranking — 4 activos · Kelly sizing · dirección
#   Tab 4 · Causalidad        — Transfer Entropy + Hurst timeline desde state
#   Tab 5 · Auditoría v23     — Thread health · Lake status · cycle metrics
#
# EJECUCIÓN:
#   streamlit run ojo_de_dios_v23.py --server.port=8080 --server.address=0.0.0.0
#
# REGLAS ACTIVAS:
#   Regla 4  : λ por activo — BTC=21d · NVDA=63d · XAU=63d · NIFTY50=42d
#   Regla 13 : LSTM arquitectura inamovible (input=20, hidden=64, layers=1)
#   Regla D-0: Torre de Control al cargar el módulo
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
import requests
import streamlit as st

# ── Bootstrap de paths (proceso Streamlit independiente) ──────────────────────
def _bootstrap() -> None:
    _root = Path(os.environ.get("SPEL_ROOT_V23", "/content/spel_root"))
    for p in [str(_root), str(_root / "core"), str(_root / "interface")]:
        if p not in sys.path:
            sys.path.insert(0, p)

_bootstrap()

# ── Imports opcionales (fallan elegantemente) ──────────────────────────────────
try:
    import pydeck as pdk
    _PYDECK_OK = True
except ImportError:
    _PYDECK_OK = False

try:
    from spel_hud import render_tactical_hud as _render_hud_native, _BACKBONE_AVAILABLE
    _HUD_OK = _BACKBONE_AVAILABLE
except ImportError:
    _HUD_OK = False

_log = logging.getLogger("spel.ojo_v23")

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG DE PÁGINA
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="👁️ SPEL Ojo de Dios v23",
    page_icon="👁️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  .stApp { background-color: #07080f; color: #d0d0d0; }
  .metric-block {
    background: linear-gradient(135deg, #0d0d1a 0%, #141428 100%);
    border: 1px solid #2a2a4a; border-radius: 6px;
    padding: 12px 16px; margin: 3px 0;
  }
  .score-hero { font-size: 3.5rem; font-weight: 900; font-family: 'Courier New', monospace; }
  .label-mono { font-family: 'Courier New', monospace; font-size: 10px;
                color: #666; letter-spacing: 3px; }
  .pill-green { background:#003322; border:1px solid #00cc66; border-radius:4px;
                padding:2px 8px; color:#00cc66; font-size:11px; font-family:monospace; }
  .pill-red   { background:#220011; border:1px solid #cc0033; border-radius:4px;
                padding:2px 8px; color:#cc0033; font-size:11px; font-family:monospace; }
  .pill-amber { background:#221100; border:1px solid #cc6600; border-radius:4px;
                padding:2px 8px; color:#cc6600; font-size:11px; font-family:monospace; }
  .pill-flat  { background:#111; border:1px solid #444; border-radius:4px;
                padding:2px 8px; color:#666; font-size:11px; font-family:monospace; }
  .thread-alive   { color: #00cc66; }
  .thread-dead    { color: #cc0033; }
  .stTabs [data-baseweb="tab"] { font-size:0.82rem; font-weight:700; }
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# PATHS Y CONSTANTES
# ══════════════════════════════════════════════════════════════════════════════

_ROOT          = Path(os.environ.get("SPEL_ROOT_V23",  "/content/spel_root"))
_STATE_FILE    = Path(os.environ.get("SPEL_STATE_DIR", str(_ROOT / "state"))) / "ranking_latest.json"
_TELEGRAM_TOK  = os.environ.get("TELEGRAM_TOKEN", "")
_TELEGRAM_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
_ACTIVOS       = ["NVDA", "BTC", "XAU", "NIFTY50"]

# Posiciones 2D para el radar PyDeck (grilla 2×2 en coordenadas arbitrarias)
_RADAR_POSITIONS = {
    "NVDA":    {"x": -0.5, "y":  0.5, "label": "NVDA"},
    "BTC":     {"x":  0.5, "y":  0.5, "label": "BTC"},
    "XAU":     {"x": -0.5, "y": -0.5, "label": "XAU"},
    "NIFTY50": {"x":  0.5, "y": -0.5, "label": "NIFTY50"},
}

_DIRECTION_COLOR = {
    "LONG":    [0, 220, 100, 200],
    "SHORT":   [220, 50, 50, 200],
    "FLAT":    [100, 100, 100, 150],
    "UNKNOWN": [80, 80, 80, 120],
}

_FILTER_LABEL = {
    "PASS_ALL":       ("🟢", "PASS ALL"),
    "PENALIZED_TE":   ("🟡", "TE PENALIZADO"),
    "PENALIZED_BOTH": ("🟠", "DOBLE PENALIZACIÓN"),
    "REJECT_RW":      ("🔴", "RECHAZADO — RANDOM WALK"),
    "UNKNOWN":        ("⚪", "DESCONOCIDO"),
}


# ══════════════════════════════════════════════════════════════════════════════
# LECTOR DE ESTADO — ÚNICO PUNTO DE CONTACTO CON EL ORQUESTADOR
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=30, show_spinner=False)
def _read_state() -> dict[str, Any]:
    """
    Lee ranking_latest.json atómico producido por el Orquestador.
    TTL=30s: el Orquestador corre cada 60s, el dashboard actualiza cada 30s.
    En caso de cualquier error → estado de espera, nunca crash.
    """
    if not _STATE_FILE.exists():
        return {"status": "WAITING", "cycle": 0, "signals": {}, "ranked_scores": [],
                "alpha_activo": None, "data_thread_alive": False,
                "compute_thread_alive": False, "ts_utc": None,
                "last_harvest_utc": None, "last_compute_utc": None}
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "ERROR_READ", "cycle": 0, "signals": {}, "ranked_scores": [],
                "alpha_activo": None, "data_thread_alive": False,
                "compute_thread_alive": False, "ts_utc": None,
                "last_harvest_utc": None, "last_compute_utc": None}


def _sig(state: dict, activo: str) -> dict[str, Any]:
    """Acceso seguro a signal dict de un activo."""
    return state.get("signals", {}).get(activo, {})


def _fmt_ts(ts_str: str | None) -> str:
    if not ts_str:
        return "—"
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%H:%M:%S UTC")
    except Exception:
        return str(ts_str)[:19]


def _score_to_pct(natural_score: float | None) -> str:
    if natural_score is None:
        return "—"
    return f"{natural_score * 100:.1f}%"


def _direction_pill(direction: str) -> str:
    d = direction.upper() if direction else "UNKNOWN"
    if d == "LONG":
        return "<span class='pill-green'>▲ LONG</span>"
    elif d == "SHORT":
        return "<span class='pill-red'>▼ SHORT</span>"
    elif d == "FLAT":
        return "<span class='pill-flat'>— FLAT</span>"
    return "<span class='pill-flat'>? UNKNOWN</span>"


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

def _render_sidebar(state: dict) -> tuple[bool, float, bool]:
    with st.sidebar:
        st.markdown("## 👁️ Ojo de Dios v23")
        st.markdown("*Socio-Political Entropy Loss*")
        st.markdown("---")

        # Thread health
        d_alive = state.get("data_thread_alive", False)
        c_alive = state.get("compute_thread_alive", False)
        d_icon  = "🟢" if d_alive else "🔴"
        c_icon  = "🟢" if c_alive else "🔴"
        st.markdown(f"{d_icon} **Data Thread**")
        st.caption(f"Último harvest: {_fmt_ts(state.get('last_harvest_utc'))}")
        st.markdown(f"{c_icon} **Compute Thread**")
        st.caption(f"Último cómputo: {_fmt_ts(state.get('last_compute_utc'))}")
        st.caption(f"Ciclo #{state.get('cycle', 0)}")

        st.markdown("---")
        auto_refresh = st.toggle("♻️ Auto-refresh 30s", value=True)
        umbral = st.slider("🎯 Umbral Gatillo Telegram", 60.0, 100.0, 85.0, step=1.0)
        debug  = st.toggle("🔬 Modo Debug", value=False)

        st.markdown("---")
        if st.button("🔄 Forzar lectura", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        st.markdown("---")
        st.caption(f"Estado: `{state.get('status', '?')}`")
        st.caption(f"Version: `{state.get('version', 'v23.0.0')}`")
        ts_now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        st.caption(f"Render: `{ts_now}`")

    return auto_refresh, umbral, debug


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — RADAR DE GUERRA (PyDeck 3D + HUD)
# ══════════════════════════════════════════════════════════════════════════════

def _render_radar(state: dict) -> None:
    st.markdown("### 🎯 Radar de Guerra — Mapa de Señales 3D")
    st.caption(
        "Elevación = Score Natural Bayesiano. Color = Dirección. "
        "LONG=verde · SHORT=rojo · FLAT=gris. Fuente: SPELBackbone.dynamic_ranking()"
    )

    signals = state.get("signals", {})
    alpha   = state.get("alpha_activo")

    # ── Construir datos del radar ─────────────────────────────────────────────
    radar_rows = []
    for activo, pos in _RADAR_POSITIONS.items():
        sig  = signals.get(activo, {})
        ns   = sig.get("natural_score", 0.0)
        dirn = sig.get("direction", "FLAT").upper()
        anom = sig.get("anomaly_type", "NONE")
        col  = _DIRECTION_COLOR.get(dirn, _DIRECTION_COLOR["UNKNOWN"])
        is_alpha = (activo == alpha)
        radar_rows.append({
            "activo":        activo,
            "x":             pos["x"],
            "y":             pos["y"],
            "score":         ns,
            "elevation":     int(ns * 1000),
            "color":         col,
            "is_alpha":      is_alpha,
            "direction":     dirn,
            "anomaly_type":  anom,
            "label":         f"{'⭐ ' if is_alpha else ''}{activo}\n{dirn}\n{ns*100:.1f}%",
        })

    if _PYDECK_OK and radar_rows:
        col_layer = pdk.Layer(
            "ColumnLayer",
            data=radar_rows,
            get_position=["x", "y"],
            get_elevation="elevation",
            elevation_scale=1,
            radius=0.12,
            get_fill_color="color",
            pickable=True,
            auto_highlight=True,
            coverage=0.85,
        )
        text_layer = pdk.Layer(
            "TextLayer",
            data=radar_rows,
            get_position=["x", "y"],
            get_text="activo",
            get_size=18,
            get_color=[200, 200, 200, 240],
            get_alignment_baseline="'bottom'",
        )
        view = pdk.ViewState(
            longitude=0, latitude=0,
            zoom=12.5, pitch=50, bearing=20,
        )
        deck = pdk.Deck(
            layers=[col_layer, text_layer],
            initial_view_state=view,
            map_style="",
            tooltip={"text": "{activo}\nDirección: {direction}\nScore: {score:.3f}\nAnomalía: {anomaly_type}"},
            parameters={"clearColor": [0.027, 0.031, 0.059, 1]},
        )
        st.pydeck_chart(deck)
    else:
        # Fallback Plotly 3D si PyDeck no está disponible
        _render_radar_plotly(radar_rows, alpha)

    # ── HUD Táctico inline (spel_hud.py) ─────────────────────────────────────
    if _HUD_OK and alpha and signals:
        st.markdown("---")
        st.markdown("#### 🖥️ HUD Táctico — Alpha Signal")
        try:
            from spel_backbone_engine import (
                BackboneSignal, SignalDirection, FilterStage,
                StructuralLevels, KellyResult, RankingResult
            )
            ranking_obj = _deserialize_ranking(state)
            if ranking_obj:
                _render_hud_native(backbone_output=ranking_obj)
        except Exception as e:
            _log.warning("HUD nativo falló (%s) — usando fallback inline", e)
            _render_hud_fallback(state, alpha)
    else:
        _render_hud_fallback(state, alpha)


def _render_radar_plotly(rows: list[dict], alpha: str | None) -> None:
    """Radar 3D en Plotly como fallback de PyDeck."""
    if not rows:
        st.info("⏳ Esperando primer ciclo del Orquestador...")
        return

    colors = [f"rgba({r['color'][0]},{r['color'][1]},{r['color'][2]},0.8)" for r in rows]
    fig = go.Figure(data=[go.Bar(
        x=[r["activo"] for r in rows],
        y=[r["score"] * 100 for r in rows],
        text=[f"{r['direction']}<br>{r['score']*100:.1f}%" for r in rows],
        textposition="outside",
        marker_color=colors,
        marker_line_width=2,
        marker_line_color=["gold" if r["activo"] == alpha else "#333" for r in rows],
    )])
    fig.update_layout(
        height=360, paper_bgcolor="#07080f", plot_bgcolor="#0d0d1a",
        yaxis=dict(title="Score Natural (%)", range=[0, 110], color="#888"),
        xaxis=dict(color="#888"),
        font=dict(color="#ccc", family="Courier New"),
        showlegend=False,
        title=dict(text="Score Natural por Activo (SPELBackbone)", font_color="#888", font_size=11),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_hud_fallback(state: dict, alpha: str | None) -> None:
    """HUD inline cuando spel_hud.py no está disponible o falla."""
    if not alpha:
        st.info("⏳ Sin alpha signal aún. El Orquestador está inicializando...")
        return

    sig = _sig(state, alpha)
    if not sig:
        st.warning(f"⚠️ Signal para {alpha} no disponible en el estado actual.")
        return

    ns      = sig.get("natural_score", 0.0)
    dirn    = sig.get("direction", "FLAT")
    fstage  = sig.get("filter_stage", "UNKNOWN")
    regime  = sig.get("market_regime", "—")
    hurst   = sig.get("hurst", 0.0)
    te_gov  = sig.get("te_gov", 0.0)
    te_bus  = sig.get("te_bus", 0.0)
    post    = sig.get("posterior", 0.0)
    godel   = sig.get("godel_signal", False)
    anom    = sig.get("anomaly_type", "NONE")
    levels  = sig.get("levels") or {}
    kelly   = sig.get("kelly") or {}

    fi, fl = _FILTER_LABEL.get(fstage, ("⚪", fstage))
    pct_color = "#00FF9C" if ns >= 0.7 else ("#FFD700" if ns >= 0.5 else "#FF4B4B")

    st.markdown(f"""
    <div style="background:linear-gradient(135deg,#0d0d1a,#1a1a2e);border:1px solid {pct_color};
                border-radius:8px;padding:18px;font-family:'Courier New',monospace;
                box-shadow:0 0 25px {pct_color}22;">
      <div style="font-size:10px;color:#666;letter-spacing:3px">ALPHA SIGNAL · SPEL BACKBONE v22</div>
      <div style="display:flex;align-items:center;gap:24px;margin-top:8px;">
        <div>
          <div style="font-size:10px;color:#666">ACTIVO</div>
          <div style="font-size:22px;color:#ddd;font-weight:900">⭐ {alpha}</div>
        </div>
        <div>
          <div style="font-size:10px;color:#666">SCORE NATURAL</div>
          <div style="font-size:36px;color:{pct_color};font-weight:900">{ns*100:.1f}%</div>
        </div>
        <div>
          <div style="font-size:10px;color:#666">DIRECCIÓN</div>
          {_direction_pill(dirn)}
        </div>
        <div>
          <div style="font-size:10px;color:#666">FILTRO</div>
          <div style="font-size:12px;color:#aaa">{fi} {fl}</div>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("📐 Hurst",    f"{hurst:.3f}",  delta=regime)
    with c2:
        st.metric("🔀 TE_GOV",   f"{te_gov:.4f} bits")
        st.metric("🔀 TE_BUS",   f"{te_bus:.4f} bits")
    with c3:
        st.metric("🎲 Posterior", f"{post:.4f}",  delta=anom)
        st.metric("⚡ Gödel",    "✅ ACTIVO" if godel else "⚪ INACTIVO")
    with c4:
        if levels:
            st.metric("🎯 Entry",   f"${levels.get('entry_price', 0):.4f}")
            st.metric("🛑 Stop",    f"${levels.get('stop_loss', 0):.4f}")
            st.metric("💎 TP",      f"${levels.get('take_profit', 0):.4f}")
        if kelly:
            st.metric("📐 Kelly f", f"{kelly.get('kelly_fractional', 0)*100:.2f}%")
            st.metric("📦 Contratos", f"{kelly.get('contracts', 0)}")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — ALPHA SIGNAL DETALLE
# ══════════════════════════════════════════════════════════════════════════════

def _render_alpha_detail(state: dict) -> None:
    st.markdown("### ⭐ Alpha Signal — Detalle Bayesiano Completo")

    alpha = state.get("alpha_activo")
    if not alpha:
        st.info("⏳ Sin ciclo completado todavía.")
        return

    sig = _sig(state, alpha)
    st.caption(
        f"Activo Alpha: **{alpha}** · "
        f"Ciclo #{state.get('cycle', 0)} · "
        f"Timestamp: {_fmt_ts(state.get('last_compute_utc'))}"
    )

    # Bayesian decomposition
    st.markdown("#### Filtro Bayesiano de Triple Tamiz")
    fstage = sig.get("filter_stage", "UNKNOWN")
    fi, fl = _FILTER_LABEL.get(fstage, ("⚪", fstage))
    prior   = sig.get("likelihood", 0.0)
    post    = sig.get("posterior", 0.0)
    ns      = sig.get("natural_score", 0.0)
    base_acc = {"NVDA": 0.550, "BTC": 0.528, "XAU": 0.547, "NIFTY50": 0.625}.get(alpha, 0.5)

    fig_bayes = go.Figure()
    fig_bayes.add_trace(go.Bar(
        x=["Prior (base_accuracy)", "Likelihood (anomalía)", "Posterior (P·señal|evidencia)", "Score Natural"],
        y=[base_acc, prior, post, ns],
        marker_color=["#3B82F6", "#8B5CF6", "#10B981", "#F59E0B"],
        text=[f"{v:.4f}" for v in [base_acc, prior, post, ns]],
        textposition="outside",
    ))
    fig_bayes.update_layout(
        height=280, paper_bgcolor="#07080f", plot_bgcolor="#0d0d1a",
        yaxis=dict(range=[0, 1.1], color="#888"),
        xaxis=dict(color="#888"),
        font=dict(color="#ccc", family="Courier New", size=10),
        showlegend=False,
        title=dict(text=f"{fi} Filtro Bayesiano: {fl}", font_color="#aaa", font_size=12),
    )
    st.plotly_chart(fig_bayes, use_container_width=True)

    # Niveles estructurales
    levels = sig.get("levels") or {}
    kelly  = sig.get("kelly") or {}
    col_l, col_k = st.columns(2)

    with col_l:
        st.markdown("#### 🏛️ Niveles Estructurales (ATR + Fibonacci)")
        if levels:
            entry = levels.get("entry_price", 0)
            sl    = levels.get("stop_loss", 0)
            tp    = levels.get("take_profit", 0)
            atr   = levels.get("atr14", 0)
            rpu   = levels.get("risk_per_unit", 0)
            rr    = levels.get("rr_ratio", 0)
            dirn  = sig.get("direction", "FLAT")

            fig_l = go.Figure()
            # Take profit
            fig_l.add_hline(y=tp, line_color="#00FF9C", line_width=2,
                            annotation_text=f"TP  ${tp:.4f}", annotation_position="right")
            # Entry
            fig_l.add_hline(y=entry, line_color="#FFD700", line_width=2,
                            annotation_text=f"Entry ${entry:.4f}", annotation_position="right")
            # Stop
            fig_l.add_hline(y=sl, line_color="#FF4B4B", line_width=2,
                            annotation_text=f"SL  ${sl:.4f}", annotation_position="right")

            price_range = abs(tp - sl) * 1.3
            y_mid = (tp + sl) / 2
            fig_l.update_layout(
                height=250, paper_bgcolor="#07080f", plot_bgcolor="#0d0d1a",
                yaxis=dict(range=[y_mid - price_range/2, y_mid + price_range/2], color="#888"),
                xaxis=dict(visible=False),
                font=dict(color="#ccc", family="Courier New", size=10),
                showlegend=False,
                title=dict(text=f"ATR14={atr:.4f} · R/R={rr:.2f}x · Risk/unit=${rpu:.4f}",
                           font_color="#888", font_size=10),
            )
            st.plotly_chart(fig_l, use_container_width=True)
        else:
            st.info("Niveles no disponibles (FilterStage=REJECT_RW)")

    with col_k:
        st.markdown("#### 📐 Kelly Micro-Capital")
        if kelly:
            kf   = kelly.get("kelly_fractional", 0)
            kful = kelly.get("kelly_full", 0)
            cont = kelly.get("contracts", 0)
            cap  = kelly.get("capital", 0)
            risk = kelly.get("capital_at_risk", 0)
            st.metric("Kelly Full (f*)",      f"{kful*100:.3f}%")
            st.metric("Kelly Fraccional (25%)",f"{kf*100:.3f}%")
            st.metric("Contratos",             f"{cont}")
            st.metric("Capital en Riesgo",     f"${risk:.2f}")
            st.metric("Capital Total",         f"${cap:.2f}")
            risk_pct = risk / cap * 100 if cap > 0 else 0
            if risk_pct > 2.5:
                st.error(f"⚠️ Riesgo {risk_pct:.2f}% > 2.5% — revisar sizing")
            else:
                st.success(f"✅ Riesgo {risk_pct:.2f}% — dentro del límite")
        else:
            st.info("Kelly no calculado (señal FLAT o filtro rechazado)")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — PORTFOLIO RANKING
# ══════════════════════════════════════════════════════════════════════════════

def _render_portfolio(state: dict) -> None:
    st.markdown("### 🌍 Portfolio Ranking — 4 Activos · SPELBackbone.dynamic_ranking()")

    ranked = state.get("ranked_scores", [])
    signals = state.get("signals", {})
    alpha   = state.get("alpha_activo")

    if not ranked:
        st.info("⏳ Sin ranking disponible. El Orquestador no ha completado un ciclo.")
        return

    for rank_idx, (activo, score) in enumerate(ranked, start=1):
        sig    = signals.get(activo, {})
        dirn   = sig.get("direction", "FLAT")
        fstage = sig.get("filter_stage", "UNKNOWN")
        regime = sig.get("market_regime", "—")
        hurst  = sig.get("hurst", 0.0)
        te_gov = sig.get("te_gov", 0.0)
        godel  = sig.get("godel_signal", False)
        anom   = sig.get("anomaly_type", "NONE")
        kelly  = sig.get("kelly") or {}
        levels = sig.get("levels") or {}

        is_alpha = (activo == alpha)
        border   = "2px solid gold" if is_alpha else "1px solid #2a2a4a"
        bg       = "linear-gradient(135deg,#141400,#1a1a00)" if is_alpha else "linear-gradient(135deg,#0d0d1a,#141428)"
        fi, fl   = _FILTER_LABEL.get(fstage, ("⚪", fstage))
        ns_pct   = score * 100

        st.markdown(f"""
        <div style="background:{bg};border:{border};border-radius:8px;
                    padding:14px 18px;margin:6px 0;font-family:'Courier New',monospace;">
          <div style="display:flex;align-items:center;justify-content:space-between">
            <div>
              <span style="color:#666;font-size:10px">#{rank_idx} {'⭐ ALPHA' if is_alpha else 'ACTIVO'}</span>
              <span style="font-size:20px;color:#ddd;font-weight:900;margin-left:10px">{activo}</span>
              {_direction_pill(dirn)}
            </div>
            <div style="text-align:right">
              <div style="font-size:28px;color:{'gold' if is_alpha else '#aaa'};font-weight:900">{ns_pct:.1f}%</div>
              <div style="font-size:10px;color:#666">Score Natural</div>
            </div>
          </div>
          <div style="display:flex;gap:20px;margin-top:8px;font-size:11px;color:#888">
            <span>{fi} {fl}</span>
            <span>H={hurst:.3f} · {regime}</span>
            <span>TE_GOV={te_gov:.4f}b</span>
            <span>{'⚡ GÖDEL' if godel else ''}</span>
            <span style="color:#aaa">{anom}</span>
          </div>
        </div>
        """, unsafe_allow_html=True)

    # Gauge comparativo
    st.markdown("---")
    st.markdown("#### Score Natural Comparativo")
    cols = st.columns(len(ranked))
    for i, (activo, score) in enumerate(ranked):
        with cols[i]:
            is_alpha = (activo == alpha)
            dirn = signals.get(activo, {}).get("direction", "FLAT")
            arc_color = "#00FF9C" if dirn=="LONG" else ("#FF4B4B" if dirn=="SHORT" else "#555")
            fig = go.Figure(go.Indicator(
                mode="gauge+number",
                value=score * 100,
                number={"font": {"size": 22, "color": arc_color, "family": "Courier New"},
                        "suffix": "%"},
                title={"text": f"{'⭐ ' if is_alpha else ''}{activo}",
                       "font": {"size": 11, "color": "gold" if is_alpha else "#ccc",
                                "family": "Courier New"}},
                gauge={"axis": {"range": [0, 100], "tickfont": {"size": 8, "color": "#444"}},
                       "bar": {"color": arc_color, "thickness": 0.22},
                       "bgcolor": "#0d0d1a", "bordercolor": "#222",
                       "steps": [{"range": [0, 40], "color": "#111"},
                                  {"range": [40, 70], "color": "#181820"},
                                  {"range": [70, 100], "color": "#1d1d28"}],
                       "threshold": {"line": {"color": "#FFD700", "width": 2},
                                     "value": 70}},
            ))
            fig.update_layout(height=200, margin=dict(t=30, b=5, l=5, r=5),
                              paper_bgcolor="#07080f")
            st.plotly_chart(fig, use_container_width=True, key=f"rank_gauge_{activo}")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — CAUSALIDAD (TE + HURST)
# ══════════════════════════════════════════════════════════════════════════════

def _render_causality(state: dict) -> None:
    st.markdown("### 🔀 Transfer Entropy + Hurst — Causalidad Informacional")
    st.caption(
        "TE(GDELT→Precio) en bits. Umbral de causalidad: 0.05 bits = ~3% reducción de incertidumbre. "
        "Hurst > 0.65 = persistencia fuerte · < 0.35 = reversión."
    )

    signals = state.get("signals", {})
    if not signals:
        st.info("⏳ Sin datos de causalidad todavía.")
        return

    # TE chart
    activos  = list(signals.keys())
    te_govs  = [signals[a].get("te_gov", 0) for a in activos]
    te_buses = [signals[a].get("te_bus", 0) for a in activos]
    hursts   = [signals[a].get("hurst", 0) for a in activos]
    regimes  = [signals[a].get("market_regime", "—") for a in activos]

    fig_te = go.Figure()
    fig_te.add_trace(go.Bar(
        name="TE_GOV",
        x=activos, y=te_govs,
        marker_color="#3B82F6",
        text=[f"{v:.4f}" for v in te_govs], textposition="outside",
    ))
    fig_te.add_trace(go.Bar(
        name="TE_BUS",
        x=activos, y=te_buses,
        marker_color="#8B5CF6",
        text=[f"{v:.4f}" for v in te_buses], textposition="outside",
    ))
    fig_te.add_hline(y=0.05, line_dash="dash", line_color="#FFD700",
                     annotation_text="Umbral 0.05b", annotation_position="right")
    fig_te.add_hline(y=0.15, line_dash="dash", line_color="#FF4B4B",
                     annotation_text="Spillover fuerte 0.15b", annotation_position="right")
    fig_te.update_layout(
        height=300, barmode="group",
        paper_bgcolor="#07080f", plot_bgcolor="#0d0d1a",
        yaxis=dict(title="TE (bits)", color="#888"),
        xaxis=dict(color="#888"),
        font=dict(color="#ccc", family="Courier New", size=10),
        legend=dict(bgcolor="#0d0d1a", bordercolor="#333"),
        title=dict(text="Transfer Entropy GDELT→Precio por Actor (Schreiber 2000)",
                   font_color="#888", font_size=11),
    )
    st.plotly_chart(fig_te, use_container_width=True)

    # Hurst chart
    fig_h = go.Figure()
    hurst_colors = ["#00FF9C" if h > 0.65 else ("#FF4B4B" if h < 0.35 else "#FFD700")
                    for h in hursts]
    fig_h.add_trace(go.Bar(
        x=activos, y=hursts,
        marker_color=hurst_colors,
        text=[f"{h:.3f}<br>{r}" for h, r in zip(hursts, regimes)],
        textposition="outside",
    ))
    fig_h.add_hline(y=0.65, line_dash="dash", line_color="#00FF9C",
                    annotation_text="Trend fuerte (0.65)", annotation_position="right")
    fig_h.add_hline(y=0.5,  line_dash="dot",  line_color="#666",
                    annotation_text="Random Walk (0.5)", annotation_position="right")
    fig_h.add_hline(y=0.35, line_dash="dash", line_color="#FF4B4B",
                    annotation_text="Reversión fuerte (0.35)", annotation_position="right")
    fig_h.update_layout(
        height=280, paper_bgcolor="#07080f", plot_bgcolor="#0d0d1a",
        yaxis=dict(title="Hurst Exponent", range=[0, 1], color="#888"),
        xaxis=dict(color="#888"),
        font=dict(color="#ccc", family="Courier New", size=10),
        showlegend=False,
        title=dict(text="Exponente de Hurst · R/S (65%) + DFA (35%)",
                   font_color="#888", font_size=11),
    )
    st.plotly_chart(fig_h, use_container_width=True)

    # Tabla detallada
    st.markdown("#### Desglose por Activo")
    for activo in activos:
        sig = signals[activo]
        godel = sig.get("godel_signal", False)
        anom  = sig.get("anomaly_type", "NONE")
        anom_score = sig.get("anomaly_score", 0.0)
        fi, fl = _FILTER_LABEL.get(sig.get("filter_stage", "UNKNOWN"), ("⚪", "—"))
        dominant = sig.get("dominant_actor", "—") if hasattr(sig, "get") else "—"
        st.markdown(
            f"**{activo}** — TE_GOV={sig.get('te_gov',0):.4f}b · "
            f"TE_BUS={sig.get('te_bus',0):.4f}b · "
            f"Hurst={sig.get('hurst',0):.3f} · "
            f"Régimen: `{sig.get('market_regime','—')}` · "
            f"Anomalía: `{anom}` (score={anom_score:.3f}) · "
            f"Gödel: {'✅' if godel else '⚪'}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — AUDITORÍA v23
# ══════════════════════════════════════════════════════════════════════════════

def _render_audit(state: dict) -> None:
    st.markdown("### 🔬 Auditoría v23 — Estado del Orquestador")

    status = state.get("status", "UNKNOWN")
    cycle  = state.get("cycle", 0)
    d_alive = state.get("data_thread_alive", False)
    c_alive = state.get("compute_thread_alive", False)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("📡 Estado",    status)
    with col2:
        st.metric("🔄 Ciclos",   cycle)
    with col3:
        st.metric("📦 Data Thread", "🟢 VIVO" if d_alive else "🔴 MUERTO")
    with col4:
        st.metric("🧮 Compute Thread", "🟢 VIVO" if c_alive else "🔴 MUERTO")

    st.markdown("---")

    # Checks detallados
    checks = {
        "Estado general":       (status not in ("WAITING", "ERROR_READ", "INITIALIZING"), status),
        "Data Thread vivo":     (d_alive, "OK" if d_alive else "THREAD MUERTO — verificar Launcher"),
        "Compute Thread vivo":  (c_alive, "OK" if c_alive else "THREAD MUERTO — verificar Launcher"),
        "Ciclos completados":   (cycle > 0, f"{cycle} ciclos" if cycle > 0 else "Sin ciclos — Orquestador iniciando"),
        "Alpha signal presente":(state.get("alpha_activo") is not None, state.get("alpha_activo") or "Sin alpha"),
        "State file accesible": (_STATE_FILE.exists(), str(_STATE_FILE)),
    }

    for label, (ok, detail) in checks.items():
        if ok:
            st.success(f"**{label}:** {detail}")
        else:
            st.error(f"**{label}:** {detail}")

    # Señales de todos los activos
    signals = state.get("signals", {})
    if signals:
        st.markdown("---")
        st.markdown("#### Señales activas en estado")
        for a, s in signals.items():
            ns = s.get("natural_score", 0)
            d  = s.get("direction", "?")
            f  = s.get("filter_stage", "?")
            st.markdown(f"- **{a}**: `{d}` · Score={ns*100:.1f}% · FilterStage={f}")

    # Timestamps
    st.markdown("---")
    st.markdown("#### Timestamps del ciclo")
    st.caption(f"Último harvest: {_fmt_ts(state.get('last_harvest_utc'))}")
    st.caption(f"Último compute: {_fmt_ts(state.get('last_compute_utc'))}")
    st.caption(f"State ts:       {_fmt_ts(state.get('ts_utc'))}")
    st.caption(f"State file:     `{_STATE_FILE}`")
    st.caption(f"Versión state:  `{state.get('version', '?')}`")


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM — EL GATILLO (preservado y mejorado de v7)
# ══════════════════════════════════════════════════════════════════════════════

def _send_telegram(msg: str) -> dict:
    tok = _TELEGRAM_TOK
    cid = _TELEGRAM_CHAT
    if not tok or not cid:
        return {"ok": False, "error": "Credenciales no configuradas"}
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id": cid, "text": msg, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=10,
        )
        d = r.json()
        return {"ok": d.get("ok"), "message_id": d.get("result", {}).get("message_id"),
                "error": d.get("description")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _render_telegram(state: dict, umbral: float) -> None:
    alpha   = state.get("alpha_activo")
    signals = state.get("signals", {})
    if not alpha or not signals:
        return

    sig   = _sig(state, alpha)
    ns    = sig.get("natural_score", 0.0)
    dirn  = sig.get("direction", "FLAT")
    anom  = sig.get("anomaly_type", "NONE")
    hurst = sig.get("hurst", 0.0)
    te_gov= sig.get("te_gov", 0.0)
    godel = sig.get("godel_signal", False)
    cycle = state.get("cycle", 0)

    if "telegram_ultima_alerta" not in st.session_state:
        st.session_state["telegram_ultima_alerta"] = None

    def _puede():
        u = st.session_state["telegram_ultima_alerta"]
        return u is None or (datetime.now(timezone.utc) - u).total_seconds() >= 300

    def _disparo():
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        lvl = "🚨 ALERTA MÁXIMA" if ns >= 0.85 else ("⚠️ SEÑAL ALTA" if ns >= 0.70 else "📊 ACTUALIZACIÓN")
        msg = (
            f"<b>👁️ SPEL OJO DE DIOS v23 — {lvl}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>⭐ Alpha: {alpha} · Score: {ns*100:.1f}%</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Dirección: <code>{dirn}</code>\n"
            f"Anomalía: <code>{anom}</code>\n"
            f"Hurst: <code>{hurst:.3f}</code>\n"
            f"TE_GOV: <code>{te_gov:.4f} bits</code>\n"
            f"Gödel: {'✅ ACTIVO' if godel else '⚪ INACTIVO'}\n"
            f"Ciclo: #{cycle}\n\n"
            f"<b>Arquitectura:</b> LSTM input=20 · hidden=64 · layers=1 (Regla 13)\n"
            f"⏱️ <code>{ts} UTC</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>SPEL v23 · Backbone Bayesiano + TE + Hurst</i>"
        )
        res = _send_telegram(msg)
        if res.get("ok"):
            st.session_state["telegram_ultima_alerta"] = datetime.now(timezone.utc)
        return res

    st.markdown("---")
    st.markdown("#### 📡 El Gatillo — Telegram")

    tok_ok = bool(_TELEGRAM_TOK)
    cid_ok = bool(_TELEGRAM_CHAT)
    puede  = _puede()

    c1, c2 = st.columns(2)
    with c1:
        if tok_ok and cid_ok:
            st.success("✅ Credenciales OK")
        else:
            st.error(f"❌ TOKEN: {'✅' if tok_ok else '❌'} · CHAT_ID: {'✅' if cid_ok else '❌'}")
    with c2:
        if not puede:
            elapsed  = (datetime.now(timezone.utc) - st.session_state["telegram_ultima_alerta"]).total_seconds()
            st.warning(f"⏳ Cooldown: {max(0, 300-elapsed):.0f}s")
        else:
            st.info("⚡ Listo")

    if ns * 100 >= umbral:
        st.markdown(f"""
        <div style="background:#1A0000;border:2px solid #FF4B4B;border-radius:8px;
                    padding:12px;font-family:'Courier New',monospace;
                    box-shadow:0 0 15px #FF4B4B44;">
          <span style="color:#FF4B4B;font-size:14px;font-weight:bold;">
            🚨 GATILLO ARMADO — Score {ns*100:.1f}% ≥ {umbral:.0f}%
          </span>
        </div>
        """, unsafe_allow_html=True)

        _auto_key = f"auto_v23_{cycle}_{alpha}"
        if _auto_key not in st.session_state and tok_ok and cid_ok and puede:
            st.session_state[_auto_key] = True
            res = _disparo()
            st.success(f"🤖 AUTO-DISPARO: msg_id={res.get('message_id')}") if res.get("ok") \
                else st.error(f"🤖 AUTO-DISPARO fallido: {res.get('error')}")

        cb1, cb2 = st.columns(2)
        with cb1:
            if st.button("📨 ENVIAR MANUAL", type="primary", use_container_width=True,
                         disabled=not (tok_ok and cid_ok and puede), key="btn_tg_v23"):
                res = _disparo()
                st.success(f"✅ Enviado") if res.get("ok") else st.error(f"❌ {res.get('error')}")
        with cb2:
            if st.button("🔕 Silenciar 5min", use_container_width=True, key="btn_sil_v23"):
                st.session_state["telegram_ultima_alerta"] = datetime.now(timezone.utc)
                st.warning("Silenciado")


# ══════════════════════════════════════════════════════════════════════════════
# DESERIALIZACIÓN DE RANKINGRESULT (para spel_hud.py nativo)
# ══════════════════════════════════════════════════════════════════════════════

def _deserialize_ranking(state: dict) -> Any | None:
    """Reconstruye RankingResult desde el state dict para render_tactical_hud."""
    try:
        from spel_backbone_engine import (
            BackboneSignal, SignalDirection, FilterStage,
            StructuralLevels, KellyResult, RankingResult,
        )
        all_signals = {}
        for activo, s in state.get("signals", {}).items():
            levels = None
            lev_d  = s.get("levels") or {}
            if lev_d:
                levels = StructuralLevels(
                    activo=activo,
                    direction=SignalDirection(s.get("direction", "FLAT")),
                    entry_price=lev_d.get("entry_price", 0),
                    stop_loss=lev_d.get("stop_loss", 0),
                    take_profit=lev_d.get("take_profit", 0),
                    atr14=lev_d.get("atr14", 0),
                    risk_per_unit=lev_d.get("risk_per_unit", 0),
                    rr_ratio=lev_d.get("rr_ratio", 2.5),
                )
            kelly = None
            kel_d = s.get("kelly") or {}
            if kel_d:
                kelly = KellyResult(
                    activo=activo,
                    natural_score=s.get("natural_score", 0),
                    win_loss_ratio=2.5,
                    kelly_full=kel_d.get("kelly_full", 0),
                    kelly_fractional=kel_d.get("kelly_fractional", 0),
                    contracts=kel_d.get("contracts", 0),
                    capital_at_risk=kel_d.get("capital_at_risk", 0),
                    capital=kel_d.get("capital", 0),
                )
            bsig = BackboneSignal(
                activo=activo,
                direction=SignalDirection(s.get("direction", "FLAT")),
                natural_score=s.get("natural_score", 0),
                filter_stage=FilterStage(s.get("filter_stage", "PASS_ALL")),
                hurst=s.get("hurst", 0),
                te_gov=s.get("te_gov", 0),
                te_bus=s.get("te_bus", 0),
                anomaly_type=s.get("anomaly_type", "NONE"),
                godel_signal=s.get("godel_signal", False),
                market_regime=s.get("market_regime", "RANDOM_WALK"),
                levels=levels,
                kelly=kelly,
                likelihood=s.get("likelihood", 0),
                posterior=s.get("posterior", 0),
                anomaly_score=s.get("anomaly_score", 0),
            )
            all_signals[activo] = bsig

        alpha = state.get("alpha_activo")
        if not alpha or alpha not in all_signals:
            return None

        return RankingResult(
            alpha_activo=alpha,
            alpha_signal=all_signals[alpha],
            all_signals=all_signals,
            ranked_scores=[(a, s) for a, s in state.get("ranked_scores", [])],
        )
    except Exception as e:
        _log.warning("Deserialización RankingResult falló: %s", e)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    state = _read_state()
    auto_refresh, umbral, debug = _render_sidebar(state)

    # Header
    alpha  = state.get("alpha_activo", "—")
    status = state.get("status", "WAITING")
    cycle  = state.get("cycle", 0)
    ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    st.markdown(
        f"# 👁️ Ojo de Dios v23 · `{alpha}`",
    )
    status_color = "#00cc66" if status == "RUNNING" else "#cc6600" if status == "INITIALIZING" else "#cc0033"
    st.markdown(
        f"<span style='font-family:monospace;font-size:11px;color:{status_color}'>"
        f"● {status}</span> "
        f"<span style='font-family:monospace;font-size:11px;color:#555'>"
        f"Ciclo #{cycle} · {ts_now} UTC · v23.0.0</span>",
        unsafe_allow_html=True,
    )

    # Quick status bar
    signals = state.get("signals", {})
    d_alive = state.get("data_thread_alive", False)
    c_alive = state.get("compute_thread_alive", False)
    alpha_ns = _sig(state, alpha).get("natural_score", 0) if alpha != "—" else 0

    scols = st.columns(5)
    with scols[0]:
        st.markdown(f"{'🟢' if d_alive else '🔴'} **Data**: {'VIVO' if d_alive else 'MUERTO'}")
    with scols[1]:
        st.markdown(f"{'🟢' if c_alive else '🔴'} **Compute**: {'VIVO' if c_alive else 'MUERTO'}")
    with scols[2]:
        godel_any = any(_sig(state, a).get("godel_signal", False) for a in _ACTIVOS)
        st.markdown(f"{'⚡' if godel_any else '⚪'} **Gödel**: {'ACTIVO' if godel_any else 'INACTIVO'}")
    with scols[3]:
        pct_color = "green" if alpha_ns >= 0.7 else ("orange" if alpha_ns >= 0.5 else "red")
        st.markdown(f"⭐ **Alpha Score**: :{pct_color}[{alpha_ns*100:.1f}%]")
    with scols[4]:
        st.markdown(f"🔄 **Ciclo**: #{cycle}")

    st.markdown("---")

    # Tabs
    tabs = st.tabs([
        "🎯 Radar de Guerra",
        "⭐ Alpha Signal",
        "🌍 Portfolio Ranking",
        "🔀 Causalidad TE+Hurst",
        "🔬 Auditoría v23",
    ])
    with tabs[0]:
        _render_radar(state)
        _render_telegram(state, umbral)
    with tabs[1]:
        _render_alpha_detail(state)
    with tabs[2]:
        _render_portfolio(state)
    with tabs[3]:
        _render_causality(state)
    with tabs[4]:
        _render_audit(state)

    if debug:
        with st.expander("🔬 Debug — State JSON completo"):
            st.json(state)

    # Auto-refresh (no bloquea el proceso — solo agenda rerun)
    if auto_refresh:
        time.sleep(1)
        st.rerun()


if __name__ == "__main__":
    main()
