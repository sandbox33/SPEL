"""
tests/test_sonda_instrumentos.py
==================================
Cobertura de ingestion/sonda_instrumentos.py contra un Deriv falso. Las
respuestas son SINTÉTICAS, armadas con los nombres de campo de los esquemas
oficiales (deriv-com/deriv-api-docs, config/v3/*/receive.json); ningún
número de acá es una medición.
"""

from __future__ import annotations

import json

import pytest

import governance.persistence as persistence_module
from governance.persistence import DRIVE_ROOT_ENV_VAR
from ingestion.sonda_instrumentos import (
    CONTROL_POSITIVO,
    DIAS_DE_SONDA,
    ORO_PREREGISTRADO,
    Estado,
    dir_instrumentos,
    main,
    seleccionar,
    sondear,
)
from tests.deriv_falso import DerivFalso

EPOCH = 1_790_000_000   # 2026-09-21 en UTC
FECHA = "2026-09-21"


@pytest.fixture(autouse=True)
def _drive_root_temporal(monkeypatch, tmp_path):
    monkeypatch.setenv(DRIVE_ROOT_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)


def _sim(codigo, market, esquema="symbol"):
    return {esquema: codigo, "market": market, "display_name": codigo,
            "submarket": "x", "exchange_is_open": 1, "is_trading_suspended": 0}


ACTIVOS = [
    _sim("frxEURUSD", "forex"),
    _sim("cryBTCUSD", "cryptocurrency"),
    _sim("cryETHUSD", "cryptocurrency"),
    _sim("frxXAUUSD", "commodities"),
    _sim("R_50", "synthetic_index"),
]


def _contratos(multup=True, rango=(1, 2, 5, 10)):
    disp = [{"contract_type": "CALL", "expiry_type": "daily"}]
    if multup:
        for t in ("MULTUP", "MULTDOWN"):
            disp.append({"contract_type": t, "expiry_type": "no_expiry",
                         "start_type": "spot", "min_contract_duration": "0",
                         "max_contract_duration": "0", "multiplier_range": list(rango),
                         "default_stake": 10, "cancellation_range": ["1h"]})
    return {"contracts_for": {"available": disp}}


def _deriv(activos=ACTIVOS, *, contratos=None, stake_min="1.00", comision=0.05):
    contratos = contratos or {}

    def proposal(p):
        return {"proposal": {"ask_price": p["amount"], "commission": comision,
                             "spot": 65000.0, "validation_params": {
                                 "stake": {"min": stake_min, "max": "2000.00"}},
                             "limit_order": {"stop_out": {"value": "64000.0",
                                                          "order_amount": -p["amount"]}}}}

    return DerivFalso({
        "authorize": lambda p: {"authorize": {"is_virtual": 1, "currency": "USD",
                                              "loginid": "VRTC9",
                                              "landing_company_name": "virtual"}},
        "time": lambda p: {"time": EPOCH},
        "active_symbols": lambda p: {"active_symbols": activos},
        "contracts_for": lambda p: contratos.get(p["contracts_for"], _contratos()),
        "proposal": proposal,
    })


async def _sondear(d, **kw):
    kw.setdefault("token", "tok")
    return await sondear("demo", app_id="1", connector=d.connector, **kw)


# ═══ Selección ════════════════════════════════════════════════════════════

def test_un_sintetico_no_entra_aunque_diga_btc():
    """El rechazo que pide el brief. Un sintético con BTC en el nombre y en
    un mercado sintético no puede llegar a medirse."""
    sel = seleccionar(ACTIVOS + [_sim("BTCSYN", "synthetic_index")])
    assert [i["symbol"] for i in sel.btc] == ["cryBTCUSD"]
    assert "BTCSYN" in sel.sinteticos_descartados


@pytest.mark.parametrize("mercado", ["synthetic_index", "basket_index", "derived"])
def test_un_indice_cesta_de_oro_no_entra_con_ningun_nombre_de_mercado(mercado):
    """Deriv tiene índices cesta con XAU en el código. El filtro es por
    mercado EXACTO: un nombre de mercado que este módulo no conoce no abre
    la puerta."""
    sel = seleccionar(ACTIVOS + [_sim("WLDXAU", mercado)])
    assert [i["symbol"] for i in sel.oro] == ["frxXAUUSD"]


def test_acepta_los_dos_esquemas_y_registra_cual_respondio():
    nuevo = [_sim(i["symbol"], i["market"], "underlying_symbol") for i in ACTIVOS]
    assert seleccionar(ACTIVOS).esquemas == ["symbol"]
    sel = seleccionar(nuevo)
    assert sel.esquemas == ["underlying_symbol"]
    assert [i["underlying_symbol"] for i in sel.btc] == ["cryBTCUSD"]


def test_btc_fuera_del_mercado_cripto_no_es_candidato():
    sel = seleccionar([_sim("frxBTCUSD", "forex")])
    assert sel.btc == []


# ═══ La corrida ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_sin_el_control_positivo_la_corrida_es_invalida_y_no_escribe():
    d = _deriv([a for a in ACTIVOS if a["symbol"] != CONTROL_POSITIVO])
    res = await _sondear(d, write=True)
    assert res.estado == Estado.INVALIDO
    assert not dir_instrumentos().exists()
    assert d.de_tipo("contracts_for") == []


