"""
tests/test_frescura.py
=======================
Cobertura de ingestion/frescura.py.

Series sintéticas en `tmp_path` vía `SPEL_DRIVE_ROOT`, escritas con
`append_day()` de verdad: sin red, sin Drive, sin mocks de `read_series`.

LOS CUATRO CASOS DEL BRIEF, más los dos que no estaban y que habrían puesto
la alarma en rojo el primer día:

  · EURUSD acumula días insuficientes por construcción de su filtro. Si la
    alarma mirara el `n_events` de cada activo, estaría en rojo permanente.
  · NVDA, NIFTY50 y EURUSD empiezan su serie DESPUÉS de la fecha de corte
    (no tienen siembra). Con una sola fecha de corte para los cinco, los días
    entre la marca y su primera fila contarían como huecos internos.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

import governance.persistence as persistence_module
from governance.persistence import DRIVE_ROOT_ENV_VAR
from ingestion.frescura import (
    Nivel,
    dias_no_publicados,
    evaluar,
    indexar,
    inventariar_huecos,
    leer_marca_de_inicio,
    main,
    registrar_inicio_si_falta,
    ruta_marca,
)
from ingestion.gdelt_aggregation import DailyAggregationResult
from ingestion.gdelt_series import append_day

CORTE = date(2026, 9, 4)


@pytest.fixture(autouse=True)
def _drive_root_temporal(monkeypatch, tmp_path):
    monkeypatch.setenv(DRIVE_ROOT_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)


def _fila(asset, d, n_events=10):
    vacio = n_events < 5
    return DailyAggregationResult(
        day=d, asset=asset,
        entropy_shannon=None if vacio else 1.1,
        zipf_concentration=None if vacio else 0.2,
        goldstein_mean=None if vacio else 1.0,
        tone_variance=None if vacio else 0.3,
        n_events=n_events, insufficient_events=vacio)


def _sembrar(asset, desde, dias, *, vacios=(), ausentes=(), n_events=10):
    for k in range(dias):
        d = desde + timedelta(days=k)
        if d in ausentes:
            continue
        append_day(_fila(asset, d, 0 if d in vacios else n_events))


def _dia(n):
    return CORTE + timedelta(days=n)


def _estado(asset, assets=("BTC", "XAU"), ultimo_publicado=None):
    ultimo = ultimo_publicado or _dia(9)
    return next(e for e in evaluar(list(assets), ultimo_publicado=ultimo)
                if e.asset == asset)


# ═══ Los cuatro casos del brief ═══════════════════════════════════════════

def test_sin_gap_es_verde():
    registrar_inicio_si_falta(CORTE)
    for a in ("BTC", "XAU"):
        _sembrar(a, CORTE, 10)

    e = _estado("BTC")
    assert e.nivel == Nivel.VERDE
    assert e.huecos_internos == [] and e.pendientes_punta == []


def test_dia_pendiente_en_la_punta_es_verde_con_aviso():
    """GDELT todavía no publicó a las 06:30 UTC. `run_gdelt` escribió el día
    vacío (404) y la corrida siguiente lo reintenta: no es una falla."""
    registrar_inicio_si_falta(CORTE)
    for a in ("BTC", "XAU"):
        _sembrar(a, CORTE, 10, vacios={_dia(9)})

    e = _estado("BTC")
    assert e.nivel == Nivel.AVISO
    assert e.pendientes_punta == [_dia(9)]
    assert e.huecos_internos == []


def test_hueco_interno_posterior_al_corte_es_rojo():
    """El día 5 quedó vacío para todos y el 6 en adelante ya se bajaron con
    datos. Si GDELT publicó el 6, publicó el 5: no se cura solo."""
    registrar_inicio_si_falta(CORTE)
    for a in ("BTC", "XAU"):
        _sembrar(a, CORTE, 10, vacios={_dia(5)})

    e = _estado("BTC")
    assert e.nivel == Nivel.ROJO
    assert e.huecos_internos == [_dia(5)]


def test_hueco_anterior_al_corte_no_dispara():
    """Deuda heredada: la serie histórica trae 24/25 huecos y no son una
    falla de la automatización."""
    registrar_inicio_si_falta(CORTE)
    for a in ("BTC", "XAU"):
        _sembrar(a, CORTE - timedelta(days=20), 30,
                 ausentes={CORTE - timedelta(days=10)})

    e = _estado("BTC")
    assert e.nivel == Nivel.VERDE
    assert CORTE - timedelta(days=10) in inventariar_huecos("BTC")


def test_un_dia_ausente_con_datos_despues_tambien_es_rojo():
    """"Vacío o ausente": que falte la fila entera es lo mismo que que esté
    vacía, si después ya hay datos."""
    registrar_inicio_si_falta(CORTE)
    for a in ("BTC", "XAU"):
        _sembrar(a, CORTE, 10, ausentes={_dia(3)})

    assert _estado("BTC").nivel == Nivel.ROJO


# ═══ Los dos falsos rojos que el diseño tiene que evitar ══════════════════

def test_eurusd_con_dias_insuficientes_no_pone_la_alarma_en_rojo():
    """EURUSD acumula días con menos de 5 eventos -- y a veces con cero --
    por construcción de su filtro. Mientras los activos CORE tengan datos,
    ese día SÍ se publicó, y no es un hueco."""
    registrar_inicio_si_falta(CORTE)
    assets = ("BTC", "XAU", "EURUSD")
    _sembrar("BTC", CORTE, 10)
    _sembrar("XAU", CORTE, 10)
    # Patrón MIXTO, como el real: ceros intercalados con 1-4 eventos, todos
    # insuficientes. La primera versión de este test sembraba cero TODOS los
    # días, y así no atrapaba el error que decía atrapar: sin un día no-cero
    # después, todos los ceros caían en "la punta" y el mutante que mira el
    # n_events de cada activo daba aviso en vez de rojo. Encontrado con un
    # mutante que sobrevivió.
    for k in range(10):
        append_day(_fila("EURUSD", _dia(k), n_events=0 if k % 2 == 0 else 3))

    e = _estado("EURUSD", assets)
    assert e.nivel != Nivel.ROJO, e.linea()
    assert e.huecos_internos == []


def test_un_activo_que_empieza_despues_del_corte_no_es_rojo():
    """NVDA no tiene siembra: CI le empieza la serie con los últimos días.
    Con una sola fecha de corte para todos, los días entre la marca y su
    primera fila contarían como huecos internos -- la alarma nacería en rojo
    el primer día. El rango de cada activo arranca en su primera fila."""
    registrar_inicio_si_falta(CORTE)
    assets = ("BTC", "XAU", "NVDA")
    _sembrar("BTC", CORTE, 10)
    _sembrar("XAU", CORTE, 10)
    _sembrar("NVDA", _dia(5), 5)

    e = _estado("NVDA", assets)
    assert e.nivel == Nivel.VERDE, e.linea()


def test_pero_un_hueco_dentro_del_rango_de_ese_activo_si_dispara():
    """Contraprueba del anterior: arrancar el rango en la primera fila no
    puede volver ciega a la alarma para ese activo."""
    registrar_inicio_si_falta(CORTE)
    assets = ("BTC", "XAU", "NVDA")
    _sembrar("BTC", CORTE, 10)
    _sembrar("XAU", CORTE, 10)
    _sembrar("NVDA", _dia(5), 5, ausentes={_dia(7)})

    assert _estado("NVDA", assets).nivel == Nivel.ROJO


# ═══ La regla del día no publicado ════════════════════════════════════════

def test_vacio_en_todos_los_activos_es_no_publicado():
    for a in ("BTC", "XAU", "EURUSD"):
        _sembrar(a, CORTE, 5, vacios={_dia(2)})

    assert dias_no_publicados(indexar(["BTC", "XAU", "EURUSD"])) == {_dia(2)}


def test_vacio_solo_en_eurusd_no_es_no_publicado():
    _sembrar("BTC", CORTE, 5)
    _sembrar("XAU", CORTE, 5)
    _sembrar("EURUSD", CORTE, 5, vacios={_dia(2)})

    assert dias_no_publicados(indexar(["BTC", "XAU", "EURUSD"])) == set()


def test_un_dia_donde_solo_eurusd_tiene_fila_no_califica():
    """Sin un activo CORE en el día, un cero de EURUSD se explica por su
    filtro. Hace falta un CORE en cero para concluir que falta el archivo."""
    _sembrar("EURUSD", CORTE, 3, n_events=0)

    assert dias_no_publicados(indexar(["BTC", "XAU", "EURUSD"])) == set()


# ═══ Marca de inicio ══════════════════════════════════════════════════════

def test_sin_marca_la_alarma_no_evalua_nada_y_no_es_roja():
    _sembrar("BTC", CORTE, 10, ausentes={_dia(3)})

    e = _estado("BTC")
    assert leer_marca_de_inicio() is None
    assert e.nivel == Nivel.AVISO


def test_la_marca_nace_en_null_y_se_fija_una_sola_vez():
    """Moverla después escondería los huecos del tramo que quedara afuera."""
    ruta_marca().parent.mkdir(parents=True, exist_ok=True)
    ruta_marca().write_text('{"desde": null}\n', encoding="utf-8")

    assert registrar_inicio_si_falta(CORTE) is True
    assert registrar_inicio_si_falta(_dia(5)) is False
    assert leer_marca_de_inicio() == CORTE
    assert json.loads(ruta_marca().read_text())["desde"] == CORTE.isoformat()


# ═══ Retraso e inventario ═════════════════════════════════════════════════

def test_el_retraso_se_reporta_como_aviso_y_no_como_rojo():
    """Si la serie va detrás del último día publicado, se dice. No es rojo:
    el brief reserva el rojo para lo que no se cura solo."""
    registrar_inicio_si_falta(CORTE)
    for a in ("BTC", "XAU"):
        _sembrar(a, CORTE, 5)

    e = _estado("BTC", ultimo_publicado=_dia(9))
    assert e.nivel == Nivel.AVISO
    assert e.dias_de_retraso == 5


def test_el_inventario_cuenta_dias_de_calendario_sin_fila():
    """La definición que hace cerrar las cifras del brief: 4.904 días de
    calendario - 4.880 filas = 24 huecos en BTC."""
    ausentes = {date(2020, 1, 3), date(2020, 1, 7), date(2020, 1, 8)}
    _sembrar("BTC", date(2020, 1, 1), 10, ausentes=ausentes)

    assert inventariar_huecos("BTC") == sorted(ausentes)


# ═══ CLI ══════════════════════════════════════════════════════════════════

def test_el_cli_sale_uno_con_hueco_interno(capsys):
    registrar_inicio_si_falta(CORTE)
    for a in ("BTC", "XAU"):
        _sembrar(a, CORTE, 10, vacios={_dia(5)})

    assert main(["--assets", "BTC", "XAU"], hoy=_dia(10)) == 1
    salida = capsys.readouterr()
    assert "ROJO" in salida.out and "BTC" in salida.err


def test_el_cli_sale_cero_con_punta_pendiente(capsys):
    registrar_inicio_si_falta(CORTE)
    for a in ("BTC", "XAU"):
        _sembrar(a, CORTE, 10, vacios={_dia(9)})

    assert main(["--assets", "BTC", "XAU"], hoy=_dia(10)) == 0
    assert "VERDE_CON_AVISO" in capsys.readouterr().out


def test_una_linea_por_activo_que_se_entiende_sola(capsys):
    registrar_inicio_si_falta(CORTE)
    for a in ("BTC", "XAU"):
        _sembrar(a, CORTE, 10)

    main(["--assets", "BTC", "XAU"], hoy=_dia(10))
    lineas = [l for l in capsys.readouterr().out.splitlines()
              if l.strip().startswith(("BTC", "XAU"))]
    assert len(lineas) == 2
    for l in lineas:
        assert "último" in l and "retraso" in l and "huecos internos" in l
