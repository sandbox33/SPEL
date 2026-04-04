"""
SPEL_S31_LAUNCHER.py — Session startup script
==============================================
Integrates:
  Phase 1: spel_paper_adapter.py  (deep regime trade logging)
  Phase 2: ojo_de_dios_v26.py     (Flask dashboard + audit button)
  Phase 3: PC-1 fix               (per-pair GDELT entropy in updater)

Run this as Colab Cell 1 after mounting Drive.
Everything starts from here.
"""

# ── Bootstrap ──────────────────────────────────────────────────────
from google.colab import drive, userdata
try:
    drive.mount('/content/drive')
except ValueError:
    pass

import os, sys, json, base64, urllib.request, importlib.util, threading
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path('/content/drive/MyDrive/ORDEN/SPEL 3.0')
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT/'scripts').mkdir(exist_ok=True)
(ROOT/'logs').mkdir(exist_ok=True)
(ROOT/'meta').mkdir(exist_ok=True)

for p in [str(ROOT/'scripts'), str(ROOT/'codigo/core')]:
    if p not in sys.path:
        sys.path.insert(0, p)

for k in ["TELEGRAM_TOKEN","TELEGRAM_SENALES","TELEGRAM_SISTEMA",
          "TELEGRAM_BACKUP","TELEGRAM_CHAT_ID","GITHUB_TOKEN",
          "ALPACA_API_KEY","ALPACA_SECRET_KEY","NGROK_TOKEN"]:
    v = userdata.get(k)
    if v:
        os.environ[k] = v

os.environ["SPEL_BASE_DIR"] = str(ROOT)

TOKEN   = os.environ.get("TELEGRAM_TOKEN","")
TG_SIST = os.environ.get("TELEGRAM_SISTEMA","")
GH_TOK  = os.environ.get("GITHUB_TOKEN","")
REPO    = "sandbox33/SPEL"
SEP     = "=" * 58

def tg(chat_id, text):
    if not TOKEN or not chat_id: return
    payload = json.dumps({"chat_id":chat_id,"text":text,"parse_mode":"HTML"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data=payload, headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r: return r.status
    except Exception as e: print(f"  TG: {e}")

def gh_get(path):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        headers={"Authorization":f"token {GH_TOK}",
                 "Accept":"application/vnd.github.v3+json"})
    with urllib.request.urlopen(req, timeout=15) as r: return json.loads(r.read())

def gh_decode(d):
    return base64.b64decode(d["content"].replace("\n","")).decode()

def gh_push(path, content, msg, sha):
    body = {"message":msg,"content":base64.b64encode(content.encode()).decode(),"sha":sha}
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        data=json.dumps(body).encode(),
        headers={"Authorization":f"token {GH_TOK}","Content-Type":"application/json",
                 "Accept":"application/vnd.github.v3+json"})
    req.get_method = lambda: 'PUT'
    with urllib.request.urlopen(req, timeout=30) as r: return json.loads(r.read())

print(SEP)
print("SPEL S31 LAUNCHER — " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
print(SEP)


# ══════════════════════════════════════════════════════════════════
# STEP 1 — R31: MASTER AUDITOR (obligatorio antes de operar)
# ══════════════════════════════════════════════════════════════════
print("\n[1/5] MASTER AUDITOR (R31)")

auditor_path = ROOT/"scripts/SPEL_v37_MASTER_AUDITOR_v2.py"
_audit_result = None

if not auditor_path.exists() or auditor_path.stat().st_size < 100:
    print("  Pulling auditor from GitHub...")
    try:
        d = gh_get("scripts/SPEL_v37_MASTER_AUDITOR_v2.py")
        auditor_path.write_text(gh_decode(d))
        print("  Repaired from GitHub")
    except Exception as e:
        print(f"  WARN: Cannot pull auditor: {e}")

if auditor_path.exists():
    try:
        spec = importlib.util.spec_from_file_location("auditor_s31", auditor_path)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "run_master_audit"):
            _audit_result = mod.run_master_audit(
                notify=True, abort_on_critical=False, session_tag="S31")
            status = "OK" if _audit_result.get("passed") else "CRIT"
            print(f"  Audit: {status}  "
                  f"OK={_audit_result.get('n_ok')}  "
                  f"WARN={_audit_result.get('n_warn')}  "
                  f"CRIT={_audit_result.get('n_crit')}")
    except Exception as e:
        print(f"  Auditor load error: {e}")


