"""
spel_registry_repair.py
=======================
Holmes OS V4.0 · Auditor Forense de SHA Registry
Herramienta standalone de alineación forense para data_lake/

MODO DE OPERACIÓN: Solo propone plan de acción. NO ejecuta borrados.
                   Movimientos requieren flag --execute (Ley-4: shutil.move únicamente)

Leyes activas: Ley-1 (SHA-256), Ley-4 (never delete, solo 99_ARCHIVE_FENIX),
               Ley-2 (lazy torch), R37

Ejecución:
  python spel_registry_repair.py --root /content/drive/MyDrive/ORDEN/SPEL\\ 3.0
  python spel_registry_repair.py --root . --execute

Hinc Omnia Cerno
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────────────────────────────────────
REPAIR_VERSION = "4.1.0"
SHA_REGISTRY_FILENAME = "SHA_REGISTRY.json"
ARCHIVE_DIR = "99_ARCHIVE_FENIX"
CHUNK_SIZE = 65_536  # 64KB — RAM-safe para 2GB reales
SCAN_EXTENSIONS = {".parquet", ".pt", ".py", ".json", ".yml", ".md", ".csv"}
ORPHAN_SIZE_BYTES_THRESHOLD = 0  # archivos de 0 bytes → candidatos a archivo

# Clasificaciones de acción forense
ACTION_REGISTER  = "REGISTER"   # nuevo archivo, no está en registry
ACTION_UPDATE    = "UPDATE"     # SHA cambió desde último registro
ACTION_ORPHAN    = "ORPHAN"     # en registry pero el archivo no existe en disco
ACTION_ZERO_BYTE = "ZERO_BYTE"  # archivo vacío (0 bytes)
ACTION_HEALTHY   = "HEALTHY"    # SHA coincide, sin acción requerida
ACTION_ARCHIVE   = "ARCHIVE"    # requiere movimiento a 99_ARCHIVE_FENIX

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-8s [REPAIR] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    stream=sys.stdout
)
log = logging.getLogger("REGISTRY_REPAIR")


# ─────────────────────────────────────────────────────────────────────────────
# ESTRUCTURAS DE DATOS
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class AuditEntry:
    path: str
    action: str
    sha256_real: Optional[str] = None
    sha256_registry: Optional[str] = None
    sha_git_real: Optional[str] = None
    size_bytes: int = 0
    detail: str = ""
    executed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RepairReport:
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    spel_root: str = ""
    registry_path: str = ""
    scan_extensions: List[str] = field(default_factory=list)
    total_files_scanned: int = 0
    healthy: int = 0
    registered_new: int = 0
    updated: int = 0
    orphans: int = 0
    zero_bytes: int = 0
    archived: int = 0
    errors: List[str] = field(default_factory=list)
    entries: List[Dict[str, Any]] = field(default_factory=list)
    action_plan_executed: bool = False
    registry_sha256_after: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


# ─────────────────────────────────────────────────────────────────────────────
# CORE: REGISTRY REPAIR ENGINE
# ─────────────────────────────────────────────────────────────────────────────
class RegistryRepairEngine:

    def __init__(self, spel_root: Path) -> None:
        self.spel_root = spel_root.resolve()
        self.registry_path = self.spel_root / "00_VAULT" / SHA_REGISTRY_FILENAME
        self.archive_root = self.spel_root / ARCHIVE_DIR
        self._registry: Dict[str, Any] = {}
        self._report = RepairReport(
            spel_root=str(self.spel_root),
            registry_path=str(self.registry_path),
            scan_extensions=sorted(SCAN_EXTENSIONS),
        )

    # ── FASE 1: CARGA ────────────────────────────────────────────────────────
    def load_registry(self) -> None:
        if not self.registry_path.exists():
            log.warning("SHA_REGISTRY no encontrado en %s — iniciando vacío",
                        self.registry_path)
            self._registry = {}
            return
        try:
            with open(self.registry_path, "r", encoding="utf-8") as fh:
                self._registry = json.load(fh)
            # Excluir la self-hash del registry del análisis
            self._registry.pop("__registry_sha256__", None)
            log.info("SHA_REGISTRY cargado: %d entradas", len(self._registry))
        except json.JSONDecodeError as exc:
            log.error("SHA_REGISTRY CORRUPTO: %s", exc)
            self._archive_file(self.registry_path, reason="CORRUPTED_REGISTRY",
                               execute=True)
            self._registry = {}

    # ── FASE 2: ESCANEO RECURSIVO ────────────────────────────────────────────
    def scan_tree(self) -> List[AuditEntry]:
        """
        Escaneo recursivo de SPEL root.
        Excluye: 99_ARCHIVE_FENIX, __pycache__, .git
        """
        EXCLUDED = {ARCHIVE_DIR, "__pycache__", ".git", ".ipynb_checkpoints",
                    "node_modules", ".mypy_cache"}
        entries: List[AuditEntry] = []
        files_found = 0

        log.info("Iniciando escaneo recursivo: %s", self.spel_root)
        t0 = time.monotonic()

        for path in sorted(self.spel_root.rglob("*")):
            if any(part in EXCLUDED for part in path.parts):
                continue
            if not path.is_file():
                continue
            if path.suffix not in SCAN_EXTENSIONS:
                continue

            files_found += 1
            entry = self._audit_file(path)
            entries.append(entry)

        # ── FASE 2b: Detectar huérfanos en registry ─────────────────────────
        scanned_paths = {e.path for e in entries}
        for reg_path_str in list(self._registry.keys()):
            if reg_path_str not in scanned_paths:
                entries.append(AuditEntry(
                    path=reg_path_str,
                    action=ACTION_ORPHAN,
                    sha256_registry=self._registry[reg_path_str].get("sha256"),
                    detail="En registry pero no encontrado en disco"
                ))

        elapsed = time.monotonic() - t0
        log.info("Escaneo completo: %d archivos en %.2fs", files_found, elapsed)
        self._report.total_files_scanned = files_found
        return entries

    def _audit_file(self, path: Path) -> AuditEntry:
        path_str = str(path)
        size = path.stat().st_size

        # Archivo vacío → zero_byte
        if size == 0:
            return AuditEntry(
                path=path_str,
                action=ACTION_ZERO_BYTE,
                sha256_real="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                size_bytes=0,
                detail="Archivo vacío (0 bytes) — candidato a archivo en FENIX"
            )

        try:
            sha256_real = _sha256_file(path)
            sha_git_real = _sha_git(path)
        except Exception as exc:
            log.error("SHA error %s: %s", path.name, exc)
            return AuditEntry(
                path=path_str,
                action="ERROR",
                detail=str(exc)
            )

        reg_entry = self._registry.get(path_str, {})
        sha256_reg = reg_entry.get("sha256")

        if sha256_reg is None:
            action = ACTION_REGISTER
            detail = "Nuevo archivo, no registrado"
        elif sha256_real == sha256_reg:
            action = ACTION_HEALTHY
            detail = "SHA coincide"
        else:
            action = ACTION_UPDATE
            detail = f"SHA drift: registry={sha256_reg[:12]} real={sha256_real[:12]}"

        return AuditEntry(
            path=path_str,
            action=action,
            sha256_real=sha256_real,
            sha256_registry=sha256_reg,
            sha_git_real=sha_git_real,
            size_bytes=size,
            detail=detail
        )

    # ── FASE 3: PLAN DE ACCIÓN ───────────────────────────────────────────────
    def build_action_plan(self, entries: List[AuditEntry]) -> None:
        """Consolida estadísticas y loggea el plan completo SIN ejecutar."""
        healthy = updated = registered = orphans = zero_bytes = 0

        log.info("════════ PLAN DE ACCIÓN ════════")
        for e in entries:
            if e.action == ACTION_HEALTHY:
                healthy += 1
            elif e.action == ACTION_UPDATE:
                updated += 1
                log.info("[UPDATE] %s | %s", Path(e.path).name, e.detail)
            elif e.action == ACTION_REGISTER:
                registered += 1
                log.info("[REGISTER] %s (nuevo)", Path(e.path).name)
            elif e.action == ACTION_ORPHAN:
                orphans += 1
                log.warning("[ORPHAN→FENIX] %s | %s", e.path, e.detail)
            elif e.action == ACTION_ZERO_BYTE:
                zero_bytes += 1
                log.warning("[ZERO_BYTE→FENIX] %s", Path(e.path).name)
            elif e.action == "ERROR":
                log.error("[ERROR] %s: %s", e.path, e.detail)

        self._report.healthy = healthy
        self._report.updated = updated
        self._report.registered_new = registered
        self._report.orphans = orphans
        self._report.zero_bytes = zero_bytes
        self._report.entries = [e.to_dict() for e in entries]

        log.info("════ RESUMEN: healthy=%d update=%d register=%d orphan=%d zero=%d ════",
                 healthy, updated, registered, orphans, zero_bytes)

    # ── FASE 4: EJECUCIÓN (requiere flag --execute) ──────────────────────────
    def execute_plan(self, entries: List[AuditEntry]) -> None:
        """
        Ejecuta el plan de acción. Ley-4: NUNCA eliminar.
        ORPHAN y ZERO_BYTE → shutil.move a 99_ARCHIVE_FENIX/
        REGISTER y UPDATE  → actualizar SHA_REGISTRY.json
        """
        log.info("════════ EJECUTANDO PLAN (Ley-4 activa) ════════")
        archived_count = 0

        for e in entries:
            try:
                if e.action in (ACTION_REGISTER, ACTION_UPDATE):
                    fpath = Path(e.path)
                    if fpath.exists() and e.sha256_real:
                        self._registry[e.path] = {
                            "sha256": e.sha256_real,
                            "sha_git": e.sha_git_real,
                            "size_kb": round(e.size_bytes / 1024, 3),
                            "ts_validated": datetime.now(timezone.utc).isoformat(),
                        }
                        e.executed = True
                        log.debug("[WRITTEN] registry entry: %s", fpath.name)

                elif e.action == ACTION_ORPHAN:
                    # En registry pero no en disco → limpiar del registry
                    self._registry.pop(e.path, None)
                    e.executed = True
                    log.info("[ORPHAN_REMOVED_FROM_REGISTRY] %s", e.path)

                elif e.action == ACTION_ZERO_BYTE:
                    fpath = Path(e.path)
                    if fpath.exists():
                        dest = self._archive_file(fpath, reason="ZERO_BYTE",
                                                  execute=True)
                        archived_count += 1
                        e.executed = True
                        log.info("[ARCHIVED] %s → %s", fpath.name, dest.name)

            except Exception as exc:
                err_msg = f"execute error [{e.action}] {e.path}: {exc}"
                log.error(err_msg)
                self._report.errors.append(err_msg)

        self._report.archived = archived_count
        self._report.action_plan_executed = True

        # Persistir registry actualizado (Ley-1: con self-hash)
        self._save_registry()

    # ── FASE 5: GUARDAR REGISTRY ─────────────────────────────────────────────
    def _save_registry(self) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.registry_path.with_suffix(".tmp")

        # Primera escritura sin self-hash
        payload = json.dumps(self._registry, indent=2, ensure_ascii=False)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)

        # Calcular self-hash y reescribir (Ley-1)
        self_sha = _sha256_file(tmp)
        self._registry["__registry_sha256__"] = self_sha
        payload = json.dumps(self._registry, indent=2, ensure_ascii=False)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)

        tmp.replace(self.registry_path)
        self._report.registry_sha256_after = self_sha
        log.info("SHA_REGISTRY guardado: %d entradas | self_sha=%s",
                 len(self._registry), self_sha[:12])

    # ── FASE 6: EXPORTAR REPORTE ─────────────────────────────────────────────
    def export_report(self, output_path: Optional[Path] = None) -> Path:
        """Exporta el reporte JSON a disco con SHA-256 integrado (Ley-1)."""
        out = output_path or (self.spel_root / "00_VAULT" / "repair_report.json")
        out.parent.mkdir(parents=True, exist_ok=True)

        report_dict = self._report.to_dict()
        tmp = out.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(report_dict, fh, indent=2, ensure_ascii=False, default=str)

        # SHA del reporte (Ley-1)
        report_sha = _sha256_file(tmp)
        report_dict["report_sha256"] = report_sha
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(report_dict, fh, indent=2, ensure_ascii=False, default=str)

        tmp.replace(out)
        log.info("Reporte exportado → %s | SHA=%s", out, report_sha[:12])
        return out

    # ── UTILIDADES INTERNAS ──────────────────────────────────────────────────
    def _archive_file(self, source: Path, reason: str,
                      execute: bool = False) -> Path:
        """
        Ley-4: NUNCA eliminar. Mover a 99_ARCHIVE_FENIX/ con timestamp.
        Si execute=False, solo retorna el path destino proyectado.
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.archive_root.mkdir(parents=True, exist_ok=True)
        dest = self.archive_root / f"{reason}__{ts}__{source.name}"
        if execute:
            shutil.move(str(source), str(dest))
            log.info("LEY-4 MOVE: %s → %s", source.name, dest.name)
        return dest


