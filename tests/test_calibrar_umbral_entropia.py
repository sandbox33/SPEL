"""
tests/test_calibrar_umbral_entropia.py
========================================
Cobertura de tools/calibrar_umbral_entropia.py.

SERIES SINTÉTICAS EN DISCO, NO MOCKS DE `read_series`. Se apunta
`SPEL_DRIVE_ROOT` a un `tmp_path` y se escriben días de verdad con
`append_day()`. Un mock de `read_series` probaría que el tool llama bien a
una función que el test inventó; esto prueba que lee la serie real, con su
formato real y su deduplicación real.

EL TEST QUE SOSTIENE AL TOOL es
`test_el_umbral_del_tool_es_identico_al_de_produccion`. Todo lo demás es
mecánica; ese es el que impide que el tool se despegue del motor. Un tool
que calcula el percentil de otra manera mide otra cosa -- que es el defecto
que este repo ya tuvo cuando `measure_godel_samples.py` medía un P90 contra
una máscara que operaba en P66, y nadie lo notó porque los dos números
existían y ninguno era absurdo.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

import governance.persistence as persistence_module
from core.scoring import (
    GODEL_MASK_PERCENTILE,
    GODEL_ROLLING_WINDOW_DAYS,
    compute_adaptive_percentile,
)
from governance.persistence import DRIVE_ROOT_ENV_VAR
from ingestion.gdelt_aggregation import DailyAggregationResult
from ingestion.gdelt_series import append_day
from tools.calibrar_umbral_entropia import (
    ADVERTENCIA,
    N_MINIMO_PARCIAL,
    SCHEMA_VERSION,
    VERSION_SCRIPT,
    Estado,
    a_documento,
    calibrar,
    calibrar_activo,
    main,
)

GLOBAL_DEFAULT = 1.0


@pytest.fixture(autouse=True)
def _drive_root_temporal(monkeypatch, tmp_path):
    monkeypatch.setenv(DRIVE_ROOT_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)


def _sembrar(asset: str, entropias, *, desde=date(2020, 1, 1)) -> list[float]:
    """Escribe días reales. `None` en la lista = día con
    insufficient_events, que es como la serie representa un día que no llegó
    a MIN_EVENTS_FOR_VALID_DAY."""
    for i, e in enumerate(entropias):
        append_day(DailyAggregationResult(
            day=desde + timedelta(days=i), asset=asset,
            entropy_shannon=e,
            zipf_concentration=None if e is None else 0.2,
            goldstein_mean=None if e is None else 1.0,
            tone_variance=None if e is None else 0.3,
            n_events=0 if e is None else 10,
            insufficient_events=e is None))
    return [e for e in entropias if e is not None]


def _rampa(n: int, base: float = 0.5, paso: float = 0.001) -> list[float]:
    """Determinista, sin RNG: un valor esperado que dependa de la versión de
    numpy no es un valor esperado."""
    return [base + paso * i for i in range(n)]


# ═══ Un caso por estado de la tabla ═══════════════════════════════════════

def test_con_252_o_mas_dias_el_estado_es_medido():
    _sembrar("BTC", _rampa(GODEL_ROLLING_WINDOW_DAYS))
    r = calibrar_activo("BTC", global_default=GLOBAL_DEFAULT)

    assert r.estado == Estado.MEDIDO
    assert r.n_validos == GODEL_ROLLING_WINDOW_DAYS
    assert r.umbral_historia_completa is not None
    assert r.umbral_ultimos_252 is not None
    assert r.sha256_serie


def test_entre_100_y_251_el_estado_es_parcial_y_el_umbral_va_marcado():
    """Publica umbral, pero diciendo que se midió sobre menos historia que
    la que usa producción. Un umbral parcial sin marca se lee igual que uno
    completo."""
    _sembrar("BTC", _rampa(150))
    r = calibrar_activo("BTC", global_default=GLOBAL_DEFAULT)

    assert r.estado == Estado.PARCIAL
    assert r.umbral_historia_completa is not None
    assert r.motivo and str(GODEL_ROLLING_WINDOW_DAYS) in r.motivo


def test_bajo_100_el_estado_es_insuficiente_y_no_se_publica_umbral():
    """NO SE INTERPOLA NI SE RELLENA. El 100 no es un número nuevo: es
    MIN_OBS_FOR_ROLLING, el piso con que el propio motor deja de confiar en
    un percentil rolling."""
    _sembrar("BTC", _rampa(N_MINIMO_PARCIAL - 1))
    r = calibrar_activo("BTC", global_default=GLOBAL_DEFAULT)

    assert r.estado == Estado.INSUFICIENTE
    assert r.umbral_historia_completa is None
    assert r.umbral_ultimos_252 is None
    assert r.n_validos == N_MINIMO_PARCIAL - 1


def test_sin_serie_es_un_resultado_no_un_error():
    r = calibrar_activo("BTC", global_default=GLOBAL_DEFAULT)

    assert r.estado == Estado.SIN_SERIE
    assert r.n_filas == 0 and r.n_validos == 0
    assert r.umbral_historia_completa is None
    assert r.motivo


# ═══ EL test: comparabilidad con producción ═══════════════════════════════

def test_el_umbral_del_tool_es_identico_al_de_produccion():
    """Sobre la MISMA ventana, el umbral del tool y el de
    `compute_adaptive_percentile()` tienen que ser el mismo número. No
    aproximadamente: idéntico, porque es la misma llamada.

    Si esto se rompe, el tool está calculando el percentil por su cuenta y
    el reporte dice medir el umbral de la máscara midiendo otra cosa."""
    entropias = _sembrar("BTC", _rampa(300))
    r = calibrar_activo("BTC", global_default=GLOBAL_DEFAULT)

    esperado_completo = compute_adaptive_percentile(
        history=entropias, percentile=GODEL_MASK_PERCENTILE,
        global_default=GLOBAL_DEFAULT).value
    esperado_ventana = compute_adaptive_percentile(
        history=entropias[-GODEL_ROLLING_WINDOW_DAYS:],
        percentile=GODEL_MASK_PERCENTILE,
        global_default=GLOBAL_DEFAULT).value

    assert r.umbral_historia_completa == esperado_completo
    assert r.umbral_ultimos_252 == esperado_ventana


def test_usa_el_percentil_de_la_mascara_no_otro():
    """Contraprueba del anterior: si el tool usara, digamos, P90, el test de
    arriba seguiría pasando solo si el esperado también usara P90. Acá se
    fija que el percentil publicado ES el de la máscara."""
    _sembrar("BTC", _rampa(300))
    r = calibrar(["BTC"], global_default=GLOBAL_DEFAULT)

    assert r.percentil == GODEL_MASK_PERCENTILE == 66.0


def test_la_historia_completa_y_la_ventana_dan_numeros_distintos():
    """Con deriva, recortar a 252 cambia el percentil. Si los dos campos
    fueran siempre iguales, uno de los dos no estaría midiendo lo que dice."""
    _sembrar("BTC", _rampa(600))
    r = calibrar_activo("BTC", global_default=GLOBAL_DEFAULT)

    assert r.umbral_historia_completa != r.umbral_ultimos_252
    assert r.umbral_ultimos_252 > r.umbral_historia_completa  # rampa creciente


# ═══ Días sin entropía ════════════════════════════════════════════════════

def test_los_dias_sin_entropia_no_cuentan_para_n_validos():
    """`n_validos` es el de días con entropía, NUNCA el de filas. Publicar
    el de filas inflaría la confianza en la medición con días que no
    aportaron ningún número."""
    con_huecos = _rampa(200) + [None] * 50
    _sembrar("BTC", con_huecos)
    r = calibrar_activo("BTC", global_default=GLOBAL_DEFAULT)

    assert r.n_filas == 250
    assert r.n_validos == 200
    assert r.estado == Estado.PARCIAL, "250 filas no alcanzan si 50 son huecos"


def test_un_activo_de_puros_huecos_es_insuficiente_no_medido():
    """El caso de EURUSD: la serie existe y tiene filas, pero ningún día
    llegó a MIN_EVENTS_FOR_VALID_DAY tras filtrar."""
    _sembrar("EURUSD", [None] * 300)
    r = calibrar_activo("EURUSD", global_default=GLOBAL_DEFAULT)

    assert r.estado == Estado.INSUFICIENTE
    assert r.n_filas == 300 and r.n_validos == 0
    assert r.umbral_historia_completa is None


# ═══ Read-only por defecto ════════════════════════════════════════════════

def test_sin_write_no_se_crea_ningun_archivo(tmp_path, capsys):
    _sembrar("BTC", _rampa(300))
    antes = {p for p in tmp_path.rglob("*")}

    assert main(["--assets", "BTC"]) == 0
    capsys.readouterr()

    assert {p for p in tmp_path.rglob("*")} == antes


def test_con_write_el_json_valida_contra_el_esquema(tmp_path, capsys):
    _sembrar("BTC", _rampa(300))
    destino = tmp_path / "calib.json"

    assert main(["--assets", "BTC", "--write", "--salida", str(destino)]) == 0
    capsys.readouterr()

    d = json.loads(destino.read_text(encoding="utf-8"))
    assert d["schema_version"] == SCHEMA_VERSION
    assert d["version_script"] == VERSION_SCRIPT
    assert d["generado_por"] == "tools/calibrar_umbral_entropia.py"
    assert d["percentil"] == GODEL_MASK_PERCENTILE
    assert d["medido_utc"].endswith("Z")
    assert d["commit"]
    assert d["advertencia"] == ADVERTENCIA

    btc = d["activos"]["BTC"]
    for campo in ("estado", "n_validos", "n_filas", "umbral_historia_completa",
                  "umbral_ultimos_252", "rango", "sha256_serie",
                  "fuente_percentil"):
        assert campo in btc, f"falta {campo}"
    assert isinstance(d["zonas_grises"], list) and d["zonas_grises"]
    assert isinstance(d["peticiones_admin"], list)


def test_salida_sin_write_es_error_de_invocacion(capsys):
    """Pedir una ruta de salida sin pedir escribir es una contradicción, no
    una preferencia: elegir una de las dos en silencio sería adivinar."""
    assert main(["--salida", "/tmp/x.json"]) == 2
    assert "ERROR" in capsys.readouterr().err


def test_sin_datos_sale_cero_y_reporta_sin_serie(capsys):
    """CRITERIO DE ACEPTACIÓN DEL BRIEF: que funcione sin datos es parte del
    contrato, no una degradación. Es además la situación del sandbox."""
    assert main([]) == 0

    salida = capsys.readouterr().out
    assert salida.count("estado: sin_serie") == 5
    assert "ZONAS GRISES" in salida
    assert "PETICIONES AL ADMIN" in salida


# ═══ Determinismo ═════════════════════════════════════════════════════════

def test_dos_corridas_dan_el_mismo_sha_y_los_mismos_umbrales():
    _sembrar("BTC", _rampa(300))
    a = calibrar_activo("BTC", global_default=GLOBAL_DEFAULT)
    b = calibrar_activo("BTC", global_default=GLOBAL_DEFAULT)

    assert a.sha256_serie == b.sha256_serie
    assert a.umbral_historia_completa == b.umbral_historia_completa
    assert a.umbral_ultimos_252 == b.umbral_ultimos_252


def test_el_sha_cambia_si_la_serie_cambia():
    """Contraprueba: si el sha fuera constante, el test de determinismo
    pasaría igual y no probaría nada. Es lo que permite saber después si una
    medición corresponde a la serie que hoy está en disco."""
    _sembrar("BTC", _rampa(300))
    antes = calibrar_activo("BTC", global_default=GLOBAL_DEFAULT).sha256_serie

    _sembrar("BTC", [0.9], desde=date(2021, 6, 1))
    despues = calibrar_activo("BTC", global_default=GLOBAL_DEFAULT).sha256_serie

    assert antes != despues


def test_el_sha_distingue_el_dia_y_no_solo_el_valor():
    """Se hashea el par (día, entropía): dos series con los mismos valores
    en días distintos NO son la misma serie."""
    _sembrar("BTC", _rampa(120), desde=date(2020, 1, 1))
    uno = calibrar_activo("BTC", global_default=GLOBAL_DEFAULT).sha256_serie

    _sembrar("XAU", _rampa(120), desde=date(2021, 1, 1))
    otro = calibrar_activo("XAU", global_default=GLOBAL_DEFAULT).sha256_serie

    assert uno != otro


# ═══ Zonas grises y peticiones: secciones FIJAS ═══════════════════════════

def test_las_dos_secciones_salen_aunque_no_haya_nada_que_decir(capsys):
    """Un reporte que omite una sección cuando está vacía entrena a no
    buscarla, y el día que tenga contenido nadie la va a leer."""
    _sembrar("BTC", _rampa(300))
    r = calibrar(["BTC"], global_default=GLOBAL_DEFAULT)

    assert r.zonas_grises, "la sección desapareció al no haber zonas grises"
    assert "Ninguna" in r.zonas_grises[0]
    assert r.peticiones_admin, "las peticiones al Admin no son opcionales"


def test_las_zonas_grises_traen_el_numero_que_las_respalda():
    _sembrar("BTC", _rampa(50))
    r = calibrar(["BTC"], global_default=GLOBAL_DEFAULT)

    assert any("50/50" in z for z in r.zonas_grises)


def test_la_peticion_del_filtro_gobierno_nombra_el_filtro_real():
    """Cita GOBIERNO_COUNTRY_FILTERS desde el módulo, no una copia: si
    alguien lo amplía, el texto de la petición cambia solo."""
    from core.scoring import GOBIERNO_COUNTRY_FILTERS

    r = calibrar(["EURUSD"], global_default=GLOBAL_DEFAULT)
    peticion = next(p for p in r.peticiones_admin if "GOBIERNO" in p)

    assert str(GOBIERNO_COUNTRY_FILTERS) in peticion
    assert "NINGÚN VOLUMEN DE INGESTA LO ARREGLA" in peticion


def test_la_peticion_de_desalineacion_contrasta_medible_contra_operable():
    """El hecho estructural: los que tienen serie no son los que están en
    _DERIV_SYMBOL_MAP."""
    _sembrar("BTC", _rampa(300))
    _sembrar("XAU", _rampa(300))
    r = calibrar(["BTC", "XAU", "EURUSD"], global_default=GLOBAL_DEFAULT)

    peticion = next(p for p in r.peticiones_admin if "DESALINEACIÓN" in p)
    assert "'BTC'" in peticion and "'XAU'" in peticion
    assert "EURUSD" in peticion


# ═══ La advertencia, que es lo que evita el cableado ══════════════════════

def test_la_advertencia_esta_en_el_docstring_en_el_reporte_y_en_el_json(capsys):
    """Las tres, porque las tres se leen por separado. Quien copie el JSON a
    otro repo no va a leer el docstring."""
    import tools.calibrar_umbral_entropia as mod

    assert "NO ES EL UMBRAL DE PRODUCCIÓN" in (mod.__doc__ or "").upper()

    _sembrar("BTC", _rampa(300))
    main(["--assets", "BTC"])
    assert "NO ES EL DE PRODUCCIÓN" in capsys.readouterr().out

    r = calibrar(["BTC"], global_default=GLOBAL_DEFAULT)
    assert "NUNCA se lee" in a_documento(r)["advertencia"]


# ═══ Lo que este tool NO hace ═════════════════════════════════════════════

def test_el_tool_no_escribe_ningun_dia_de_serie(tmp_path, capsys):
    """Solo lee. Un tool de calibración que además ingiere es un tool que
    cambia lo que está midiendo."""
    import ast
    import inspect

    import tools.calibrar_umbral_entropia as mod

    arbol = ast.parse(inspect.getsource(mod))
    importados = set()
    for n in ast.walk(arbol):
        if isinstance(n, ast.ImportFrom) and n.module:
            importados.update(f"{n.module}.{a.name}" for a in n.names)

    assert "ingestion.gdelt_series.append_day" not in importados
    assert "ingestion.gdelt_series.read_series" in importados
