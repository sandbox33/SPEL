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

FIX de esta sesión (hallazgo #6 de auditoría): la primera versión tenía
la raíz de Drive hardcodeada a la ruta de Colab de Altair, sin
condicional -- no rompía nada porque el módulo todavía no hace I/O
real, pero violaba el mismo principio que gobierna
governance/secrets.py (detección de entorno, nunca ruta fija), y
hubiera fallado en cuanto GitHub Actions (que ya existe desde el patch
0011 -- .github/workflows/tests.yml) necesitara este stream. drive_root()
ahora sigue EXACTAMENTE el mismo orden de prioridad que
secrets.py::load_secret(): env var -> detección de Colab -> fallback.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path


class PersistenceStream(str, Enum):
    METRICS = "metrics"
    CONFIG = "config"
    MODELS = "models"
    DECISION_LOG = "decision_log"


#: Nombre de la env var de override explícito -- mismo rol que las claves
#: de governance/secrets.py, pero para una ruta en vez de una credencial.
DRIVE_ROOT_ENV_VAR = "SPEL_DRIVE_ROOT"

#: Fallback cuando no hay override ni Colab -- NO es Drive real. Nombre
#: con punto y prefijo explícito para que nadie lo confunda con
#: persistencia real (desarrollo local, o CI que solo corre tests).
LOCAL_FALLBACK_DRIVE_ROOT = Path(".spel_drive_stream")

#: Ruta real de Colab de Altair -- ahora SOLO se usa si _is_colab() la
#: confirma, no incondicionalmente como antes del fix.
COLAB_DRIVE_ROOT = Path("/content/drive/MyDrive/SPEL_trabajo")

#: Rutas relativas dentro de su storage -- estas NO dependen del entorno
#: (a diferencia de la raíz de Drive). Los de GitHub se resuelven contra
#: la raíz del repo clonado; los de Drive contra drive_root().
PERSISTENCE_RELATIVE_PATHS: dict[PersistenceStream, str] = {
    PersistenceStream.METRICS: "metrics",
    PersistenceStream.CONFIG: "config/",
    PersistenceStream.MODELS: "models",
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


def _is_colab() -> bool:
    """Detecta Colab por import, mismo mecanismo que
    secrets.py::_try_colab_userdata() -- sin ofuscación: si algún
    escáner necesita bloquear Colab en cierto contexto, que sea una
    regla explícita del CI, no un truco de string concatenado."""
    try:
        import google.colab  # noqa: F401
        return True
    except ImportError:
        return False


def drive_root() -> Path:
    """
    Raíz de los streams de Drive. Prioridad -- MISMO orden que
    secrets.py::load_secret(), no un mecanismo nuevo inventado acá:
      1. env var SPEL_DRIVE_ROOT -- override explícito. Para GitHub
         Actions o cualquier entorno que no sea Colab pero necesite
         escribir streams de Drive en algún lado (ej. un runner con un
         volumen montado, o un test que quiere una ruta controlada).
      2. Colab, si `google.colab` es importable -> la ruta de trabajo
         real de Altair (COLAB_DRIVE_ROOT).
      3. Fallback local (LOCAL_FALLBACK_DRIVE_ROOT) -- para que el
         módulo no rompa en desarrollo local o en CI que no necesita
         persistencia real (ej. correr la suite de tests). Marcado
         explícitamente en el nombre para que nadie lo confunda con
         persistencia real.

    Se evalúa en cada llamada (no es una constante fijada al importar)
    -- así un test puede cambiar la env var entre llamadas sin
    recargar el módulo, y el comportamiento siempre refleja el entorno
    ACTUAL, no el de cuando Python cargó este archivo.
    """
    override = os.environ.get(DRIVE_ROOT_ENV_VAR)
    if override:
        return Path(override)

    if _is_colab():
        return COLAB_DRIVE_ROOT

    return LOCAL_FALLBACK_DRIVE_ROOT


def stream_path(stream: PersistenceStream) -> str:
    """Ruta declarada para un stream. Único punto de verdad -- si esto
    cambia, cambia acá y en ningún otro lado. Para streams de Drive, se
    resuelve dinámicamente contra drive_root() (ver su docstring)."""
    relative = PERSISTENCE_RELATIVE_PATHS[stream]
    if stream in DRIVE_STREAMS:
        return str(drive_root() / relative)
    return relative


def stream_is_local_to_drive(stream: PersistenceStream) -> bool:
    """True si el stream vive en Drive (no versionado en git)."""
    return stream in DRIVE_STREAMS


def stream_is_versioned(stream: PersistenceStream) -> bool:
    """True si el stream vive en el repo (versionado en git)."""
    return stream in GITHUB_STREAMS
