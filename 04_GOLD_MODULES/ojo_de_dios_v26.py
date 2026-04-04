"""
ojo_de_dios_v26.py — SPEL S31
==============================
Dashboard Flask con:
  - Score Grid (4 activos core)
  - Forex HUD (entropy per-pair post-PC1-fix)
  - Data Health (SHA / Drift / Hurst / Gödel OR%)
  - Audit Button (dispara MASTER_AUDITOR_v2 en background)
  - Auto-Log (últimas 10 entradas de change_log.json)
  - Trade Journal (trade_log.csv últimos trades)
  - ngrok auto-tunnel para acceso móvil

Uso en Colab:
    exec(open(ROOT/'scripts/ojo_de_dios_v26.py').read())
    # URL ngrok aparece en el output
"""

import os, sys, json, csv, threading, subprocess, time, importlib.util
from pathlib import Path
from datetime import datetime, timezone
from flask import Flask, jsonify, render_template_string

ROOT = Path(os.environ.get("SPEL_BASE_DIR",
            "/content/drive/MyDrive/ORDEN/SPEL 3.0"))

for p in [str(ROOT/"scripts"), str(ROOT/"codigo/core")]:
    if p not in sys.path:
        sys.path.insert(0, p)

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

# ── Audit state (shared across threads) ───────────────────────────
_audit_state = {
    "running":   False,
    "last_run":  None,
    "result":    None,
    "ts":        None,
}
_audit_lock = threading.Lock()


