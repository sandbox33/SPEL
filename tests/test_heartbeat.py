"""
tests/test_heartbeat.py
========================
Cobertura de tools/heartbeat.py.

POR QUÉ ESTE ARCHIVO NO EXISTÍA HASTA HOY, que es el dato que lo motiva:
`tools/heartbeat.py` era el ÚNICO módulo del repo sin un solo test --
cero referencias en todo `tests/` (auditoría del 9-sep-2026). Y es el
módulo que un workflow de GitHub Actions ejecuta. Un entry point sin
tests es el que se rompe sin que nadie se entere, porque su fallo se ve
en la pestaña Actions y no en la suite.

Los dos que más importan:

  · `test_cada_linea_de_resultado_lleva_su_marca_de_sintetico` -- el
    encabezado solo no alcanza. Quien lee el log de Actions copia una
    línea suelta, y una línea suelta sin marca se lee como un dato real.

  · `test_el_veredicto_es_degenerado_y_esta_fijado_a_proposito` -- fija
    un hallazgo, no un comportamiento deseado. Que esté en la suite es
    lo que impide que alguien lo "descubra" dentro de seis meses y crea
    que se rompió algo.
"""

from __future__ import annotations

import pytest

import tools.heartbeat as hb
from tools.heartbeat import (
    DEFAULT_SEED,
    REAL_MODE_BLOCKED_REASON,
    SYNTHETIC_INPUTS,
    SYNTHETIC_MARK,
    build_parser,
    run_heartbeat,
)


def _lineas_de_resultado(salida: str) -> list[str]:
    """Las líneas por activo, sin el encabezado ni el cierre."""
    return [l for l in salida.splitlines() if "mc_approved=" in l]


# ─── Contrato de proceso: corre, sale 0, procesa los cinco ────────────────

def test_corre_y_sale_cero_en_modo_sintetico(capsys):
    """La prueba de vida más básica: la cadena entera se ejecuta sin
    lanzar. Es lo único que el workflow de Actions comprueba hoy, y hasta
    este patch no lo comprobaba nadie más."""
    assert run_heartbeat([]) == 0

    salida = capsys.readouterr().out
    assert "[heartbeat]" in salida
    assert len(_lineas_de_resultado(salida)) == len(SYNTHETIC_INPUTS) == 5


def test_procesa_exactamente_los_activos_declarados(capsys):
    run_heartbeat([])
    salida = capsys.readouterr().out

    for asset in SYNTHETIC_INPUTS:
        assert asset in salida, f"falta {asset} en la salida"
    assert f"{len(SYNTHETIC_INPUTS)} activos procesados" in salida


def test_no_sale_en_rojo_por_correr_sintetico(capsys):
    """DELIBERADO, y contra el precedente del legacy.
    `spel_orchestrator_v10.py` (líneas 577-604) marca el job en rojo
    cuando detecta un placeholder -- pero ahí se trata de SECRETOS
    ausentes, que son un fallo real. Acá los sintéticos son el estado
    esperado y declarado, y un rojo permanente en Actions entrena a
    ignorar el rojo."""
    assert run_heartbeat([]) == 0
    assert run_heartbeat(["--dry-run"]) == 0


# ─── La marca de sintético, en cada línea ─────────────────────────────────

def test_cada_linea_de_resultado_lleva_su_marca_de_sintetico(capsys):
    """El encabezado solo no alcanza: quien lee el log de Actions copia
    una línea suelta, y sin marca se lee como un dato real."""
    run_heartbeat([])
    salida = capsys.readouterr().out

    lineas = _lineas_de_resultado(salida)
    assert lineas, "no hubo líneas de resultado que verificar"
    for linea in lineas:
        assert SYNTHETIC_MARK in linea, (
            f"línea sin marca de sintético: {linea!r}")


def test_el_encabezado_y_el_cierre_tambien_la_llevan(capsys):
    run_heartbeat([])
    lineas = capsys.readouterr().out.strip().splitlines()

    assert SYNTHETIC_MARK in lineas[0], "el encabezado no declara el modo"
    assert SYNTHETIC_MARK in lineas[-1], "el cierre no declara el modo"


def test_el_modo_sintetico_es_el_default_sin_pasar_nada(capsys):
    """Invertir el default es el punto: el modo honesto no puede depender
    de que alguien se acuerde de pedirlo."""
    assert build_parser().parse_args([]).dry_run is True
    assert build_parser().parse_args([]).real is False

    run_heartbeat([])   # sin ningún flag
    assert SYNTHETIC_MARK in capsys.readouterr().out


# ─── Reproducibilidad: la semilla se pasa de verdad ───────────────────────

def test_dos_corridas_con_la_misma_semilla_dan_el_mismo_resultado(capsys):
    """El defecto real que tenía este script: NO pasaba `seed` a
    `run_monte_carlo_validation`, que lo acepta desde que se portó. Su
    salida era irreproducible, así que no había forma de distinguir "el
    heartbeat cambió porque el código cambió" de "cambió porque es
    aleatorio"."""
    run_heartbeat([])
    primera = _lineas_de_resultado(capsys.readouterr().out)
    run_heartbeat([])
    segunda = _lineas_de_resultado(capsys.readouterr().out)

    assert primera == segunda, "la salida no es reproducible"


def test_una_semilla_distinta_produce_numeros_distintos(capsys):
    """Contraprueba del anterior: si la semilla se ignorara, el test de
    reproducibilidad pasaría igual y no probaría nada."""
    run_heartbeat(["--seed", "1"])
    con_1 = _lineas_de_resultado(capsys.readouterr().out)
    run_heartbeat(["--seed", "2"])
    con_2 = _lineas_de_resultado(capsys.readouterr().out)

    assert con_1 != con_2, "la semilla no llega al Monte Carlo"