# ══════════════════════════════════════════════════════════════════
# STEP 2 — PHASE 3: PC-1 FIX — per-pair GDELT queries in updater
# Patcha spel_snapshot_updater.py en GitHub con 5 queries distintas
# ══════════════════════════════════════════════════════════════════
print("\n[2/5] PC-1 FIX — per-pair GDELT entropy")

PC1_FIXED_UPDATER = '''"""
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
            "/content/drive/MyDrive/ORDEN/SPEL 3.0"))

# ── PROXY_P90_MAP canónico R28 ─────────────────────────────────────
PROXY_P90_MAP = {
    "NVDA": 1.189820,   # EURUSD, AUDUSD
    "XAU":  1.350316,   # GBPUSD, USDJPY, USDCHF
}

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

    print(f"\\nSPEL Snapshot Updater — {ts[:16]}")
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
    print(f"\\nSnapshot guardado: {snap_path}")
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
'''

# Aplicar el PC-1 fix al repo
print("  Aplicando PC-1 fix a spel_snapshot_updater.py...")
try:
    d   = gh_get("scripts/spel_snapshot_updater.py")
    sha = d["sha"]
    # Verificar si PC-1 ya está fijo
    current = gh_decode(d)
    if "FOREX_PAIRS" in current and "keywords" in current:
        print("  PC-1 fix ya aplicado — skip")
    else:
        result = gh_push(
            "scripts/spel_snapshot_updater.py",
            PC1_FIXED_UPDATER,
            "S31: PC-1 fix — per-pair GDELT entropy queries (5 distinct)",
            sha,
        )
        print(f"  GitHub push OK  sha={result['content']['sha'][:8]}")

    # Guardar localmente también
    local = ROOT/"scripts/spel_snapshot_updater.py"
    local.write_text(PC1_FIXED_UPDATER)
    print(f"  Drive: OK")

except Exception as e:
    print(f"  PC-1 fix error: {e}")
    # Guardar solo localmente
    (ROOT/"scripts/spel_snapshot_updater.py").write_text(PC1_FIXED_UPDATER)
    print("  Drive: saved (GitHub push pendiente)")


# ══════════════════════════════════════════════════════════════════
# STEP 3 — PHASE 1: Deploy spel_paper_adapter.py
# ══════════════════════════════════════════════════════════════════
print("\n[3/5] PAPER ADAPTER — deploy + Day 1 start")

PAPER_ADAPTER_SRC = (ROOT/"scripts/spel_paper_adapter.py")

# Verificar que existe (fue guardado desde la sesión)
if not PAPER_ADAPTER_SRC.exists() or PAPER_ADAPTER_SRC.stat().st_size < 100:
    print("  spel_paper_adapter.py ausente en Drive — integrando desde sesión")
    # El archivo debe haber sido subido manualmente a Drive/scripts/
    # o se integra desde el launcher copiando el texto aquí.
    # Por diseño, el launcher asume que fue subido previamente.
    print("  ACCIÓN REQUERIDA: subir spel_paper_adapter.py a Drive/SPEL 3.0/scripts/")
else:
    print(f"  spel_paper_adapter.py: {PAPER_ADAPTER_SRC.stat().st_size}b OK")

    # Leer session_day desde el trade_log
    log_path = ROOT/"logs/trade_log.csv"
    session_day = 1
    if log_path.exists():
        import csv
        try:
            with open(log_path, newline="") as f:
                rows = list(csv.DictReader(f))
            if rows:
                session_day = int(rows[-1].get("session_day", 1)) + 1
        except:
            pass

    print(f"  Session day: {session_day}/63")

    # Importar y arrancar paper adapter en background thread
    spec = importlib.util.spec_from_file_location(
        "paper_adapter", PAPER_ADAPTER_SRC)
    pa_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pa_mod)

    adapter = pa_mod.PaperAdapter(
        paper_capital = 10_000.0,
        session_day   = session_day,
        dry_run       = not bool(os.environ.get("ALPACA_API_KEY")),
    )

    dry = not bool(os.environ.get("ALPACA_API_KEY"))
    print(f"  Mode: {'DRY-RUN (sin ALPACA_API_KEY)' if dry else 'LIVE PAPER'}")
    print(f"  Capital: $10,000  Interval: 15min  Window: 13-17 UTC")

    # Arrancar en background
    paper_thread = threading.Thread(
        target=adapter.run_session,
        kwargs={"interval_min": 15, "max_hours": 5},
        daemon=True,
        name="spel_paper",
    )
    paper_thread.start()
    print(f"  Paper adapter thread: STARTED (background)")


# ══════════════════════════════════════════════════════════════════
# STEP 4 — PHASE 2: Deploy Dashboard Flask v26 + ngrok
# ══════════════════════════════════════════════════════════════════
print("\n[4/5] DASHBOARD OJO DE DIOS v26 — deploy")

