"""Holmes/modules/detective/base_detective.py
Abstract base for all Holmes detectives.
[FIX S41]: Circular import eliminado. Anomaly se importa en runtime, no a nivel de módulo.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Solo para type hints — cero import en runtime (evita circular)
    from kernel.holmes import Anomaly


class BaseDetective(ABC):
    """Abstract base for all Holmes detectives. scan() → list[Anomaly]."""

    def __init__(self, root: Path):
        self.root = root

    @abstractmethod
    def scan(self) -> list:
        """Execute all checks. Non-blocking. Thread-safe. Returns list[Anomaly]."""
        ...

    def _read_json(self, path: Path) -> dict | list | None:
        """Utility: read JSON file safely. Returns None on any error."""
        try:
            return __import__("json").loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
