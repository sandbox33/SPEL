"""
spel_export_feature_cache.py
SPEL v40 · Feature Cache Export · S36

Exporta a GitHub:
  feature-cache branch → meta/feature_cache/{asset}_tail.json
                         últimas lookback+5 filas × 20 TENSOR_COLS
                         + scaler params embebidos
                         + SHA provenance chain

  model-cache branch   → checkpoints/{asset}.pt (0.09MB × 4)

Ejecutar desde Colab post-ingest semanal.
Streamlit Cloud lee estas ramas para inferencia LSTM sin Drive.

R32: feature_cache incluye sha_parquet de origen → trazabilidad completa.
R7:  este script escribe solo a GH — no modifica parquets de Drive.
"""

import os, sys, json, base64, hashlib, io
import numpy as np
import polars as pl
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

ROOT  = Path(os.environ.get("SPEL_BASE_DIR", "/content/drive/MyDrive/ORDEN/SPEL 3.0"))
TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO  = "sandbox33/SPEL"
HDRS  = {
    "Authorization": f"token {TOKEN}",
    "Accept":        "application/vnd.github.v3+json",
    "Content-Type":  "application/json",
}

# ── Lookbacks canónicos R4 ───────────────────────────────────────────
LOOKBACKS = {"BTC": 21, "XAU": 63, "NIFTY50": 42, "NVDA": 63}

# ── TENSOR_COLS R13 ──────────────────────────────────────────────────
TENSOR_COLS = [
    "high","low","log_return","entropy_shannon","entropy_decay_lambda",
    "entropy_psych_vix","fibonacci_lag_1","fibonacci_lag_2","fibonacci_lag_3",
    "fibonacci_lag_5","fibonacci_lag_8","fibonacci_lag_13","fibonacci_lag_21",
    "goldstein_geo","n_events_ohlcv","vitality_tesla","mass_panic_index",
    "fear_momentum","vix_norm","nash_frozen_7d",
]

assert len(TENSOR_COLS) == 20, f"R13 violation: {len(TENSOR_COLS)} cols != 20"


# ════════════════════════════════════════════════════════════════════
# GH API helpers
# ════════════════════════════════════════════════════════════════════

def _gh_get_sha(path: str, branch: str) -> str | None:
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/contents/{path}?ref={branch}",
            headers=HDRS)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()).get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _gh_put(path: str, content_bytes: bytes, msg: str, branch: str) -> str:
    """PUT file to GH branch. Creates branch if absent. Returns commit sha."""
    # Ensure branch exists
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/git/refs/heads/{branch}",
            headers=HDRS)
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Create branch from main
            main_sha = json.loads(urllib.request.urlopen(
                urllib.request.Request(
                    f"https://api.github.com/repos/{REPO}/git/refs/heads/main",
                    headers=HDRS), timeout=10).read())["object"]["sha"]
            body = json.dumps({"ref": f"refs/heads/{branch}",
                               "sha": main_sha}).encode()
            urllib.request.urlopen(
                urllib.request.Request(
                    f"https://api.github.com/repos/{REPO}/git/refs",
                    data=body, method="POST", headers=HDRS), timeout=10)
            print(f"  🌿 Branch '{branch}' created from main")

    existing_sha = _gh_get_sha(path, branch)
    body = json.dumps({
        "message": msg,
        "content": base64.b64encode(content_bytes).decode(),
        "branch":  branch,
        **({"sha": existing_sha} if existing_sha else {}),
    }).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        data=body, method="PUT", headers=HDRS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["commit"]["sha"][:8]


# ════════════════════════════════════════════════════════════════════
# STEP 1 — Export feature snapshots → feature-cache branch
# ════════════════════════════════════════════════════════════════════

