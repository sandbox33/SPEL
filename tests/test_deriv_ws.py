"""
tests/test_deriv_ws.py
========================
Cobertura de ingestion/deriv_ws.py: el entorno obligatorio, la lista blanca
de mensajes, y el test de contrato del Entregable 4 -- con `demo` el flujo
corre; con `real` y sin el permiso explícito falla ANTES de abrir la
conexión.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path

import pytest

from ingestion.adapters import AdapterAuthError, AdapterDataError
from ingestion.deriv_ws import (
    MENSAJES_PERMITIDOS,
    PERMISO_REAL_ENV_VAR,
    EntornoNoCoincideError,
    EntornoNoPermitidoError,
    MensajeNoPermitidoError,
    abrir_sesion,
)
from tests.deriv_falso import DerivFalso

RAIZ = Path(__file__).resolve().parent.parent


def _deriv(is_virtual=1):
    return DerivFalso({
        "authorize": lambda p: {"authorize": {"is_virtual": is_virtual,
                                              "currency": "USD",
                                              "loginid": "VRTC1"}},
        "time": lambda p: {"time": 1_790_000_000},
        "ping": lambda p: {"ping": "pong"},
    })


@pytest.fixture(autouse=True)
def _sin_permiso_real(monkeypatch):
    monkeypatch.delenv(PERMISO_REAL_ENV_VAR, raising=False)


# ═══ El contrato del Entregable 4 ═════════════════════════════════════════

@pytest.mark.asyncio
async def test_con_demo_el_flujo_corre():
    d = _deriv()
    async with abrir_sesion("demo", app_id="1", token="tok", connector=d.connector) as s:
        r = await s.pedir({"time": 1})
    assert r.datos["time"] == 1_790_000_000
    assert d.conexiones == 1


def test_con_real_sin_permiso_falla_antes_de_abrir_la_conexion():
    d = _deriv(is_virtual=0)
    with pytest.raises(EntornoNoPermitidoError, match=PERMISO_REAL_ENV_VAR):
        abrir_sesion("real", app_id="1", token="tok", connector=d.connector)
    assert d.conexiones == 0
    assert d.enviados == []


@pytest.mark.asyncio
async def test_con_real_y_el_permiso_explicito_corre(monkeypatch):
    monkeypatch.setenv(PERMISO_REAL_ENV_VAR, "1")
    d = _deriv(is_virtual=0)
    async with abrir_sesion("real", app_id="1", token="tok", connector=d.connector) as s:
        await s.pedir({"ping": 1})
    assert d.conexiones == 1


@pytest.mark.parametrize("valor", ["true", "yes", "si", "0", ""])
def test_el_permiso_es_exactamente_uno(monkeypatch, valor):
    monkeypatch.setenv(PERMISO_REAL_ENV_VAR, valor)
    with pytest.raises(EntornoNoPermitidoError):
        abrir_sesion("real", app_id="1", token="tok")


@pytest.mark.parametrize("entorno", ["Demo", "REAL", "", "prueba", None])
def test_un_entorno_desconocido_no_se_acepta(entorno):
    with pytest.raises(EntornoNoPermitidoError):
        abrir_sesion(entorno, app_id="1")


def test_el_entorno_no_tiene_default():
    p = inspect.signature(abrir_sesion).parameters["entorno"]
    assert p.default is inspect.Parameter.empty
    assert p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD


# ═══ El token decide la cuenta: se verifica contra el entorno ═════════════

@pytest.mark.asyncio
async def test_un_token_de_cuenta_real_con_entorno_demo_corta_la_sesion():
    """En la API legacy el endpoint es uno solo: el token es lo que elige la
    cuenta, así que es lo único contra lo que el entorno se puede comprobar."""
    d = _deriv(is_virtual=0)
    with pytest.raises(EntornoNoCoincideError):
        async with abrir_sesion("demo", app_id="1", token="tok", connector=d.connector):
            pass
    assert d.de_tipo("time") == []


@pytest.mark.asyncio
async def test_sin_token_no_se_llama_a_authorize():
    """Los siete mensajes son públicos (auth_required: 0). Un authorize con
    un token vacío fue la causa del 401 que registra provider_coverage."""
    d = _deriv()
    async with abrir_sesion("demo", app_id="1", connector=d.connector) as s:
        await s.pedir({"time": 1})
    assert d.de_tipo("authorize") == []


@pytest.mark.asyncio
async def test_un_error_de_authorize_no_filtra_el_token():
    d = DerivFalso({"authorize": lambda p: {"error": {"code": "InvalidToken",
                                                      "message": "token inválido"}}})
    with pytest.raises(AdapterAuthError) as exc:
        async with abrir_sesion("demo", app_id="1", token="SECRETO-123",
                                connector=d.connector):
            pass
    assert "SECRETO-123" not in str(exc.value)


# ═══ Lista blanca ═════════════════════════════════════════════════════════

def test_la_lista_blanca_es_la_del_brief():
    assert MENSAJES_PERMITIDOS == {"active_symbols", "contracts_for", "proposal",
                                   "ticks_history", "time", "authorize", "ping"}


@pytest.mark.asyncio
@pytest.mark.parametrize("mensaje", ["buy", "sell", "cancel", "portfolio",
                                     "transaction", "proposal_open_contract"])
async def test_un_mensaje_fuera_de_la_lista_no_se_envia(mensaje):
    d = _deriv()
    async with abrir_sesion("demo", app_id="1", connector=d.connector) as s:
        with pytest.raises(MensajeNoPermitidoError):
            await s.pedir({mensaje: 1})
    assert d.enviados == []


@pytest.mark.asyncio
@pytest.mark.parametrize("subscribe", [0, 1])
async def test_proposal_con_subscribe_no_se_envia(subscribe):
    """El esquema oficial solo admite subscribe=1; sin el campo es una
    cotización. Cualquier valor presente se rechaza."""
    d = _deriv()
    async with abrir_sesion("demo", app_id="1", connector=d.connector) as s:
        with pytest.raises(MensajeNoPermitidoError):
            await s.pedir({"proposal": 1, "subscribe": subscribe})
    assert d.enviados == []


def test_ningun_mensaje_de_orden_aparece_como_literal_en_el_codigo():
    """Por AST, no con grep: los docstrings pueden nombrarlos, un literal
    no. Se recorre todo lo que corre, execution/ incluido -- se lee, no se
    toca."""
    prohibidos = {"buy", "sell", "cancel"}
    hallados = []
    for paq in ("core", "ingestion", "orchestration", "governance",
                "execution", "tools", "research"):
        for py in sorted((RAIZ / paq).rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            arbol = ast.parse(py.read_text(encoding="utf-8"))
            docstrings = {
                id(n.body[0].value) for n in ast.walk(arbol)
                if isinstance(n, (ast.Module, ast.FunctionDef,
                                  ast.AsyncFunctionDef, ast.ClassDef))
                and n.body and isinstance(n.body[0], ast.Expr)
                and isinstance(n.body[0].value, ast.Constant)
            }
            for n in ast.walk(arbol):
                if (isinstance(n, ast.Constant) and isinstance(n.value, str)
                        and id(n) not in docstrings
                        and n.value.strip().lower() in prohibidos):
                    hallados.append(f"{py.relative_to(RAIZ)}:{n.lineno} {n.value!r}")
    assert not hallados, "mensajes de orden en el código:\n" + "\n".join(hallados)


# ═══ Respuestas ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cada_respuesta_lleva_el_sha256_de_su_texto_crudo():
    d = _deriv()
    async with abrir_sesion("demo", app_id="1", connector=d.connector) as s:
        r = await s.pedir({"time": 1})
    assert r.sha256 == hashlib.sha256(r.crudo.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_una_respuesta_desparejada_se_rechaza():
    d = DerivFalso({"time": lambda p: {"time": 1, "req_id": 999}})
    async with abrir_sesion("demo", app_id="1", connector=d.connector) as s:
        with pytest.raises(AdapterDataError, match="desparejada"):
            await s.pedir({"time": 1})
