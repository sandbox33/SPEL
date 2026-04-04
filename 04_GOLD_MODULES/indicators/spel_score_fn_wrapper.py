# spel_score_fn_wrapper.py
# [BUG-J2-002 FIX] — S42 · Hinc Omnia Cerno
# Factory de score_fn(asset: str) → float compatible con SPELPaperAdapterV2
# R37: sin import torch top-level.
from __future__ import annotations
import polars as pl
from pathlib import Path
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from spel_score_engine import GoldScoreEngine


def _load_parquet(asset: str, root: Path) -> pl.DataFrame:
    """Carga parquet canónico con LazyFrame (Polars perezoso — RAM guard)."""
    candidates = [
        root / f"data_lake/{asset}/intraday/{asset}_ohlcv_v5.parquet",
        root / f"data_lake/{asset}/ohlcv/aggregated/{asset}_ohlcv_v5.parquet",
    ]
    for p in candidates:
        if p.exists():
            try:
                schema = pl.read_parquet_schema(str(p))
                overrides = {k: pl.Float32 for k, v in schema.items() if v == pl.Float64}
                return pl.scan_parquet(str(p), schema_overrides=overrides).collect()
            except Exception:
                return pl.DataFrame()
    return pl.DataFrame()


def make_score_fn(engine: object, root: Path):
    """Retorna score_fn(asset: str) -> float. Firma compatible con adapter."""
    def _score_fn(asset: str) -> float:
        try:
            df = _load_parquet(asset, root)
            result = engine.compute(asset, df)
            return result.gold_score
        except Exception as e:
            import logging
            logging.getLogger("score_fn_wrapper").error("score_fn(%s) failed: %s", asset, e)
            return 0.5
    return _score_fn