def export_feature_snapshots(reg: dict, meta: dict) -> dict[str, str]:
    """
    Per activo: extrae últimas (lookback + 5) filas × 20 TENSOR_COLS.
    Embebe scaler params (mean/std) para que Streamlit Cloud pueda
    normalizar sin Drive.

    Formato JSON (no parquet) para zero-dependency read en cloud:
    {
      "asset":         "BTC",
      "sha_parquet":   "89f5872b5d4d",  ← provenance R32
      "lookback":      21,
      "n_rows":        26,              ← lookback + 5
      "tensor_cols":   [...20 cols...],
      "scaler_mean":   [...20 floats...],
      "scaler_std":    [...20 floats...],
      "p90_entropy":   2.002221,
      "p90_method":    "rolling_252d",
      "rows":          [[...], [...]]   ← float32 values, n_rows × 20
      "exported_utc":  "2026-03-21T..."
    }
    """
    ckpt_reg = meta.get("checkpoint_registry", {})
    commits  = {}

    print("\n── STEP 1: Feature snapshot export → feature-cache ─────────")

    for asset, lookback in LOOKBACKS.items():
        pq_path = ROOT / f"data_lake/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet"
        if not pq_path.exists():
            print(f"  ❌ {asset}: parquet missing — skip"); continue

        # Verify SHA provenance R32
        sha_disk = hashlib.sha256(pq_path.read_bytes()).hexdigest()[:12]
        sha_reg  = reg.get(asset, {}).get("sha_v5", "")
        if sha_disk != sha_reg:
            print(f"  ❌ {asset}: SHA mismatch disk={sha_disk} reg={sha_reg} — EF-19 abort")
            continue

        # Load parquet — TENSOR_COLS R13 + close separately (fix G)
        all_cols  = pl.scan_parquet(str(pq_path)).collect_schema().names()
        close_col = next((c for c in ['close','Close','CLOSE'] if c in all_cols), None)
        df        = pl.scan_parquet(str(pq_path)).select(TENSOR_COLS).collect()
        df_close  = pl.scan_parquet(str(pq_path)).select(close_col).collect()                     if close_col else None

        # Extract tail rows
        n_rows     = lookback + 5
        tail       = df.tail(n_rows).to_numpy(allow_copy=True).astype(np.float32)
        last_close = float(df_close[close_col][-1]) if df_close is not None else None

        # Validate shape R13
        assert tail.shape[1] == 20, f"R13: {asset} has {tail.shape[1]} cols"

        # Get scaler params
        cr = ckpt_reg.get(asset, {})
        if asset == "NVDA":
            scaler_path = ROOT / "meta/scalers/NVDA_scaler.json"
            sd = json.loads(scaler_path.read_text())
            # Normalize key names (may be 'mean'/'std' or 'mean_'/'scale_')
            mu_key    = next((k for k in ['mean_','mean'] if k in sd), None)
            std_key   = next((k for k in ['scale_','std'] if k in sd), None)
            if not mu_key or not std_key:
                print(f"  ❌ NVDA scaler keys not found: {list(sd.keys())} — skip")
                continue
            scaler_mean = np.array(sd[mu_key], dtype=np.float32)
            scaler_std  = np.array(sd[std_key], dtype=np.float32)
        else:
            if 'scaler_mean' not in cr or 'scaler_std' not in cr:
                print(f"  ❌ {asset}: scaler absent from META — skip"); continue
            scaler_mean = np.array(cr['scaler_mean'], dtype=np.float32)
            scaler_std  = np.array(cr['scaler_std'],  dtype=np.float32)

        assert scaler_mean.shape[0] == 20, f"R13: scaler mean {scaler_mean.shape}"
        assert scaler_std.shape[0]  == 20, f"R13: scaler std {scaler_std.shape}"

        # Sanity: epsilon guard on scaler std (avoid div-by-zero in cloud inference)
        scaler_std = np.where(np.isnan(scaler_std) | (scaler_std < 1e-8), 1e-8, scaler_std)

        p90_val    = reg[asset]['p90_entropy']
        p90_method = reg[asset].get('p90_method', 'historical')

        payload = {
            "asset":        asset,
            "sha_parquet":  sha_disk,
            "lookback":     lookback,
            "n_rows":       int(tail.shape[0]),
            "tensor_cols":  TENSOR_COLS,
            "scaler_mean":  scaler_mean.tolist(),
            "scaler_std":   scaler_std.tolist(),
            "p90_entropy":  p90_val,
            "p90_method":   p90_method,
            "last_close":   last_close,
            "rows":         tail.tolist(),
            "exported_utc": datetime.now(timezone.utc).isoformat(),
            "schema_version": "v5.1.1",
            "r13_cols":     20,
            "r32_sha_chain": f"disk={sha_disk} registry={sha_reg} match=True",
        }

        content_bytes = json.dumps(payload, separators=(',', ':')).encode()
        size_kb = len(content_bytes) / 1024

        commit = _gh_put(
            f"meta/feature_cache/{asset}_tail.json",
            content_bytes,
            f"data(R32): feature snapshot {asset} sha={sha_disk} {datetime.now(timezone.utc):%Y-%m-%dT%H:%M}UTC",
            "feature-cache",
        )
        commits[asset] = commit
        print(f"  ✅ {asset}: {tail.shape[0]}×20 rows | "
              f"p90={p90_val} [{p90_method}] | "
              f"{size_kb:.1f}KB | commit={commit}")

    return commits


# ════════════════════════════════════════════════════════════════════
# STEP 2 — Export checkpoints → model-cache branch
# ════════════════════════════════════════════════════════════════════

