"""
spel_graph_tab.py — SPEL 3.0 · AST Module Dependency Graph · Dashboard Tab
Protocol: SPEL_S48 v4.8 · Hinc Omnia Cerno

Renders an interactive Cytoscape.js dependency graph inside Streamlit
via st.components.v1.html. No external Streamlit plugins required.

Tab contract:
  - Import and call render_graph_tab(data) from spel_dashboard.py
  - 'data' is the dict returned by _load_all_vault()
  - graph JSON is read from data["graph"] (live_graph_data.json)
  - Falls back to embedded STATIC_GRAPH if JSON is empty/stale

Features:
  - Node tiers: DIAMOND (green) | GOLD (yellow) | VAULT (blue) |
                INFRA (cyan) | ZOMBIE (red/dim)
  - Edge types: READS / WRITES / CALLS / TRIGGERS / NOTIFIES
  - Hover tooltip: label, tier, size, layer, status
  - Click-to-select: highlights all connected edges
  - Live issues panel below graph: severity-sorted
  - Controls: fit view, toggle edge labels, filter by tier

RAM: Cytoscape.js loads from cdnjs CDN — zero local Python memory
GC:  No Polars/numpy in this module
"""

import json
import os
from pathlib import Path
from typing import Optional
import streamlit as st

# ─── Static fallback graph (embedded — works even if live_graph_data.json is empty)
# Updated at S48 from Biblia + module audit
STATIC_GRAPH: dict = {
    "total_nodes": 36,
    "total_edges": 45,
    "nodes": [
        {"id": "gdelt_foundation",    "label": "gdelt_foundation",     "tier": "DIAMOND", "layer": "data",   "size": 8148,  "protected": True},
        {"id": "critical_loss",       "label": "critical_loss_opt",    "tier": "DIAMOND", "layer": "model",  "size": 16673, "protected": True},
        {"id": "godel_bound",         "label": "godel_bound",          "tier": "DIAMOND", "layer": "signal", "size": 17600, "protected": False},
        {"id": "capa_c_inference",    "label": "capa_c_inference",     "tier": "DIAMOND", "layer": "model",  "size": 14680, "protected": False},
        {"id": "dna_sovereign",       "label": "dna_sovereign",        "tier": "DIAMOND", "layer": "data",   "size": 31689, "protected": False},
        {"id": "spel_math_engine",    "label": "spel_math_engine",     "tier": "DIAMOND", "layer": "signal", "size": 54661, "protected": False},
        {"id": "spel_backbone",       "label": "spel_backbone_engine", "tier": "DIAMOND", "layer": "model",  "size": 45595, "protected": False},
        {"id": "orchestrator_v10",    "label": "orchestrator_v10",     "tier": "GOLD",    "layer": "ops",    "size": 30012, "protected": False},
        {"id": "bayesian_core",       "label": "spel_bayesian_core",   "tier": "GOLD",    "layer": "signal", "size": 22825, "protected": False},
        {"id": "forex_bridge",        "label": "spel_forex_bridge",    "tier": "GOLD",    "layer": "exec",   "size": 19585, "protected": False},
        {"id": "dead_man_switch",     "label": "spel_dead_man_switch", "tier": "GOLD",    "layer": "ops",    "size": 4200,  "protected": False},
        {"id": "dashboard",           "label": "spel_dashboard",       "tier": "GOLD",    "layer": "ui",     "size": 28000, "protected": False},
        {"id": "harvester_v3",        "label": "spel_harvester_v3",    "tier": "GOLD",    "layer": "data",   "size": 37357, "protected": False},
        {"id": "ingest_incremental",  "label": "ingest_incremental",   "tier": "GOLD",    "layer": "data",   "size": 19554, "protected": False},
        {"id": "score_engine",        "label": "spel_score_engine",    "tier": "GOLD",    "layer": "signal", "size": 12526, "protected": False},
        {"id": "ojo_de_dios_v26",     "label": "ojo_de_dios_v26",      "tier": "GOLD",    "layer": "ui",     "size": 32208, "protected": False},
        {"id": "web3_adapter",        "label": "spel_web3_adapter",    "tier": "GOLD",    "layer": "exec",   "size": 11454, "protected": False},
        {"id": "paper_adapter_v2",    "label": "paper_adapter_v2",     "tier": "GOLD",    "layer": "exec",   "size": 26231, "protected": False},
        {"id": "live_bma_result",     "label": "live_bma_result.json", "tier": "VAULT",   "layer": "state",  "size": 1934,  "protected": False},
        {"id": "last_signal",         "label": "last_signal.json",     "tier": "VAULT",   "layer": "state",  "size": 0,     "protected": False},
        {"id": "system_pulse",        "label": "system_pulse.json",    "tier": "VAULT",   "layer": "state",  "size": 241,   "protected": False},
        {"id": "gate_metrics",        "label": "gate_metrics.json",    "tier": "VAULT",   "layer": "state",  "size": 242,   "protected": False},
        {"id": "sha_registry",        "label": "SHA_REGISTRY.json",    "tier": "VAULT",   "layer": "state",  "size": 1013,  "protected": False},
        {"id": "live_graph_data",     "label": "live_graph_data.json", "tier": "VAULT",   "layer": "state",  "size": 36549, "protected": False},
        {"id": "live_dashboard_stats","label": "live_dashboard_stats", "tier": "VAULT",   "layer": "state",  "size": 783,   "protected": False},
        {"id": "live_forex_signal",   "label": "live_forex_signal",    "tier": "VAULT",   "layer": "state",  "size": 0,     "protected": False},
        {"id": "gh_patrol",           "label": "patrol.yml (GH/15min)","tier": "INFRA",   "layer": "ci",     "size": 0,     "protected": False},
        {"id": "tg_sistema",          "label": "TG · SISTEMA",         "tier": "INFRA",   "layer": "notify", "size": 0,     "protected": False},
        {"id": "tg_senales",          "label": "TG · SEÑALES",         "tier": "INFRA",   "layer": "notify", "size": 0,     "protected": False},
        {"id": "tg_chaos",            "label": "TG · CHAOS",           "tier": "INFRA",   "layer": "notify", "size": 0,     "protected": False},
        {"id": "tg_backup",           "label": "TG · BACKUP",          "tier": "INFRA",   "layer": "notify", "size": 0,     "protected": False},
        {"id": "orchestrator_v9",     "label": "orchestrator_v9 ⚰",   "tier": "ZOMBIE",  "layer": "ops",    "size": 31943, "protected": False},
        {"id": "spel_forex_iq",       "label": "spel_forex_iq ⚰",     "tier": "ZOMBIE",  "layer": "exec",   "size": 25955, "protected": False},
        {"id": "spel_forex_iq_runner","label": "spel_forex_iq_run ⚰", "tier": "ZOMBIE",  "layer": "exec",   "size": 2392,  "protected": False},
        {"id": "bulk_harvester",      "label": "spel_bulk_harvest ⚰",  "tier": "ZOMBIE",  "layer": "data",   "size": 46489, "protected": False},
        {"id": "daily_check_ghost",   "label": "spel_daily_check ⚰",  "tier": "ZOMBIE",  "layer": "ops",    "size": 0,     "protected": False},
    ],
    "edges": [
        {"source": "gh_patrol",        "target": "dead_man_switch",    "type": "TRIGGERS",  "label": "step 6/8"},
        {"source": "gh_patrol",        "target": "orchestrator_v10",   "type": "TRIGGERS",  "label": "step 7"},
        {"source": "gh_patrol",        "target": "tg_chaos",           "type": "NOTIFIES",  "label": "SOS fail"},
        {"source": "orchestrator_v10", "target": "bayesian_core",      "type": "CALLS",     "label": "run_bma()"},
        {"source": "orchestrator_v10", "target": "forex_bridge",       "type": "CALLS",     "label": "run_forex()"},
        {"source": "orchestrator_v10", "target": "web3_adapter",       "type": "CALLS",     "label": "run_web3()"},
        {"source": "orchestrator_v10", "target": "live_bma_result",    "type": "WRITES",    "label": "BMA result"},
        {"source": "orchestrator_v10", "target": "system_pulse",       "type": "WRITES",    "label": "pulse"},
        {"source": "orchestrator_v10", "target": "sha_registry",       "type": "WRITES",    "label": "intraday"},
        {"source": "orchestrator_v10", "target": "live_dashboard_stats","type": "WRITES",   "label": "dashboard"},
        {"source": "orchestrator_v10", "target": "live_graph_data",    "type": "WRITES",    "label": "enrich"},
        {"source": "orchestrator_v10", "target": "tg_sistema",         "type": "NOTIFIES",  "label": "cycle"},
        {"source": "orchestrator_v10", "target": "tg_chaos",           "type": "NOTIFIES",  "label": "SOS"},
        {"source": "bayesian_core",    "target": "last_signal",        "type": "READS",     "label": "inputs"},
        {"source": "bayesian_core",    "target": "live_bma_result",    "type": "WRITES",    "label": "Gold Score"},
        {"source": "bayesian_core",    "target": "gate_metrics",       "type": "READS",     "label": "GT-Score"},
        {"source": "godel_bound",      "target": "bayesian_core",      "type": "CALLS",     "label": "godel_score"},
        {"source": "godel_bound",      "target": "last_signal",        "type": "READS",     "label": "thresholds"},
        {"source": "forex_bridge",     "target": "live_bma_result",    "type": "READS",     "label": "EURUSD gold"},
        {"source": "forex_bridge",     "target": "live_forex_signal",  "type": "WRITES",    "label": ".lock"},
        {"source": "forex_bridge",     "target": "tg_chaos",           "type": "NOTIFIES",  "label": "shield"},
        {"source": "forex_bridge",     "target": "tg_senales",         "type": "NOTIFIES",  "label": "EXECUTE"},
        {"source": "web3_adapter",     "target": "live_bma_result",    "type": "READS",     "label": "gold>0.85"},
        {"source": "web3_adapter",     "target": "tg_chaos",           "type": "NOTIFIES",  "label": "dry-run"},
        {"source": "harvester_v3",     "target": "last_signal",        "type": "WRITES",    "label": "OHLCV"},
        {"source": "ingest_incremental","target": "last_signal",       "type": "WRITES",    "label": "GDELT"},
        {"source": "gdelt_foundation", "target": "last_signal",        "type": "WRITES",    "label": "entropy/TE"},
        {"source": "capa_c_inference", "target": "last_signal",        "type": "WRITES",    "label": "prediction"},
        {"source": "capa_c_inference", "target": "spel_backbone",      "type": "CALLS",     "label": "LSTM"},
        {"source": "spel_backbone",    "target": "critical_loss",      "type": "CALLS",     "label": "loss"},
        {"source": "score_engine",     "target": "last_signal",        "type": "WRITES",    "label": "GT-Score"},
        {"source": "spel_math_engine", "target": "score_engine",       "type": "CALLS",     "label": "math"},
        {"source": "dead_man_switch",  "target": "system_pulse",       "type": "READS",     "label": "--check"},
        {"source": "dead_man_switch",  "target": "system_pulse",       "type": "WRITES",    "label": "--pulse"},
        {"source": "dead_man_switch",  "target": "tg_chaos",           "type": "NOTIFIES",  "label": "SOS"},
        {"source": "dashboard",        "target": "live_bma_result",    "type": "READS",     "label": "BMA"},
        {"source": "dashboard",        "target": "live_dashboard_stats","type": "READS",    "label": "stats"},
        {"source": "dashboard",        "target": "gate_metrics",       "type": "READS",     "label": "GT-Score"},
        {"source": "dashboard",        "target": "system_pulse",       "type": "READS",     "label": "health"},
        {"source": "dashboard",        "target": "live_forex_signal",  "type": "READS",     "label": "EURUSD"},
        {"source": "dashboard",        "target": "live_graph_data",    "type": "READS",     "label": "graph tab"},
        {"source": "dashboard",        "target": "sha_registry",       "type": "READS",     "label": "SHA"},
        {"source": "dna_sovereign",    "target": "sha_registry",       "type": "WRITES",    "label": "SHA val"},
        {"source": "tg_sistema",       "target": "dashboard",          "type": "READS",     "label": "Streamlit"},
        {"source": "tg_senales",       "target": "dashboard",          "type": "READS",     "label": "Streamlit"},
    ],
    "issues": [
        {"severity": "CRITICAL", "id": "SEC-01",
         "description": "GitHub token exposed in BIBLIA JSON — REVOKE at github.com/settings/tokens"},
        {"severity": "HIGH", "id": "MOD-01",
         "description": "spel_forex_iq.py + spel_forex_iq_runner.py still in GH — IQ Option not fully extirpated"},
        {"severity": "HIGH", "id": "MOD-02",
         "description": "orchestrator_v9.py in repo as zombie alongside v10"},
        {"severity": "HIGH", "id": "DATA-01",
         "description": "last_signal.json not committed → vitality_tesla=0 (DATA_STALE) every cold start"},
        {"severity": "MEDIUM", "id": "MOD-03",
         "description": "spel_daily_check.py is 0 bytes (ghost file)"},
        {"severity": "MEDIUM", "id": "MOD-04",
         "description": "live_bma_history.json is 2B (empty []) — history not accumulating"},
        {"severity": "MEDIUM", "id": "MOD-05",
         "description": "spel_bulk_harvester.py (46KB) still in GH after euthanasia decision"},
        {"severity": "LOW", "id": "CI-01",
         "description": "git pull --rebase logs unstaged changes error (masked by || true)"},
    ]
}


