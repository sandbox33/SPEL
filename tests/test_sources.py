"""
tests/test_sources.py
======================
Cobertura de ingestion/sources.py — el punto de composición.

Ningún test de acá toca la red ni depende de credenciales reales: el
fixture `sin_secretos` deja el entorno en un estado conocido antes de cada
uno, y los que necesitan una credencial la inventan.

El último test es un GUARDIÁN, no un test de unidad: corre solo cuando el
entorno afirma tener secretos configurados (`SPEL_EXPECT_SECRETS=1`, que
pone el workflow `live-tests.yml`), y su trabajo es detectar el caso en que
los secretos están cargados en GitHub pero mal escritos o no inyectados —
un fallo que ningún test offline puede ver, porque offline la ausencia es
el estado esperado.
"""

from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path

import pytest

import governance.secrets as secrets_mod
from governance.secrets import SecretKey
from ingestion.adapters import DerivAdapter, TwelveDataAdapter
from ingestion.sources import SourceInventory, build_price_sources

TOKEN_FALSO = "token-de-prueba-que-no-debe-filtrarse"
APP_ID_FALSO = "app-id-de-prueba"
KEY_FALSA = "key-de-prueba-que-no-debe-filtrarse"


@pytest.fixture
def sin_secretos(monkeypatch):
    """Entorno sin ninguna credencial de precio.

    El `delenv` solo no alcanza: `load_secret()` tiene un segundo tier que
    consulta Colab userdata, así que en una máquina con Drive montado y
    secretos cargados (el entorno real de trabajo de este proyecto) los
    tests dependerían de credenciales de verdad y pasarían o fallarían
    según la máquina. Neutralizar el tier de Colab hace que 'sin secretos'
    signifique lo mismo en todos lados.
    """
    for clave in (
        SecretKey.TWELVEDATA_API_KEY,
        SecretKey.DERIV_API_TOKEN,
        SecretKey.DERIV_APP_ID,
    ):
        monkeypatch.delenv(clave, raising=False)
    monkeypatch.setattr(secrets_mod, "_try_colab_userdata", lambda key: None)


# ══════════════════════════════════════════════════════════════════════════
#  Una fuente sin credencial es capacidad ausente, no error
# ══════════════════════════════════════════════════════════════════════════

def test_sin_credenciales_no_lanza_y_reporta_por_que(sin_secretos):
    """Un motor que se cae entero al arrancar porque falta una clave es peor
    que uno que arranca degradado y lo dice."""
    inventario = build_price_sources()

    assert len(inventario) == 0
    assert inventario.disponibles == {}
    assert set(inventario.ausentes) == {"twelvedata", "deriv"}


def test_solo_twelvedata_configurada(sin_secretos, monkeypatch):
    monkeypatch.setenv(SecretKey.TWELVEDATA_API_KEY, KEY_FALSA)

    inventario = build_price_sources()

    assert len(inventario) == 1
    assert "twelvedata" in inventario
    assert "deriv" not in inventario
    assert isinstance(inventario.disponibles["twelvedata"], TwelveDataAdapter)


def test_solo_deriv_configurada(sin_secretos, monkeypatch):
    monkeypatch.setenv(SecretKey.DERIV_API_TOKEN, TOKEN_FALSO)
    monkeypatch.setenv(SecretKey.DERIV_APP_ID, APP_ID_FALSO)

    inventario = build_price_sources()

    assert len(inventario) == 1
    assert "deriv" in inventario
    assert isinstance(inventario.disponibles["deriv"], DerivAdapter)


def test_deriv_con_token_pero_sin_app_id_no_se_construye(sin_secretos, monkeypatch):
    """Deriv necesita las DOS credenciales. Tener el token y que falte el
    app_id es un caso real y frecuente (el token se rota, el app_id se
    olvida en el otro entorno), y el motivo tiene que decir cuál de las dos
    falta — no un 'credenciales incompletas' que obliga a adivinar."""
    monkeypatch.setenv(SecretKey.DERIV_API_TOKEN, TOKEN_FALSO)

    inventario = build_price_sources()

    assert "deriv" not in inventario
    assert inventario.ausentes["deriv"] == f"falta {SecretKey.DERIV_APP_ID}"
    assert SecretKey.DERIV_API_TOKEN not in inventario.ausentes["deriv"]


def test_ambas_fuentes_configuradas(sin_secretos, monkeypatch):
    monkeypatch.setenv(SecretKey.TWELVEDATA_API_KEY, KEY_FALSA)
    monkeypatch.setenv(SecretKey.DERIV_API_TOKEN, TOKEN_FALSO)
    monkeypatch.setenv(SecretKey.DERIV_APP_ID, APP_ID_FALSO)

    inventario = build_price_sources()

    assert len(inventario) == 2
    assert inventario.ausentes == {}


def test_timeout_s_se_propaga_a_los_adapters(sin_secretos, monkeypatch):
    """Un timeout distinto por fuente sería una decisión de operación; hasta
    que haya un motivo medido para diferenciarlos, el inventario propaga uno
    solo y hay que poder verificar que llega."""
    monkeypatch.setenv(SecretKey.TWELVEDATA_API_KEY, KEY_FALSA)
    monkeypatch.setenv(SecretKey.DERIV_API_TOKEN, TOKEN_FALSO)
    monkeypatch.setenv(SecretKey.DERIV_APP_ID, APP_ID_FALSO)

    inventario = build_price_sources(timeout_s=42.0)

    assert inventario.disponibles["twelvedata"]._timeout_s == 42.0
    assert inventario.disponibles["deriv"]._timeout_s == 42.0


