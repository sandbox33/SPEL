"""
spel_paper_adapter_v2.py
SPEL v40 · Paper Trading Adapter · S35→S36

Cambios vs v1 (967c05b3):
  - R33: PerformanceMonitor con dual accounting ($10 sandbox / $100k canonical)
  - Alpaca crypto fix: get_crypto_bars() + BTC/USD slash format
  - RAM threshold: 0.45GB → 0.85GB
  - vault.stop() fix: threading cleanup correcto
  - EF-20 guard: gate metrics nunca sobre denominador micro-fractal

Invariante R32: ninguna escritura sobre parquets sin SHA_REGISTRY sync atómica.
Invariante R33: gate metrics EXCLUSIVAMENTE sobre CANONICAL_CAPITAL = $100,000.
"""

from __future__ import annotations

import os, sys, json, csv, hashlib, time, logging, threading
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import polars as pl

# ── Alpaca imports (fail-fast si no instalado) ──────────────────────
try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
    from alpaca.data.historical.stock import StockHistoricalDataClient
    from alpaca.data.historical.crypto import CryptoHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame
except ImportError as e:
    raise ImportError(f"pip install alpaca-py — {e}") from e

logger = logging.getLogger("spel.paper_adapter")

# ═══════════════════════════════════════════════════════════════════
# CONFIG — R33 / R30 / Dual Accounting
# ═══════════════════════════════════════════════════════════════════

SANDBOX_CAPITAL:   float = 10.0        # Capital real en cuenta paper
CANONICAL_CAPITAL: float = 100_000.0   # Denominador exclusivo para gate R30
KELLY_CAP:         float = 0.05        # Hard cap fracción Kelly (post-cap)
RAM_ABORT_GB:      float = 0.85        # Elevado de 0.45 → 0.85 (V3 stress test)
EVAL_INTERVAL_MIN: int   = 15          # Frecuencia evaluate_once()
TRADE_WINDOW_UTC   = (13, 17)          # Ventana de operación L-V

# ── Asset map — formato Alpaca v2 ───────────────────────────────────
#   equity  → StockHistoricalDataClient + get_stock_bars
#   crypto  → CryptoHistoricalDataClient + get_crypto_bars + BTC/USD format
ASSET_MAP: dict[str, dict] = {
    "BTC":    {"symbol": "BTC/USD",  "asset_class": "crypto",    "alpaca_avail": True},
    "NVDA":   {"symbol": "NVDA",     "asset_class": "us_equity", "alpaca_avail": True},
    "XAU":    {"symbol": None,       "asset_class": "futures",   "alpaca_avail": False},  # no Alpaca Paper
    "NIFTY50":{"symbol": None,       "asset_class": "index",     "alpaca_avail": False},  # no Alpaca Paper
}


# ═══════════════════════════════════════════════════════════════════
# R33 PERFORMANCE MONITOR — Dual Accounting
# ═══════════════════════════════════════════════════════════════════

@dataclass
class GateMetrics:
    """
    Todas las métricas del gate R30 en escala canónica ($100k).
    El sandbox ($10) es invisible aquí — EF-20 guard.
    """
    n_evaluations:      int   = 0
    n_trades_godel:     int   = 0      # trades donde godel_active=True
    n_godel_wins:       int   = 0      # godel trades con PnL > 0
    pnl_kelly_weighted: float = 0.0    # ∑(PnL × kelly_fraction) en escala canónica
    max_drawdown_7d:    float = 0.0    # max rolling 7d drawdown sobre equity canónica
    no_trade_rate:      float = 0.0    # fraction evaluations sin viable signal
    n_no_trade:         int   = 0
    equity_canonical:   float = CANONICAL_CAPITAL  # equity curve en $100k
    peak_equity:        float = CANONICAL_CAPITAL
    sha_mismatches:     int   = 0      # EF-19 violations detectadas


