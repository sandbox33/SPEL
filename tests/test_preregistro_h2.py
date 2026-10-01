"""
tests/test_preregistro_h2.py
==============================
El pre-registro H2 no se modifica después de fusionarse. Este test lo hace
valer igual que los de H1 y H3: fija el sha256 de research/preregistro_h2.md,
verifica que cada número de core/preregistro_h2.py esté escrito en él
(parseado, no solo buscado) y que el documento no tenga voseo.
"""

from __future__ import annotations

import ast
import hashlib
from datetime import date, timedelta
from pathlib import Path

import pytest

import core.preregistro_h2 as pre
from tests.test_registro_linguistico import formas_voseantes

RAIZ = Path(__file__).resolve().parent.parent
DOC = RAIZ / "research" / "preregistro_h2.md"

#: Si cambia, el documento cambió. Después de fusionado, eso es un
#: experimento nuevo: preregistro_h2_v2.md, no una edición de este.
SHA256_PREREGISTRO = "e4e1d7a2ac993c3944247c2bfdf9ebe0686a97a7cbc03b7c6068a930fc93b9b2"


def _texto() -> str:
    return DOC.read_text(encoding="utf-8")


def _leer(escrito: str) -> float:
    return float(escrito.replace(".", "").replace(",", "."))


def test_el_documento_no_cambio():
    assert hashlib.sha256(DOC.read_bytes()).hexdigest() == SHA256_PREREGISTRO


def test_el_documento_no_usa_voseo():
    assert formas_voseantes(_texto()) == []


@pytest.mark.parametrize("valor, fragmento, escrito", [
    (pre.REZAGO_ENTROPIA_DIAS_CALENDARIO, "`REZAGO_ENTROPIA_DIAS_CALENDARIO = 2`", "2"),
    (pre.TOLERANCIA_ASOF_DIAS, "(`TOLERANCIA_ASOF_DIAS = 0`)", "0"),
    (pre.ALFA_DIEBOLD_MARIANO, "**Alfa fijado antes: 0,05.**", "0,05"),
    (pre.VENTANA_INICIAL_BARRAS, "arranca con **504** barras", "504"),
])
def test_cada_numero_del_modulo_esta_escrito_en_el_documento(valor, fragmento, escrito):
    assert fragmento in _texto(), fragmento
    assert escrito in fragmento
    assert _leer(escrito) == pytest.approx(valor)


@pytest.mark.parametrize("calendario, ventanas", [
    ("continuo (BTC)", pre.HAR_VENTANAS_CONTINUO),
    ("hábil (XAU)", pre.HAR_VENTANAS_HABIL),
])
def test_las_ventanas_del_har_son_las_de_la_tabla(calendario, ventanas):
    fila = "| " + calendario + " | " + " | ".join(f"**{v}**" for v in ventanas) + " |"
    assert fila in _texto(), fila


def test_el_rezago_es_de_calendario_y_la_lectura_de_rezago_1_no_esta():
    """Decisión del Admin del 29-sep: rezago 2 en días de calendario. La
    lectura de rezago 1 se elimina del documento, no queda como opción."""
    texto = " ".join(_texto().split())
    assert "La lectura de rezago 1 queda descartada." in texto
    assert "Rezago 1, como dice el brief" not in texto
    assert "[PENDIENTE DEL ADMIN]** El brief pide" not in texto
    assert "el lunes usa la entropía del **sábado**" in texto
    assert "`DIAS_DE_RETRASO_DE_PUBLICACION = 1`" in texto


# ═══ La unión entropía-retorno ════════════════════════════════════════════

def _diaria(desde: date, hasta: date, *, sin=()) -> dict:
    """Entropía en todos los días de calendario, como la publica GDELT."""
    out, d = {}, desde
    while d <= hasta:
        if d not in sin:
            out[d] = 1.0 + d.toordinal() % 7
        d += timedelta(days=1)
    return out


def test_el_lunes_del_oro_usa_la_entropia_del_sabado():
    lunes = date(2026, 9, 28)
    assert lunes.weekday() == 0
    e = _diaria(date(2026, 9, 1), date(2026, 9, 30))
    usada = pre.fecha_de_entropia(lunes, e)
    assert usada == date(2026, 9, 26) and usada.weekday() == 5, \
        "el sábado, no el jueves que daría correr dos velas hábiles"


def test_ninguna_fila_usa_entropia_posterior_a_t_menos_2():
    e = _diaria(date(2025, 1, 1), date(2025, 12, 31), sin={date(2025, 3, 3)})
    dias = [date(2025, 1, 3) + timedelta(days=k) for k in range(360)]
    habiles = [d for d in dias if d.weekday() < 5]
    for t in dias + habiles:
        usada = pre.fecha_de_entropia(t, e)
        if usada is not None:
            assert usada <= t - timedelta(days=2), (t, usada)
            assert usada == t - timedelta(days=2), "tolerancia cero: exacta o nada"


def test_sin_entropia_en_t_menos_2_el_retorno_se_excluye():
    """El hueco de 2025: el as-of no arrastra la entropía del 13-jun."""
    hueco = {date(2025, 6, 14) + timedelta(days=k) for k in range(18)}
    e = _diaria(date(2025, 6, 1), date(2025, 7, 31), sin=hueco)
    assert pre.fecha_de_entropia(date(2025, 6, 15), e) == date(2025, 6, 13)
    for k in range(18):
        t = date(2025, 6, 16) + timedelta(days=k)
        assert pre.fecha_de_entropia(t, e) is None, t
    assert pre.fecha_de_entropia(date(2025, 7, 4), e) == date(2025, 7, 2)


def test_un_dia_con_insufficient_events_no_tiene_entropia():
    e = _diaria(date(2026, 9, 1), date(2026, 9, 30))
    e[date(2026, 9, 10)] = None
    assert pre.fecha_de_entropia(date(2026, 9, 12), e) is None


def test_con_tolerancia_el_asof_busca_hacia_atras_y_nunca_adelante(monkeypatch):
    """La regla es un as-of de verdad: si el Admin abre la tolerancia, busca
    hacia atrás hasta ese tope, y nunca toma t−1."""
    monkeypatch.setattr(pre, "TOLERANCIA_ASOF_DIAS", 2)
    e = _diaria(date(2026, 9, 1), date(2026, 9, 30),
                sin={date(2026, 9, 18), date(2026, 9, 17)})
    assert pre.fecha_de_entropia(date(2026, 9, 20), e) == date(2026, 9, 16)
    e.pop(date(2026, 9, 16))
    assert pre.fecha_de_entropia(date(2026, 9, 20), e) is None
    assert pre.fecha_de_entropia(date(2026, 9, 21), e) == date(2026, 9, 19)


@pytest.mark.parametrize("t, ancho", [(100, 4), (504, 5), (1000, 6), (4500, 9)])
def test_el_ancho_de_newey_west_es_la_regla_escrita(t, ancho):
    assert "`⌊4 · (T/100)^(2/9)⌋`" in _texto()
    assert pre.ancho_newey_west(t) == ancho


def test_el_ancho_no_admite_t_cero():
    with pytest.raises(ValueError):
        pre.ancho_newey_west(0)


def test_el_modulo_no_trae_codigo_de_evaluacion():
    """El código de evaluación va en otro PR. Acá, solo math para una regla
    de una línea y fechas para la unión entropía-retorno."""
    arbol = ast.parse((RAIZ / "core" / "preregistro_h2.py").read_text(encoding="utf-8"))
    importados = {a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names}
    importados |= {n.module for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom)}
    assert importados <= {"__future__", "math", "datetime", "typing"}, importados
