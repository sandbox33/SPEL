"""
tests/test_source_registry.py
==============================
Cobertura de ingestion/source_registry.py.

Los tests que más importan son los de los TRES ESTADOS y los de PROFUNDIDAD.
Colapsar NO_VERIFICADO en VERIFICADO_SIN_COBERTURA convierte "no sabemos" en
"no hay" —el error que dejó a NIFTY50 en el alcance del proyecto— y leer un
TOPE_DE_PETICION como profundidad total hace planificar sobre un número que
no es el que se cree.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from ingestion.source_registry import (
    REGISTRY_PATH,
    SCHEMA_VERSION,
    CoverageState,
    DepthKind,
    RegistryError,
    SourceRegistry,
    load_registry,
    render_text,
)


@pytest.fixture
def registro():
    """El registro real del repo. Se usa a propósito en vez de un doble: si
    alguien edita el JSON y rompe un invariante, estos tests lo dicen."""
    return load_registry()


def _entrada(**kw) -> dict:
    base = {
        "activo": "BTC", "proveedor": "prov",
        "estado": CoverageState.VERIFICADO_CON_DATOS,
        "verificado_el": "2026-08-30",
        "verificado_como": "petición real",
        "filas_verificadas": 10,
    }
    base.update(kw)
    return base


def _escribir(tmp_path, entradas, **extra) -> object:
    doc = {"schema_version": SCHEMA_VERSION, "entradas": entradas}
    doc.update(extra)
    ruta = tmp_path / "reg.json"
    ruta.write_text(json.dumps(doc), encoding="utf-8")
    return ruta


# ══════════════════════════════════════════════════════════════════════════
#  Los tres estados — la distinción central
# ══════════════════════════════════════════════════════════════════════════

def test_los_tres_estados_existen_y_son_distintos():
    assert len(CoverageState.todos()) == 3
    assert CoverageState.NO_VERIFICADO != CoverageState.VERIFICADO_SIN_COBERTURA


def test_solo_verificado_con_datos_cuenta_como_usable(tmp_path):
    ruta = _escribir(tmp_path, [
        _entrada(activo="A", estado=CoverageState.VERIFICADO_CON_DATOS),
        _entrada(activo="B", estado=CoverageState.VERIFICADO_SIN_COBERTURA,
                 filas_verificadas=None),
        _entrada(activo="C", estado=CoverageState.NO_VERIFICADO,
                 filas_verificadas=None),
    ])

    r = load_registry(ruta)

    assert len(r) == 1, "__len__ cuenta usables, no entradas"
    assert len(r.entradas) == 3
    assert [e.activo for e in r.usables()] == ["A"]


def test_no_verificado_no_se_lee_como_sin_cobertura(registro):
    """Tiingo devolvió seis 403: eso es AUTENTICACIÓN, no cobertura.
    Colapsarlo en 'sin cobertura' sería inventar un hecho que nadie midió —
    y es el error que dejó NIFTY50 meses en el alcance."""
    tiingo = registro.for_provider("tiingo")

    assert len(tiingo) == 6
    assert all(e.estado == CoverageState.NO_VERIFICADO for e in tiingo)
    assert not any(e.estado == CoverageState.VERIFICADO_SIN_COBERTURA
                   for e in tiingo)
    assert all("403" in e.verificado_como for e in tiingo)


def test_sin_verificar_lista_los_huecos_de_conocimiento(registro):
    """Cada NO_VERIFICADO es una pregunta abierta, no una respuesta
    negativa."""
    huecos = registro.sin_verificar()

    assert huecos, "hay huecos y el registro los tiene que exponer"
    pares = {(e.activo, e.proveedor) for e in huecos}
    assert ("NVDA", "tiingo") in pares, "el candidato sin descartar para NVDA"
    assert ("NIFTY50", "twelvedata") in pares


def test_contains_pregunta_por_cobertura_no_por_presencia(registro):
    """Mismo idiom que SourceInventory.__contains__: NIFTY50 APARECE en el
    registro (con dos entradas) y aun así no está cubierto."""
    assert "BTC" in registro
    assert "NIFTY50" not in registro
    assert registro.for_asset("NIFTY50"), "aparece, pero sin cobertura usable"


def test_activos_sin_cobertura_incluye_los_que_solo_tienen_no_verificado(tmp_path):
    ruta = _escribir(tmp_path, [
        _entrada(activo="SOLO_DUDA", estado=CoverageState.NO_VERIFICADO,
                 filas_verificadas=None),
    ])

    r = load_registry(ruta)

    assert r.activos_sin_cobertura() == ("SOLO_DUDA",)
    assert r.activos_cubiertos() == ()


# ══════════════════════════════════════════════════════════════════════════
#  Profundidad verificada ≠ profundidad disponible
# ══════════════════════════════════════════════════════════════════════════

def test_las_5000_filas_de_fx_son_un_tope_de_peticion(registro):
    """Planificar sobre 5.000 como si fuera la historia completa es el error
    que este campo existe para evitar."""
    e = registro.get("EURUSD", "alphavantage")

    assert e.filas_verificadas == 5000
    assert e.profundidad == DepthKind.TOPE_DE_PETICION
    assert e.profundidad_es_un_piso is True
    assert any("TOPE" in a.upper() for a in e.advertencias)


def test_los_30_puntos_de_twelvedata_tambien_son_un_tope(registro):
    """El sondeo pidió 30 y recibió 30: no midió la profundidad, midió su
    propia petición."""
    for activo in ("BTC", "XAU", "NVDA", "EURUSD", "GBPUSD", "USDJPY"):
        e = registro.get(activo, "twelvedata")
        assert e.filas_verificadas == 30, activo
        assert e.profundidad == DepthKind.TOPE_DE_PETICION, activo
        assert e.profundidad_es_un_piso is True, activo


def test_btc_y_xau_en_alphavantage_si_tienen_profundidad_completa(registro):
    """Estas dos no chocaron ningún tope: lo que llegó es la historia."""
    btc = registro.get("BTC", "alphavantage")
    xau = registro.get("XAU", "alphavantage")

    assert btc.profundidad == DepthKind.COMPLETA
    assert btc.profundidad_es_un_piso is False
    assert (btc.filas_verificadas, btc.primera_fecha, btc.ultima_fecha) == (
        5889, "2010-07-17", "2026-08-30")
    assert xau.profundidad == DepthKind.COMPLETA
    assert xau.filas_verificadas == 5379


# ══════════════════════════════════════════════════════════════════════════
#  Contrato OHLCV — usable no es lo mismo que suficiente
# ══════════════════════════════════════════════════════════════════════════

def test_xau_en_alphavantage_es_usable_pero_no_cumple_el_contrato(registro):
    """Trae datos reales y solo `date,price`. Una fuente usable puede no
    servir para OHLCV, y el registro tiene que dejar ver la diferencia."""
    e = registro.get("XAU", "alphavantage")

    assert e.usable is True
    assert e.cumple_contrato_ohlcv is False
    assert set(e.columnas_del_contrato_faltantes) == {"open", "high", "low", "volume"}


def test_la_unica_fuente_verificada_con_ohlc_para_oro_es_twelvedata(registro):
    """Consecuencia práctica del test anterior, y un hallazgo que el
    registro hace visible de un vistazo."""
    con_ohlc = [e for e in registro.for_asset("XAU") if e.cumple_contrato_ohlcv]

    assert [e.proveedor for e in con_ohlc] == []  # ninguna cumple el contrato ENTERO
    # Pero TwelveData sí trae OHLC: solo le falta volumen.
    td = registro.get("XAU", "twelvedata")
    assert set(td.columnas_del_contrato_faltantes) == {"volume"}
    assert "open" not in td.columnas_del_contrato_faltantes


def test_nvda_tiene_el_muro_premium_registrado(registro):
    """Uno de los dos fallos que motivaron el registro."""
    av = registro.get("NVDA", "alphavantage")

    assert av.estado == CoverageState.VERIFICADO_SIN_COBERTURA
    assert av.usable is False
    assert "premium" in av.verificado_como.lower()


def test_nifty50_esta_registrado_como_sin_proveedor(registro):
    """El otro fallo. Que quede escrito es el punto del PR."""
    av = registro.get("NIFTY50", "alphavantage")

    assert av.estado == CoverageState.VERIFICADO_SIN_COBERTURA
    assert "INDEX_CATALOG" in av.verificado_como


def test_el_cambio_de_unidad_de_volumen_de_btc_esta_advertido(registro):
    """Un dato que se ve normal y compara dos magnitudes distintas."""
    e = registro.get("BTC", "alphavantage")

    assert any("unidad" in a.lower() for a in e.advertencias)
    assert any("2025" in a for a in e.advertencias)


# ══════════════════════════════════════════════════════════════════════════
#  Ninguna entrada sin fecha de verificación
# ══════════════════════════════════════════════════════════════════════════

def test_toda_entrada_del_registro_real_tiene_fecha(registro):
    """Un dato sin fecha es un dato sin valor: las cuotas y los muros de
    pago cambian."""
    assert registro.entradas
    for e in registro.entradas:
        assert isinstance(e.verificado_el, date)
        assert e.verificado_como, f"{e.activo}/{e.proveedor} sin cómo"


def test_una_entrada_sin_fecha_no_carga(tmp_path):
    ruta = _escribir(tmp_path, [_entrada(verificado_el=None)])

    with pytest.raises(RegistryError, match="verificado_el"):
        load_registry(ruta)


def test_una_fecha_ilegible_no_carga(tmp_path):
    ruta = _escribir(tmp_path, [_entrada(verificado_el="30/08/2026")])

    with pytest.raises(RegistryError, match="fecha ISO"):
        load_registry(ruta)


def test_desactualizadas_usa_la_fecha_para_avisar(registro):
    """El registro guarda hechos con fecha de vencimiento."""
    recientes = registro.desactualizadas(date(2026, 9, 3), max_dias=90)
    viejas = registro.desactualizadas(date(2027, 9, 3), max_dias=90)

    assert recientes == ()
    assert len(viejas) == len(registro.entradas)


def test_antiguedad_en_dias(registro):
    e = registro.get("BTC", "alphavantage")

    assert e.antiguedad_dias(date(2026, 8, 30)) == 0
    assert e.antiguedad_dias(date(2026, 9, 9)) == 10


# ══════════════════════════════════════════════════════════════════════════
#  Validación al cargar
# ══════════════════════════════════════════════════════════════════════════

def test_estado_desconocido_no_carga(tmp_path):
    ruta = _escribir(tmp_path, [_entrada(estado="MAS_O_MENOS")])

    with pytest.raises(RegistryError, match="estado"):
        load_registry(ruta)


def test_profundidad_desconocida_no_carga(tmp_path):
    ruta = _escribir(tmp_path, [_entrada(profundidad="BASTANTE")])

    with pytest.raises(RegistryError, match="profundidad"):
        load_registry(ruta)


def test_verificado_con_datos_sin_filas_es_contradiccion(tmp_path):
    """El registro no debe poder expresar 'llegaron datos' y 'no hay filas'
    a la vez."""
    ruta = _escribir(tmp_path, [
        _entrada(estado=CoverageState.VERIFICADO_CON_DATOS,
                 filas_verificadas=None),
    ])

    with pytest.raises(RegistryError, match="filas_verificadas"):
        load_registry(ruta)


def test_par_duplicado_no_carga(tmp_path):
    """Dos entradas del mismo (activo, proveedor) son dos verdades sobre lo
    mismo — exactamente lo que este proyecto viene corrigiendo."""
    ruta = _escribir(tmp_path, [_entrada(), _entrada()])

    with pytest.raises(RegistryError, match="duplicado"):
        load_registry(ruta)


def test_el_registro_real_no_tiene_pares_duplicados(registro):
    pares = [(e.activo, e.proveedor) for e in registro.entradas]

    assert len(pares) == len(set(pares))


def test_schema_version_desconocida_no_carga(tmp_path):
    ruta = tmp_path / "reg.json"
    ruta.write_text(json.dumps({"schema_version": 99, "entradas": []}))

    with pytest.raises(RegistryError, match="schema_version"):
        load_registry(ruta)


def test_json_invalido_no_carga(tmp_path):
    ruta = tmp_path / "reg.json"
    ruta.write_text("{ esto no es json")

    with pytest.raises(RegistryError, match="JSON"):
        load_registry(ruta)


def test_archivo_ausente_lo_dice_claro(tmp_path):
    with pytest.raises(RegistryError, match="No existe"):
        load_registry(tmp_path / "no_existe.json")


# ══════════════════════════════════════════════════════════════════════════
#  Inmutabilidad y formato
# ══════════════════════════════════════════════════════════════════════════

def test_las_entradas_son_inmutables(registro):
    """Una medición no se parchea: una medición nueva es una entrada nueva
    con fecha nueva. Mismo criterio que SourceInventory."""
    e = registro.entradas[0]

    with pytest.raises(Exception):
        e.estado = CoverageState.VERIFICADO_CON_DATOS
    with pytest.raises(Exception):
        registro.entradas = ()


def test_el_archivo_de_datos_es_json_legible_y_diffeable():
    """Un cambio de cobertura tiene que verse en el diff de un PR: JSON
    indentado, una clave por línea, sin todo en una sola."""
    texto = REGISTRY_PATH.read_text(encoding="utf-8")

    assert texto.count("\n") > 100, "no está todo en una línea"
    assert '\n  "entradas"' in texto or '\n  "meta"' in texto
    json.loads(texto)  # y sigue siendo JSON válido


def test_render_text_muestra_los_tres_estados(registro):
    salida = render_text(registro, hoy=date(2026, 9, 3))

    for estado in CoverageState.todos():
        assert estado in salida
    assert "NIFTY50" in salida


def test_render_text_avisa_si_hay_entradas_viejas(registro):
    salida = render_text(registro, hoy=date(2027, 9, 3))

    assert "90 días" in salida
    assert "cambian" in salida


# ══════════════════════════════════════════════════════════════════════════
#  Reglas duras
# ══════════════════════════════════════════════════════════════════════════

def test_el_modulo_no_toca_la_red_ni_credenciales():
    """Lee el registro, no lo produce. Y `load_secret()` tiene un único call
    site autorizado, que es ingestion/sources.py."""
    import ast
    import inspect
    from pathlib import Path

    import ingestion.source_registry as mod

    arbol = ast.parse(Path(inspect.getfile(mod)).read_text())
    importados: set[str] = set()
    nombres: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            importados.update(a.name for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom):
            if nodo.module:
                importados.add(nodo.module)
            nombres.update(a.name for a in nodo.names)

    raices = {m.split(".")[0] for m in importados}
    assert not ({"httpx", "requests", "websockets", "urllib", "socket",
                 "torch", "sklearn", "yfinance"} & raices), importados
    assert not any(m.startswith("archive") for m in importados), importados
    assert "load_secret" not in nombres
    assert "governance.secrets" not in importados


def test_no_duplica_sources_py():
    """Port, don't rewrite: el registro NO importa SourceInventory ni lo
    reimplementa. Son dos preguntas distintas (foto de runtime vs. memoria
    histórica) que comparten idioms, no código."""
    import ast
    import inspect
    from pathlib import Path

    import ingestion.source_registry as mod

    arbol = ast.parse(Path(inspect.getfile(mod)).read_text())
    nombres = {a.name for n in ast.walk(arbol)
               if isinstance(n, ast.ImportFrom) for a in n.names}

    assert "SourceInventory" not in nombres
    assert "build_price_sources" not in nombres


def test_comparte_los_idioms_de_source_inventory():
    """Los dos son frozen, los dos tienen __len__ que cuenta lo usable y
    __contains__ que pregunta por disponibilidad real."""
    import dataclasses

    from ingestion.sources import SourceInventory

    for cls in (SourceInventory, SourceRegistry):
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen is True
        assert hasattr(cls, "__len__")
        assert hasattr(cls, "__contains__")
        assert hasattr(cls, "log_summary")