@pytest.mark.asyncio
async def test_mide_stake_minimo_y_comision_al_stake_minimo():
    d = _deriv(stake_min="1.00", comision=0.05)
    res = await _sondear(d)
    btc = next(m for m in res.mediciones if m.simbolo == "cryBTCUSD")

    assert res.estado == Estado.OK
    assert btc.multiplicador_usado == 1.0, "el multiplicador más bajo"
    assert (btc.stake_min, btc.stake_max) == (1.0, 2000.0)
    assert btc.comision == 0.05
    montos = [p["amount"] for p in d.de_tipo("proposal") if p["symbol"] == "cryBTCUSD"]
    assert montos == [10, 1.0], "primero al default_stake, después al mínimo"


@pytest.mark.asyncio
async def test_la_moneda_sale_de_la_cuenta_y_sin_token_la_comision_no_se_mide():
    d = _deriv()
    res = await _sondear(d, token=None)
    assert d.de_tipo("proposal") == []
    assert all("no se escribe una a mano" in m.sin_medir for m in res.mediciones)
    assert all(m.contratos.multiplicadores for m in res.mediciones)


@pytest.mark.asyncio
async def test_ninguna_proposal_pide_stream():
    d = _deriv()
    await _sondear(d)
    assert all("subscribe" not in p for p in d.de_tipo("proposal"))


@pytest.mark.asyncio
async def test_mas_de_un_btc_que_califica_termina_en_peticion_al_admin():
    activos = ACTIVOS + [_sim("cryBTCUSDT", "cryptocurrency")]
    res = await _sondear(_deriv(activos), write=True)
    assert res.estado == Estado.PETICION_ADMIN
    assert res.btc_califican == ["cryBTCUSD", "cryBTCUSDT"]
    resumen = json.loads((dir_instrumentos() / f"resumen_{FECHA}.json").read_text())
    assert resumen["estado"] == Estado.PETICION_ADMIN


@pytest.mark.asyncio
async def test_un_btc_sin_multup_no_califica():
    activos = ACTIVOS + [_sim("cryBTCEUR", "cryptocurrency")]
    res = await _sondear(_deriv(activos, contratos={"cryBTCEUR": _contratos(multup=False)}))
    assert res.estado == Estado.OK
    assert res.btc_califican == ["cryBTCUSD"]


@pytest.mark.asyncio
async def test_el_oro_preregistrado_se_verifica_contra_la_api():
    res = await _sondear(_deriv([a for a in ACTIVOS if a["symbol"] != ORO_PREREGISTRADO]))
    assert res.oro_preregistrado_presente is False


# ═══ Salida ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_escribe_un_json_por_simbolo_con_los_sha256_de_cada_respuesta():
    await _sondear(_deriv(), write=True)
    doc = json.loads((dir_instrumentos() / f"cryBTCUSD_{FECHA}.json").read_text())
    assert doc["fecha_servidor"] == FECHA and doc["entorno"] == "demo"
    assert set(doc["sha256"]) == {"active_symbols", "contracts_for",
                                  "proposal_sondeo", "proposal_stake_min"}
    assert set(doc["crudos"]) == {"contracts_for", "proposal_sondeo", "proposal_stake_min"}
    assert "loginid" not in doc["cuenta"]


@pytest.mark.asyncio
async def test_la_fecha_es_la_del_servidor_y_una_segunda_corrida_no_escribe():
    await _sondear(_deriv(), write=True)
    antes = sorted(p.name for p in dir_instrumentos().iterdir())

    d = _deriv()
    res = await _sondear(d, write=True)

    assert res.estado == Estado.YA_MEDIDO_HOY
    assert sorted(p.name for p in dir_instrumentos().iterdir()) == antes
    assert d.de_tipo("active_symbols") == []


@pytest.mark.asyncio
async def test_con_siete_dias_medidos_la_sonda_no_abre_conexion():
    dir_instrumentos().mkdir(parents=True)
    for k in range(DIAS_DE_SONDA):
        (dir_instrumentos() / f"resumen_2026-09-{10 + k}.json").write_text("{}")
    d = _deriv()
    res = await _sondear(d)
    assert res.estado == Estado.COMPLETA
    assert d.conexiones == 0


# ═══ CLI ══════════════════════════════════════════════════════════════════

def test_el_cli_exige_entorno(capsys):
    with pytest.raises(SystemExit):
        main([])


def test_el_cli_sin_app_id_sale_dos(monkeypatch, capsys):
    monkeypatch.delenv("DERIV_APP_ID", raising=False)
    assert main(["--entorno", "demo"]) == 2


def test_el_cli_con_invalido_sale_uno(monkeypatch, capsys):
    monkeypatch.setenv("DERIV_APP_ID", "1")
    monkeypatch.setenv("DERIV_API_TOKEN", "tok")
    d = _deriv([a for a in ACTIVOS if a["symbol"] != CONTROL_POSITIVO])
    assert main(["--entorno", "demo"], connector=d.connector) == 1
    assert "INVALIDO" in capsys.readouterr().out
