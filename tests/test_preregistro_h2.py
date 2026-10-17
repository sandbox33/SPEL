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
SHA256_PREREGISTRO = "ab3696123f6f8d99fc45cad8517ce892ce6d797454cd586eb2f1dcef4fc9c10f"


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
    (pre.REZAGO_SECUNDARIO_DIAS_CALENDARIO, "`REZAGO_SECUNDARIO_DIAS_CALENDARIO = 1`", "1"),
    (pre.PRUEBAS_POR_ACTIVO, "Por activo hay **6** pruebas", "6"),
    (pre.NIVEL_MCS, "nivel **0,90**", "0,90"),
    (pre.REPLICAS_MCS, "**10.000** réplicas", "10.000"),
    (pre.SEMILLA_MCS, "semilla **20261017**", "20261017"),
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


def test_el_rezago_es_de_calendario_y_el_de_1_dia_es_secundario():
    """Decisión del Admin del 29-sep: rezago 2 en días de calendario. D6
    (14-oct) devuelve el de 1 día como secundario: ya no está descartado."""
    texto = " ".join(_texto().split())
    assert "La lectura de rezago 1 queda descartada." not in texto
    assert "**La lectura secundaria (D6)** usa la entropía de la fecha `t − 1`" in texto
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


# ═══ D6 (14-oct-2026): la secundaria, los horizontes, la familia y el MCS ═══

def test_los_horizontes_son_los_del_documento():
    fragmento = "**Horizontes (D6): `h` = 1, 5 y 22 barras del calendario del activo.**"
    assert fragmento in " ".join(_texto().split())
    assert pre.HORIZONTES == (1, 5, 22)


def test_la_familia_son_los_rezagos_por_los_horizontes_y_no_un_literal():
    """La secundaria cuenta en el N (D6): entra en la familia de Holm."""
    assert pre.PRUEBAS_POR_ACTIVO == len(pre.REZAGOS_DIAS_CALENDARIO) * len(pre.HORIZONTES) == 6
    arbol = ast.parse((RAIZ / "core" / "preregistro_h2.py").read_text(encoding="utf-8"))
    asignacion = next(n for n in arbol.body if isinstance(n, ast.Assign)
                      and [t.id for t in n.targets] == ["PRUEBAS_POR_ACTIVO"])
    assert not isinstance(asignacion.value, ast.Constant)


def test_la_primaria_es_t_menos_2_y_va_primero():
    assert pre.REZAGOS_DIAS_CALENDARIO == (2, 1)
    e = _diaria(date(2026, 9, 1), date(2026, 9, 30))
    assert pre.fecha_de_entropia(date(2026, 9, 20), e) == date(2026, 9, 18)


def test_la_secundaria_usa_t_menos_1_y_tampoco_rellena():
    e = _diaria(date(2026, 9, 1), date(2026, 9, 30), sin={date(2026, 9, 14)})
    assert pre.fecha_de_entropia(date(2026, 9, 20), e, rezago=1) == date(2026, 9, 19)
    assert pre.fecha_de_entropia(date(2026, 9, 15), e, rezago=1) is None
    assert pre.fecha_de_entropia(date(2026, 9, 16), e) is None


@pytest.mark.parametrize("rezago", [0, 3, -1, True, None])
def test_ningun_otro_rezago_vale(rezago):
    """El 0 sería la entropía del mismo día; True contaría como 1."""
    e = _diaria(date(2026, 9, 1), date(2026, 9, 30))
    with pytest.raises(ValueError, match="rezago"):
        pre.fecha_de_entropia(date(2026, 9, 20), e, rezago=rezago)


@pytest.mark.parametrize("t, h, rezagos", [
    (100, 1, 4), (100, 5, 5), (504, 1, 5), (504, 22, 22), (4500, 1, 9), (4500, 5, 9),
])
def test_los_rezagos_hac_son_el_ancho_o_el_horizonte(t, h, rezagos):
    assert "`⌊4 · (T/100)^(2/9)⌋` rezagos, o `h` si `h` es mayor" in " ".join(_texto().split())
    assert pre.rezagos_hac(t, h) == rezagos


