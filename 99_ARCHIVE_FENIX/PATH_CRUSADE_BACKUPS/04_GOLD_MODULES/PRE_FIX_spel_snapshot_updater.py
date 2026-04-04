"""
spel_snapshot_updater.py — SPEL S31 (PC-1 fixed)
==================================================
Calcula entropy features via GDELT TV API con queries ESPECÍFICAS por par.
Elimina PC-1: entropy uniforme para todos los pares.

PC-1 fix: cada par usa keywords propios del banco central relevante.
"""
import os, json, sys, time, math, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(os.environ.get("SPEL_BASE_DIR",
            "/content/drive/MyDrive/SPEL-v2.0"))

# ── PROXY_P90_MAP dinámico R28/R34 ──────────────────────────────────
# Lee SHA_REGISTRY.json en runtime — propaga rolling-252d recalibrations
# automáticamente a todos los proxies sin cambios de código.
# Fallback explícito con alerta CRIT si el proxy no está en el registry.
def _load_proxy_p90_map(registry_path: str | None = None) -> dict[str, float]:
    """
    Carga p90_entropy por activo proxy desde SHA_REGISTRY.json.

    Hierarchy de búsqueda:
      1. registry_path arg (test injection)
      2. SPEL_BASE_DIR env var + /meta/SHA_REGISTRY.json
      3. Relative ./meta/SHA_REGISTRY.json (Actions runner context)

    Falla ruidosamente con CRIT alert si un proxy requerido está ausente.
    No acepta KeyError silencioso — R34 compliance.
    """
    import os, json, logging
    from pathlib import Path

    log = logging.getLogger("spel.snapshot_updater")

    # Locate registry
    if registry_path:
        reg_file = Path(registry_path)
    else:
        base = os.environ.get("SPEL_BASE_DIR")
        reg_file = (Path(base) / "meta/SHA_REGISTRY.json") if base                    else Path("meta/SHA_REGISTRY.json")

    if not reg_file.exists():
        log.critical(f"CRIT [R34]: SHA_REGISTRY not found at {reg_file} — "
                     f"falling back to conservative p90=1.5 for ALL proxies")
        return {"NVDA": 1.5, "XAU": 1.5, "BTC": 1.5, "NIFTY50": 1.5}

    try:
        reg = json.loads(reg_file.read_text())
    except Exception as e:
        log.critical(f"CRIT [R34]: SHA_REGISTRY parse error: {e} — fallback p90=1.5")
        return {"NVDA": 1.5, "XAU": 1.5, "BTC": 1.5, "NIFTY50": 1.5}

    p90_map   = {}
    REQUIRED  = {"NVDA", "XAU"}   # proxies usados en PAIR_CONFIG
    FALLBACK  = 1.5                # conservador — Gödel menos activo que p90 histórico

    for asset, meta in reg.items():
        if not isinstance(meta, dict):
            continue
        p90 = meta.get("p90_entropy")
        if p90 is None:
            log.warning(f"WARN [R28]: {asset} in registry missing p90_entropy — skip")
            continue
        p90_val = float(p90)
        # Sanity check: canonical entropy space [1.0, 3.0]
        if not (1.0 <= p90_val <= 3.0):
            log.warning(f"WARN [R28]: {asset} p90={p90_val} outside [1.0,3.0] — "
                        f"using fallback {FALLBACK}")
            p90_map[asset] = FALLBACK
        else:
            p90_map[asset] = p90_val

    # Alert on missing required proxies
    for proxy in REQUIRED:
        if proxy not in p90_map:
            log.critical(f"CRIT [R34]: proxy '{proxy}' absent from SHA_REGISTRY — "
                         f"forex OR% will use fallback {FALLBACK}. "
                         f"Run ingest + R32 sync before next Actions cycle.")
            p90_map[proxy] = FALLBACK

    log.info(f"PROXY_P90_MAP loaded from registry: "
             f"{ {k: round(v,4) for k,v in p90_map.items()} }")
    return p90_map

# Called once at module load — Actions runner picks up rolling-252d values
# No re-import needed: the map is rebuilt fresh on every J0 execution
PROXY_P90_MAP = _load_proxy_p90_map()

# ── FOREX config — PC-1 FIX: queries específicas por par ──────────
FOREX_PAIRS = {
    "EURUSD": {
        "proxy":    "NVDA",
        "keywords": ["Federal Reserve", "ECB", "European Central Bank",
                     "dollar", "euro", "inflation", "rate hike"],
    },
    "GBPUSD": {
        "proxy":    "XAU",
        "keywords": ["Bank of England", "BOE", "pound", "sterling",
                     "UK economy", "British inflation"],
    },
    "USDJPY": {
        "proxy":    "XAU",
        "keywords": ["Bank of Japan", "BOJ", "yen", "Japanese economy",
                     "Ueda", "yield curve control"],
    },
    "USDCHF": {
        "proxy":    "XAU",
        "keywords": ["Swiss National Bank", "SNB", "Swiss franc",
                     "CHF", "Jordan", "Swiss inflation"],
    },
    "AUDUSD": {
        "proxy":    "NVDA",
        "keywords": ["Reserve Bank Australia", "RBA", "Australian dollar",
                     "AUD", "commodities", "China trade"],
    },
}

