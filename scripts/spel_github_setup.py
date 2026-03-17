"""
SPEL — GitHub Setup & Sync Scripts
====================================
Ejecutar UNA SOLA VEZ en Colab para:
  1. Inicializar estructura del repo en Drive
  2. Crear los scripts de sync (sync_ohlcv.py, sync_gdelt.py)
  3. Empujar a GitHub con configuración de Actions

Pre-requisitos:
  - git configurado en Colab (nombre + email)
  - Token de GitHub en Colab Secrets como GITHUB_TOKEN
  - Repo creado en GitHub (puede estar vacío)
  - Google Drive montado

Después de este setup: un click en GitHub Actions → datos actualizados.
"""

import os, subprocess, json
from pathlib import Path
from datetime import datetime

# ── CONFIGURAR AQUÍ ─────────────────────────────────────────────
GITHUB_USER  = "TU_USUARIO_GITHUB"    # ← cambiar
GITHUB_REPO  = "spel-v2"              # ← nombre del repo en GitHub
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")  # desde Colab Secrets
DRIVE_ROOT   = Path("/content/drive/MyDrive/SPEL-v2.0")
REPO_LOCAL   = Path("/content/spel_repo")   # directorio local del repo
# ────────────────────────────────────────────────────────────────

def run(cmd: str, cwd=None, check=True):
    """Ejecutar comando y mostrar output."""
    print(f"  $ {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if result.stdout.strip():
        print(f"    {result.stdout.strip()}")
    if result.returncode != 0:
        print(f"    STDERR: {result.stderr.strip()}")
        if check:
            raise RuntimeError(f"Comando falló: {cmd}")
    return result


print("=" * 60)
print("SPEL — GitHub Setup")
print("=" * 60)

# ── 1. CREAR ESTRUCTURA DEL REPO ──────────────────────────────
print("\n[1/5] Creando estructura del repo...")
REPO_LOCAL.mkdir(parents=True, exist_ok=True)

dirs = [
    REPO_LOCAL / '.github' / 'workflows',
    REPO_LOCAL / 'spel_scripts',
    REPO_LOCAL / 'spel_data' / 'ohlcv',
    REPO_LOCAL / 'spel_data' / 'gdelt_raw',
    REPO_LOCAL / 'spel_data' / 'meta',
]
for d in dirs:
    d.mkdir(parents=True, exist_ok=True)
    print(f"  ✅ {d}")

# ── 2. CREAR sync_ohlcv.py ─────────────────────────────────────
print("\n[2/5] Creando sync_ohlcv.py...")
sync_ohlcv_content = '''"""
SPEL — Sync OHLCV desde Yahoo Finance
Descarga los últimos N días de OHLCV para cada activo.
Preserva el parquet histórico haciendo append solo de filas nuevas.
"""
import argparse, hashlib, json
from datetime import datetime, timedelta
from pathlib import Path
import polars as pl
import yfinance as yf
import numpy as np

TICKERS = {
    'NVDA':    'NVDA',
    'BTC':     'BTC-USD',
    'XAU':     'GC=F',
    'NIFTY50': '^NSEI',
}
SHA_ESPERADOS = {
    'NVDA':    'f496c377c7ae',
    'BTC':     'a2c4e6f6e816',
    'XAU':     'a8e10cff2e80',
    'NIFTY50': '981989b7024d',
}

def sha12(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""): h.update(chunk)
    return h.hexdigest()[:12]

def download_ohlcv(activo, ticker, days=10):
    end   = datetime.utcnow()
    start = end - timedelta(days=days + 5)  # buffer para feriados
    df = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                     end=end.strftime("%Y-%m-%d"), progress=False, auto_adjust=True)
    if df.empty:
        print(f"  ⚠️  {activo}: sin datos desde Yahoo")
        return None
    df = df.reset_index()
    df.columns = [c.lower().replace(" ", "_") for c in df.columns]
    # Rename to SPEL schema
    rename = {"date": "date", "open": "open", "high": "high",
              "low": "low", "close": "close", "volume": "volume"}
    df = df[[c for c in rename.keys() if c in df.columns]]
    pl_df = pl.from_pandas(df)
    pl_df = pl_df.with_columns(
        pl.col("date").cast(pl.Datetime("ms", "UTC"))
    )
    return pl_df.sort("date")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--activos", default="NVDA,BTC,XAU,NIFTY50")
    parser.add_argument("--days", type=int, default=10)
    parser.add_argument("--out-dir", default="spel_data/ohlcv")
    args = parser.parse_args()

    activos_list = [a.strip() for a in args.activos.split(",")]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    resultados = {}
    for activo in activos_list:
        ticker = TICKERS.get(activo)
        if not ticker:
            print(f"  ⚠️  {activo}: ticker no configurado")
            continue

        print(f"  Descargando {activo} ({ticker})...")
        fresh = download_ohlcv(activo, ticker, days=args.days)
        if fresh is None:
            continue

        # Guardar solo las filas nuevas (o el parquet completo si no existe)
        out_path = out_dir / f"{activo}_ohlcv_fresh.parquet"
        fresh.write_parquet(str(out_path))
        sha = sha12(out_path)
        print(f"  ✅ {activo}: {len(fresh)} filas guardadas | SHA: {sha}")
        resultados[activo] = {"n_filas": len(fresh), "sha": sha,
                              "fecha_min": str(fresh["date"].min()),
                              "fecha_max": str(fresh["date"].max())}

    # Guardar resumen
    with open(out_dir / "sync_summary.json", "w") as f:
        json.dump({"timestamp": datetime.utcnow().isoformat(), "activos": resultados}, f, indent=2)
    print(f"  Resumen guardado en {out_dir}/sync_summary.json")

if __name__ == "__main__":
    main()
'''
(REPO_LOCAL / 'spel_scripts' / 'sync_ohlcv.py').write_text(sync_ohlcv_content)
print("  ✅ spel_scripts/sync_ohlcv.py")

# ── 3. CREAR sync_gdelt.py ─────────────────────────────────────
print("\n[3/5] Creando sync_gdelt.py...")
sync_gdelt_content = '''"""
SPEL — Sync GDELT últimos N días
Descarga CSVs nativos de GDELT para los últimos N días.
Filtra por activos relevantes y calcula entropía incremental.
"""
import argparse, requests, zipfile, io, json
from datetime import datetime, timedelta
from pathlib import Path
import polars as pl
import numpy as np

GDELT_BASE = "http://data.gdeltproject.org/gdeltv2"
ACTIVOS = ["NVDA", "BTC", "XAU", "NIFTY50"]
KEYWORDS = {
    "NVDA":    ["NVIDIA", "semiconductor", "GPU", "AI chip"],
    "BTC":     ["Bitcoin", "cryptocurrency", "crypto", "blockchain"],
    "XAU":     ["gold", "bullion", "precious metal", "safe haven"],
    "NIFTY50": ["India", "NSE", "Nifty", "Mumbai", "BSE"],
}

def get_gdelt_urls(days=7):
    """Genera URLs de los últimos N días (archivos 15min de GDELT)."""
    urls = []
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    for d in range(days):
        dt = now - timedelta(days=d)
        # GDELT publica cada 15 minutos, tomamos los de las 0h de cada día
        for h in [0, 6, 12, 18]:
            ts = dt.replace(hour=h)
            ts_str = ts.strftime("%Y%m%d%H%M%S")
            urls.append(f"{GDELT_BASE}/{ts_str}.export.CSV.zip")
    return urls

def entropy_shannon(counts):
    """Entropía de Shannon de una distribución de conteos."""
    total = sum(counts)
    if total == 0: return 0.0
    probs = [c / total for c in counts if c > 0]
    return float(-sum(p * np.log2(p) for p in probs))

def download_and_process(url, activos):
    """Descarga un ZIP de GDELT y extrae entropía por activo."""
    try:
        r = requests.get(url, timeout=30)
        if r.status_code != 200: return None
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            fname = z.namelist()[0]
            with z.open(fname) as f:
                lines = f.read().decode("latin-1").strip().split("\\n")
    except Exception as e:
        print(f"  WARN: {url.split("/")[-1]} — {e}")
        return None

    # Extraer campos relevantes (GDELT CSV tiene 58 cols)
    # Col 26: GoldsteinScale, Col 27: NumMentions, Col 34: AvgTone
    rows_by_activo = {a: [] for a in activos}
    for line in lines:
        parts = line.split("\\t")
        if len(parts) < 35: continue
        full_text = " ".join(parts[:30]).upper()
        for activo in activos:
            if any(kw.upper() in full_text for kw in KEYWORDS[activo]):
                try:
                    goldstein = float(parts[26]) if parts[26] else 0.0
                    mentions  = int(parts[27]) if parts[27] else 0
                    tone      = float(parts[34]) if parts[34] else 0.0
                    rows_by_activo[activo].append((goldstein, mentions, tone))
                except (ValueError, IndexError):
                    pass
    return rows_by_activo

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--out-dir", default="spel_data/gdelt_raw")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    urls = get_gdelt_urls(args.days)
    print(f"  Descargando {len(urls)} archivos GDELT ({args.days} días)...")

    datos_por_activo = {a: [] for a in ACTIVOS}
    for i, url in enumerate(urls):
        if i % 10 == 0:
            print(f"  Progreso: {i}/{len(urls)}...")
        resultado = download_and_process(url, ACTIVOS)
        if resultado:
            dt_str = url.split("/")[-1][:14]
            try:
                dt = datetime.strptime(dt_str, "%Y%m%d%H%M%S")
                date_str = dt.strftime("%Y-%m-%d")
            except:
                continue
            for activo, rows in resultado.items():
                if rows:
                    goldstein_vals = [r[0] for r in rows]
                    tone_vals      = [r[2] for r in rows]
                    n_events       = len(rows)
                    ent            = entropy_shannon([1] * n_events)  # uniforme como proxy
                    datos_por_activo[activo].append({
                        "date": date_str,
                        "n_events": n_events,
                        "goldstein_mean": float(np.mean(goldstein_vals)),
                        "tone_variance":  float(np.var(tone_vals)),
                        "entropy_shannon": ent,
                    })

    # Agregar por día y guardar
    for activo, rows in datos_por_activo.items():
        if not rows: continue
        df = pl.from_dicts(rows)
        df = (df.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d"))
               .group_by("date")
               .agg([
                   pl.col("n_events").sum(),
                   pl.col("goldstein_mean").mean(),
                   pl.col("tone_variance").mean(),
                   pl.col("entropy_shannon").mean(),
               ])
               .sort("date"))
        out_path = out_dir / f"{activo}_gdelt_fresh.parquet"
        df.write_parquet(str(out_path))
        print(f"  ✅ {activo}: {len(df)} días guardados → {out_path.name}")

if __name__ == "__main__":
    main()
'''
(REPO_LOCAL / 'spel_scripts' / 'sync_gdelt.py').write_text(sync_gdelt_content)
print("  ✅ spel_scripts/sync_gdelt.py")

# ── 4. COPIAR WORKFLOW DE GITHUB ACTIONS ─────────────────────
print("\n[4/5] Configurando GitHub Actions workflow...")
import shutil
workflow_src = Path("/content/drive/MyDrive/SPEL-v2.0/meta/spel_github_sync.yml")
workflow_dst = REPO_LOCAL / ".github" / "workflows" / "spel_sync.yml"
if workflow_src.exists():
    shutil.copy(workflow_src, workflow_dst)
    print(f"  ✅ Copiado desde Drive")
else:
    print(f"  ⚠️  spel_github_sync.yml no encontrado en Drive")
    print(f"     Cópialo manualmente a: {workflow_dst}")

# ── 5. CREAR .gitignore ────────────────────────────────────────
gitignore = """
# Datos grandes — nunca en git (usar Drive)
*.parquet
*.pt
*.pth
*.csv
*.zip
spel_data/

# Python
__pycache__/
*.pyc
.ipynb_checkpoints/

# Secretos
*.env
secrets.json
"""
(REPO_LOCAL / '.gitignore').write_text(gitignore)

# ── README ────────────────────────────────────────────────────
readme = f"""# SPEL — Socio-Political Entropy Loss

Sistema de predicción de regímenes de mercado basado en entropía mediática.

## Setup rápido

```bash
# 1. Clonar
git clone https://github.com/{GITHUB_USER}/{GITHUB_REPO}

# 2. Un click en GitHub → Actions → "SPEL — Sync & Validate Data" → Run workflow
```

## Estructura

```
spel_scripts/
  sync_ohlcv.py     ← Descarga OHLCV fresco (Yahoo Finance)
  sync_gdelt.py     ← Descarga GDELT últimos N días
.github/workflows/
  spel_sync.yml     ← Workflow: sync automático + DNA audit
```

## Datos (en Drive, no en git)

Los datos de producción están en Google Drive:
`/content/drive/MyDrive/SPEL-v2.0/data_lake/`

SHA verificados:
- NVDA: `3627a749da49`
- BTC:  `a2c4e6f6e816`
- XAU:  `a8e10cff2e80`
- NIFTY50: `5e9624595c03`

Generado: {datetime.utcnow().strftime('%Y-%m-%d')}
"""
(REPO_LOCAL / 'README.md').write_text(readme)

# ── 6. INICIALIZAR GIT Y PUSH ─────────────────────────────────
print("\n[5/5] Inicializando git y haciendo push...")
if not GITHUB_TOKEN:
    print("  ⚠️  GITHUB_TOKEN no encontrado en variables de entorno.")
    print("  Cargarlo con: import os; os.environ['GITHUB_TOKEN'] = 'ghp_...'")
    print("  O desde Colab Secrets (recomendado)")
else:
    try:
        run("git init", cwd=REPO_LOCAL)
        run('git config user.email "spel@trading.bot"', cwd=REPO_LOCAL)
        run('git config user.name "SPEL Bot"', cwd=REPO_LOCAL)
        run("git add -A", cwd=REPO_LOCAL)
        run('git commit -m "SPEL v2.0 — Initial setup con GitHub Actions"', cwd=REPO_LOCAL)
        remote_url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{GITHUB_REPO}.git"
        run(f"git remote add origin {remote_url}", cwd=REPO_LOCAL, check=False)
        run("git branch -M main", cwd=REPO_LOCAL)
        run("git push -u origin main", cwd=REPO_LOCAL)
        print("\n  ✅ Push exitoso!")
        print(f"  Repo: https://github.com/{GITHUB_USER}/{GITHUB_REPO}")
        print(f"  Actions: https://github.com/{GITHUB_USER}/{GITHUB_REPO}/actions")
    except RuntimeError as e:
        print(f"\n  ❌ {e}")
        print("  Revisar GITHUB_USER, GITHUB_REPO y GITHUB_TOKEN")

print("\n" + "=" * 60)
print("SETUP COMPLETADO")
print("=" * 60)
print(f"""
Para actualizar datos con un click:
  1. Ir a https://github.com/{GITHUB_USER}/{GITHUB_REPO}/actions
  2. Click en "SPEL — Sync & Validate Data"
  3. Click en "Run workflow"
  4. Los parquets frescos quedan como artifacts descargables

Para sincronización automática diaria:
  Ya está configurado: cron "0 6 * * 1-5" (lun-vie 6am UTC)

Para descargar artifacts a Drive desde Colab:
  import requests
  # Ver spel_scripts/download_artifacts.py (a crear en siguiente sesión)
""")
