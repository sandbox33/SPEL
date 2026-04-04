import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.subplots as sp
import json
import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

# ------------------------------------------------------------------------------
# PAGE CONFIG
# ------------------------------------------------------------------------------
st.set_page_config(
    page_title="SPEL-FX Institutional Terminal",
    layout="wide",
    initial_sidebar_state="expanded",
    page_icon="📈"
)

# ------------------------------------------------------------------------------
# CUSTOM CSS (Cyberpunk Noir)
# ------------------------------------------------------------------------------
st.markdown("""
<style>
    /* Global dark background */
    .stApp {
        background-color: #0a0a0a;
        color: #e0e0e0;
        font-family: 'Courier New', monospace;
    }
    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #0f0f0f;
        border-right: 1px solid #00ffff30;
    }
    /* Headers */
    h1, h2, h3, h4 {
        font-family: 'Courier New', monospace;
        font-weight: bold;
        color: #00ffff;
        text-shadow: 0 0 2px #00ffff;
    }
    /* Buttons */
    .stButton>button {
        font-family: 'Courier New', monospace;
        font-weight: bold;
        border-radius: 0;
        border: none;
        background-color: #1e1e1e;
        color: #00ffff;
        box-shadow: 0 0 2px #00ffff;
        transition: all 0.2s ease;
    }
    .stButton>button:hover {
        background-color: #00ffff22;
        border: 1px solid #00ffff;
        box-shadow: 0 0 8px #00ffff;
    }
    /* Buy button specific */
    .buy-button button {
        background-color: #00ffaa22;
        color: #00ffaa;
        box-shadow: 0 0 4px #00ffaa;
    }
    .buy-button button:hover {
        background-color: #00ffaa44;
        border-color: #00ffaa;
    }
    /* Sell button specific */
    .sell-button button {
        background-color: #ff005522;
        color: #ff0055;
        box-shadow: 0 0 4px #ff0055;
    }
    .sell-button button:hover {
        background-color: #ff005544;
        border-color: #ff0055;
    }
    /* Tables */
    .dataframe {
        background-color: #0a0a0a;
        color: #e0e0e0;
        font-family: 'Courier New', monospace;
        border: 1px solid #00ffff40;
    }
    .dataframe th {
        background-color: #1a1a1a;
        color: #00ffff;
        border-bottom: 1px solid #00ffff;
    }
    .dataframe td {
        border: 1px solid #00ffff20;
    }
    /* Tabs */
    .stTabs [data-baseweb="tab-list"] {
        gap: 16px;
        background-color: #0a0a0a;
        border-bottom: 1px solid #00ffff40;
    }
    .stTabs [data-baseweb="tab"] {
        font-family: 'Courier New', monospace;
        font-weight: bold;
        color: #aaa;
        background-color: #0a0a0a;
        border-radius: 0;
        padding: 8px 16px;
    }
    .stTabs [aria-selected="true"] {
        color: #00ffff;
        background-color: #1a1a1a;
        border-bottom: 2px solid #00ffff;
    }
    /* Expander */
    .streamlit-expanderHeader {
        font-family: 'Courier New', monospace;
        background-color: #1a1a1a;
        border: 1px solid #00ffff40;
        color: #00ffff;
    }
    /* Form */
    .stForm {
        background-color: #0f0f0f;
        border: 1px solid #00ffff30;
        padding: 16px;
        border-radius: 0;
    }
    /* Toast */
    .stToast {
        background-color: #0a0a0a;
        border-left: 4px solid #00ffff;
        color: #00ffff;
        font-family: monospace;
    }
</style>
""", unsafe_allow_html=True)

# ------------------------------------------------------------------------------
# CONSTANTS
# ------------------------------------------------------------------------------
DATA_LAKE_ROOT = Path("data_lake")
META_DIR = Path("meta")
LOGS_DIR = Path("logs")
CAPITAL = 100_000  # USD

# ------------------------------------------------------------------------------
# CACHED DATA LOADERS
# ------------------------------------------------------------------------------
@st.cache_data(ttl=300)  # 5 minutes cache for live data, but actual files change rarely
def load_last_signal() -> Optional[Dict]:
    """Load pipeline sentinel JSON."""
    try:
        with open(META_DIR / "last_signal.json", "r") as f:
            data = json.load(f)
        # Validate required fields
        if "ts" not in data or "records" not in data:
            return None
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return None

@st.cache_data(ttl=3600)
def load_forex_scalers() -> Optional[Dict]:
    """Load scaler registry metadata."""
    try:
        with open(META_DIR / "forex_scalers.json", "r") as f:
            data = json.load(f)
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return None

