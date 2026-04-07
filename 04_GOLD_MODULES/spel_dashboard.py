"""
spel_dashboard.py — SPEL 3.0 · Institutional Command Dashboard v4.7
Protocol: SPEL_Sovereign_Architecture v4.7 · S47 · Hinc Omnia Cerno

Deployment: Streamlit Cloud / Docker container
RAM ceiling: 2.3–3.0 GB (Polars Float32 lazy scan + explicit GC)

Design: High-Contrast Cyberpunk — dark ground, monospace, geodesic grid
Prohibited: Moving averages, RSI, MACD, any retail indicator
Rendered:   GT-Score · Shannon Entropy (P90) · KL Divergence · 4 TG nodes

Zero-Trust reads: every JSON load wrapped in try/except with fallback struct
Race condition: reads only, never writes. Dashboard is pure consumer.
Refresh: st.rerun() loop at configurable TTL (default 30s)
GC: explicit gc.collect() after every data reload cycle
"""

import gc
import json
import os
import time
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import streamlit as st

# ── Page config (must be first Streamlit call) ─────────────────────────────────
st.set_page_config(
    page_title="SPEL · Hinc Omnia Cerno",
    page_icon="⬡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── CSS: High-Contrast Cyberpunk Sovereign Theme ───────────────────────────────
_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;600;700&family=Share+Tech+Mono&display=swap');

:root {
    --bg:      #040608;
    --bg2:     #080c10;
    --bg3:     #0c1016;
    --border:  #142030;
    --border2: #1e3050;
    --text:    #b8ccd8;
    --dim:     #4a6070;
    --accent:  #00e87a;   /* sovereign green */
    --gold:    #f0c040;   /* GT-Score gold   */
    --chaos:   #e83050;   /* CHAOS red       */
    --kl:      #40a0e0;   /* KL blue         */
    --warn:    #e07820;   /* warning orange  */
}

html, body, .stApp { background: var(--bg) !important; }
* { font-family: 'JetBrains Mono', 'Share Tech Mono', monospace !important; }

/* remove Streamlit chrome */
#MainMenu, footer, header, .stDeployButton { display: none !important; }
.block-container { padding: 1rem 1.5rem !important; max-width: 100% !important; }

h1 { color: var(--accent) !important; font-size: 1.1rem !important;
     letter-spacing: 3px; text-transform: uppercase; font-weight: 700;
     border-bottom: 1px solid var(--border2); padding-bottom: 0.4rem; margin-bottom: 1rem; }
h2 { color: var(--dim) !important; font-size: 0.7rem !important;
     letter-spacing: 2px; text-transform: uppercase; margin: 0.6rem 0 0.3rem; }
h3 { color: var(--text) !important; font-size: 0.85rem !important; letter-spacing: 1px; }

/* metric overrides */
[data-testid="metric-container"] {
    background: var(--bg2) !important;
    border: 1px solid var(--border) !important;
    border-radius: 4px !important;
    padding: 0.6rem 0.8rem !important;
}
[data-testid="metric-container"] label {
    color: var(--dim) !important; font-size: 0.62rem !important;
    letter-spacing: 1.5px; text-transform: uppercase;
}
[data-testid="metric-container"] [data-testid="stMetricValue"] {
    color: var(--text) !important; font-size: 1.4rem !important; font-weight: 700;
}
[data-testid="metric-container"] [data-testid="stMetricDelta"] {
    font-size: 0.7rem !important;
}

/* panel cards */
.spel-card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0.8rem 1rem;
    margin-bottom: 0.6rem;
}
.spel-card.accent  { border-left: 3px solid var(--accent); }
.spel-card.gold    { border-left: 3px solid var(--gold);   }
.spel-card.chaos   { border-left: 3px solid var(--chaos);  }
.spel-card.kl      { border-left: 3px solid var(--kl);     }
.spel-card.warn    { border-left: 3px solid var(--warn);   }

