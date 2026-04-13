"""
spel_narrative.py
=================
Holmes OS V4.0 · Motor de Narrativa IA
Síntesis de lenguaje natural para post-mortems de operaciones

Pipeline:
  trade_data + GDELT headlines + OHLCV last 10 candles
    → Gemini API (urllib nativo, R21)
    → Canal SEÑALES (WIN) o CAOS (LOSS)

Diferenciación FOREX vs CORE:
  FOREX (EURUSD, GBPUSD, USDJPY):
    - Umbrales de entropía: [1e-4, 3e-3] (forex_scalers.json)
    - Prompt enfatiza macro + carry trade + geopolítica rápida
  CORE (NVDA, BTC, XAU, NIFTY50):
    - Umbrales P90: [0.8, 2.8]
    - Prompt enfatiza regímenes KL, CVD institucional, swing

Leyes: R21, Ley-4 (logs persistidos en FENIX nunca borrados),
       cero frameworks IA pesados (solo urllib)

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
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────────────────────────────────────
NARRATIVE_VERSION = "4.1.0"
GEMINI_MODEL = "gemini-1.5-flash"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
ARCHIVE_DIR = "99_ARCHIVE_FENIX"
NARRATIVE_LOG_FILE = Path("00_VAULT/narrative_log.jsonl")

FOREX_ASSETS = {"EURUSD", "GBPUSD", "USDJPY", "EURJPY", "GBPJPY"}
CORE_ASSETS  = {"NVDA", "BTC", "XAU", "NIFTY50"}

# Umbrales por asset class (EF-22)
FOREX_ENTROPY_RANGE = (1e-4, 3e-3)
CORE_P90_RANGE      = (0.8, 2.8)

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-8s [NARRATIVE] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    stream=sys.stdout,
)
log = logging.getLogger("NARRATIVE")


# ─────────────────────────────────────────────────────────────────────────────
# ESTRUCTURAS DE DATOS
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class TradeContext:
    """Contexto de una operación para el motor narrativo."""
    asset: str
    outcome: str          # WIN | LOSS
    direction: str        # LONG | SHORT | FLAT
    entry_ts: str
    entry_price: float
    exit_price: float = 0.0
    gt_score: float = 0.0
    entropy_value: float = 0.0
    kl_div: float = 0.0
    regime: str = "UNKNOWN"
    gdelt_headlines: List[str] = field(default_factory=list)
    ohlcv_last10: List[Dict[str, Any]] = field(default_factory=list)
    vitality: float = 0.0
    breach_probability: float = 0.0

    @property
    def is_forex(self) -> bool:
        return self.asset.upper() in FOREX_ASSETS

    @property
    def pnl_pct(self) -> float:
        if self.entry_price <= 0 or self.exit_price <= 0:
            return 0.0
        mult = 1 if self.direction == "LONG" else -1
        return mult * (self.exit_price - self.entry_price) / self.entry_price * 100


@dataclass
class NarrativeResult:
    """Resultado del motor narrativo con metadata de auditoría."""
    ts: str
    asset: str
    outcome: str
    narrative_text: str
    prompt_sha256: str
    gemini_latency_ms: int
    channel_sent: str
    telegram_ok: bool
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# SECRET VAULT (R21) — reutiliza lógica del guardian
# ─────────────────────────────────────────────────────────────────────────────
def _get_secret(key: str) -> str:
    """3-tier secret routing: ENV → Colab → local vault."""
    # Tier 1: os.environ (GH Actions)
    val = os.environ.get(key)
    if val:
        return val
    # Tier 2: Colab userdata
    try:
        from google.colab import userdata  # type: ignore
        val = userdata.get(key)
        if val:
            return val
    except Exception:
        pass
    # Tier 3: secrets.json local
    vault = Path("00_VAULT/secrets.json")
    if vault.exists():
        try:
            with open(vault, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            val = data.get(key)
            if val:
                log.warning("SECRET [%s] → LOCAL_VAULT. Verificar .gitignore.", key)
                return val
        except Exception:
            pass
    raise RuntimeError(f"[VAULT_MISS] Clave '{key}' no encontrada en ningún tier.")


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT BUILDER — diferenciación FOREX vs CORE
# ─────────────────────────────────────────────────────────────────────────────
class PromptBuilder:
    """
    Construye prompts especializados por asset class.
    FOREX: foco en macro + carry trade + microestructura FX
    CORE:  foco en régimen KL + flujo institucional + GDELT entropy swing
    """

    @staticmethod
    def build(ctx: TradeContext) -> str:
        headlines_str = (
            " | ".join(ctx.gdelt_headlines[:5])
            if ctx.gdelt_headlines else "Sin titulares GDELT disponibles"
        )

        # Construir resumen OHLCV
        ohlcv_summary = ""
        if ctx.ohlcv_last10:
            closes = [c.get("close", 0) for c in ctx.ohlcv_last10[-5:]]
            if closes:
                trend = "alcista" if closes[-1] > closes[0] else "bajista"
                ohlcv_summary = (
                    f"Últimas 5 velas cierre: {', '.join(f'{c:.4f}' for c in closes)} "
                    f"(tendencia {trend}). "
                )

        base_prompt = (
            "Actúa como analista quant institucional de nivel senior. "
            "Responde EXCLUSIVAMENTE con 3 líneas contundentes. "
            "Sin preamble, sin disclaimers. Solo análisis directo.\n\n"
        )

        if ctx.is_forex:
            threshold_info = (
                f"Entropía: {ctx.entropy_value:.6f} "
                f"(rango operativo FOREX: [{FOREX_ENTROPY_RANGE[0]:.4e}, {FOREX_ENTROPY_RANGE[1]:.4e}]). "
            )
            specialization = (
                "Analiza desde la perspectiva de: diferenciales de tasas, "
                "carry trade, flujo de órdenes institucional FX, y "
                "geopolítica de corto plazo detectada en GDELT. "
            )
        else:
            threshold_info = (
                f"GT-Score: {ctx.gt_score:.4f} "
                f"(P90 Core: [{CORE_P90_RANGE[0]}, {CORE_P90_RANGE[1]}]). "
                f"KL Divergence: {ctx.kl_div:.6f}. "
                f"Vitality Tesla: {ctx.vitality:.4f}. "
            )
            specialization = (
                "Analiza desde la perspectiva de: régimen de volatilidad KL, "
                "flujo institucional (CVD/whale detection), "
                "entropía GDELT como señal líder, y "
                "momentum de largo plazo. "
            )

        prompt = (
            f"{base_prompt}"
            f"OPERACIÓN: {ctx.asset} · {ctx.outcome} · {ctx.direction}\n"
            f"Entrada: {ctx.entry_price:.4f} · Salida: {ctx.exit_price:.4f} "
            f"· PnL: {ctx.pnl_pct:+.2f}%\n"
            f"Régimen: {ctx.regime}. "
            f"{threshold_info}"
            f"{ohlcv_summary}"
            f"P(breach): {ctx.breach_probability:.2%}.\n"
            f"Noticias GDELT: {headlines_str}\n\n"
            f"{specialization}"
            f"¿Por qué ocurrió este movimiento? ¿Qué habría cambiado el resultado? "
            f"¿Cuál es la lección cuantitativa?"
        )
        return prompt

    @staticmethod
    def sha256(prompt: str) -> str:
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# GEMINI API CLIENT (urllib nativo — Ley: cero frameworks IA pesados)
# ─────────────────────────────────────────────────────────────────────────────
class GeminiClient:
    """
    Cliente HTTP directo para Gemini API.
    Model: gemini-1.5-flash (API gratuita).
    Rate limit: 15 req/min en tier free → Circuit Breaker integrado.
    """

    def __init__(self) -> None:
        self._failures = 0
        self._max_failures = 3
        self._cb_open = False
        self._cb_opened_at = 0.0
        self._cooldown = 60.0

    def generate(self, prompt: str, max_tokens: int = 250,
                 temperature: float = 0.25) -> Optional[str]:
        """
        Genera texto con Gemini. Retorna None si CB abierto o error.
        """
        # Circuit Breaker check
        if self._cb_open:
            elapsed = time.monotonic() - self._cb_opened_at
            if elapsed < self._cooldown:
                log.warning("GEMINI CB OPEN — %.0fs restantes", self._cooldown - elapsed)
                return None
            self._cb_open = False
            self._failures = 0
            log.info("GEMINI CB → HALF_OPEN")

        try:
            api_key = _get_secret("GEMINI_API_KEY")
        except RuntimeError as exc:
            log.error("GEMINI key miss: %s", exc)
            return None

        url = f"{GEMINI_URL}?key={api_key}"
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
                "topP": 0.9,
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT",
                 "threshold": "BLOCK_NONE"},
            ],
        }).encode("utf-8")

        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self._failures = 0
                text = (
                    data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                    .strip()
                )
                log.info("GEMINI → %d chars generados", len(text))
                return text
        except urllib.error.HTTPError as exc:
            self._on_failure(exc.code)
            body_txt = exc.read().decode("utf-8", errors="replace")
            log.error("GEMINI HTTP %d: %s", exc.code, body_txt[:200])
            return None
        except Exception as exc:
            self._on_failure()
            log.error("GEMINI error: %s", exc)
            return None

    def _on_failure(self, code: int = 0) -> None:
        self._failures += 1
        if code == 429:
            log.warning("GEMINI 429 rate-limit")
        if self._failures >= self._max_failures:
            self._cb_open = True
            self._cb_opened_at = time.monotonic()
            log.error("GEMINI CB → OPEN (cooldown %.0fs)", self._cooldown)


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM SENDER (routing SEÑALES / CAOS)
# ─────────────────────────────────────────────────────────────────────────────
class NarrativeTelegramSender:
    """Envía narrativas al canal correcto según outcome."""

    # Full 4-channel map — Holmes OS canonical routing
    # SISTEMA  → health / watchdog (no usado por narrative, referencia arquitectural)
    # SENALES  → trade signals WIN (lenguaje natural operativo)
    # BACKUP   → .pt checkpoints (no usado por narrative, referencia arquitectural)
    # CAOS     → LOSS post-mortems + auto-train trigger
    CHANNEL_MAP = {
        "WIN":    "TELEGRAM_SENALES",
        "LOSS":   "TELEGRAM_CAOS",
        "HEALTH": "TELEGRAM_SISTEMA",   # reservado para Guardian health
        "BACKUP": "TELEGRAM_BACKUP",    # reservado para checkpoint events
    }

    def send(self, narrative: str, ctx: TradeContext) -> Tuple_[bool, str]:
        """
        WIN  → SEÑALES (lenguaje natural, operativa limpia)
        LOSS → CAOS    (post-mortem + trigger reentrenamiento)
        Returns: (success, channel_name)
        """
        try:
            token    = _get_secret("TELEGRAM_TOKEN")
            chan_key = self.CHANNEL_MAP.get(ctx.outcome, "TELEGRAM_CAOS")
            chat_id  = _get_secret(chan_key)
        except RuntimeError as exc:
            log.error("TELEGRAM secret: %s", exc)
            return False, "UNKNOWN"

        pnl_sign = "🟢" if ctx.outcome == "WIN" else "🔴"
        asset_class = "FOREX" if ctx.is_forex else "CORE"
        header = (
            f"{pnl_sign} <b>[{ctx.outcome}] {ctx.asset} · {asset_class} · "
            f"{ctx.direction}</b>\n"
            f"PnL: {ctx.pnl_pct:+.2f}% | GT-Score: {ctx.gt_score:.4f} | "
            f"Régimen: {ctx.regime}\n"
            f"{'─' * 30}\n"
        )
        retraining_note = ""
        if ctx.outcome == "LOSS":
            retraining_note = (
                f"\n{'─' * 30}\n"
                f"⚙️ <i>GHA Auto-Train Trigger activado para {ctx.asset}</i>"
            )

        message = f"{header}{narrative}{retraining_note}"

        url = TELEGRAM_API_BASE.format(token=token)
        body = json.dumps({
            "chat_id": chat_id,
            "text": message[:4096],  # límite Telegram
            "parse_mode": "HTML",
        }).encode("utf-8")

        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                ok = resp.status == 200
                if ok:
                    log.info("TELEGRAM → %s ✓ (%s)", chan_key, ctx.asset)
                return ok, chan_key
        except Exception as exc:
            log.error("TELEGRAM send error: %s", exc)
            return False, chan_key

    # Python typing workaround
    def __class_getitem__(cls, _): return cls


# Patch: Tuple_ alias para evitar import typing en signature
from typing import Tuple as Tuple_


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT LOGGER — persistencia JSONL (Ley-4: append-only, nunca borrar)
# ─────────────────────────────────────────────────────────────────────────────
class NarrativeAuditLogger:
    """
    Persiste cada resultado narrativo en narrative_log.jsonl.
    Append-only — Ley-4: nunca borrar, rotar a FENIX si supera 50MB.
    """
    MAX_SIZE_MB = 50

    def __init__(self, log_path: Path = NARRATIVE_LOG_FILE) -> None:
        self._path = log_path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, result: NarrativeResult) -> None:
        try:
            self._rotate_if_needed()
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
        except Exception as exc:
            log.error("NarrativeAuditLogger append error: %s", exc)

    def _rotate_if_needed(self) -> None:
        if not self._path.exists():
            return
        size_mb = self._path.stat().st_size / (1024 * 1024)
        if size_mb > self.MAX_SIZE_MB:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            archive = (self._path.parent.parent / ARCHIVE_DIR /
                       f"narrative_log__{ts}.jsonl")
            archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(self._path), str(archive))  # Ley-4
            log.info("narrative_log rotado → %s (Ley-4)", archive.name)


# ─────────────────────────────────────────────────────────────────────────────
# NARRATIVE ENGINE — orquestador principal
# ─────────────────────────────────────────────────────────────────────────────
class NarrativeEngine:
    """
    Orquestador del motor narrativo.
    Flujo: TradeContext → PromptBuilder → GeminiClient → TelegramSender → AuditLog
    """

    def __init__(self) -> None:
        self.gemini   = GeminiClient()
        self.telegram = NarrativeTelegramSender()
        self.auditor  = NarrativeAuditLogger()

    def process_trade(self, ctx: TradeContext) -> NarrativeResult:
        """
        Pipeline completo de síntesis narrativa para un trade cerrado.
        Registra en JSONL independientemente del resultado de Gemini/Telegram.
        """
        log.info("═══ NARRATIVE ENGINE START — %s %s %s ═══",
                 ctx.asset, ctx.outcome, ctx.direction)

        # 1. Build prompt
        prompt = PromptBuilder.build(ctx)
        prompt_sha = PromptBuilder.sha256(prompt)
        log.debug("Prompt SHA: %s", prompt_sha[:12])

        # 2. Gemini call
        t0 = time.monotonic()
        narrative_text = self.gemini.generate(prompt, max_tokens=250, temperature=0.25)
        latency_ms = int((time.monotonic() - t0) * 1000)

        if not narrative_text:
            narrative_text = self._fallback_narrative(ctx)
            log.warning("Gemini falló → usando narrativa fallback")

        # 3. Telegram routing (WIN→SEÑALES, LOSS→CAOS)
        tg_ok, channel_sent = self.telegram.send(narrative_text, ctx)

        # 4. Construir resultado
        result = NarrativeResult(
            ts=datetime.now(timezone.utc).isoformat(),
            asset=ctx.asset,
            outcome=ctx.outcome,
            narrative_text=narrative_text,
            prompt_sha256=prompt_sha,
            gemini_latency_ms=latency_ms,
            channel_sent=channel_sent,
            telegram_ok=tg_ok,
            error=None if narrative_text else "GEMINI_FAIL+FALLBACK",
        )

        # 5. Audit log (Ley-4: append-only)
        self.auditor.append(result)

        log.info("═══ NARRATIVE ENGINE COMPLETE — latency=%dms tg=%s ═══",
                 latency_ms, "OK" if tg_ok else "FAIL")
        return result

    @staticmethod
    def _fallback_narrative(ctx: TradeContext) -> str:
        """
        Narrativa de fallback cuando Gemini no responde.
        Basada puramente en datos cuantitativos del contexto.
        """
        asset_class = "FOREX" if ctx.is_forex else "CORE"
        threshold_breach = (
            "BREACH" if (ctx.is_forex and
                         not (FOREX_ENTROPY_RANGE[0] <= ctx.entropy_value <= FOREX_ENTROPY_RANGE[1]))
            else "BREACH" if (not ctx.is_forex and
                              ctx.gt_score < CORE_P90_RANGE[0])
            else "IN_RANGE"
        )
        lines = [
            f"[{asset_class}] Trade {ctx.outcome} con GT-Score={ctx.gt_score:.4f} "
            f"en régimen {ctx.regime}. Umbral: {threshold_breach}.",
            f"Entropía={ctx.entropy_value:.6f} · KL={ctx.kl_div:.6f} · "
            f"P(breach)={ctx.breach_probability:.2%}. "
            f"Narrativa automática — Gemini API no disponible.",
            f"PnL={ctx.pnl_pct:+.2f}% · Dirección={ctx.direction} · "
            f"Vitality={ctx.vitality:.4f}. Revisar canal CAOS para detalles.",
        ]
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# LOADER — lee trade_resolution.json generado por el dashboard
# ─────────────────────────────────────────────────────────────────────────────
def load_trade_context_from_resolution(
    resolution_path: Path = Path("trade_resolution.json"),
    holmes_state_path: Path = Path("holmes_state.json"),
) -> Optional[TradeContext]:
    """
    Construye TradeContext desde trade_resolution.json + holmes_state.json.
    Llamado por el workflow GHA tras WIN/LOSS registrado en Dashboard.
    """
    if not resolution_path.exists():
        log.error("trade_resolution.json no encontrado: %s", resolution_path)
        return None
    try:
        with open(resolution_path, "r", encoding="utf-8") as fh:
            res = json.load(fh)
    except Exception as exc:
        log.error("Error leyendo trade_resolution.json: %s", exc)
        return None

    # Enriquecer con holmes_state si está disponible
    state: Dict[str, Any] = {}
    if holmes_state_path.exists():
        try:
            with open(holmes_state_path, "r", encoding="utf-8") as fh:
                state = json.load(fh)
        except Exception:
            pass

    asset = res.get("asset", "UNKNOWN")
    is_forex = asset.upper() in FOREX_ASSETS
    scores = state.get("scores", {})
    score_section = scores.get("forex" if is_forex else "core", {})
    mc = state.get("montecarlo", {})

    return TradeContext(
        asset=asset,
        outcome=res.get("outcome", "LOSS"),
        direction=res.get("direction", "FLAT"),
        entry_ts=res.get("entry_ts", ""),
        entry_price=float(res.get("entry_price", 0)),
        exit_price=float(res.get("exit_price", res.get("entry_price", 0))),
        gt_score=float(res.get("gt_score_at_entry",
                               score_section.get("gt_score", 0))),
        entropy_value=float(score_section.get("entropy", 0)),
        kl_div=float(scores.get("core", {}).get("kl_div", 0)),
        regime=res.get("regime_at_entry",
                       scores.get("core", {}).get("regime", "UNKNOWN")),
        gdelt_headlines=res.get("gdelt_headlines",
                                state.get("gdelt_headlines", [])),
        ohlcv_last10=state.get("ohlcv_last10", []),
        vitality=float(scores.get("core", {}).get("vitality", 0)),
        breach_probability=float(mc.get("breach_probability", 0)),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT — invocado por GHA workflow o manualmente
# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="SPEL 3.0 Narrative Engine — Holmes OS V4.0"
    )
    parser.add_argument("--resolution", default="trade_resolution.json", type=Path,
                        help="Path a trade_resolution.json generado por el dashboard")
    parser.add_argument("--state", default="holmes_state.json", type=Path,
                        help="Path a holmes_state.json del Guardian")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Modo test: genera narrativa con datos sintéticos (no requiere archivos)"
    )
    args = parser.parse_args()

    engine = NarrativeEngine()

    if args.test:
        log.info("MODO TEST — datos sintéticos")
        ctx = TradeContext(
            asset="NVDA",
            outcome="WIN",
            direction="LONG",
            entry_ts="2026-04-12T09:30:00Z",
            entry_price=875.50,
            exit_price=892.30,
            gt_score=2.14,
            entropy_value=0.0018,
            kl_div=0.087,
            regime="BULL_MOMENTUM",
            gdelt_headlines=[
                "NVIDIA announces H200 cluster expansion",
                "Fed holds rates, tech sector rallies",
                "Geopolitical tensions ease in Southeast Asia",
            ],
            vitality=1.34,
            breach_probability=0.08,
        )
    else:
        ctx = load_trade_context_from_resolution(args.resolution, args.state)
        if ctx is None:
            log.error("No se pudo construir TradeContext. Abortando.")
            return 1

    result = engine.process_trade(ctx)
    print("\n" + "═" * 60)
    print(f"NARRATIVA [{result.outcome}] {result.asset}")
    print("═" * 60)
    print(result.narrative_text)
    print("═" * 60)
    print(f"Latencia Gemini: {result.gemini_latency_ms}ms | "
          f"Telegram {result.channel_sent}: {'✓' if result.telegram_ok else '✗'}")

    return 0 if result.telegram_ok else 1


if __name__ == "__main__":
    sys.exit(main())