# ─── Color palette (matches dashboard CSS variables) ──────────────────────────
TIER_COLORS = {
    "DIAMOND": {"bg": "#00e87a", "border": "#00e87a", "text": "#000000"},
    "GOLD":    {"bg": "#f0c040", "border": "#f0c040", "text": "#000000"},
    "VAULT":   {"bg": "#40a0e0", "border": "#40a0e0", "text": "#000000"},
    "INFRA":   {"bg": "#40c8e0", "border": "#40c8e0", "text": "#000000"},
    "ZOMBIE":  {"bg": "#3a1010", "border": "#e83050", "text": "#e83050"},
}

EDGE_COLORS = {
    "READS":    "#40a0e0",
    "WRITES":   "#f0c040",
    "CALLS":    "#00e87a",
    "TRIGGERS": "#e07820",
    "NOTIFIES": "#a060c0",
}

SEVERITY_COLORS = {
    "CRITICAL": "#e83050",
    "HIGH":     "#e07820",
    "MEDIUM":   "#f0c040",
    "LOW":      "#4a6070",
}


def _build_cytoscape_elements(graph: dict) -> list:
    """Convert graph dict to Cytoscape.js elements array."""
    elements = []

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    # Build node set for edge validation
    node_ids = {n["id"] for n in nodes}

    for n in nodes:
        tier    = n.get("tier", "GOLD")
        colors  = TIER_COLORS.get(tier, TIER_COLORS["GOLD"])
        layer   = n.get("layer", "ops")
        size_b  = n.get("size", 0)
        size_kb = f"{size_b/1024:.1f}KB" if size_b > 0 else "–"
        protected = "🔒 PROTECTED" if n.get("protected") else ""

        # Node size: proportional to file size (min 30, max 80)
        node_size = max(30, min(80, 30 + (size_b / 2000)))

        elements.append({
            "data": {
                "id":        n["id"],
                "label":     n.get("label", n["id"]),
                "tier":      tier,
                "layer":     layer,
                "size_kb":   size_kb,
                "protected": protected,
                "bg":        colors["bg"],
                "border":    colors["border"],
                "text":      colors["text"],
                "node_size": node_size,
            }
        })

    for i, e in enumerate(edges):
        src = e.get("source", "")
        tgt = e.get("target", "")
        if src not in node_ids or tgt not in node_ids:
            continue
        etype  = e.get("type", "CALLS")
        color  = EDGE_COLORS.get(etype, "#4a6070")
        elements.append({
            "data": {
                "id":     f"e{i}",
                "source": src,
                "target": tgt,
                "type":   etype,
                "label":  e.get("label", ""),
                "color":  color,
            }
        })

    return elements