@st.cache_data(ttl=3600)
def list_available_pairs() -> List[str]:
    """Scan data_lake for pairs that have features parquet."""
    pairs = []
    if not DATA_LAKE_ROOT.exists():
        return pairs
    for pair_dir in DATA_LAKE_ROOT.iterdir():
        if pair_dir.is_dir():
            feat_path = pair_dir / "ohlcv" / "features" / f"{pair_dir.name}_features.parquet"
            if feat_path.exists():
                pairs.append(pair_dir.name)
    return sorted(pairs)

@st.cache_data(ttl=300)
def load_features_parquet(pair: str) -> Optional[pd.DataFrame]:
    """Load precomputed features for a given pair."""
    path = DATA_LAKE_ROOT / pair / "ohlcv" / "features" / f"{pair}_features.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        # Ensure datetime column exists
        if "time" not in df.columns:
            # If index is datetime, reset
            if isinstance(df.index, pd.DatetimeIndex):
                df = df.reset_index()
                df.rename(columns={"index": "time"}, inplace=True)
            else:
                st.warning(f"Features file for {pair} missing 'time' column. Using index.")
                df = df.reset_index()
        # Convert time to datetime if needed
        if not pd.api.types.is_datetime64_any_dtype(df["time"]):
            df["time"] = pd.to_datetime(df["time"])
        return df
    except Exception as e:
        st.error(f"Error loading features for {pair}: {e}")
        return None

@st.cache_data(ttl=300)
def load_trade_log() -> Optional[pd.DataFrame]:
    """Load historical trade log CSV."""
    path = LOGS_DIR / "trade_log.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
        # Ensure timestamp column exists
        if "timestamp" not in df.columns:
            if "time" in df.columns:
                df.rename(columns={"time": "timestamp"}, inplace=True)
            else:
                st.warning("Trade log missing timestamp column, adding index as fallback.")
                df["timestamp"] = pd.to_datetime("now")
        return df
    except Exception as e:
        st.error(f"Error loading trade log: {e}")
        return None

# ------------------------------------------------------------------------------
# HELPER FUNCTIONS
# ------------------------------------------------------------------------------
def check_pipeline_status(signal_data: Optional[Dict]) -> Tuple[str, str]:
    """
    Returns (status_text, status_emoji) based on last_signal.json.
    """
    if signal_data is None:
        return "PIPELINE: DISCONNECTED", "🔴"
    ts_str = signal_data.get("ts")
    records = signal_data.get("records", 0)
    if not ts_str:
        return "PIPELINE: DISCONNECTED", "🔴"
    try:
        last_time = datetime.fromisoformat(ts_str)
        # Consider disconnected if older than 6 hours (GHA usually runs hourly)
        if datetime.now() - last_time > timedelta(hours=6) or records == 0:
            return "PIPELINE: DISCONNECTED", "🔴"
        else:
            return "PIPELINE: LIVE", "🟢"
    except:
        return "PIPELINE: DISCONNECTED", "🔴"

