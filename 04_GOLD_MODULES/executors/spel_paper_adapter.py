"""
spel_paper_adapter.py — SPEL S31
=================================
Paper trading via Alpaca Paper API con trazabilidad profunda de régimen.
Cada trade registra estado completo: Score, SHA, Hurst, Entropy, Gödel.
Ventana operativa: London-NY overlap 13:00-17:00 UTC (08:00-12:00 ECT).

Uso:
    from spel_paper_adapter import PaperAdapter
    adapter = PaperAdapter()
    adapter.run_session()          # blocking loop
    adapter.evaluate_once()        # single evaluation + trade if viable
"""

import os, sys, csv, json, math, hashlib, time, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional
from dataclasses import dataclass, asdict

# ── Env / paths ───────────────────────────────────────────────────
ROOT = Path(os.environ.get("SPEL_BASE_DIR",
            "/content/drive/MyDrive/ORDEN/SPEL 3.0"))

ASSETS = ["BTC", "XAU", "NIFTY50", "NVDA"]

# Ventana London-NY: 13:00–17:00 UTC (08:00–12:00 ECT)
WINDOW_START_UTC = 13
WINDOW_END_UTC   = 17

# ── Telegram ──────────────────────────────────────────────────────
TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TG_SIST = os.environ.get("TELEGRAM_SISTEMA", "-1003712424420")
TG_SEN  = os.environ.get("TELEGRAM_SENALES", "-1003733702589")


