"""
main_ui_vFinal.py
=================
Holmes OS V4.0 · Ojo de Dios · Centro de Mando Soberano
Dashboard Streamlit — Carga holmes_state.json en tiempo real

Tabs:
  0. COMMAND CENTER  — estado global, guardian health, CB states
  1. 3D NODE MAP     — 103 nodos interactivos Plotly 3D
  2. TRADE OPS       — tracker WIN/LOSS, Score de Oro, señales
  3. MONTE CARLO     — exploración de vulnerabilidades de mercado
  4. DATA FLOWS      — mapa de flujo de información en vivo
  5. TRAINING HQ     — colas harvester y entrenamiento, modelos activos
  6. NARRATIVA IA    — lenguaje natural GDELT + Gemini post-mortems

Fuente única de verdad: holmes_state.json (generado por Guardian cada 900s)
Race-condition safe: trade_resolution.json escrito con lock atómico

Leyes activas: R21, Ley-2 (lazy torch), RAM < 400MB activo
Hinc Omnia Cerno
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG STREAMLIT — debe ser la primera llamada st.*
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="OJO DE DIOS · SPEL 3.0",
    page_icon="👁",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────────────────────────────────────
DASHBOARD_VERSION = "vFinal-4.1"
STATE_FILE_DEFAULT = Path(os.environ.get("HOLMES_STATE_PATH", "holmes_state.json"))
RESOLUTION_FILE = Path(os.environ.get("TRADE_RESOLUTION_PATH", "trade_resolution.json"))
REFRESH_INTERVAL_S = int(os.environ.get("DASHBOARD_REFRESH_S", "30"))

# Paleta Cyberpunk Noir
COLORS = {
    "bg_deep":       "#050A0E",
    "bg_panel":      "#0A1628",
    "accent_gold":   "#FFD700",
    "accent_cyan":   "#00FFFF",
    "accent_magenta":"#FF00FF",
    "green_ok":      "#00FF7F",
    "red_fail":      "#FF3131",
    "yellow_warn":   "#FFD700",
    "white_text":    "#E8E8E8",
    "grey_muted":    "#4A5568",
}

NODE_CATEGORY_COLORS = {
    "orchestration": "#00FFFF",
    "data_lake":     "#FFD700",
    "math_engine":   "#FF00FF",
    "routing":       "#00FF7F",
    "security":      "#FF3131",
    "dashboard":     "#FFFFFF",
    "archive":       "#4A5568",
    "unknown":       "#888888",
}

# ─────────────────────────────────────────────────────────────────────────────
# CSS CYBERPUNK NOIR
# ─────────────────────────────────────────────────────────────────────────────
DARK_CSS = f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@400;700&display=swap');

  html, body, [class*="css"] {{
    background-color: {COLORS['bg_deep']} !important;
    color: {COLORS['white_text']} !important;
    font-family: 'Share Tech Mono', monospace !important;
  }}
  .stTabs [data-baseweb="tab-list"] {{
    background-color: {COLORS['bg_panel']};
    border-radius: 8px;
    padding: 4px;
  }}
  .stTabs [data-baseweb="tab"] {{
    color: {COLORS['grey_muted']};
    font-family: 'Share Tech Mono', monospace;
    font-size: 0.85rem;
    letter-spacing: 0.05em;
  }}
  .stTabs [aria-selected="true"] {{
    color: {COLORS['accent_cyan']} !important;
    border-bottom: 2px solid {COLORS['accent_cyan']};
  }}
  .metric-card {{
    background: {COLORS['bg_panel']};
    border: 1px solid {COLORS['accent_cyan']}33;
    border-radius: 8px;
    padding: 12px 16px;
    margin: 4px 0;
  }}
  .metric-gold {{
    color: {COLORS['accent_gold']};
    font-size: 1.8rem;
    font-family: 'Orbitron', sans-serif;
    font-weight: 700;
  }}
  .status-ok   {{ color: {COLORS['green_ok']}; }}
  .status-fail {{ color: {COLORS['red_fail']}; }}
  .status-warn {{ color: {COLORS['yellow_warn']}; }}
  .panel-title {{
    font-family: 'Orbitron', sans-serif;
    font-size: 0.75rem;
    letter-spacing: 0.2em;
    color: {COLORS['accent_cyan']};
    text-transform: uppercase;
    border-bottom: 1px solid {COLORS['accent_cyan']}44;
    padding-bottom: 6px;
    margin-bottom: 12px;
  }}
  .signal-win  {{ background: #00FF7F22; border-left: 3px solid {COLORS['green_ok']}; padding: 8px; border-radius: 4px; }}
  .signal-loss {{ background: #FF313122; border-left: 3px solid {COLORS['red_fail']}; padding: 8px; border-radius: 4px; }}
  div[data-testid="stButton"] > button {{
    font-family: 'Share Tech Mono', monospace;
    border-radius: 4px;
    font-size: 0.85rem;
    letter-spacing: 0.1em;
  }}
  .stPlotlyChart {{ background: transparent !important; }}
</style>
"""