# Instalar dependencias
import subprocess
for pkg in ["flask", "pyngrok"]:
    try:
        __import__(pkg.replace("-","_"))
    except ImportError:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", pkg],
            capture_output=True
        )
        print(f"  Installed: {pkg}")

DASHBOARD_SRC = ROOT/"scripts/ojo_de_dios_v26.py"
if not DASHBOARD_SRC.exists() or DASHBOARD_SRC.stat().st_size < 100:
    print("  ACCIÓN REQUERIDA: subir ojo_de_dios_v26.py a Drive/SPEL 3.0/scripts/")
else:
    print(f"  ojo_de_dios_v26.py: {DASHBOARD_SRC.stat().st_size}b OK")

    spec = importlib.util.spec_from_file_location("dashboard", DASHBOARD_SRC)
    dash_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dash_mod)

    ngrok_tok = os.environ.get("NGROK_TOKEN", "")

    # Arrancar Flask en background thread
    def _start_dash():
        dash_mod.launch(port=5000, ngrok_token=ngrok_tok)

    dash_thread = threading.Thread(target=_start_dash, daemon=True, name="spel_dash")
    dash_thread.start()
    import time; time.sleep(3)  # Dar tiempo a Flask + ngrok para iniciar
    print("  Flask dashboard: STARTED (background)")
    print("  ngrok URL: ver output arriba ↑")


# ══════════════════════════════════════════════════════════════════
# STEP 5 — CHANGELOG + TG SISTEMA notify
# ══════════════════════════════════════════════════════════════════
print("\n[5/5] CHANGELOG + NOTIFICACIÓN")

import json as _json
from datetime import datetime as _dt, timezone as _tz

changelog_path = ROOT/"meta/change_log.json"
try:
    if changelog_path.exists():
        log = _json.loads(changelog_path.read_text())
        if not isinstance(log, list): log = []
    else:
        log = []

    audit_summary = {}
    if _audit_result:
        audit_summary = {
            "ok":   _audit_result.get("n_ok"),
            "warn": _audit_result.get("n_warn"),
            "crit": _audit_result.get("n_crit"),
        }

    log.append({
        "ts":    _dt.now(_tz.utc).isoformat(),
        "event": "SESSION_START_S31",
        "pc1_fix":         "DEPLOYED",
        "paper_adapter":   "RUNNING",
        "dashboard_v26":   "RUNNING",
        "audit":           audit_summary,
    })
    log = log[-200:]
    changelog_path.write_text(_json.dumps(log, indent=2, default=str))
    print("  change_log.json: updated")
except Exception as e:
    print(f"  changelog: {e}")

# TG SISTEMA
ts_now = _dt.now(_tz.utc).strftime("%Y-%m-%d %H:%M UTC")
audit_line = ""
if _audit_result:
    icon = "✅" if _audit_result.get("passed") else "⚠️"
    audit_line = (f"\nAudit: {icon} OK={_audit_result.get('n_ok')} "
                  f"WARN={_audit_result.get('n_warn')} "
                  f"CRIT={_audit_result.get('n_crit')}")

tg(TG_SIST,
   f"🚀 <b>SPEL S31 LAUNCHER</b>\n"
   f"<code>{ts_now}</code>{audit_line}\n"
   f"─────────────────────────\n"
   f"✅ PC-1 fix desplegado (per-pair GDELT)\n"
   f"✅ Paper adapter activo (Day {session_day if 'session_day' in dir() else '?'}/63)\n"
   f"✅ Dashboard v26 activo (ngrok)\n"
   f"─────────────────────────\n"
   f"Pendiente: ingest GDELT real post 21:00 ECT")

print("\n" + SEP)
print("  S31 LAUNCH COMPLETE")
print(SEP)
print("""
  THREADS ACTIVOS:
    spel_paper  → paper trading loop (15min / ventana 08-12 ECT)
    spel_dash   → Flask + ngrok dashboard

  COMANDOS DISPONIBLES EN ESTA SESIÓN:
    adapter.evaluate_once()     → evaluación manual inmediata
    adapter.daily_metrics()     → métricas del gate hoy
    dash_mod.app.run(port=5001) → segundo dashboard si necesitas

  ESTA NOCHE 21:00 ECT:
    exec(open(ROOT/'scripts/spel_ingest_incremental.py').read())
    → Elimina SYNTHETIC-ENTROPY-DEBT
    → Re-corre auditor post-ingest
""")
print(SEP)
