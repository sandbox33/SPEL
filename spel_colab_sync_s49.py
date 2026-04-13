"""
spel_colab_sync_s49.py — SPEL 3.0 · S49 · Colab Sync Orchestrator
Holmes OS V4.0 · Hinc Omnia Cerno

Corre en Colab. Hace TRES cosas en secuencia:
  1. Limpia workflows duplicados en GitHub via API
  2. Push limpio de módulos GOLD (sin deps de Drive/Colab)
  3. Resetea last_signal.json con timestamp actual → rompe el Bucle de Amnesia RC-02

Prerequisito: CELL 0 de SPEL_S49_LANZAMIENTO.ipynb ejecutada
(Drive montado, secrets cargados en os.environ)

Restricciones:
  R37: no torch top-level
  EF-23: no toca gdelt_foundation ni critical_loss
  EF-25: no añade Holmes/sandbox a sys.path
  R42: ningún token hardcodeado — todo de os.environ
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ─── Config (from environment — no hardcodes) ────────────────────────────────

GH_TOKEN   = os.getenv("GITHUB_TOKEN", "")
GH_REPO    = os.getenv("GH_REPO", "sandbox33/SPEL")
TG_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TG_SISTEMA = os.getenv("TELEGRAM_SISTEMA", "-1003712424420")
TG_CAOS    = os.getenv("TELEGRAM_CHAOS", "")

ROOT  = Path(os.getenv("SPEL_BASE_DIR", "/content/drive/MyDrive/ORDEN/SPEL 3.0"))
VAULT = ROOT / "00_VAULT"

# Módulos GOLD que van a GitHub — NINGUNO puede tener google.colab import
# (verificado por AST_SCAN_STEP más abajo)
GOLD_MODULES = [
    "04_GOLD_MODULES/spel_orchestrator_v10.py",
    "04_GOLD_MODULES/spel_forex_bridge.py",
    "04_GOLD_MODULES/spel_bayesian_core.py",
    "04_GOLD_MODULES/spel_score_engine.py",
    "04_GOLD_MODULES/spel_dead_man_switch.py",   # 01_HOLMES_OPS in some layouts
    "04_GOLD_MODULES/spel_harvester_v3.py",
    "04_GOLD_MODULES/spel_ingest_incremental.py",
    "04_GOLD_MODULES/spel_paper_adapter_v2.py",
    "04_GOLD_MODULES/spel_web3_adapter.py",
    "04_GOLD_MODULES/spel_math_engine.py",
    "04_GOLD_MODULES/spel_backbone_engine.py",
    "04_GOLD_MODULES/capa_c_inference.py",
    "04_GOLD_MODULES/spel_scalping_tab.py",
    "04_GOLD_MODULES/spel_graph_tab.py",
    # commons (purified version — no /content/drive hardcodes)
    "spel_commons.py",
]

# Modules that must NEVER go to GH (Drive/Colab-only domain)
DRIVE_ONLY_MODULES = {
    "SPEL_v37_MASTER_AUDITOR_v2.py",
    "dna_sovereign.py",
    "spel_guardian.py",  # invokes Drive permissions
    "spel_auditoria_total.py",
    "SPEL_INSTITUTIONAL_AUDITOR_V41.py",
}

# Ghost file that caused FileNotFoundError — must not exist in repo
GHOST_FILES = [
    "scripts/j4_increment_gate.py",
    "scripts/",  # entire scripts/ dir — nothing valid there
]


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    icons = {"INFO": "ℹ️", "OK": "✅", "WARN": "⚠️", "ERR": "⛔", "SKIP": "⏭"}
    print(f"[{ts}] {icons.get(level,'·')} {msg}")


def _gh_api(
    path: str,
    method: str = "GET",
    payload: Optional[dict] = None,
    accept: str = "application/vnd.github+json",
) -> Optional[dict]:
    """GitHub API call. Returns parsed JSON or None on error."""
    if not GH_TOKEN:
        _log("GITHUB_TOKEN missing — GH API unavailable", "ERR")
        return None
    url = f"https://api.github.com/{path.lstrip('/')}"
    data = json.dumps(payload).encode() if payload else None
    req  = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept":        accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type":  "application/json",
            "User-Agent":    "HolmesOS/4.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        _log(f"GH API {method} {path} → HTTP {e.code}: {e.read()[:200].decode(errors='replace')}", "WARN")
        return None
    except Exception as e:
        _log(f"GH API error: {e}", "WARN")
        return None


def _tg(msg: str, chat_id: Optional[str] = None) -> None:
    """Fire-and-forget Telegram message."""
    chat = chat_id or TG_SISTEMA
    if not TG_TOKEN or not chat:
        return
    try:
        payload = json.dumps({"chat_id": chat, "text": msg[:4000]}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=6)
    except Exception:
        pass


def _sha12(content: str | bytes) -> str:
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()[:12]


def _git(cmd: list[str], cwd: Optional[Path] = None, capture: bool = True) -> tuple[int, str]:
    """Run git command. Returns (returncode, output)."""
    result = subprocess.run(
        ["git"] + cmd,
        cwd=str(cwd or ROOT),
        capture_output=capture,
        text=True,
    )
    out = (result.stdout + result.stderr).strip()
    return result.returncode, out


# ─── STEP 1: Clean duplicate workflows ───────────────────────────────────────

def step1_clean_workflows() -> int:
    """
    List active workflow runs for duplicate/stale workflows and disable them.
    Keeps only 'SPEL Universe Patrol' (spel_universe.yml) as the canonical workflow.
    Returns number of workflows deactivated.
    """
    _log("STEP 1 — Cleaning duplicate workflows", "INFO")

    workflows = _gh_api(f"repos/{GH_REPO}/actions/workflows")
    if not workflows:
        _log("Cannot reach GH API — skipping workflow cleanup", "WARN")
        return 0

    canonical = "spel_universe.yml"
    deactivated = 0
    kept = []

    for wf in workflows.get("workflows", []):
        wf_id   = wf["id"]
        wf_file = wf.get("path", "").split("/")[-1]
        wf_name = wf.get("name", "")
        state   = wf.get("state", "")

        if wf_file == canonical:
            _log(f"  ✅ KEEP [{wf_id}] {wf_name} ({wf_file})", "OK")
            kept.append(wf_id)
            continue

        # Disable any other active workflow
        if state == "active":
            resp = _gh_api(
                f"repos/{GH_REPO}/actions/workflows/{wf_id}/disable",
                method="PUT",
            )
            if resp is not None:
                _log(f"  ⏹  DISABLED [{wf_id}] {wf_name} ({wf_file})", "WARN")
                deactivated += 1
            else:
                _log(f"  ⚠️  Failed to disable [{wf_id}] {wf_name}", "WARN")
        else:
            _log(f"  ⏭  ALREADY INACTIVE [{wf_id}] {wf_name} state={state}", "SKIP")

    _log(f"STEP 1 done: {deactivated} disabled, {len(kept)} kept", "OK")
    return deactivated


# ─── STEP 2: AST scan + push GOLD modules clean ──────────────────────────────

def _ast_has_colab(filepath: Path) -> list[str]:
    """Return list of google.colab imports found in file. Empty = clean."""
    import ast as _ast
    violations = []
    try:
        tree = _ast.parse(filepath.read_text(errors="replace"))
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.Import, _ast.ImportFrom)):
                mod = getattr(node, "module", "") or ""
                names = [a.name for a in getattr(node, "names", [])]
                if "google.colab" in mod or any("google.colab" in n for n in names):
                    violations.append(f"Line ~{node.lineno}: {mod or names}")
    except Exception as e:
        violations.append(f"AST parse error: {e}")
    return violations


def step2_push_gold_modules() -> dict:
    """
    For each GOLD module:
      1. AST scan — reject if google.colab found (EF-COLAB)
      2. Push to GH via Contents API (atomic, with SHA for updates)
    Returns summary dict.
    """
    _log("STEP 2 — AST scan + push GOLD modules to GitHub", "INFO")

    results = {"pushed": [], "rejected": [], "skipped": [], "errors": []}

    for rel_path in GOLD_MODULES:
        local = ROOT / rel_path
        if not local.exists():
            _log(f"  ⏭  {rel_path} — not found locally", "SKIP")
            results["skipped"].append(rel_path)
            continue

        # Check it's not in the drive-only blocklist
        if local.name in DRIVE_ONLY_MODULES:
            _log(f"  ⛔ {rel_path} — DRIVE_ONLY, must not go to GH", "ERR")
            results["rejected"].append(rel_path)
            continue

        # AST scan for google.colab leaks
        violations = _ast_has_colab(local)
        if violations:
            _log(f"  ⛔ {rel_path} — EF-COLAB violations: {violations}", "ERR")
            results["rejected"].append(f"{rel_path} [{violations[0]}]")
            continue

        # Read content and encode
        content_bytes = local.read_bytes()
        content_b64   = base64.b64encode(content_bytes).decode("ascii")
        sha_local     = _sha12(content_bytes)

        # Get current GH SHA (needed for updates)
        existing = _gh_api(f"repos/{GH_REPO}/contents/{rel_path}")
        gh_sha   = existing.get("sha") if existing and isinstance(existing, dict) else None

        payload: dict = {
            "message": f"chore(s49): sync {local.name} sha={sha_local[:8]} [GOLD_PUSH]",
            "content": content_b64,
        }
        if gh_sha:
            payload["sha"] = gh_sha  # required for update

        resp = _gh_api(f"repos/{GH_REPO}/contents/{rel_path}", method="PUT", payload=payload)
        if resp and isinstance(resp, dict) and "content" in resp:
            _log(f"  ✅ {rel_path} → sha={sha_local[:8]}", "OK")
            results["pushed"].append(rel_path)
        else:
            _log(f"  ⛔ {rel_path} — push failed", "ERR")
            results["errors"].append(rel_path)

    # Also delete ghost files if they exist in GH
    _log("  Checking ghost files...", "INFO")
    for ghost in GHOST_FILES:
        existing = _gh_api(f"repos/{GH_REPO}/contents/{ghost}")
        if existing and isinstance(existing, dict) and "sha" in existing:
            _gh_api(
                f"repos/{GH_REPO}/contents/{ghost}",
                method="DELETE",
                payload={
                    "message": f"chore(s49): delete ghost file {ghost} [EF-26]",
                    "sha": existing["sha"],
                },
            )
            _log(f"  🗑️  Deleted ghost: {ghost}", "WARN")

    _log(f"STEP 2 done: {len(results['pushed'])} pushed · {len(results['rejected'])} rejected · {len(results['errors'])} errors", "OK")
    return results


# ─── STEP 3: Reset last_signal.json → break RC-02 Amnesia Loop ───────────────

def step3_reset_last_signal() -> dict:
    """
    Write a minimal but valid last_signal.json with:
      - Current UTC timestamp
      - decision = RESET_S49
      - vitality_tesla = 6 (non-zero — dashboard unlocks)
      - All required fields present (prevents downstream KeyError crashes)

    Commits the file to GH and writes locally to VAULT.
    """
    _log("STEP 3 — Reset last_signal.json (break RC-02 Amnesia Loop)", "INFO")

    signal = {
        "ts":               datetime.now(timezone.utc).isoformat(),
        "decision":         "RESET_S49",
        "asset":            "EURUSD",
        "session":          "S49",
        "vitality_tesla":   6,
        "entropy_shannon":  1.85,
        "transfer_entropy": 0.30,
        "kl_divergence":    0.04,
        "backbone_direction": 0.0,
        "backbone_pred":    0.0,
        "gold_score":       0.0,
        "regime":           "NORMAL",
        "close":            0.0,
        "_source":          "spel_colab_sync_s49.step3_reset",
        "_warning":         "RESET signal — placeholder until next GH Actions cycle overwrites",
        "_sha":             _sha12(datetime.now(timezone.utc).isoformat()),
    }

    signal_json = json.dumps(signal, indent=2)
    sha_local   = _sha12(signal_json)

    # 1. Write locally to VAULT
    local_path = VAULT / "last_signal.json"
    local_path.write_text(signal_json, encoding="utf-8")
    _log(f"  Local: {local_path} written (sha={sha_local[:8]})", "OK")

    # 2. Commit to GitHub via Contents API (so next GH Actions run finds it)
    rel_path = "meta/last_signal.json"
    existing = _gh_api(f"repos/{GH_REPO}/contents/{rel_path}")
    gh_sha   = existing.get("sha") if existing and isinstance(existing, dict) else None

    payload: dict = {
        "message": f"fix(ci): reset last_signal.json — break RC-02 amnesia [S49]",
        "content": base64.b64encode(signal_json.encode()).decode("ascii"),
    }
    if gh_sha:
        payload["sha"] = gh_sha

    resp = _gh_api(f"repos/{GH_REPO}/contents/{rel_path}", method="PUT", payload=payload)
    if resp and "content" in resp:
        _log(f"  GitHub: {rel_path} committed (sha={sha_local[:8]})", "OK")
        gh_ok = True
    else:
        _log(f"  GitHub: commit failed — local write succeeded but GH not updated", "WARN")
        gh_ok = False

    # 3. TG notification
    _tg(
        f"🔄 <b>SPEL S49 — last_signal RESET</b>\n"
        f"RC-02 Amnesia Loop broken.\n"
        f"ts: {signal['ts'][:16]}\n"
        f"GH commit: {'✅' if gh_ok else '⚠️ failed'}\n"
        f"Next cycle will overwrite with real GDELT signal.",
    )

    return {"local": str(local_path), "gh_committed": gh_ok, "sha": sha_local[:8]}


# ─── STEP 4: Trigger workflow_dispatch → start fresh cycle ───────────────────

def step4_trigger_workflow(workflow_file: str = "spel_universe.yml") -> bool:
    """Trigger a manual workflow run to start a fresh cycle with clean state."""
    _log(f"STEP 4 — Triggering {workflow_file} dispatch", "INFO")

    resp = _gh_api(
        f"repos/{GH_REPO}/actions/workflows/{workflow_file}/dispatches",
        method="POST",
        payload={"ref": "main"},
    )
    # dispatch returns 204 (no content) → resp = {}
    if resp is not None:
        _log(f"  ✅ workflow_dispatch sent → {workflow_file}", "OK")
        return True
    else:
        _log(f"  ⛔ workflow_dispatch failed — trigger manually in GH Actions tab", "ERR")
        return False


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("═" * 64)
    print("  SPEL_COLAB_SYNC_S49 · Holmes OS V4.0 · Hinc Omnia Cerno")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("═" * 64)

    # Pre-flight
    if not GH_TOKEN:
        _log("GITHUB_TOKEN missing — run CELL 0 first (load_secrets)", "ERR")
        sys.exit(1)
    if not ROOT.exists():
        _log(f"ROOT not found: {ROOT}", "ERR")
        sys.exit(1)
    if not VAULT.exists():
        _log(f"VAULT not found: {VAULT} — meta/ dir missing", "ERR")
        sys.exit(1)

    _log(f"ROOT:  {ROOT}", "OK")
    _log(f"VAULT: {VAULT}", "OK")
    _log(f"REPO:  {GH_REPO}", "OK")

    # Execute steps
    print()
    wf_cleaned = step1_clean_workflows()

    print()
    push_results = step2_push_gold_modules()

    print()
    signal_result = step3_reset_last_signal()

    print()
    triggered = step4_trigger_workflow()

    # Summary
    print()
    print("─" * 64)
    print("  SYNC SUMMARY")
    print("─" * 64)
    print(f"  Workflows deactivated:  {wf_cleaned}")
    print(f"  GOLD modules pushed:    {len(push_results['pushed'])}")
    print(f"  GOLD modules rejected:  {len(push_results['rejected'])} (EF-COLAB violations)")
    print(f"  GOLD modules errored:   {len(push_results['errors'])}")
    print(f"  last_signal reset:      sha={signal_result['sha']} GH={'✅' if signal_result['gh_committed'] else '⚠️'}")
    print(f"  Workflow dispatch:      {'✅' if triggered else '⚠️ manual trigger needed'}")
    print()
    if push_results["rejected"]:
        print("  REJECTED (EF-COLAB — fix before pushing):")
        for r in push_results["rejected"]:
            print(f"    × {r}")
    if push_results["errors"]:
        print("  ERRORS (network/API):")
        for e in push_results["errors"]:
            print(f"    ! {e}")
    print()
    print("  NEXT STEP: Watch TG_SISTEMA for 'cycle=N COMPLETE'")
    print("  Gate R30: Día 16/63 · Data Stale → SHOULD BE DEAD")
    print("  Hinc Omnia Cerno")
    print("═" * 64)


if __name__ == "__main__":
    main()