def export_checkpoints() -> dict[str, str]:
    """
    Push 4 × 0.09MB checkpoints a model-cache branch.
    R29: checkpoints NO en main branch — model-cache es rama aislada
         accesible solo por Streamlit Cloud inference, no indexada por CI.
    """
    ckpt_dir = ROOT / "checkpoints"
    CKPT_MAP = {
        "BTC":     "BTC_LSTM_v3c_F1_0.4994.pt",
        "XAU":     "XAU_LSTM_v3_godel_valloss0.4386.pt",
        "NIFTY50": "NIFTY50_LSTM_v3_godel_valloss0.3784.pt",
        "NVDA":    "NVDA_LSTM_v3_godel_valloss0.3857.pt",
    }

    print("\n── STEP 2: Checkpoint export → model-cache ──────────────────")
    commits = {}

    for asset, fname in CKPT_MAP.items():
        ckpt_path = ckpt_dir / fname
        if not ckpt_path.exists():
            print(f"  ❌ {asset}: {fname} missing — R29 restore from TG_BACKUP first")
            continue

        ckpt_bytes = ckpt_path.read_bytes()
        sha12      = hashlib.sha256(ckpt_bytes).hexdigest()[:12]
        size_kb    = len(ckpt_bytes) / 1024

        commit = _gh_put(
            f"checkpoints/{asset}.pt",
            ckpt_bytes,
            f"model(R29): checkpoint {asset} sha={sha12} {datetime.now(timezone.utc):%Y-%m-%dT%H:%M}UTC",
            "model-cache",
        )
        commits[asset] = commit
        print(f"  ✅ {asset}: {fname} | {size_kb:.1f}KB | sha={sha12} | commit={commit}")

    return commits


# ════════════════════════════════════════════════════════════════════
# STEP 3 — Index manifest → feature-cache branch
# ════════════════════════════════════════════════════════════════════

def export_manifest(feat_commits: dict, ckpt_commits: dict, reg: dict):
    """
    Escribe manifest.json en feature-cache branch.
    Streamlit Cloud lo lee primero para verificar freshness
    antes de cargar feature snapshots y checkpoints.
    """
    manifest = {
        "schema_version":  "v5.1",
        "exported_utc":    datetime.now(timezone.utc).isoformat(),
        "assets":          list(LOOKBACKS.keys()),
        "tensor_cols":     TENSOR_COLS,
        "r13_cols":        20,
        "feature_commits": feat_commits,
        "model_commits":   ckpt_commits,
        "p90_map": {
            asset: {
                "p90_entropy": reg.get(asset,{}).get("p90_entropy", 1.5),
                "p90_method":  reg.get(asset,{}).get("p90_method", "historical"),
                "sha_v5":      reg.get(asset,{}).get("sha_v5", "?"),
            }
            for asset in LOOKBACKS
        },
        "inference_ready": all(a in feat_commits and a in ckpt_commits
                               for a in LOOKBACKS),
    }

    commit = _gh_put(
        "meta/feature_cache/manifest.json",
        json.dumps(manifest, indent=2).encode(),
        f"data: manifest update {datetime.now(timezone.utc):%Y-%m-%dT%H:%M}UTC",
        "feature-cache",
    )

    print(f"\n── STEP 3: Manifest → feature-cache | commit={commit}")
    print(f"  inference_ready: {manifest['inference_ready']}")
    for asset, info in manifest['p90_map'].items():
        print(f"  {asset}: p90={info['p90_entropy']} [{info['p90_method']}]")

    return manifest


# ════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════

def run_export():
    if not TOKEN:
        raise RuntimeError("GITHUB_TOKEN not set")

    reg  = json.loads((ROOT / "meta/SHA_REGISTRY.json").read_text())
    meta = json.loads((ROOT / "meta/SPEL_META.json").read_text())

    print("═"*60)
    print(f"  SPEL FEATURE CACHE EXPORT")
    print(f"  {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    print("═"*60)

    feat_commits = export_feature_snapshots(reg, meta)
    ckpt_commits = export_checkpoints()
    manifest     = export_manifest(feat_commits, ckpt_commits, reg)

    print("\n" + "═"*60)
    print(f"  EXPORT COMPLETE")
    print(f"  Features: {len(feat_commits)}/4 assets")
    print(f"  Models:   {len(ckpt_commits)}/4 checkpoints")
    print(f"  Ready:    {manifest['inference_ready']}")
    print(f"\n  Streamlit Cloud inference enabled.")
    print(f"  Branches: feature-cache · model-cache")
    print("═"*60)

    return manifest


if __name__ == "__main__":
    run_export()
