# ══════════════════════════════════════════════════════════════════════════════
# Holmes OS V4.0 — DNA SOVEREIGN MODULE
# Módulo: Holmes/modules/dna_sovereign/dna_sovereign.py
# Proyecto: SPEL 3.0 · Hinc Omnia Cerno
# Autor: Abraham Fuenmayor · S41 · 28-Mar-2026
#
# PROPÓSITO: Autómata de inteligencia del sistema de archivos.
#   — Lee, actualiza y protege el ADN_Sovereign_Map.json en Drive.
#   — Clasifica cada módulo (VITAL / META_TOOL / DEPRECATED / UNKNOWN).
#   — Detecta violaciones R37, conflictos de identidad, huérfanos peligrosos.
#   — Actualiza el mapa después de cada mutación del sistema (entrada/salida).
#   — Se comunica con Telegram sobre todo hallazgo. NUNCA falla en silencio.
#   — Es el único módulo autorizado para escribir ADN_Sovereign_Map.json.
#
# PROTOCOLO OMEGA — LEYES APLICADAS:
#   Ley 1: SHA-256 en toda escritura del mapa.
#   Ley 2: import torch NUNCA top-level (este módulo no usa torch).
#   Ley 3: CRITICAL → Telegram inmediato con descripción exacta y fix.
#   Ley 4: NUNCA eliminar archivos. Solo mover a DEPRECATED_EXTERN/.
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
import shutil
import urllib.request
import urllib.error

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger("holmes.dna_sovereign")

# ── RUTAS CANÓNICAS ────────────────────────────────────────────────────────────
_DNA_MAP_FILENAME    = "ADN_Sovereign_Map.json"
_DEPRECATED_DIRNAME  = "SPEL_DEPRECATED_EXTERN"   # fuera de sys.path de Python
_ARCHIVE_DIRNAME     = "ARCHIVE_S37"
_VAULT_SUBDIR        = "Holmes/vault"

# ── ZONAS DE PROTECCIÓN (Protocolo OMEGA — XML v5.3) ─────────────────────────
_VITAL_PATHS: frozenset[str] = frozenset({
    "codigo/core/capa_c_inference.py",
    "codigo/core/spel_backbone_engine.py",
    "codigo/core/spel_math_engine.py",
    "codigo/core/spel_trading_router.py",
    "codigo/core/spel_orchestrator_v9.py",
    "codigo/core/spel_adapter_bridge.py",
    "codigo/core/spel_meta_guardian.py",
    "codigo/core/spel_cost_model.py",
    "codigo/core/spel_ingestion.py",
    "codigo/core/gdelt_foundation.py",          # EF-23: inamovible
    "codigo/core/critical_loss_optimized.py",   # EF-23: inamovible
    "scripts/spel_score_engine.py",
    "scripts/spel_paper_adapter_v2.py",
    "scripts/spel_forex_iq_runner.py",
    "scripts/spel_snapshot_updater.py",
    "scripts/spel_ingest_incremental.py",
    "meta/SHA_REGISTRY.json",
})

_META_TOOL_PATHS: frozenset[str] = frozenset({
    "SPEL_autofixer.py",
    "meta/spel_dna_audit.py",
    "meta/spel_drive_auditor.py",
    "meta/spel_retrain_v5_clean.py",
    "scripts/SPEL_DNA_Scanner.py",
    "scripts/SPEL_DNA_MAPPER.py",
})

_EF23_IMMUTABLE: frozenset[str] = frozenset({
    "codigo/core/gdelt_foundation.py",
    "codigo/core/critical_loss_optimized.py",
})

_R37_PATTERN = re.compile(r'^(?![ \t])(import torch|from torch)', re.MULTILINE)

# ── CLASIFICACIÓN DE MÓDULOS ──────────────────────────────────────────────────
class ModuleClass(str, Enum):
    VITAL        = "VITAL"         # Core del sistema — prohibido borrar/mover
    META_TOOL    = "META_TOOL"     # Herramientas de mantenimiento — protegido
    DEPRECATED   = "DEPRECATED"   # Candidato a DEPRECATED_EXTERN
    GHOST        = "GHOST"         # Solo en Drive, sin dependencias, sin uso
    SHADOW_VITAL = "SHADOW_VITAL"  # Solo en Drive pero tiene dependencias entrantes
    UNKNOWN      = "UNKNOWN"       # Sin clasificar


