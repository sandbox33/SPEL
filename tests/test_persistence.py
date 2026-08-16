"""
tests/test_persistence.py
==========================
Cobertura de governance/persistence.py -- los 4 streams declarados
(Decisión #14). No hay I/O real que probar todavía (el módulo solo
declara rutas) -- estos tests protegen la estructura: que los 4 streams
existan, que cada uno esté clasificado en exactamente un lugar
(Drive XOR GitHub, nunca ambos ni ninguno), y que las rutas no cambien
por accidente en un refactor futuro.
"""

from __future__ import annotations

from governance.persistence import (
    DRIVE_ROOT,
    DRIVE_STREAMS,
    GITHUB_STREAMS,
    PERSISTENCE_PATHS,
    PersistenceStream,
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


def test_cada_stream_tiene_ruta_declarada():
    for stream in PersistenceStream:
        assert stream in PERSISTENCE_PATHS
        assert stream_path(stream)  # no vacío


def test_metrics_y_models_son_drive():
    assert stream_is_local_to_drive(PersistenceStream.METRICS) is True
    assert stream_is_local_to_drive(PersistenceStream.MODELS) is True


def test_config_y_decision_log_son_github():
    assert stream_is_versioned(PersistenceStream.CONFIG) is True
    assert stream_is_versioned(PersistenceStream.DECISION_LOG) is True


def test_ningun_stream_es_drive_y_github_a_la_vez():
    assert DRIVE_STREAMS.isdisjoint(GITHUB_STREAMS)


def test_todos_los_streams_estan_clasificados_en_algun_lado():
    # ni huérfanos (ni Drive ni GitHub) ni duplicados -- ya cubierto por
    # el test anterior, este confirma que la UNIÓN cubre los 4.
    assert DRIVE_STREAMS | GITHUB_STREAMS == set(PersistenceStream)


def test_rutas_de_drive_son_absolutas_bajo_drive_root():
    for stream in DRIVE_STREAMS:
        assert stream_path(stream).startswith(str(DRIVE_ROOT))


def test_rutas_de_github_son_repo_relativas_no_absolutas():
    for stream in GITHUB_STREAMS:
        assert not stream_path(stream).startswith("/")


def test_decision_log_apunta_a_decision_log_md():
    assert stream_path(PersistenceStream.DECISION_LOG) == "decision-log.md"