@pytest.mark.parametrize("h", [0, 2, 7, 30])
def test_los_rezagos_hac_solo_para_un_horizonte_del_documento(h):
    with pytest.raises(ValueError, match="horizonte"):
        pre.rezagos_hac(100, h)


@pytest.mark.parametrize("fragmento", [
    "continuo, 24/7, para BTC (D6)",
    "**Todo lo del día de la semana se estima solo en entrenamiento (D6).**",
    "y **nunca decide** la conclusión. Cuenta en el N (D3).",
    "**Model Confidence Set** de Hansen, Lunde y Nason (2011)",
    "Los 6 p-valores se ajustan por **Holm**",
])
def test_lo_que_fija_d6_esta_escrito(fragmento):
    assert fragmento in " ".join(_texto().split())


def test_h2_esta_en_el_registro_de_ensayos_con_este_documento():
    """D3: los dos rezagos son ensayos, pre-registrados y no corridos, y
    apuntan al sha256 de este documento."""
    from governance import ensayos
    de_h2 = {e["id"]: e for e in ensayos.leer() if e["hipotesis"] == "H2"}
    assert set(de_h2) == {f"h2-rezago-{r}" for r in pre.REZAGOS_DIAS_CALENDARIO}
    for e in de_h2.values():
        assert e["estado"] == "pre-registrado" and e["cuenta_en_n"] is True
        assert e["preregistro_sha256"] == SHA256_PREREGISTRO


# ═══ Brief 11–13-B (17-oct-2026): P1 y P6 ═════════════════════════════════

def test_no_queda_ninguna_marca_de_pendiente_del_admin():
    """La última era la fuente de precios, y la cierra P1."""
    assert "PENDIENTE DEL ADMIN" not in _texto()


def test_la_fuente_de_precios_es_la_de_p1():
    """Antes, la sección 2 dejaba la fuente pendiente, con una propuesta. P1
    la fija: las velas de D1, solo con la compuerta del brief 14 en verde, y
    sin fuente de reemplazo para un activo en rojo."""
    texto = " ".join(_texto().split())
    assert ("**Precios (P1, auditor, 17-oct-2026): las velas diarias de D1 (TwelveData), y solo "
            "después de que el brief 14 las valide con su compuerta.**") in texto
    assert ("H2 no corre sobre ese activo: no hay fuente de reemplazo sin validar, y la "
            "compuerta no se relaja.") in texto
    assert "Propuesta de este PR, que no se aplica sin el Admin" not in texto
    assert "la evaluación se detiene y pregunta antes de correr" in texto


P6_APROBADAS = (
    "En XAU son las mismas tres",
    "D6 da los números y no la unidad.",
    "Los 6 p-valores se ajustan por **Holm**",
    "Lo que D6 no fija queda fijado acá",
    "D6 pide comparar con MCS y con Diebold–Mariano",
)


@pytest.mark.parametrize("despues", P6_APROBADAS)
def test_las_interpretaciones_de_p6_estan_aprobadas(despues):
    assert f"**[INTERPRETACIÓN aprobada (P6)]** {despues}" in " ".join(_texto().split())


def test_solo_las_de_p6_dicen_aprobada():
    """P6 aprueba cinco. Las demás —la tolerancia cero, Garman–Klass y las dos
    nuevas de la sección 2— las sigue aprobando el Admin al fusionar."""
    texto = " ".join(_texto().split())
    assert "Las que dicen **[INTERPRETACIÓN aprobada (P6)]** ya las aprobó el auditor" in texto
    # Las cinco marcas, más la vez que el encabezado explica la marca.
    assert texto.count("[INTERPRETACIÓN aprobada (P6)]") == len(P6_APROBADAS) + 1
    for sin_aprobar in ("**[INTERPRETACIÓN] Tolerancia cero**",
                        '**[INTERPRETACIÓN]** "HAR-RV con los mismos datos"',
                        "**[INTERPRETACIÓN]** Si el brief 14 sale rojo para BTC",
                        "**[INTERPRETACIÓN] La hora de corte**"):
        assert sin_aprobar in texto, sin_aprobar
