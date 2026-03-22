"""
SPEL v40 · Ojo de Dios · Dashboard Institucional
Hinc Omnia Cerno

Deploy: Streamlit Cloud → sandbox33/SPEL → scripts/main_ui.py
Data:   github.com/sandbox33/SPEL/raw/dashboard-data/spel_v40_execution_summary.json
        meta/SHA_REGISTRY.json + meta/forex_macro_snapshot.json (branch main)

R7: read-only. Zero writes. Zero Drive dependency.
R33: canonical $100k visible en gate panel.
EF-18: LEVANTADO — GDELT real 63/63 confirmado S36.
"""

import streamlit as st
import json, urllib.request, time
from datetime import datetime, timezone

st.set_page_config(
    page_title="SPEL v40 · Hinc Omnia Cerno",
    page_icon="👁",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;700;800&display=swap');
:root {
  --bg:#050810; --surface:#0c1020; --border:#1a2540;
  --cyan:#00d4ff; --green:#00ff88; --yellow:#ffd700;
  --red:#ff3366; --dim:#4a5568; --text:#e2e8f0;
  --mono:'Space Mono',monospace; --sans:'Syne',sans-serif;
}
html,body,[class*="css"]{background:var(--bg)!important;color:var(--text)!important;font-family:var(--sans)!important;}
.stApp{background:var(--bg)!important;}
h1,h2,h3{font-family:var(--sans)!important;font-weight:800!important;}
[data-testid="metric-container"]{background:var(--surface)!important;border:1px solid var(--border)!important;border-radius:4px!important;padding:12px!important;}
[data-testid="stSidebar"]{background:var(--surface)!important;}
hr{border-color:var(--border)!important;}
.pc{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:16px;margin-bottom:12px;}
.badge{display:inline-block;padding:2px 8px;border-radius:3px;font-family:var(--mono);font-size:10px;font-weight:700;}
.bg{background:#00ff8822;color:#00ff88;border:1px solid #00ff88;}
.by{background:#ffd70022;color:#ffd700;border:1px solid #ffd700;}
.br{background:#ff336622;color:#ff3366;border:1px solid #ff3366;}
.bc{background:#00d4ff22;color:#00d4ff;border:1px solid #00d4ff;}
.bd{background:#1a254022;color:#4a5568;border:1px solid #4a5568;}
@keyframes gp{0%{box-shadow:0 0 0 0 rgba(0,212,255,.4);}70%{box-shadow:0 0 0 8px rgba(0,212,255,0);}100%{box-shadow:0 0 0 0 rgba(0,212,255,0);}}
.ga{animation:gp 2s infinite;border-color:#00d4ff!important;}
.hdr{background:linear-gradient(180deg,rgba(0,212,255,.03),rgba(0,212,255,.08),rgba(0,212,255,.03));border-top:1px solid var(--cyan);border-bottom:1px solid var(--border);padding:12px 20px;margin-bottom:20px;}
</style>
""", unsafe_allow_html=True)

# ── Data loaders ─────────────────────────────────────────────────────
GH_RAW  = "https://raw.githubusercontent.com/sandbox33/SPEL"
REFRESH = 30

def _fetch(url: str) -> dict | list | None:
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            return json.loads(r.read())
    except Exception:
        return None

@st.cache_data(ttl=REFRESH)
def load_summary():  return _fetch(f"{GH_RAW}/dashboard-data/spel_v40_execution_summary.json") or {}
@st.cache_data(ttl=REFRESH)
def load_registry(): return _fetch(f"{GH_RAW}/main/meta/SHA_REGISTRY.json") or {}
@st.cache_data(ttl=REFRESH)
def load_forex():    return _fetch(f"{GH_RAW}/main/meta/forex_macro_snapshot.json") or {}
@st.cache_data(ttl=REFRESH)
def load_gate():     return _fetch(f"{GH_RAW}/dashboard-data/gate_metrics.json") or {}
@st.cache_data(ttl=60)
def load_trades():
    try:
        import csv, io
        with urllib.request.urlopen(f"{GH_RAW}/dashboard-data/trade_log.csv", timeout=8) as r:
            return list(csv.DictReader(io.StringIO(r.read().decode())))[-30:]
    except Exception:
        return []

def _sc(s):  return "bg" if s>=75 else "by" if s>=60 else "br"
def _b(t,c): return f'<span class="badge {c}">{t}</span>'
def _ts(iso):
    try:    return datetime.fromisoformat(iso.replace("Z","+00:00")).strftime("%H:%M UTC")
    except: return iso[:16] if iso else "—"

def _whale(rec):
    e   = float(rec.get("entropy",0) or 0)
    p90 = float(rec.get("p90_entropy",1.2) or 1.2)
    g   = rec.get("godel_active", False)
    s   = int(rec.get("score_oro",0) or 0)
    compressed = e/max(p90,1e-10) < 0.85
    spike      = e/max(p90,1e-10) > 1.15
    ws = sum([g and compressed, compressed, spike, s>=65])
    if g and compressed: return "ACCUMULATION 🐋", ws, "#00d4ff"
    if spike and g:      return "DISTRIBUTION ⚡", ws, "#ffd700"
    if spike:            return "TRENDING 📈",     ws, "#00ff88"
    return               "NEUTRAL ○",             ws, "#4a5568"

# ════════════════════════════════════════════════════════════════
# SIDEBAR
# ════════════════════════════════════════════════════════════════
def sidebar():
    with st.sidebar:
        st.markdown("### 👁 SPEL v40")
        st.caption("Hinc Omnia Cerno")
        st.divider()
        st.caption("**Data** → dashboard-data branch")
        st.caption(f"**Refresh** → {REFRESH}s")
        st.divider()
        st.markdown("[🔗 Actions](https://github.com/sandbox33/SPEL/actions)")
        st.markdown("[🔗 SHA Registry](https://github.com/sandbox33/SPEL/blob/main/meta/SHA_REGISTRY.json)")
        st.divider()
        st.caption("R33: canonical $100k | sandbox $10")
        st.caption("EF-18: LEVANTADO S36")
        st.caption("Gate R30: Día activo")
        if st.button("🔄 Refresh"):
            st.cache_data.clear(); st.rerun()

# ════════════════════════════════════════════════════════════════
# P0 — HEADER
# ════════════════════════════════════════════════════════════════
def p0_header(s, g):
    gs  = g.get("gate_status", g) if g else {}
    day = s.get("day_counter","?")
    eq  = gs.get("equity_canonical",100_000)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    st.markdown(f"""
    <div class="hdr">
      <span style="font-family:var(--mono);font-size:11px;color:var(--cyan);letter-spacing:3px">
        👁 OJO DE DIOS · SPEL v40 · HINC OMNIA CERNO
      </span>
      <span style="float:right;font-family:var(--mono);font-size:10px;color:var(--dim)">
        {now} &nbsp;·&nbsp; Day {day}/63 &nbsp;·&nbsp; ${eq:,.0f} canonical
      </span>
    </div>""", unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════
# P1 — SEÑALES ACCIONABLES (manual trading — top priority)
# ════════════════════════════════════════════════════════════════
def p1_signals(s):
    st.markdown("### 🎯 Señales Accionables — Operación Manual")
    recs   = s.get("last_cycle",[])
    viable = [r for r in recs if r.get("viable")]
    if not viable:
        best = max(recs, key=lambda r: int(r.get("score_oro",0) or 0)) if recs else None
        st.info("Sin señales viables. Próximo check: 07:45 ECT o ciclo de 15min.")
        if best:
            sc = int(best.get("score_oro",0) or 0)
            st.caption(f"Mejor candidato: **{best.get('asset')}** "
                      f"score={sc}/100 {best.get('direction')} (threshold 70)")
        return
    for rec in viable:
        asset = rec.get("asset","?")
        sc    = int(rec.get("score_oro",0) or 0)
        d     = rec.get("direction","?")
        e     = float(rec.get("entry_price",0) or 0)
        sl    = float(rec.get("stop_loss",0) or 0)
        tp    = float(rec.get("take_profit",0) or 0)
        k     = float(rec.get("kelly_fraction",0) or 0)
        rr    = (tp-e)/max(abs(sl-e),1e-10) if e else 0
        rk    = 100_000 * k * 0.05
        regime= rec.get("regime_label","?")
        st.markdown(f"""
        <div class="pc" style="border-color:#00ff88">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
            <span style="font-family:var(--sans);font-size:20px;font-weight:800;color:#00ff88">{asset}</span>
            <span>{_b(d,'bg')} {_b(f'{sc}/100','bg')} {_b(regime,'bc')}</span>
          </div>
          <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;font-family:var(--mono);font-size:10px">
            <div><div style="color:var(--dim)">ENTRY</div>
                 <div style="color:var(--text);font-size:14px;font-weight:700">{e:.4f}</div></div>
            <div><div style="color:var(--red)">STOP LOSS</div>
                 <div style="color:var(--red);font-size:14px;font-weight:700">{sl:.4f}</div></div>
            <div><div style="color:var(--green)">TAKE PROFIT</div>
                 <div style="color:var(--green);font-size:14px;font-weight:700">{tp:.4f}</div></div>
            <div><div style="color:var(--dim)">R:R / KELLY</div>
                 <div style="color:var(--yellow);font-size:14px;font-weight:700">{rr:.1f}x / {k:.4f}</div></div>
          </div>
          <div style="margin-top:6px;font-family:var(--mono);font-size:9px;color:var(--dim)">
            Risk $100k base: ${rk:.2f} &nbsp;·&nbsp; R33 canonical
          </div>
        </div>""", unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════
# P2 — SCORE GRID + WHALE DETECTOR
# ════════════════════════════════════════════════════════════════
def p2_score_whale(s):
    recs = s.get("last_cycle",[])
    if not recs: st.caption("Sin ciclo reciente"); return
    c1, c2 = st.columns([3,2])
    with c1:
        st.markdown("### 🎯 Score de Oro — Core Assets")
        cols = st.columns(len(recs))
        for col, rec in zip(cols, recs):
            asset  = rec.get("asset","?")
            sc     = int(rec.get("score_oro",0) or 0)
            d      = rec.get("direction","?")
            g      = rec.get("godel_active",False)
            viable = rec.get("viable",False)
            e      = float(rec.get("entry_price",0) or 0)
            sl_v   = float(rec.get("stop_loss",0) or 0)
            tp_v   = float(rec.get("take_profit",0) or 0)
            k      = float(rec.get("kelly_fraction",0) or 0)
            sha    = rec.get("sha_parquet","?")
            cc     = "#00ff88" if sc>=75 else "#ffd700" if sc>=60 else "#ff3366"
            gcls   = "ga" if g else ""
            with col:
                st.markdown(f"""
                <div class="pc {gcls}" style="border-color:{'#00ff88' if viable else 'var(--border)'};text-align:center">
                  <div style="font-family:var(--sans);font-size:16px;font-weight:800">{asset}</div>
                  <div style="font-family:var(--mono);font-size:30px;font-weight:700;color:{cc}">{sc}</div>
                  <div style="font-size:9px;color:var(--dim);font-family:var(--mono)">/100</div>
                  <div style="margin:6px 0">{_b(d,_sc(sc))} {_b('G✓' if g else 'G○','bc' if g else 'bd')}</div>
                  <div style="font-family:var(--mono);font-size:9px;color:var(--dim);text-align:left;margin-top:8px">
                    E:{f'{e:.4f}' if e else '—'}<br>SL:{f'{sl_v:.4f}' if sl_v else '—'}<br>
                    TP:{f'{tp_v:.4f}' if tp_v else '—'}<br>K:{k:.4f}<br>
                    <span style="color:#2d3a55">{sha[:8]}</span>
                  </div>
                  {'<div style="color:var(--green);font-size:9px;margin-top:4px">⭐ VIABLE</div>' if viable else ''}
                </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown("### 🐋 Wyckoff · Whale Detector")
        for rec in recs:
            asset = rec.get("asset","?")
            regime, ws, wc = _whale(rec)
            e_val  = float(rec.get("entropy",0) or 0)
            p90    = float(rec.get("p90_entropy",1.2) or 1.2)
            st.markdown(f"""
            <div class="pc" style="text-align:center;padding:10px">
              <div style="font-family:var(--mono);font-size:9px;color:var(--dim)">{asset}</div>
              <div style="font-family:var(--sans);font-size:12px;font-weight:700;color:{wc};margin:4px 0">{regime}</div>
              <div style="background:var(--border);border-radius:2px;height:4px;margin:4px 0">
                <div style="width:{ws*25}%;height:100%;background:linear-gradient(90deg,#00d4ff,#00ff88);border-radius:2px"></div>
              </div>
              <div style="font-family:var(--mono);font-size:9px;color:var(--dim)">
                ent={e_val:.3f} p90={p90:.3f} score={ws}/4
              </div>
            </div>""", unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════
# P3 — ENTROPY GAUGE
# ════════════════════════════════════════════════════════════════
def p3_entropy(s, forex):
    st.markdown("### ⚡ GDELT Entropy — Activaciones Gödel")
    try:
        import plotly.graph_objects as go
        items = []
        for rec in s.get("last_cycle",[]):
            items.append({"l":rec.get("asset","?"),
                          "e":float(rec.get("entropy",0) or 0),
                          "p":float(rec.get("p90_entropy",1.2) or 1.2),
                          "g":rec.get("godel_active",False)})
        for pair, v in list(forex.get("pairs",{}).items())[:5]:
            items.append({"l":pair,
                          "e":float(v.get("entropy",0) or 0),
                          "p":float(v.get("p90",1.2) or 1.2),
                          "g":v.get("godel_active",False)})
        if not items: st.caption("Sin datos"); return
        colors = ["#00ff88" if i["g"] else "#00d4ff" if i["e"]>=i["p"]*0.85 else "#1a2540"
                  for i in items]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=[i["l"] for i in items], y=[i["e"] for i in items],
            marker_color=colors, text=[f'{i["e"]:.3f}' for i in items],
            textposition="outside", textfont=dict(family="Space Mono",size=9,color="#4a5568")))
        fig.add_trace(go.Scatter(x=[i["l"] for i in items], y=[i["p"] for i in items],
            mode="lines+markers", name="P90",
            line=dict(color="#ff3366",dash="dot",width=1.5), marker=dict(size=4,color="#ff3366")))
        fig.update_layout(paper_bgcolor="#050810",plot_bgcolor="#0c1020",
            font=dict(family="Space Mono",color="#4a5568",size=10),
            legend=dict(orientation="h",y=1.1,font=dict(size=9)),
            margin=dict(l=30,r=30,t=30,b=20),height=240,showlegend=True,
            yaxis=dict(gridcolor="#1a2540",zerolinecolor="#1a2540"),
            xaxis=dict(gridcolor="#1a2540"),bargap=0.3)
        st.plotly_chart(fig, use_container_width=True)
        st.caption("🟢 Gödel activo  🔵 Approaching P90  ⬜ Below threshold  — P90 threshold")
    except ImportError:
        for i in (s.get("last_cycle",[]) or []):
            e = float(i.get("entropy",0) or 0)
            p = float(i.get("p90_entropy",1.2) or 1.2)
            st.text(f"{i.get('asset')}: {e:.3f} / {p:.3f} {'✅' if e>=p else '○'}")

# ════════════════════════════════════════════════════════════════
# P4 — FOREX HUD
# ════════════════════════════════════════════════════════════════
def p4_forex(forex):
    st.markdown("### 📡 Forex HUD — Confluencia Macro GDELT")
    pairs = forex.get("pairs",{})
    if not pairs:
        st.caption(f"Snapshot: {_ts(forex.get('updated',''))}"); return
    st.caption(f"Snapshot: {_ts(forex.get('updated',''))} | Señal viable ≥ 75pts")
    for pair, v in pairs.items():
        e    = float(v.get("entropy",0) or 0)
        p90  = float(v.get("p90",1.2) or 1.2)
        g    = v.get("godel_active",False)
        f    = float(v.get("fear_momentum",0) or 0)
        n    = float(v.get("nash_frozen",0.5) or 0.5)
        prx  = v.get("proxy","?")
        ms   = min(int(abs(f)*100),35) if g else 10
        ms  += 10 if n>0.75 else 0
        cc   = "#00ff88" if ms>=35 else "#ffd700" if ms>=25 else "#4a5568"
        bw   = int((ms/45)*100)
        c1, c2 = st.columns([1,4])
        with c1:
            st.markdown(f"<div style='font-family:var(--sans);font-weight:800;font-size:15px'>{pair}</div>"
                       f"<div style='font-family:var(--mono);font-size:9px;color:var(--dim)'>proxy:{prx}</div>",
                       unsafe_allow_html=True)
        with c2:
            st.markdown(f"""
            <div style="background:var(--surface);border:1px solid var(--border);border-radius:4px;padding:8px">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
                <span style="font-family:var(--mono);font-size:18px;font-weight:700;color:{cc}">{ms}</span>
                <span style="font-family:var(--mono);font-size:9px;color:var(--dim)">macro pts</span>
                {'<span style="color:var(--cyan);font-size:9px">⚡ Gödel</span>' if g else ''}
              </div>
              <div style="background:var(--border);border-radius:2px;height:3px">
                <div style="width:{bw}%;height:100%;background:{cc};border-radius:2px"></div>
              </div>
              <div style="font-family:var(--mono);font-size:9px;color:var(--dim);margin-top:4px">
                ent={e:.3f} p90={p90:.3f} nash={n:.2f} fear={f:+.3f}
              </div>
            </div>""", unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════
# P5 — GATE R30
# ════════════════════════════════════════════════════════════════
def p5_gate(s, g):
    st.markdown("### 🛡️ Gate R30 — Paper Trading 63 días")
    gs  = g.get("gate_status", g) if g else {}
    day = int(s.get("day_counter",0) or 0)
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Hit Rate Gödel", f"{gs.get('hit_rate_godel',0):.1%}", "target >56%")
    c2.metric("Max DD 7d",      f"{gs.get('max_drawdown_7d',0):.2%}", "limit 8%",
              delta_color="inverse")
    c3.metric("PnL Kelly $100k",f"${gs.get('pnl_kelly_weighted',0):,.2f}")
    c4.metric("No-Trade Rate",  f"{gs.get('no_trade_rate',0):.1%}", "target 30-70%")
    pct = min(day/63, 1.0)
    st.markdown(f"""
    <div style="margin-top:8px">
      <div style="font-family:var(--mono);font-size:10px;color:var(--dim);margin-bottom:4px">
        Día {day}/63 — {pct:.0%}</div>
      <div style="background:var(--border);border-radius:3px;height:6px">
        <div style="width:{pct*100:.0f}%;height:100%;border-radius:3px;
                    background:linear-gradient(90deg,#00d4ff,#00ff88)"></div>
      </div>
    </div>""", unsafe_allow_html=True)
    conds = [
        ("hit_rate >56%",  gs.get("hit_rate_pass",False)),
        ("drawdown <8%",   gs.get("drawdown_pass",False)),
        ("PnL >0",         gs.get("pnl_pass",False)),
        ("no-trade 30-70%",gs.get("no_trade_pass",False)),
        ("SHA clean",      gs.get("sha_pass",True)),
        ("GDELT real ✅",  True),
    ]
    cols = st.columns(len(conds))
    for col,(label,passed) in zip(cols,conds):
        col.markdown(
            f"<div style='text-align:center;font-family:Space Mono;font-size:9px;"
            f"color:{'#00ff88' if passed else '#4a5568'}'>"
            f"{'✅' if passed else '⬜'}<br>{label}</div>",
            unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════
# P6+P7 — TRADE JOURNAL + HEALTH
# ════════════════════════════════════════════════════════════════
def p6_journal(trades):
    st.markdown("### 📒 Trade Journal")
    if not trades:
        st.caption("Sin trades — primera evaluación pendiente"); return
    COLS = ["timestamp_utc","asset","direction","score_oro","godel_active",
            "viable","regime_label","pnl_canonical","kelly_fraction","sha_parquet"]
    rows = [{k:t.get(k,"") for k in COLS} for t in reversed(trades[-15:])]
    try:
        import pandas as pd
        st.dataframe(pd.DataFrame(rows), use_container_width=True, height=260,
            column_config={
                "score_oro":     st.column_config.NumberColumn("Score",format="%d"),
                "pnl_canonical": st.column_config.NumberColumn("PnL $100k",format="$%.2f"),
                "kelly_fraction":st.column_config.NumberColumn("Kelly",format="%.4f"),
            })
    except ImportError:
        st.table(rows[:8])

def p7_health(s, registry):
    st.markdown("### ⚙️ Health")
    acct = s.get("alpaca_account",{})
    gate = s.get("gate_metrics",{})
    gs   = gate.get("gate_status",gate) if gate else {}
    st.markdown("**SHA Registry**")
    for asset, meta in registry.items():
        if isinstance(meta,dict):
            sha = meta.get("sha_v5","?")[:8]
            st.caption(f"`{asset}` `{sha}`")
    st.divider()
    eq = acct.get("equity","?")
    if eq and eq != "?":
        st.metric("Alpaca Equity", f"${float(eq):,.2f}")
    sha_m = gs.get("sha_mismatches",0)
    st.error(f"⚠️ {sha_m} SHA mismatch") if sha_m else st.caption("✅ SHA clean")
    st.caption(f"Evals: {gs.get('n_evaluations',0)}")
    st.caption(f"Gödel trades: {gs.get('n_trades_godel',0)}/30")

# ════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════
def main():
    sidebar()
    s   = load_summary()
    reg = load_registry()
    fx  = load_forex()
    tr  = load_trades()
    g   = load_gate()
    if g and not s.get("gate_metrics"):
        s["gate_metrics"] = g

    p0_header(s, g)
    p1_signals(s)
    st.divider()
    p2_score_whale(s)
    st.divider()
    p3_entropy(s, fx)
    st.divider()
    p4_forex(fx)
    st.divider()
    p5_gate(s, g)
    st.divider()
    cc, cd = st.columns([2,1])
    with cc: p6_journal(tr)
    with cd: p7_health(s, reg)
    st.divider()
    st.caption(
        f"👁 SPEL v40 · {_ts(s.get('updated_utc',''))} | "
        f"R7 read-only | R33 $100k canonical | EF-18 LEVANTADO S36 | "
        f"Gate R30 Day {s.get('day_counter','?')}/63"
    )
    time.sleep(REFRESH)
    st.rerun()

if __name__ == "__main__":
    main()