.tag {
    display: inline-block;
    font-size: 0.6rem; font-weight: 700;
    padding: 1px 6px; border-radius: 2px;
    letter-spacing: 1px; text-transform: uppercase;
    margin-right: 4px;
}
.tag-green  { background: rgba(0,232,122,0.12); color: var(--accent); }
.tag-gold   { background: rgba(240,192,64,0.12); color: var(--gold);  }
.tag-red    { background: rgba(232,48,80,0.12);  color: var(--chaos); }
.tag-blue   { background: rgba(64,160,224,0.12); color: var(--kl);    }
.tag-dim    { background: rgba(74,96,112,0.15);  color: var(--dim);   }
.tag-warn   { background: rgba(224,120,32,0.15); color: var(--warn);  }

.score-display {
    font-size: 3.5rem; font-weight: 700; letter-spacing: -1px;
    line-height: 1; margin: 0.3rem 0;
}
.score-display.green { color: var(--accent); }
.score-display.gold  { color: var(--gold);   }
.score-display.red   { color: var(--chaos);  }

.node-row {
    display: flex; align-items: center; gap: 10px;
    padding: 6px 0; border-bottom: 1px solid var(--border);
}
.node-dot {
    width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
}
.dot-green { background: var(--accent); box-shadow: 0 0 6px var(--accent); }
.dot-red   { background: var(--chaos);  box-shadow: 0 0 6px var(--chaos);  }
.dot-gold  { background: var(--gold);   box-shadow: 0 0 6px var(--gold);   }
.dot-dim   { background: var(--dim);    }

.bar-track {
    height: 6px; background: var(--border2); border-radius: 3px;
    width: 100%; margin-top: 4px;
}
.bar-fill {
    height: 6px; border-radius: 3px; transition: width 0.3s ease;
}
.bar-green { background: var(--accent); }
.bar-gold  { background: var(--gold);   }
.bar-red   { background: var(--chaos);  }
.bar-blue  { background: var(--kl);     }

.mono-sm { font-size: 0.7rem; color: var(--dim); letter-spacing: 0.5px; }
.mono-xs { font-size: 0.62rem; color: var(--dim); letter-spacing: 0.5px; }

textarea { background: var(--bg3) !important; color: var(--text) !important;
           border: 1px solid var(--border2) !important; border-radius: 4px !important;
           font-size: 0.75rem !important; }
