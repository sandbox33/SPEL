"""
spel_guardian.py
================
Holmes OS V4.0 · Guardian Central · Domain Alfa (GitHub Actions)
API Gateway | SHA Validator | Secret Vault | Telegram Router | Circuit Breaker

Leyes activas: Ley-1 (SHA-256), Ley-2 (lazy torch), Ley-4 (never delete),
               R21 (cero hardcode), R37 (lazy imports), R40/EF-25 (no sandbox en prod)

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
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import urllib.request
import urllib.error

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTES DE ARQUITECTURA
# ─────────────────────────────────────────────────────────────────────────────
GUARDIAN_VERSION = "4.1.0"
SHA_REGISTRY_FILENAME = "SHA_REGISTRY.json"
ARCHIVE_DIR_NAME = "99_ARCHIVE_FENIX"
CHUNK_SIZE = 65_536          # 64KB — RAM-safe para 2GB reales
REGISTRY_MAX_SIZE_KB = 512   # lectura parcial si supera este umbral
TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"

# Canales Telegram — nombres canónicos Holmes OS V4.0
class TelegramChannel(str, Enum):
    SISTEMA   = "TELEGRAM_SISTEMA"
    SENALES   = "TELEGRAM_SENALES"
    BACKUP    = "TELEGRAM_BACKUP"
    CAOS      = "TELEGRAM_CAOS"

# Estados del Circuit Breaker
class CBState(Enum):
    CLOSED   = auto()   # operación normal
    OPEN     = auto()   # bloqueado tras fallos
    HALF_OPEN = auto()  # prueba de recuperación

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
def _build_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)-8s [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ"
        ))
        log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    return log

log = _build_logger("GUARDIAN")


# ─────────────────────────────────────────────────────────────────────────────
# TIER-1: SECRET VAULT  (R21 compliant — cero hardcode)
# ─────────────────────────────────────────────────────────────────────────────
class SecretVault:
    """
    Carga secretos con prioridad tripartita:
      1. os.environ          → GitHub Actions Secrets
      2. google.colab.userdata → Colab runtime
      3. 00_VAULT/secrets.json → fallback local (nunca en repo)

    Si ningún tier provee la clave, lanza RuntimeError y alerta CAOS.
    """

    _REQUIRED_KEYS = [
        "GITHUB_TOKEN",
        "TELEGRAM_TOKEN",
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
        "TELEGRAM_SISTEMA",
        "TELEGRAM_SENALES",
        "TELEGRAM_BACKUP",
        "TELEGRAM_CAOS",
    ]

    def __init__(self, vault_path: Optional[Path] = None) -> None:
        self._vault_path = vault_path or Path("00_VAULT/secrets.json")
        self._cache: Dict[str, str] = {}
        self._tier_used: Dict[str, str] = {}

    def get_secret(self, key: str) -> str:
        """Retorna el secreto o lanza RuntimeError con diagnóstico."""
        if key in self._cache:
            return self._cache[key]

        # Tier 1: GitHub Actions / entorno
        val = os.environ.get(key)
        if val:
            self._cache[key] = val
            self._tier_used[key] = "ENV"
            log.debug("SECRET [%s] → tier ENV", key)
            return val

        # Tier 2: Google Colab userdata
        val = self._colab_get(key)
        if val:
            self._cache[key] = val
            self._tier_used[key] = "COLAB"
            log.debug("SECRET [%s] → tier COLAB", key)
            return val

        # Tier 3: secrets.json local
        val = self._local_vault_get(key)
        if val:
            self._cache[key] = val
            self._tier_used[key] = "LOCAL_VAULT"
            log.warning(
                "SECRET [%s] → tier LOCAL_VAULT. "
                "Verificar que secrets.json NO esté en el repositorio.",
                key
            )
            return val

        raise RuntimeError(
            f"[VAULT_MISS] Clave '{key}' no encontrada en ningún tier. "
            f"Verificar GH Secrets / Colab userdata / 00_VAULT/secrets.json"
        )

    def validate_required(self) -> List[str]:
        """Retorna lista de claves faltantes sin lanzar excepción."""
        missing = []
        for k in self._REQUIRED_KEYS:
            try:
                self.get_secret(k)
            except RuntimeError:
                missing.append(k)
        return missing

    @staticmethod
    def _colab_get(key: str) -> Optional[str]:
        try:
            from google.colab import userdata  # type: ignore
            return userdata.get(key)
        except Exception:
            return None

    def _local_vault_get(self, key: str) -> Optional[str]:
        if not self._vault_path.exists():
            return None
        try:
            with open(self._vault_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data.get(key)
        except Exception as exc:
            log.error("LOCAL_VAULT read error: %s", exc)
            return None

    def tier_report(self) -> Dict[str, str]:
        return dict(self._tier_used)


# ─────────────────────────────────────────────────────────────────────────────
# TIER-2: CIRCUIT BREAKER  (por servicio, reset automático 60s)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class CircuitBreaker:
    """
    Circuit Breaker per-service. Maneja 429 (rate-limit) y errores de red.
    Estado: CLOSED → OPEN (tras max_failures) → HALF_OPEN → CLOSED
    """
    service: str
    max_failures: int = 3
    cooldown_sec: float = 60.0
    _state: CBState = field(default=CBState.CLOSED, init=False)
    _failures: int = field(default=0, init=False)
    _opened_at: float = field(default=0.0, init=False)

    def call_allowed(self) -> bool:
        if self._state == CBState.CLOSED:
            return True
        if self._state == CBState.OPEN:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self.cooldown_sec:
                self._state = CBState.HALF_OPEN
                log.info("CB [%s] → HALF_OPEN (%.1fs elapsed)", self.service, elapsed)
                return True
            log.warning("CB [%s] OPEN — %.1fs restantes", self.service,
                        self.cooldown_sec - elapsed)
            return False
        # HALF_OPEN: permite exactamente 1 intento
        return True

    def on_success(self) -> None:
        if self._state != CBState.CLOSED:
            log.info("CB [%s] → CLOSED (recuperado)", self.service)
        self._state = CBState.CLOSED
        self._failures = 0

    def on_failure(self, status_code: int = 0) -> None:
        self._failures += 1
        if status_code == 429:
            log.warning("CB [%s] 429 RATE-LIMIT — fallo %d/%d",
                        self.service, self._failures, self.max_failures)
        if self._failures >= self.max_failures or self._state == CBState.HALF_OPEN:
            self._state = CBState.OPEN
            self._opened_at = time.monotonic()
            log.error("CB [%s] → OPEN (cooldown %.0fs)", self.service, self.cooldown_sec)

    def status(self) -> Dict[str, Any]:
        return {
            "service": self.service,
            "state": self._state.name,
            "failures": self._failures,
        }


# ─────────────────────────────────────────────────────────────────────────────
# TIER-3: SHA VALIDATOR  (Ley-1 compliant)
# ─────────────────────────────────────────────────────────────────────────────
class SHAValidator:
    """
    Validador de integridad SHA.

    NOTA ARQUITECTURAL: SHA_REGISTRY almacena DOS hashes por entrada:
      - sha256: SHA-256 raw del contenido binario (integridad local / Ley-1)
      - sha_git: SHA-1 git-blob = sha1("blob {size}\\0{content}") (sincronización GH)

    Este diseño corrige la causa raíz de la divergencia masiva detectada en
    NATHAN_DRAKE_AUDIT.json: la comparación previa mezclaba sha256 con sha_git,
    métricas fundamentalmente incomparables.
    """

    def __init__(self, registry_path: Path) -> None:
        self._path = registry_path
        self._registry: Dict[str, Dict[str, Any]] = {}
        self._dirty = False

    def load(self) -> None:
        """Carga el registro con lectura parcial si supera REGISTRY_MAX_SIZE_KB."""
        if not self._path.exists():
            log.warning("SHA_REGISTRY no encontrado en %s — iniciando vacío", self._path)
            self._registry = {}
            return
        size_kb = self._path.stat().st_size / 1024
        if size_kb > REGISTRY_MAX_SIZE_KB:
            log.warning("SHA_REGISTRY tamaño %.1f KB > umbral %d KB — carga parcial",
                        size_kb, REGISTRY_MAX_SIZE_KB)
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                self._registry = json.load(fh)
            log.info("SHA_REGISTRY cargado: %d entradas (%.1f KB)",
                     len(self._registry), size_kb)
        except json.JSONDecodeError as exc:
            log.error("SHA_REGISTRY corrupto: %s — respaldando y reiniciando", exc)
            self._archive_corrupted_registry()
            self._registry = {}

    def save(self) -> None:
        """Escritura atómica con SHA-256 del propio registro (Ley-1)."""
        if not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        payload = json.dumps(self._registry, indent=2, ensure_ascii=False)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
        # Self-hash del registro (Ley-1)
        self._registry["__registry_sha256__"] = _sha256_file(tmp)
        payload = json.dumps(self._registry, indent=2, ensure_ascii=False)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
        tmp.replace(self._path)
        log.info("SHA_REGISTRY guardado: %d entradas → %s",
                 len(self._registry) - 1, self._path)
        self._dirty = False

    def validate_file(self, file_path: Path) -> Tuple[bool, str, str]:
        """
        Valida un archivo contra el registro.
        Returns: (is_valid, real_sha256, registered_sha256)
        """
        key = str(file_path)
        real_sha = _sha256_file(file_path)
        entry = self._registry.get(key, {})
        registered = entry.get("sha256", "UNREGISTERED")
        is_valid = (real_sha == registered) if registered != "UNREGISTERED" else False
        return is_valid, real_sha, registered

    def register_file(self, file_path: Path) -> str:
        """Calcula y registra ambos hashes (sha256 + sha_git). Ley-1."""
        key = str(file_path)
        sha256 = _sha256_file(file_path)
        sha_git = _sha_git(file_path)
        size_kb = file_path.stat().st_size / 1024
        self._registry[key] = {
            "sha256": sha256,
            "sha_git": sha_git,
            "size_kb": round(size_kb, 3),
            "ts_validated": _utc_now(),
        }
        self._dirty = True
        return sha256

    def get_entry(self, file_path: Path) -> Optional[Dict[str, Any]]:
        return self._registry.get(str(file_path))

    def _archive_corrupted_registry(self) -> None:
        archive = _archive_path(self._path, "CORRUPTED_REGISTRY")
        shutil.move(str(self._path), str(archive))
        log.warning("SHA_REGISTRY corrupto movido → %s (Ley-4)", archive)

    @property
    def registry(self) -> Dict[str, Dict[str, Any]]:
        return self._registry


# ─────────────────────────────────────────────────────────────────────────────
# TIER-4: TELEGRAM ROUTER
# ─────────────────────────────────────────────────────────────────────────────
class TelegramRouter:
    """
    Enruta mensajes a los 4 canales Holmes OS.
    SISTEMA  → salud del sistema y health_check periódico
    SEÑALES  → señales de trading confirmadas
    BACKUP   → operaciones de respaldo y sync
    CAOS     → alertas críticas, anomalías, errores de Circuit Breaker
    """

    def __init__(self, vault: SecretVault) -> None:
        self._vault = vault
        self._cb = CircuitBreaker(service="TELEGRAM", max_failures=3, cooldown_sec=60.0)

    def send(self, channel: TelegramChannel, message: str,
             parse_mode: str = "HTML") -> bool:
        """Envía mensaje al canal especificado. Retorna True si exitoso."""
        if not self._cb.call_allowed():
            log.warning("TELEGRAM CB OPEN — mensaje descartado canal %s", channel.value)
            return False
        try:
            token = self._vault.get_secret("TELEGRAM_TOKEN")
            chat_id = self._vault.get_secret(channel.value)
        except RuntimeError as exc:
            log.error("TELEGRAM secret miss: %s", exc)
            return False

        url = TELEGRAM_API_BASE.format(token=token)
        payload = json.dumps({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": parse_mode,
        }).encode("utf-8")

        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    self._cb.on_success()
                    log.debug("TELEGRAM → %s ✓", channel.name)
                    return True
                self._cb.on_failure(resp.status)
                log.error("TELEGRAM HTTP %d → canal %s", resp.status, channel.name)
                return False
        except urllib.error.HTTPError as exc:
            self._cb.on_failure(exc.code)
            log.error("TELEGRAM HTTPError %d → %s: %s", exc.code, channel.name, exc)
            return False
        except Exception as exc:
            self._cb.on_failure()
            log.error("TELEGRAM error → %s: %s", channel.name, exc)
            return False

    def alert_caos(self, title: str, detail: str) -> None:
        msg = f"🔴 <b>[CAOS] {title}</b>\n<pre>{detail[:3000]}</pre>"
        self.send(TelegramChannel.CAOS, msg)

    def health_report(self, report: Dict[str, Any]) -> None:
        lines = [f"🟢 <b>[SISTEMA] Guardian Health Check</b>",
                 f"📅 {report.get('ts', '?')}",
                 f"📦 Archivos validados: {report.get('files_validated', 0)}",
                 f"✅ OK: {report.get('ok', 0)} | ❌ FAIL: {report.get('fail', 0)}",
                 f"🔐 Secretos missing: {report.get('missing_secrets', [])}",
                 f"⚡ Circuit Breakers: {report.get('circuit_breakers', {})}",
                 f"📊 Guardian v{GUARDIAN_VERSION}"]
        self.send(TelegramChannel.SISTEMA, "\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# TIER-5: GITHUB SYNC CLIENT
# ─────────────────────────────────────────────────────────────────────────────
class GitHubSyncClient:
    """
    Cliente minimalista para la API de GitHub.
    Usa urllib stdlib — sin dependencias externas (RAM-safe).
    Solo sincroniza si sha_git difiere del remoto.
    """

    GITHUB_API = "https://api.github.com"

    def __init__(self, vault: SecretVault) -> None:
        self._vault = vault
        self._cb = CircuitBreaker(service="GITHUB", max_failures=3, cooldown_sec=120.0)
        self._repo: Optional[str] = None

    def _headers(self) -> Dict[str, str]:
        token = self._vault.get_secret("GITHUB_TOKEN")
        return {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(self, method: str, endpoint: str,
                 body: Optional[Dict] = None) -> Tuple[int, Any]:
        """HTTP request con Circuit Breaker."""
        if not self._cb.call_allowed():
            raise RuntimeError(f"GitHub CB OPEN — {endpoint}")
        url = f"{self.GITHUB_API}{endpoint}"
        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                self._cb.on_success()
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            self._cb.on_failure(exc.code)
            body_txt = exc.read().decode("utf-8", errors="replace")
            log.error("GitHub HTTP %d %s: %s", exc.code, endpoint, body_txt[:200])
            return exc.code, {}
        except Exception as exc:
            self._cb.on_failure()
            raise

    def get_remote_sha(self, repo: str, file_path: str, branch: str = "main") -> Optional[str]:
        """Obtiene el SHA git del archivo remoto."""
        endpoint = f"/repos/{repo}/contents/{file_path}?ref={branch}"
        status, data = self._request("GET", endpoint)
        if status == 200:
            return data.get("sha")
        if status == 404:
            return None
        return None

    def push_file(self, repo: str, file_path: str, local_path: Path,
                  branch: str = "main", message: Optional[str] = None) -> bool:
        """
        Sube archivo a GitHub solo si sha_git difiere (evita commits vacíos).
        Ley-1: incluye SHA en el commit message.
        """
        import base64
        with open(local_path, "rb") as fh:
            content = fh.read()
        b64_content = base64.b64encode(content).decode("utf-8")
        local_git_sha = _sha_git_from_bytes(content)

        remote_sha = self.get_remote_sha(repo, file_path, branch)
        if remote_sha == local_git_sha:
            log.debug("SYNC SKIP %s — sha_git idéntico", file_path)
            return True

        commit_msg = message or f"[Guardian] sync {file_path} sha_git:{local_git_sha[:12]}"
        body: Dict[str, Any] = {
            "message": commit_msg,
            "content": b64_content,
            "branch": branch,
        }
        if remote_sha:
            body["sha"] = remote_sha  # requerido para actualizar

        status, _ = self._request("PUT", f"/repos/{repo}/contents/{file_path}", body)
        success = status in (200, 201)
        if success:
            log.info("SYNC OK %s → %s", file_path, repo)
        else:
            log.error("SYNC FAIL %s HTTP %d", file_path, status)
        return success

    def cb_status(self) -> Dict[str, Any]:
        return self._cb.status()


# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATOR  — Guardian Central
# ─────────────────────────────────────────────────────────────────────────────
class GuardianOrchestrator:
    """
    Núcleo administrativo. Coordina los 5 tiers.
    Entry point principal para GitHub Actions.
    """

    def __init__(
        self,
        spel_root: Optional[Path] = None,
        registry_path: Optional[Path] = None,
        github_repo: str = "sandbox33/SPEL",
    ) -> None:
        self.spel_root = spel_root or Path(".")
        self.registry_path = registry_path or (self.spel_root / "00_VAULT" / SHA_REGISTRY_FILENAME)
        self.github_repo = github_repo

        # Inicializar tiers
        self.vault = SecretVault(vault_path=self.spel_root / "00_VAULT" / "secrets.json")
        self.sha_validator = SHAValidator(self.registry_path)
        self.telegram = TelegramRouter(self.vault)
        self.gh_client = GitHubSyncClient(self.vault)

        self._health: Dict[str, Any] = {
            "ts": _utc_now(),
            "files_validated": 0,
            "ok": 0,
            "fail": 0,
            "synced": 0,
            "missing_secrets": [],
            "circuit_breakers": {},
            "errors": [],
        }

    def run_health_check(
        self,
        validate_paths: Optional[List[Path]] = None,
        sync_on_change: bool = False,
        branch: str = "main",
    ) -> Dict[str, Any]:
        """
        Pipeline completo de Guardian:
          1. Validar secretos requeridos
          2. Cargar SHA_REGISTRY
          3. Validar integridad de archivos monitoreados
          4. Sincronizar Drive→GitHub si sha_git difiere (opcional)
          5. Enviar health_check a Telegram SISTEMA
          6. Alertar a CAOS si hay fallos
        """
        log.info("═══ GUARDIAN HEALTH CHECK START — %s ═══", self._health["ts"])

        # 1. Secretos
        missing = self.vault.validate_required()
        self._health["missing_secrets"] = missing
        if missing:
            log.error("SECRETOS FALTANTES: %s", missing)
            self.telegram.alert_caos("VAULT_MISS", f"Claves faltantes: {missing}")

        # 2. Cargar registro SHA
        self.sha_validator.load()

        # 3. Validar archivos
        paths_to_check = validate_paths or self._discover_critical_files()
        ok_count = 0
        fail_count = 0
        failed_files: List[str] = []

        for fpath in paths_to_check:
            if not fpath.exists():
                log.debug("SKIP (no existe): %s", fpath)
                continue
            is_valid, real_sha, reg_sha = self.sha_validator.validate_file(fpath)
            if is_valid:
                ok_count += 1
                log.debug("SHA OK: %s", fpath.name)
            else:
                if reg_sha == "UNREGISTERED":
                    # Registrar primera vez
                    self.sha_validator.register_file(fpath)
                    log.info("SHA REGISTERED (nuevo): %s → %s", fpath.name, real_sha[:12])
                    ok_count += 1
                else:
                    fail_count += 1
                    failed_files.append(str(fpath))
                    log.warning("SHA DRIFT: %s | real=%s | registry=%s",
                                fpath.name, real_sha[:12], reg_sha[:12])

        self._health["files_validated"] = ok_count + fail_count
        self._health["ok"] = ok_count
        self._health["fail"] = fail_count

        # 4. Sincronización selectiva Drive→GitHub
        if sync_on_change and not missing:
            synced = self._sync_changed_files(branch)
            self._health["synced"] = synced

        # 5. Persistir registro actualizado
        self.sha_validator.save()

        # 6. Circuit breakers status
        self._health["circuit_breakers"] = {
            "github": self.gh_client.cb_status(),
            "telegram": self.telegram._cb.status(),
        }

        # 7. Reportar a Telegram
        if fail_count > 0 or missing:
            self.telegram.alert_caos(
                "SHA_DRIFT_DETECTED",
                f"Archivos con drift: {len(failed_files)}\n" +
                "\n".join(failed_files[:20])
            )
        self.telegram.health_report(self._health)

        log.info("═══ GUARDIAN HEALTH CHECK COMPLETE — OK:%d FAIL:%d ═══",
                 ok_count, fail_count)
        return self._health

    def _discover_critical_files(self) -> List[Path]:
        """
        Descubre archivos críticos monitoreados por Guardian.
        Excluye 99_ARCHIVE_FENIX, __pycache__, .git
        """
        CRITICAL_EXTENSIONS = {".py", ".json", ".yml", ".md"}
        EXCLUDED_DIRS = {"99_ARCHIVE_FENIX", "__pycache__", ".git", "node_modules"}
        result: List[Path] = []
        try:
            for p in self.spel_root.rglob("*"):
                if any(part in EXCLUDED_DIRS for part in p.parts):
                    continue
                if p.is_file() and p.suffix in CRITICAL_EXTENSIONS:
                    result.append(p)
        except Exception as exc:
            log.error("_discover_critical_files error: %s", exc)
        log.info("Archivos críticos descubiertos: %d", len(result))
        return result

    def _sync_changed_files(self, branch: str) -> int:
        """Sincroniza solo archivos con sha_git distinto al remoto."""
        synced = 0
        registry = self.sha_validator.registry
        for path_str, entry in registry.items():
            if path_str == "__registry_sha256__":
                continue
            fpath = Path(path_str)
            if not fpath.exists():
                continue
            local_sha_git = entry.get("sha_git", "")
            remote_sha = self.gh_client.get_remote_sha(
                self.github_repo,
                path_str.replace(str(self.spel_root) + "/", ""),
                branch
            )
            if local_sha_git != remote_sha:
                try:
                    ok = self.gh_client.push_file(
                        self.github_repo,
                        path_str.replace(str(self.spel_root) + "/", ""),
                        fpath,
                        branch=branch
                    )
                    if ok:
                        synced += 1
                except Exception as exc:
                    log.error("SYNC error %s: %s", fpath.name, exc)
                    self._health["errors"].append(str(exc))
        return synced


# ─────────────────────────────────────────────────────────────────────────────
# UTILIDADES PURAS  (sin side-effects)
# ─────────────────────────────────────────────────────────────────────────────
def _sha256_file(path: Path) -> str:
    """SHA-256 del contenido binario. Chunks de 64KB — RAM-safe."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha_git(path: Path) -> str:
    """SHA-1 compatible con git blob: sha1("blob {size}\\0{content}")."""
    with open(path, "rb") as fh:
        content = fh.read()
    return _sha_git_from_bytes(content)


