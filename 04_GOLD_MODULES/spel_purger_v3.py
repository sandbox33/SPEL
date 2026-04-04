"""
spel_purger_v3.py
═══════════════════════════════════════════════════════════════════════
SPEL v3.0 · Centinela · Ley 4: Supremacía del Registro y Purga

Bóveda Zombi — Mission:
  Escanea scripts/ y codigo/core/ en busca de archivos que NO deben
  existir en producción. Los mueve a ARCHIVE_S37/ (fuera de sys.path).

Patrones zombi detectados:
  1. Sufijos de sesión   : _bak, _bak_s*, _fix_s2*, _fix_s3*
  2. Copias de Drive     : " (1).py", " (2).py" (espacio + paréntesis)
  3. Scripts de emergencia: spel_fix_s21*.py → spel_fix_s36*.py
  4. Parches one-shot    : spel_patch_*.py
  5. Diagnósticos legacy : spel_diagnostico_*.py
  6. Notebooks incrustados: *_NOTEBOOK.py (82KB, no son core)
  7. Versiones numéricas : ojo_de_dios_v23.py (si hay v26)

Comportamiento:
  - Por defecto: DRY-RUN (imprime lo que haría, no mueve nada)
  - Con --execute: mueve a ARCHIVE_S37/ y genera purge_report.json
  - Nunca elimina — ARCHIVE_S37/ es la cuarentena, no la papelera

Usage:
  python scripts/spel_purger_v3.py           # dry-run
  python scripts/spel_purger_v3.py --execute # ejecutar purga real
  python scripts/spel_purger_v3.py --report  # solo generar reporte JSON
"""

import sys, os, json, hashlib, shutil, re, argparse
from pathlib import Path
from datetime import datetime, timezone

# ── Configuración ─────────────────────────────────────────────────────────────
REPO_ROOT   = Path(os.environ.get("SPEL_BASE_DIR",
                                  os.getcwd()))
ARCHIVE_DIR = REPO_ROOT / "ARCHIVE_S37"
REPORT_FILE = REPO_ROOT / "purge_report.json"

# Directorios que se escanean buscando zombis
SCAN_DIRS = [
    REPO_ROOT / "scripts",
    REPO_ROOT / "codigo" / "core",
    REPO_ROOT / "codigo" / "interface",
    REPO_ROOT,   # raíz del repo (archivos sueltos)
]

# Archivos que NUNCA se tocan — whitelist por nombre exacto
PROTECTED = {
    "spel_master.yml",
    "spel_score_engine.py",
    "spel_session_start.py",
    "spel_snapshot_updater.py",
    "spel_paper_adapter_v2.py",
    "spel_export_feature_cache.py",
    "spel_cloud_inference.py",
    "spel_forex_iq.py",
    "spel_forex_iq_runner.py",
    "spel_harvester_v3.py",      # versión canónica del harvester
    "spel_ingest_incremental.py",
    "spel_daily_runner.py",
    "spel_preflight_s24.py",
    "spel_telegram_setup.py",
    "spel_github_setup.py",
    "spel_p90_recalibrate.py",
    "spel_session_start.py",
    "main_ui.py",
    "spel_purger_v3.py",         # no se autoarchiva
    "spel_sentinel_core.py",
    "SPEL_DNA_Scanner.py",
    "SPEL_INSTITUTIONAL_AUDITOR_V41.py",
    "SPEL_Universal_Harvester.py",
    "SPEL_Antifragile_Core.py",
}