button[kind="primary"] {
    background: var(--accent) !important; color: #000 !important;
    font-weight: 700 !important; border-radius: 3px !important;
    border: none !important; letter-spacing: 1px;
}
.stProgress > div > div { background: var(--accent) !important; }
</style>
"""
st.markdown(_CSS, unsafe_allow_html=True)


# ── ROOT detection (3-way, mirrors orchestrator) ───────────────────────────────
def _detect_root() -> Path:
    _IS_GH = os.environ.get("GITHUB_ACTIONS") == "true"
    if _IS_GH:
        return Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve()
    _env = os.environ.get("SPEL_BASE_DIR", "")
    if _env:
        return Path(_env).resolve()
    _p = Path("/content/drive/MyDrive/ORDEN/SPEL 3.0")
    return _p if _p.exists() else Path(".").resolve()

ROOT  = _detect_root()
VAULT = ROOT / "00_VAULT"


# ── Zero-Trust JSON loader ─────────────────────────────────────────────────────
def _jload(path: Path, default=None):
    """Load JSON with fallback. Never raises. Cached at Streamlit TTL layer."""
    if default is None:
        default = {}
    try:
        return json.loads(path.read_text()) if path.exists() else default
    except Exception:
        return default


# ── Cached data loaders (TTL-based to cap re-reads) ───────────────────────────
@st.cache_data(ttl=30, show_spinner=False)
def _load_all_vault(vault_str: str) -> dict:
    """
    Single-pass vault read. All JSONs in one cached call.
    TTL=30s → max 2 disk reads/min. Prevents I/O hammering on refresh.
    Returns flat dict of all dashboard-relevant data.
    """
    vault = Path(vault_str)
    data  = {
        "bma":     _jload(vault / "live_bma_result.json"),
        "dash":    _jload(vault / "live_dashboard_stats.json"),
        "gate":    _jload(vault / "gate_metrics.json"),
        "pulse":   _jload(vault / "system_pulse.json"),
        "history": _jload(vault / "live_bma_history.json", default=[]),
        "forex":   _jload(vault / "forex_bridge_state.json"),
        "signal":  _jload(vault / "last_signal.json"),
        "graph":   _jload(vault / "live_graph_data.json"),
    }
    return data


@st.cache_data(ttl=120, show_spinner=False)
def _load_sha_registry(vault_str: str) -> dict:
    return _jload(Path(vault_str) / "registry" / "SHA_REGISTRY.json")


# ── RAM guardian (2.3–3.0 GB ceiling) ─────────────────────────────────────────
def _ram_mb() -> float:
    try:
        import psutil
        return psutil.virtual_memory().available / (1024 ** 2)
    except ImportError:
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if "MemAvailable" in line:
                        return int(line.split()[1]) / 1024
        except Exception:
            pass
    return 9999.0


def _ram_check_and_gc(threshold_mb: float = 800.0) -> float:
    """GC sweep if available RAM below threshold. Returns available MB."""
    avail = _ram_mb()
    if avail < threshold_mb:
        gc.collect()
        _load_all_vault.clear()    # invalidate cache to free strings
    return avail


# ── Utility renderers ──────────────────────────────────────────────────────────
def _ts_age(ts_str: str) -> str:
    """Human-readable age from ISO timestamp."""
    if not ts_str:
        return "–"
    try:
        dt  = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        if age < 60:    return f"{int(age)}s ago"
        if age < 3600:  return f"{int(age/60)}m ago"
        return f"{int(age/3600)}h ago"
    except Exception:
        return "–"


def _score_color(v: float, lo: float = 0.62, hi: float = 0.80) -> str:
    if v >= hi:  return "green"
    if v >= lo:  return "gold"
    return "red"


def _bar(value: float, max_val: float = 1.0, color: str = "green") -> str:
    pct = min(100, int(value / max_val * 100))
    return (f'<div class="bar-track"><div class="bar-fill bar-{color}" '
            f'style="width:{pct}%"></div></div>')


def _tag(label: str, color: str = "dim") -> str:
    return f'<span class="tag tag-{color}">{label}</span>'


def _dot(color: str = "green") -> str:
    return f'<div class="node-dot dot-{color}"></div>'


# ── Panel: GT-Score sovereign display ─────────────────────────────────────────
def _render_gt_score(data: dict) -> None:
    dash   = data.get("dash", {})
    signal = data.get("signal", {})
    bma    = data.get("bma", {})
    gate   = data.get("gate", {})

    gold   = float(dash.get("gold_score_bma",
                   signal.get("gold_score", 0.0)))
    certeza = round(gold * 10, 2)
    action  = dash.get("action", signal.get("action", "HOLD"))
    regime  = dash.get("regime", signal.get("regime", "UNKNOWN"))
    asset   = signal.get("asset", "–")
    gt      = gate.get("gt_score")
    gt_str  = f"{gt:.4f}" if isinstance(gt, (int, float)) else "–"

    color = _score_color(gold)
    action_color = {"LONG": "green", "SHORT": "red", "HOLD": "gold"}.get(action, "dim")

    st.markdown(f"""
<div class="spel-card gold">
  <h2>GOLD TICKET SCORE · BMA</h2>
  <div class="score-display {color}">{gold:.4f}</div>
  <div style="margin-top:0.2rem">
    {_tag(action, action_color)}
    {_tag(regime, 'blue')}
    {_tag(asset, 'dim')}
  </div>
  {_bar(gold, color=color)}
  <div class="mono-sm" style="margin-top:0.5rem">
    Certeza: {certeza}/10 &nbsp;|&nbsp; GT-Score Gate R30: {gt_str}
  </div>
