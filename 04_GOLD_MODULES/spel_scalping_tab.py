"""
spel_scalping_tab.py — SPEL 3.0 · Institutional Forex Scalping Tab
Protocol: SPEL_S49 v4.9 · Hinc Omnia Cerno

Tab contract:
  from spel_scalping_tab import render_scalping_tab
  render_scalping_tab(data)   # data = _load_all_vault()

Rules enforced:
  R37:  NO top-level torch import
  R28:  Signal only when regime == GODEL_ON
  R13:  Gold Score weights inamovible
  EF-25: NO Holmes/sandbox in sys.path
  PROHIBITION: NO pandas, NO real-money execution (paper only until Gate R30)

Zero external deps beyond streamlit + stdlib.
"""

from __future__ import annotations
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import streamlit as st

# ─── Constants (R13 inamovible) ──────────────────────────────────────────────

GODEL_ON_THRESHOLD   = 0.65   # Gold Score mínimo para operar
KL_ALERT_THRESHOLD   = 0.20   # bits — Chaos Bridge alert
TE_MIN_OPERATIONAL   = 0.30   # Transfer Entropy mínimo para señal no-FLAT
HALF_KELLY_CAP       = 0.25   # Fracción máxima de capital (half-Kelly cap)
RR_MIN               = 2.0    # Risk:Reward mínimo institucional
BASE_SL_PIPS         = 15     # Stop base en pips, ajustado por KL

REGIME_LABELS = {
    "GODEL_ON":      ("✅ GÖDEL ON",    "#00ff88"),
    "CRISIS_CONTRA": ("⚡ CRISIS CONTRA","#ffaa00"),
    "NORMAL":        ("⏸ NORMAL",       "#888888"),
    "UNKNOWN":       ("❓ UNKNOWN",      "#ff4444"),
}

# ─── Signal computation (pure Python — no torch, no pandas) ──────────────────

def _safe_float(val, default: float = 0.0) -> float:
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _load_vault_json(path: Path) -> dict:
    try:
        if path.exists() and path.stat().st_size > 5:
            return json.loads(path.read_text())
    except Exception:
        pass
    return {}