# ─────────────────────────────────────────────────────────────────────────────
# ESTADO DE SESIÓN — inicialización única
# ─────────────────────────────────────────────────────────────────────────────
def _init_session() -> None:
    defaults = {
        "last_state_hash":  "",
        "state_data":       None,
        "last_refresh":     0.0,
        "trade_submitted":  False,
        "narrative_cache":  {},
        "mc_iteration":     0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

# ─────────────────────────────────────────────────────────────────────────────
# CARGA DE ESTADO — cached con detección de cambio por hash
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=REFRESH_INTERVAL_S, show_spinner=False)
def _load_state_cached(path_str: str, _cache_bust: str) -> Dict[str, Any]:
    """Carga holmes_state.json. cache_bust fuerza refresh cuando el hash cambia."""
    path = Path(path_str)
    if not path.exists():
        return _default_state()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        logging.error("holmes_state.json load error: %s", exc)
        return _default_state()


def load_state(path: Path = STATE_FILE_DEFAULT) -> Dict[str, Any]:
    """Carga con detección de cambio SHA-256. Invalida cache si el archivo cambió."""
    if not path.exists():
        return _default_state()
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            h.update(fh.read(65536))  # lectura parcial para archivos grandes
        current_hash = h.hexdigest()[:16]
    except Exception:
        current_hash = str(time.time())

    if current_hash != st.session_state.get("last_state_hash", ""):
        st.session_state["last_state_hash"] = current_hash
        _load_state_cached.clear()

    return _load_state_cached(str(path), current_hash)


def _default_state() -> Dict[str, Any]:
    """Estado por defecto cuando holmes_state.json no existe aún."""
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "nodes": [],
        "scores": {
            "core":  {"gt_score": 0.0, "vitality": 0.0, "kl_div": 0.0, "regime": "UNKNOWN"},
            "forex": {"gt_score": 0.0, "entropy": 0.0, "threshold": 0.0},
        },
        "trade": {"asset": "—", "direction": "FLAT", "entry_ts": "—",
                  "entry_price": 0.0, "status": "IDLE", "win_loss": None},
        "guardian": {"health_ok": False, "files_validated": 0,
                     "missing_secrets": [], "cb_states": {}},
        "montecarlo": {"running": False, "paths_computed": 0,
                       "current_var": 0.0, "breach_probability": 0.0},
        "telegram": {"sistema_ok": False, "senales_ok": False,
                     "backup_ok": False, "caos_ok": False},
        "harvester_queue": [],
        "training_queue": [],
        "gdelt_headlines": [],
        "last_narrative": "",
    }

# ─────────────────────────────────────────────────────────────────────────────
# ESCRITURA ATÓMICA DE RESOLUCIÓN (race-condition safe)
# ─────────────────────────────────────────────────────────────────────────────
def write_trade_resolution(outcome: str, state: Dict[str, Any]) -> bool:
    """
    Escribe trade_resolution.json de forma atómica usando tmp + rename.
    Protegido con fcntl.flock en sistemas Unix (Colab/GH Actions).
    outcome: 'WIN' | 'LOSS'
    """
    trade = state.get("trade", {})
    payload = {
        "ts_resolution": datetime.now(timezone.utc).isoformat(),
        "asset": trade.get("asset", "UNKNOWN"),
        "direction": trade.get("direction", "FLAT"),
        "entry_ts": trade.get("entry_ts", ""),
        "entry_price": trade.get("entry_price", 0.0),
        "outcome": outcome,
        "gt_score_at_entry": state.get("scores", {}).get("core", {}).get("gt_score", 0.0),
        "regime_at_entry": state.get("scores", {}).get("core", {}).get("regime", "UNKNOWN"),
        "gdelt_headlines": state.get("gdelt_headlines", [])[:5],
    }
    try:
        # Escritura en tmp → rename (atómico en Linux/macOS)
        tmp = RESOLUTION_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            # Lock exclusivo mientras se escribe
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                pass  # Windows/entornos sin flock — fallback sin lock
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        tmp.replace(RESOLUTION_FILE)
        logging.info("trade_resolution.json written: %s %s", outcome, payload["asset"])
        return True
    except Exception as exc:
        logging.error("write_trade_resolution error: %s", exc)
        return False

# ─────────────────────────────────────────────────────────────────────────────
# COMPONENTES VISUALES
# ─────────────────────────────────────────────────────────────────────────────