</div>
""", unsafe_allow_html=True)


# ── Panel: Entropy + KL ───────────────────────────────────────────────────────
def _render_entropy(data: dict) -> None:
    dash    = data.get("dash", {})
    history = data.get("history", [])
    bma_sum = data.get("bma", {}).get("global", {})

    shannon  = float(dash.get("shannon_entropy",
                     bma_sum.get("shannon_entropy", 0.0)))
    kl       = float(dash.get("kl_rolling",
               dash.get("kl_divergence",
               bma_sum.get("kl_rolling",
               bma_sum.get("kl_divergence", 0.0)))))
    lam      = float(dash.get("lambda_decay",
                     bma_sum.get("lambda_decay", 0.0)))
    vt       = int(dash.get("vitality_tesla",
                  data.get("signal", {}).get("vitality_tesla", 0)))

    P90_SHANNON = 0.35   # scalping threshold (S47)
    KL_CEIL     = 0.20

    shan_color = "red" if shannon > P90_SHANNON else ("gold" if shannon > 0.25 else "green")
    kl_color   = "red" if kl > KL_CEIL else ("gold" if kl > 0.12 else "green")
    lam_color  = "red" if lam < 0.45 else ("gold" if lam < 0.70 else "green")
    vt_label   = {3: "CREACIÓN", 6: "ESTRUCTURA/NASH", 9: "TRASCENDENCIA"}.get(vt, "–")
    vt_color   = {3: "green",    6: "gold",            9: "red"}.get(vt, "dim")

    # KL rolling sparkline (last 10 records)
    kl_hist = [round(h.get("kl", 0.0), 4) for h in history[-10:]] if history else []

    st.markdown(f"""
<div class="spel-card kl">
  <h2>SHANNON ENTROPY · KL DIVERGENCE · VITALITY TESLA</h2>
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:0.5rem;margin-bottom:0.5rem">
    <div>
      <div class="mono-xs">SHANNON P90={P90_SHANNON}</div>
      <div style="font-size:1.8rem;font-weight:700;color:var(--{'accent' if shan_color=='green' else 'chaos' if shan_color=='red' else 'gold'})">{shannon:.4f}</div>
      {_bar(shannon, 0.5, shan_color)}
    </div>
    <div>
      <div class="mono-xs">KL DRIFT CEILING={KL_CEIL}</div>
      <div style="font-size:1.8rem;font-weight:700;color:var(--{'accent' if kl_color=='green' else 'chaos' if kl_color=='red' else 'gold'})">{kl:.4f}</div>
      {_bar(kl, 0.3, kl_color)}
    </div>
    <div>
      <div class="mono-xs">LAMBDA DECAY</div>
      <div style="font-size:1.8rem;font-weight:700;color:var(--{'accent' if lam_color=='green' else 'chaos' if lam_color=='red' else 'gold'})">{lam:.4f}</div>
      {_bar(lam, 1.0, lam_color)}
    </div>
  </div>
  <div>
    {_tag(f'VITALITY {vt}', vt_color)}
    {_tag(vt_label, vt_color)}
  </div>
  <div class="mono-xs" style="margin-top:0.5rem">
    KL rolling (last {len(kl_hist)}): {" · ".join(str(v) for v in kl_hist) or "–"}
  </div>
</div>
""", unsafe_allow_html=True)


# ── Panel: Gate R30 ───────────────────────────────────────────────────────────
def _render_gate(data: dict) -> None:
    gate   = data.get("gate", {})
    day    = int(gate.get("day", 0))
    target = int(gate.get("target_days", 63))
    cap    = float(gate.get("capital_base", 100000))
    gt     = gate.get("gt_score")
    verdict= gate.get("verdict", "IN_PROGRESS")
    trades = gate.get("trades", [])
    pct    = day / target if target > 0 else 0

    gt_str = f"{gt:.4f}" if isinstance(gt, (int, float)) else "COMPUTING"
    v_color = ("green" if verdict == "PASS" else
               "red"   if verdict == "NO_GO" else "gold")

    n_win  = sum(1 for t in trades if float(t.get("pnl", 0)) > 0) if trades else 0
    n_loss = len(trades) - n_win if trades else 0

    st.markdown(f"""
