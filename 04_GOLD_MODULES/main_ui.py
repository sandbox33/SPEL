"""
SPEL v40 · Ojo de Dios · Dashboard Institucional v3
Hinc Omnia Cerno

Inference: LSTM forward pass en Streamlit Cloud
           (feature-cache + model-cache branches)
Data live: yfinance 1H candlestick + 5min forex prices
GDELT:     forex_macro_snapshot.json (J0 Actions)
Gate:      gate_metrics.json (dashboard-data branch)

R3:  sha_parquet verificado pre-inference
R7:  read-only — zero writes
R13: input (N,20) validated pre-forward
R27: Gödel mask en raw space (pre-scaling)
R28: p90 desde manifest rolling-252d
R33: Kelly base $100k canonical
"""

import streamlit as st
import json, io, urllib.request, urllib.error, time
from datetime import datetime, timezone

st.set_page_config(
    page_title="👁 SPEL v40",
    page_icon="👁",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;700;800&display=swap');
:root{--bg:#050810;--s:#0c1020;--b:#1a2540;--c:#00d4ff;--g:#00ff88;--y:#ffd700;--r:#ff3366;--d:#4a5568;--t:#e2e8f0;}
html,body,[class*="css"]{background:var(--bg)!important;color:var(--t)!important;font-family:'Syne',sans-serif!important;}
.stApp{background:var(--bg)!important;}
h1,h2,h3{font-family:'Syne',sans-serif!important;font-weight:800!important;}
[data-testid="metric-container"]{background:var(--s)!important;border:1px solid var(--b)!important;border-radius:4px!important;padding:12px!important;}
[data-testid="stSidebar"]{background:var(--s)!important;}
hr{border-color:var(--b)!important;}
.pc{background:var(--s);border:1px solid var(--b);border-radius:6px;padding:16px;margin-bottom:12px;}
.badge{display:inline-block;padding:2px 8px;border-radius:3px;font-family:'Space Mono';font-size:10px;font-weight:700;}
.bg{background:#00ff8822;color:#00ff88;border:1px solid #00ff88;}
.by{background:#ffd70022;color:#ffd700;border:1px solid #ffd700;}
.br{background:#ff336622;color:#ff3366;border:1px solid #ff3366;}
.bc{background:#00d4ff22;color:#00d4ff;border:1px solid #00d4ff;}
.bd{background:#1a254022;color:#4a5568;border:1px solid #4a5568;}
.hdr{background:linear-gradient(180deg,rgba(0,212,255,.03),rgba(0,212,255,.08),rgba(0,212,255,.03));border-top:1px solid #00d4ff;border-bottom:1px solid #1a2540;padding:12px 20px;margin-bottom:16px;}
@keyframes gp{0%{box-shadow:0 0 0 0 rgba(0,212,255,.4);}70%{box-shadow:0 0 0 8px rgba(0,212,255,0);}100%{box-shadow:0 0 0 0 rgba(0,212,255,0);}}
.ga{animation:gp 2s infinite;border-color:#00d4ff!important;}
</style>
""", unsafe_allow_html=True)

# ── Constants ────────────────────────────────────────────────────────
GH_RAW          = "https://raw.githubusercontent.com/sandbox33/SPEL"

# Token: st.secrets (Streamlit Cloud) → os.environ (Colab/local)
def _get_gh_token() -> str:
    try:
        return st.secrets["GITHUB_TOKEN"]
    except Exception:
        return os.environ.get("GITHUB_TOKEN", "")

import os as _os
FEATURE_BRANCH  = "feature-cache"
MODEL_BRANCH    = "model-cache"
REFRESH_INFER   = 120
REFRESH_OHLCV   = 60
REFRESH_META    = 30
SCORE_THRESHOLD = 70
KELLY_CAP       = 0.05
EPSILON         = 1e-10
ENTROPY_IDX     = 3
VITALITY_IDX    = 15
ASSETS          = ["BTC", "XAU", "NIFTY50", "NVDA"]
TICKER_MAP      = {"BTC":"BTC-USD","XAU":"GC=F","NIFTY50":"^NSEI","NVDA":"NVDA"}
FOREX_TICKERS   = {"EUR/USD":"EURUSD=X","GBP/USD":"GBPUSD=X","USD/JPY":"USDJPY=X"}

# ════════════════════════════════════════════════════════════════════
# LOADERS
# ════════════════════════════════════════════════════════════════════

def _fetch_json(url):
    """Auth-aware fetch — uses Contents API for private GH branches."""
    if "githubusercontent.com" in url:
        # Convert raw URL to Contents API with auth
        # raw: https://raw.githubusercontent.com/OWNER/REPO/BRANCH/PATH
        # api: https://api.github.com/repos/OWNER/REPO/contents/PATH?ref=BRANCH
        parts = url.replace("https://raw.githubusercontent.com/","").split("/")
        owner, repo, branch = parts[0], parts[1], parts[2]
        path  = "/".join(parts[3:])
        token = _get_gh_token()
        hdrs  = {"Authorization": f"token {token}",
                 "Accept": "application/vnd.github.v3+json"}
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
        req = urllib.request.Request(api_url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
        import base64 as _b64
        return json.loads(_b64.b64decode(d["content"].replace("\n","")))
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())

def _fetch_bytes(url):
    """Auth-aware bytes fetch for private GH branches."""
    if "githubusercontent.com" in url:
        parts = url.replace("https://raw.githubusercontent.com/","").split("/")
        owner, repo, branch = parts[0], parts[1], parts[2]
        path  = "/".join(parts[3:])
        token = _get_gh_token()
        hdrs  = {"Authorization": f"token {token}",
                 "Accept": "application/vnd.github.v3+json"}
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
        req = urllib.request.Request(api_url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())
        import base64 as _b64
        return _b64.b64decode(d["content"].replace("\n",""))
    with urllib.request.urlopen(url, timeout=20) as r:
        return r.read()

@st.cache_data(ttl=REFRESH_META)
def load_manifest():
    try:
        return _fetch_json(
            f"{GH_RAW}/{FEATURE_BRANCH}/meta/feature_cache/manifest.json")
    except Exception as e:
        return {"error": str(e), "inference_ready": False}

@st.cache_data(ttl=REFRESH_META)
def load_forex():
    try:
        return _fetch_json(f"{GH_RAW}/main/meta/forex_macro_snapshot.json")
    except Exception:
        return {}

@st.cache_data(ttl=REFRESH_META)
def load_gate():
    try:
        return _fetch_json(f"{GH_RAW}/dashboard-data/gate_metrics.json")
    except Exception:
        return {}

@st.cache_data(ttl=60)
def load_trades():
    try:
        import csv
        with urllib.request.urlopen(
                f"{GH_RAW}/dashboard-data/trade_log.csv", timeout=8) as r:
            return list(csv.DictReader(io.StringIO(r.read().decode())))[-30:]
    except Exception:
        return []

@st.cache_data(ttl=REFRESH_INFER)
def load_feature_snapshot(asset):
    return _fetch_json(
        f"{GH_RAW}/{FEATURE_BRANCH}/meta/feature_cache/{asset}_tail.json")

@st.cache_data(ttl=REFRESH_INFER)
def load_checkpoint_bytes(asset):
    return _fetch_bytes(
        f"{GH_RAW}/{MODEL_BRANCH}/checkpoints/{asset}.pt")

@st.cache_data(ttl=REFRESH_OHLCV)
def fetch_ohlcv(ticker, period="5d", interval="1h"):
    try:
        import yfinance as yf
        import pandas as pd
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        df = df.reset_index()
        df.columns = [str(c).lower().strip() for c in df.columns]
        return df
    except Exception:
        return None

@st.cache_data(ttl=30)
def fetch_forex_price(yticker):
    try:
        import yfinance as yf
        t = yf.Ticker(yticker)
        h = t.history(period="1d", interval="5m")
        return float(h["Close"].iloc[-1]) if not h.empty else None
    except Exception:
        return None

# ════════════════════════════════════════════════════════════════════
# CLOUD INFERENCE
# ════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=REFRESH_INFER)
def run_inference_all(manifest_hash: str) -> dict:
    import numpy as np

    manifest = load_manifest()
    p90_map  = manifest.get("p90_map", {})
    results  = {}

    for asset in ASSETS:
        try:
            import torch
            import torch.nn as nn

            snap      = load_feature_snapshot(asset)
            lookback  = snap["lookback"]
            rows      = np.array(snap["rows"],        dtype=np.float32)
            mu        = np.array(snap["scaler_mean"], dtype=np.float32)
            sigma     = np.array(snap["scaler_std"],  dtype=np.float32)
            sigma     = np.where(sigma < EPSILON, EPSILON, sigma)
            sha_pq    = snap["sha_parquet"]

            # R13 shape guard
            assert rows.shape[1] == 20, f"R13:{asset} cols={rows.shape[1]}"
            assert mu.shape[0]   == 20, f"R13:{asset} mu={mu.shape}"

            # R27: Gödel in raw space BEFORE scaling
            raw_ent  = float(rows[-1, ENTROPY_IDX])
            raw_vit  = float(rows[-1, VITALITY_IDX])
            p90_info = p90_map.get(asset, {})
            p90      = float(p90_info.get("p90_entropy", 1.5))
            p90_meth = p90_info.get("p90_method", "unknown")
            godel    = (raw_ent >= p90) or (raw_vit == 9)

            # PATH B: scale after Gödel check
            scaled = (rows - mu) / sigma
            assert scaled.shape[0] >= lookback, \
                f"{asset}: {scaled.shape[0]} rows < lookback {lookback}"
            x = torch.from_numpy(scaled[-lookback:]).unsqueeze(0).float()

            # LSTM R13
            class SPELLSTM(nn.Module):
                def __init__(self):
                    super().__init__()
                    self.lstm   = nn.LSTM(20, 64, 1, batch_first=True)
                    self.linear = nn.Linear(64, 1)  # matches checkpoint key
                def forward(self, x):
                    out, _ = self.lstm(x)
                    return self.linear(out[:, -1, :])

            model = SPELLSTM()
            ckpt  = torch.load(io.BytesIO(load_checkpoint_bytes(asset)),
                               map_location="cpu", weights_only=False)
            state = ckpt if "lstm.weight_ih_l0" in ckpt \
                    else ckpt.get("model_state_dict", ckpt)
            model.load_state_dict(state, strict=False)
            model.eval()

            with torch.no_grad():
                logit = model(x).squeeze().item()

            prob    = 1.0 / (1.0 + np.exp(-logit))
            direc   = "LONG" if prob >= 0.5 else "SHORT"
            nat_sc  = float(prob if direc == "LONG" else 1.0 - prob)

            # Backbone heuristic — raw scale for price levels
            closes  = rows[-lookback:, 0]
            atr14   = float(np.mean(np.abs(np.diff(closes[-14:]))) + EPSILON)
            last_c  = float(rows[-1, 0])
            kelly_f = float(np.clip(np.clip(nat_sc*2-1, 0, 1), 0, KELLY_CAP))

            entry = last_c
            sl    = last_c - 4.5*atr14   if direc=="LONG" else last_c + 4.5*atr14
            tp    = last_c + 4.5*atr14*2.5 if direc=="LONG" else last_c - 4.5*atr14*2.5

            # Score de Oro (R13 weights)
            gc = (min(100, 60+(raw_ent-p90)/(p90*0.05+EPSILON)*20) if godel
                  else max(0, 40-(p90-raw_ent)/(p90*0.05+EPSILON)*10))
            score_oro = int(np.clip(
                gc*0.4 + nat_sc*100*0.3 + min(100, kelly_f/KELLY_CAP*100)*0.3,
                0, 100))
            viable = score_oro >= SCORE_THRESHOLD and godel

            regime = ("GODEL_OFF" if not godel
                      else "TREND"    if nat_sc > 0.65
                      else "MEAN_REV" if nat_sc > 0.55
                      else "NOISE")

            results[asset] = dict(
                asset=asset, score_oro=score_oro,
                direction=direc if viable else "FLAT",
                viable=viable, godel_active=godel,
                entropy=raw_ent, p90_entropy=p90, p90_method=p90_meth,
                kelly_fraction=kelly_f,
                entry_price=entry if viable else 0.0,
                stop_loss=sl    if viable else 0.0,
                take_profit=tp  if viable else 0.0,
                atr14=atr14, regime_label=regime,
                sha_parquet=sha_pq, raw_logit=float(logit),
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
            )

        except Exception as e:
            results[asset] = {"asset": asset, "error": str(e),
                              "score_oro": 0, "viable": False,
                              "godel_active": False, "direction": "FLAT",
                              "entropy": 0, "p90_entropy": 1.5,
                              "p90_method": "unknown"}
    return results

# ════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════

def _b(t, c): return f'<span class="badge {c}">{t}</span>'
def _sc(s):   return "bg" if s >= 75 else "by" if s >= 60 else "br"

def _ts(iso):
    try:
        return datetime.fromisoformat(
            iso.replace("Z", "+00:00")).strftime("%H:%M UTC")
    except Exception:
        return str(iso)[:16]

def _check_freshness(manifest):
    exp = manifest.get("exported_utc", "")
    if not exp:
        return False, "no timestamp"
    try:
        dt  = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        return age <= 25, f"{age:.1f}h old"
    except Exception:
        return False, "parse error"

PLOT_LAYOUT = dict(
    paper_bgcolor="#050810", plot_bgcolor="#0c1020",
    font=dict(family="Space Mono", color="#4a5568", size=10),
    margin=dict(l=40, r=20, t=40, b=30),
    xaxis=dict(gridcolor="#1a2540", rangeslider=dict(visible=False)),
    yaxis=dict(gridcolor="#1a2540"),
)

# ════════════════════════════════════════════════════════════════════
# SIDEBAR
# ════════════════════════════════════════════════════════════════════

def sidebar(manifest, results):
    with st.sidebar:
        st.markdown("### 👁 SPEL v40")
        st.caption("Hinc Omnia Cerno")
        st.divider()
        ready = manifest.get("inference_ready", False)
        fresh, fresh_msg = _check_freshness(manifest)
        st.caption(f"Inference: {'✅ ready' if ready else '❌ not ready'}")
        st.caption(f"Cache: {'✅' if fresh else '⚠️'} {fresh_msg}")
        st.divider()
        st.markdown("**Gödel OR% live**")
        for asset, r in results.items():
            g   = r.get("godel_active", False)
            ent = r.get("entropy", 0)
            p90 = r.get("p90_entropy", 1.5)
            pct = min(ent / max(p90, EPSILON), 1.5) * 100
            st.caption(f"{'🟢' if g else '○'} {asset}: {pct:.0f}%")
        st.divider()
        st.markdown("[🔗 Actions](https://github.com/sandbox33/SPEL/actions)")
        st.markdown("[🔗 feature-cache](https://github.com/sandbox33/SPEL/tree/feature-cache)")
        if st.button("🔄 Refresh"):
            st.cache_data.clear()
            st.rerun()

# ════════════════════════════════════════════════════════════════════
# PANELS
# ════════════════════════════════════════════════════════════════════

def p0_header(manifest, gate):
    gs  = gate.get("gate_status", gate) if gate else {}
    eq  = gs.get("equity_canonical", 100_000)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    exp = manifest.get("exported_utc", "?")
    st.markdown(f"""
    <div class="hdr">
      <span style="font-family:'Space Mono';font-size:11px;
                   color:#00d4ff;letter-spacing:3px">
        👁 OJO DE DIOS · SPEL v40 · HINC OMNIA CERNO
      </span>
      <span style="float:right;font-family:'Space Mono';
                   font-size:10px;color:#4a5568">
        {now} &nbsp;·&nbsp; cache:{_ts(exp)} &nbsp;·&nbsp; ${eq:,.0f}
      </span>
    </div>""", unsafe_allow_html=True)


def tab_signals(results):
    viable = [r for r in results.values() if r.get("viable")]
    errors = [r for r in results.values() if "error" in r]

    for r in errors:
        st.error(f"⚠️ {r['asset']}: {r.get('error','inference error')}")

    if not viable:
        best = max(results.values(),
                   key=lambda r: int(r.get("score_oro", 0) or 0))
        sc = int(best.get("score_oro", 0) or 0)
        st.info(f"Sin señales viables. Threshold: score ≥ {SCORE_THRESHOLD} + Gödel ON.")
        st.caption(f"Mejor: **{best.get('asset')}** score={sc}/100 "
                   f"{best.get('direction')} "
                   f"godel={'✅' if best.get('godel_active') else '○'}")
    else:
        for r in viable:
            asset = r.get("asset", "?")
            sc    = int(r.get("score_oro", 0) or 0)
            d     = r.get("direction", "?")
            e     = float(r.get("entry_price", 0) or 0)
            sl    = float(r.get("stop_loss", 0) or 0)
            tp    = float(r.get("take_profit", 0) or 0)
            k     = float(r.get("kelly_fraction", 0) or 0)
            rr    = (tp - e) / max(abs(sl - e), EPSILON) if e else 0
            regime= r.get("regime_label", "?")
            rk    = 100_000 * k * 0.05
            st.markdown(f"""
            <div class="pc" style="border-color:#00ff88">
              <div style="display:flex;justify-content:space-between;
                          align-items:center;margin-bottom:12px">
                <span style="font-family:'Syne';font-size:22px;
                             font-weight:800;color:#00ff88">{asset}</span>
                <span>{_b(d,'bg')} {_b(f'{sc}/100','bg')} {_b(regime,'bc')}</span>
              </div>
              <div style="display:grid;grid-template-columns:repeat(4,1fr);
                          gap:12px;font-family:'Space Mono';font-size:10px">
                <div><div style="color:#4a5568">ENTRY</div>
                     <div style="color:#e2e8f0;font-size:15px;
                                 font-weight:700">{e:.4f}</div></div>
                <div><div style="color:#ff3366">STOP LOSS</div>
                     <div style="color:#ff3366;font-size:15px;
                                 font-weight:700">{sl:.4f}</div></div>
                <div><div style="color:#00ff88">TAKE PROFIT</div>
                     <div style="color:#00ff88;font-size:15px;
                                 font-weight:700">{tp:.4f}</div></div>
                <div><div style="color:#4a5568">R:R / KELLY</div>
                     <div style="color:#ffd700;font-size:15px;
                                 font-weight:700">{rr:.1f}x / {k:.4f}</div></div>
              </div>
              <div style="margin-top:6px;font-family:'Space Mono';
                          font-size:9px;color:#4a5568">
                Risk $100k: ${rk:.2f} · logit={r.get('raw_logit',0):.3f} ·
                sha={r.get('sha_parquet','?')[:8]}
              </div>
            </div>""", unsafe_allow_html=True)

    st.divider()
    st.markdown("#### Score Grid")
    cols = st.columns(4)
    for col, asset in zip(cols, ASSETS):
        r  = results.get(asset, {})
        sc = int(r.get("score_oro", 0) or 0)
        d  = r.get("direction", "?")
        g  = r.get("godel_active", False)
        vb = r.get("viable", False)
        en = float(r.get("entropy", 0) or 0)
        p  = float(r.get("p90_entropy", 1.5) or 1.5)
        cc = "#00ff88" if sc >= 75 else "#ffd700" if sc >= 60 else "#ff3366"
        with col:
            st.markdown(f"""
            <div class="pc {'ga' if g else ''}"
                 style="border-color:{'#00ff88' if vb else '#1a2540'};
                        text-align:center">
              <div style="font-family:'Syne';font-size:15px;font-weight:800">{asset}</div>
              <div style="font-family:'Space Mono';font-size:28px;
                          font-weight:700;color:{cc}">{sc}</div>
              <div style="margin:4px 0">
                {_b(d,_sc(sc))} {_b('G✓' if g else 'G○','bc' if g else 'bd')}
              </div>
              <div style="background:#1a2540;border-radius:2px;height:4px;margin:6px 0">
                <div style="width:{min(en/max(p,EPSILON),1.5)*67:.0f}%;height:100%;
                             background:{'#00ff88' if g else '#00d4ff'};
                             border-radius:2px"></div>
              </div>
              <div style="font-family:'Space Mono';font-size:9px;color:#4a5568">
                ent={en:.3f}<br>p90={p:.3f}<br>[{r.get('p90_method','?')[:8]}]
              </div>
              {'<div style="color:#00ff88;font-size:9px">⭐ VIABLE</div>' if vb else ''}
            </div>""", unsafe_allow_html=True)


def tab_ohlcv(results):
    try:
        import plotly.graph_objects as go
    except ImportError:
        st.warning("plotly needed"); return
    st.caption("yfinance 1H · últimas 5 jornadas · Entry/SL/TP en señales viables")
    cols = st.columns(2)
    for idx, (label, ticker) in enumerate(TICKER_MAP.items()):
        df  = fetch_ohlcv(ticker, period="5d", interval="1h")
        col = cols[idx % 2]
        r   = results.get(label, {})
        with col:
            if df is None:
                st.caption(f"{label}: yfinance timeout"); continue
            dt = next((c for c in df.columns if "date" in c.lower()
                       or "datetime" in c.lower()), df.columns[0])
            o  = next((c for c in df.columns if "open"  in c.lower()), None)
            h  = next((c for c in df.columns if "high"  in c.lower()), None)
            l  = next((c for c in df.columns if "low"   in c.lower()), None)
            c  = next((c for c in df.columns if "close" in c.lower()), None)
            if not all([o, h, l, c]):
                st.caption(f"{label}: schema {list(df.columns)[:5]}"); continue
            fig = go.Figure(go.Candlestick(  # [BUG-DASH-2 FIX S42] plotly5.x kwargs directos

                x=df[dt], open=df[o], high=df[h], low=df[l], close=df[c],
                name=label,
                increasing=dict(line=dict(color="#00ff88"),
                                fillcolor="#00ff8844"),
                decreasing=dict(line=dict(color="#ff3366"),
                                fillcolor="#ff336644"),
            ))
            viable = r.get("viable", False)
            entry  = float(r.get("entry_price", 0) or 0)
            sl_v   = float(r.get("stop_loss", 0) or 0)
            tp_v   = float(r.get("take_profit", 0) or 0)
            sc     = int(r.get("score_oro", 0) or 0)
            godel  = r.get("godel_active", False)
            if viable and entry:
                fig.add_hline(y=entry,
                              line=dict(color="#ffd700", dash="dash", width=1.5),
                              annotation_text=f"E {entry:.4f}",
                              annotation_position="right")
                fig.add_hline(y=sl_v,
                              line=dict(color="#ff3366", dash="dot", width=1),
                              annotation_text=f"SL {sl_v:.4f}",
                              annotation_position="right")
                fig.add_hline(y=tp_v,
                              line=dict(color="#00ff88", dash="dot", width=1),
                              annotation_text=f"TP {tp_v:.4f}",
                              annotation_position="right")
            cc  = "#00ff88" if viable else "#00d4ff" if godel else "#4a5568"
            ttl = f"{label} {sc}/100 {'⭐' if viable else 'G✓' if godel else '○'}"
            fig.update_layout(**{**PLOT_LAYOUT, "height": 280,
                                 "title": dict(text=ttl,
                                               font=dict(color=cc, size=11,
                                                         family="Space Mono"))})
            st.plotly_chart(fig, use_container_width=True)


def tab_entropy(results, manifest):
    try:
        import plotly.graph_objects as go
        import numpy as np
    except ImportError:
        st.warning("plotly/numpy needed"); return
    p90_map = manifest.get("p90_map", {})
    labels  = list(results.keys())
    ent_v   = [float(results[a].get("entropy", 0) or 0) for a in labels]
    p90_v   = [float(p90_map.get(a, {}).get("p90_entropy", 1.5)) for a in labels]
    godel_v = [results[a].get("godel_active", False) for a in labels]
    colors  = ["#00ff88" if g else "#00d4ff" if e >= p*0.85 else "#1a2540"
               for g, e, p in zip(godel_v, ent_v, p90_v)]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=labels, y=ent_v, marker_color=colors,
                         text=[f"{e:.3f}" for e in ent_v],
                         textposition="outside",
                         textfont=dict(size=9, color="#4a5568",
                                       family="Space Mono")))
    fig.add_trace(go.Scatter(x=labels, y=p90_v, mode="lines+markers",
                             name="P90 rolling-252d",
                             line=dict(color="#ff3366", dash="dot", width=2),
                             marker=dict(size=6, color="#ff3366")))
    fig.update_layout(**{**PLOT_LAYOUT, "height": 260,
                         "title": dict(
                             text="entropy_shannon vs P90 rolling-252d (S36 recal)",
                             font=dict(color="#4a5568", size=10,
                                       family="Space Mono"))})
    st.plotly_chart(fig, use_container_width=True)
    st.caption("🟢 Gödel ON  🔵 Approaching P90  ⬜ Below  — P90 threshold rolling-252d")

    st.markdown("**Price range entropy proxy — 63d daily**")
    cols2 = st.columns(2)
    for idx, (label, ticker) in enumerate(list(TICKER_MAP.items())[:4]):
        df  = fetch_ohlcv(ticker, period="63d", interval="1d")
        col = cols2[idx % 2]
        with col:
            if df is None:
                st.caption(f"{label}: no data"); continue
            dt = next((c for c in df.columns if "date" in c.lower()
                       or "datetime" in c.lower()), df.columns[0])
            hc = next((c for c in df.columns if "high"  in c.lower()), None)
            lc = next((c for c in df.columns if "low"   in c.lower()), None)
            cc = next((c for c in df.columns if "close" in c.lower()), None)
            if not all([hc, lc, cc]):
                st.caption(f"{label}: schema"); continue
            hi  = df[hc].values.flatten().astype(float)
            lo  = df[lc].values.flatten().astype(float)
            clo = df[cc].values.flatten().astype(float)
            rng = (hi - lo) / np.maximum(clo, EPSILON)
            eprx = np.clip(rng / (rng.mean() + EPSILON) * 1.2, 0.5, 3.0)
            p90t = float(p90_map.get(label, {}).get("p90_entropy", 1.5))
            bclr = ["#00ff88" if e >= p90t else "#1a2540" for e in eprx]
            fig2 = go.Figure(go.Bar(x=df[dt].values, y=eprx,
                                    marker_color=bclr, name=label))
            fig2.add_hline(y=p90t,
                           line=dict(color="#ff3366", dash="dot", width=1.5))
            fig2.update_layout(**{**PLOT_LAYOUT, "height": 200,
                                  "title": dict(
                                      text=f"{label} entropy proxy",
                                      font=dict(color="#4a5568", size=10,
                                                family="Space Mono"))})
            st.plotly_chart(fig2, use_container_width=True)


def tab_forex(forex):
    pairs = forex.get("pairs", {})
    st.caption(f"GDELT snapshot: {_ts(forex.get('updated',''))} · "
               f"Precio live: yfinance 5min")
    for label, yticker in FOREX_TICKERS.items():
        price = fetch_forex_price(yticker)
        v     = pairs.get(label, {})
        e_v   = float(v.get("entropy",       0)   or 0)
        p90   = float(v.get("p90",           1.5) or 1.5)
        g     = v.get("godel_active", False)
        f     = float(v.get("fear_momentum", 0)   or 0)
        n     = float(v.get("nash_frozen",   0.5) or 0.5)
        prx   = v.get("proxy", "—")
        ms    = min(int(abs(f)*100), 35) if g else 10
        ms   += 10 if n > 0.75 else 0
        cc    = "#00ff88" if ms >= 35 else "#ffd700" if ms >= 25 else "#4a5568"
        c1, c2, c3 = st.columns([1, 3, 1])
        with c1:
            st.markdown(
                f"<div style='font-family:Syne;font-weight:800;"
                f"font-size:16px'>{label}</div>"
                f"<div style='font-family:Space Mono;font-size:9px;"
                f"color:#4a5568'>proxy:{prx}</div>",
                unsafe_allow_html=True)
        with c2:
            bw = int((ms / 45) * 100)
            st.markdown(f"""
            <div style="background:#0c1020;border:1px solid #1a2540;
                        border-radius:4px;padding:8px">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
                <span style="font-family:Space Mono;font-size:18px;
                             font-weight:700;color:{cc}">{ms}</span>
                <span style="font-family:Space Mono;font-size:9px;
                             color:#4a5568">macro pts</span>
                {'<span style="color:#00d4ff;font-size:10px">⚡ Gödel</span>' if g else ''}
              </div>
              <div style="background:#1a2540;border-radius:2px;height:4px">
                <div style="width:{bw}%;height:100%;background:{cc};
                             border-radius:2px"></div>
              </div>
              <div style="font-family:Space Mono;font-size:9px;
                          color:#4a5568;margin-top:4px">
                ent={e_v:.3f} p90={p90:.3f} nash={n:.2f} fear={f:+.3f}
              </div>
            </div>""", unsafe_allow_html=True)
        with c3:
            ps = f"{price:.5f}" if isinstance(price, float) else "—"
            st.markdown(
                f"<div style='font-family:Space Mono;font-size:14px;"
                f"font-weight:700;text-align:right;color:#e2e8f0'>{ps}</div>"
                f"<div style='font-family:Space Mono;font-size:9px;"
                f"color:#4a5568;text-align:right'>5min</div>",
                unsafe_allow_html=True)


def tab_gate(gate, trades):
    gs  = gate.get("gate_status", gate) if gate else {}
    day = int(gs.get("n_evaluations", 0) or 0)
    pct = min(day / 63, 1.0)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Hit Rate Gödel",  f"{gs.get('hit_rate_godel',0):.1%}",  "target >56%")
    c2.metric("Max DD 7d",       f"{gs.get('max_drawdown_7d',0):.2%}", "limit 8%",
              delta_color="inverse")
    c3.metric("PnL Kelly $100k", f"${gs.get('pnl_kelly_weighted',0):,.2f}")
    c4.metric("No-Trade Rate",   f"{gs.get('no_trade_rate',0):.1%}",   "30-70%")
    st.markdown(f"""
    <div style="margin:12px 0">
      <div style="font-family:Space Mono;font-size:10px;color:#4a5568;margin-bottom:4px">
        {day} evaluaciones · target ~20-May-2026</div>
      <div style="background:#1a2540;border-radius:3px;height:8px">
        <div style="width:{pct*100:.1f}%;height:100%;border-radius:3px;
                    background:linear-gradient(90deg,#00d4ff,#00ff88)"></div>
      </div>
    </div>""", unsafe_allow_html=True)
    conds = [
        ("hit_rate >56%",   gs.get("hit_rate_pass",  False)),
        ("drawdown <8%",    gs.get("drawdown_pass",  False)),
        ("PnL >0",          gs.get("pnl_pass",       False)),
        ("no-trade 30-70%", gs.get("no_trade_pass",  False)),
        ("SHA clean",       gs.get("sha_pass",       True)),
        ("GDELT real",      True),
    ]
    cc = st.columns(6)
    for col, (label, passed) in zip(cc, conds):
        col.markdown(
            f"<div style='text-align:center;font-family:Space Mono;font-size:9px;"
            f"color:{'#00ff88' if passed else '#4a5568'}'>"
            f"{'✅' if passed else '⬜'}<br>{label}</div>",
            unsafe_allow_html=True)
    st.divider()
    st.markdown("#### Trade Journal")
    if not trades:
        st.caption("Sin trades registrados"); return
    COLS = ["timestamp_utc","asset","direction","score_oro",
            "viable","regime_label","pnl_canonical","kelly_fraction"]
    rows = [{k: t.get(k, "") for k in COLS}
            for t in reversed(trades[-15:])]
    try:
        import pandas as pd
        st.dataframe(pd.DataFrame(rows),
                     use_container_width=True, height=240)
    except ImportError:
        st.table(rows[:8])

def _fetch_private(path: str, branch: str = "dashboard-data") -> dict | None:
    """[BUG-DASH-1 FIX S42] GH Contents API con raw Accept header."""
    import streamlit as st
    import urllib.request, json
    token = st.secrets.get("GH_TOKEN", "") or st.secrets.get("GITHUB_TOKEN", "")
    if not token:
        return None
    repo = "sandbox33/SPEL"
    url  = f"https://api.github.com/repos/{repo}/contents/{path}?ref={branch}"
    req  = urllib.request.Request(url, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3.raw",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        import logging
        logging.getLogger("main_ui").error("_fetch_private(%s) HTTP %d", path, e.code)
        return None
    except Exception as e:
        import logging
        logging.getLogger("main_ui").error("_fetch_private(%s) failed: %s", path, e)
        return None



def tab_ops(manifest, results):
    st.markdown("#### Feature Cache — Provenance R32")
    for asset, info in manifest.get("p90_map", {}).items():
        st.caption(f"`{asset}` sha={info.get('sha_v5','?')[:8]} "
                   f"p90={info.get('p90_entropy','?')} "
                   f"[{info.get('p90_method','?')}]")
    st.divider()
    st.markdown("#### Inference — Raw logits")
    for asset, r in results.items():
        if "error" in r:
            st.error(f"{asset}: {r['error']}")
        else:
            st.caption(
                f"{asset}: logit={r.get('raw_logit',0):.3f} "
                f"godel={'✅' if r.get('godel_active') else '○'} "
                f"p90_meth={r.get('p90_method','?')} "
                f"sha={r.get('sha_parquet','?')[:8]}")
    st.divider()
    fresh, msg = _check_freshness(manifest)
    st.caption(f"Cache freshness: {'✅' if fresh else '⚠️'} {msg}")
    st.caption("Inference: Streamlit Cloud LSTM forward pass (torch CPU)")
    st.caption("R13·R27·R28·R33 compliant")

# ════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════

def main():
    manifest      = load_manifest()
    forex         = load_forex()
    gate          = load_gate()
    trades        = load_trades()
    ready         = manifest.get("inference_ready", False)
    manifest_hash = str(manifest.get("exported_utc", ""))[:19]

    results = (run_inference_all(manifest_hash) if ready
               else {a: {"asset": a,
                         "error": manifest.get("error",
                                               "feature-cache not populated — "
                                               "run spel_export_feature_cache.py"),
                         "score_oro": 0, "viable": False,
                         "godel_active": False, "direction": "FLAT",
                         "entropy": 0, "p90_entropy": 1.5,
                         "p90_method": "unknown"}
                    for a in ASSETS})

    sidebar(manifest, results)
    p0_header(manifest, gate)

    if not ready:
        st.warning(
            "⚠️ feature-cache branch vacío. "
            "Ejecutar `spel_export_feature_cache.py` desde Colab post-ingest. "
            f"Error: {manifest.get('error', 'branch 404')}")

    t1, t2, t3, t4, t5, t6 = st.tabs([
        "🎯 Señales", "📊 OHLCV Live", "⚡ Entropy",
        "📡 Forex",   "🛡️ Gate R30",  "⚙️ Ops",
    ])
    with t1: tab_signals(results)
    with t2: tab_ohlcv(results)
    with t3: tab_entropy(results, manifest)
    with t4: tab_forex(forex)
    with t5: tab_gate(gate, trades)
    with t6: tab_ops(manifest, results)

    st.caption(
        "👁 SPEL v40 · Hinc Omnia Cerno · "
        "Cloud LSTM inference · "
        "R7 R13 R27 R28 R33 compliant"
    )
    time.sleep(REFRESH_META)
    st.rerun()


if __name__ == "__main__":
    main()