def compute_scalping_signal(
    last_signal: dict,
    bma_result:  dict,
    gate_metrics: dict,
) -> dict:
    """
    Institutional scalping signal for EURUSD.
    Returns full signal packet. Never raises — degrades gracefully.

    Output schema:
      signal:      "LONG" | "SHORT" | "FLAT"
      gate:        "EXECUTE" | "HOLD" | "BLOCKED"
      reason:      str (human-readable gate rationale)
      confidence:  float [0,1]
      kelly_f:     float [0, HALF_KELLY_CAP]
      sl_pips:     int
      tp_pips:     int
      regime:      str
      gold_score:  float
      shannon:     float
      te:          float
      kl:          float
      backbone:    float
    """
    gold_score       = _safe_float(bma_result.get("gold_score"))
    regime           = bma_result.get("regime", "UNKNOWN")
    shannon          = _safe_float(last_signal.get("entropy_shannon") or last_signal.get("shannon_entropy"))
    te               = _safe_float(last_signal.get("transfer_entropy") or bma_result.get("transfer_entropy"))
    kl               = _safe_float(bma_result.get("kl_divergence"))
    backbone_dir     = _safe_float(last_signal.get("backbone_direction") or last_signal.get("backbone_pred"))
    hit_rate         = _safe_float(gate_metrics.get("hit_rate_godel"), default=0.5)
    vitality_tesla   = _safe_float(last_signal.get("vitality_tesla"))

    # ── Gate logic (R28 absolute) ─────────────────────────────────────────
    if vitality_tesla == 0.0 and not last_signal:
        return _blocked_signal("DATA_STALE — last_signal.json vacío", regime)

    if regime != "GODEL_ON":
        label = REGIME_LABELS.get(regime, REGIME_LABELS["UNKNOWN"])[0]
        return _blocked_signal(f"Regime={label} → inacción disciplinada (R28)", regime,
                               gold_score=gold_score, shannon=shannon, te=te, kl=kl)

    if gold_score < GODEL_ON_THRESHOLD:
        return _blocked_signal(
            f"Gold Score {gold_score:.3f} < {GODEL_ON_THRESHOLD} → umbral GÖDEL no alcanzado",
            regime, gold_score=gold_score, shannon=shannon, te=te, kl=kl)

    if kl > KL_ALERT_THRESHOLD:
        return _blocked_signal(
            f"KL Divergence {kl:.3f} > {KL_ALERT_THRESHOLD} bits → Chaos Bridge alerta",
            regime, gold_score=gold_score, shannon=shannon, te=te, kl=kl)

    # ── Direction ─────────────────────────────────────────────────────────
    if te >= TE_MIN_OPERATIONAL and backbone_dir > 0.05:
        signal = "LONG"
    elif te >= TE_MIN_OPERATIONAL and backbone_dir < -0.05:
        signal = "SHORT"
    else:
        return _blocked_signal(
            f"TE={te:.3f} < {TE_MIN_OPERATIONAL} o backbone ambiguo ({backbone_dir:+.3f}) → FLAT",
            regime, gold_score=gold_score, shannon=shannon, te=te, kl=kl, gate="HOLD")

    # ── Sizing (conservative half-Kelly) ──────────────────────────────────
    # f* = (edge / odds) — proxy: edge ≈ gold_score * (hit_rate - 0.5) * 2
    edge    = max(0.0, gold_score * (hit_rate - 0.5) * 2.0)
    kelly_f = min(edge * 0.5, HALF_KELLY_CAP)  # half-Kelly + cap

    # ── Stop/Target (widen with KL volatility) ────────────────────────────
    sl_pips = int(BASE_SL_PIPS * (1.0 + kl * 2.0))
    tp_pips = int(sl_pips * RR_MIN)

    # ── Confidence proxy ──────────────────────────────────────────────────
    confidence = min(1.0, gold_score * (te / max(te, 0.001)) * (1.0 - kl / KL_ALERT_THRESHOLD))

    return {
        "signal":     signal,
        "gate":       "EXECUTE",
        "reason":     f"GÖDEL ON · Gold={gold_score:.3f} · TE={te:.3f} · KL={kl:.3f} · bb={backbone_dir:+.3f}",
        "confidence": round(confidence, 4),
        "kelly_f":    round(kelly_f, 4),
        "sl_pips":    sl_pips,
        "tp_pips":    tp_pips,
        "regime":     regime,
        "gold_score": gold_score,
        "shannon":    shannon,
        "te":         te,
        "kl":         kl,
        "backbone":   backbone_dir,
    }


def _blocked_signal(reason: str, regime: str = "UNKNOWN", gate: str = "BLOCKED", **kwargs) -> dict:
    base = {
        "signal":     "FLAT",
        "gate":       gate,
        "reason":     reason,
        "confidence": 0.0,
        "kelly_f":    0.0,
        "sl_pips":    0,
        "tp_pips":    0,
        "regime":     regime,
        "gold_score": 0.0,
        "shannon":    0.0,
        "te":         0.0,
        "kl":         0.0,
        "backbone":   0.0,
    }
    base.update(kwargs)
    return base


# ─── History helpers ──────────────────────────────────────────────────────────

def _load_bma_history(vault: Path, n: int = 96) -> list[dict]:
    """Load last n BMA history entries. Returns [] if not yet wired."""
    p = vault / "live_bma_history.json"
    try:
        if p.exists() and p.stat().st_size > 5:
            raw = json.loads(p.read_text())
            if isinstance(raw, list):
                return raw[-n:]
    except Exception:
        pass
    return []


def _sparkline_text(values: list[float], width: int = 20) -> str:
    """ASCII sparkline — no deps."""
    blocks = " ▁▂▃▄▅▆▇█"
    if not values:
        return "─" * width
    mn, mx = min(values), max(values)
    rng = mx - mn or 1e-9
    chars = [blocks[int((v - mn) / rng * (len(blocks) - 1))] for v in values[-width:]]
    return "".join(chars)


# ─── Gate R30 helpers ─────────────────────────────────────────────────────────

def _gate_r30_metrics(gate_metrics: dict) -> dict:
    return {
        "gt_score":      _safe_float(gate_metrics.get("gt_score")),
        "hit_rate":      _safe_float(gate_metrics.get("hit_rate_godel")),
        "max_dd":        _safe_float(gate_metrics.get("max_drawdown_7d")),
        "pnl":           _safe_float(gate_metrics.get("pnl_kelly_weighted")),
        "no_trade_rate": _safe_float(gate_metrics.get("no_trade_rate")),
    }


