"""
Holmes/modules/detective/error_detective.py
Detects: GH Actions failures, BUG-YAML-001, R37 torch violations in YAML jobs.
Source: subprocess gh CLI + static scan of scripts/ for top-level torch imports.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path

from .base_detective import BaseDetective

# Import Anomaly lazily to avoid circular import at collection time
def _Anomaly(*args, **kwargs):
    from kernel.holmes import Anomaly
    return Anomaly(*args, **kwargs)


log = logging.getLogger("Holmes.ErrorDetective")

# R37: top-level torch import pattern (never inside a function/class body)
_TORCH_TOP_LEVEL = re.compile(
    r"^(?![ \t])import torch(?:\s|$)|^(?![ \t])from torch",
    re.MULTILINE,
)

# YAML J-Audit condition that causes BUG-YAML-001
_BAD_YAML_COND = re.compile(
    r"startsWith\(github\.event\.schedule,\s*'0 '\)"
)


class ErrorDetective(BaseDetective):
    """
    Two scan tracks:
      A. GH Actions — last 5 runs via gh run list (requires GH_TOKEN or gh CLI).
      B. Static analysis — scans scripts/ for R37 torch top-level violations.
    """

    GH_RUNS_LIMIT = 5
    SCRIPTS_GLOB  = "**/*.py"
    YAML_GLOB     = ".github/workflows/*.yml"

    def scan(self) -> list:
        from kernel.holmes import Anomaly
        anomalies: list[Anomaly] = []
        anomalies.extend(self._scan_gh_actions(Anomaly))
        anomalies.extend(self._scan_torch_violations(Anomaly))
        anomalies.extend(self._scan_yaml_condition(Anomaly))
        return anomalies

    # ── Track A: GH Actions run status ───────────────────────────────────────
    def _scan_gh_actions(self, Anomaly) -> list:
        token = os.environ.get("GITHUB_TOKEN", "")
        repo  = os.environ.get("GH_REPO", "sandbox33/SPEL-v2.0")
        anomalies = []

        # Try gh CLI first (preferred in Colab/Termux), fallback to REST API
        runs = self._fetch_runs_gh_cli(repo) or self._fetch_runs_api(repo, token)

        if runs is None:
            anomalies.append(Anomaly(
                type="GH_ACTIONS_UNREACHABLE",
                component="github_actions",
                description="Cannot read GH Actions runs (no gh CLI and no GH_TOKEN).",
                severity="MEDIUM",
            ))
            return anomalies

        for run in runs[:self.GH_RUNS_LIMIT]:
            conclusion = run.get("conclusion", "")
            status     = run.get("status", "")
            name       = run.get("name", run.get("displayTitle", "unknown"))
            run_id     = str(run.get("databaseId", run.get("id", "?")))
            workflow   = run.get("workflowName", "?")

            if conclusion in ("failure", "cancelled", "timed_out"):
                # Detect if failure is likely BUG-YAML-001 (J-Audit skipped)
                is_yaml_bug = "J-Audit" in name or "audit" in name.lower()
                anomalies.append(Anomaly(
                    type="YAML_CONDITION_BUG" if is_yaml_bug else "GH_RUN_FAILURE",
                    component=f"{workflow}#{run_id}",
                    description=(
                        f"Run '{name}' concluded '{conclusion}'. "
                        + ("Likely BUG-YAML-001: j_audit.if condition. "
                           "Fix: set to 'github.event.schedule != \"\"'."
                           if is_yaml_bug else "")
                    ),
                    severity="CRITICAL" if is_yaml_bug else "HIGH",
                    metadata={"run_id": run_id, "workflow": workflow,
                              "conclusion": conclusion},
                ))

        return anomalies

    def _fetch_runs_gh_cli(self, repo: str) -> list | None:
        try:
            result = subprocess.run(
                ["gh", "run", "list", "--repo", repo,
                 "--limit", str(self.GH_RUNS_LIMIT),
                 "--json", "databaseId,name,conclusion,status,workflowName"],
                capture_output=True, text=True, timeout=20,
            )
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout)
        except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
            pass
        return None

    def _fetch_runs_api(self, repo: str, token: str) -> list | None:
        if not token:
            return None
        import urllib.request
        url = (f"https://api.github.com/repos/{repo}/actions/runs"
               f"?per_page={self.GH_RUNS_LIMIT}")
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept":        "application/vnd.github.v3+json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
            return data.get("workflow_runs", [])
        except Exception as e:
            log.warning("REST API fallback failed: %s", e)
            return None

    # ── Track B: R37 torch top-level violation scan ──────────────────────────
    def _scan_torch_violations(self, Anomaly) -> list:
        """
        R37 absolute: 'import torch' / 'from torch' never at module level.
        Scans all .py files under root. Reports file + line number.
        """
        scripts_dir = self.root / "scripts"
        if not scripts_dir.exists():
            # Fallback to repo root
            scripts_dir = self.root

        anomalies = []
        for py_file in sorted(scripts_dir.glob(self.SCRIPTS_GLOB)):
            if "_ARCHIVE" in str(py_file) or "QUARANTINE" in str(py_file):
                continue
            try:
                src   = py_file.read_text(encoding="utf-8", errors="replace")
                lines = src.splitlines()
                for i, line in enumerate(lines, start=1):
                    stripped = line.strip()
                    # Top-level = no leading whitespace + is torch import
                    if (not line[0:1].isspace()
                            and (stripped.startswith("import torch")
                                 or stripped.startswith("from torch"))):
                        anomalies.append(Anomaly(
                            type="TORCH_VIOLATION",
                            component=str(py_file.relative_to(self.root)),
                            description=(
                                f"R37 violation: top-level torch import at L{i}. "
                                f"Move inside function/method (Ley 2)."
                            ),
                            severity="CRITICAL",
                            metadata={"line": i, "code": stripped[:80]},
                        ))
                        break   # one report per file
            except Exception as e:
                log.debug("Cannot scan %s: %s", py_file, e)

        return anomalies

    # ── Track C: YAML condition audit ────────────────────────────────────────
    def _scan_yaml_condition(self, Anomaly) -> list:
        """
        Detects BUG-YAML-001 directly in the YAML source.
        If startsWith(github.event.schedule, '0 ') pattern found → CRITICAL.
        """
        anomalies = []
        yaml_dir  = self.root.parent / ".github" / "workflows"
        if not yaml_dir.exists():
            yaml_dir = Path(".github/workflows")

        for yml in sorted(yaml_dir.glob("*.yml")):
            try:
                content = yml.read_text(encoding="utf-8", errors="replace")
                if _BAD_YAML_COND.search(content):
                    anomalies.append(Anomaly(
                        type="YAML_CONDITION_BUG",
                        component=str(yml),
                        description=(
                            "BUG-YAML-001: startsWith(github.event.schedule, '0 ') "
                            "fails for multi-minute crons ('0,' prefix). "
                            "Fix: replace with 'github.event.schedule != \"\"'."
                        ),
                        severity="CRITICAL",
                        metadata={"file": yml.name},
                    ))
            except Exception as e:
                log.debug("Cannot read %s: %s", yml, e)

        return anomalies