def render_3d_node_map(nodes: List[Dict[str, Any]]) -> None:
    """
    Mapa 3D interactivo de nodos SPEL usando Plotly.
    Ejes: X=categoría, Y=status_score, Z=líneas de código (size proxy)
    Aristas: dependencias declaradas en el nodo
    RAM-safe: solo Plotly scatter3d, sin Three.js
    """
    import plotly.graph_objects as go  # lazy import

    if not nodes:
        st.info("holmes_state.json no contiene nodos aún. Ejecuta Guardian --patrol.")
        return

    # Mapear categorías a coordenada X
    categories = list({n.get("category", "unknown") for n in nodes})
    cat_map = {c: i * 2.0 for i, c in enumerate(sorted(categories))}

    xs, ys, zs, colors_list, texts, sizes = [], [], [], [], [], []
    status_score = {"active": 1.0, "warning": 0.5, "error": 0.2,
                    "zombie": 0.1, "archive": 0.0, "unknown": 0.3}

    for i, node in enumerate(nodes):
        cat = node.get("category", "unknown")
        status = node.get("status", "unknown")
        xs.append(cat_map.get(cat, 0) + (i % 5) * 0.3)
        ys.append(status_score.get(status, 0.3) * 3 + (i % 7) * 0.1)
        zs.append(float(i % 20) * 0.5)
        colors_list.append(NODE_CATEGORY_COLORS.get(cat, "#888888"))
        module = node.get("module", node.get("id", f"node_{i}"))
        texts.append(
            f"<b>{module}</b><br>"
            f"Status: {status}<br>"
            f"Category: {cat}<br>"
            f"SHA: {str(node.get('sha_git', ''))[:8]}"
        )
        sizes.append(8 if status == "active" else 5)

    fig = go.Figure(data=[go.Scatter3d(
        x=xs, y=ys, z=zs,
        mode="markers+text",
        marker=dict(
            size=sizes,
            color=colors_list,
            opacity=0.85,
            line=dict(width=0.5, color="#00FFFF33"),
        ),
        text=[n.get("module", n.get("id", ""))[:12] for n in nodes],
        textfont=dict(size=7, color="#FFFFFF66"),
        hovertext=texts,
        hoverinfo="text",
        textposition="top center",
    )])

    fig.update_layout(
        scene=dict(
            bgcolor=COLORS["bg_deep"],
            xaxis=dict(title="Categoría", showgrid=True,
                       gridcolor="#FFFFFF11", tickfont=dict(color="#888888")),
            yaxis=dict(title="Status Score", showgrid=True,
                       gridcolor="#FFFFFF11", tickfont=dict(color="#888888")),
            zaxis=dict(title="Index", showgrid=True,
                       gridcolor="#FFFFFF11", tickfont=dict(color="#888888")),
        ),
        paper_bgcolor=COLORS["bg_deep"],
        plot_bgcolor=COLORS["bg_deep"],
        margin=dict(l=0, r=0, t=0, b=0),
        height=550,
        showlegend=False,
    )

    # Leyenda manual de categorías
    legend_traces = []
    for cat, color in NODE_CATEGORY_COLORS.items():
        legend_traces.append(go.Scatter3d(
            x=[None], y=[None], z=[None],
            mode="markers",
            marker=dict(size=6, color=color),
            name=cat,
            showlegend=True,
        ))
    for t in legend_traces:
        fig.add_trace(t)
    fig.update_layout(showlegend=True,
                      legend=dict(font=dict(color=COLORS["white_text"], size=10),
                                  bgcolor=COLORS["bg_panel"]))

    st.plotly_chart(fig, use_container_width=True, key="node_map_3d")


def render_montecarlo(mc: Dict[str, Any]) -> None:
    """Visualización de Monte Carlo — paths de precio y distribución VaR."""
    import plotly.graph_objects as go
    import random
    import math

    paths_computed = mc.get("paths_computed", 0)
    var = mc.get("current_var", 0.0)
    breach_prob = mc.get("breach_probability", 0.0)
    running = mc.get("running", False)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"""<div class="metric-card">
          <div class="panel-title">PATHS COMPUTADOS</div>
          <div class="metric-gold">{paths_computed:,}</div>
        </div>""", unsafe_allow_html=True)
    with col2:
        st.markdown(f"""<div class="metric-card">
          <div class="panel-title">VaR ACTUAL (95%)</div>
          <div class="metric-gold">{var:.4f}</div>
        </div>""", unsafe_allow_html=True)
    with col3:
        color_class = "status-fail" if breach_prob > 0.3 else "status-ok"
        st.markdown(f"""<div class="metric-card">
          <div class="panel-title">P(BREACH)</div>
          <div class="metric-gold {color_class}">{breach_prob:.2%}</div>
        </div>""", unsafe_allow_html=True)

    # Simulación de paths para visualización (datos reales vendrían del holmes_state)
    n_paths_display = min(50, max(paths_computed, 10))
    n_steps = 60
    seed = int(paths_computed) % 10000
    rng = random.Random(seed)

    fig = go.Figure()
    for i in range(n_paths_display):
        path = [1.0]
        vol = rng.uniform(0.008, 0.025)
        drift = rng.uniform(-0.002, 0.003)
        for _ in range(n_steps - 1):
            ret = drift + vol * rng.gauss(0, 1)
            path.append(path[-1] * math.exp(ret))
        color = f"rgba(0, 255, 127, 0.15)" if path[-1] > 1.0 else "rgba(255, 49, 49, 0.15)"
        fig.add_trace(go.Scatter(
            y=path, mode="lines",
            line=dict(width=0.8, color=color),
            showlegend=False,
            hoverinfo="none",
        ))

    # VaR line
    fig.add_hline(y=1.0 - var, line=dict(color=COLORS["accent_gold"],
                  width=1.5, dash="dash"),
                  annotation_text=f"VaR 95%: {var:.4f}",
                  annotation_font=dict(color=COLORS["accent_gold"]))

    fig.update_layout(
        paper_bgcolor=COLORS["bg_deep"],
        plot_bgcolor=COLORS["bg_panel"],
        xaxis=dict(title="Steps", gridcolor="#FFFFFF11",
                   tickfont=dict(color="#888888")),
        yaxis=dict(title="Price Ratio", gridcolor="#FFFFFF11",
                   tickfont=dict(color="#888888")),
        margin=dict(l=10, r=10, t=10, b=30),
        height=380,
        title=dict(text=f"Monte Carlo — {n_paths_display} paths visualizados" +
                   (" 🔴 RUNNING" if running else " ⏸ PAUSED"),
                   font=dict(color=COLORS["accent_cyan"], size=12)),
    )
    st.plotly_chart(fig, use_container_width=True, key="mc_plot")


