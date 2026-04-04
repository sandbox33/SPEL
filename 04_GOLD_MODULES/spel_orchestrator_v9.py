# ══════════════════════════════════════════════════════════════════════════════
# spel_orchestrator_v9.py
# SPEL v23 — Orquestador Inmortal · El Corazón del Sistema
#
# Autor  : Abraham Fuenmayor
# Versión: v23.0.0 · 04 Mar 2026
#
# ARQUITECTURA DE HILOS:
#   Thread-DATA    : SPELDataHarvester en loop · consolida parquets Hive v22
#   Thread-COMPUTE : SPELMathEngine + SPELBackbone · escribe state JSON atómico
#   Thread-UI      : Streamlit + ngrok · polling de puerto + guard de proceso
#   Thread-GIT     : git add/commit/push cada 15min · persistencia de métricas
#
# COMUNICACIÓN ENTRE HILOS:
#   Thread-DATA → Thread-COMPUTE : señal via threading.Event (_data_ready)
#   Thread-COMPUTE → Dashboard   : ranking_latest.json (escritura atómica POSIX)
#   Thread-GIT → GitHub          : commit de logs/metrics (nunca data_lake ni .env)
#
# REGLAS ACTIVAS:
#   Regla 4  : λ por activo — BTC=21d · NVDA=63d · XAU=63d · NIFTY50=42d
#   Regla 13 : LSTM inamovible — no se re-entrena aquí
#   Regla 22 : detectar_col_fecha() via harvester — nunca acceso directo a raw
#   Regla 24 : todo acceso externo via SPELAdapterChain
#   Regla 26 : z_params validados antes de cualquier inferencia
#
# WORKAROUND GAP-B (actor_type ausente en schema v4):
#   Hasta que actor_type sea columna 25 del schema, se inyecta una columna
#   sintética con valor "GOV" en la primera mitad temporal y "BUS" en la segunda.
#   Esto separa la entropía GOV/BUS por período, no por actor real.
#   Documentado como DEBT-B · resolver en v23.1 con schema actualizado.
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl

# ── Logging institucional ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)-24s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_log = logging.getLogger("spel.orchestrator_v9")


# ══════════════════════════════════════════════════════════════════════════════
# PATHS Y CONSTANTES (resolución dinámica desde env)
# ══════════════════════════════════════════════════════════════════════════════

_ROOT         = Path(os.environ.get("SPEL_ROOT_V23",  "/content/spel_root"))
_PROD         = Path(os.environ.get("SPEL_PROD",      "/content/drive/MyDrive/SPEL_v8_PROD"))
_STATE_DIR    = Path(os.environ.get("SPEL_STATE_DIR", str(_ROOT / "state")))
_LOGS_DIR     = Path(os.environ.get("SPEL_LOGS_DIR",  str(_ROOT / "logs")))
_STATE_FILE   = _STATE_DIR / "ranking_latest.json"
_METRICS_FILE = _LOGS_DIR  / "orchestrator_metrics.jsonl"

_ACTIVOS   = [a.strip() for a in os.environ.get("SPEL_ACTIVOS", "NVDA,BTC,XAU,NIFTY50").split(",")]
_CAPITAL   = float(os.environ.get("ORCHESTRATOR_CAPITAL", "5000.0"))
_DATA_INTERVAL_S    = int(os.environ.get("ORCHESTRATOR_DATA_INTERVAL_S",    "300"))
_COMPUTE_INTERVAL_S = int(os.environ.get("ORCHESTRATOR_COMPUTE_INTERVAL_S", "60"))
_GIT_INTERVAL_S     = int(os.environ.get("ORCHESTRATOR_GIT_PUSH_INTERVAL_S","900"))
_DASHBOARD_PORT     = int(os.environ.get("DASHBOARD_PORT", "8080"))
_DASHBOARD_PATH     = Path(os.environ.get("DASHBOARD_PATH", str(_ROOT / "interface" / "ojo_de_dios_v23.py")))