<div class="spel-card accent">
  <h2>GATE R30 · PAPER TRADING PERFORMANCE</h2>
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:0.5rem">
    <div><div class="mono-xs">DAY</div>
         <div style="font-size:1.6rem;font-weight:700;color:var(--accent)">{day}/{target}</div></div>
    <div><div class="mono-xs">GT-SCORE</div>
         <div style="font-size:1.6rem;font-weight:700;color:var(--gold)">{gt_str}</div></div>
    <div><div class="mono-xs">CAPITAL BASE</div>
         <div style="font-size:1.6rem;font-weight:700;color:var(--text)">${cap:,.0f}</div></div>
    <div><div class="mono-xs">VERDICT</div>
         <div style="font-size:1.0rem;font-weight:700;margin-top:0.3rem">
           {_tag(verdict, v_color)}</div></div>
  </div>
  {_bar(pct, 1.0, 'accent')}
  <div class="mono-xs" style="margin-top:0.4rem">
    Trades: {len(trades)} &nbsp;|&nbsp;
    Win: {n_win} &nbsp;|&nbsp; Loss: {n_loss} &nbsp;|&nbsp;
    Threshold: GT > 0.15 → GO LIVE
  </div>
</div>
""", unsafe_allow_html=True)


# ── Panel: 4 TG Network Nodes ─────────────────────────────────────────────────
def _render_tg_nodes(data: dict) -> None:
    pulse  = data.get("pulse", {})
    graph  = data.get("graph", {})
    signal = data.get("signal", {})
    forex  = data.get("forex", {})
    bma    = data.get("bma", {})

    last_pulse = pulse.get("last_pulse_utc", "")
    decision   = pulse.get("last_decision", "–")
    health     = pulse.get("system_health", "–")
    cycle_n    = pulse.get("cycle_number", 0)

    n_nodes    = graph.get("total_nodes", 0)
    n_edges    = graph.get("total_edges", 0)
    g_status   = graph.get("status", "–")
    chaos_armed= graph.get("telegram_chaos_armed", False)

    sig_action  = signal.get("action", "–")
    sig_asset   = signal.get("asset", "–")
    sig_ts      = signal.get("generated_at", signal.get("timestamp", ""))
    sig_score   = signal.get("gold_score", 0.0)

    forex_status= forex.get("status", "–")
    forex_ts    = forex.get("ts", "")
    forex_exec  = forex.get("EXECUTION_STATUS", "–")
    shield_ok   = not forex.get("shield", {}).get("blocked", True)

    def _node_color(ok: bool) -> str:
        return "green" if ok else "red"

    st.markdown("<h2>TELEMETRY · NETWORK NODES</h2>", unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        pulse_ok = bool(last_pulse) and health == "SOVEREIGN_CLEAN"
        st.markdown(f"""
<div class="spel-card {'accent' if pulse_ok else 'chaos'}">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
    {_dot(_node_color(pulse_ok))}
    <span style="color:var(--dim);font-size:0.65rem;letter-spacing:1px">SISTEMA</span>
  </div>
  <div class="mono-sm">{decision}</div>
  <div class="mono-xs" style="margin-top:4px">Cycle #{cycle_n}</div>
  <div class="mono-xs">{_ts_age(last_pulse)}</div>
  <div style="margin-top:6px">{_tag(health, 'green' if pulse_ok else 'red')}</div>
</div>""", unsafe_allow_html=True)

    with c2:
        colab_ok = bool(bma) and bool(bma.get("generated_at"))
        bma_ts   = bma.get("generated_at", "")
        n_assets = len(bma.get("bma_by_asset", {}))
        st.markdown(f"""
<div class="spel-card {'accent' if colab_ok else 'warn'}">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
    {_dot(_node_color(colab_ok))}
    <span style="color:var(--dim);font-size:0.65rem;letter-spacing:1px">BACKUP · COLAB</span>
  </div>
  <div class="mono-sm">BMA {n_assets} assets</div>
  <div class="mono-xs" style="margin-top:4px">{_ts_age(bma_ts)}</div>
  <div style="margin-top:6px">
    {_tag('LIVE' if colab_ok else 'STALE', 'green' if colab_ok else 'warn')}
  </div>