# Patrones regex que identifican zombis
ZOMBIE_PATTERNS = [
    # Drive duplicates: "archivo (1).py", "archivo (2).py"
    re.compile(r"^.+\s\(\d+\)\.py$"),

    # Session fix scripts: spel_fix_s21.py, spel_fix_s21b.py, etc.
    re.compile(r"^spel_fix_s\d+[a-z]?\.py$"),

    # Backup suffixes: spel_harvester_v3_bak_s22c.py
    re.compile(r"^.+_bak(_s\d+[a-z]?)?\.(py|json)$"),

    # Patch one-shots: spel_patch_mathengine.py
    re.compile(r"^spel_patch_.+\.py$"),

    # Diagnostics: spel_diagnostico_s21b.py
    re.compile(r"^spel_diagnostico_.+\.py$"),

    # Numbered notebooks: SPEL_S27_NOTEBOOK.py, SPEL_S28_retraining.py
    re.compile(r"^SPEL_S\d+_.+\.py$"),

    # Old launchers: SPEL_S31_LAUNCHER.py
    re.compile(r"^SPEL_S\d+_LAUNCHER\.py$"),

    # Stress tests: SPEL_S34_stress_test_v2.py
    re.compile(r"^SPEL_S\d+_stress_test.+\.py$"),

    # Versioned dashboards superseded: ojo_de_dios_v23.py (v23 < v26)
    re.compile(r"^ojo_de_dios_v\d+\.py$"),

    # Audit JSONs with timestamps: auditoria_total_20260311_1550.json
    re.compile(r"^auditoria_total_\d{8}_\d{4}\.json$"),

    # SHA registry backups: SHA_REGISTRY_backup_S24_*.json
    re.compile(r"^SHA_REGISTRY_backup_.+\.json$"),

    # Recovery scripts: spel_entropy_recovery.py, spel_fix_vix_leakage.py
    re.compile(r"^spel_(entropy_recovery|fix_vix_leakage|retrain_v5_clean)\.py$"),

    # Master auditor versions < V41
    re.compile(r"^SPEL_(v\d+_ALPACA_FINAL|V40_ALPACA_FINAL|MASTER_AUDITOR_V4[0])\.py$"),
    re.compile(r"^SPEL_v\d+_MASTER_AUDITOR_v\d+\.py$"),

    # Godel bound backup
    re.compile(r"^godel_bound_bak.+\.py$"),
]

# ── Utilidades ────────────────────────────────────────────────────────────────