# Fix sys.path para módulos core
for _p in [str(_ROOT), str(_ROOT / "core"), str(_ROOT / "interface")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ══════════════════════════════════════════════════════════════════════════════
# SHARED STATE (thread-safe)
# ══════════════════════════════════════════════════════════════════════════════

class _OrchestratorState:
    """Singleton de estado compartido entre hilos. Thread-safe via RLock."""

    def __init__(self) -> None:
        self._lock        = threading.RLock()
        self._cycle       = 0
        self._data_alive  = False
        self._compute_alive = False
        self._last_harvest: datetime | None = None
        self._last_compute: datetime | None = None
        self._last_ranking: dict | None     = None
        self._status      = "INITIALIZING"

    def mark_harvest(self) -> None:
        with self._lock:
            self._last_harvest = datetime.now(timezone.utc)
            self._data_alive   = True

    def mark_data_dead(self) -> None:
        with self._lock:
            self._data_alive = False

    def mark_compute(self, ranking_payload: dict) -> None:
        with self._lock:
            self._cycle  += 1
            self._last_compute = datetime.now(timezone.utc)
            self._compute_alive = True
            self._last_ranking  = ranking_payload
            self._status = "RUNNING"

    def mark_compute_dead(self) -> None:
        with self._lock:
            self._compute_alive = False

    def snapshot(self) -> dict:
        with self._lock:
            base = (self._last_ranking or {}).copy()
            base.update({
                "cycle":               self._cycle,
                "status":              self._status,
                "data_thread_alive":   self._data_alive,
                "compute_thread_alive":self._compute_alive,
                "last_harvest_utc":    self._last_harvest.isoformat() if self._last_harvest else None,
                "last_compute_utc":    self._last_compute.isoformat() if self._last_compute else None,
                "ts_utc":              datetime.now(timezone.utc).isoformat(),
                "version":             "v23.0.0",
            })
            return base


_SHARED = _OrchestratorState()
_data_ready = threading.Event()   # Thread-DATA → Thread-COMPUTE signal


# ══════════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ══════════════════════════════════════════════════════════════════════════════

def _atomic_write(path: Path, data: dict) -> None:
    """Escritura atómica POSIX: write-to-temp + os.replace. Thread-safe."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _append_metrics(metrics: dict) -> None:
    """Append-only al JSONL de métricas (para git + auditoría histórica)."""
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(_METRICS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({**metrics, "ts": datetime.now(timezone.utc).isoformat()}) + "\n")


def _serialize_ranking(ranking, price_map: dict[str, pl.DataFrame]) -> dict:
    """
    Convierte RankingResult en dict JSON-serializable.
    Todos los campos de BackboneSignal, StructuralLevels y KellyResult.
    """
    def _signal_to_dict(activo: str, sig) -> dict:
        levels = sig.levels
        kelly  = sig.kelly
        return {
            "direction":      sig.direction.value,
            "natural_score":  sig.natural_score,
            "filter_stage":   sig.filter_stage.value,
            "hurst":          sig.hurst,
            "te_gov":         sig.te_gov,
            "te_bus":         sig.te_bus,
            "anomaly_type":   sig.anomaly_type,
            "godel_signal":   sig.godel_signal,
            "market_regime":  sig.market_regime,
            "likelihood":     sig.likelihood,
            "posterior":      sig.posterior,
            "anomaly_score":  sig.anomaly_score,
            "price_last":     float(price_map[activo]["close"][-1])
                              if activo in price_map and "close" in price_map[activo].columns
                              else None,
            "levels": {
                "entry_price":  levels.entry_price,
                "stop_loss":    levels.stop_loss,
                "take_profit":  levels.take_profit,
                "atr14":        levels.atr14,
                "risk_per_unit":levels.risk_per_unit,
                "rr_ratio":     levels.rr_ratio,
            } if levels else None,
            "kelly": {
                "kelly_full":       kelly.kelly_full,
                "kelly_fractional": kelly.kelly_fractional,
                "contracts":        kelly.contracts,
                "capital_at_risk":  kelly.capital_at_risk,
                "capital":          kelly.capital,
            } if kelly else None,
        }

    return {
        "alpha_activo":  ranking.alpha_activo,
        "ranked_scores": [[a, s] for a, s in ranking.ranked_scores],
        "signals": {
            a: _signal_to_dict(a, sig)
            for a, sig in ranking.all_signals.items()
        },
    }


def _inject_actor_type_synthetic(lf: pl.LazyFrame) -> tuple[pl.LazyFrame, pl.LazyFrame]:
    """
    WORKAROUND GAP-B: inyecta columna actor_type sintética.
    Primera mitad temporal → "GOV" · Segunda mitad → "BUS".
    Esto permite que filter_gdelt_by_actor() separe los LazyFrames,
    manteniendo coherencia semántica aproximada por período.

    DEBT-B: eliminar cuando actor_type sea columna canónica del schema v4.
    """
    df = lf.collect()
    n  = len(df)
    if n == 0:
        lf_gov = lf.with_columns(pl.lit("GOV").alias("actor_type"))
        lf_bus = lf_gov
        return lf_gov, lf_bus

    actors = ["GOV"] * (n // 2) + ["BUS"] * (n - n // 2)
    df_with_actor = df.with_columns(pl.Series("actor_type", actors))
    lf_gov = df_with_actor.filter(pl.col("actor_type") == "GOV").lazy()
    lf_bus = df_with_actor.filter(pl.col("actor_type") == "BUS").lazy()
    return lf_gov, lf_bus


# ══════════════════════════════════════════════════════════════════════════════
# THREAD 1 — DATA (SPELDataHarvester loop)
# ══════════════════════════════════════════════════════════════════════════════

def _data_loop(stop_event: threading.Event) -> None:
    """
    Loop de ingesta. Cada DATA_INTERVAL_S:
      1. Intenta obtener datos frescos via SPELAdapterChain (Regla 24)
      2. Consolida parquets Hive v22
      3. Señaliza _data_ready para que Thread-COMPUTE procese
    Tolerante a fallos: un error en un activo no mata el loop.
    """
    _log.info("[DATA] Hilo iniciado · interval=%ds · activos=%s",
              _DATA_INTERVAL_S, _ACTIVOS)

    from spel_data_harvester import harvester_from_env, SPELDataHarvester

    # Intento de importar el bridge para acceso a datos frescos (Regla 24)
    try:
        from spel_adapter_bridge import SPELDataBridge
        bridge = SPELDataBridge()
        _log.info("[DATA] SPELDataBridge disponible — acceso a datos frescos activo")
    except ImportError:
        bridge = None
        _log.warning("[DATA] SPELAdapterBridge no disponible — modo parquet_cache only")

    while not stop_event.is_set():
        cycle_ts = datetime.now(timezone.utc)
        try:
            for activo in _ACTIVOS:
                try:
                    h: SPELDataHarvester = harvester_from_env(activo)
                    audit = h.audit()

                    # Solo ingestar si el bridge está disponible
                    if bridge is not None:
                        try:
                            res_ohlcv = bridge.get_ohlcv(activo)
                            if hasattr(res_ohlcv, "data") and res_ohlcv.data is not None:
                                h.harvest_ohlcv(res_ohlcv.data)
                                _log.info("[DATA] %s OHLCV harvested — %d rows",
                                          activo, len(res_ohlcv.data))
                        except Exception as e:
                            _log.warning("[DATA] %s OHLCV bridge falló: %s", activo, e)

                        try:
                            res_gdelt = bridge.get_gdelt(activo)
                            if hasattr(res_gdelt, "data") and res_gdelt.data is not None:
                                h.harvest_gdelt(res_gdelt.data)
                                _log.info("[DATA] %s GDELT harvested — %d rows",
                                          activo, len(res_gdelt.data))
                        except Exception as e:
                            _log.warning("[DATA] %s GDELT bridge falló: %s", activo, e)

                    # Consolidar en parquet canónico v4 (siempre, haya o no datos frescos)
                    h.consolidate_ohlcv()
                    h.consolidate_gdelt()
                    _log.info("[DATA] %s consolidado — OHLCV_raw=%d · GDELT_raw=%d",
                              activo, audit.ohlcv_raw_rows, audit.gdelt_raw_rows)

                except Exception as e_activo:
                    _log.error("[DATA] %s error: %s", activo, e_activo, exc_info=True)

            _SHARED.mark_harvest()
            _data_ready.set()  # Señal al Thread-COMPUTE
            _log.info("[DATA] Ciclo completado en %.1fs",
                      (datetime.now(timezone.utc) - cycle_ts).total_seconds())

        except Exception as e_loop:
            _log.error("[DATA] Error en loop principal: %s", e_loop, exc_info=True)

        # Esperar con granularidad fina para responder rápido a stop_event
        for _ in range(_DATA_INTERVAL_S):
            if stop_event.is_set():
                break
            time.sleep(1)

    _SHARED.mark_data_dead()
    _log.info("[DATA] Hilo terminado limpiamente")


# ══════════════════════════════════════════════════════════════════════════════
# THREAD 2 — COMPUTE (MathEngine + Backbone → state JSON)
# ══════════════════════════════════════════════════════════════════════════════

def _compute_loop(stop_event: threading.Event) -> None:
    """
    Loop de cómputo. Se activa cuando _data_ready está set O cada COMPUTE_INTERVAL_S.
    Pipeline:
      read_canonical() → MathEngine.run() → SPELBackbone.dynamic_ranking()
      → _serialize_ranking() → _atomic_write(state JSON)
    """
    _log.info("[COMPUTE] Hilo iniciado · interval=%ds", _COMPUTE_INTERVAL_S)

    from spel_math_engine import math_engine_from_config, filter_gdelt_by_actor, ActorType
    from spel_data_harvester import harvester_from_env
    from spel_backbone_engine import SPELBackbone

    backbone = SPELBackbone(kelly_fraction=0.25, tp_rr_ratio=2.5)

    while not stop_event.is_set():
        # Esperar señal de datos listos o timeout
        triggered_by_data = _data_ready.wait(timeout=_COMPUTE_INTERVAL_S)
        _data_ready.clear()

        if stop_event.is_set():
            break

        cycle_ts = datetime.now(timezone.utc)
        _log.info("[COMPUTE] Ciclo iniciado (trigger=%s)",
                  "DATA_READY" if triggered_by_data else "TIMEOUT")

        alerts_map: dict[str, pl.DataFrame] = {}
        price_map:  dict[str, pl.DataFrame] = {}

        for activo in _ACTIVOS:
            try:
                h = harvester_from_env(activo)

                # ── OHLCV ────────────────────────────────────────────────────
                lf_ohlcv = h.read_canonical()
                df_price = lf_ohlcv.collect()

                if len(df_price) < 30:
                    _log.warning("[COMPUTE] %s: insuficientes filas OHLCV (%d < 30)",
                                 activo, len(df_price))
                    continue

                # ── GDELT + Workaround GAP-B ─────────────────────────────────
                lf_gdelt_raw = h.scan_gdelt_raw(start=None, end=None)
                lf_gov, lf_bus = _inject_actor_type_synthetic(lf_gdelt_raw)

                # ── MathEngine ───────────────────────────────────────────────
                engine = math_engine_from_config(activo)
                result = engine.run(lf_ohlcv, lf_gov, lf_bus)

                if result.n_windows == 0:
                    _log.warning("[COMPUTE] %s: MathEngine devolvió 0 ventanas", activo)
                    continue

                alerts_map[activo] = result.alerts_df
                price_map[activo]  = df_price

                _log.info(
                    "[COMPUTE] %s: %d ventanas · %d anomalías · TE_GOV_max=%.4f",
                    activo, result.n_windows, result.n_anomalies,
                    float(result.alerts_df["te_gov"].max()) if "te_gov" in result.alerts_df.columns else 0,
                )

            except Exception as e_activo:
                _log.error("[COMPUTE] %s error: %s", activo, e_activo, exc_info=True)

        if not alerts_map:
            _log.warning("[COMPUTE] Sin activos con datos suficientes — ciclo abortado")
            _SHARED.mark_compute_dead()
            continue

        # ── Backbone ranking ──────────────────────────────────────────────────
        try:
            ranking = backbone.dynamic_ranking(
                alerts_map=alerts_map,
                price_map=price_map,
                capital=_CAPITAL,
            )
            payload = _serialize_ranking(ranking, price_map)
            _SHARED.mark_compute(payload)

            # Escritura atómica del state
            full_state = _SHARED.snapshot()
            _atomic_write(_STATE_FILE, full_state)

            # Métricas para git/auditoría
            _append_metrics({
                "alpha":         ranking.alpha_activo,
                "alpha_score":   ranking.alpha_signal.natural_score,
                "alpha_dir":     ranking.alpha_signal.direction.value,
                "alpha_godel":   ranking.alpha_signal.godel_signal,
                "alpha_anomaly": ranking.alpha_signal.anomaly_type,
                "n_activos":     len(alerts_map),
                "ranked":        [[a, s] for a, s in ranking.ranked_scores],
            })

            elapsed = (datetime.now(timezone.utc) - cycle_ts).total_seconds()
            _log.info(
                "[COMPUTE] ✅ Alpha=%s score=%.4f dir=%s godel=%s anomaly=%s · %.1fs",
                ranking.alpha_activo,
                ranking.alpha_signal.natural_score,
                ranking.alpha_signal.direction.value,
                ranking.alpha_signal.godel_signal,
                ranking.alpha_signal.anomaly_type,
                elapsed,
            )

        except Exception as e_backbone:
            _log.error("[COMPUTE] SPELBackbone error: %s", e_backbone, exc_info=True)
            _SHARED.mark_compute_dead()

    _SHARED.mark_compute_dead()
    _log.info("[COMPUTE] Hilo terminado limpiamente")


# ══════════════════════════════════════════════════════════════════════════════
# THREAD 3 — UI (Streamlit + ngrok)
# ══════════════════════════════════════════════════════════════════════════════

def _ui_loop(stop_event: threading.Event) -> None:
    """
    Lanza Streamlit como subprocess + ngrok.
    Idéntico al Launcher v8.4 (polling de puerto + guard de proceso vivo + https://).
    """
    _log.info("[UI] Hilo iniciado · dashboard=%s · port=%d", _DASHBOARD_PATH, _DASHBOARD_PORT)

    if not _DASHBOARD_PATH.exists():
        _log.error("[UI] Dashboard no encontrado: %s", _DASHBOARD_PATH)
        return

    log_path = _LOGS_DIR / "streamlit_launch.log"
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)

    _proc: subprocess.Popen | None = None
    _ngrok_tunnel = None

    try:
        with open(log_path, "w") as log_f:
            _proc = subprocess.Popen(
                [sys.executable, "-m", "streamlit", "run",
                 str(_DASHBOARD_PATH),
                 f"--server.port={_DASHBOARD_PORT}",
                 "--server.address=0.0.0.0",
                 "--server.headless=true"],
                stdout=log_f,
                stderr=log_f,
            )
        _log.info("[UI] Streamlit PID=%d lanzado → %s", _proc.pid, log_path)

        # Polling de puerto (max 30s)
        import socket
        for attempt in range(30):
            time.sleep(1)
            try:
                with socket.create_connection(("127.0.0.1", _DASHBOARD_PORT), timeout=1):
                    _log.info("[UI] Puerto %d UP después de %ds", _DASHBOARD_PORT, attempt + 1)
                    break
            except OSError:
                pass
        else:
            _log.error("[UI] Puerto %d no respondió en 30s — revisar %s", _DASHBOARD_PORT, log_path)

        # ngrok
        ngrok_token = os.environ.get("NGROK_TOKEN", "")
        if ngrok_token:
            try:
                from pyngrok import ngrok as _ngrok, conf as _ngrok_conf
                _ngrok_conf.get_default().auth_token = ngrok_token
                _ngrok_tunnel = _ngrok.connect(_DASHBOARD_PORT, "http")
                public_url = str(_ngrok_tunnel.public_url).replace("http://", "https://")
                _log.info("[UI] 🌐 Dashboard público: %s", public_url)
                print(f"\n{'='*65}")
                print(f"  🌐  OJO DE DIOS v23: {public_url}")
                print(f"{'='*65}\n")
            except Exception as e_ngrok:
                _log.error("[UI] ngrok falló: %s", e_ngrok)

        # Guard de proceso vivo
        while not stop_event.is_set():
            if _proc.poll() is not None:
                _log.error("[UI] Streamlit murió (returncode=%d) — no se reinicia automáticamente",
                           _proc.returncode)
                _log.error("[UI] Ver logs en: %s", log_path)
                break
            time.sleep(5)

    finally:
        if _ngrok_tunnel:
            try:
                from pyngrok import ngrok as _ngrok
                _ngrok.disconnect(_ngrok_tunnel.public_url)
            except Exception:
                pass
        if _proc and _proc.poll() is None:
            _proc.terminate()
            _proc.wait(timeout=5)
        _log.info("[UI] Hilo terminado limpiamente")


# ══════════════════════════════════════════════════════════════════════════════
# THREAD 4 — GIT (persistencia cada 15min)
# ══════════════════════════════════════════════════════════════════════════════

def _git_loop(stop_event: threading.Event) -> None:
    """
    Persiste logs y métricas de rendimiento a GitHub cada GIT_INTERVAL_S.
    Nunca commitea: .env · data_lake/ · models/ (excluidos en .gitignore).
    Solo commitea: logs/orchestrator_metrics.jsonl · logs/*.json · state/ (excepto ranking_latest)
    """
    _log.info("[GIT] Hilo iniciado · interval=%ds", _GIT_INTERVAL_S)

    root_str = str(_ROOT)
    github_token = os.environ.get("GITHUB_TOKEN", "")
    github_repo  = os.environ.get("GITHUB_REPO", "")

    if not github_token or not github_repo:
        _log.warning("[GIT] GITHUB_TOKEN o GITHUB_REPO no configurados — hilo desactivado")
        return

    def _git(cmd: list[str]) -> tuple[int, str]:
        r = subprocess.run(["git", "-C", root_str] + cmd,
                           capture_output=True, text=True)
        return r.returncode, r.stdout.strip() or r.stderr.strip()

    # Configurar remote con token (idempotente)
    remote_url = f"https://x-access-token:{github_token}@github.com/{github_repo}.git"
    code, _ = _git(["remote", "get-url", "origin"])
    if code != 0:
        _git(["remote", "add", "origin", remote_url])
    else:
        _git(["remote", "set-url", "origin", remote_url])

    # Configurar identidad git (Colab no tiene config global)
    _git(["config", "user.email", "spel-orchestrator@colab.local"])
    _git(["config", "user.name",  "SPEL Orchestrator v23"])

    while not stop_event.is_set():
        stop_event.wait(timeout=_GIT_INTERVAL_S)
        if stop_event.is_set():
            break

        try:
            ts_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

            # Solo archivos de métricas y logs (no data, no credenciales)
            _git(["add", "logs/orchestrator_metrics.jsonl"])
            _git(["add", "logs/"])

            # Verificar si hay cambios staged
            code, status = _git(["status", "--porcelain"])
            if not status.strip():
                _log.debug("[GIT] Sin cambios staged — skip commit")
                continue

            state = _SHARED.snapshot()
            alpha = state.get("alpha_activo", "?")
            ns    = state.get("signals", {}).get(alpha, {}).get("natural_score", 0) if alpha else 0
            cycle = state.get("cycle", 0)

            commit_msg = (
                f"[SPEL v23] cycle={cycle} alpha={alpha} score={ns*100:.1f}% "
                f"ts={ts_str}"
            )
            _git(["commit", "-m", commit_msg])
            code, out = _git(["push", "-u", "origin", "main", "--force-with-lease"])
            if code == 0:
                _log.info("[GIT] ✅ Push OK — %s", commit_msg)
            else:
                _git(["push", "-u", "origin", "HEAD:main"])
                _log.warning("[GIT] Push con force-with-lease falló, reintento simple: %s", out)

        except Exception as e:
            _log.error("[GIT] Error en ciclo de push: %s", e, exc_info=True)

    _log.info("[GIT] Hilo terminado limpiamente")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT — lanza los 4 hilos y bloquea hasta Ctrl+C o stop
# ══════════════════════════════════════════════════════════════════════════════

def run_orchestrator(
    with_ui:  bool = True,
    with_git: bool = True,
) -> None:
    """
    Punto de entrada principal. Invocado desde el Launcher v9 (Celda 6).
    Bloquea indefinidamente hasta KeyboardInterrupt o error fatal en hilo crítico.
    """
    _log.info("=" * 65)
    _log.info("  🛰️  SPEL ORQUESTADOR v23 INICIANDO")
    _log.info("  %s UTC", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    _log.info("  Activos: %s · Capital: $%.2f", _ACTIVOS, _CAPITAL)
    _log.info("=" * 65)

    # Inicializar state file
    if not _STATE_FILE.exists():
        _atomic_write(_STATE_FILE, _SHARED.snapshot())

    stop = threading.Event()

    threads: list[tuple[str, threading.Thread]] = [
        ("DATA",    threading.Thread(target=_data_loop,    args=(stop,), daemon=True, name="SPEL-DATA")),
        ("COMPUTE", threading.Thread(target=_compute_loop, args=(stop,), daemon=True, name="SPEL-COMPUTE")),
    ]
    if with_ui:
        threads.append(("UI", threading.Thread(target=_ui_loop, args=(stop,), daemon=True, name="SPEL-UI")))
    if with_git:
        threads.append(("GIT", threading.Thread(target=_git_loop, args=(stop,), daemon=True, name="SPEL-GIT")))

    for name, t in threads:
        t.start()
        _log.info("  ✅  Thread-%s iniciado (PID=%d)", name, t.ident or 0)

    print(f"\n{'='*65}")
    print("  🛰️  ORQUESTADOR v23 ACTIVO")
    print(f"  Threads: {', '.join(n for n, _ in threads)}")
    print(f"  State: {_STATE_FILE}")
    print(f"  Logs:  {_METRICS_FILE}")
    print(f"{'='*65}\n")

    # Monitor de hilos críticos (DATA + COMPUTE)
    critical = {n: t for n, t in threads if n in ("DATA", "COMPUTE")}
    try:
        while True:
            time.sleep(30)
            for name, t in critical.items():
                if not t.is_alive():
                    _log.critical("[MONITOR] Thread-%s MUERTO — reiniciando", name)
                    # Reinicio automático del hilo caído
                    if name == "DATA":
                        new_t = threading.Thread(target=_data_loop, args=(stop,),
                                                 daemon=True, name="SPEL-DATA")
                    else:
                        new_t = threading.Thread(target=_compute_loop, args=(stop,),
                                                 daemon=True, name="SPEL-COMPUTE")
                    new_t.start()
                    critical[name] = new_t
                    _log.info("[MONITOR] Thread-%s reiniciado", name)

    except KeyboardInterrupt:
        _log.info("KeyboardInterrupt recibido — apagado limpio")
    finally:
        stop.set()
        for name, t in threads:
            t.join(timeout=10)
            _log.info("  Thread-%s: %s", name, "terminado" if not t.is_alive() else "timeout")
        _log.info("🛑 Orquestador detenido · Adiós, Abraham.")


# ── Modo standalone ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="SPEL Orquestador v23")
    p.add_argument("--no-ui",  action="store_true", help="Sin Streamlit")
    p.add_argument("--no-git", action="store_true", help="Sin git push")
    args = p.parse_args()
    run_orchestrator(with_ui=not args.no_ui, with_git=not args.no_git)