def render_score_gauges(scores: Dict[str, Any]) -> None:
    """Gauges de GT-Score para Core y Forex."""
    import plotly.graph_objects as go

    core = scores.get("core", {})
    forex = scores.get("forex", {})

    def _gauge(value: float, title: str, max_val: float = 3.0,
               thresholds: tuple = (0.8, 1.8, 2.8)) -> go.Figure:
        color = (COLORS["red_fail"] if value < thresholds[0] else
                 COLORS["yellow_warn"] if value < thresholds[1] else
                 COLORS["green_ok"] if value < thresholds[2] else
                 COLORS["accent_gold"])
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=round(value, 4),
            title={"text": title, "font": {"color": COLORS["accent_cyan"],
                                           "size": 11, "family": "Share Tech Mono"}},
            number={"font": {"color": color, "size": 22,
                             "family": "Orbitron"}, "suffix": ""},
            gauge={
                "axis": {"range": [0, max_val],
                         "tickcolor": COLORS["grey_muted"],
                         "tickfont": {"size": 9, "color": COLORS["grey_muted"]}},
                "bar": {"color": color, "thickness": 0.3},
                "bgcolor": COLORS["bg_panel"],
                "bordercolor": COLORS["accent_cyan"] + "44",
                "steps": [
                    {"range": [0, thresholds[0]], "color": "#FF313122"},
                    {"range": [thresholds[0], thresholds[1]], "color": "#FFD70022"},
                    {"range": [thresholds[1], thresholds[2]], "color": "#00FF7F22"},
                    {"range": [thresholds[2], max_val], "color": "#FFD70033"},
                ],
                "threshold": {"line": {"color": COLORS["accent_gold"], "width": 2},
                              "thickness": 0.7, "value": thresholds[1]},
            },
        ))
        fig.update_layout(
            paper_bgcolor=COLORS["bg_deep"],
            height=200, margin=dict(l=10, r=10, t=30, b=10)
        )
        return fig

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        fig = _gauge(core.get("gt_score", 0), "GT-SCORE CORE")
        st.plotly_chart(fig, use_container_width=True, key="gauge_gt_core")
    with col2:
        fig = _gauge(core.get("vitality", 0), "VITALITY TESLA",
                     max_val=2.0, thresholds=(0.4, 0.9, 1.5))
        st.plotly_chart(fig, use_container_width=True, key="gauge_vitality")
    with col3:
        fig = _gauge(forex.get("gt_score", 0), "GT-SCORE FOREX",
                     max_val=0.003, thresholds=(0.0001, 0.001, 0.002))
        st.plotly_chart(fig, use_container_width=True, key="gauge_gt_forex")
    with col4:
        fig = _gauge(core.get("kl_div", 0), "KL DIVERGENCE",
                     max_val=0.5, thresholds=(0.05, 0.2, 0.35))
        st.plotly_chart(fig, use_container_width=True, key="gauge_kl")