</div>""", unsafe_allow_html=True)

    with c3:
        sig_ok   = bool(sig_action) and sig_action != "HOLD"
        sig_color= {"LONG": "green", "SHORT": "red", "HOLD": "gold"}.get(sig_action, "dim")
        st.markdown(f"""
<div class="spel-card {'accent' if sig_ok else 'gold'}">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
    {_dot(_node_color(sig_ok))}
    <span style="color:var(--dim);font-size:0.65rem;letter-spacing:1px">SEÑALES · SCALPING</span>
  </div>
  <div style="font-size:1.3rem;font-weight:700;color:var(--{'accent' if sig_color=='green' else 'chaos' if sig_color=='red' else 'gold'})">{sig_action}</div>
  <div class="mono-xs">{sig_asset} · {float(sig_score):.4f}</div>
  <div class="mono-xs">{_ts_age(sig_ts)}</div>
  <div style="margin-top:6px">{_tag('ACTIVE' if sig_ok else 'HOLD', sig_color)}</div>
</div>""", unsafe_allow_html=True)

    with c4:
        chaos_ok = chaos_armed
        st.markdown(f"""
<div class="spel-card {'chaos' if not shield_ok else 'accent'}">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
    {_dot('green' if chaos_ok else 'red')}
    <span style="color:var(--dim);font-size:0.65rem;letter-spacing:1px">CHAOS · DIAGNOSTICS</span>
  </div>
  <div class="mono-sm">{forex_status}</div>
  <div class="mono-xs">EXEC: {forex_exec}</div>
  <div class="mono-xs">{_ts_age(forex_ts)}</div>
  <div style="margin-top:6px">
    {_tag('SHIELD CLEAR' if shield_ok else 'SHIELD BLOCK',
          'green' if shield_ok else 'red')}
    {_tag('CHAOS ARMED' if chaos_armed else 'CHAOS OFFLINE',
          'green' if chaos_armed else 'red')}
  </div>
</div>""", unsafe_allow_html=True)


# ── Panel: Forex Resilience Shield ────────────────────────────────────────────
def _render_shield(data: dict) -> None:
    forex  = data.get("forex", {})
    shield = forex.get("shield", {})
    if not shield:
        return
    details  = shield.get("details", {})
    checks   = shield.get("checks",  {})
    blocked  = shield.get("blocked", False)
    reason   = shield.get("reason", "")

    thresholds = details.get("thresholds", {})
    vals = [
        ("SHANNON",  details.get("shannon_entropy", 0),  thresholds.get("entropy_max", 0.35), "≤"),
        ("KL DRIFT", details.get("kl_divergence",   0),  thresholds.get("kl_max",      0.20), "≤"),
        ("LAMBDA",   details.get("lambda_decay",    0),  thresholds.get("lambda_min",  0.45), "≥"),
        ("GOLD EUR", details.get("gold_score_eurusd",0), thresholds.get("gold_min",    0.58), "≥"),
    ]

    rows = ""
    for name, val, thr, op in vals:
        ok    = (val <= thr if op == "≤" else val >= thr)
        color = "green" if ok else "red"
        rows += (f'<div class="node-row">'
                 f'{_dot(color)}'
                 f'<span class="mono-sm" style="flex:1">{name}</span>'
                 f'<span class="mono-sm">{float(val):.4f}</span>'
                 f'<span class="mono-xs" style="margin-left:8px">{op} {thr}</span>'
                 f'{_tag("OK" if ok else "BLOCK", color)}'
                 f'</div>')

    st.markdown(f"""
<div class="spel-card {'chaos' if blocked else 'accent'}">
  <h2>RESILIENCE SHIELD · EURUSD v4.7</h2>
  <div style="margin:4px 0">
    {_tag('BLOCKED' if blocked else 'CLEAR', 'red' if blocked else 'green')}
    <span class="mono-xs" style="margin-left:6px">{reason or 'Entry permitted'}</span>
  </div>
  {rows}