def _build_issues_html(issues: list) -> str:
    """Build severity-sorted issues panel HTML."""
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    sorted_issues = sorted(issues, key=lambda x: order.get(x.get("severity", "LOW"), 99))

    rows = ""
    for issue in sorted_issues:
        sev   = issue.get("severity", "LOW")
        color = SEVERITY_COLORS.get(sev, "#4a6070")
        rows += f"""
        <tr>
          <td style="color:{color};font-weight:700;white-space:nowrap;padding:4px 8px">{sev}</td>
          <td style="color:#6080a0;padding:4px 8px;white-space:nowrap">{issue.get("id","")}</td>
          <td style="color:#b8ccd8;padding:4px 8px">{issue.get("description","")}</td>
        </tr>"""

    return f"""
    <table style="width:100%;border-collapse:collapse;font-family:'JetBrains Mono',monospace;font-size:0.72rem">
      <thead>
        <tr style="border-bottom:1px solid #1e3050">
          <th style="color:#4a6070;text-align:left;padding:4px 8px;letter-spacing:1px">SEVERITY</th>
          <th style="color:#4a6070;text-align:left;padding:4px 8px;letter-spacing:1px">ID</th>
          <th style="color:#4a6070;text-align:left;padding:4px 8px;letter-spacing:1px">DESCRIPTION</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"""


