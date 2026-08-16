"""
tests/test_persistence.py
==========================
Cobertura de governance/persistence.py -- los 4 streams declarados
(Decisión #14) y drive_root() (fix del hallazgo #6 de auditoría: antes
hardcodeado, ahora detecta entorno con el mismo orden de prioridad que
secrets.py::load_secret() -- env var, Colab, fallback).
"""

from __future__ import annotations

import governance.persistence as persistence_module
from governance.persistence import (
    COLAB_DRIVE_ROOT,
    DRIVE_ROOT_ENV_VAR,
    DRIVE_STREAMS,
    GITHUB_STREAMS,
    LOCAL_FALLBACK_DRIVE_ROOT,
    PERSISTENCE_RELATIVE_PATHS,
    PersistenceStream,
    drive_root,
    stream_is_local_to_drive,
    stream_is_versioned,
    stream_path,
)


def test_existen_exactamente_los_4_streams():
    assert set(PersistenceStream) == {
        PersistenceStream.METRICS,
        PersistenceStream.CONFIG,
        PersistenceStream.MODELS,
        PersistenceStream.DECISION_LOG,
    }


def test_cada_stream_tiene_ruta_relativa_declarada():
    for stream in PersistenceStream:
        assert stream in PERSISTENCE_RELATIVE_PATHS
        assert PERSISTENCE_RELATIVE_PATHS[stream]  # no vacío


def test_metrics_y_models_son_drive():
    assert stream_is_local_to_drive(PersistenceStream.METRICS) is True
    assert stream_is_local_to_drive(PersistenceStream.MODELS) is True


def test_config_y_decision_log_son_github():
    assert stream_is_versioned(PersistenceStream.CONFIG) is True
    assert stream_is_versioned(PersistenceStream.DECISION_LOG) is True


def test_ningun_stream_es_drive_y_github_a_la_vez():
    assert DRIVE_STREAMS.isdisjoint(GITHUB_STREAMS)


def test_todos_los_streams_estan_clasificados_en_algun_lado():
    assert DRIVE_STREAMS | GITHUB_STREAMS == set(PersistenceStream)


def test_rutas_de_github_son_repo_relativas_no_absolutas():
    for stream in GITHUB_STREAMS:
        assert not stream_path(stream).startswith("/")


def test_decision_log_apunta_a_decision_log_md():
    assert stream_path(PersistenceStream.DECISION_LOG) == "decision-log.md"


# ─── drive_root() -- fix del hallazgo #6 de auditoría ──────────────────────

def test_drive_root_usa_fallback_local_sin_env_var_ni_colab(monkeypatch):
    monkeypatch.delenv(DRIVE_ROOT_ENV_VAR, raising=False)
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)
    assert drive_root() == LOCAL_FALLBACK_DRIVE_ROOT


def test_drive_root_respeta_env_var_override(monkeypatch, tmp_path):
    monkeypatch.setenv(DRIVE_ROOT_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)
    assert drive_root() == tmp_path


def test_drive_root_detecta_colab_cuando_no_hay_override(monkeypatch):
    monkeypatch.delenv(DRIVE_ROOT_ENV_VAR, raising=False)
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: True)
    assert drive_root() == COLAB_DRIVE_ROOT


def test_drive_root_env_var_tiene_prioridad_sobre_colab(monkeypatch, tmp_path):
    # Aunque _is_colab() diga True, si hay override explícito gana el
    # override -- mismo orden que secrets.py::load_secret() (env var
    # primero, antes de intentar Colab userdata).
    monkeypatch.setenv(DRIVE_ROOT_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: True)
    assert drive_root() == tmp_path


def test_drive_root_se_evalua_en_cada_llamada_no_es_constante_fija(monkeypatch, tmp_path):
    # Corrige exactamente el hallazgo #6: la versión vieja fijaba la
    # ruta al importar el módulo. Esta se recalcula por llamada.
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)
    monkeypatch.delenv(DRIVE_ROOT_ENV_VAR, raising=False)
    primero = drive_root()

    monkeypatch.setenv(DRIVE_ROOT_ENV_VAR, str(tmp_path))
    segundo = drive_root()

    assert primero != segundo
    assert segundo == tmp_path


def test_stream_path_de_metrics_usa_drive_root_actual(monkeypatch, tmp_path):
    monkeypatch.setenv(DRIVE_ROOT_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)
    assert stream_path(PersistenceStream.METRICS) == str(tmp_path / "metrics")


def test_stream_path_de_models_usa_drive_root_actual(monkeypatch, tmp_path):
    monkeypatch.setenv(DRIVE_ROOT_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)
    assert stream_path(PersistenceStream.MODELS) == str(tmp_path / "models")


def test_rutas_de_drive_son_absolutas_bajo_el_drive_root_actual(monkeypatch, tmp_path):
    monkeypatch.setenv(DRIVE_ROOT_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)
    for stream in DRIVE_STREAMS:
        assert stream_path(stream).startswith(str(tmp_path))


# ─── decision-log.md real, no solo la ruta declarada ────────────────────────

def test_decision_log_md_existe_en_la_raiz_del_repo():
    # stream_path(DECISION_LOG) declara "decision-log.md" -- confirma
    # que el archivo REAL está donde el stream dice que debería estar,
    # no en otro lado por accidente.
    from pathlib import Path
    ruta_declarada = stream_path(PersistenceStream.DECISION_LOG)
    assert Path(ruta_declarada).exists()


def test_decision_log_md_no_esta_vacio():
    from pathlib import Path
    contenido = Path(stream_path(PersistenceStream.DECISION_LOG)).read_text()
    assert len(contenido) > 100