def create_candlestick_with_entropy(df: pd.DataFrame, pair: str) -> go.Figure:
    """Create a candlestick chart with entropy area and background shading."""
    # Ensure we have the required columns
    required = ["time", "open", "high", "low", "close", "entropy_z"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        st.warning(f"Missing columns for candlestick: {missing}. Skipping chart.")
        return go.Figure()
    
    # Sort by time
    df = df.sort_values("time")
    
    # Subplot: row1 candlestick, row2 entropy area
    fig = sp.make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.7, 0.3],
        subplot_titles=(f"{pair} Price Action", "Entropy Z-Score (Shannon Proxy)")
    )
    
    # Candlestick trace
    fig.add_trace(
        go.Candlestick(
            x=df["time"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="OHLC",
            line=dict(width=1),
            increasing_line_color="#00ffaa",
            decreasing_line_color="#ff0055",
        ),
        row=1, col=1
    )
    
    # Entropy area trace
    fig.add_trace(
        go.Scatter(
            x=df["time"],
            y=df["entropy_z"],
            fill='tozeroy',
            mode='lines',
            line=dict(color="#00ffff", width=1),
            fillcolor="rgba(0, 255, 255, 0.2)",
            name="Entropy Z",
        ),
        row=2, col=1
    )
    
    # Add background rectangles for market regimes
    # Red: chaos (Z>2), Green: structured (Z<-1)
    for idx in range(len(df)-1):
        start = df["time"].iloc[idx]
        end = df["time"].iloc[idx+1]
        z = df["entropy_z"].iloc[idx]
        if z > 2.0:
            fillcolor = "rgba(255, 0, 0, 0.1)"
            row = 1
        elif z < -1.0:
            fillcolor = "rgba(0, 255, 0, 0.1)"
            row = 1
        else:
            continue
        fig.add_vrect(
            x0=start, x1=end,
            fillcolor=fillcolor,
            opacity=0.3,
            layer="below",
            line_width=0,
            row=row, col=1
        )
    
    # Layout styling
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0a0a0a",
        plot_bgcolor="#0a0a0a",
        font=dict(family="Courier New, monospace", color="#e0e0e0"),
        hovermode="x unified",
        margin=dict(l=20, r=20, t=40, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    fig.update_xaxes(gridcolor="#333333", linecolor="#00ffff", showgrid=True)
    fig.update_yaxes(gridcolor="#333333", linecolor="#00ffff")
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="Entropy Z", row=2, col=1)
    
    return fig

def get_recent_signals(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Extract recent signals based on logit/signal column."""
    if df is None or df.empty:
        return pd.DataFrame()
    
    # Look for a column indicating signal strength (logit, prediction, signal)
    signal_col = None
    for col in ["logit", "prediction", "signal", "signal_strength"]:
        if col in df.columns:
            signal_col = col
            break
    if signal_col is None:
        # If no explicit signal, use entropy_z as proxy (not ideal but falls back)
        if "entropy_z" in df.columns:
            signal_col = "entropy_z"
            st.info("No explicit logit column found, using entropy_z as proxy signal.")
        else:
            return pd.DataFrame()
    
    # Select columns: time, close, signal
    cols = ["time", "close", signal_col]
    cols_present = [c for c in cols if c in df.columns]
    signals_df = df[cols_present].tail(n).copy()
    signals_df = signals_df.sort_values("time", ascending=False)
    signals_df.columns = ["Timestamp", "Close", "Signal"]
    return signals_df

def simulate_order(pair: str, lot: float, side: str, current_price: float) -> Dict:
    """Simulate an order execution and return a record."""
    return {
        "timestamp": datetime.now().isoformat(),
        "pair": pair,
        "side": side,
        "lot": lot,
        "price": current_price,
        "status": "EXECUTED",
        "message": f"{side} {lot} {pair} @ {current_price:.5f}"
    }

# ------------------------------------------------------------------------------
# SESSION STATE INIT
# ------------------------------------------------------------------------------
if "simulated_trades" not in st.session_state:
    st.session_state.simulated_trades = []

# ------------------------------------------------------------------------------
# MAIN APP
# ------------------------------------------------------------------------------
def main():
    # --- SIDEBAR ---
    with st.sidebar:
        st.markdown("## 🔮 SPEL-FX")
        st.markdown("---")
        available_pairs = list_available_pairs()
        if not available_pairs:
            st.error("No feature parquet files found in data_lake/.")
            st.stop()
        selected_pair = st.selectbox(
            "Select Currency Pair",
            available_pairs,
            index=0,
            help="Choose pair for visualization and signal analysis."
        )
        st.markdown("---")
        st.markdown("### System Status")
        st.markdown("**Cache:** Active")
        st.markdown("**Data Source:** Parquet + JSON (pre‑computed)")
        st.markdown("**Version:** 2.0 (Institutional)")

    # --- SENTINEL HEADER ---
    signal_data = load_last_signal()
    status_text, emoji = check_pipeline_status(signal_data)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown(f"<h1 style='text-align: center;'>{emoji} {status_text}</h1>", unsafe_allow_html=True)
        st.markdown(f"<p style='text-align: center; font-family: monospace;'>CAPITAL CANÓNICO: ${CAPITAL:,}</p>", unsafe_allow_html=True)
        if status_text == "PIPELINE: DISCONNECTED":
            st.markdown("<p style='text-align: center; color: #ff0055;'>⚠️ TG ALERT: Check GHA J1</p>", unsafe_allow_html=True)
    st.markdown("---")

    # --- LOAD DATA FOR SELECTED PAIR ---
    features_df = load_features_parquet(selected_pair)
    if features_df is None or features_df.empty:
        st.error(f"No features data found for {selected_pair}. Please check the data pipeline.")
        st.stop()
    
    # --- TABS ---
    tab1, tab2 = st.tabs(["📈 Market Regime & Info‑Theoretic", "⚡ OPERATION PANEL"])
    
    # ========================= TAB 1 =========================
    with tab1:
        # Candlestick + Entropy
        st.markdown("### Market Visualization")
        chart = create_candlestick_with_entropy(features_df, selected_pair)
        st.plotly_chart(chart, use_container_width=True)
        
        # Audit View (expander)
        with st.expander("📂 Audit View: Scalers Metadata (EF‑22 / OR%)"):
            scalers = load_forex_scalers()
            if scalers:
                # Flatten the JSON into a DataFrame for readability
                rows = []
                for pair, meta in scalers.items():
                    row = {"Pair": pair}
                    if isinstance(meta, dict):
                        row.update(meta)
                    else:
                        row["metadata"] = meta
                    rows.append(row)
                audit_df = pd.DataFrame(rows)
                st.dataframe(audit_df, use_container_width=True)
                st.caption("Registry of scalers: each pair has isolated namespace (input_dim=12, Z_PORTABILITY_LOG=EF‑22).")
            else:
                st.warning("forex_scalers.json not found or empty.")
    
    # ========================= TAB 2 =========================
    with tab2:
        st.markdown("### Operation Panel")
        left_col, right_col = st.columns([1, 1.5], gap="large")
        
        # LEFT COLUMN: Live Order Book & Simulation Log
        with left_col:
            st.markdown("#### 📡 Live Order Book (Historical)")
            trade_log_df = load_trade_log()
            if trade_log_df is not None and not trade_log_df.empty:
                # Display last 5 trades in monospace style
                last_trades = trade_log_df.tail(5).copy()
                # Format timestamps if needed
                if "timestamp" in last_trades.columns:
                    last_trades["timestamp"] = pd.to_datetime(last_trades["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")
                st.dataframe(last_trades, use_container_width=True)
            else:
                st.info("No historical trade log found. Simulated orders will appear below.")
            
            st.markdown("#### 🧪 Simulated Execution Log")
            if st.session_state.simulated_trades:
                sim_df = pd.DataFrame(st.session_state.simulated_trades)
                sim_df = sim_df[["timestamp", "pair", "side", "lot", "price", "status"]]
                st.dataframe(sim_df, use_container_width=True)
            else:
                st.caption("No simulated orders yet. Use the form to execute a test order.")
        
        # RIGHT COLUMN: Trade Execution Form + Signals
        with right_col:
            st.markdown("#### 🕹️ Order Entry")
            with st.form("order_form", clear_on_submit=False):
                # Pair selector (pre‑filled with sidebar selection)
                trade_pair = st.selectbox(
                    "Instrument",
                    available_pairs,
                    index=available_pairs.index(selected_pair) if selected_pair in available_pairs else 0,
                    help="Currency pair to trade."
                )
                lot_size = st.number_input("Lot Size", min_value=0.01, value=1.0, step=0.1, format="%.2f")
                # Two side‑by‑side submit buttons
                col_buy, col_sell = st.columns(2)
                with col_buy:
                    buy_clicked = st.form_submit_button("BUY", use_container_width=True)
                with col_sell:
                    sell_clicked = st.form_submit_button("SELL", use_container_width=True)
                
                # Handle form submission
                if buy_clicked or sell_clicked:
                    # Get current price (last close)
                    current_price = features_df["close"].iloc[-1] if "close" in features_df.columns else 0.0
                    if current_price == 0.0:
                        st.error("Cannot determine current price. Ensure features file has 'close' column.")
                    else:
                        side = "BUY" if buy_clicked else "SELL"
                        order = simulate_order(trade_pair, lot_size, side, current_price)
                        st.session_state.simulated_trades.append(order)
                        # Show toast notification
                        st.toast(f"⚠️ ENVIANDO ORDEN A BROKER API...\n✅ {order['message']}", icon="💹")
                        st.success(f"Operación simulada: {order['message']}")
            
            st.markdown("#### 📡 Signals View (Recent Alphas)")
            signals_df = get_recent_signals(features_df, n=10)
            if not signals_df.empty:
                st.dataframe(signals_df, use_container_width=True)
            else:
                st.info("No signal data available for this pair.")
        
        # Additional styling for buy/sell buttons
        st.markdown("""
        <style>
            /* Target the specific buttons in the form */
            div[data-testid="column"]:first-child button {
                background-color: #00ffaa22;
                border-color: #00ffaa;
                color: #00ffaa;
            }
            div[data-testid="column"]:last-child button {
                background-color: #ff005522;
                border-color: #ff0055;
                color: #ff0055;
            }
        </style>
        """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
