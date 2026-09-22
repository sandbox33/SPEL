"""
tests/test_verificar_siembra.py
=================================
Cobertura de tools/verificar_siembra.py.

Series sintéticas en `tmp_path` vía `SPEL_DRIVE_ROOT`. El caso que coincide
se arma con las cifras REALES (4.904 días de calendario, 24 huecos en BTC):
un test con cifras de juguete no probaría que las constantes cierran entre
sí, y un error de un día en `CIFRAS_SIEMBRA` pondría la verificación en
rojo el día que el Admin la dispare.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

import pytest

import governance.persistence as persistence_module
import tools.verificar_siembra as vs
from governance.persistence import DRIVE_ROOT_ENV_VAR
from ingestion.gdelt_aggregation import DailyAggregationResult
from ingestion.gdelt_series import _result_to_line, _series_file_path, append_day
from tools.verificar_siembra import CIFRAS_SIEMBRA, main, verificar_activo

INICIO = date(2013, 4, 1)
FIN = date(2026, 9, 3)


@pytest.fixture(autouse=True)
def _drive_root_temporal(monkeypatch, tmp_path):
    monkeypatch.setenv(DRIVE_ROOT_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)


def _fila(asset, d, valido=True):
    return DailyAggregationResult(
        day=d, asset=asset,
        entropy_shannon=1.1 if valido else None,
        zipf_concentration=0.2 if valido else None,
        goldstein_mean=1.0 if valido else None,
        tone_variance=0.3 if valido else None,
        n_events=100 if valido else 2, insufficient_events=not valido)


def _huecos(n):
    """n huecos repartidos por el rango, ninguno en los extremos."""
    paso = (FIN - INICIO).days // (n + 1)
    return {INICIO + timedelta(days=paso * (k + 1)) for k in range(n)}


def _sembrar(asset, *, huecos, invalidos=(), hasta=FIN, salto_final=True):
    """Escribe el archivo de una vez, como lo subiría el Admin -- no con
    append_day(), que es lo que se está verificando que NO haya corrido."""
    lineas = []
    d = INICIO
    while d <= hasta:
        if d not in huecos:
            lineas.append(_result_to_line(_fila(asset, d, d not in invalidos)))
        d += timedelta(days=1)
    ruta = _series_file_path(asset)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text("\n".join(lineas) + ("\n" if salto_final else ""),
                    encoding="utf-8")
    return ruta


# ═══ Las cifras cierran ═══════════════════════════════════════════════════

def test_las_cifras_cierran_contra_el_calendario():
    """4.904 días de calendario: 4.880 filas son 24 huecos en BTC y 4.879
    son 25 en XAU. Las cuatro cifras del brief tienen que ser coherentes
    entre sí antes de que sirvan para comparar nada."""
    calendario = (FIN - INICIO).days + 1
    assert calendario == 4904
    assert calendario - CIFRAS_SIEMBRA["BTC"].filas == 24
    assert calendario - CIFRAS_SIEMBRA["XAU"].filas == 25
    for c in CIFRAS_SIEMBRA.values():
        assert (c.primer_dia, c.ultimo_dia) == (str(INICIO), str(FIN))


def test_una_siembra_que_coincide_es_verde():
    _sembrar("BTC", huecos=_huecos(24))

    v = verificar_activo("BTC", CIFRAS_SIEMBRA["BTC"])

    assert not v.rojo, v.discrepancias
    assert (v.filas, v.validos) == (4880, 4880)
    assert len(v.huecos) == 24


def test_el_sha256_es_el_del_archivo_byte_por_byte():
    ruta = _sembrar("XAU", huecos=_huecos(25))

    v = verificar_activo("XAU", CIFRAS_SIEMBRA["XAU"])

    assert v.sha256 == hashlib.sha256(ruta.read_bytes()).hexdigest()
    assert not v.rojo


# ═══ Lo que tiene que dar rojo ════════════════════════════════════════════

def test_una_fila_de_menos_es_rojo():
    _sembrar("BTC", huecos=_huecos(25))

    v = verificar_activo("BTC", CIFRAS_SIEMBRA["BTC"])

    assert v.rojo
    assert any(d.startswith("filas: 4879") for d in v.discrepancias)


def test_un_dia_sin_entropia_es_rojo_aunque_las_filas_coincidan():
    """Filas y válidos son dos cifras distintas a propósito: una siembra
    del activo o del filtro equivocado puede tener todas las filas."""
    invalido = INICIO + timedelta(days=3)
    _sembrar("BTC", huecos=_huecos(24), invalidos={invalido})

    v = verificar_activo("BTC", CIFRAS_SIEMBRA["BTC"])

    assert v.rojo
    assert any(d.startswith("válidos: 4879") for d in v.discrepancias)


def test_una_siembra_truncada_es_rojo():
    """El caso de un archivo subido a medias: termina antes."""
    _sembrar("BTC", huecos=_huecos(24), hasta=FIN - timedelta(days=30))

    v = verificar_activo("BTC", CIFRAS_SIEMBRA["BTC"])

    assert v.rojo
    assert any(d.startswith("último día del rango") for d in v.discrepancias)


def test_sin_archivo_es_rojo():
    v = verificar_activo("BTC", CIFRAS_SIEMBRA["BTC"])
    assert v.rojo and not v.existe


def test_una_linea_corrupta_es_rojo():
    """Dos JSON pegados en una línea: lo que deja un salto recortado a mitad
    de archivo. `read_series()` la descarta con un warning y el tool lo
    cuenta escuchando ese warning, no re-parseando."""
    ruta = _sembrar("BTC", huecos=_huecos(24))
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    lineas[10:12] = [lineas[10] + lineas[11]]
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")

    v = verificar_activo("BTC", CIFRAS_SIEMBRA["BTC"])

    assert v.rojo
    assert v.lineas_corruptas == 1
    assert "1 línea(s) corrupta(s)" in v.discrepancias


# ═══ Lo que NO tiene que dar rojo ═════════════════════════════════════════

def test_sin_salto_final_es_aviso_y_no_rojo():
    """append_day() lo agrega en la primera escritura desde el Brief D.
    Bloquear por esto sería un rojo que no protege de nada."""
    _sembrar("BTC", huecos=_huecos(24), salto_final=False)

    v = verificar_activo("BTC", CIFRAS_SIEMBRA["BTC"])

    assert not v.rojo, v.discrepancias
    assert not v.termina_en_salto and v.avisos


def test_los_dias_que_ci_agrego_despues_no_lo_ponen_en_rojo():
    """Si se vuelve a verificar después de que CI escribió, las cifras se
    cuentan dentro del rango sembrado. El total va aparte."""
    _sembrar("BTC", huecos=_huecos(24))
    for k in range(1, 6):
        append_day(_fila("BTC", FIN + timedelta(days=k)))

    v = verificar_activo("BTC", CIFRAS_SIEMBRA["BTC"])

    assert not v.rojo, v.discrepancias
    assert v.filas_totales == 4885
    assert v.ultimo_dia_total == str(FIN + timedelta(days=5))


# ═══ CLI ══════════════════════════════════════════════════════════════════

def test_el_cli_sale_cero_si_las_dos_coinciden(capsys):
    _sembrar("BTC", huecos=_huecos(24))
    _sembrar("XAU", huecos=_huecos(25))

    assert main([]) == 0
    assert "RESULTADO: VERDE" in capsys.readouterr().out


def test_el_cli_sale_uno_si_una_no_coincide(capsys):
    _sembrar("BTC", huecos=_huecos(24))
    _sembrar("XAU", huecos=_huecos(24))

    assert main([]) == 1
    salida = capsys.readouterr().out
    assert "── XAU: ROJO" in salida and "── BTC: VERDE" in salida


def test_un_activo_sin_cifras_sale_dos(capsys):
    assert main(["--assets", "NVDA"]) == 2


def test_no_escribe_nada(tmp_path):
    _sembrar("BTC", huecos=_huecos(24), salto_final=False)
    antes = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    main(["--assets", "BTC"])

    despues = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert despues == antes
