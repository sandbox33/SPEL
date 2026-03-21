# spel_cost_model.py
# Fix: COST-PNL DIAG HIGH - P&L sin costos = backtest inflado
# Sesion 16 - 07 Mar 2026
#
# USO donde calcules P&L (1 linea):
#   from spel_cost_model import SPELCostModel
#   pnl_neto = SPELCostModel.pnl_neto(pnl_bruto, activo="BTC", n_trades=1)
from __future__ import annotations
from dataclasses import dataclass
import logging

# (comision_rt_pct, spread_pct, slippage_pct) todos round-trip
_COSTOS = {
    "BTC":     (0.10, 0.05, 0.05),
    "NVDA":    (0.01, 0.02, 0.02),
    "XAU":     (0.10, 0.15, 0.10),
    "NIFTY50": (0.07, 0.05, 0.05),
}

@dataclass
class CostoActivo:
    activo: str
    comision_rt_pct: float
    spread_pct: float
    slippage_pct: float

    @property
    def total_pct(self) -> float:
        return self.comision_rt_pct + self.spread_pct + self.slippage_pct


class SPELCostModel:
    @classmethod
    def get_costo(cls, activo: str) -> CostoActivo:
        if activo not in _COSTOS:
            logging.getLogger(f"SPEL.{activo}.cost_model").warning(
                f"Activo {activo!r} sin mapeo - usando BTC conservador"
            )
            activo = "BTC"
        c, s, sl = _COSTOS[activo]
        return CostoActivo(activo=activo, comision_rt_pct=c, spread_pct=s, slippage_pct=sl)

    @classmethod
    def pnl_neto(cls, pnl_bruto: float, activo: str, n_trades: int = 1) -> float:
        costo = cls.get_costo(activo)
        costos_totales = (costo.total_pct / 100.0) * n_trades
        neto = pnl_bruto - costos_totales
        logging.getLogger(f"SPEL.{activo}.cost_model").debug(
            f"bruto={pnl_bruto:.4f} costos={costos_totales:.4f} neto={neto:.4f}"
        )
        return neto

    @classmethod
    def resumen(cls, activo: str) -> dict:
        c = cls.get_costo(activo)
        return {"activo": activo, "comision_rt": c.comision_rt_pct,
                "spread": c.spread_pct, "slippage": c.slippage_pct,
                "total_pct": c.total_pct}

    @classmethod
    def breakeven_trades(cls, pnl_bruto: float, activo: str) -> int:
        pct = cls.get_costo(activo).total_pct / 100.0
        return int(pnl_bruto / pct) if pct > 0 else 999_999