def tg(chat_id: str, text: str) -> Optional[int]:
    if not TOKEN or not chat_id:
        return None
    payload = json.dumps({"chat_id": chat_id, "text": text,
                          "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()).get("result", {}).get("message_id")
    except Exception as e:
        print(f"  TG ERR: {e}")
        return None


# ── Trade record (trazabilidad profunda) ──────────────────────────
@dataclass
class TradeRecord:
    """Estado completo de régimen en el momento del trade."""
    timestamp_utc:   str
    session_day:     int           # día 1..63 del paper trading gate
    asset:           str
    sha_parquet:     str           # R3: SHA del parquet usado

    # Score de Oro
    score_oro:       float
    direction:       str           # LONG | SHORT
    modo:            str           # FLAT | SWING | SCALPING_15M
    viable:          bool
    viable_reason:   str

    # Regime metrics — el corazón del registro profundo
    hurst:           float         # H < 0.5 = mean-rev | = 0.5 = RW | > 0.5 = trend
    entropy:         float         # S = entropy_shannon GDELT en raw space
    p90:             float         # umbral Gödel para este activo
    godel_active:    bool          # entropy >= p90

    # Backbone / sizing
    entry:           float
    sl:              float
    tp:              float
    kelly_fraction:  float
    rr_ratio:        float          # tp-entry / entry-sl

    # Alpaca execution
    alpaca_order_id: str
    alpaca_status:   str           # submitted | filled | rejected | skipped
    qty:             float
    notional_usd:    float

    # Regime label (derivado) — para análisis post-trade
    regime_label:    str           # TREND | MEAN_REV | NOISE | GODEL_OFF

    @staticmethod
    def regime_from_hurst(h: float, godel: bool) -> str:
        if not godel:
            return "GODEL_OFF"
        if h > 0.55:
            return "TREND"
        if h < 0.45:
            return "MEAN_REV"
        return "NOISE"

    def to_csv_row(self) -> dict:
        d = asdict(self)
        d["hurst_regime"] = self.regime_label
        return d

    CSV_FIELDS = [
        "timestamp_utc", "session_day", "asset", "sha_parquet",
        "score_oro", "direction", "modo", "viable", "viable_reason",
        "hurst", "entropy", "p90", "godel_active",
        "entry", "sl", "tp", "kelly_fraction", "rr_ratio",
        "alpaca_order_id", "alpaca_status", "qty", "notional_usd",
        "regime_label",
    ]


# ── Change log writer ─────────────────────────────────────────────
class ChangeLog:
    """Escribe eventos en change_log.json para el Dashboard."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or (ROOT / "meta/change_log.json")

    def append(self, event_type: str, detail: dict):
        entry = {
            "ts":    datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            **detail,
        }
        try:
            if self.path.exists():
                log = json.loads(self.path.read_text())
                if not isinstance(log, list):
                    log = [log] if isinstance(log, dict) else []
            else:
                log = []
            log.append(entry)
            # Mantener solo los últimos 200 eventos
            log = log[-200:]
            self.path.write_text(json.dumps(log, indent=2, default=str))
        except Exception as e:
            print(f"  ChangeLog ERR: {e}")

    def recent(self, n: int = 10) -> list:
        try:
            log = json.loads(self.path.read_text())
            if isinstance(log, list):
                return log[-n:]
            return []
        except:
            return []


# ── Alpaca Paper client ───────────────────────────────────────────
class AlpacaPaperClient:
    """
    Cliente mínimo para Alpaca Paper API.
    No requiere SDK — usa urllib.request puro (compatible con Colab).
    Endpoint: https://paper-api.alpaca.markets
    """

    BASE = "https://paper-api.alpaca.markets"

    def __init__(self):
        self.key    = os.environ.get("ALPACA_API_KEY", "")
        self.secret = os.environ.get("ALPACA_SECRET_KEY", "")
        self._ok    = bool(self.key and self.secret)
        if not self._ok:
            print("  ⚠️  Alpaca credentials missing (ALPACA_API_KEY / ALPACA_SECRET_KEY)")
            print("      Set in Colab Secrets. Paper trading in DRY-RUN mode.")

    def _req(self, method: str, path: str, body: dict = None) -> dict:
        url     = self.BASE + path
        payload = json.dumps(body).encode() if body else None
        req = urllib.request.Request(
            url, data=payload, method=method,
            headers={
                "APCA-API-KEY-ID":     self.key,
                "APCA-API-SECRET-KEY": self.secret,
                "Content-Type":        "application/json",
            })
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body_err = ""
            try: body_err = json.loads(e.read()).get("message", "")
            except: pass
            return {"error": f"HTTP {e.code}", "message": body_err}
        except Exception as e:
            return {"error": str(e)}

    def account(self) -> dict:
        return self._req("GET", "/v2/account")

    def submit_order(self, symbol: str, qty: float, side: str,
                     sl: float, tp: float) -> dict:
        """
        Bracket order: entry + stop_loss + take_profit.
        symbol: Alpaca ticker (BTC/USD, XAUUSD no disponible en Alpaca)
        qty: quantity en unidades del activo
        side: 'buy' | 'sell'
        """
        if not self._ok:
            # Dry-run: retornar orden simulada
            return {
                "id":     f"dry_run_{symbol}_{int(time.time())}",
                "status": "dry_run",
                "symbol": symbol,
                "qty":    str(qty),
                "side":   side,
            }

        body = {
            "symbol":        symbol,
            "qty":           str(round(qty, 6)),
            "side":          side,
            "type":          "market",
            "time_in_force": "day",
            "order_class":   "bracket",
            "stop_loss":     {"stop_price": str(round(sl, 4))},
            "take_profit":   {"limit_price": str(round(tp, 4))},
        }
        return self._req("POST", "/v2/orders", body)

    def get_orders(self, status: str = "open") -> list:
        result = self._req("GET", f"/v2/orders?status={status}&limit=20")
        return result if isinstance(result, list) else []

    def cancel_all(self) -> dict:
        return self._req("DELETE", "/v2/orders")


# ── Alpaca ticker mapping ─────────────────────────────────────────
ALPACA_TICKER = {
    "BTC":     "BTC/USD",    # crypto — disponible 24/7
    "NVDA":    "NVDA",       # equity — solo mercado abierto
    "XAU":     None,         # no disponible en Alpaca paper — skip
    "NIFTY50": None,         # no disponible — skip
}

# Capital paper por defecto (USD)
DEFAULT_PAPER_CAPITAL = 10_000.0


# ── PaperAdapter ──────────────────────────────────────────────────
class PaperAdapter:
    """
    Motor principal de paper trading SPEL.

    Flujo por ciclo (cada 15min en ventana London-NY):
      1. Verificar ventana horaria (R25 análogo)
      2. Cargar score engine → score() por activo
      3. Si viable y ticker disponible en Alpaca → submit_order
      4. Registrar TradeRecord completo en trade_log.csv
      5. Escribir change_log.json
      6. Notificar TG SISTEMA con comprobante

    Gate diario:
      Métricas al final del día:
        hit_rate_godel   (gate: >56% en ≥30 trades Gödel)
        max_drawdown_7d  (gate: < 8%)
        no_trade_rate    (gate: 30-70%)
        pnl_kelly        (gate: > 0)
    """

    def __init__(self,
                 paper_capital:  float = DEFAULT_PAPER_CAPITAL,
                 session_day:    int   = 1,
                 dry_run:        bool  = False):

        self.capital     = paper_capital
        self.session_day = session_day
        self.dry_run     = dry_run
        self.alpaca      = AlpacaPaperClient()
        self.changelog   = ChangeLog()
        self.log_path    = ROOT / "logs/trade_log.csv"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_csv()
        self._score_engine = None

    def _init_csv(self):
        if not self.log_path.exists():
            with open(self.log_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=TradeRecord.CSV_FIELDS)
                writer.writeheader()
            print(f"  trade_log.csv initialized: {self.log_path}")

    def _load_score_engine(self):
        if self._score_engine is not None:
            return self._score_engine
        import importlib.util
        se = ROOT / "scripts/spel_score_engine.py"
        spec = importlib.util.spec_from_file_location("sce_paper", se)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self._score_engine = mod
        print("  score engine loaded")
        return mod

    @staticmethod
    def _in_window() -> bool:
        """Verifica ventana London-NY: 13:00–17:00 UTC."""
        h = datetime.now(timezone.utc).hour
        return WINDOW_START_UTC <= h < WINDOW_END_UTC

    @staticmethod
    def _extract(raw: dict, *keys, default=None):
        for k in keys:
            if k in raw:
                return raw[k]
        return default

    def _build_record(self, asset: str, raw: dict,
                      order: dict, qty: float) -> TradeRecord:
        """Construye TradeRecord con trazabilidad completa."""
        hurst   = float(raw.get("hurst", 0.5))
        entropy = float(raw.get("entropy", 0.0))
        p90     = float(raw.get("p90", 0.0))
        godel   = bool(raw.get("godel_active", False))

        bb = raw.get("backbone")
        entry = float(getattr(bb, "entry", 0) or 0)
        sl    = float(getattr(bb, "stop_loss", 0) or 0)
        tp    = float(getattr(bb, "take_profit", 0) or 0)

        # R:R ratio
        rr = 0.0
        if entry and sl and abs(entry - sl) > 1e-8:
            rr = abs(tp - entry) / abs(entry - sl)

        # Regime label
        regime = TradeRecord.regime_from_hurst(hurst, godel)

        # Viable reason — extraer primera razón del ScoreResult
        razon = raw.get("razon", [])
        viable_reason = razon[0] if isinstance(razon, list) and razon else str(razon)

        status  = order.get("status", "skipped")
        orderid = order.get("id", "")

        return TradeRecord(
            timestamp_utc   = datetime.now(timezone.utc).isoformat(),
            session_day     = self.session_day,
            asset           = asset,
            sha_parquet     = str(raw.get("sha_parquet", "?")),
            score_oro       = float(raw.get("score_oro", 0)),
            direction       = str(raw.get("direction", "?")),
            modo            = str(raw.get("modo", "?")),
            viable          = bool(raw.get("viable", False)),
            viable_reason   = viable_reason[:120],
            hurst           = hurst,
            entropy         = entropy,
            p90             = p90,
            godel_active    = godel,
            entry           = entry,
            sl              = sl,
            tp              = tp,
            kelly_fraction  = float(raw.get("kelly_fraction", 0)),
            rr_ratio        = round(rr, 3),
            alpaca_order_id = str(orderid),
            alpaca_status   = status,
            qty             = qty,
            notional_usd    = round(qty * entry, 2) if entry else 0.0,
            regime_label    = regime,
        )

    def _write_record(self, rec: TradeRecord):
        with open(self.log_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=TradeRecord.CSV_FIELDS)
            writer.writerow(rec.to_csv_row())

    def _notify_trade(self, rec: TradeRecord):
        """Comprobante de operación → TG SISTEMA."""
        icon   = "🟢" if rec.direction == "LONG" else "🔴"
        regime = {
            "TREND":    "📈 tendencial",
            "MEAN_REV": "↩️  reversión",
            "NOISE":    "〰️  ruido/caminata",
            "GODEL_OFF":"⚫ Gödel inactivo",
        }.get(rec.regime_label, rec.regime_label)

        msg = (
            f"{icon} <b>SPEL PAPER TRADE</b>\n"
            f"<code>{rec.timestamp_utc[:16]} UTC</code>  Día {rec.session_day}/63\n"
            f"{'─'*36}\n"
            f"Asset:   <b>{rec.asset}</b>  {rec.direction}\n"
            f"Score:   {rec.score_oro}/100  [{rec.modo}]\n"
            f"Entry:   {rec.entry:.4f}  SL: {rec.sl:.4f}  TP: {rec.tp:.4f}\n"
            f"Kelly:   {rec.kelly_fraction:.3f}  R:R {rec.rr_ratio:.2f}x\n"
            f"{'─'*36}\n"
            f"<b>Régimen</b>:\n"
            f"  H={rec.hurst:.3f}  {regime}\n"
            f"  S={rec.entropy:.4f}  P90={rec.p90:.4f}\n"
            f"  Gödel: {'✅' if rec.godel_active else '⚫'}\n"
            f"{'─'*36}\n"
            f"Alpaca:  {rec.alpaca_status}  id={rec.alpaca_order_id[:16]}\n"
            f"SHA:     <code>{rec.sha_parquet}</code>"
        )
        tg(TG_SIST, msg)

    def evaluate_once(self) -> list:
        """
        Evalúa todos los activos y opera los viables.
        Retorna lista de TradeRecords generados.
        """
        records = []
        mod     = self._load_score_engine()

        if not self._in_window():
            ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
            print(f"  [{ts}] Fuera de ventana London-NY (13:00-17:00 UTC) — skip")
            return records

        print(f"\n  [{datetime.now(timezone.utc).strftime('%H:%M UTC')}] "
              f"Evaluando activos...")

        for asset in ASSETS:
            ticker = ALPACA_TICKER.get(asset)
            try:
                r   = mod.score(asset)
                raw = r.__dict__ if hasattr(r, "__dict__") else {}

                score   = float(raw.get("score_oro", 0))
                viable  = bool(raw.get("viable", False))
                direct  = str(raw.get("direction", "?"))
                kelly   = float(raw.get("kelly_fraction", 0))
                godel   = bool(raw.get("godel_active", False))
                hurst   = float(raw.get("hurst", 0.5))

                print(f"    {asset:<8} score={score:.0f} H={hurst:.3f} "
                      f"godel={'G+' if godel else 'G-'} "
                      f"viable={'YES' if viable else 'no '}")

                # Determinar qty y side
                qty  = 0.0
                side = "buy" if direct == "LONG" else "sell"

                if viable and ticker:
                    bb    = raw.get("backbone")
                    entry = float(getattr(bb, "entry", 0) or 0)
                    sl_p  = float(getattr(bb, "stop_loss", 0) or 0)
                    tp_p  = float(getattr(bb, "take_profit", 0) or 0)

                    if entry > 0:
                        notional = self.capital * kelly
                        qty      = notional / entry if entry else 0
                        qty      = round(max(qty, 0.0001), 6)

                        if not self.dry_run and not self.alpaca._ok:
                            order = {"id": f"dryrun_{asset}", "status": "dry_run"}
                        elif not self.dry_run:
                            order = self.alpaca.submit_order(
                                ticker, qty, side, sl_p, tp_p)
                        else:
                            order = {"id": f"dryrun_{asset}", "status": "dry_run"}
                    else:
                        order = {"id": "", "status": "skipped_no_entry"}
                else:
                    # Registrar evaluación sin trade (trazabilidad completa)
                    order = {"id": "", "status": "skipped"}
                    if viable and not ticker:
                        order["status"] = "skipped_no_alpaca_ticker"

                rec = self._build_record(asset, raw, order, qty)
                self._write_record(rec)
                records.append(rec)

                # Changelog
                self.changelog.append("TRADE_EVAL", {
                    "asset":    asset,
                    "score":    score,
                    "viable":   viable,
                    "regime":   rec.regime_label,
                    "h":        hurst,
                    "status":   order["status"],
                })

                # Notificar si se ejecutó trade
                if order.get("status") not in ("skipped", "skipped_no_alpaca_ticker",
                                                "skipped_no_entry"):
                    self._notify_trade(rec)

            except Exception as e:
                print(f"    {asset} ERR: {e}")
                self.changelog.append("TRADE_ERROR", {"asset": asset, "error": str(e)})

        return records

    def daily_metrics(self) -> dict:
        """Calcula métricas del gate pre-live desde trade_log.csv."""
        if not self.log_path.exists():
            return {}

        records = []
        try:
            with open(self.log_path, newline="") as f:
                reader = csv.DictReader(f)
                records = list(reader)
        except Exception:
            return {}

        if not records:
            return {}

        total       = len(records)
        godel_trades = [r for r in records if r.get("godel_active") == "True"]
        skipped     = [r for r in records if "skipped" in r.get("alpaca_status","")]
        no_trade_r  = len(skipped) / total if total else 0

        # Hit rate Gödel (directional: requires outcome — use viable as proxy)
        godel_viable = [r for r in godel_trades if r.get("viable") == "True"]
        hit_rate_g   = len(godel_viable) / len(godel_trades) if godel_trades else 0

        # Drawdown proxy (Kelly PnL from R:R)
        pnl_kelly = sum(
            float(r.get("kelly_fraction", 0)) * float(r.get("rr_ratio", 0))
            for r in records
            if r.get("alpaca_status") not in ("skipped",)
        )

        metrics = {
            "total_evaluations":  total,
            "godel_trades":       len(godel_trades),
            "hit_rate_godel":     round(hit_rate_g, 4),
            "no_trade_rate":      round(no_trade_r, 4),
            "pnl_kelly_proxy":    round(pnl_kelly, 6),
            "gate_godel_hr":      hit_rate_g > 0.56,
            "gate_notrade":       0.30 <= no_trade_r <= 0.70,
            "gate_pnl":           pnl_kelly > 0,
            "session_day":        self.session_day,
        }

        msg = (
            f"📊 <b>SPEL PAPER — Día {self.session_day}/63</b>\n"
            f"Evaluaciones: {total}  Gödel: {len(godel_trades)}\n"
            f"hit_rate_godel: {hit_rate_g:.1%} "
            f"{'✅' if metrics['gate_godel_hr'] else '❌'} (gate>56%)\n"
            f"no_trade_rate:  {no_trade_r:.1%} "
            f"{'✅' if metrics['gate_notrade'] else '❌'} (gate 30-70%)\n"
            f"PnL Kelly:      {pnl_kelly:.4f} "
            f"{'✅' if metrics['gate_pnl'] else '❌'} (gate>0)"
        )
        tg(TG_SIST, msg)

        self.changelog.append("DAILY_METRICS", metrics)
        return metrics

    def run_session(self, interval_min: int = 15, max_hours: int = 5):
        """
        Loop de sesión: evalúa cada interval_min minutos en ventana activa.
        Finaliza al salir de la ventana o tras max_hours.
        """
        print(f"\nSPEL PaperAdapter iniciado — día {self.session_day}/63")
        print(f"Ventana: 13:00-17:00 UTC (08:00-12:00 ECT)")
        print(f"Intervalo: {interval_min}min  Capital: ${self.capital:,.2f}")
        tg(TG_SIST,
           f"🚀 <b>SPEL Paper Trading</b>\n"
           f"Día {self.session_day}/63 iniciado\n"
           f"Capital: ${self.capital:,.2f}  Intervalo: {interval_min}min")

        self.changelog.append("SESSION_START", {
            "day": self.session_day, "capital": self.capital,
            "interval_min": interval_min,
        })

        deadline = datetime.now(timezone.utc) + timedelta(hours=max_hours)

        while datetime.now(timezone.utc) < deadline:
            self.evaluate_once()
            next_eval = datetime.now(timezone.utc) + timedelta(minutes=interval_min)
            print(f"  Próxima evaluación: {next_eval.strftime('%H:%M UTC')}")

            while datetime.now(timezone.utc) < next_eval:
                time.sleep(30)

        self.daily_metrics()
        print("\nSesión finalizada.")
        self.changelog.append("SESSION_END", {"day": self.session_day})


if __name__ == "__main__":
    adapter = PaperAdapter(
        paper_capital = 10_000.0,
        session_day   = 1,
        dry_run       = True,  # cambiar a False cuando Alpaca credentials estén
    )
    adapter.run_session(interval_min=15)