</div>""", unsafe_allow_html=True)


# ── Panel: System graph health ─────────────────────────────────────────────────
def _render_graph_health(data: dict) -> None:
    graph = data.get("graph", {})
    if not graph:
        return
    summary = graph.get("summary", {})
    diamond = summary.get("diamond", 0)
    gold    = summary.get("gold",    0)
    ghost   = summary.get("ghost",   0)
    chaos_t = summary.get("chaos_triggers", 0)
    n_edges = graph.get("total_edges", 0)
    cycles  = graph.get("cycles_detected", 0)
    orphans = len(graph.get("orphan_modules", []))
    g_ts    = graph.get("generated_at", "")

    cycle_color = "red" if cycles > 0 else "green"
    ghost_color = "red" if ghost > 0 else "green"

    st.markdown(f"""
<div class="spel-card accent">
  <h2>LIVE GRAPH · MODULE HEALTH</h2>
  <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:0.4rem">
    <div><div class="mono-xs">DIAMOND</div>
         <div style="font-size:1.4rem;font-weight:700;color:var(--kl)">{diamond}</div></div>
    <div><div class="mono-xs">GOLD</div>
         <div style="font-size:1.4rem;font-weight:700;color:var(--gold)">{gold}</div></div>
    <div><div class="mono-xs">EDGES</div>
         <div style="font-size:1.4rem;font-weight:700;color:var(--accent)">{n_edges}</div></div>
    <div><div class="mono-xs">GHOST</div>
         <div style="font-size:1.4rem;font-weight:700;color:var(--{'chaos' if ghost>0 else 'accent'})">{ghost}</div></div>
    <div><div class="mono-xs">CYCLES</div>
         <div style="font-size:1.4rem;font-weight:700;color:var(--{'chaos' if cycles>0 else 'accent'})">{cycles}</div></div>
    <div><div class="mono-xs">ORPHANS</div>
         <div style="font-size:1.4rem;font-weight:700;color:var(--warn)">{orphans}</div></div>
  </div>
  <div class="mono-xs" style="margin-top:0.4rem">
    {_tag('CYCLE-FREE' if cycles==0 else f'{cycles} CYCLES', cycle_color)}
    {_tag('NO GHOSTS' if ghost==0 else f'{ghost} GHOSTS', ghost_color)}
    &nbsp;Updated: {_ts_age(g_ts)}
  </div>