# ══════════════════════════════════════════════════════════════════════════
#  Ninguna credencial se filtra, por ningún canal
# ══════════════════════════════════════════════════════════════════════════

def test_log_summary_no_expone_valores(sin_secretos, monkeypatch, caplog):
    """Reporta presencia, nunca valores — ni un prefijo ni los últimos
    cuatro caracteres. Un secreto parcial sigue siendo un secreto filtrado,
    y en un log de CI queda para siempre."""
    monkeypatch.setenv(SecretKey.TWELVEDATA_API_KEY, KEY_FALSA)
    monkeypatch.setenv(SecretKey.DERIV_API_TOKEN, TOKEN_FALSO)
    monkeypatch.setenv(SecretKey.DERIV_APP_ID, APP_ID_FALSO)

    with caplog.at_level("INFO", logger="spel.ingestion.sources"):
        build_price_sources().log_summary()

    assert "twelvedata" in caplog.text  # sí reporta la fuente
    assert KEY_FALSA not in caplog.text
    assert TOKEN_FALSO not in caplog.text
    assert APP_ID_FALSO not in caplog.text


def test_el_motivo_nombra_la_variable_de_entorno(sin_secretos):
    """Un 'no disponible' que no dice qué configurar obliga a leer el código
    fuente para averiguarlo."""
    ausentes = build_price_sources().ausentes

    assert SecretKey.TWELVEDATA_API_KEY in ausentes["twelvedata"]
    assert SecretKey.DERIV_API_TOKEN in ausentes["deriv"]
    assert SecretKey.DERIV_APP_ID in ausentes["deriv"]


def test_repr_del_inventario_no_filtra_credenciales(sin_secretos, monkeypatch):
    """`SourceInventory` es un dataclass, así que su repr incluye los
    adapters. Hoy los adapters no son dataclasses y su repr por defecto no
    dice nada — pero convertir uno a dataclass más adelante volcaría la key
    en cualquier traceback o log que imprima el inventario. Este test es el
    que avisa si eso pasa."""
    monkeypatch.setenv(SecretKey.TWELVEDATA_API_KEY, KEY_FALSA)
    monkeypatch.setenv(SecretKey.DERIV_API_TOKEN, TOKEN_FALSO)
    monkeypatch.setenv(SecretKey.DERIV_APP_ID, APP_ID_FALSO)

    texto = repr(build_price_sources())

    assert KEY_FALSA not in texto
    assert TOKEN_FALSO not in texto
    assert APP_ID_FALSO not in texto


# ══════════════════════════════════════════════════════════════════════════
#  Arquitectura — el punto de composición es UNO, verificado con AST
# ══════════════════════════════════════════════════════════════════════════

def test_los_adapters_no_leen_credenciales_por_su_cuenta():
    """El invariante que sostiene todo este módulo: los adapters reciben
    credenciales por constructor y NUNCA las buscan solos. Si
    `ingestion/adapters.py` importara `governance.secrets` u `os.environ`,
    habría dos lugares donde se resuelven credenciales y '¿por qué no
    arrancó tal fuente?' volvería a ser una búsqueda en vez de una lectura.

    Se verifica con AST y no con grep porque grep encuentra la palabra en
    los docstrings —que hablan de esto explícitamente— y daría un falso
    positivo permanente. El AST solo ve imports reales."""
    fuente = Path(inspect.getfile(TwelveDataAdapter)).read_text()
    arbol = ast.parse(fuente)

    importados: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            importados.update(alias.name for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            importados.add(nodo.module)

    assert not any(m.startswith("governance") for m in importados), (
        f"ingestion/adapters.py importa governance.*: {sorted(importados)}. "
        f"Las credenciales se resuelven solo en ingestion/sources.py."
    )
    assert "os" not in importados, (
        "ingestion/adapters.py importa 'os' — si es para leer el entorno, "
        "rompe el punto de composición único."
    )


# ══════════════════════════════════════════════════════════════════════════
#  GUARDIÁN — solo donde el entorno AFIRMA tener secretos configurados
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("SPEL_EXPECT_SECRETS") != "1",
    reason="solo corre donde se afirma que hay secretos inyectados "
           "(SPEL_EXPECT_SECRETS=1, lo pone live-tests.yml)",
)
def test_guardian_los_secretos_declarados_llegaron_de_verdad():
    """Detecta el fallo que ningún test offline puede ver: los secretos
    están cargados en GitHub, pero mal escritos en el `env:` del workflow,
    o el workflow no los inyecta, o el secreto existe con otro nombre.

    Offline la ausencia de credenciales es el estado ESPERADO, así que
    ningún test puede distinguir "no hay secretos porque es un entorno de
    desarrollo" de "hay secretos y no llegaron". Este guardián invierte la
    pregunta: si el entorno afirma que los inyectó, el inventario tiene que
    poder construir algo. Un inventario vacío acá es una falla de
    plomería, no una capacidad ausente.
    """
    inventario = build_price_sources()

    assert len(inventario) > 0, (
        "SPEL_EXPECT_SECRETS=1 pero no se pudo construir ninguna fuente. "
        f"Motivos: {inventario.ausentes}. Revisar que los nombres del "
        f"bloque env: del workflow coincidan con SecretKey."
    )