# ─── Main render function ─────────────────────────────────────────────────────

def render_scalping_tab(data: dict) -> None:
    """
    Entry point. Call from spel_dashboard.py:
        render_scalping_tab(_load_all_vault(str(VAULT)))
    
    'data' keys used:
        data["bma"]         → live_bma_result.json content
        data["signal"]      → last_signal.json content
        data["gate"]        → gate_metrics.json content
        data["forex"]       → live_forex_signal.json content
        data["pulse"]       → system_pulse.json content
        data["vault_path"]  → Path to vault dir (optional, for BMA history)
    """

    # ── Resolve vault path (tries common locations) ───────────────────────
    vault_path: Optional[Path] = None
    if "vault_path" in data:
        vault_path = Path(data["vault_path"])
    else:
        for candidate in [
            "/content/drive/MyDrive/SPEL-v2.0/meta",
            "/content/drive/MyDrive/ORDEN/SPEL 3.0/00_VAULT",
            "meta",
        ]:
            p = Path(candidate)
            if p.exists():
                vault_path = p
                break

    bma_result   = data.get("bma")    or {}
    last_signal  = data.get("signal") or {}
    gate_metrics = data.get("gate")   or {}
    forex_signal = data.get("forex")  or {}
    pulse        = data.get("pulse")  or {}
    bma_history  = _load_bma_history(vault_path) if vault_path else []

    sig = compute_scalping_signal(last_signal, bma_result, gate_metrics)
    gm  = _gate_r30_metrics(gate_metrics)
    regime_label, regime_color = REGIME_LABELS.get(sig["regime"], REGIME_LABELS["UNKNOWN"])

    # ── Header ────────────────────────────────────────────────────────────
    st.markdown(f"""
<div style="
  background: #0a0f1a;
  border: 1px solid #1a2a4a;
  border-radius: 6px;
  padding: 12px 20px;
  margin-bottom: 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
">
  <div>
    <span style="color:#4af; font-size:18px; font-weight:700; letter-spacing:2px;">
      ⚡ SCALPING FOREX — EURUSD
    </span>
    <span style="color:#666; font-size:12px; margin-left:16px;">
      Holmes OS V4.0 · Paper Mode · Gate R30 Día 16/63
    </span>
  </div>
  <div style="
    background: {regime_color}22;
    border: 1px solid {regime_color};
    border-radius: 4px;
    padding: 4px 14px;
    color: {regime_color};
    font-weight: 700;
    font-size: 14px;
    letter-spacing: 1px;
  ">{regime_label}</div>
</div>
""", unsafe_allow_html=True)

    # SEC-01 banner if data is stale or blocked
    if sig["gate"] == "BLOCKED" and "DATA_STALE" in sig["reason"]:
        st.error("⛔ DATA_STALE activo — `last_signal.json` vacío entre runs. "
                 "Deploy `patrol_s48_fixed.yml` → siguiente ciclo de 15min resolverá esto.")

    # ── Row 1: 4 panels ───────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns([1.2, 1.1, 1.1, 1.0])

    with c1:
        st.markdown("**SIGNAL PANEL**")
        signal_emoji = {"LONG": "📈 LONG", "SHORT": "📉 SHORT", "FLAT": "⏸ FLAT"}[sig["signal"]]
        signal_color = {"LONG": "#00ff88", "SHORT": "#ff4444", "FLAT": "#888888"}[sig["signal"]]
        st.markdown(f"""
<div style="text-align:center; padding:10px; background:#0a0f1a; border:1px solid #1a2a4a; border-radius:6px;">
  <div style="font-size:28px; font-weight:900; color:{signal_color}; letter-spacing:2px;">
    {signal_emoji}
  </div>
  <div style="color:#aaa; font-size:11px; margin-top:4px;">
    Gate: <span style="color:{'#00ff88' if sig['gate']=='EXECUTE' else '#ff4444'}; font-weight:700;">
      {sig['gate']}
    </span>
  </div>
</div>
""", unsafe_allow_html=True)
        st.metric("Gold Score", f"{sig['gold_score']:.3f}", delta=None)
        st.metric("Confianza",  f"{sig['confidence']*100:.1f}%")
        st.metric("Kelly f*",   f"{sig['kelly_f']*100:.2f}% capital")
        if sig["reason"]:
            st.caption(f"💬 {sig['reason'][:120]}")

    with c2:
        st.markdown("**ENTROPY GATE**")
        p90_ref = _safe_float(
            (last_signal or {}).get("p90_entropy") or
            bma_result.get("p90_entropy"), default=1.9
        )
        shannon_pct = min(1.0, sig["shannon"] / max(p90_ref, 0.001))
        st.markdown(f"**Shannon H(X):** `{sig['shannon']:.4f}`")
        st.progress(shannon_pct, text=f"vs p90 {p90_ref:.3f}")

        te_color = "🟢" if sig["te"] >= TE_MIN_OPERATIONAL else "🔴"
        st.markdown(f"**Transfer Entropy:** {te_color} `{sig['te']:.4f}` bits")
        st.caption(f"Umbral TE: {TE_MIN_OPERATIONAL} bits")

        kl_pct = min(1.0, sig["kl"] / KL_ALERT_THRESHOLD)
        kl_color = "#ff4444" if sig["kl"] > KL_ALERT_THRESHOLD * 0.8 else "#00ff88"
        st.markdown(f"**KL Divergence:** `{sig['kl']:.4f}` bits")
        st.progress(kl_pct, text=f"alerta en {KL_ALERT_THRESHOLD} bits")

        st.markdown(f"**Backbone:** `{sig['backbone']:+.4f}`")

        # Sparkline from BMA history
        if bma_history:
            gs_vals = [_safe_float(e.get("gold_score")) for e in bma_history]
            te_vals = [_safe_float(e.get("transfer_entropy")) for e in bma_history]
            kl_vals = [_safe_float(e.get("kl_divergence")) for e in bma_history]
            st.markdown(f"""
<div style="font-family:monospace; font-size:11px; color:#4af; background:#0a0f1a; padding:8px; border-radius:4px; margin-top:8px;">
GS: {_sparkline_text(gs_vals)}<br/>
TE: {_sparkline_text(te_vals)}<br/>
KL: <span style="color:#ff9500;">{_sparkline_text(kl_vals)}</span>
</div>
""", unsafe_allow_html=True)
        else:
            st.caption("📊 Sparklines: disponibles tras wiring BMA history (S49 §8)")

    with c3:
        st.markdown("**EXEC PREVIEW** *(Paper)*")

        # Pull real EURUSD reference from forex_signal if available
        ref_price  = _safe_float(forex_signal.get("entry_price") or forex_signal.get("price"), default=0.0)
        price_disp = f"{ref_price:.4f}" if ref_price > 0 else "—(sin feed)"

        if sig["gate"] == "EXECUTE" and sig["sl_pips"] > 0:
            pip_val = 0.0001  # EURUSD pip
            sl_price = ref_price - sig["sl_pips"] * pip_val if sig["signal"] == "LONG" \
                       else ref_price + sig["sl_pips"] * pip_val
            tp_price = ref_price + sig["tp_pips"] * pip_val if sig["signal"] == "LONG" \
                       else ref_price - sig["tp_pips"] * pip_val

            canonical_capital = 100_000.0
            position_usd = canonical_capital * sig["kelly_f"]

            st.markdown(f"""
<div style="background:#0a0f1a; border:1px solid #1a2a4a; border-radius:6px; padding:12px; font-size:13px;">
  <div style="color:#aaa;">Entry ref: <span style="color:#fff;">{price_disp}</span></div>
  <div style="color:#aaa;">SL:  <span style="color:#ff4444;">{sl_price:.4f}</span>  ({sig['sl_pips']} pip)</div>
  <div style="color:#aaa;">TP:  <span style="color:#00ff88;">{tp_price:.4f}</span>  ({sig['tp_pips']} pip)</div>
  <div style="color:#aaa;">RR:  <span style="color:#fff;">1:{RR_MIN:.0f}</span></div>
  <div style="color:#aaa; margin-top:8px;">Size: <span style="color:#4af;">${position_usd:,.0f}</span></div>
  <div style="color:#aaa;">Kelly f*: <span style="color:#4af;">{sig['kelly_f']*100:.2f}%</span></div>
</div>
""", unsafe_allow_html=True)
        else:
            st.markdown(f"""
<div style="background:#0a0f1a; border:1px solid #2a1a1a; border-radius:6px; padding:12px; color:#666; font-size:12px;">
  Gate: {sig['gate']}<br/>
  {sig['reason'][:100]}
</div>
""", unsafe_allow_html=True)

        # Paper mode disclaimer
        st.markdown("""
<div style="background:#1a0a0a; border:1px solid #5a1a1a; border-radius:4px; padding:6px 10px; margin-top:8px; font-size:11px; color:#ff6666;">
  ⚠️ PAPER ONLY · Capital canónico $100k (R33)<br/>
  Live bloqueado hasta Gate R30 GO LIVE
</div>
""", unsafe_allow_html=True)

    with c4:
        st.markdown("**SESSION STATS**")
        # Gate R30 metrics
        cycle_ts = pulse.get("ts") or bma_result.get("ts") or "—"
        st.metric("GT-Score", f"{gm['gt_score']:.4f}" if gm['gt_score'] else "—")
        st.metric("Hit Rate (Gödel)", f"{gm['hit_rate']*100:.1f}%" if gm['hit_rate'] else "—")
        st.metric("Max DD 7d", f"{gm['max_dd']*100:.2f}%" if gm['max_dd'] else "—")
        st.metric("PnL Kelly", f"${gm['pnl']:,.2f}" if gm['pnl'] else "—")

        st.markdown(f"""
<div style="background:#0a0f1a; border:1px solid #1a2a4a; border-radius:4px; padding:8px; margin-top:8px; font-size:11px; color:#888;">
  Gate R30: Día 16/63<br/>
  Target: ~20-May-2026<br/>
  Último ciclo:<br/>
  <span style="color:#4af;">{str(cycle_ts)[:16]}</span>
</div>
""", unsafe_allow_html=True)

    # ── Row 2: BMA History Chart ──────────────────────────────────────────
    st.markdown("---")
    st.markdown("**📊 ENTROPY FLOW — Historial BMA (últimas 96 entradas · 24h)**")

    if bma_history:
        import streamlit as st

        try:
            # Try plotly if available, else fallback to st.line_chart
            import plotly.graph_objects as go  # type: ignore

            ts_labels = [e.get("ts", "")[:16] for e in bma_history]
            gs_series = [_safe_float(e.get("gold_score")) for e in bma_history]
            te_series = [_safe_float(e.get("transfer_entropy")) for e in bma_history]
            kl_series = [_safe_float(e.get("kl_divergence")) for e in bma_history]

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=ts_labels, y=gs_series, name="Gold Score",
                                     line=dict(color="#00ff88", width=2)))
            fig.add_trace(go.Scatter(x=ts_labels, y=te_series, name="Transfer Entropy",
                                     line=dict(color="#4af", width=1.5)))
            fig.add_trace(go.Scatter(x=ts_labels, y=kl_series, name="KL Divergence",
                                     line=dict(color="#ff9500", width=1.5, dash="dot")))
            fig.add_hline(y=GODEL_ON_THRESHOLD, line_color="#00ff88",
                          line_dash="dash", annotation_text="GÖDEL ON")
            fig.add_hline(y=KL_ALERT_THRESHOLD, line_color="#ff4444",
                          line_dash="dash", annotation_text="KL ALERTA")
            fig.update_layout(
                height=280,
                margin=dict(l=0, r=0, t=20, b=0),
                paper_bgcolor="#0a0f1a",
                plot_bgcolor="#0a0f1a",
                font=dict(color="#aaa"),
                legend=dict(orientation="h", y=1.05),
                xaxis=dict(showgrid=False, tickfont=dict(size=9)),
                yaxis=dict(showgrid=True, gridcolor="#1a2a4a"),
            )
            st.plotly_chart(fig, use_container_width=True)
        except ImportError:
            # Fallback: st.line_chart
            import collections
            chart_data = collections.defaultdict(list)
            for e in bma_history:
                chart_data["Gold Score"].append(_safe_float(e.get("gold_score")))
                chart_data["TE"].append(_safe_float(e.get("transfer_entropy")))
                chart_data["KL"].append(_safe_float(e.get("kl_divergence")))
            st.line_chart(chart_data)
    else:
        st.markdown("""
<div style="background:#0a0f1a; border:1px solid #1a2a4a; border-radius:6px; padding:24px; text-align:center; color:#666;">
  📊 Historial vacío — <code>live_bma_history.json = 2 bytes</code><br/>
  El acumulador BMA (Step 3 de S49) debe ejecutarse primero.<br/>
  Tras un ciclo con el patch del orchestrator: datos aparecen aquí automáticamente.
</div>
""", unsafe_allow_html=True)

    # ── Row 3: Microstructure (CVD / Order Flow) ──────────────────────────
    st.markdown("---")
    st.markdown("**🔬 MICROESTRUCTURA — CVD · Order Flow · Liquidity Sweeps**")

    cvd_val   = _safe_float(forex_signal.get("cvd") or last_signal.get("cvd"))
    sweeps    = int(_safe_float(forex_signal.get("liquidity_sweeps") or last_signal.get("liquidity_sweeps")))
    spread_p  = _safe_float(forex_signal.get("spread_pips") or last_signal.get("spread_pips"), default=1.2)

    mc1, mc2, mc3 = st.columns(3)
    with mc1:
        cvd_color = "#00ff88" if cvd_val > 0 else "#ff4444" if cvd_val < 0 else "#888"
        cvd_label = "Compradores agresivos ✅" if cvd_val > 0 else \
                    "Vendedores agresivos ⚠️" if cvd_val < 0 else "Neutro —"
        st.markdown(f"""
<div style="background:#0a0f1a; border:1px solid #1a2a4a; border-radius:6px; padding:12px;">
  <div style="color:#aaa; font-size:11px;">Cumulative Volume Delta</div>
  <div style="color:{cvd_color}; font-size:22px; font-weight:700;">
    {f'{cvd_val:+,.0f}' if cvd_val != 0 else '—'}
  </div>
  <div style="color:#666; font-size:11px;">{cvd_label}</div>
  <div style="color:#444; font-size:10px; margin-top:4px;">
    {'Feed: live_forex_signal.json' if cvd_val != 0 else 'Feed: sin datos (0 bytes)'}
  </div>
</div>
""", unsafe_allow_html=True)

    with mc2:
        sweep_color = "#ff4444" if sweeps > 0 else "#00ff88"
        sweep_label = f"{sweeps} barrida(s) detectada(s) ⚠️" if sweeps > 0 else "Limpio — sin barrida institucional"
        st.markdown(f"""
<div style="background:#0a0f1a; border:1px solid #1a2a4a; border-radius:6px; padding:12px;">
  <div style="color:#aaa; font-size:11px;">Liquidity Sweeps (15min)</div>
  <div style="color:{sweep_color}; font-size:22px; font-weight:700;">{sweeps}</div>
  <div style="color:#666; font-size:11px;">{sweep_label}</div>
  <div style="color:#444; font-size:10px; margin-top:4px;">
    Confirmación pre-ejecución institucional (R16 microestructura)
  </div>
</div>
""", unsafe_allow_html=True)

    with mc3:
        spread_ok = spread_p <= 2.0
        st.markdown(f"""
<div style="background:#0a0f1a; border:1px solid #1a2a4a; border-radius:6px; padding:12px;">
  <div style="color:#aaa; font-size:11px;">Spread actual (pips)</div>
  <div style="color:{'#00ff88' if spread_ok else '#ff4444'}; font-size:22px; font-weight:700;">
    {spread_p:.1f} pip
  </div>
  <div style="color:#666; font-size:11px;">
    {'✅ Spread dentro de rango' if spread_ok else '⚠️ Spread elevado — spreads > 2 pip penalizan scalping'}
  </div>
  <div style="color:#444; font-size:10px; margin-top:4px;">Umbral máximo institucional: 2.0 pip</div>
</div>
""", unsafe_allow_html=True)

    # ── Footer ────────────────────────────────────────────────────────────
    ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    st.markdown(f"""
<div style="margin-top:16px; padding:8px 12px; background:#050810; border-top:1px solid #1a2a4a; font-size:10px; color:#444; display:flex; justify-content:space-between;">
  <span>SPEL 3.0 · Holmes OS V4.0 · spel_scalping_tab.py · S49</span>
  <span>Rendered: {ts_now}</span>
  <span style="color:#ff4444;">⚠️ Paper Mode — Gate R30 activo</span>
</div>
""", unsafe_allow_html=True)