</div>""", unsafe_allow_html=True)


# ── Panel: Cognitive Terminal ─────────────────────────────────────────────────
def _render_cognitive_terminal(vault: Path) -> None:
    st.markdown("<h2>COGNITIVE TERMINAL · GEOPOLITICAL INTELLIGENCE INPUT</h2>",
                unsafe_allow_html=True)
    st.markdown(
        '<div class="mono-xs" style="margin-bottom:0.4rem">'
        'Paste AI output (Claude / Gemini / DeepSeek) for Holmes context injection. '
        'Saved to Drive_Cache_Caliente.</div>',
        unsafe_allow_html=True,
    )
    with st.form("cognitive_terminal", clear_on_submit=False):
        c1, c2 = st.columns([3, 1])
        with c1:
            label = st.selectbox("Context tag",
                                  ["GOV", "CORP", "BUS", "RUMOR", "BLOOMBERG"],
                                  label_visibility="collapsed")
        with c2:
            asset_tag = st.selectbox("Asset",
                                      ["NVDA", "BTC", "XAU", "NIFTY50", "EURUSD", "GLOBAL"],
                                      label_visibility="collapsed")
        analysis_input = st.text_area(
            "AI Analysis",
            placeholder="Paste institutional analysis here...",
            height=90,
            label_visibility="collapsed",
        )
        submitted = st.form_submit_button("INJECT TO HOLMES", type="primary")

        if submitted and analysis_input.strip():
            record = {
                "ts":       datetime.now(timezone.utc).isoformat(),
                "tag":      label,
                "asset":    asset_tag,
                "analysis": analysis_input.strip()[:8000],
                "sha":      hashlib.sha256(analysis_input.encode()).hexdigest()[:16],
            }
            _cognitive_path = vault / "holmes_cognitive_log.json"
            existing: list = []
            try:
                existing = json.loads(_cognitive_path.read_text())
            except Exception:
                pass
            existing.append(record)
            if len(existing) > 500:
                existing = existing[-500:]
            try:
                _tmp = _cognitive_path.with_suffix(".tmp")
                _tmp.write_bytes(json.dumps(existing, indent=2).encode())
                _tmp.replace(_cognitive_path)
                st.success(f"Injected [{label}/{asset_tag}] · SHA {record['sha']}")
            except Exception as e:
                st.error(f"Write failed: {e}")


# ── Sidebar: system meta + refresh ────────────────────────────────────────────
def _render_sidebar(avail_mb: float, data: dict) -> int:
    sha_reg = _load_sha_registry(str(VAULT))
    with st.sidebar:
        st.markdown("## ⬡ SPEL 3.0")
        st.markdown('<div class="mono-xs">Holmes OS V4.0 · Gate R30</div>',
                    unsafe_allow_html=True)
        st.divider()
        ttl = st.slider("Refresh interval (s)", 15, 120, 30, 5)
        if st.button("FORCE REFRESH", type="primary"):
            _load_all_vault.clear()
            st.rerun()
        st.divider()
        st.markdown(f'<div class="mono-xs">RAM available: {avail_mb:.0f} MB</div>',
                    unsafe_allow_html=True)
        gc_flag = avail_mb < 800
        if gc_flag:
            st.warning(f"LOW RAM: {avail_mb:.0f}MB — GC triggered")
        st.divider()
        st.markdown('<div class="mono-xs">SHA_REGISTRY</div>', unsafe_allow_html=True)
        n_sha = len(sha_reg) - (1 if "_meta" in sha_reg else 0)
        st.markdown(f'<div class="mono-xs">{n_sha} entries</div>',
                    unsafe_allow_html=True)
        meta = sha_reg.get("_meta", {})
        st.markdown(f'<div class="mono-xs">{meta.get("version","–")}</div>',
                    unsafe_allow_html=True)
        st.divider()
        st.markdown('<div class="mono-xs">ROOT</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="mono-xs" style="word-break:break-all">{ROOT}</div>',
                    unsafe_allow_html=True)
    return ttl


# ── Main render loop ───────────────────────────────────────────────────────────
def main():
    # RAM check + GC before any data load
    avail_mb = _ram_check_and_gc(threshold_mb=800.0)

    # Single-pass vault read (cached)
    data = _load_all_vault(str(VAULT))

    # Sidebar
    refresh_ttl = _render_sidebar(avail_mb, data)

    # Header
    st.markdown("# ⬡ SPEL · INSTITUTIONAL COMMAND · HINC OMNIA CERNO", unsafe_allow_html=True)

    # Row 1: GT-Score | Entropy
    c_score, c_entropy = st.columns([1, 2])
    with c_score:
        _render_gt_score(data)
    with c_entropy:
        _render_entropy(data)

    # Row 2: Gate R30
    _render_gate(data)

    # Row 3: 4 TG Network Nodes
    _render_tg_nodes(data)

    # Row 4: Shield | Graph health
    c_shield, c_graph = st.columns([1, 1])
    with c_shield:
        _render_shield(data)
    with c_graph:
        _render_graph_health(data)

    # Row 5: Cognitive Terminal
    _render_cognitive_terminal(VAULT)

    # Footer
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    st.markdown(
        f'<div class="mono-xs" style="text-align:right;margin-top:1rem;'
        f'color:var(--dim)">Hinc Omnia Cerno · {now_str} · '
        f'RAM {avail_mb:.0f}MB · refresh {refresh_ttl}s</div>',
        unsafe_allow_html=True,
    )

    # Explicit GC after render
    del data
    gc.collect()

    # Auto-refresh
    time.sleep(refresh_ttl)
    st.rerun()


if __name__ == "__main__":
    main()