def _sha_git_from_bytes(content: bytes) -> str:
    """SHA-1 git blob desde bytes en memoria."""
    header = f"blob {len(content)}\0".encode("utf-8")
    return hashlib.sha1(header + content).hexdigest()  # noqa: S324 (git protocol)


def _archive_path(source: Path, reason: str) -> Path:
    """
    Construye path de destino en 99_ARCHIVE_FENIX.
    Ley-4: NUNCA eliminar. Solo mover.
    Formato: 99_ARCHIVE_FENIX/{reason}__{timestamp}__{filename}
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_root = source.parents[1] / ARCHIVE_DIR_NAME
    archive_root.mkdir(parents=True, exist_ok=True)
    return archive_root / f"{reason}__{ts}__{source.name}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT — GitHub Actions compatible
# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="SPEL 3.0 Guardian Central — Holmes OS V4.0"
    )
    parser.add_argument("--spel-root", default=".", type=Path,
                        help="Raíz del proyecto SPEL")
    parser.add_argument("--repo", default="sandbox33/SPEL",
                        help="Repositorio GitHub (owner/name)")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--sync", action="store_true",
                        help="Activar sincronización Drive→GitHub")
    parser.add_argument("--paths", nargs="*", type=Path,
                        help="Paths específicos a validar")
    args = parser.parse_args()

    guardian = GuardianOrchestrator(
        spel_root=args.spel_root,
        github_repo=args.repo,
    )
    health = guardian.run_health_check(
        validate_paths=args.paths,
        sync_on_change=args.sync,
        branch=args.branch,
    )

    # Exit code para GitHub Actions: 1 si hay fallos críticos
    critical_fail = health["fail"] > 0 or bool(health["missing_secrets"])
    return 1 if critical_fail else 0


if __name__ == "__main__":
    sys.exit(main())