# ── HTML template — dark terminal aesthetic ────────────────────────
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>SPEL · Ojo de Dios v26</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;600;700&family=Space+Grotesk:wght@300;500;700&display=swap');

  :root {
    --bg:       #050510;
    --surface:  #0a0a1f;
    --border:   #1a1a3a;
    --accent:   #4040ff;
    --cyan:     #00d4ff;
    --green:    #00ff9d;
    --yellow:   #ffd700;
    --red:      #ff3355;
    --orange:   #ff8800;
    --muted:    #3a3a6a;
    --text:     #c8c8ff;
    --dim:      #5a5a8a;
    --font-mono: 'JetBrains Mono', monospace;
    --font-ui:   'Space Grotesk', sans-serif;
  }

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-mono);
    font-size: 13px;
    min-height: 100vh;
    padding: 12px;
    overflow-x: hidden;
  }

  /* ── Header ── */
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid var(--border);
    padding-bottom: 10px;
    margin-bottom: 14px;
    flex-wrap: wrap;
    gap: 8px;
  }
  .logo {
    font-family: var(--font-ui);
    font-size: 20px;
    font-weight: 700;
    letter-spacing: 4px;
    background: linear-gradient(135deg, var(--accent), var(--cyan));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .logo-sub { font-size: 10px; color: var(--dim); letter-spacing: 2px; }
  .status-dot {
    display: inline-block; width: 8px; height: 8px;
    border-radius: 50%; background: var(--green);
    box-shadow: 0 0 8px var(--green);
    animation: pulse 2s infinite;
  }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.4; } }

  .ts { color: var(--dim); font-size: 11px; }

  /* ── Grid ── */
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 10px;
    margin-bottom: 12px;
  }

  /* ── Panel ── */
  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 14px;
    position: relative;
    overflow: hidden;
  }
  .panel::before {
    content: '';
    position: absolute; top: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, var(--accent), var(--cyan), transparent);
  }
  .panel-title {
    font-family: var(--font-ui);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 3px;
    text-transform: uppercase;
    color: var(--accent);
    margin-bottom: 12px;
  }

  /* ── Asset card ── */
  .asset-card {
    padding: 10px;
    border: 1px solid var(--border);
    border-radius: 5px;
    margin-bottom: 8px;
    background: rgba(0,0,0,0.3);
  }
  .asset-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 6px;
  }
  .asset-name { font-weight: 700; font-size: 15px; font-family: var(--font-ui); }
  .score-bar-wrap {
    height: 5px; background: var(--border); border-radius: 3px;
    margin: 6px 0;
  }
  .score-bar { height: 5px; border-radius: 3px; transition: width 0.6s ease; }
  .score-green  { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .score-yellow { background: var(--yellow); box-shadow: 0 0 6px var(--yellow); }
  .score-red    { background: var(--red); }

  .row { display: flex; justify-content: space-between; font-size: 11px; color: var(--dim); margin-top: 4px; }
  .row span { color: var(--text); }

  /* ── Chips ── */
  .chip {
    display: inline-block; padding: 2px 8px; border-radius: 3px;
    font-size: 10px; font-weight: 700; letter-spacing: 1px;
  }
  .chip-green  { background: rgba(0,255,157,0.15); color: var(--green); border: 1px solid rgba(0,255,157,0.3); }
  .chip-yellow { background: rgba(255,215,0,0.12); color: var(--yellow); border: 1px solid rgba(255,215,0,0.3); }
  .chip-red    { background: rgba(255,51,85,0.12); color: var(--red);   border: 1px solid rgba(255,51,85,0.3); }
  .chip-cyan   { background: rgba(0,212,255,0.12); color: var(--cyan);  border: 1px solid rgba(0,212,255,0.3); }
  .chip-dim    { background: rgba(58,58,106,0.4); color: var(--dim);    border: 1px solid var(--muted); }

  /* ── Forex table ── */
  .fx-table { width: 100%; border-collapse: collapse; font-size: 11px; }
  .fx-table th {
    color: var(--dim); text-align: left; padding: 4px 6px;
    border-bottom: 1px solid var(--border); font-weight: 400;
  }
  .fx-table td { padding: 5px 6px; border-bottom: 1px solid rgba(26,26,58,0.5); }
  .fx-table tr:last-child td { border-bottom: none; }
  .ent-bar-wrap {
    width: 60px; height: 4px; background: var(--border);
    border-radius: 2px; display: inline-block; vertical-align: middle;
    margin-left: 4px;
  }
  .ent-bar { height: 4px; border-radius: 2px; background: var(--cyan); }

  /* ── Health grid ── */
  .health-row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 5px 0; border-bottom: 1px solid rgba(26,26,58,0.5);
    font-size: 11px;
  }
  .health-row:last-child { border-bottom: none; }
  .health-label { color: var(--dim); min-width: 90px; }
  .health-val   { font-weight: 600; }

  /* ── Change log ── */
  .log-entry {
    padding: 6px 0; border-bottom: 1px solid rgba(26,26,58,0.4);
    font-size: 11px; display: flex; gap: 8px; align-items: flex-start;
  }
  .log-entry:last-child { border-bottom: none; }
  .log-ts { color: var(--dim); min-width: 70px; }
  .log-event { font-weight: 600; min-width: 110px; }
  .log-detail { color: var(--dim); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* ── Audit button ── */
  .audit-btn {
    background: transparent;
    border: 2px solid var(--accent);
    color: var(--accent);
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 2px;
    padding: 10px 20px;
    border-radius: 5px;
    cursor: pointer;
    transition: all 0.2s;
    text-transform: uppercase;
    width: 100%;
    margin-top: 8px;
  }
  .audit-btn:hover { background: var(--accent); color: #fff; box-shadow: 0 0 20px rgba(64,64,255,0.4); }
  .audit-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .audit-btn.running { border-color: var(--cyan); color: var(--cyan); animation: blink 0.8s infinite; }
  @keyframes blink { 0%,100%{opacity:1;} 50%{opacity:0.3;} }

  /* ── Audit result ── */
  .audit-result { margin-top: 10px; font-size: 11px; }
  .audit-line { padding: 3px 0; }
  .audit-ok   { color: var(--green); }
  .audit-warn { color: var(--yellow); }
  .audit-crit { color: var(--red); }

  /* ── Trade log ── */
  .trade-entry {
    padding: 7px 0; border-bottom: 1px solid rgba(26,26,58,0.4);
    font-size: 11px;
  }
  .trade-entry:last-child { border-bottom: none; }
  .trade-asset { font-weight: 700; font-family: var(--font-ui); }
  .regime-label { font-size: 10px; padding: 1px 5px; border-radius: 2px; }
  .regime-trend    { background: rgba(0,255,157,0.12); color: var(--green); }
  .regime-meanrev  { background: rgba(0,212,255,0.12); color: var(--cyan); }
  .regime-noise    { background: rgba(255,136,0,0.12); color: var(--orange); }
  .regime-godeloff { background: rgba(58,58,106,0.4);  color: var(--muted); }

  /* ── Footer ── */
  .footer {
    margin-top: 14px; padding-top: 10px;
    border-top: 1px solid var(--border);
    font-size: 10px; color: var(--muted);
    display: flex; justify-content: space-between;
    flex-wrap: wrap; gap: 4px;
  }

  /* Mobile adjustments */
  @media (max-width: 480px) {
    body { padding: 8px; font-size: 12px; }
    .grid { grid-template-columns: 1fr; }
    .logo { font-size: 16px; }
  }
</style>
</head>
<body>

<div class="header">
  <div>
    <div class="logo">👁 OJO DE DIOS</div>
    <div class="logo-sub">SPEL v37 · S31 · <span class="status-dot"></span> LIVE</div>
  </div>
  <div class="ts" id="clock">—</div>
</div>

<div class="grid" id="score-grid">
  <!-- Populated by JS -->
  <div class="panel" style="grid-column: 1/-1; text-align:center; color:var(--dim); padding:30px;">
    Cargando scores...
  </div>
</div>

<div class="grid">

  <!-- Forex HUD -->
  <div class="panel" style="grid-column: span 2;">
    <div class="panel-title">📡 Forex Macro · Entropy per-pair</div>
    <table class="fx-table" id="forex-table">
      <thead>
        <tr>
          <th>Par</th><th>Entropy</th><th>P90</th><th>Gödel</th><th>Proxy</th><th>Vitality</th>
        </tr>
      </thead>
      <tbody id="forex-body">
        <tr><td colspan="6" style="color:var(--dim);padding:12px;">Cargando...</td></tr>
      </tbody>
    </table>
  </div>

  <!-- Data Health -->
  <div class="panel">
    <div class="panel-title">🏥 Data Health</div>
    <div id="health-content" style="color:var(--dim)">Cargando...</div>
  </div>

  <!-- Audit panel -->
  <div class="panel">
    <div class="panel-title">🔍 MASTER AUDITOR</div>
    <button class="audit-btn" id="audit-btn" onclick="runAudit()">
      ⚡ EJECUTAR AUDIT
    </button>
    <div class="audit-result" id="audit-result"></div>
  </div>

</div>

<div class="grid">

  <!-- Change log -->
  <div class="panel" style="grid-column: span 2;">
    <div class="panel-title">📋 System Log · últimas 10 entradas</div>
    <div id="changelog-content" style="color:var(--dim)">Cargando...</div>
  </div>

  <!-- Trade Journal -->
  <div class="panel" style="grid-column: span 2;">
    <div class="panel-title">📊 Trade Journal · papel</div>
    <div id="trade-journal" style="color:var(--dim)">Cargando...</div>
  </div>

</div>

<div class="footer">
  <span>SPEL 3.0 · Schema v5.1 · Polars · PATH B</span>
  <span id="paper-day">Día ? / 63 paper trading</span>
  <span>Auto-refresh 30s</span>
</div>

<script>
// ── Clock ─────────────────────────────────────────────────────────
function updateClock() {
  document.getElementById('clock').textContent =
    new Date().toISOString().slice(0,16).replace('T',' ') + ' UTC';
}
setInterval(updateClock, 1000);
updateClock();

// ── Score color ───────────────────────────────────────────────────
function scoreClass(s) {
  return s >= 70 ? 'score-green' : s >= 60 ? 'score-yellow' : 'score-red';
}
function chipClass(s) {
  return s >= 70 ? 'chip-green' : s >= 60 ? 'chip-yellow' : 'chip-red';
}
function hurstChip(h) {
  if (h > 0.55) return '<span class="chip chip-green">TREND</span>';
  if (h < 0.45) return '<span class="chip chip-cyan">MEAN_REV</span>';
  return '<span class="chip chip-dim">NOISE</span>';
}
function godelChip(g) {
  return g ? '<span class="chip chip-green">G+</span>'
           : '<span class="chip chip-dim">G-</span>';
}
function dirChip(d) {
  return d === 'LONG'
    ? '<span class="chip chip-green">▲ LONG</span>'
    : '<span class="chip chip-red">▼ SHORT</span>';
}

// ── Load scores ───────────────────────────────────────────────────
function loadScores() {
  fetch('/api/scores').then(r=>r.json()).then(data=>{
    const grid = document.getElementById('score-grid');
    if (!data.length) return;
    grid.innerHTML = '';
    data.forEach(a => {
      const sc  = a.score_oro || 0;
      const bar = Math.min(sc, 100);
      const cls = scoreClass(sc);
      const el  = document.createElement('div');
      el.className = 'panel';
      el.innerHTML = `
        <div class="asset-header">
          <span class="asset-name">${a.asset}</span>
          <span class="chip ${chipClass(sc)}">${sc}/100</span>
        </div>
        <div class="score-bar-wrap">
          <div class="score-bar ${cls}" style="width:${bar}%"></div>
        </div>
        <div class="row">Direction ${dirChip(a.direction)}</div>
        <div class="row">Gödel ${godelChip(a.godel_active)} &nbsp; Hurst ${hurstChip(a.hurst)}</div>
        <div class="row"><span style="color:var(--dim)">S=</span><span>${(a.entropy||0).toFixed(4)}</span>
             &nbsp; <span style="color:var(--dim)">P90=</span><span>${(a.p90||0).toFixed(4)}</span></div>
        <div class="row"><span style="color:var(--dim)">Kelly</span><span>${(a.kelly||0).toFixed(4)}</span>
             &nbsp; <span style="color:var(--dim)">modo</span><span>${a.modo||'?'}</span></div>
        <div class="row"><span style="color:var(--dim)">Entry</span><span>${a.entry||'—'}</span></div>
        <div class="row" style="margin-top:4px">
          <span style="color:var(--dim);font-size:10px">sha</span>
          <code style="font-size:10px;color:var(--cyan)">${a.sha||'?'}</code>
        </div>
        <div class="row" style="margin-top:4px">
          ${a.viable
            ? '<span class="chip chip-green">⚡ VIABLE</span>'
            : `<span class="chip chip-dim" title="${a.reason||''}"">NO VIABLE</span>`}
        </div>`;
      grid.appendChild(el);
    });
  }).catch(e => console.error('scores:', e));
}

// ── Load forex ────────────────────────────────────────────────────
function loadForex() {
  fetch('/api/forex').then(r=>r.json()).then(data=>{
    const tbody = document.getElementById('forex-body');
    if (!data.length) { tbody.innerHTML='<tr><td colspan="6" style="color:var(--dim)">Sin datos</td></tr>'; return; }
    tbody.innerHTML = data.map(p => {
      const pct    = Math.min((p.entropy / 2.5) * 100, 100);
      const gChip  = p.godel
        ? '<span class="chip chip-green">✅</span>'
        : '<span class="chip chip-dim">⛔</span>';
      return `<tr>
        <td><b>${p.pair}</b></td>
        <td>${p.entropy.toFixed(4)}
          <span class="ent-bar-wrap"><span class="ent-bar" style="width:${pct}%"></span></span></td>
        <td>${p.p90.toFixed(4)}</td>
        <td>${gChip}</td>
        <td style="color:var(--dim)">${p.proxy}</td>
        <td>${p.vitality||'—'}</td>
      </tr>`;
    }).join('');
  }).catch(e => console.error('forex:', e));
}

// ── Load health ───────────────────────────────────────────────────
function loadHealth() {
  fetch('/api/health').then(r=>r.json()).then(data=>{
    const el = document.getElementById('health-content');
    el.innerHTML = data.map(item => `
      <div class="health-row">
        <span class="health-label">${item.label}</span>
        <span class="health-val" style="color:${item.color}">${item.value}</span>
        <span class="chip ${item.ok ? 'chip-green' : 'chip-red'}">${item.ok ? 'OK' : 'CRIT'}</span>
      </div>`).join('');
  }).catch(e => console.error('health:', e));
}

// ── Load changelog ────────────────────────────────────────────────
function loadChangelog() {
  fetch('/api/changelog').then(r=>r.json()).then(data=>{
    const el = document.getElementById('changelog-content');
    if (!data.length) { el.innerHTML='<span style="color:var(--dim)">Sin eventos</span>'; return; }
    el.innerHTML = data.reverse().map(e => {
      const ts     = (e.ts||'').slice(11,19);
      const event  = e.event||'?';
      const detail = Object.entries(e)
        .filter(([k])=>!['ts','event'].includes(k))
        .map(([k,v])=>`${k}:${JSON.stringify(v)}`).join(' ').slice(0,80);
      const color = event.includes('ERROR') ? 'var(--red)'
                  : event.includes('CRIT')  ? 'var(--orange)'
                  : event === 'TRADE_EVAL'  ? 'var(--cyan)'
                  : 'var(--text)';
      return `<div class="log-entry">
        <span class="log-ts">${ts}</span>
        <span class="log-event" style="color:${color}">${event}</span>
        <span class="log-detail">${detail}</span>
      </div>`;
    }).join('');
  }).catch(e => console.error('changelog:', e));
}

// ── Load trades ───────────────────────────────────────────────────
function loadTrades() {
  fetch('/api/trades').then(r=>r.json()).then(data=>{
    const el = document.getElementById('trade-journal');
    if (!data.length) { el.innerHTML='<span style="color:var(--dim)">Sin trades aún — Day 0/63</span>'; return; }
    const regimeClass = {
      'TREND':    'regime-trend',
      'MEAN_REV': 'regime-meanrev',
      'NOISE':    'regime-noise',
      'GODEL_OFF':'regime-godeloff',
    };
    el.innerHTML = data.map(t => `
      <div class="trade-entry">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span class="trade-asset">${t.asset}</span>
          <span class="regime-label ${regimeClass[t.regime_label]||''}">${t.regime_label}</span>
          <span style="color:var(--dim)">${(t.timestamp_utc||'').slice(11,16)}</span>
        </div>
        <div class="row">
          score=${t.score_oro}  H=${parseFloat(t.hurst||0).toFixed(3)}
          S=${parseFloat(t.entropy||0).toFixed(4)}
          ${t.viable==='True'?'<span class="chip chip-green">VIABLE</span>':''}
          <span style="color:var(--dim)">${t.alpaca_status}</span>
        </div>
      </div>`).join('');
    // Update paper day counter
    const lastDay = data[data.length-1]?.session_day || '?';
    document.getElementById('paper-day').textContent = `Día ${lastDay}/63 paper trading`;
  }).catch(e => console.error('trades:', e));
}

// ── Audit button ──────────────────────────────────────────────────
function runAudit() {
  const btn = document.getElementById('audit-btn');
  const res = document.getElementById('audit-result');
  btn.disabled = true;
  btn.classList.add('running');
  btn.textContent = '⏳ EJECUTANDO AUDIT...';
  res.innerHTML = '<span style="color:var(--cyan)">Iniciando MASTER AUDITOR v2...</span>';

  fetch('/api/audit', {method:'POST'}).then(r=>r.json()).then(data=>{
    btn.disabled = false;
    btn.classList.remove('running');
    btn.textContent = '⚡ EJECUTAR AUDIT';

    if (data.status === 'running') {
      res.innerHTML = '<span style="color:var(--yellow)">Audit en progreso... recargar en 30s</span>';
      return;
    }

    const r = data.result || {};
    const statusColor = r.passed ? 'var(--green)' : 'var(--red)';
    res.innerHTML = `
      <div class="audit-line" style="color:${statusColor}; font-weight:700; margin-bottom:6px">
        ${r.passed ? '✅ SYSTEM OK' : '🔴 CRITICAL FAILURES'}
        — OK=${r.n_ok||0} WARN=${r.n_warn||0} CRIT=${r.n_crit||0}
      </div>
      ${(r.critical||[]).map(c=>`<div class="audit-line audit-crit">[!] ${c}</div>`).join('')}
      ${(r.warnings||[]).map(w=>`<div class="audit-line audit-warn">[~] ${w}</div>`).join('')}
      <div style="color:var(--dim);font-size:10px;margin-top:6px">${r.ts||''}</div>`;
  }).catch(e => {
    btn.disabled = false;
    btn.classList.remove('running');
    btn.textContent = '⚡ EJECUTAR AUDIT';
    res.innerHTML = `<span style="color:var(--red)">Error: ${e}</span>`;
  });
}

// ── Refresh loop ──────────────────────────────────────────────────
function refresh() {
  loadScores();
  loadForex();
  loadHealth();
  loadChangelog();
  loadTrades();
}
refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>
"""


# ── Score engine loader ────────────────────────────────────────────
_score_mod = None
_score_lock = threading.Lock()

def get_score_engine():
    global _score_mod
    with _score_lock:
        if _score_mod is None:
            se = ROOT / "scripts/spel_score_engine.py"
            if se.exists():
                spec = importlib.util.spec_from_file_location("sce_dash", se)
                _score_mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(_score_mod)
    return _score_mod


# ── API endpoints ──────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/scores")
def api_scores():
    mod = get_score_engine()
    results = []
    if not mod:
        return jsonify(results)

    assets = ["BTC", "XAU", "NIFTY50", "NVDA"]
    for asset in assets:
        try:
            r   = mod.score(asset)
            raw = r.__dict__ if hasattr(r, "__dict__") else {}
            bb  = raw.get("backbone")
            entry = None
            if bb:
                v = getattr(bb, "entry", None)
                entry = f"{v:.4f}" if v and not (isinstance(v, float) and (v != v)) else None

            razon = raw.get("razon", [])
            reason = razon[0][:60] if isinstance(razon, list) and razon else str(razon)[:60]

            results.append({
                "asset":        asset,
                "score_oro":    raw.get("score_oro", 0),
                "direction":    raw.get("direction", "?"),
                "godel_active": bool(raw.get("godel_active", False)),
                "viable":       bool(raw.get("viable", False)),
                "modo":         raw.get("modo", "?"),
                "hurst":        raw.get("hurst", 0),
                "entropy":      raw.get("entropy", 0),
                "p90":          raw.get("p90", 0),
                "kelly":        raw.get("kelly_fraction", 0),
                "sha":          raw.get("sha_parquet", "?"),
                "entry":        entry,
                "reason":       reason,
            })
        except Exception as e:
            results.append({"asset": asset, "error": str(e),
                            "score_oro": 0, "direction": "?",
                            "godel_active": False, "viable": False,
                            "modo": "ERROR", "hurst": 0, "entropy": 0,
                            "p90": 0, "kelly": 0, "sha": "?", "entry": None, "reason": str(e)})
    return jsonify(results)


@app.route("/api/forex")
def api_forex():
    try:
        fms   = json.loads((ROOT / "meta/forex_macro_snapshot.json").read_text())
        pairs = fms.get("pairs", {})
        result = [
            {
                "pair":     pair,
                "entropy":  data.get("entropy", 0),
                "p90":      data.get("p90", 0),
                "godel":    data.get("godel_active", False),
                "proxy":    data.get("proxy", "?"),
                "vitality": data.get("vitality", 0),
                "fear":     data.get("fear_momentum", 0),
                "nash":     data.get("nash_frozen", 0),
            }
            for pair, data in pairs.items()
        ]
        return jsonify(result)
    except Exception as e:
        return jsonify([{"error": str(e)}])


@app.route("/api/health")
def api_health():
    items = []
    try:
        reg_path = ROOT / "meta/SHA_REGISTRY.json"
        meta_path = ROOT / "meta/SPEL_META.json"

        if reg_path.exists():
            reg = json.loads(reg_path.read_text())
            for asset in ["BTC", "XAU", "NIFTY50", "NVDA"]:
                d = reg.get(asset, {})
                sha = d.get("sha_v5", "?")
                p90 = d.get("p90_entropy", 0)
                items.append({
                    "label": f"SHA {asset}",
                    "value": sha,
                    "color": "var(--cyan)",
                    "ok":    sha != "?",
                })

        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            ins  = meta.get("input_size", 0)
            items.append({
                "label": "input_size (R13)",
                "value": str(ins),
                "color": "var(--green)" if ins == 20 else "var(--red)",
                "ok":    ins == 20,
            })

        # Checkpoints
        ckpts = {
            "BTC":     "BTC_LSTM_v3c_F1_0.4994.pt",
            "XAU":     "XAU_LSTM_v3_godel_valloss0.4386.pt",
            "NIFTY50": "NIFTY50_LSTM_v3_godel_valloss0.3784.pt",
            "NVDA":    "NVDA_LSTM_v3_godel_valloss0.3857.pt",
        }
        for asset, fname in ckpts.items():
            path = ROOT / "checkpoints" / fname
            ok   = path.exists() and path.stat().st_size > 1000
            items.append({
                "label": f"ckpt {asset}",
                "value": f"{path.stat().st_size/1024:.0f}KB" if ok else "MISSING",
                "color": "var(--green)" if ok else "var(--red)",
                "ok":    ok,
            })

    except Exception as e:
        items.append({"label": "health.load", "value": str(e)[:40],
                      "color": "var(--red)", "ok": False})
    return jsonify(items)


@app.route("/api/changelog")
def api_changelog():
    try:
        path = ROOT / "meta/change_log.json"
        if not path.exists():
            return jsonify([])
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            # Old format (single object) — convert
            return jsonify([{"ts": "legacy", "event": "CHANGE_LOG", **data}])
        return jsonify(data[-10:] if isinstance(data, list) else [])
    except Exception as e:
        return jsonify([{"event": "ERROR", "ts": datetime.now(timezone.utc).isoformat(),
                         "detail": str(e)}])


@app.route("/api/trades")
def api_trades():
    log_path = ROOT / "logs/trade_log.csv"
    if not log_path.exists():
        return jsonify([])
    try:
        with open(log_path, newline="") as f:
            reader = csv.DictReader(f)
            rows   = list(reader)
        return jsonify(rows[-20:])
    except Exception as e:
        return jsonify([{"error": str(e)}])


@app.route("/api/audit", methods=["POST"])
def api_audit():
    with _audit_lock:
        if _audit_state["running"]:
            return jsonify({"status": "running"})
        _audit_state["running"] = True
        _audit_state["result"]  = None

    def run_audit_bg():
        try:
            auditor_path = ROOT / "scripts/SPEL_v37_MASTER_AUDITOR_v2.py"
            if not auditor_path.exists():
                _audit_state["result"] = {
                    "passed": False, "n_ok": 0, "n_warn": 0, "n_crit": 1,
                    "critical": ["auditor_not_found"],
                    "warnings": [],
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
                return

            spec = importlib.util.spec_from_file_location("auditor_bg", auditor_path)
            mod  = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # run_master_audit is defined at module level after exec
            if hasattr(mod, "run_master_audit"):
                result = mod.run_master_audit(
                    notify=True,
                    abort_on_critical=False,
                    session_tag="S31_DASHBOARD",
                )
                _audit_state["result"] = result
            else:
                _audit_state["result"] = {
                    "passed": False, "n_ok": 0, "n_warn": 0, "n_crit": 1,
                    "critical": ["run_master_audit_not_found"],
                    "warnings": [],
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as e:
            _audit_state["result"] = {
                "passed": False, "n_ok": 0, "n_warn": 0, "n_crit": 1,
                "critical": [str(e)[:80]],
                "warnings": [],
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        finally:
            with _audit_lock:
                _audit_state["running"] = False
                _audit_state["ts"]      = datetime.now(timezone.utc).isoformat()

    thread = threading.Thread(target=run_audit_bg, daemon=True)
    thread.start()
    return jsonify({"status": "started"})


@app.route("/api/audit/result")
def api_audit_result():
    with _audit_lock:
        return jsonify({
            "running": _audit_state["running"],
            "result":  _audit_state["result"],
            "ts":      _audit_state["ts"],
        })


# ── ngrok launcher ─────────────────────────────────────────────────
def start_with_ngrok(port: int = 5000, ngrok_token: str = None):
    """Inicia Flask + ngrok. Retorna la URL pública."""
    try:
        from pyngrok import ngrok as _ngrok, conf as _conf
        if ngrok_token:
            _conf.get_default().auth_token = ngrok_token
        elif os.environ.get("NGROK_TOKEN"):
            _conf.get_default().auth_token = os.environ["NGROK_TOKEN"]

        tunnel  = _ngrok.connect(port, "http")
        pub_url = tunnel.public_url
        print(f"\n{'='*54}")
        print(f"  OJO DE DIOS v26 — LIVE")
        print(f"  URL pública  : {pub_url}")
        print(f"  URL local    : http://127.0.0.1:{port}")
        print(f"  Abre en tu Redmi Note 11 Pro+")
        print(f"{'='*54}\n")

        # Notificar URL a TG SISTEMA
        import urllib.request as _ur
        tok = os.environ.get("TELEGRAM_TOKEN","")
        cid = os.environ.get("TELEGRAM_SISTEMA","")
        if tok and cid:
            msg = (f"🖥️ <b>OJO DE DIOS v26</b>\n"
                   f"Dashboard LIVE\n<code>{pub_url}</code>")
            payload = json.dumps({"chat_id":cid,"text":msg,"parse_mode":"HTML"}).encode()
            req = _ur.Request(f"https://api.telegram.org/bot{tok}/sendMessage",
                              data=payload, headers={"Content-Type":"application/json"})
            try: _ur.urlopen(req, timeout=10)
            except: pass

        return pub_url
    except ImportError:
        print("pyngrok not installed: pip install pyngrok --break-system-packages")
        return None
    except Exception as e:
        print(f"ngrok error: {e}")
        return None


def launch(port: int = 5000, ngrok_token: str = None):
    """Punto de entrada principal."""
    url = start_with_ngrok(port, ngrok_token)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False,
            threaded=True)


if __name__ == "__main__":
    launch(port=5000)