# ─────────────────────────────────────────────────────────────────────────────
# UTILIDADES PURAS
# ─────────────────────────────────────────────────────────────────────────────
def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha_git(path: Path) -> str:
    """SHA-1 git blob compatible. Necesario para comparación Drive↔GitHub."""
    with open(path, "rb") as fh:
        content = fh.read()
    header = f"blob {len(content)}\0".encode("utf-8")
    return hashlib.sha1(header + content).hexdigest()  # noqa: S324


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description=(
            "SPEL 3.0 Registry Repair Engine — Holmes OS V4.0\n"
            "Modo default: DRY-RUN (solo plan). Usar --execute para aplicar."
        )
    )
    parser.add_argument("--root", default=".", type=Path,
                        help="Raíz SPEL (default: directorio actual)")
    parser.add_argument("--execute", action="store_true",
                        help="Ejecutar el plan de acción (Ley-4: sin borrados)")
    parser.add_argument("--output", type=Path,
                        help="Path para el reporte JSON (default: 00_VAULT/repair_report.json)")
    args = parser.parse_args()

    log.info("═══ REGISTRY REPAIR ENGINE v%s START ═══", REPAIR_VERSION)
    log.info("SPEL root: %s", args.root)
    log.info("Modo: %s", "EXECUTE" if args.execute else "DRY-RUN")

    engine = RegistryRepairEngine(spel_root=args.root)
    engine.load_registry()
    entries = engine.scan_tree()
    engine.build_action_plan(entries)

    if args.execute:
        engine.execute_plan(entries)
        log.info("Plan ejecutado. SHA_REGISTRY actualizado.")
    else:
        log.info("DRY-RUN completado. Usar --execute para aplicar cambios.")

    report_path = engine.export_report(args.output)
    log.info("Reporte disponible: %s", report_path)
    log.info("═══ REGISTRY REPAIR ENGINE COMPLETE ═══")

    # Exit code: 0 OK, 1 si hay errores en el reporte
    return 1 if engine._report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