GDELT_BASE = "https://api.gdeltproject.org/api/v2/tv/tv"


def fetch_gdelt_entropy(keywords: list, max_retries: int = 3) -> float | None:
    """
    Calcula entropy_shannon de la cobertura GDELT para keywords específicos.
    Retorna None en caso de fallo (se usará fallback al snapshot anterior).
    """
    query = " OR ".join(f'"{kw}"' for kw in keywords[:3])  # top 3 keywords
    params = urllib.parse.urlencode({
        "query": query,
        "mode":  "timelinevol",
        "format":"json",
    })
    url = f"{GDELT_BASE}?{params}"

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "SPEL/2.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
            series = data.get("timeline", [{}])[0].get("data", [])
            if not series:
                return None
            # Extraer valores de frecuencia
            vals = [float(pt.get("value", 0)) for pt in series if pt.get("value")]
            if not vals or sum(vals) == 0:
                return None
            # Shannon entropy sobre distribución normalizada
            total = sum(vals)
            probs = [v / total for v in vals if v > 0]
            entropy = -sum(p * math.log(p + 1e-12) for p in probs)
            # Normalizar a escala [0, 3] (compatible con P90 canónico)
            entropy_norm = min(entropy / math.log(len(probs) + 1), 1.0) * 3.0
            return round(entropy_norm, 6)
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"  GDELT fetch failed ({url[:60]}): {e}")
    return None


def fetch_vitality(proxy: str) -> int:
    """Vitality score del proxy (0-9) desde yfinance."""
    try:
        import yfinance as yf
        ticker_map = {"NVDA": "NVDA", "XAU": "GC=F"}
        ticker = yf.Ticker(ticker_map.get(proxy, proxy))
        hist   = ticker.history(period="2d")
        if hist.empty:
            return 5
        vol_ratio = hist["Volume"].iloc[-1] / (hist["Volume"].mean() + 1e-10)
        vitality  = int(min(round(vol_ratio * 5), 9))
        return max(vitality, 1)
    except Exception:
        return 5


def compute_fear_momentum(entropy: float, p90: float) -> float:
    return round(entropy - p90, 6)


def run_update():
    ts  = datetime.now(timezone.utc).isoformat()
    out = {"updated": ts, "pairs": {}}

    # Cargar snapshot anterior como fallback
    snap_path = ROOT / "meta/forex_macro_snapshot.json"
    old_snap  = {}
    if snap_path.exists():
        try:
            old_snap = json.loads(snap_path.read_text()).get("pairs", {})
        except Exception:
            pass

    print(f"\nSPEL Snapshot Updater — {ts[:16]}")
    print(f"PC-1 FIX: per-pair GDELT queries")
    print("-" * 40)

    for pair, cfg in FOREX_PAIRS.items():
        proxy    = cfg["proxy"]
        keywords = cfg["keywords"]
        p90      = PROXY_P90_MAP.get(proxy, 1.2)

        # Fetch entropy con keywords específicos del par
        entropy = fetch_gdelt_entropy(keywords)

        # Fallback al snapshot anterior si GDELT falla
        if entropy is None:
            old = old_snap.get(pair, {})
            entropy = old.get("entropy", 1.2)
            print(f"  {pair}: GDELT fallback → entropy={entropy:.4f}")
        else:
            print(f"  {pair}: entropy={entropy:.4f} (keywords={keywords[0]!r})")

        vitality     = fetch_vitality(proxy)
        godel_active = bool(entropy >= p90)
        fear_mom     = compute_fear_momentum(entropy, p90)
        nash_frozen  = round(1.0 - abs(entropy - p90) / (p90 + 1e-10), 4)

        out["pairs"][pair] = {
            "entropy":      entropy,
            "p90":          p90,
            "vitality":     vitality,
            "godel_active": godel_active,
            "fear_momentum":fear_mom,
            "nash_frozen":  nash_frozen,
            "proxy":        proxy,
            "as_of":        ts,
        }
        time.sleep(0.5)  # rate limit GDELT API

    # Guardar en Drive
    snap_path.write_text(json.dumps(out, indent=2))
    print(f"\nSnapshot guardado: {snap_path}")
    print(f"Pares actualizados: {list(out['pairs'].keys())}")

    # Verificar: ¿entropy es uniforme? (PC-1 residual check)
    ents = set(round(p["entropy"], 3) for p in out["pairs"].values())
    if len(ents) == 1:
        print(f"  WARN PC-1: entropy aún uniforme {ents} — GDELT devolvió mismo valor")
    else:
        print(f"  OK PC-1: entropy diferenciada {ents}")

    return out


if __name__ == "__main__":
    result = run_update()
    # El job de Actions hace git add + commit después de este script
    # El snapshot se commiteará automáticamente por el Actions workflow