class PerformanceMonitor:
    """
    R33 invariant: todos los cálculos de gate sobre CANONICAL_CAPITAL.

    El adapter puede ejecutar trades en SANDBOX_CAPITAL ($10) para
    simular sizing real, pero las métricas se escalan al denominador
    canónico ANTES de acumular estadísticas de gate.

    Fórmula de escalado:
        pnl_canonical = pnl_sandbox × (CANONICAL_CAPITAL / SANDBOX_CAPITAL)

    Para drawdown y Kelly, el sizing teórico se recalcula sobre CANONICAL_CAPITAL.
    """

    _EF20_GUARD = True  # Flag que impide acidentalmente usar sandbox como denominador

    def __init__(self, log_path: Path, canonical_capital: float = CANONICAL_CAPITAL):
        self._gate  = GateMetrics()
        self._lock  = threading.Lock()
        self._log   = log_path
        self._equity_window: list[float] = []  # últimos 7d de equity canónica
        self._canonical = canonical_capital

        if canonical_capital == SANDBOX_CAPITAL:
            raise ValueError(
                "EF-20: canonical_capital == sandbox_capital. "
                "El denominador del gate no puede ser el micro-fractal. "
                "Usar CANONICAL_CAPITAL=100_000."
            )

    def record_evaluation(
        self,
        godel_active: bool,
        viable: bool,
        kelly_fraction: float,
        pnl_sandbox: Optional[float] = None,  # PnL real en $10 sandbox
        sha_matched: bool = True,
    ) -> GateMetrics:
        """
        Registra una evaluación del score engine.

        pnl_sandbox: PnL realizado en cuenta sandbox ($10).
                     Internamente escalado a $100k antes de acumular.
        """
        with self._lock:
            g = self._gate
            g.n_evaluations += 1

            if not sha_matched:
                g.sha_mismatches += 1
                logger.warning("EF-19: SHA mismatch detectado en evaluación")

            if not viable:
                g.n_no_trade += 1
                g.no_trade_rate = g.n_no_trade / max(g.n_evaluations, 1)
                return GateMetrics(**asdict(g))

            # ── Escalar PnL a denominador canónico (R33) ─────────────
            if pnl_sandbox is not None:
                scale = self._canonical / SANDBOX_CAPITAL  # 10_000×
                pnl_canonical = pnl_sandbox * scale

                # Acumular PnL kelly-weighted en escala canónica
                g.pnl_kelly_weighted += pnl_canonical * kelly_fraction

                # Equity curve canónica
                g.equity_canonical += pnl_canonical
                self._equity_window.append(g.equity_canonical)
                if len(self._equity_window) > 7 * 24:  # 7d a 15min ≈ 672 puntos
                    self._equity_window.pop(0)

                # Drawdown canónico (peak-to-trough sobre $100k)
                g.peak_equity = max(g.peak_equity, g.equity_canonical)
                drawdown = (g.peak_equity - g.equity_canonical) / (g.peak_equity + 1e-10)
                g.max_drawdown_7d = max(g.max_drawdown_7d, drawdown)

            # ── Gödel hit rate ────────────────────────────────────────
            if godel_active:
                g.n_trades_godel += 1
                if pnl_sandbox is not None and pnl_sandbox > 0:
                    g.n_godel_wins += 1

            g.no_trade_rate = g.n_no_trade / max(g.n_evaluations, 1)

            self._persist()
            return GateMetrics(**asdict(g))

    def gate_status(self) -> dict:
        """Evalúa las 7 condiciones R30 en escala canónica."""
        with self._lock:
            g = self._gate
            hit_rate = (g.n_godel_wins / max(g.n_trades_godel, 1))
            return {
                "hit_rate_godel":      round(hit_rate, 4),
                "hit_rate_pass":       hit_rate > 0.56 and g.n_trades_godel >= 30,
                "max_drawdown_7d":     round(g.max_drawdown_7d, 4),
                "drawdown_pass":       g.max_drawdown_7d < 0.08,
                "no_trade_rate":       round(g.no_trade_rate, 4),
                "no_trade_pass":       0.30 <= g.no_trade_rate <= 0.70,
                "pnl_kelly_weighted":  round(g.pnl_kelly_weighted, 2),
                "pnl_pass":            g.pnl_kelly_weighted > 0,
                "sha_mismatches":      g.sha_mismatches,
                "sha_pass":            g.sha_mismatches == 0,
                "n_evaluations":       g.n_evaluations,
                "equity_canonical":    round(g.equity_canonical, 2),
                "canonical_capital":   self._canonical,
                "sandbox_capital":     SANDBOX_CAPITAL,
                "scale_factor":        self._canonical / SANDBOX_CAPITAL,
                "ef20_guard":          self._EF20_GUARD,
            }

    def _persist(self):
        """Persist gate metrics a JSON (no parquet — no R32 aquí)."""
        try:
            data = asdict(self._gate)
            data["gate_status"]  = self.gate_status()
            data["updated_utc"]  = datetime.now(timezone.utc).isoformat()
            self._log.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error(f"PerformanceMonitor._persist failed: {e}")


# ═══════════════════════════════════════════════════════════════════
# ALPACA CLIENT FACTORY — crypto vs equity routing
# ═══════════════════════════════════════════════════════════════════

