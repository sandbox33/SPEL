"""
spel_snapshot_updater.py — corre en GitHub Actions sin Drive
Recalcula entropy features via GDELT TV API + yfinance proxy
"""
import json, requests, numpy as np
from datetime import datetime, timezone
from pathlib import Path

# R28: P90 por proxy — BUG-SNAPSHOT-GODEL fix (S29)
PROXY_P90_MAP = {
    'NVDA': 1.189820,  # EURUSD, AUDUSD
    'XAU':  1.350316,  # GBPUSD, USDJPY, USDCHF
}


P90 = {"NVDA":1.18982,"XAU":1.350316,"BTC":1.170901,"NIFTY50":1.186823}
PROXY_MAP = {
    "EURUSD":"NVDA","GBPUSD":"XAU",
    "USDJPY":"XAU","USDCHF":"XAU","AUDUSD":"NVDA"
}
GDELT_KEYWORDS = {
    "NVDA": ["nvidia","artificial intelligence","semiconductors"],
    "XAU":  ["gold","geopolitical","federal reserve","safe haven"],
}

def fetch_gdelt_entropy(keywords):
    try:
        kw  = " OR ".join(keywords[:3])
        url = (f"https://api.gdeltproject.org/api/v2/tv/tv?query={kw}"
               f"&mode=timelinevol&format=json&TIMESPAN=1440")
        r   = requests.get(url, timeout=10)
        if r.status_code != 200: return None
        data = r.json().get("timeline",[{}])
        if not data or "series" not in data[0]: return None
        vals = [s.get("value",0) for s in data[0]["series"][-24:]]
        if not vals or sum(vals)==0: return None
        arr  = np.array(vals, dtype=float)+1e-9
        arr /= arr.sum()
        return round(float(-np.sum(arr*np.log(arr+1e-10))),4)
    except Exception:
        return None

def fetch_vitality(proxy):
    try:
        kw  = " OR ".join(GDELT_KEYWORDS[proxy][:2])
        url = (f"https://api.gdeltproject.org/api/v2/doc/doc?query={kw}"
               f"&mode=artlist&maxrecords=50&format=json")
        r   = requests.get(url, timeout=10)
        n   = len(r.json().get("articles",[]))
        return 9 if n>35 else (6 if n>15 else 3)
    except Exception:
        return 6

snapshot = {}
now      = datetime.now(timezone.utc)

for pair, proxy in PROXY_MAP.items():
    entropy = fetch_gdelt_entropy(GDELT_KEYWORDS[proxy])
    if entropy is None:
        try:
            old     = json.load(open("meta/forex_macro_snapshot.json"))
            entropy = old["pairs"][pair]["entropy"]
        except Exception:
            entropy = 1.2
    vitality    = fetch_vitality(proxy)
    p90         = P90[proxy]
    godel       = (entropy >= p90) or (vitality == 9)
    fear_mom    = round(entropy - p90, 4)
    nash_frozen = 0.85 if abs(fear_mom)<0.05 else 0.60
    snapshot[pair] = {
        "entropy": entropy, "p90": PROXY_P90_MAP.get(proxy, p90),
        "vitality": vitality, "nash_frozen": nash_frozen,
        "fear_momentum": fear_mom, "godel_active": godel,
        "proxy": proxy, "as_of": now.isoformat(),
    }

Path("meta/forex_macro_snapshot.json").write_text(
    json.dumps({"updated":now.isoformat(),"pairs":snapshot},indent=2)
)
print(f"Snapshot actualizado: {now.strftime('%H:%M UTC')}")
for p,v in snapshot.items():
    print(f"  {p}: entropy={v['entropy']} godel={v['godel_active']}")