def _sha12(path: Path) -> str:
    """SHA-256 primeros 12 hex — para el reporte de integridad."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()[:12]


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_zombie(filename: str) -> tuple[bool, str]:
    """
    Retorna (True, razón) si el archivo coincide con un patrón zombi
    y no está en la whitelist.
    """
    if filename in PROTECTED:
        return False, ""

    for pattern in ZOMBIE_PATTERNS:
        if pattern.match(filename):
            return True, pattern.pattern
    return False, ""


def _resolve_duplicate_dest(dest: Path) -> Path:
    """Si ya existe un archivo en el destino, añade timestamp para evitar colisión."""
    if not dest.exists():
        return dest
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return dest.with_name(f"{dest.stem}_{ts}{dest.suffix}")

# ── Escáner principal ─────────────────────────────────────────────────────────

def scan() -> list[dict]:
    """
    Escanea SCAN_DIRS y retorna lista de zombis encontrados.
    Cada ítem: {path, filename, reason, size_kb, sha12, scan_dir}
    """
    zombies = []
    seen    = set()   # evitar duplicados si un archivo está en dos scan dirs

    for scan_dir in SCAN_DIRS:
        if not scan_dir.exists():
            continue
        # Solo nivel superficial — no recursivo (evita escanear ARCHIVE_S37)
        depth = 1 if scan_dir == REPO_ROOT else 2
        for path in sorted(scan_dir.rglob("*.py") if depth > 1
                           else scan_dir.glob("*.py")):
            # No entrar en subdirectorios de la raíz (solo archivos en raíz)
            if scan_dir == REPO_ROOT and path.parent != REPO_ROOT:
                continue
            # No escanear el propio ARCHIVE_S37 ni QUARANTINE_BIN
            if any(p.name in ("ARCHIVE_S37", "QUARANTINE_BIN", "_ARCHIVE_S36",
                               ".git", "__pycache__")
                   for p in path.parents):
                continue

            abs_str = str(path.resolve())
            if abs_str in seen:
                continue
            seen.add(abs_str)

            is_z, reason = _is_zombie(path.name)
            if is_z:
                zombies.append({
                    "path":     str(path),
                    "filename": path.name,
                    "reason":   reason,
                    "size_kb":  round(path.stat().st_size / 1024, 2),
                    "sha12":    _sha12(path),
                    "scan_dir": str(scan_dir),
                })

    # También escanear JSONs de auditoría en meta/
    meta_dir = REPO_ROOT / "meta"
    if meta_dir.exists():
        for path in meta_dir.glob("*.json"):
            is_z, reason = _is_zombie(path.name)
            if is_z:
                zombies.append({
                    "path":     str(path),
                    "filename": path.name,
                    "reason":   reason,
                    "size_kb":  round(path.stat().st_size / 1024, 2),
                    "sha12":    _sha12(path),
                    "scan_dir": str(meta_dir),
                })

    return zombies


def print_scan_results(zombies: list[dict], execute: bool) -> None:
    mode = "EJECUTANDO PURGA" if execute else "DRY-RUN (usa --execute para aplicar)"
    print(f"\n{'═'*66}")
    print(f"  SPEL Purger v3.0 · Ley 4: Bóveda Zombi · {mode}")
    print(f"  {_ts()}")
    print(f"{'═'*66}\n")

    if not zombies:
        print("  ✅ Ningún archivo zombi detectado. El entorno está limpio.")
        return

    total_kb = sum(z["size_kb"] for z in zombies)
    print(f"  Zombis detectados: {len(zombies)} archivos ({total_kb:.1f} KB total)\n")

    for z in zombies:
        status = "→ MOVER" if execute else "→ PENDIENTE"
        rel    = Path(z["path"]).relative_to(REPO_ROOT) if REPO_ROOT in Path(z["path"]).parents else z["path"]
        print(f"  ☠  {z['filename']:<45} {z['size_kb']:>6.1f}KB  {status}")
        print(f"      {rel}")
        print(f"      SHA: {z['sha12']} | Patrón: {z['reason'][:50]}")

    print(f"\n  Destino: {ARCHIVE_DIR}")
    if not execute:
        print(f"\n  ℹ️  Ejecutar con --execute para aplicar los cambios.")


def execute_purge(zombies: list[dict]) -> list[dict]:
    """
    Mueve los zombis a ARCHIVE_S37/. Retorna lista de movimientos realizados.
    NUNCA elimina — la cuarentena es reversible.
    """
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    moved = []

    for z in zombies:
        src  = Path(z["path"])
        if not src.exists():
            print(f"  ⚠️  Ya no existe: {src.name} — skip")
            continue

        # Preservar estructura relativa dentro del ARCHIVE
        try:
            rel  = src.relative_to(REPO_ROOT)
            dest = ARCHIVE_DIR / rel
        except ValueError:
            dest = ARCHIVE_DIR / src.name

        dest = _resolve_duplicate_dest(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.move(str(src), str(dest))
            z["archived_to"] = str(dest)
            z["moved_at"]    = _ts()
            moved.append(z)
            print(f"  ✅ ARCHIVADO: {src.name} → {dest.relative_to(REPO_ROOT)}")
        except Exception as e:
            z["error"] = str(e)
            print(f"  ❌ ERROR moviendo {src.name}: {e}")

    return moved


def write_report(zombies: list[dict], moved: list[dict], execute: bool) -> None:
    report = {
        "ts":         _ts(),
        "mode":       "execute" if execute else "dry-run",
        "scan_dirs":  [str(d) for d in SCAN_DIRS],
        "total_found":len(zombies),
        "total_moved":len(moved),
        "total_kb":   round(sum(z["size_kb"] for z in zombies), 2),
        "zombies":    zombies,
        "moved":      moved,
    }
    REPORT_FILE.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"\n  📋 Reporte escrito: {REPORT_FILE}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="SPEL Purger v3 — Ley 4: Bóveda Zombi"
    )
    parser.add_argument("--execute", action="store_true",
                        help="Ejecutar la purga real (por defecto: dry-run)")
    parser.add_argument("--report",  action="store_true",
                        help="Solo generar reporte JSON, no imprimir tabla")
    args = parser.parse_args()

    zombies = scan()
    print_scan_results(zombies, execute=args.execute)

    moved = []
    if args.execute and zombies:
        print(f"\n  Iniciando movimiento a {ARCHIVE_DIR}...\n")
        moved = execute_purge(zombies)
        print(f"\n  Purga completa: {len(moved)}/{len(zombies)} archivos archivados.")

    write_report(zombies, moved, execute=args.execute)

    # Exit code 0 siempre — la purga nunca debe bloquear el pipeline
    sys.exit(0)


if __name__ == "__main__":
    main()