class AlpacaClientFactory:
    """
    Instancia los clientes correctos por asset_class.
    Fail-fast si faltan credenciales (EF-19 compliance).
    """

    def __init__(self, api_key: str, api_secret: str, paper: bool = True):
        if not api_key or not api_secret:
            raise RuntimeError("EF-19: ALPACA_KEY / ALPACA_SECRET missing — abort")

        self.trading = TradingClient(api_key, api_secret, paper=paper)
        self._stock  = StockHistoricalDataClient(api_key, api_secret)
        self._crypto = CryptoHistoricalDataClient(api_key, api_secret)

    def get_bars(
        self,
        asset: str,
        symbol: str,
        asset_class: str,
        timeframe: TimeFrame,
        start: datetime,
        end: Optional[datetime] = None,
    ) -> Optional[pl.DataFrame]:
        """
        Routing correcto por asset_class.
        - equity  → StockHistoricalDataClient
        - crypto  → CryptoHistoricalDataClient + BTC/USD slash format
        Errores son aislados por activo — un fallo de ticker no mata el pipeline.
        """
        end = end or datetime.now(timezone.utc)

        try:
            if asset_class == "crypto":
                # Fix ALPACA_CRYPTO_400: get_crypto_bars + slash format
                req  = CryptoBarsRequest(symbol_or_symbols=symbol,
                                         timeframe=timeframe,
                                         start=start, end=end)
                bars = self._crypto.get_crypto_bars(req)
                if bars and symbol in bars.df.index.get_level_values(0):
                    raw = bars.df.loc[symbol].reset_index()
                else:
                    return None

            elif asset_class == "us_equity":
                req  = StockBarsRequest(symbol_or_symbols=symbol,
                                        timeframe=timeframe,
                                        start=start, end=end)
                bars = self._stock.get_stock_bars(req)
                if bars and symbol in bars.df.index.get_level_values(0):
                    raw = bars.df.loc[symbol].reset_index()
                else:
                    return None

            else:
                logger.info(f"  {asset}: {asset_class} no disponible en Alpaca Paper — skip")
                return None

            # Convertir a Polars — sin pandas en el path caliente
            return pl.from_pandas(raw).rename({
                "timestamp": "date",
                "open": "open", "high": "high", "low": "low",
                "close": "close", "volume": "volume",
            }).with_columns(
                pl.col("close").cast(pl.Float32),
                pl.col("high").cast(pl.Float32),
                pl.col("low").cast(pl.Float32),
            )

        except Exception as e:
            # Aislamiento por activo — log y continuar con otros
            logger.error(f"  {asset} ({symbol}) bars fetch failed: {e}")
            return None

    def get_account(self) -> dict:
        acct = self.trading.get_account()
        return {
            "equity":         float(acct.equity),
            "cash":           float(acct.cash),
            "buying_power":   float(acct.buying_power),
            "status":         str(acct.status),
        }


# ═══════════════════════════════════════════════════════════════════
# TRADE RECORD — 22 campos
# ═══════════════════════════════════════════════════════════════════

@dataclass
class TradeRecord:
    timestamp_utc:   str
    asset:           str
    direction:       str
    score_oro:       int
    godel_active:    bool
    regime_label:    str
    hurst:           float
    entropy:         float
    p90_entropy:     float
    kelly_fraction:  float         # sobre CANONICAL_CAPITAL
    entry_price:     float
    stop_loss:       float
    take_profit:     float
    atr14:           float
    viable:          bool
    sha_parquet:     str
    sandbox_capital: float = SANDBOX_CAPITAL
    canonical_capital: float = CANONICAL_CAPITAL
    pnl_sandbox:     Optional[float] = None
    pnl_canonical:   Optional[float] = None   # pnl_sandbox × scale
    alpaca_order_id: Optional[str]   = None
    notes:           str = ""

    def to_csv_row(self) -> dict:
        d = asdict(self)
        return d


# ═══════════════════════════════════════════════════════════════════
# RAM GUARD
# ═══════════════════════════════════════════════════════════════════

def _ram_gb() -> float:
    try:
        import psutil
        return psutil.virtual_memory().used / 1e9
    except Exception:
        return 0.0

def _ram_guard(threshold_gb: float = RAM_ABORT_GB):
    used = _ram_gb()
    if used > threshold_gb:
        raise MemoryError(
            f"RAM abort: {used:.2f}GB > {threshold_gb}GB threshold. "
            f"Elevar RAM_ABORT_GB o liberar memoria antes de continuar."
        )
    return used