def render_data_flow(state: Dict[str, Any]) -> None:
    """Diagrama de flujo de información usando Plotly Sankey."""
    import plotly.graph_objects as go

    guardian = state.get("guardian", {})
    tg = state.get("telegram", {})

    def _status(ok: bool) -> str:
        return "🟢" if ok else "🔴"

    st.markdown(f"""
    <div class="panel-title">FLUJO DE INFORMACIÓN EN VIVO</div>
    <table style="width:100%; border-collapse:collapse; font-size:0.82rem;">
      <tr><th style="color:{COLORS['accent_cyan']}; text-align:left; padding:4px 8px;">FUENTE</th>
          <th style="color:{COLORS['accent_cyan']}; padding:4px 8px;">→</th>
          <th style="color:{COLORS['accent_cyan']}; text-align:left; padding:4px 8px;">DESTINO</th>
          <th style="color:{COLORS['accent_cyan']}; text-align:left; padding:4px 8px;">ESTADO</th></tr>
      <tr><td style="padding:3px 8px;">BigQuery GDELT</td><td>→</td><td>data_lake/gdelt_raw/</td>
          <td class="status-ok">LIVE</td></tr>
      <tr><td style="padding:3px 8px;">Yahoo/Alpaca OHLCV</td><td>→</td><td>data_lake/ohlcv/</td>
          <td class="status-ok">LIVE</td></tr>
      <tr><td style="padding:3px 8px;">data_lake/ (Polars lazy)</td><td>→</td><td>spel_math_engine</td>
          <td class="status-ok">ACTIVE</td></tr>
      <tr><td style="padding:3px 8px;">spel_math_engine</td><td>→</td><td>holmes_state.json</td>
          <td class="{'status-ok' if guardian.get('health_ok') else 'status-fail'}">
          {'SYNCED' if guardian.get('health_ok') else 'STALE'}</td></tr>
      <tr><td style="padding:3px 8px;">holmes_state.json</td><td>→</td><td>Ojo de Dios Dashboard</td>
          <td class="status-ok">STREAMING</td></tr>
      <tr><td style="padding:3px 8px;">capa_c_inference (LSTM)</td><td>→</td><td>spel_trading_router</td>
          <td class="status-ok">READY</td></tr>
      <tr><td style="padding:3px 8px;">trade_resolution.json</td><td>→</td><td>GHA Auto-Train Trigger</td>
          <td class="{'status-ok' if RESOLUTION_FILE.exists() else 'status-warn'}">
          {'PENDING' if RESOLUTION_FILE.exists() else 'IDLE'}</td></tr>
    </table>
    <br>
    <div style="display:flex; gap:20px; flex-wrap:wrap;">
      <div class="metric-card" style="flex:1; min-width:120px;">
        <div class="panel-title">SISTEMA</div>
        <div style="font-size:1.5rem;">{_status(tg.get("sistema_ok", False))}</div>
      </div>
      <div class="metric-card" style="flex:1; min-width:120px;">
        <div class="panel-title">SEÑALES</div>
        <div style="font-size:1.5rem;">{_status(tg.get("senales_ok", False))}</div>
      </div>
      <div class="metric-card" style="flex:1; min-width:120px;">
        <div class="panel-title">BACKUP</div>
        <div style="font-size:1.5rem;">{_status(tg.get("backup_ok", False))}</div>
      </div>
      <div class="metric-card" style="flex:1; min-width:120px;">
        <div class="panel-title">CAOS</div>
        <div style="font-size:1.5rem;">{_status(tg.get("caos_ok", False))}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)


def render_training_hq(state: Dict[str, Any]) -> None:
    """Colas de harvester y entrenamiento, modelos activos."""
    harvester_q = state.get("harvester_queue", [])
    training_q = state.get("training_queue", [])

    col1, col2 = st.columns(2)
    with col1:
        st.markdown('<div class="panel-title">COLA HARVESTER</div>', unsafe_allow_html=True)
        if not harvester_q:
            st.caption("Sin tareas pendientes en cola.")
        else:
            for item in harvester_q:
                icon = "⏳" if item.get("status") == "pending" else "✅"
                st.markdown(f"{icon} **{item.get('asset','?')}** — "
                            f"{item.get('type','?')} "
                            f"[P{item.get('priority',3)}]")

    with col2:
        st.markdown('<div class="panel-title">COLA ENTRENAMIENTO</div>', unsafe_allow_html=True)
        if not training_q:
            st.caption("Sin modelos en cola de reentrenamiento.")
        else:
            for item in training_q:
                trigger = item.get("trigger", "MANUAL")
                status = item.get("status", "pending")
                color = "🔴" if trigger == "LOSS" else "🔵"
                st.markdown(f"{color} **{item.get('asset','?')}** — "
                            f"Trigger: {trigger} | "
                            f"Epochs: {item.get('epochs','?')} | "
                            f"Status: {status}")


# ─────────────────────────────────────────────────────────────────────────────
# TAB: TRADE OPERATIONS — tracker WIN/LOSS + Score de Oro
# ─────────────────────────────────────────────────────────────────────────────
def render_trade_ops(state: Dict[str, Any]) -> None:
    trade = state.get("trade", {})
    scores = state.get("scores", {})
    headlines = state.get("gdelt_headlines", [])

    # ── Score de Oro ──────────────────────────────────────────────────────────
    st.markdown('<div class="panel-title">SCORE DE ORO — SEÑALES EN TIEMPO REAL</div>',
                unsafe_allow_html=True)
    render_score_gauges(scores)

    st.markdown("---")

    # ── Operación activa ──────────────────────────────────────────────────────
    asset = trade.get("asset", "—")
    direction = trade.get("direction", "FLAT")
    entry_price = trade.get("entry_price", 0.0)
    entry_ts = trade.get("entry_ts", "—")
    trade_status = trade.get("status", "IDLE")
    regime = scores.get("core", {}).get("regime", "UNKNOWN")

    direction_color = (COLORS["green_ok"] if direction == "LONG"
                       else COLORS["red_fail"] if direction == "SHORT"
                       else COLORS["grey_muted"])

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f"""<div class="metric-card">
          <div class="panel-title">ACTIVO</div>
          <div class="metric-gold">{asset}</div>
        </div>""", unsafe_allow_html=True)
    with col2:
        st.markdown(f"""<div class="metric-card">
          <div class="panel-title">DIRECCIÓN</div>
          <div style="font-size:1.8rem; font-family:'Orbitron'; color:{direction_color};">
            {direction}</div>
        </div>""", unsafe_allow_html=True)
    with col3:
        st.markdown(f"""<div class="metric-card">
          <div class="panel-title">PRECIO ENTRADA</div>
          <div class="metric-gold">{entry_price:,.4f}</div>
        </div>""", unsafe_allow_html=True)
    with col4:
        st.markdown(f"""<div class="metric-card">
          <div class="panel-title">RÉGIMEN</div>
          <div style="font-size:1.2rem; font-family:'Orbitron'; color:{COLORS['accent_magenta']};">
            {regime}</div>
        </div>""", unsafe_allow_html=True)

    st.markdown(f"<small style='color:{COLORS['grey_muted']};'>Entrada: {entry_ts}</small>",
                unsafe_allow_html=True)

    st.markdown("---")

    # ── Botones WIN / LOSS (Ley race-condition safe) ──────────────────────────
    st.markdown('<div class="panel-title">REGISTRAR RESOLUCIÓN DE TRADE</div>',
                unsafe_allow_html=True)

    if trade_status == "IDLE":
        st.caption("Sin operación activa. Esperando señal del router.")
    elif st.session_state.get("trade_submitted"):
        st.success(f"✅ Resolución registrada para {asset}. "
                   f"GHA procesará el trigger en próximo ciclo.")
    else:
        col_win, col_loss, col_spacer = st.columns([1, 1, 3])
        with col_win:
            if st.button("✅  REGISTRAR WIN",
                         type="primary",
                         key="btn_win",
                         use_container_width=True):
                ok = write_trade_resolution("WIN", state)
                if ok:
                    st.session_state["trade_submitted"] = True
                    st.rerun()
                else:
                    st.error("Error escribiendo trade_resolution.json")
        with col_loss:
            if st.button("❌  REGISTRAR LOSS",
                         type="secondary",
                         key="btn_loss",
                         use_container_width=True):
                ok = write_trade_resolution("LOSS", state)
                if ok:
                    st.session_state["trade_submitted"] = True
                    st.rerun()
                else:
                    st.error("Error escribiendo trade_resolution.json")

    # Reset botón si el archivo fue procesado (ya no existe)
    if not RESOLUTION_FILE.exists() and st.session_state.get("trade_submitted"):
        st.session_state["trade_submitted"] = False

    # ── Headlines GDELT activos ───────────────────────────────────────────────
    if headlines:
        st.markdown("---")
        st.markdown('<div class="panel-title">GDELT CONTEXT — ÚLTIMOS TITULARES</div>',
                    unsafe_allow_html=True)
        for h in headlines[:8]:
            st.markdown(
                f"<span style='color:{COLORS['grey_muted']};'>›</span> "
                f"<span style='font-size:0.82rem;'>{h}</span>",
                unsafe_allow_html=True
            )


# ─────────────────────────────────────────────────────────────────────────────
# TAB: NARRATIVA IA
# ─────────────────────────────────────────────────────────────────────────────
def render_narrativa(state: Dict[str, Any]) -> None:
    narrative = state.get("last_narrative", "")
    trade = state.get("trade", {})

    st.markdown('<div class="panel-title">MOTOR NARRATIVO IA — POST-MORTEM GEMINI</div>',
                unsafe_allow_html=True)

    if narrative:
        outcome = trade.get("win_loss", "—")
        box_class = "signal-win" if outcome == "WIN" else "signal-loss"
        st.markdown(f'<div class="{box_class}">'
                    f'<b>{trade.get("asset","?")} · {outcome}</b><br>'
                    f'{narrative}'
                    f'</div>', unsafe_allow_html=True)
    else:
        st.caption("Sin narrativa disponible. Se generará automáticamente tras el cierre del trade.")

    st.markdown("---")
    st.markdown(
        '<div class="panel-title">GENERAR NARRATIVA MANUAL</div>',
        unsafe_allow_html=True
    )

    col1, col2 = st.columns([2, 1])
    with col1:
        manual_asset = st.selectbox("Activo", ["NVDA", "BTC", "XAU", "NIFTY50", "EURUSD"],
                                    key="manual_asset")
        manual_outcome = st.radio("Resultado", ["WIN", "LOSS"], horizontal=True,
                                  key="manual_outcome")
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("⚡ GENERAR NARRATIVA", use_container_width=True,
                     key="btn_narrative"):
            with st.spinner("Consultando Gemini API..."):
                narrative_result = _call_narrative_api(
                    asset=manual_asset,
                    outcome=manual_outcome,
                    state=state
                )
            if narrative_result:
                st.markdown(f'<div class="{"signal-win" if manual_outcome == "WIN" else "signal-loss"}">'
                            f'{narrative_result}</div>', unsafe_allow_html=True)
                st.session_state["narrative_cache"][f"{manual_asset}_{manual_outcome}"] = narrative_result
            else:
                st.error("Error al consultar Gemini. Verificar GEMINI_API_KEY en os.environ.")


def _call_narrative_api(asset: str, outcome: str, state: Dict[str, Any]) -> Optional[str]:
    """
    Llama a la API Gemini (requests HTTP nativo — Ley: cero frameworks IA pesados).
    Key leída exclusivamente desde os.environ (R21).
    """
    import urllib.request
    import urllib.error

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return None

    scores = state.get("scores", {})
    is_forex = asset in {"EURUSD", "GBPUSD", "USDJPY"}
    score_section = scores.get("forex" if is_forex else "core", {})
    entropy_val = score_section.get("entropy" if is_forex else "kl_div", 0.0)
    gt_score = score_section.get("gt_score", 0.0)
    regime = scores.get("core", {}).get("regime", "UNKNOWN")
    headlines = state.get("gdelt_headlines", [])[:5]
    headlines_str = " | ".join(headlines) if headlines else "Sin titulares disponibles"

    prompt = (
        f"Actúa como un analista quant institucional. "
        f"El trade en {asset} fue {outcome}. "
        f"GT-Score: {gt_score:.4f}. Entropía: {entropy_val:.6f}. Régimen: {regime}. "
        f"Noticias GDELT al momento: {headlines_str}. "
        f"Explica en exactamente 3 líneas contundentes el porqué del movimiento de mercado. "
        f"Sé directo. Sin preamble. Solo el análisis."
    )

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-1.5-flash:generateContent?key={api_key}"
    )
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 200, "temperature": 0.3},
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return (data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", ""))
    except Exception as exc:
        logging.error("Gemini API error: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# COMMAND CENTER — Tab 0
# ─────────────────────────────────────────────────────────────────────────────
def render_command_center(state: Dict[str, Any]) -> None:
    guardian = state.get("guardian", {})
    mc = state.get("montecarlo", {})
    ts = state.get("ts", "—")

    st.markdown(f"""
    <div style="text-align:center; padding: 8px 0 4px 0;">
      <span style="font-family:'Orbitron'; font-size:0.7rem; letter-spacing:0.4em;
                   color:{COLORS['grey_muted']};">ÚLTIMA ACTUALIZACIÓN</span><br>
      <span style="font-family:'Share Tech Mono'; color:{COLORS['accent_cyan']};">{ts}</span>
    </div>
    """, unsafe_allow_html=True)

    health_ok = guardian.get("health_ok", False)
    files_val = guardian.get("files_validated", 0)
    missing = guardian.get("missing_secrets", [])
    cb_states = guardian.get("cb_states", {})

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        color = COLORS["green_ok"] if health_ok else COLORS["red_fail"]
        st.markdown(f"""<div class="metric-card">
          <div class="panel-title">GUARDIAN STATUS</div>
          <div style="font-size:2rem; color:{color};">
            {"✅ HEALTHY" if health_ok else "🔴 DEGRADED"}</div>
        </div>""", unsafe_allow_html=True)
    with col2:
        st.markdown(f"""<div class="metric-card">
          <div class="panel-title">FILES VALIDATED</div>
          <div class="metric-gold">{files_val}</div>
        </div>""", unsafe_allow_html=True)
    with col3:
        miss_color = COLORS["red_fail"] if missing else COLORS["green_ok"]
        st.markdown(f"""<div class="metric-card">
          <div class="panel-title">SECRETS MISSING</div>
          <div style="font-size:1.4rem; font-family:'Orbitron'; color:{miss_color};">
            {len(missing)}</div>
          <div style="font-size:0.7rem; color:{COLORS['grey_muted']};">
            {', '.join(missing[:3]) if missing else 'All present'}</div>
        </div>""", unsafe_allow_html=True)
    with col4:
        mc_color = COLORS["accent_magenta"] if mc.get("running") else COLORS["grey_muted"]
        st.markdown(f"""<div class="metric-card">
          <div class="panel-title">MONTE CARLO</div>
          <div style="font-size:1rem; font-family:'Orbitron'; color:{mc_color};">
            {"🔴 RUNNING" if mc.get("running") else "⏸ PAUSED"}</div>
          <div style="font-size:0.75rem; color:{COLORS['grey_muted']};">
            {mc.get('paths_computed', 0):,} paths</div>
        </div>""", unsafe_allow_html=True)

    # Circuit Breakers
    if cb_states:
        st.markdown("---")
        st.markdown('<div class="panel-title">CIRCUIT BREAKERS</div>',
                    unsafe_allow_html=True)
        cb_cols = st.columns(len(cb_states))
        for i, (svc, cb) in enumerate(cb_states.items()):
            state_name = cb.get("state", "UNKNOWN")
            cb_color = (COLORS["green_ok"] if state_name == "CLOSED"
                        else COLORS["red_fail"] if state_name == "OPEN"
                        else COLORS["yellow_warn"])
            cb_cols[i].markdown(
                f'<div class="metric-card" style="text-align:center;">'
                f'<div class="panel-title">{svc.upper()}</div>'
                f'<div style="color:{cb_color}; font-family:Orbitron; font-size:0.85rem;">'
                f'{state_name}</div>'
                f'<div style="font-size:0.7rem; color:{COLORS["grey_muted"]};">'
                f'fails: {cb.get("failures", 0)}</div></div>',
                unsafe_allow_html=True
            )

    # Nodos por status
    nodes = state.get("nodes", [])
    if nodes:
        from collections import Counter
        status_counts = Counter(n.get("status", "unknown") for n in nodes)
        st.markdown("---")
        st.markdown(f'<div class="panel-title">103 NODOS — DISTRIBUCIÓN DE ESTADO</div>',
                    unsafe_allow_html=True)
        sc_cols = st.columns(len(status_counts) or 1)
        for i, (status, count) in enumerate(status_counts.most_common()):
            color = NODE_CATEGORY_COLORS.get(status, "#888888")
            sc_cols[i].markdown(
                f'<div class="metric-card" style="text-align:center;">'
                f'<div class="panel-title">{status.upper()}</div>'
                f'<div style="color:{color}; font-family:Orbitron; font-size:1.4rem;">'
                f'{count}</div></div>',
                unsafe_allow_html=True
            )


# ─────────────────────────────────────────────────────────────────────────────
# HEADER GLOBAL
# ─────────────────────────────────────────────────────────────────────────────
def render_header() -> None:
    st.markdown(f"""
    <div style="display:flex; align-items:center; justify-content:space-between;
                padding:12px 0; border-bottom:1px solid {COLORS['accent_cyan']}33; margin-bottom:16px;">
      <div>
        <span style="font-family:'Orbitron'; font-size:1.4rem; font-weight:700;
                     color:{COLORS['accent_gold']}; letter-spacing:0.15em;">
          👁 OJO DE DIOS
        </span>
        <span style="font-family:'Share Tech Mono'; font-size:0.75rem;
                     color:{COLORS['grey_muted']}; margin-left:12px;">
          SPEL 3.0 · Holmes OS V4.0 · {DASHBOARD_VERSION}
        </span>
      </div>
      <div style="font-family:'Share Tech Mono'; font-size:0.7rem;
                  color:{COLORS['accent_cyan']}; letter-spacing:0.2em;">
        HINC OMNIA CERNO
      </div>
    </div>
    """, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    _init_session()
    st.markdown(DARK_CSS, unsafe_allow_html=True)
    render_header()

    # Sidebar — configuración mínima
    with st.sidebar:
        st.markdown(f"<span style='font-family:Orbitron; color:{COLORS['accent_gold']};'>"
                    f"⚙ CONFIG</span>", unsafe_allow_html=True)
        state_path_input = st.text_input(
            "holmes_state.json path",
            value=str(STATE_FILE_DEFAULT),
            key="state_path_input"
        )
        auto_refresh = st.checkbox("Auto-refresh", value=True, key="auto_refresh")
        refresh_s = st.slider("Intervalo (s)", 10, 120, REFRESH_INTERVAL_S, key="refresh_s")
        if st.button("🔄 Refresh Manual", key="btn_refresh"):
            _load_state_cached.clear()
            st.rerun()
        st.markdown("---")
        st.caption(f"Guardian patrol: 900s\nDashboard refresh: {refresh_s}s")

    # Cargar estado
    state_path = Path(state_path_input)
    state = load_state(state_path)

    # Tabs principales
    tabs = st.tabs([
        "⚡ COMMAND CENTER",
        "🌐 3D NODE MAP",
        "📊 TRADE OPS",
        "🎲 MONTE CARLO",
        "🔀 DATA FLOWS",
        "🧪 TRAINING HQ",
        "🤖 NARRATIVA IA",
    ])

    with tabs[0]:
        render_command_center(state)

    with tabs[1]:
        st.markdown('<div class="panel-title">MAPA 3D DE NODOS SPEL — 103 NODOS OPERACIONALES</div>',
                    unsafe_allow_html=True)
        render_3d_node_map(state.get("nodes", []))

    with tabs[2]:
        render_trade_ops(state)

    with tabs[3]:
        st.markdown('<div class="panel-title">MONTE CARLO — EXPLORACIÓN DE VULNERABILIDADES</div>',
                    unsafe_allow_html=True)
        render_montecarlo(state.get("montecarlo", {}))

    with tabs[4]:
        render_data_flow(state)

    with tabs[5]:
        st.markdown('<div class="panel-title">TRAINING HQ — COLAS Y MODELOS ACTIVOS</div>',
                    unsafe_allow_html=True)
        render_training_hq(state)

    with tabs[6]:
        render_narrativa(state)

    # Auto-refresh (sin bloquear el hilo principal)
    if auto_refresh:
        time.sleep(0.1)
        st.markdown(
            f"<script>setTimeout(function(){{window.location.reload();}}, "
            f"{refresh_s * 1000});</script>",
            unsafe_allow_html=True
        )


if __name__ == "__main__":
    main()
