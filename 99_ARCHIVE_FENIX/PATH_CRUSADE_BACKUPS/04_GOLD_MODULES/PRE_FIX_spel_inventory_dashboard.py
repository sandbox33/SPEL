"""
SPEL — Data Inventory Dashboard
Versión: 1.0 · 09-Mar-2026
REGLA R7: Este módulo SOLO LEE. No calcula. No escribe.
Fuente de verdad: spel_asset_catalog.json
"""

import streamlit as st
import json
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, date
from pathlib import Path
import os

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="SPEL · Inventario",
    page_icon="🔭",
    layout="wide",
    initial_sidebar_state="expanded"
)

CATALOG_PATH = Path(os.environ.get(
    "SPEL_CATALOG",
    Path(__file__).parent.parent / "meta" / "spel_asset_catalog.json"
))

GITHUB_REPO  = os.environ.get("SPEL_GITHUB_REPO", "tu-usuario/SPEL")
GITHUB_TOKEN = os.environ.get("SPEL_GITHUB_TOKEN", "")  # en Colab Secrets

STATUS_COLOR = {
    "FULL":       "#10B981",
    "PARTIAL":    "#F59E0B",
    "STALE":      "#EF4444",
    "EMPTY":      "#374151",
    "INPROGRESS": "#3B82F6",
}
STATUS_EMOJI = {
    "FULL":       "✅",
    "PARTIAL":    "⚠️",
    "STALE":      "🔴",
    "EMPTY":      "◻️",
    "INPROGRESS": "🔄",
}
CATEGORY_COLOR = {
    "commodity": "#F59E0B",
    "crypto":    "#8B5CF6",
    "forex":     "#3B82F6",
    "equity":    "#10B981",
    "index":     "#EC4899",
}

# ─────────────────────────────────────────────
# CSS — TERMINAL OPS ROOM
# ─────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:ital,wght@0,400;0,700;1,400&family=Syne:wght@400;600;700;800&display=swap');

:root {
    --bg:        #070C18;
    --bg-card:   #0D1424;
    --bg-panel:  #0F1A2E;
    --gold:      #F59E0B;
    --gold-dim:  #78490A;
    --green:     #10B981;
    --red:       #EF4444;
    --blue:      #3B82F6;
    --purple:    #8B5CF6;
    --pink:      #EC4899;
    --text:      #CBD5E1;
    --text-dim:  #475569;
    --border:    #1E293B;
    --border-hi: #334155;
}

html, body, [class*="css"] {
    font-family: 'Syne', sans-serif !important;
    background-color: var(--bg) !important;
    color: var(--text) !important;
}

/* Main background */
.stApp { background: var(--bg) !important; }
.main .block-container { padding: 1.5rem 2rem; max-width: 100%; }

/* Sidebar */
section[data-testid="stSidebar"] {
    background: var(--bg-card) !important;
    border-right: 1px solid var(--border-hi);
}
section[data-testid="stSidebar"] * { color: var(--text) !important; }

/* Tabs */
.stTabs [data-baseweb="tab-list"] {
    background: var(--bg-card);
    border-bottom: 1px solid var(--border-hi);
    gap: 0;
}
.stTabs [data-baseweb="tab"] {
    font-family: 'Space Mono', monospace !important;
    font-size: 11px !important;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--text-dim) !important;
    padding: 12px 24px;
    border: none;
    background: transparent;
}
.stTabs [aria-selected="true"] {
    color: var(--gold) !important;
    border-bottom: 2px solid var(--gold) !important;
    background: transparent !important;
}

/* Metric cards */
.metric-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-left: 3px solid var(--gold);
    padding: 16px 20px;
    border-radius: 4px;
}
.metric-card .label {
    font-family: 'Space Mono', monospace;
    font-size: 10px;
    letter-spacing: 0.15em;
    color: var(--text-dim);
    text-transform: uppercase;
    margin-bottom: 6px;
}
.metric-card .value {
    font-family: 'Syne', sans-serif;
    font-size: 28px;
    font-weight: 800;
    color: var(--text);
    line-height: 1;
}
.metric-card .sub {
    font-family: 'Space Mono', monospace;
    font-size: 10px;
    color: var(--text-dim);
    margin-top: 4px;
}

/* Asset row card */
.asset-row {
    display: flex;
    align-items: center;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 12px 16px;
    margin-bottom: 6px;
    gap: 16px;
    transition: border-color 0.15s;
}
.asset-row:hover { border-color: var(--border-hi); }