def test_la_semilla_llega_a_run_monte_carlo_validation(monkeypatch, capsys):
    """Verificado sobre el argumento real que recibe la función, no sobre
    el resultado."""
    vistas = []
    real = hb.run_monte_carlo_validation

    def espia(**kw):
        vistas.append(kw.get("seed"))
        return real(**kw)

    monkeypatch.setattr(hb, "run_monte_carlo_validation", espia)
    run_heartbeat(["--seed", "777"])
    capsys.readouterr()

    assert vistas == [777] * len(SYNTHETIC_INPUTS)


def test_la_semilla_por_defecto_esta_declarada_y_se_reporta(capsys):
    """Que aparezca en el encabezado importa: sin la semilla en el log,
    reproducir una corrida vieja exige adivinarla."""
    run_heartbeat([])
    assert f"seed={DEFAULT_SEED}" in capsys.readouterr().out

    run_heartbeat(["--seed", "42"])
    assert "seed=42" in capsys.readouterr().out


# ─── --real: reservado, y falla diciendo por qué ──────────────────────────

def test_real_sale_con_dos_y_nombra_el_archivo_que_falta(capsys):
    """Exit 2 = fallo de invocación, misma convención que
    tools/measure_godel_samples.py. Y el mensaje nombra
    `ingestion/deriv.py`, no un "no implementado" genérico: la diferencia
    entre saber qué falta y tener que buscarlo."""
    assert run_heartbeat(["--real"]) == 2

    cap = capsys.readouterr()
    assert "ingestion/deriv.py" in cap.err
    assert REAL_MODE_BLOCKED_REASON in cap.err


def test_real_no_calcula_nada_antes_de_rechazar(monkeypatch, capsys):
    """Rechazar después de simular sería gastar el cómputo y, peor, dejar
    números a medio imprimir en el log."""
    llamadas = []
    monkeypatch.setattr(hb, "run_monte_carlo_validation",
                        lambda **kw: llamadas.append(kw))

    run_heartbeat(["--real"])
    cap = capsys.readouterr()

    assert llamadas == []
    assert "mc_approved=" not in cap.out


# ─── El hallazgo, fijado para que no sea una sorpresa ─────────────────────

def test_el_veredicto_es_degenerado_y_esta_fijado_a_proposito(capsys):
    """HALLAZGO DEL 9-SEP, NO COMPORTAMIENTO DESEADO.

    Con `base_gold_score=0.70` y `SUCCESS_SCORE_THRESHOLD=0.85`, la
    dispersión GBM a 15 minutos mueve el score simulado ±0.0003. Ninguna
    trayectoria cruza el umbral, así que `mc_approved` sale False y
    `success_rate` sale 0.0000 para los cinco activos, SIEMPRE. El
    heartbeat viene imprimiendo el mismo veredicto desde que se escribió.

    Se fija acá para que sea visible en la suite en vez de que alguien lo
    descubra en seis meses y crea que se rompió algo. No se corrige
    subiendo `base_gold_score`: ese número no saldría de ninguna medición.

    SI ESTE TEST SE PONE EN ROJO, no lo relajes -- significa que alguien
    movió los sintéticos o el umbral, y eso hay que mirarlo."""
    from core.monte_carlo import SUCCESS_SCORE_THRESHOLD

    assert all(v["base_gold_score"] < SUCCESS_SCORE_THRESHOLD
               for v in SYNTHETIC_INPUTS.values()), (
        "los sintéticos ya no están por debajo del umbral -- el veredicto "
        "dejó de ser degenerado y este test hay que rehacerlo, no borrarlo")

    run_heartbeat([])
    for linea in _lineas_de_resultado(capsys.readouterr().out):
        assert "mc_approved=False" in linea
        assert "success_rate=0.0000" in linea


def test_los_sinteticos_son_validos_para_monte_carlo():
    """`run_monte_carlo_validation` lanza con precio <= 0, volatilidad
    negativa o iteraciones <= 0. Que los cinco pasen no es obvio: son
    valores escritos a mano."""
    for asset, inputs in SYNTHETIC_INPUTS.items():
        assert inputs["current_price"] > 0, asset
        assert inputs["volatility"] >= 0, asset
        assert 0.0 <= inputs["base_gold_score"] <= 1.0, asset


# ─── Lo que este módulo NO debe tocar ─────────────────────────────────────

def test_heartbeat_no_importa_secretos_ni_execution():
    """Su propio docstring promete que no lee `governance/secrets.py` ni
    toca `execution/`. Se verifica sobre el AST y no con grep, que daría
    falso positivo con el docstring que menciona los dos."""
    import ast
    import inspect

    arbol = ast.parse(inspect.getsource(hb))

    importados = set()
    for n in ast.walk(arbol):
        if isinstance(n, ast.Import):
            importados.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            importados.add(n.module)

    for prohibido in ("governance.secrets", "execution", "execution.circuit_breaker",
                      "execution.execution_guard"):
        assert not any(m == prohibido or m.startswith(prohibido + ".")
                       for m in importados), (
            f"heartbeat importa {prohibido}, y su docstring promete que no")


def test_heartbeat_no_escribe_archivos(tmp_path, monkeypatch, capsys):
    """El reporte sale por stdout. Un heartbeat que además escribe es un
    heartbeat que puede dejar basura en cada corrida de Actions."""
    monkeypatch.chdir(tmp_path)
    antes = set(tmp_path.iterdir())

    run_heartbeat([])
    capsys.readouterr()

    assert set(tmp_path.iterdir()) == antes


@pytest.mark.parametrize("argv", [[], ["--dry-run"], ["--seed", "5"]])
def test_ningun_modo_sintetico_lanza(argv, capsys):
    assert run_heartbeat(argv) == 0
    capsys.readouterr()