def _render_cytoscape_html(elements: list, height: int = 640) -> str:
    """
    Build the full Cytoscape.js HTML page (rendered via st.components.v1.html).
    Uses cdnjs CDN — no pip install needed.
    Layout: dagre (directed acyclic graph) with layer grouping.
    """
    elements_json = json.dumps(elements)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #040608; font-family: 'JetBrains Mono', monospace; }}

  #controls {{
    display: flex; gap: 8px; padding: 8px 12px;
    background: #080c10; border-bottom: 1px solid #142030;
    flex-wrap: wrap; align-items: center;
  }}
  .ctrl-btn {{
    background: #0c1016; color: #b8ccd8; border: 1px solid #1e3050;
    padding: 4px 10px; font-size: 0.65rem; letter-spacing: 1px;
    cursor: pointer; border-radius: 2px; font-family: inherit;
    text-transform: uppercase;
  }}
  .ctrl-btn:hover {{ border-color: #00e87a; color: #00e87a; }}
  .ctrl-btn.active {{ border-color: #00e87a; color: #00e87a; background: rgba(0,232,122,0.08); }}

  .tier-legend {{
    display: flex; gap: 12px; margin-left: auto; align-items: center;
    font-size: 0.62rem; letter-spacing: 1px; color: #4a6070;
  }}
  .legend-dot {{
    display: inline-block; width: 10px; height: 10px;
    border-radius: 2px; margin-right: 4px;
  }}

  #cy {{ width: 100%; height: {height}px; background: #040608; }}

  #tooltip {{
    position: fixed; display: none;
    background: #0c1016; border: 1px solid #1e3050;
    color: #b8ccd8; padding: 8px 12px; font-size: 0.68rem;
    border-radius: 3px; pointer-events: none; z-index: 9999;
    max-width: 240px; line-height: 1.6;
    box-shadow: 0 4px 20px rgba(0,0,0,0.6);
  }}
  .tt-tier   {{ color: #00e87a; font-weight: 700; }}
  .tt-label  {{ color: #f0f4f8; font-weight: 600; margin-bottom: 4px; }}
  .tt-detail {{ color: #4a6070; font-size: 0.62rem; }}
</style>
</head>
<body>

<div id="controls">
  <button class="ctrl-btn" onclick="cy.fit()">⊞ FIT</button>
  <button class="ctrl-btn" onclick="resetHighlight()">✕ CLEAR</button>
  <button class="ctrl-btn" id="btn-labels" onclick="toggleEdgeLabels()">EDGE LABELS</button>
  <button class="ctrl-btn" id="btn-diamond" onclick="filterTier('DIAMOND')">◆ DIAMOND</button>
  <button class="ctrl-btn" id="btn-gold"    onclick="filterTier('GOLD')">◈ GOLD</button>
  <button class="ctrl-btn" id="btn-zombie"  onclick="filterTier('ZOMBIE')">⚰ ZOMBIE</button>
  <button class="ctrl-btn" id="btn-all"     onclick="filterTier(null)" style="border-color:#4a6070">ALL</button>

  <div class="tier-legend">
    <span><span class="legend-dot" style="background:#00e87a"></span>DIAMOND</span>
    <span><span class="legend-dot" style="background:#f0c040"></span>GOLD</span>
    <span><span class="legend-dot" style="background:#40a0e0"></span>VAULT</span>
    <span><span class="legend-dot" style="background:#40c8e0"></span>INFRA</span>
    <span><span class="legend-dot" style="background:#3a1010;border:1px solid #e83050"></span>ZOMBIE</span>
  </div>
</div>

<div id="cy"></div>
<div id="tooltip"></div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.28.1/cytoscape.min.js"></script>

<script>
const elements = {elements_json};

const edgeColors = {{
  READS:    '#40a0e0',
  WRITES:   '#f0c040',
  CALLS:    '#00e87a',
  TRIGGERS: '#e07820',
  NOTIFIES: '#a060c0',
}};

const cy = cytoscape({{
  container: document.getElementById('cy'),
  elements:  elements,
  style: [
    {{
      selector: 'node',
      style: {{
        'background-color':  'data(bg)',
        'border-color':      'data(border)',
        'border-width':      2,
        'width':             'data(node_size)',
        'height':            'data(node_size)',
        'label':             'data(label)',
        'color':             '#b8ccd8',
        'font-size':         '9px',
        'font-family':       'JetBrains Mono, monospace',
        'text-valign':       'bottom',
        'text-halign':       'center',
        'text-margin-y':     4,
        'text-background-color': '#040608',
        'text-background-opacity': 0.85,
        'text-background-padding': '2px',
        'min-zoomed-font-size': 7,
      }}
    }},
    {{
      selector: 'node[tier = "DIAMOND"]',
      style: {{
        'shape':        'diamond',
        'border-width': 3,
        'border-style': 'solid',
      }}
    }},
    {{
      selector: 'node[tier = "VAULT"]',
      style: {{
        'shape':        'roundrectangle',
        'border-style': 'dashed',
      }}
    }},
    {{
      selector: 'node[tier = "INFRA"]',
      style: {{
        'shape':        'hexagon',
        'border-style': 'solid',
      }}
    }},
    {{
      selector: 'node[tier = "ZOMBIE"]',
      style: {{
        'opacity':      0.5,
        'shape':        'octagon',
        'border-style': 'dotted',
        'border-width': 1,
      }}
    }},
    {{
      selector: 'edge',
      style: {{
        'width':                2,
        'line-color':           'data(color)',
        'target-arrow-color':   'data(color)',
        'target-arrow-shape':   'triangle',
        'curve-style':          'bezier',
        'opacity':              0.7,
        'label':                '',
        'font-size':            '7px',
        'color':                '#4a6070',
        'text-background-color':'#040608',
        'text-background-opacity': 0.8,
        'text-background-padding': '1px',
        'min-zoomed-font-size': 7,
      }}
    }},
    {{
      selector: 'edge[type = "WRITES"]',
      style: {{ 'line-style': 'dashed', 'line-dash-pattern': [6,3] }}
    }},
    {{
      selector: 'edge[type = "NOTIFIES"]',
      style: {{ 'line-style': 'dotted' }}
    }},
    {{
      selector: '.highlighted',
      style: {{
        'opacity':     1,
        'border-width': 4,
        'z-index':      10,
      }}
    }},
    {{
      selector: '.dimmed',
      style: {{ 'opacity': 0.08 }}
    }},
    {{
      selector: '.edge-highlighted',
      style: {{
        'width':   4,
        'opacity': 1,
        'z-index': 10,
      }}
    }},
  ],
  layout: {{
    name: 'cose',
    animate: false,
    randomize: false,
    nodeRepulsion: 8000,
    nodeOverlap: 20,
    idealEdgeLength: 120,
    edgeElasticity: 80,
    gravity: 0.5,
    numIter: 1000,
    coolingFactor: 0.95,
    minTemp: 1.0,
  }},
  wheelSensitivity: 0.3,
}});

// ── Tooltip
const tooltip = document.getElementById('tooltip');

cy.on('mouseover', 'node', function(e) {{
  const d = e.target.data();
  tooltip.innerHTML = `
    <div class="tt-label">${{d.label}}</div>
    <div class="tt-tier">${{d.tier}} · ${{d.layer.toUpperCase()}}</div>
    ${{d.protected ? '<div style="color:#e07820;font-size:0.62rem">🔒 EF-23 PROTECTED</div>' : ''}}
    <hr style="border:none;border-top:1px solid #1e3050;margin:4px 0">
    <div class="tt-detail">Size: ${{d.size_kb}}</div>
  `;
  tooltip.style.display = 'block';
}});
cy.on('mouseout',  'node', () => {{ tooltip.style.display = 'none'; }});
cy.on('mousemove', e => {{
  tooltip.style.left = (e.originalEvent.clientX + 14) + 'px';
  tooltip.style.top  = (e.originalEvent.clientY - 10) + 'px';
}});

cy.on('mouseover', 'edge', function(e) {{
  const d = e.target.data();
  tooltip.innerHTML = `
    <div class="tt-label">${{d.source}} → ${{d.target}}</div>
    <div class="tt-tier" style="color:${{edgeColors[d.type] || '#4a6070'}}">${{d.type}}</div>
    <div class="tt-detail">${{d.label}}</div>
  `;
  tooltip.style.display = 'block';
}});
cy.on('mouseout', 'edge', () => {{ tooltip.style.display = 'none'; }});

// ── Click to highlight connected subgraph
cy.on('tap', 'node', function(e) {{
  const node = e.target;
  const connected = node.closedNeighborhood();
  cy.elements().addClass('dimmed');
  connected.removeClass('dimmed').addClass('highlighted');
  connected.edges().addClass('edge-highlighted');
}});

cy.on('tap', function(e) {{
  if (e.target === cy) resetHighlight();
}});

function resetHighlight() {{
  cy.elements().removeClass('dimmed highlighted edge-highlighted');
}}

// ── Edge label toggle
let edgeLabelsOn = false;
function toggleEdgeLabels() {{
  edgeLabelsOn = !edgeLabelsOn;
  document.getElementById('btn-labels').classList.toggle('active', edgeLabelsOn);
  cy.edges().style('label', edgeLabelsOn ? 'data(label)' : '');
}}

// ── Tier filter
function filterTier(tier) {{
  document.querySelectorAll('.ctrl-btn').forEach(b => b.classList.remove('active'));
  if (!tier) {{
    cy.elements().style('display', 'element');
    document.getElementById('btn-all').classList.add('active');
  }} else {{
    cy.elements().style('display', 'element');
    cy.nodes().filter(n => n.data('tier') !== tier).style('display', 'none');
    // Also hide edges connected to hidden nodes
    cy.edges().forEach(e => {{
      const srcHidden = e.source().style('display') === 'none';
      const tgtHidden = e.target().style('display') === 'none';
      if (srcHidden || tgtHidden) e.style('display', 'none');
    }});
    const btn = document.getElementById('btn-' + tier.toLowerCase());
    if (btn) btn.classList.add('active');
  }}
}}

// Initial fit with padding
cy.ready(() => {{ cy.fit(undefined, 40); }});
</script>
</body>
</html>"""


# ─── Main entry point: render_graph_tab ───────────────────────────────────────

def render_graph_tab(data: dict, graph_height: int = 640) -> None:
    """
    Render the full SPEL Module Graph tab inside Streamlit.
    Call from spel_dashboard.py within a st.tab context.

    Args:
        data:         dict from _load_all_vault() — uses data["graph"]
        graph_height: Cytoscape canvas height in pixels (default 640)
    """
    # Load live graph from vault; fall back to static
    live_graph = data.get("graph", {})

    # Use live graph if it has real edge data; otherwise use static
    has_edges = bool(live_graph.get("edges")) or bool(live_graph.get("total_edges", 0))
    graph = live_graph if has_edges else STATIC_GRAPH

    # Merge issues: live issues override static
    if not live_graph.get("issues") and STATIC_GRAPH.get("issues"):
        graph["issues"] = STATIC_GRAPH["issues"]

    # ── Header row
    col_h, col_ts = st.columns([3, 1])
    with col_h:
        st.markdown("## ⬡ MODULE DEPENDENCY GRAPH", unsafe_allow_html=False)
    with col_ts:
        gen_at = graph.get("generated_at", "static fallback")
        st.markdown(
            f'<div class="mono-xs" style="text-align:right;margin-top:0.5rem">'
            f'Generated: {gen_at}</div>',
            unsafe_allow_html=True,
        )

    # ── Stats row
    summary = graph.get("summary", {})
    s_col = st.columns(6)
    labels = [
        ("NODES",   str(graph.get("total_nodes", len(graph.get("nodes", []))))),
        ("EDGES",   str(graph.get("total_edges", len(graph.get("edges", []))))),
        ("DIAMOND", str(summary.get("DIAMOND", 0))),
        ("GOLD",    str(summary.get("GOLD", 0))),
        ("VAULT",   str(summary.get("VAULT", 0))),
        ("ZOMBIE",  str(summary.get("ZOMBIE", 0))),
    ]
    label_colors = ["#b8ccd8", "#b8ccd8", "#00e87a", "#f0c040", "#40a0e0", "#e83050"]
    for col, (lbl, val), color in zip(s_col, labels, label_colors):
        with col:
            st.markdown(
                f'<div style="background:#080c10;border:1px solid #142030;border-radius:3px;'
                f'padding:0.4rem 0.6rem;text-align:center">'
                f'<div class="mono-xs" style="color:#4a6070">{lbl}</div>'
                f'<div style="font-size:1.3rem;font-weight:700;color:{color}">{val}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.markdown('<div style="height:0.4rem"></div>', unsafe_allow_html=True)

    # ── Cytoscape graph
    elements = _build_cytoscape_elements(graph)
    html_src = _render_cytoscape_html(elements, height=graph_height)

    st.components.v1.html(html_src, height=graph_height + 48, scrolling=False)

    # ── Issues panel
    issues = graph.get("issues", [])
    if issues:
        # Count by severity
        crit  = sum(1 for i in issues if i.get("severity") == "CRITICAL")
        high  = sum(1 for i in issues if i.get("severity") == "HIGH")
        med   = sum(1 for i in issues if i.get("severity") == "MEDIUM")

        sev_color = "#e83050" if crit > 0 else "#e07820" if high > 0 else "#f0c040"
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:12px;'
            f'padding:0.5rem 0;border-bottom:1px solid #142030;margin-bottom:0.4rem">'
            f'<span style="color:{sev_color};font-weight:700;font-size:0.72rem;'
            f'letter-spacing:1px">⚠ AUDIT ISSUES ({len(issues)})</span>'
            f'<span style="color:#e83050;font-size:0.65rem">CRITICAL:{crit}</span>'
            f'<span style="color:#e07820;font-size:0.65rem">HIGH:{high}</span>'
            f'<span style="color:#f0c040;font-size:0.65rem">MEDIUM:{med}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        issues_html = _build_issues_html(issues)
        st.markdown(
            f'<div style="background:#080c10;border:1px solid #142030;'
            f'border-radius:3px;padding:0.4rem;overflow-x:auto">'
            f'{issues_html}</div>',
            unsafe_allow_html=True,
        )


# ─── Standalone test mode ──────────────────────────────────────────────────────
if __name__ == "__main__":
    # Run as standalone: streamlit run spel_graph_tab.py
    st.set_page_config(
        page_title="SPEL · Graph Tab Test",
        page_icon="⬡",
        layout="wide",
    )
    _CSS_MIN = """
    <style>
    html,body,.stApp{background:#040608!important}
    *{font-family:'JetBrains Mono',monospace!important}
    #MainMenu,footer,header{display:none!important}
    .block-container{padding:1rem 1.5rem!important;max-width:100%!important}
    h2{color:#4a6070!important;font-size:0.7rem!important;letter-spacing:2px;text-transform:uppercase}
    .mono-xs{font-size:0.62rem;color:#4a6070;letter-spacing:0.5px}
    [data-testid="metric-container"]{background:#080c10!important;border:1px solid #142030!important}
    </style>"""
    st.markdown(_CSS_MIN, unsafe_allow_html=True)
    render_graph_tab({"graph": STATIC_GRAPH}, graph_height=680)
