"""
governance/persistence.py
==========================
4 streams de persistencia (Decisión #14, confirmada en sesión anterior).
Este módulo SOLO declara rutas y metadatos -- no hace I/O real. La
lógica de escritura/lectura de cada stream se agrega cuando el
consumidor real la necesite (Fase 2+), siguiendo el mismo principio que
`governance/secrets.py`: un solo lugar declara la estructura, nadie más
hardcodea rutas por su cuenta.

LOS 4 STREAMS:
  METRICS       -> Drive. Series de tiempo, resultados de backtests,
                   snapshots de entrenamiento. NO versionado en git
                   (cambia todo el tiempo, no es código).
  CONFIG        -> GitHub (YAML, repo-relativo). Thresholds, pesos,
                   parámetros -- versionado, con historial de git,
                   revisable en PRs.
  MODELS        -> Drive. Checkpoints, pesos entrenados (LSTM backbone
                   cuando exista). Binarios grandes, no van a git.
  DECISION_LOG  -> GitHub (decision-log.md, repo-relativo). Auditoría
                   de decisiones de arquitectura -- versionado a
                   propósito, para que el historial de decisiones sea
                   tan revisable como el código mismo.

Por qué Drive vs GitHub y no todo en un solo lugar: git no está
pensado para binarios grandes ni datos que cambian a cada corrida
(bloatea el repo, historial inútil). Drive no tiene control de
versiones real ni revisión por PR. Cada stream va donde su naturaleza
lo pide -- no es indecisión, es la razón de separarlos en 4 en vez de 1.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path


class PersistenceStream(str, Enum):
    METRICS = "metrics"
    CONFIG = "config"
    MODELS = "models"
    DECISION_LOG = "decision_log"


#: Raíz de trabajo de Altair en Drive, confirmada en sesiones anteriores
#: (SPEL_Control_Panel.ipynb corre desde acá).
DRIVE_ROOT = Path("/content/drive/MyDrive/SPEL_trabajo")

#: Ruta por stream. Los de Drive son absolutos (fuera del repo). Los de
#: GitHub son repo-relativos (se resuelven contra la raíz del repo
#: clonado, no contra Drive).
PERSISTENCE_PATHS: dict[PersistenceStream, str] = {
    PersistenceStream.METRICS: str(DRIVE_ROOT / "metrics"),
    PersistenceStream.CONFIG: "config/",
    PersistenceStream.MODELS: str(DRIVE_ROOT / "models"),
    PersistenceStream.DECISION_LOG: "decision-log.md",
}

#: Streams que viven en Drive (no versionados en git).
DRIVE_STREAMS: frozenset[PersistenceStream] = frozenset({
    PersistenceStream.METRICS,
    PersistenceStream.MODELS,
})

#: Streams que viven en el repo (versionados en git).
GITHUB_STREAMS: frozenset[PersistenceStream] = frozenset({
    PersistenceStream.CONFIG,
    PersistenceStream.DECISION_LOG,
})


def stream_path(stream: PersistenceStream) -> str:
    """Ruta declarada para un stream. Único punto de verdad -- si esto
    cambia, cambia acá y en ningún otro lado."""
    return PERSISTENCE_PATHS[stream]


def stream_is_local_to_drive(stream: PersistenceStream) -> bool:
    """True si el stream vive en Drive (no versionado en git)."""
    return stream in DRIVE_STREAMS


def stream_is_versioned(stream: PersistenceStream) -> bool:
    """True si el stream vive en el repo (versionado en git)."""
    return stream in GITHUB_STREAMS