# ═══════════════════════════════════════════════════════════════════
# SPEL PAPER ADAPTER v2
# ═══════════════════════════════════════════════════════════════════

class SPELPaperAdapterV2:
    """
    Paper trading adapter con:
      - R33 dual accounting (sandbox $10 / canonical $100k)
      - Alpaca crypto fix (BTC/USD + get_crypto_bars)
      - RAM guard elevado a 0.85GB
      - Fail-fast credentials
      - vault.stop() threading cleanup correcto
    """

    def __init__(
        self,
        root:        Path,
        api_key:     str,
        api_secret:  str,
        score_fn,          # callable(asset) → ScoreResult
    ):
        self.root      = root
        self.score_fn  = score_fn
        self._alpaca   = AlpacaClientFactory(api_key, api_secret, paper=True)
        self._stop_evt = threading.Event()
        self._thread:  Optional[threading.Thread] = None

        # Paths
        log_dir = root / "logs"
        log_dir.mkdir(exist_ok=True)
        self._trade_log   = log_dir / "trade_log.csv"
        self._active_json = log_dir / "active_trades.json"
        self._gate_json   = log_dir / "gate_metrics.json"
        self._summary_json= root / "meta" / "spel_v40_execution_summary.json"

        self._monitor = PerformanceMonitor(
            log_path=self._gate_json,
            canonical_capital=CANONICAL_CAPITAL,
        )

        self._fieldnames = list(TradeRecord.__dataclass_fields__.keys())
        if not self._trade_log.exists():
            with open(self._trade_log, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=self._fieldnames).writeheader()

        logger.info(
            f"SPELPaperAdapterV2 init | sandbox=${SANDBOX_CAPITAL} "
            f"canonical=${CANONICAL_CAPITAL:,.0f} | RAM abort={RAM_ABORT_GB}GB"
        )

    # ── Public API ─────────────────────────────────────────────────

    def start(self, interval_min: int = EVAL_INTERVAL_MIN):
        """Arranca el loop de evaluación en background thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("Adapter ya corriendo — ignorando start()")
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._loop, args=(interval_min,), daemon=True,
            name="spel-paper-adapter"
        )
        self._thread.start()
        logger.info(f"Paper adapter started — interval={interval_min}min")

    def stop(self, timeout: float = 10.0):
        """
        Fix vault.stop(*args, **kwargs): shutdown limpio del thread.
        No acepta *args/**kwargs adicionales — interfaz explícita.
        """
        self._stop_evt.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("Paper adapter thread no terminó en el timeout")
        logger.info("Paper adapter stopped")

    def evaluate_once(self) -> list[TradeRecord]:
        """
        Evalúa todos los activos disponibles en Alpaca una vez.
        Registra en trade_log.csv y actualiza gate metrics (R33).
        """
        now_utc = datetime.now(timezone.utc)
        records = []

        # Verificar ventana de trading
        if now_utc.weekday() >= 5:  # fin de semana
            logger.info("Fin de semana — skip evaluación")
            return []
        if not (TRADE_WINDOW_UTC[0] <= now_utc.hour < TRADE_WINDOW_UTC[1]):
            logger.info(f"Fuera de ventana {TRADE_WINDOW_UTC} UTC — skip")
            return []

        ram_used = _ram_guard()
        logger.debug(f"RAM: {ram_used:.2f}GB / {RAM_ABORT_GB}GB")

        for asset, cfg in ASSET_MAP.items():
            if not cfg["alpaca_avail"]:
                continue

            try:
                score = self.score_fn(asset)
            except Exception as e:
                logger.error(f"score_fn({asset}) failed: {e}")
                continue

            # EF-19: verificar SHA antes de proceder
            sha_ok = self._verify_sha(asset)
            if not sha_ok:
                logger.error(f"EF-19: SHA mismatch {asset} — skip trade")

            rec = TradeRecord(
                timestamp_utc   = now_utc.isoformat(),
                asset           = asset,
                direction       = score.direction,
                score_oro       = score.score_oro,
                godel_active    = score.godel_active,
                regime_label    = getattr(score, "regime_label", "UNKNOWN"),
                hurst           = getattr(score, "hurst", 0.0),
                entropy         = getattr(score.backbone, "entropy", 0.0)
                                  if hasattr(score, "backbone") else 0.0,
                p90_entropy     = getattr(score, "p90", 0.0),
                kelly_fraction  = score.kelly_fraction,
                entry_price     = score.backbone.levels.entry_price
                                  if (hasattr(score, "backbone") and
                                      score.backbone.levels) else 0.0,
                stop_loss       = score.backbone.levels.stop_loss
                                  if (hasattr(score, "backbone") and
                                      score.backbone.levels) else 0.0,
                take_profit     = score.backbone.levels.take_profit
                                  if (hasattr(score, "backbone") and
                                      score.backbone.levels) else 0.0,
                atr14           = score.backbone.levels.atr14
                                  if (hasattr(score, "backbone") and
                                      score.backbone.levels) else 0.0,
                viable          = score.viable,
                sha_parquet     = score.sha_parquet,
            )

            if score.viable and sha_ok:
                order_id = self._execute_paper_order(asset, cfg, score)
                rec.alpaca_order_id = order_id

            # R33: gate metrics en escala canónica
            self._monitor.record_evaluation(
                godel_active   = score.godel_active,
                viable         = score.viable,
                kelly_fraction = score.kelly_fraction,
                pnl_sandbox    = None,  # PnL se registra en close, no en open
                sha_matched    = sha_ok,
            )

            records.append(rec)
            self._append_trade_log(rec)

        self._update_summary(records)
        return records

    # ── Private ────────────────────────────────────────────────────

    def _loop(self, interval_min: int):
        while not self._stop_evt.is_set():
            try:
                records = self.evaluate_once()
                logger.info(f"Cycle done — {len(records)} assets evaluated")
            except MemoryError as e:
                logger.critical(str(e))
                self._stop_evt.set()
                break
            except Exception as e:
                logger.error(f"evaluate_once() unhandled: {e}", exc_info=True)

            # Sleep interruptible
            self._stop_evt.wait(timeout=interval_min * 60)

    def _verify_sha(self, asset: str) -> bool:
        try:
            reg_path = self.root / "meta" / "SHA_REGISTRY.json"
            reg = json.loads(reg_path.read_text())
            sha_expected = reg.get(asset, {}).get("sha_v5", "")
            pq = self.root / f"data_lake/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet"
            if not pq.exists():
                return False
            sha_actual = hashlib.sha256(pq.read_bytes()).hexdigest()[:12]
            return sha_actual == sha_expected
        except Exception:
            return False

    def _execute_paper_order(self, asset: str, cfg: dict, score) -> Optional[str]:
        """
        Envía orden paper a Alpaca. Sizing calculado sobre SANDBOX_CAPITAL.
        R33: el tamaño de la orden es micro-fractal — la métrica de gate es canónica.
        """
        try:
            side = OrderSide.BUY if score.direction == "LONG" else OrderSide.SELL
            # Notional sobre sandbox $10 (no canonical)
            notional = round(SANDBOX_CAPITAL * score.kelly_fraction, 2)
            notional = max(notional, 1.0)  # mínimo $1

            req = MarketOrderRequest(
                symbol     = cfg["symbol"],
                notional   = notional,
                side       = side,
                time_in_force = TimeInForce.DAY,
            )
            order = self._alpaca.trading.submit_order(req)
            logger.info(
                f"  Paper order {asset}: {side} notional=${notional} "
                f"order_id={order.id}"
            )
            return str(order.id)
        except Exception as e:
            logger.error(f"  Order {asset} failed: {e}")
            return None

    def _append_trade_log(self, rec: TradeRecord):
        with open(self._trade_log, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=self._fieldnames).writerow(rec.to_csv_row())

    def _update_summary(self, records: list[TradeRecord]):
        """Genera spel_v40_execution_summary.json para Dashboard Panel 6."""
        gate = self._monitor.gate_status()
        summary = {
            "updated_utc":   datetime.now(timezone.utc).isoformat(),
            "day_counter":   self._compute_day(),
            "gate_metrics":  gate,
            "last_cycle":    [asdict(r) for r in records],
            "sandbox":       {"capital": SANDBOX_CAPITAL, "note": "micro-fractal — EF-20 guard"},
            "canonical":     {"capital": CANONICAL_CAPITAL, "note": "denominador gate R30/R33"},
            "alpaca_account": self._safe_account(),
        }
        self._summary_json.parent.mkdir(exist_ok=True)
        self._summary_json.write_text(json.dumps(summary, indent=2, default=str))

    def _compute_day(self) -> int:
        start = datetime(2026, 3, 19, 2, 2, tzinfo=timezone.utc)  # Día 1/63
        return (datetime.now(timezone.utc) - start).days + 1

    def _safe_account(self) -> dict:
        try:
            return self._alpaca.get_account()
        except Exception as e:
            return {"error": str(e)}
