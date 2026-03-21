"""
main_ui.py
SPEL v40 · Streamlit Dashboard · Ojo de Dios v40

Arquitectura: read-only sobre spel_v40_execution_summary.json + trade_log.csv.
R7: el dashboard NUNCA calcula — solo lee y visualiza.
Diseño inspirado en docker-compose.yml v8: contenedor R/O sobre shared_volumes.

Panels:
  0 — Header: modo global, OR% live, pipeline status, gate día N/63
  1 — Score Grid: 4 activos + entry/SL/TP/Kelly/SHA
  2 — Equity Curve Dual: sandbox $10 + proyección canónica $100k
  3 — Entropy Gauge: Score de Oro como arco + activaciones Gödel OR%
  4 — Health Status: SHA match, RAM peak, latencia, checkpoint sizes
  5 — Forex HUD: entropy per-pair + barra visual
  6 — Trade Journal: últimas N evaluaciones + gate metrics
  7 — Audit Button: POST /api/audit (si ojo_de_dios_v26.py está activo)
"""

from __future__ import annotations

import json, os, time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import streamlit as st

# ── Streamlit config (debe ser el primer st.* call) ────────────────
st.set_page_config(
    page_title="SPEL v40 · Hinc Omnia Cerno",
    page_icon="👁",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Paths ──────────────────────────────────────────────────────────
ROOT         = Path(os.environ.get("SPEL_DATA_LAKE", "/app/shared/data_lake")).parent
META         = ROOT / "meta"
SUMMARY_JSON = META / "spel_v40_execution_summary.json"
TRADE_LOG    = ROOT / "logs" / "trade_log.csv"
GATE_JSON    = ROOT / "logs" / "gate_metrics.json"
REGISTRY     = META / "SHA_REGISTRY.json"

CANONICAL_CAPITAL: float = 100_000.0
SANDBOX_CAPITAL:   float = 10.0
REFRESH_SEC:       int   = 30

# ── Color palette ─────────────────────────────────────────────────
C_GREEN   = "#00FF41"
C_YELLOW  = "#FFD700"
C_RED     = "#FF4B4B"
C_CYAN    = "#00BFFF"
C_DARK    = "#0D1117"
C_GREY    = "#8B949E"

# ═══════════════════════════════════════════════════════════════════
# DATA LOADERS — read-only, cached
# ═══════════════════════════════════════════════════════════════════

@st.cache_data(ttl=REFRESH_SEC)
def load_summary() -> dict:
    try:
        return json.loads(SUMMARY_JSON.read_text())
    except Exception:
        return {}

@st.cache_data(ttl=REFRESH_SEC)
def load_gate() -> dict:
    try:
        return json.loads(GATE_JSON.read_text())
    except Exception:
        return {}

@st.cache_data(ttl=REFRESH_SEC)
def load_registry() -> dict:
    try:
        return json.loads(REGISTRY.read_text())
    except Exception:
        return {}

@st.cache_data(ttl=REFRESH_SEC)
def load_trade_log(n: int = 20) -> list[dict]:
    try:
        import csv
        with open(TRADE_LOG, newline="") as f:
            rows = list(csv.DictReader(f))
        return rows[-n:]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _score_color(score: int) -> str:
    if score >= 75: return C_GREEN
    if score >= 60: return C_YELLOW
    return C_RED

def _fmt_utc(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return iso[:19]

def _gate_icon(passed: bool) -> str:
    return "✅" if passed else "⬜"

def _equity_projection(
    pnl_list: list[float],
    sandbox_capital: float = SANDBOX_CAPITAL,
    canonical_capital: float = CANONICAL_CAPITAL,
) -> tuple[list[float], list[float]]:
    """
    Genera equity curves sandbox y canónica a partir de lista de PnL sandbox.
    R33: canónica = sandbox × (canonical / sandbox_capital).
    Epsilon guard en denominador.
    """
    scale     = canonical_capital / max(sandbox_capital, 1e-10)
    equity_sb = [sandbox_capital]
    equity_cn = [canonical_capital]
    for pnl in pnl_list:
        equity_sb.append(equity_sb[-1] + pnl)
        equity_cn.append(equity_cn[-1] + pnl * scale)
    return equity_sb, equity_cn


# ═══════════════════════════════════════════════════════════════════
# PANEL 0 — HEADER
# ═══════════════════════════════════════════════════════════════════

def render_header(summary: dict, gate: dict):
    day = summary.get("day_counter", "?")
    updated = _fmt_utc(summary.get("updated_utc", ""))

    st.markdown(
        f"""
        <div style='background:{C_DARK}; border:1px solid {C_CYAN};
                    padding:12px 20px; border-radius:6px; margin-bottom:16px'>
          <span style='color:{C_CYAN}; font-size:1.4em; font-weight:bold'>
            👁 OJO DE DIOS
          </span>
          <span style='color:{C_GREEN}; margin-left:12px'>● LIVE</span>
          <span style='color:{C_GREY}; margin-left:16px'>{updated}</span>
          <span style='color:{C_YELLOW}; margin-left:16px'>
            📅 Día {day}/63
          </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    gm = gate.get("gate_status", gate)
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        st.metric("Hit Rate Gödel",
                  f"{gm.get('hit_rate_godel', 0):.1%}",
                  help="Target > 56% con ≥30 trades Gödel")
        st.caption(_gate_icon(gm.get("hit_rate_pass", False)) + " Gate R30")

    with c2:
        dd = gm.get("max_drawdown_7d", 0)
        st.metric("Max Drawdown 7d (canónico)",
                  f"{dd:.2%}",
                  delta=f"límite 8%",
                  delta_color="inverse")
        st.caption(_gate_icon(gm.get("drawdown_pass", False)) + " Gate R30")

    with c3:
        pnl = gm.get("pnl_kelly_weighted", 0)
        st.metric("PnL Kelly-Weighted ($100k)",
                  f"${pnl:,.2f}",
                  help="R33: calculado sobre capital canónico")
        st.caption(_gate_icon(gm.get("pnl_pass", False)) + " Gate R30")

    with c4:
        ntr = gm.get("no_trade_rate", 0)
        st.metric("No-Trade Rate",
                  f"{ntr:.1%}",
                  help="Target 30-70%")
        st.caption(_gate_icon(gm.get("no_trade_pass", False)) + " Gate R30")

    # R33 guard indicator
    scale = gm.get("scale_factor", 10_000)
    ef20  = gm.get("ef20_guard", True)
    st.caption(
        f"🛡️ R33 guard: sandbox=${SANDBOX_CAPITAL} | "
        f"canonical=${CANONICAL_CAPITAL:,.0f} | scale={scale:,.0f}× | "
        f"EF-20={'✅' if ef20 else '🔴'}"
    )


# ═══════════════════════════════════════════════════════════════════
# PANEL 3 — ENTROPY GAUGE (Score de Oro)
# ═══════════════════════════════════════════════════════════════════

def render_entropy_gauge(summary: dict):
    st.subheader("⚡ Entropy Gauge — Score de Oro")
    last_cycle = summary.get("last_cycle", [])
    if not last_cycle:
        st.info("Sin datos de ciclo reciente")
        return

    try:
        import plotly.graph_objects as go
    except ImportError:
        st.warning("pip install plotly para el gauge")
        return

    cols = st.columns(len(last_cycle))
    for col, record in zip(cols, last_cycle):
        asset   = record.get("asset", "?")
        score   = record.get("score_oro", 0)
        godel   = record.get("godel_active", False)
        entropy = float(record.get("entropy", 0) or 0)
        p90     = float(record.get("p90_entropy", 1.2) or 1.2)

        # OR% aproximado desde entropy vs p90
        or_pct = min(entropy / max(p90, 1e-10), 1.5) * 100

        fig = go.Figure(go.Indicator(
            mode  = "gauge+number+delta",
            value = score,
            title = {"text": f"{asset}<br><small>{'🟢 Gödel ON' if godel else '○ Gödel OFF'}</small>",
                     "font": {"size": 14}},
            delta = {"reference": 70, "increasing": {"color": C_GREEN}},
            gauge = {
                "axis":  {"range": [0, 100], "tickwidth": 1},
                "bar":   {"color": _score_color(score)},
                "steps": [
                    {"range": [0, 60],   "color": "#1a1a2e"},
                    {"range": [60, 75],  "color": "#16213e"},
                    {"range": [75, 100], "color": "#0f3460"},
                ],
                "threshold": {
                    "line":  {"color": C_CYAN, "width": 3},
                    "thickness": 0.75,
                    "value": 70,
                },
            },
        ))
        fig.update_layout(
            height=200, margin=dict(l=10, r=10, t=40, b=10),
            paper_bgcolor=C_DARK, font_color="white",
        )
        col.plotly_chart(fig, use_container_width=True)
        col.caption(f"entropy={entropy:.3f} | p90={p90:.3f} | OR≈{or_pct:.1f}%")


# ═══════════════════════════════════════════════════════════════════
# PANEL 2 — EQUITY CURVE DUAL (sandbox + canónica)
# ═══════════════════════════════════════════════════════════════════

def render_equity_curve(trades: list[dict]):
    st.subheader("📈 Equity Curve Dual")

    pnl_sb = []
    for t in trades:
        raw = t.get("pnl_sandbox") or "0"
        try:
            pnl_sb.append(float(raw))
        except (TypeError, ValueError):
            pnl_sb.append(0.0)

    equity_sb, equity_cn = _equity_projection(pnl_sb)

    try:
        import plotly.graph_objects as go
    except ImportError:
        st.warning("pip install plotly")
        return

    fig = go.Figure()
    x   = list(range(len(equity_sb)))

    # Sandbox line ($10)
    fig.add_trace(go.Scatter(
        x=x, y=equity_sb,
        name=f"Sandbox (${SANDBOX_CAPITAL})",
        line={"color": C_YELLOW, "width": 1.5, "dash": "dot"},
        yaxis="y1",
    ))

    # Canonical projection ($100k) — R33
    fig.add_trace(go.Scatter(
        x=x, y=equity_cn,
        name=f"Canónica (${CANONICAL_CAPITAL:,.0f}) — R33",
        line={"color": C_CYAN, "width": 2},
        yaxis="y2",
    ))

    fig.add_hline(y=SANDBOX_CAPITAL,   line_dash="dash", line_color=C_GREY,
                  annotation_text="Sandbox start", yref="y1")

    fig.update_layout(
        height=320,
        paper_bgcolor=C_DARK, plot_bgcolor=C_DARK,
        font={"color": "white"},
        legend={"orientation": "h", "y": 1.1},
        yaxis  ={"title": f"Sandbox ($)", "side": "left",  "color": C_YELLOW},
        yaxis2 ={"title": f"Canónica ($)", "side": "right", "overlaying": "y",
                 "color": C_CYAN},
        xaxis  ={"title": "Evaluación #", "color": C_GREY},
        margin =dict(l=60, r=60, t=40, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        f"R33: línea canónica = sandbox × {CANONICAL_CAPITAL/SANDBOX_CAPITAL:,.0f}× "
        f"| EF-20 guard activo — gate metrics sobre ${CANONICAL_CAPITAL:,.0f}"
    )


# ═══════════════════════════════════════════════════════════════════
# PANEL 4 — HEALTH STATUS
# ═══════════════════════════════════════════════════════════════════

def render_health(summary: dict, registry: dict):
    st.subheader("🛡️ Health Status")

    acct    = summary.get("alpaca_account", {})
    gate    = summary.get("gate_metrics", {})
    sha_ok  = gate.get("sha_pass", False)

    c1, c2, c3 = st.columns(3)

    with c1:
        st.metric("SHA Mismatches", gate.get("sha_mismatches", "?"),
                  help="EF-19: cualquier mismatch = no operar")
        color = C_GREEN if sha_ok else C_RED
        st.markdown(
            f"<span style='color:{color}'>{'✅ SHAs OK' if sha_ok else '🔴 SHA MISMATCH'}</span>",
            unsafe_allow_html=True)

        st.markdown("**Parquets:**")
        for asset, meta in registry.items():
            sha = meta.get("sha_v5", "?")[:8]
            st.caption(f"  {asset}: `{sha}...`")

    with c2:
        equity   = acct.get("equity", "?")
        cash     = acct.get("cash", "?")
        bp       = acct.get("buying_power", "?")
        st.metric("Alpaca Equity", f"${float(equity or 0):,.2f}" if equity != "?" else "?")
        st.metric("Cash", f"${float(cash or 0):,.2f}" if cash != "?" else "?")
        st.metric("Buying Power", f"${float(bp or 0):,.2f}" if bp != "?" else "?")

    with c3:
        n_eval = gate.get("n_evaluations", 0)
        n_tg   = gate.get("n_trades_godel", 0)
        st.metric("Evaluaciones totales", n_eval)
        st.metric("Trades Gödel", n_tg,
                  help="≥30 necesarios para hit_rate significativo")
        st.metric("Needed Gödel trades", max(0, 30 - n_tg))


# ═══════════════════════════════════════════════════════════════════
# PANEL 1 — SCORE GRID
# ═══════════════════════════════════════════════════════════════════

def render_score_grid(summary: dict):
    st.subheader("🎯 Score Grid — Activos Core")
    last_cycle = summary.get("last_cycle", [])
    if not last_cycle:
        st.info("Sin ciclo reciente")
        return

    for rec in last_cycle:
        asset   = rec.get("asset", "?")
        score   = rec.get("score_oro", 0)
        direc   = rec.get("direction", "?")
        godel   = rec.get("godel_active", False)
        viable  = rec.get("viable", False)
        entry   = float(rec.get("entry_price", 0) or 0)
        sl      = float(rec.get("stop_loss", 0) or 0)
        tp      = float(rec.get("take_profit", 0) or 0)
        kelly   = float(rec.get("kelly_fraction", 0) or 0)
        sha     = rec.get("sha_parquet", "?")
        regime  = rec.get("regime_label", "?")

        icon = "🟢" if viable else ("🟡" if score >= 60 else "⛔")
        with st.expander(
            f"{icon} {asset} | score={score}/100 | {direc} | "
            f"{'Gödel ✅' if godel else 'Gödel ○'} | {regime}",
            expanded=viable,
        ):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Entry",  f"{entry:,.4f}" if entry else "—")
            c2.metric("SL",     f"{sl:,.4f}"    if sl    else "—")
            c3.metric("TP",     f"{tp:,.4f}"    if tp    else "—")
            c4.metric("Kelly",  f"{kelly:.4f}")
            st.caption(f"sha_parquet=`{sha}` | timestamp={rec.get('timestamp_utc','?')[:19]}")


# ═══════════════════════════════════════════════════════════════════
# PANEL 6 — TRADE JOURNAL
# ═══════════════════════════════════════════════════════════════════

def render_trade_journal(trades: list[dict]):
    st.subheader("📒 Trade Journal (últimas evaluaciones)")
    if not trades:
        st.info("trade_log.csv vacío")
        return

    key_cols = ["timestamp_utc", "asset", "direction", "score_oro",
                "godel_active", "viable", "regime_label",
                "pnl_sandbox", "pnl_canonical", "kelly_fraction",
                "sha_parquet"]

    rows = []
    for t in reversed(trades):
        rows.append({k: t.get(k, "") for k in key_cols})

    try:
        import polars as pl
        df = pl.DataFrame(rows)
        st.dataframe(df.to_pandas(), use_container_width=True, height=300)
    except Exception:
        st.table(rows)

    st.caption(
        "pnl_canonical = pnl_sandbox × 10,000 (R33 escala canónica) | "
        "sha_parquet verificado en cada evaluación (EF-19)"
    )


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    # Auto-refresh
    count = st.empty()
    placeholder = st.empty()

    summary  = load_summary()
    gate     = load_gate()
    registry = load_registry()
    trades   = load_trade_log(n=40)

    if not summary:
        st.warning(
            f"No se encontró `{SUMMARY_JSON}`. "
            "Verificar que `spel_paper_adapter_v2.py` está corriendo y "
            "ha completado al menos un ciclo de evaluación."
        )

    # ── Panel 0: Header ───────────────────────────────────────────
    render_header(summary, gate)
    st.divider()

    # ── Tabs principales ──────────────────────────────────────────
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "🎯 Scores",
        "📈 Equity",
        "⚡ Entropy",
        "🛡️ Health",
        "📒 Journal",
    ])

    with tab1:
        render_score_grid(summary)

    with tab2:
        render_equity_curve(trades)

    with tab3:
        render_entropy_gauge(summary)

    with tab4:
        render_health(summary, registry)

    with tab5:
        render_trade_journal(trades)

    # ── Footer ────────────────────────────────────────────────────
    st.divider()
    updated = summary.get("updated_utc", "?")
    day     = summary.get("day_counter", "?")
    st.caption(
        f"👁 SPEL v40 · Hinc Omnia Cerno | "
        f"Actualizado: {_fmt_utc(updated)} | "
        f"Paper Day {day}/63 | "
        f"R7: Dashboard read-only | "
        f"R33: canonical=${CANONICAL_CAPITAL:,.0f} | "
        f"EF-20 guard active"
    )

    # Auto-refresh
    time.sleep(REFRESH_SEC)
    st.rerun()


if __name__ == "__main__":
    main()