class ThreatLevel(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH     = "HIGH"
    MEDIUM   = "MEDIUM"
    LOW      = "LOW"
    CLEAN    = "CLEAN"


@dataclass
class ModuleRecord:
    """Registro enriquecido de un módulo en el mapa soberano."""
    path:          str
    name:          str
    module_class:  str = ModuleClass.UNKNOWN
    in_github:     bool = False
    in_drive:      bool = False
    calls_to:      list[str] = field(default_factory=list)
    called_by:     list[str] = field(default_factory=list)
    r37_violation: bool = False
    sha256:        Optional[str] = None
    size_bytes:    int = 0
    threat_level:  str = ThreatLevel.CLEAN
    threat_detail: str = ""
    last_audited:  str = ""
    action_required: str = "NONE"    # NONE | MOVE_TO_DEPRECATED | COMMIT_TO_GITHUB | LAZY_IMPORT_FIX

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SovereignAuditReport:
    """Reporte de auditoría completo. Serializable a JSON (512b+ permitido en vault)."""
    timestamp_utc:   str
    total_modules:   int
    vital_count:     int
    meta_tool_count: int
    deprecated_count:int
    ghost_count:     int
    shadow_vital_count: int
    r37_violations:  list[str]
    identity_conflicts: list[dict]
    critical_threats:   list[str]
    actions_pending:    list[dict]
    map_sha256:      str
    system_state:    str   # SOVEREIGN | DEGRADED | CRITICAL

    def to_dict(self) -> dict:
        return asdict(self)


# ══════════════════════════════════════════════════════════════════════════════
# DNA SOVEREIGN ENGINE
# ══════════════════════════════════════════════════════════════════════════════
class DNASovereignEngine:
    """
    Autómata de inteligencia del sistema de archivos de SPEL.

    Ciclo autónomo:
      1. scan()      — recorre Drive, clasifica módulos, detecta amenazas.
      2. heal()      — aplica correcciones automáticas (mover DEPRECATED, etc.).
      3. update_map() — escribe ADN_Sovereign_Map.json con SHA-256 (Ley 1).
      4. report()    — envía resumen a Telegram y retorna SovereignAuditReport.

    Holmes llama full_cycle() en cada patrol() para mantener el mapa vivo.
    """

    def __init__(
        self,
        spel_root:    Path,
        vault_dir:    Path,
        tg_token:     str = "",
        tg_sistema:   str = "",
        tg_caos:      str = "",
        dry_run:      bool = False,
    ):
        self.root       = Path(spel_root)
        self.vault      = Path(vault_dir)
        self.tg_token   = tg_token
        self.tg_sistema = tg_sistema
        self.tg_caos    = tg_caos
        self.dry_run    = dry_run   # True → solo reporta, no mueve archivos

        self.map_path   = self.root / _DNA_MAP_FILENAME
        self.deprecated = self.root.parent / _DEPRECATED_DIRNAME  # fuera de SPEL-v2.0

        self._modules:  dict[str, ModuleRecord] = {}
        self._last_map_sha: str = ""

    # ── API PÚBLICA ────────────────────────────────────────────────────────────

    def full_cycle(self) -> SovereignAuditReport:
        """
        Ciclo completo: scan → classify → detect_threats → heal → update_map → report.
        Punto de entrada de Holmes patrol().
        """
        logger.info("[DNA] ── CYCLE START ──────────────────────────────────────")
        self._scan_drive()
        self._load_github_state()
        self._classify_all()
        self._detect_r37_violations()
        self._detect_identity_conflicts()
        report = self._build_report()

        if not self.dry_run:
            self._heal(report)
            self._update_map()

        self._notify_telegram(report)
        logger.info("[DNA] ── CYCLE END  — state=%s ──────────────────────────",
                    report.system_state)
        return report

    def scan_only(self) -> dict[str, ModuleRecord]:
        """Escaneo sin escritura. Útil para preflight checks."""
        self._scan_drive()
        self._classify_all()
        self._detect_r37_violations()
        return self._modules

    def get_module_status(self, relative_path: str) -> Optional[ModuleRecord]:
        """Consulta el estado de un módulo específico por su ruta relativa."""
        return self._modules.get(relative_path)

    def register_new_file(self, relative_path: str, source: str = "DRIVE") -> None:
        """
        Registra un archivo nuevo que entró al sistema.
        Holmes llama esto cuando detecta un archivo no conocido en patrol().
        """
        full_path = self.root / relative_path
        if not full_path.exists():
            logger.warning("[DNA] register_new_file: %s no existe en disco.", relative_path)
            return

        record = self._build_record(relative_path, full_path)
        record.last_audited = datetime.now(timezone.utc).isoformat()
        self._modules[relative_path] = record
        self._update_map()

        logger.info("[DNA] Nuevo archivo registrado: %s → clase=%s",
                    relative_path, record.module_class)
        self._tg_send(
            self.tg_sistema,
            f"📥 <b>DNA: Nuevo módulo registrado</b>\n"
            f"Ruta: {relative_path}\n"
            f"Clase: {record.module_class}\n"
            f"R37: {'❌ VIOLATION' if record.r37_violation else '✅ CLEAN'}\n"
            f"Acción: {record.action_required}"
        )

    # ── ESCANEO ────────────────────────────────────────────────────────────────

    def _scan_drive(self) -> None:
        """Recorre todo el árbol de SPEL root y construye/actualiza _modules."""
        logger.info("[DNA] Escaneando Drive: %s", self.root)
        found: set[str] = set()

        for py_file in self.root.rglob("*.py"):
            rel = str(py_file.relative_to(self.root))
            found.add(rel)
            if rel not in self._modules:
                self._modules[rel] = self._build_record(rel, py_file)
            else:
                # Actualizar SHA y tamaño
                rec = self._modules[rel]
                rec.sha256     = _sha256_file(py_file)
                rec.size_bytes = py_file.stat().st_size
                rec.in_drive   = True

        # También registrar JSONs críticos
        for json_file in self.root.glob("meta/*.json"):
            rel = str(json_file.relative_to(self.root))
            if rel not in self._modules:
                rec = ModuleRecord(
                    path=rel, name=json_file.name,
                    in_drive=True, sha256=_sha256_file(json_file),
                    size_bytes=json_file.stat().st_size,
                )
                self._modules[rel] = rec

        logger.info("[DNA] Escaneados %d archivos en Drive.", len(found))

    def _load_github_state(self) -> None:
        """
        Carga el mapa existente (si existe) para preservar la info de in_github.
        No hace llamadas API — usa el mapa local en vault como fuente de verdad local.
        """
        map_path = self.vault / _DNA_MAP_FILENAME
        if not map_path.exists():
            map_path = self.map_path  # fallback al root

        if not map_path.exists():
            logger.warning("[DNA] ADN_Sovereign_Map.json no encontrado. Primera ejecución.")
            return

        try:
            raw  = json.loads(map_path.read_text(encoding="utf-8"))
            mods = raw.get("modules", {})
            for rel_path, data in mods.items():
                if rel_path in self._modules:
                    self._modules[rel_path].in_github = data.get("in_github", False)
                    self._modules[rel_path].calls_to  = data.get("calls_to", [])
                    self._modules[rel_path].called_by = data.get("called_by", [])
                else:
                    # Módulo en mapa pero no en Drive actual → ghost en GH
                    rec = ModuleRecord(
                        path=rel_path,
                        name=data.get("name", Path(rel_path).name),
                        in_github=data.get("in_github", False),
                        in_drive=False,
                        calls_to=data.get("calls_to", []),
                        called_by=data.get("called_by", []),
                        module_class=ModuleClass.GHOST,
                    )
                    self._modules[rel_path] = rec

            self._last_map_sha = _sha256_bytes(map_path.read_bytes())
            logger.info("[DNA] Mapa cargado: %d módulos.", len(mods))
        except Exception as e:
            logger.error("[DNA] Error cargando mapa: %s", e)

    # ── CLASIFICACIÓN ──────────────────────────────────────────────────────────

    def _classify_all(self) -> None:
        """Clasifica cada módulo según las zonas OMEGA y el grafo de dependencias."""
        for rel_path, rec in self._modules.items():
            rec.module_class   = self._classify_module(rel_path, rec)
            rec.action_required = self._determine_action(rel_path, rec)
            rec.last_audited   = datetime.now(timezone.utc).isoformat()

    def _classify_module(self, rel_path: str, rec: ModuleRecord) -> str:
        # EF-23: inamovibles absolutos
        if rel_path in _EF23_IMMUTABLE:
            return ModuleClass.VITAL

        # Zona VITAL explícita
        if rel_path in _VITAL_PATHS:
            return ModuleClass.VITAL

        # Tiene dependencias entrantes → SHADOW_VITAL aunque no esté en GitHub
        if rec.called_by:
            if not rec.in_github:
                return ModuleClass.SHADOW_VITAL
            return ModuleClass.VITAL

        # META_TOOL: scripts de mantenimiento
        if rel_path in _META_TOOL_PATHS or "meta/" in rel_path:
            return ModuleClass.META_TOOL

        # DEPRECATED: dentro de ARCHIVE_S37
        if _ARCHIVE_DIRNAME in rel_path:
            return ModuleClass.DEPRECATED

        # GHOST: sin llamadas, sin dependencias, no en GitHub
        if not rec.in_github and not rec.calls_to and not rec.called_by:
            return ModuleClass.GHOST

        return ModuleClass.UNKNOWN

    def _determine_action(self, rel_path: str, rec: ModuleRecord) -> str:
        if rec.module_class == ModuleClass.VITAL:
            return "NONE"
        if rec.module_class == ModuleClass.SHADOW_VITAL:
            return "COMMIT_TO_GITHUB"
        if rec.module_class == ModuleClass.DEPRECATED:
            return "MOVE_TO_DEPRECATED"
        if rec.module_class == ModuleClass.META_TOOL:
            return "NONE"
        if rec.r37_violation:
            return "LAZY_IMPORT_FIX"
        if rec.module_class == ModuleClass.GHOST:
            return "MOVE_TO_DEPRECATED"
        return "NONE"

    # ── DETECCIÓN DE AMENAZAS ─────────────────────────────────────────────────

    def _detect_r37_violations(self) -> None:
        """Escanea archivos .py en Drive para violaciones top-level import torch."""
        for rel_path, rec in self._modules.items():
            if not rel_path.endswith(".py"):
                continue
            full = self.root / rel_path
            if not full.exists():
                continue
            try:
                src = full.read_text(encoding="utf-8", errors="replace")
                hits = _R37_PATTERN.findall(src)
                rec.r37_violation = bool(hits)
                if hits:
                    rec.threat_level  = ThreatLevel.HIGH
                    rec.threat_detail = (
                        f"R37/Ley2: {len(hits)} import torch a nivel de módulo. "
                        f"Fix: lazy import dentro de funciones."
                    )
                    # P0 si es módulo vital
                    if rec.module_class == ModuleClass.VITAL:
                        rec.threat_level = ThreatLevel.CRITICAL
            except Exception as e:
                logger.warning("[DNA] R37 scan error en %s: %s", rel_path, e)

    def _detect_identity_conflicts(self) -> None:
        """
        Detecta módulos con el mismo nombre en rutas distintas.
        Caso conocido: scripts/spel_score_engine.py vs ARCHIVE_S37/spel_score_engine.py.
        """
        by_name: dict[str, list[str]] = {}
        for rel_path, rec in self._modules.items():
            name = Path(rel_path).stem
            by_name.setdefault(name, []).append(rel_path)

        for name, paths in by_name.items():
            if len(paths) > 1:
                for p in paths:
                    rec = self._modules[p]
                    if rec.module_class != ModuleClass.DEPRECATED:
                        rec.threat_level  = ThreatLevel.HIGH
                        rec.threat_detail = (
                            f"IDENTITY_CONFLICT: '{name}' existe en {len(paths)} rutas: "
                            f"{', '.join(paths)}. Riesgo de import ambiguo."
                        )
                logger.warning("[DNA] Conflicto de identidad: %s → %s", name, paths)

    # ── AUTO-SANACIÓN ─────────────────────────────────────────────────────────

    def _heal(self, report: SovereignAuditReport) -> None:
        """
        Aplica correcciones automáticas no destructivas:
          — Mueve GHOST y DEPRECATED a DEPRECATED_EXTERN (Ley 4).
          — NUNCA elimina. NUNCA toca VITAL ni EF-23.
          — Registra cada acción en vault/repair_history.json.
        """
        for action_item in report.actions_pending:
            rel_path = action_item.get("path", "")
            action   = action_item.get("action", "NONE")
            rec      = self._modules.get(rel_path)

            if not rec or action == "NONE":
                continue

            if action == "MOVE_TO_DEPRECATED":
                self._move_to_deprecated(rel_path, rec)
            elif action == "LAZY_IMPORT_FIX":
                # Auto-fix NO se aplica aquí — requiere intervención humana.
                # Holmes informa pero no toca código vital automáticamente.
                logger.info("[DNA] LAZY_IMPORT_FIX pendiente revisión humana: %s", rel_path)
                self._log_repair(rel_path, "LAZY_IMPORT_FIX", "PENDING_HUMAN",
                                 rec.threat_detail)

    def _move_to_deprecated(self, rel_path: str, rec: ModuleRecord) -> None:
        """
        Mueve archivo a SPEL_DEPRECATED_EXTERN/ (fuera de SPEL-v2.0/).
        Ley 4: nunca eliminar. El audit trail es inmortal.
        """
        src  = self.root / rel_path
        if not src.exists():
            return

        # VITAL y EF-23 son intocables
        if rel_path in _VITAL_PATHS or rel_path in _EF23_IMMUTABLE:
            logger.error("[DNA] INTENTO de mover módulo VITAL bloqueado: %s", rel_path)
            self._tg_send(
                self.tg_sistema,
                f"🚨 <b>DNA: BLOQUEO CRÍTICO</b>\n"
                f"Intento de mover módulo VITAL rechazado:\n{rel_path}\n"
                f"Sistema intacto. Revisar lógica de clasificación."
            )
            return

        dest_dir = self.deprecated / Path(rel_path).parent
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / Path(rel_path).name

        try:
            shutil.move(str(src), str(dest))
            rec.in_drive = False
            rec.module_class = ModuleClass.DEPRECATED
            self._log_repair(rel_path, "MOVED_TO_DEPRECATED", "SUCCESS",
                             f"Destino: {dest}")
            logger.info("[DNA] Movido a DEPRECATED_EXTERN: %s", rel_path)
        except Exception as e:
            logger.error("[DNA] Error moviendo %s: %s", rel_path, e)
            self._log_repair(rel_path, "MOVED_TO_DEPRECATED", "FAILED", str(e))

    def _log_repair(self, path: str, action: str, status: str, detail: str) -> None:
        """Persiste cada reparación en vault/repair_history.json (Ley 1 — inmutable)."""
        history_path = self.vault / "repair_history.json"
        try:
            history = json.loads(history_path.read_text()) if history_path.exists() else []
        except Exception:
            history = []

        history.append({
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "path":   path,
            "action": action,
            "status": status,
            "detail": detail,
        })

        try:
            history_path.write_text(json.dumps(history, indent=2))
        except Exception as e:
            logger.error("[DNA] Error escribiendo repair_history: %s", e)

    # ── ACTUALIZACIÓN DEL MAPA ────────────────────────────────────────────────

    def _update_map(self) -> None:
        """
        Escribe ADN_Sovereign_Map.json en Drive root y en vault/.
        Ley 1: incluye SHA-256 del contenido para verificación de integridad.
        """
        payload = {
            "metadata": {
                "timestamp_utc":    datetime.now(timezone.utc).isoformat(),
                "generator":        "Holmes OS V4.0 — DNA Sovereign Engine",
                "log_version":      "v45+",
                "total_modules":    len(self._modules),
                "total_in_drive":   sum(1 for r in self._modules.values() if r.in_drive),
                "total_in_github":  sum(1 for r in self._modules.values() if r.in_github),
                "vital_count":      sum(1 for r in self._modules.values()
                                        if r.module_class == ModuleClass.VITAL),
                "r37_violations":   sum(1 for r in self._modules.values() if r.r37_violation),
                "dry_run":          self.dry_run,
            },
            "modules": {
                rel: rec.to_dict()
                for rel, rec in sorted(self._modules.items())
            },
            "protected_zones": {
                "VITAL":    sorted(_VITAL_PATHS),
                "META_TOOL":sorted(_META_TOOL_PATHS),
                "EF23_IMMUTABLE": sorted(_EF23_IMMUTABLE),
            },
        }

        raw_bytes = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        sha256    = hashlib.sha256(raw_bytes).hexdigest()
        payload["metadata"]["map_sha256"] = sha256

        final_bytes = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")

        # Escribir en root y en vault (redundancia soberana)
        for dest in [self.map_path, self.vault / _DNA_MAP_FILENAME]:
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(final_bytes)
            except Exception as e:
                logger.error("[DNA] Error escribiendo mapa en %s: %s", dest, e)

        self._last_map_sha = sha256
        logger.info("[DNA] Mapa actualizado — SHA256: %s", sha256[:16])

    # ── REPORTE Y TELEGRAM ────────────────────────────────────────────────────

    def _build_report(self) -> SovereignAuditReport:
        r37_violations   = [r for r, rec in self._modules.items() if rec.r37_violation]
        critical_threats = [
            r for r, rec in self._modules.items()
            if rec.threat_level == ThreatLevel.CRITICAL
        ]
        actions_pending  = [
            {"path": r, "action": rec.action_required,
             "module_class": rec.module_class, "threat": rec.threat_detail}
            for r, rec in self._modules.items()
            if rec.action_required not in ("NONE",)
        ]

        # Conflictos de identidad
        by_name: dict[str, list[str]] = {}
        for rel in self._modules:
            by_name.setdefault(Path(rel).stem, []).append(rel)
        identity_conflicts = [
            {"name": n, "paths": ps}
            for n, ps in by_name.items() if len(ps) > 1
        ]

        # Estado del sistema
        if critical_threats:
            state = "CRITICAL"
        elif r37_violations or identity_conflicts:
            state = "DEGRADED"
        else:
            state = "SOVEREIGN"

        return SovereignAuditReport(
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            total_modules=len(self._modules),
            vital_count=sum(1 for r in self._modules.values()
                            if r.module_class == ModuleClass.VITAL),
            meta_tool_count=sum(1 for r in self._modules.values()
                                if r.module_class == ModuleClass.META_TOOL),
            deprecated_count=sum(1 for r in self._modules.values()
                                 if r.module_class == ModuleClass.DEPRECATED),
            ghost_count=sum(1 for r in self._modules.values()
                            if r.module_class == ModuleClass.GHOST),
            shadow_vital_count=sum(1 for r in self._modules.values()
                                   if r.module_class == ModuleClass.SHADOW_VITAL),
            r37_violations=r37_violations,
            identity_conflicts=identity_conflicts,
            critical_threats=critical_threats,
            actions_pending=actions_pending,
            map_sha256=self._last_map_sha,
            system_state=state,
        )

    def _notify_telegram(self, report: SovereignAuditReport) -> None:
        """
        Ley 3: CRITICAL → Telegram inmediato. DEGRADED → resumen. SOVEREIGN → log only.
        NUNCA falla en silencio.
        """
        state_icon = {"SOVEREIGN": "✅", "DEGRADED": "⚠️", "CRITICAL": "🚨"}.get(
            report.system_state, "📡"
        )

        lines = [
            f"{state_icon} <b>Holmes DNA — {report.system_state}</b>",
            f"Módulos: {report.total_modules} "
            f"(VITAL:{report.vital_count} | META:{report.meta_tool_count} "
            f"| GHOST:{report.ghost_count} | SHADOW:{report.shadow_vital_count})",
        ]

        if report.r37_violations:
            lines.append(f"⛔ R37 Violations: {len(report.r37_violations)}")
            for v in report.r37_violations[:3]:
                lines.append(f"  • {v}")
            if len(report.r37_violations) > 3:
                lines.append(f"  …y {len(report.r37_violations) - 3} más")

        if report.identity_conflicts:
            lines.append(f"🔀 Conflictos de identidad: {len(report.identity_conflicts)}")
            for c in report.identity_conflicts[:2]:
                lines.append(f"  • {c['name']}: {', '.join(c['paths'][:2])}")

        if report.actions_pending:
            lines.append(f"🔧 Acciones pendientes: {len(report.actions_pending)}")

        lines.append(f"SHA mapa: {report.map_sha256[:16] if report.map_sha256 else 'N/A'}")
        lines.append("<i>Hinc Omnia Cerno</i>")

        msg = "\n".join(lines)

        # CRITICAL y DEGRADED → TG_SISTEMA
        if report.system_state in ("CRITICAL", "DEGRADED"):
            self._tg_send(self.tg_sistema, msg)

        # CRITICAL → también TG_CAOS (entrenamiento/escalación)
        if report.system_state == "CRITICAL":
            caos_msg = (
                f"🚨 <b>CAOS ALERT — DNA CRITICAL</b>\n"
                f"Amenazas: {', '.join(report.critical_threats[:5])}\n"
                f"Requiere análisis y reentrenamiento si es drift de modelo.\n"
                f"<i>Holmes OS V4.0</i>"
            )
            self._tg_send(self.tg_caos, caos_msg)

        logger.info("[DNA] Reporte: %s | R37:%d | Conflicts:%d | Actions:%d",
                    report.system_state,
                    len(report.r37_violations),
                    len(report.identity_conflicts),
                    len(report.actions_pending))

    def _tg_send(self, chat_id: str, text: str) -> bool:
        """Fire-and-forget. Nunca lanza excepción al exterior (Ley 3)."""
        if not self.tg_token or not chat_id:
            logger.warning("[DNA] TG no configurado — mensaje no enviado.")
            return False
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
                data=json.dumps({
                    "chat_id":    chat_id,
                    "text":       text[:4096],
                    "parse_mode": "HTML",
                }).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=8)
            return True
        except Exception as e:
            logger.error("[DNA] TG send failed (chat=%s): %s", chat_id, e)
            return False

    # ── CONSTRUCCIÓN DE REGISTRO ──────────────────────────────────────────────

    def _build_record(self, rel_path: str, full_path: Path) -> ModuleRecord:
        rec = ModuleRecord(
            path=rel_path,
            name=full_path.name,
            in_drive=full_path.exists(),
        )
        if full_path.exists():
            try:
                rec.sha256     = _sha256_file(full_path)
                rec.size_bytes = full_path.stat().st_size
            except Exception:
                pass
        return rec


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except Exception:
        pass
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── INTEGRACIÓN CON HOLMES KERNEL ─────────────────────────────────────────────

def build_dna_engine_from_env(spel_root: Path, vault_dir: Path) -> DNASovereignEngine:
    """
    Factory que construye el engine con secrets desde variables de entorno.
    Holmes kernel llama esto en __init__ para wiring automático.
    """
    import os
    return DNASovereignEngine(
        spel_root=spel_root,
        vault_dir=vault_dir,
        tg_token=os.environ.get("TELEGRAM_TOKEN", ""),
        tg_sistema=os.environ.get("TELEGRAM_SISTEMA", ""),
        tg_caos=os.environ.get("TELEGRAM_CAOS", ""),
        dry_run=os.environ.get("DNA_DRY_RUN", "false").lower() == "true",
    )