/* Heat bar */
.heat-bar-bg {
    background: var(--bg-panel);
    border-radius: 2px;
    height: 6px;
    flex: 1;
    max-width: 120px;
}
.heat-bar-fill {
    height: 6px;
    border-radius: 2px;
    background: linear-gradient(90deg, #374151 0%, #F59E0B 60%, #EF4444 100%);
}

/* Status badges */
.badge {
    font-family: 'Space Mono', monospace;
    font-size: 9px;
    letter-spacing: 0.08em;
    padding: 2px 6px;
    border-radius: 2px;
    text-transform: uppercase;
    display: inline-block;
}

/* Section headers */
.section-header {
    font-family: 'Space Mono', monospace;
    font-size: 10px;
    letter-spacing: 0.2em;
    color: var(--text-dim);
    text-transform: uppercase;
    border-bottom: 1px solid var(--border);
    padding-bottom: 8px;
    margin-bottom: 16px;
    margin-top: 24px;
}

/* Priority tag */
.priority-hot  { color: #EF4444; font-weight: 700; }
.priority-warm { color: #F59E0B; }
.priority-cold { color: var(--text-dim); }

/* Gap alert */
.gap-alert {
    background: rgba(239,68,68,0.08);
    border: 1px solid rgba(239,68,68,0.25);
    border-left: 3px solid #EF4444;
    padding: 10px 14px;
    border-radius: 4px;
    margin-bottom: 8px;
    font-family: 'Space Mono', monospace;
    font-size: 11px;
}

/* Download command box */
.cmd-box {
    background: #020409;
    border: 1px solid var(--border-hi);
    border-radius: 4px;
    padding: 14px 18px;
    font-family: 'Space Mono', monospace;
    font-size: 11px;
    color: #10B981;
    white-space: pre-wrap;
    word-break: break-all;
}

/* Top banner */
.top-banner {
    display: flex;
    align-items: baseline;
    gap: 12px;
    margin-bottom: 24px;
    border-bottom: 1px solid var(--border-hi);
    padding-bottom: 16px;
}
.top-banner .title {
    font-family: 'Syne', sans-serif;
    font-size: 22px;
    font-weight: 800;
    letter-spacing: -0.02em;
    color: #F1F5F9;
}
.top-banner .version {
    font-family: 'Space Mono', monospace;
    font-size: 10px;
    color: var(--gold);
    letter-spacing: 0.1em;
    border: 1px solid var(--gold-dim);
    padding: 2px 8px;
    border-radius: 2px;
}
.top-banner .rule {
    font-family: 'Space Mono', monospace;
    font-size: 9px;
    color: var(--text-dim);
    margin-left: auto;
}

/* Hide streamlit chrome */
#MainMenu, footer, header { visibility: hidden; }
div[data-testid="stToolbar"] { display: none; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────
# CARGA DE DATOS — SOLO LEE
# ─────────────────────────────────────────────
@st.cache_data(ttl=60)
def load_catalog():
    """Carga el catálogo. Si no existe, retorna estructura vacía."""
    if not CATALOG_PATH.exists():
        alt_paths = [
            Path("/content/spel_root/meta/spel_asset_catalog.json"),
            Path("/content/drive/MyDrive/SPEL-v2.0/meta/spel_asset_catalog.json"),
            Path("spel_asset_catalog.json"),
        ]
        for p in alt_paths:
            if p.exists():
                with open(p) as f:
                    return json.load(f)
        return None
    with open(CATALOG_PATH) as f:
        return json.load(f)

def completeness_score(asset: dict) -> float:
    """Calcula % completeness. 0.0 – 1.0"""
    checks = {
        "ohlcv_full":      asset["ohlcv"]["status"] == "FULL",
        "ohlcv_partial":   asset["ohlcv"]["status"] in ("FULL", "PARTIAL"),
        "gdelt_available": asset["gdelt"]["status"] in ("FULL", "PARTIAL"),
        "gdelt_gov":       asset["gdelt"]["coverage"]["GOV"] > 0,
        "gdelt_bus":       asset["gdelt"]["coverage"]["BUS"] > 0,
        "gdelt_igo":       asset["gdelt"]["coverage"]["IGO"] > 0,
        "news":            asset["news"]["status"] not in ("EMPTY",),
    }
    weights = [0.3, 0.0, 0.25, 0.1, 0.1, 0.1, 0.15]
    return sum(w for (_, v), w in zip(checks.items(), weights) if v)

catalog = load_catalog()


# ─────────────────────────────────────────────
# BANNER
# ─────────────────────────────────────────────
st.markdown("""
<div class="top-banner">
  <span class="title">SPEL · DATA INVENTORY</span>
  <span class="version">v2.0</span>
  <span class="rule">R7: SOLO LEE — no calcula, no escribe</span>
</div>
""", unsafe_allow_html=True)

if catalog is None:
    st.error(f"❌ Catálogo no encontrado en: `{CATALOG_PATH}`\n\nVerifica la variable de entorno `SPEL_CATALOG`.")
    st.stop()

assets    = catalog["assets"]
meta      = catalog["_meta"]
last_upd  = meta.get("last_updated", "?")
total     = len(assets)


# ─────────────────────────────────────────────
# SIDEBAR — FILTROS
# ─────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div class="section-header">Filtros</div>', unsafe_allow_html=True)

    categories = ["Todas"] + sorted({a["category"] for a in assets})
    sel_cat    = st.selectbox("Categoría", categories)

    statuses   = ["Todos", "FULL", "PARTIAL", "STALE", "EMPTY", "INPROGRESS"]
    sel_status = st.selectbox("Estado OHLCV", statuses)

    min_heat   = st.slider("Heat Score mínimo", 0.0, 10.0, 0.0, 0.5)
    show_spel  = st.checkbox("Solo activos SPEL-v2.0", False)

    st.markdown('<div class="section-header">GitHub Actions</div>', unsafe_allow_html=True)
    gh_repo  = st.text_input("Repo (usuario/repo)", GITHUB_REPO)
    gh_token = st.text_input("Token (opcional)", type="password",
                             help="Para disparar workflow_dispatch desde aquí")

    st.markdown('<div class="section-header">Catálogo</div>', unsafe_allow_html=True)
    st.markdown(f"""
    <div style="font-family:'Space Mono',monospace; font-size:10px; color:#475569; line-height:1.8">
    Actualizado: {last_upd}<br>
    Total activos: {total}<br>
    Schema: v{meta.get('schema_version','?')}
    </div>
    """, unsafe_allow_html=True)


# ─────────────────────────────────────────────
# FILTRADO
# ─────────────────────────────────────────────
def apply_filters(assets_list):
    out = assets_list
    if sel_cat != "Todas":
        out = [a for a in out if a["category"] == sel_cat]
    if sel_status != "Todos":
        out = [a for a in out if a["ohlcv"]["status"] == sel_status]
    out = [a for a in out if a["heat_score"] >= min_heat]
    if show_spel:
        spel_ids = {"XAU", "BTC", "NVDA", "NIFTY50"}
        out = [a for a in out if a["id"] in spel_ids]
    return out

filtered = apply_filters(assets)
filtered_sorted = sorted(filtered, key=lambda x: -x["heat_score"])


# ─────────────────────────────────────────────
# MÉTRICAS GLOBALES
# ─────────────────────────────────────────────
n_full    = sum(1 for a in assets if a["ohlcv"]["status"] == "FULL")
n_partial = sum(1 for a in assets if a["ohlcv"]["status"] == "PARTIAL")
n_empty   = sum(1 for a in assets if a["ohlcv"]["status"] == "EMPTY")
n_stale   = sum(1 for a in assets if a["ohlcv"]["status"] == "STALE")
n_hot     = sum(1 for a in assets if a["heat_score"] >= 8.0)
total_gap_days = sum(
    a["ohlcv"]["gap_days"] or 0 for a in assets
    if a["ohlcv"]["gap_days"] is not None
)

c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    st.markdown(f"""<div class="metric-card">
        <div class="label">Total activos</div>
        <div class="value">{total}</div>
        <div class="sub">en catálogo</div>
    </div>""", unsafe_allow_html=True)
with c2:
    st.markdown(f"""<div class="metric-card" style="border-left-color:#10B981">
        <div class="label">OHLCV completos</div>
        <div class="value" style="color:#10B981">{n_full}</div>
        <div class="sub">{n_partial} parciales · {n_empty} vacíos</div>
    </div>""", unsafe_allow_html=True)
with c3:
    st.markdown(f"""<div class="metric-card" style="border-left-color:#EF4444">
        <div class="label">Días con gaps</div>
        <div class="value" style="color:#EF4444">{total_gap_days}</div>
        <div class="sub">en activos con datos</div>
    </div>""", unsafe_allow_html=True)
with c4:
    st.markdown(f"""<div class="metric-card" style="border-left-color:#F59E0B">
        <div class="label">Activos calientes</div>
        <div class="value" style="color:#F59E0B">{n_hot}</div>
        <div class="sub">heat score ≥ 8.0</div>
    </div>""", unsafe_allow_html=True)
with c5:
    st.markdown(f"""<div class="metric-card" style="border-left-color:#3B82F6">
        <div class="label">Mostrando</div>
        <div class="value" style="color:#3B82F6">{len(filtered)}</div>
        <div class="sub">con filtros activos</div>
    </div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)


# ─────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs([
    "🔥 ARSENAL",
    "📅 CRONOLOGÍA",
    "🔍 INSPECTOR",
    "⬇️ DESCARGA",
])


# ══════════════════════════════════════════════
# TAB 1 — ARSENAL
# ══════════════════════════════════════════════
with tab1:
    st.markdown('<div class="section-header">Ranking por heat score — mayor calor = operar primero</div>',
                unsafe_allow_html=True)

    # Tabla de calor con plotly
    df = pd.DataFrame([{
        "ID":        a["id"],
        "Nombre":    a["name"],
        "Cat":       a["category"],
        "Heat":      a["heat_score"],
        "Rank":      a["predictability_rank"],
        "OHLCV":     a["ohlcv"]["status"],
        "GDELT":     a["gdelt"]["status"],
        "GOV":       f'{a["gdelt"]["coverage"]["GOV"]*100:.0f}%' if a["gdelt"]["coverage"]["GOV"] else "—",
        "BUS":       f'{a["gdelt"]["coverage"]["BUS"]*100:.0f}%' if a["gdelt"]["coverage"]["BUS"] else "—",
        "IGO":       f'{a["gdelt"]["coverage"]["IGO"]*100:.0f}%' if a["gdelt"]["coverage"]["IGO"] else "—",
        "OF":        a["order_flow"]["status"],
        "Lookback":  f'{a["spel_lookback_days"]}d',
        "Nota":      a["spel_note"][:60] + "…" if len(a["spel_note"]) > 60 else a["spel_note"],
    } for a in filtered_sorted])

    if df.empty:
        st.info("Sin activos con los filtros seleccionados.")
    else:
        # Heatmap de heat scores
        fig_heat = go.Figure()
        for cat in df["Cat"].unique():
            sub = df[df["Cat"] == cat]
            fig_heat.add_trace(go.Bar(
                name=cat,
                x=sub["ID"],
                y=sub["Heat"],
                marker_color=CATEGORY_COLOR.get(cat, "#6B7280"),
                text=sub["Heat"],
                textposition="outside",
                textfont=dict(family="Space Mono", size=10, color="#CBD5E1"),
            ))

        fig_heat.update_layout(
            barmode="group",
            height=280,
            paper_bgcolor="#070C18",
            plot_bgcolor="#0D1424",
            font=dict(family="Syne", color="#CBD5E1"),
            showlegend=True,
            legend=dict(
                bgcolor="#0D1424",
                bordercolor="#1E293B",
                font=dict(family="Space Mono", size=10),
            ),
            xaxis=dict(
                gridcolor="#1E293B",
                tickfont=dict(family="Space Mono", size=10),
            ),
            yaxis=dict(
                gridcolor="#1E293B",
                range=[0, 11],
                tickfont=dict(family="Space Mono", size=10),
                title="Heat Score",
                titlefont=dict(family="Space Mono", size=10),
            ),
            margin=dict(l=40, r=20, t=20, b=40),
        )
        fig_heat.add_hline(y=8.0, line_dash="dot", line_color="#EF4444",
                           annotation_text="Umbral HOT", annotation_font_size=9)
        st.plotly_chart(fig_heat, use_container_width=True)

        # Tabla detallada
        st.markdown('<div class="section-header">Detalle por activo</div>', unsafe_allow_html=True)

        # Color coding para status
        def color_status(val):
            c = STATUS_COLOR.get(val, "#374151")
            return f'background-color: {c}22; color: {c}; font-family: Space Mono; font-size: 10px;'

        def color_heat(val):
            if val >= 8.5: return 'color: #EF4444; font-weight: bold;'
            if val >= 7.0: return 'color: #F59E0B;'
            return 'color: #6B7280;'

        display_df = df[["ID", "Nombre", "Cat", "Heat", "Rank", "OHLCV", "GDELT", "GOV", "BUS", "IGO", "Lookback", "Nota"]].copy()

        styled = display_df.style \
            .applymap(color_status, subset=["OHLCV", "GDELT"]) \
            .applymap(color_heat, subset=["Heat"]) \
            .set_properties(**{
                'font-family': 'Space Mono',
                'font-size': '11px',
                'background-color': '#0D1424',
                'color': '#CBD5E1',
            }) \
            .set_table_styles([{
                'selector': 'th',
                'props': [
                    ('background-color', '#0F1A2E'),
                    ('color', '#F59E0B'),
                    ('font-family', 'Space Mono'),
                    ('font-size', '10px'),
                    ('letter-spacing', '0.1em'),
                    ('text-transform', 'uppercase'),
                ]
            }]) \
            .format({"Heat": "{:.1f}"})

        st.dataframe(styled, use_container_width=True, height=400)

        # Nota de la tesis
        st.markdown("""
        <div style="background:#0D1424; border:1px solid #1E293B; border-left:3px solid #F59E0B;
                    padding:10px 14px; border-radius:4px; margin-top:16px;
                    font-family:'Space Mono',monospace; font-size:10px; color:#6B7280; line-height:1.7">
        🔑 &nbsp;<strong style="color:#F59E0B">TESIS SPEL:</strong>
        Opera los de heat ≥ 8.0 primero. Los de predictability_rank bajo son los más limpios para el LSTM.
        Los de rank alto son ruidosos — ahí el Gödel dice NO → no operar.
        </div>
        """, unsafe_allow_html=True)


# ══════════════════════════════════════════════
# TAB 2 — CRONOLOGÍA
# ══════════════════════════════════════════════
with tab2:
    st.markdown('<div class="section-header">Cobertura temporal de datos por activo</div>',
                unsafe_allow_html=True)

    today_str = date.today().isoformat()

    # Construir datos para gantt
    gantt_rows = []
    for a in filtered_sorted:
        aid  = a["id"]
        cat  = a["category"]

        # OHLCV
        if a["ohlcv"]["start_date"]:
            gantt_rows.append({
                "Activo":   aid,
                "Tipo":     "OHLCV",
                "Start":    a["ohlcv"]["start_date"],
                "End":      a["ohlcv"]["end_date"] or today_str,
                "Status":   a["ohlcv"]["status"],
                "Category": cat,
            })
        # Gaps OHLCV
        for gap in a["ohlcv"].get("gap_periods", []):
            gantt_rows.append({
                "Activo":   aid,
                "Tipo":     "OHLCV GAP",
                "Start":    gap["from"],
                "End":      gap["to"],
                "Status":   "STALE",
                "Category": cat,
                "Reason":   gap.get("reason", ""),
            })

        # GDELT
        if a["gdelt"]["start_date"]:
            gantt_rows.append({
                "Activo":   aid,
                "Tipo":     "GDELT",
                "Start":    a["gdelt"]["start_date"],
                "End":      a["gdelt"]["end_date"] or today_str,
                "Status":   a["gdelt"]["status"],
                "Category": cat,
            })
        # Gaps GDELT
        for gap in a["gdelt"].get("gap_periods", []):
            gantt_rows.append({
                "Activo":   aid,
                "Tipo":     "GDELT GAP",
                "Start":    gap["from"],
                "End":      gap["to"],
                "Status":   "STALE",
                "Category": cat,
                "Reason":   gap.get("reason", ""),
            })

    if not gantt_rows:
        st.info("Sin datos de cobertura temporal para los filtros seleccionados.")
    else:
        df_gantt = pd.DataFrame(gantt_rows)

        color_map = {
            "FULL":       "#10B981",
            "PARTIAL":    "#F59E0B",
            "STALE":      "#EF4444",
            "EMPTY":      "#1E293B",
            "INPROGRESS": "#3B82F6",
        }
        df_gantt["Color"] = df_gantt["Status"].map(color_map).fillna("#374151")
        df_gantt["Label"] = df_gantt.apply(
            lambda r: f"{r['Tipo']} · {r['Status']}", axis=1)

        fig_gantt = go.Figure()

        # Un trace por tipo×status para la leyenda
        seen = set()
        for _, row in df_gantt.iterrows():
            key = (row["Tipo"], row["Status"])
            show = key not in seen
            seen.add(key)

            is_gap = "GAP" in row["Tipo"]
            hover  = f"<b>{row['Activo']}</b> — {row['Tipo']}<br>{row['Start']} → {row['End']}"
            if "Reason" in row and row["Reason"]:
                hover += f"<br><i>{row['Reason']}</i>"

            fig_gantt.add_trace(go.Bar(
                name=row["Label"] if show else "",
                showlegend=show,
                x=[(pd.to_datetime(row["End"]) - pd.to_datetime(row["Start"])).days],
                y=[f"{row['Activo']} · {row['Tipo'].replace(' GAP','')}"],
                base=[(pd.to_datetime(row["Start"]) - pd.to_datetime("2015-01-01")).days],
                orientation="h",
                marker_color=row["Color"] if not is_gap else "#EF4444",
                marker_opacity=0.9 if not is_gap else 0.5,
                marker_pattern_shape="" if not is_gap else "/",
                hovertemplate=hover + "<extra></extra>",
            ))

        # X ticks en años
        year_ticks = list(range(2015, 2027))
        tick_vals  = [(datetime(y, 1, 1) - datetime(2015, 1, 1)).days for y in year_ticks]

        fig_gantt.update_layout(
            barmode="overlay",
            height=max(300, len(df_gantt["Activo"].unique()) * 35 + 100),
            paper_bgcolor="#070C18",
            plot_bgcolor="#0D1424",
            font=dict(family="Syne", color="#CBD5E1", size=11),
            legend=dict(
                bgcolor="#0D1424",
                bordercolor="#1E293B",
                font=dict(family="Space Mono", size=9),
                orientation="h",
                y=-0.15,
            ),
            xaxis=dict(
                tickvals=tick_vals,
                ticktext=[str(y) for y in year_ticks],
                gridcolor="#1E293B",
                tickfont=dict(family="Space Mono", size=10),
                title="Línea de tiempo",
                titlefont=dict(family="Space Mono", size=10),
            ),
            yaxis=dict(
                gridcolor="#1E293B",
                tickfont=dict(family="Space Mono", size=10),
                autorange="reversed",
            ),
            margin=dict(l=160, r=20, t=20, b=80),
        )
        # Línea de hoy
        today_days = (datetime.now() - datetime(2015, 1, 1)).days
        fig_gantt.add_vline(
            x=today_days, line_dash="dot", line_color="#F59E0B",
            annotation_text="HOY", annotation_font_size=9,
            annotation_font_color="#F59E0B",
        )

        st.plotly_chart(fig_gantt, use_container_width=True)

    # Tabla de gaps
    st.markdown('<div class="section-header">Gaps detectados</div>', unsafe_allow_html=True)

    all_gaps = []
    for a in filtered_sorted:
        for g in a["ohlcv"].get("gap_periods", []):
            days = (pd.to_datetime(g["to"]) - pd.to_datetime(g["from"])).days
            all_gaps.append({
                "Activo": a["id"], "Tipo": "OHLCV",
                "Desde": g["from"], "Hasta": g["to"],
                "Días": days, "Razón": g.get("reason", "")
            })
        for g in a["gdelt"].get("gap_periods", []):
            days = (pd.to_datetime(g["to"]) - pd.to_datetime(g["from"])).days
            all_gaps.append({
                "Activo": a["id"], "Tipo": "GDELT",
                "Desde": g["from"], "Hasta": g["to"],
                "Días": days, "Razón": g.get("reason", "")
            })

    if all_gaps:
        df_gaps = pd.DataFrame(all_gaps).sort_values("Días", ascending=False)
        st.dataframe(
            df_gaps.style.set_properties(**{
                'font-family': 'Space Mono',
                'font-size': '11px',
                'background-color': '#0D1424',
                'color': '#CBD5E1',
            }).applymap(lambda v: 'color:#EF4444; font-weight:bold' if isinstance(v, int) and v > 30 else '',
                        subset=["Días"]),
            use_container_width=True, height=250
        )
    else:
        st.success("✅ Sin gaps registrados en los activos filtrados.")


# ══════════════════════════════════════════════
# TAB 3 — INSPECTOR
# ══════════════════════════════════════════════
with tab3:
    st.markdown('<div class="section-header">Inspección detallada por activo</div>',
                unsafe_allow_html=True)

    asset_ids = [a["id"] for a in filtered_sorted]
    if not asset_ids:
        st.info("Sin activos con los filtros seleccionados.")
    else:
        sel_id   = st.selectbox("Selecciona activo", asset_ids,
                                format_func=lambda x: f"{x} — {next(a['name'] for a in assets if a['id']==x)}")
        asset    = next(a for a in assets if a["id"] == sel_id)
        cat_col  = CATEGORY_COLOR.get(asset["category"], "#6B7280")

        # Header del activo
        heat    = asset["heat_score"]
        rank    = asset["predictability_rank"]
        heat_cl = "priority-hot" if heat >= 8.5 else ("priority-warm" if heat >= 7.0 else "priority-cold")

        st.markdown(f"""
        <div style="background:#0D1424; border:1px solid #1E293B; border-left:4px solid {cat_col};
                    padding:16px 20px; border-radius:4px; margin-bottom:20px;">
          <div style="display:flex; gap:24px; align-items:center; flex-wrap:wrap;">
            <div>
              <span style="font-family:'Syne',sans-serif; font-size:26px; font-weight:800; color:#F1F5F9">{asset['id']}</span>
              <span style="font-family:'Space Mono',monospace; font-size:11px; color:#6B7280; margin-left:12px">{asset['name']}</span>
            </div>
            <div style="margin-left:auto; text-align:right;">
              <div style="font-family:'Space Mono',monospace; font-size:10px; color:#6B7280">HEAT SCORE</div>
              <div style="font-family:'Syne',sans-serif; font-size:28px; font-weight:800; color:{'#EF4444' if heat>=8.5 else ('#F59E0B' if heat>=7.0 else '#6B7280')}">{heat}</div>
            </div>
            <div style="text-align:right;">
              <div style="font-family:'Space Mono',monospace; font-size:10px; color:#6B7280">PRED. RANK</div>
              <div style="font-family:'Syne',sans-serif; font-size:28px; font-weight:800; color:#3B82F6">#{rank}</div>
            </div>
          </div>
          <div style="font-family:'Space Mono',monospace; font-size:10px; color:#6B7280; margin-top:12px; line-height:1.6">
            <span style="color:{cat_col}">[{asset['category']}]</span>
            &nbsp;·&nbsp; {asset['exchange']}
            &nbsp;·&nbsp; Lookback: {asset['spel_lookback_days']}d
            &nbsp;·&nbsp; P90 entropía: {asset['spel_p90_entropy'] or 'pendiente calibrar'}
          </div>
          <div style="font-family:'Space Mono',monospace; font-size:10px; color:#F59E0B; margin-top:8px;">
            💡 {asset['spel_note']}
          </div>
        </div>
        """, unsafe_allow_html=True)

        c_ohlcv, c_gdelt, c_of, c_news = st.columns(4)

        with c_ohlcv:
            s = asset["ohlcv"]
            sc = STATUS_COLOR.get(s["status"], "#374151")
            st.markdown(f"""
            <div class="metric-card" style="border-left-color:{sc}">
              <div class="label">OHLCV</div>
              <div style="font-family:'Space Mono',monospace; font-size:12px; font-weight:700; color:{sc}">{STATUS_EMOJI.get(s['status'],'')} {s['status']}</div>
              <div class="sub">{s['total_days']} días · SHA: {s['sha'] or '—'}</div>
              <div class="sub">{s['start_date'] or '?'} → {s['end_date'] or '?'}</div>
              <div class="sub" style="color:#EF4444">Gaps: {s['gap_days'] or 0}d</div>
              <div class="sub">1m: {'✅' if s['intraday']['1m']['available'] else '◻️'} &nbsp; 15m: {'✅' if s['intraday']['15m']['available'] else '◻️'}</div>
            </div>""", unsafe_allow_html=True)

        with c_gdelt:
            g = asset["gdelt"]
            gc = STATUS_COLOR.get(g["status"], "#374151")
            st.markdown(f"""
            <div class="metric-card" style="border-left-color:{gc}">
              <div class="label">GDELT Entropía</div>
              <div style="font-family:'Space Mono',monospace; font-size:12px; font-weight:700; color:{gc}">{STATUS_EMOJI.get(g['status'],'')} {g['status']}</div>
              <div class="sub">{g['total_rows']} filas · gaps: {g['gap_days'] or 0}d</div>
              <div class="sub">{g['start_date'] or '?'} → {g['end_date'] or '?'}</div>
              <div class="sub">GOV: {int(g['coverage']['GOV']*100)}% &nbsp; BUS: {int(g['coverage']['BUS']*100)}% &nbsp; IGO: {int(g['coverage']['IGO']*100)}%</div>
            </div>""", unsafe_allow_html=True)

        with c_of:
            o = asset["order_flow"]
            oc = STATUS_COLOR.get(o["status"], "#374151")
            st.markdown(f"""
            <div class="metric-card" style="border-left-color:{oc}">
              <div class="label">Order Flow</div>
              <div style="font-family:'Space Mono',monospace; font-size:12px; font-weight:700; color:{oc}">{STATUS_EMOJI.get(o['status'],'')} {o['status']}</div>
              <div class="sub">Tick data: {'✅' if o['tick_data'] else '◻️'}</div>
              <div class="sub">{o['notes']}</div>
            </div>""", unsafe_allow_html=True)

        with c_news:
            n = asset["news"]
            nc = STATUS_COLOR.get(n["status"], "#374151")
            bug_txt = f"⚠️ {n['bug']}" if n.get("bug") else ""
            st.markdown(f"""
            <div class="metric-card" style="border-left-color:{nc}">
              <div class="label">News / API</div>
              <div style="font-family:'Space Mono',monospace; font-size:12px; font-weight:700; color:{nc}">{STATUS_EMOJI.get(n['status'],'')} {n['status']}</div>
              <div class="sub">Fuente: {n['source'] or '—'}</div>
              <div class="sub">Último: {n['last_update'] or '—'}</div>
              <div class="sub" style="color:#EF4444">{bug_txt}</div>
            </div>""", unsafe_allow_html=True)

        # Gaps detallados
        all_asset_gaps = asset["ohlcv"]["gap_periods"] + asset["gdelt"]["gap_periods"]
        if all_asset_gaps:
            st.markdown('<div class="section-header">Gaps detectados</div>', unsafe_allow_html=True)
            for g in all_asset_gaps:
                tipo = "OHLCV" if g in asset["ohlcv"]["gap_periods"] else "GDELT"
                days = (pd.to_datetime(g["to"]) - pd.to_datetime(g["from"])).days
                st.markdown(f"""
                <div class="gap-alert">
                  ⬜ [{tipo}] &nbsp; {g['from']} → {g['to']} &nbsp; · &nbsp; {days} días
                  <br><span style="color:#6B7280">{g.get('reason','')}</span>
                </div>""", unsafe_allow_html=True)

        # GDELT coverage radar
        cov = asset["gdelt"]["coverage"]
        if any(v > 0 for v in cov.values()):
            st.markdown('<div class="section-header">Cobertura GDELT por categoría</div>', unsafe_allow_html=True)
            fig_radar = go.Figure(go.Scatterpolar(
                r=[cov["GOV"], cov["BUS"], cov["IGO"], cov["GOV"]],
                theta=["GOV", "BUS", "IGO", "GOV"],
                fill="toself",
                fillcolor="rgba(245,158,11,0.15)",
                line=dict(color="#F59E0B", width=2),
                name="Cobertura",
            ))
            fig_radar.update_layout(
                polar=dict(
                    bgcolor="#0D1424",
                    radialaxis=dict(visible=True, range=[0, 1],
                                   gridcolor="#1E293B", tickfont=dict(family="Space Mono", size=9)),
                    angularaxis=dict(gridcolor="#1E293B",
                                     tickfont=dict(family="Space Mono", size=11, color="#F59E0B")),
                ),
                paper_bgcolor="#070C18",
                showlegend=False,
                height=280,
                margin=dict(l=60, r=60, t=20, b=20),
            )
            st.plotly_chart(fig_radar, use_container_width=True)


# ══════════════════════════════════════════════
# TAB 4 — DESCARGA
# ══════════════════════════════════════════════
with tab4:
    st.markdown('<div class="section-header">Download Manager — vía GitHub Actions</div>',
                unsafe_allow_html=True)

    st.markdown("""
    <div style="background:#0D1424; border:1px solid #1E293B; border-left:3px solid #3B82F6;
                padding:12px 16px; border-radius:4px; margin-bottom:20px;
                font-family:'Space Mono',monospace; font-size:10px; color:#6B7280; line-height:1.7">
    🔁 &nbsp;Arquitectura híbrida: este dashboard muestra qué descargar.
    El músculo es GitHub Actions (<code>spel_data_downloader.yml</code>).
    <br>
    El workflow se dispara con <code>workflow_dispatch</code> — sin agentes corriendo en Colab 24/7.
    </div>
    """, unsafe_allow_html=True)

    col_sel, col_cfg = st.columns([1, 1])

    with col_sel:
        st.markdown('<div class="section-header">Configurar descarga</div>', unsafe_allow_html=True)

        dl_asset = st.selectbox("Activo", [a["id"] for a in filtered_sorted],
                                key="dl_asset")
        dl_from  = st.date_input("Fecha inicio", value=date(2026, 1, 1), key="dl_from")
        dl_to    = st.date_input("Fecha fin",    value=date.today(),      key="dl_to")
        dl_types = st.multiselect(
            "Tipos de datos",
            ["ohlcv", "gdelt_gov", "gdelt_bus", "gdelt_igo", "news", "orderflow"],
            default=["ohlcv", "gdelt_gov", "gdelt_bus", "gdelt_igo"],
        )

    with col_cfg:
        st.markdown('<div class="section-header">Comando generado</div>', unsafe_allow_html=True)

        types_str = ",".join(dl_types) if dl_types else "ohlcv"

        # URL para workflow_dispatch
        api_url = f"https://api.github.com/repos/{gh_repo}/actions/workflows/spel_data_downloader.yml/dispatches"

        curl_cmd = f"""curl -X POST \\
  -H "Authorization: Bearer $SPEL_GITHUB_TOKEN" \\
  -H "Accept: application/vnd.github+json" \\
  {api_url} \\
  -d '{{
    "ref": "main",
    "inputs": {{
      "asset":      "{dl_asset}",
      "date_from":  "{dl_from.isoformat()}",
      "date_to":    "{dl_to.isoformat()}",
      "data_types": "{types_str}"
    }}
  }}'"""

        st.markdown(f'<div class="cmd-box">{curl_cmd}</div>', unsafe_allow_html=True)

        gh_url = f"https://github.com/{gh_repo}/actions/workflows/spel_data_downloader.yml"

        st.markdown(f"""
        <div style="margin-top:12px; font-family:'Space Mono',monospace; font-size:10px; color:#6B7280; line-height:1.8">
        O dispara manualmente en:<br>
        <a href="{gh_url}" target="_blank" style="color:#3B82F6">{gh_url}</a>
        </div>
        """, unsafe_allow_html=True)

        if gh_token and st.button("🚀 Disparar workflow ahora", type="primary"):
            import requests as req
            payload = {
                "ref": "main",
                "inputs": {
                    "asset":      dl_asset,
                    "date_from":  dl_from.isoformat(),
                    "date_to":    dl_to.isoformat(),
                    "data_types": types_str,
                }
            }
            resp = req.post(api_url,
                            headers={
                                "Authorization": f"Bearer {gh_token}",
                                "Accept": "application/vnd.github+json",
                            },
                            json=payload)
            if resp.status_code == 204:
                st.success(f"✅ Workflow disparado — {dl_asset} · {dl_from} → {dl_to} · [{types_str}]")
            else:
                st.error(f"❌ Error {resp.status_code}: {resp.text}")

    # Cola visual de gaps pendientes (auto-generada desde el catálogo)
    st.markdown('<div class="section-header">Cola sugerida — gaps detectados en catálogo</div>',
                unsafe_allow_html=True)

    queue_items = []
    for a in sorted(assets, key=lambda x: -x["heat_score"]):
        for g in a["ohlcv"].get("gap_periods", []):
            days = (pd.to_datetime(g["to"]) - pd.to_datetime(g["from"])).days
            queue_items.append({
                "Prioridad": "🔴 ALTA" if a["heat_score"] >= 8 else "🟡 MEDIA",
                "Activo":    a["id"],
                "Tipo":      "ohlcv",
                "Desde":     g["from"],
                "Hasta":     g["to"],
                "Días":      days,
                "Heat":      a["heat_score"],
                "Razón":     g.get("reason", ""),
            })
        for g in a["gdelt"].get("gap_periods", []):
            days = (pd.to_datetime(g["to"]) - pd.to_datetime(g["from"])).days
            queue_items.append({
                "Prioridad": "🔴 ALTA" if a["heat_score"] >= 8 else "🟡 MEDIA",
                "Activo":    a["id"],
                "Tipo":      "gdelt_gov,gdelt_bus,gdelt_igo",
                "Desde":     g["from"],
                "Hasta":     g["to"],
                "Días":      days,
                "Heat":      a["heat_score"],
                "Razón":     g.get("reason", ""),
            })

    if queue_items:
        df_queue = pd.DataFrame(queue_items).sort_values(["Heat", "Días"],
                                                          ascending=[False, False])
        st.dataframe(
            df_queue.style.set_properties(**{
                'font-family': 'Space Mono',
                'font-size': '11px',
                'background-color': '#0D1424',
                'color': '#CBD5E1',
            }),
            use_container_width=True, height=280
        )
    else:
        st.success("✅ Sin gaps en cola — catálogo completo.")

    st.markdown("""
    <div style="margin-top:24px; background:#020409; border:1px solid #1E293B;
                padding:12px 16px; border-radius:4px;
                font-family:'Space Mono',monospace; font-size:10px; color:#374151; line-height:1.8">
    # Ejecutar con variable de entorno (no hardcodear el token):<br>
    export SPEL_GITHUB_TOKEN=$(cat /run/secrets/github_token)<br>
    # O desde Colab Secrets:<br>
    from google.colab import userdata<br>
    os.environ['SPEL_GITHUB_TOKEN'] = userdata.get('SPEL_GITHUB_TOKEN')
    </div>
    """, unsafe_allow_html=True)
