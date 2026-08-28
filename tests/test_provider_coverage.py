"""
tests/test_provider_coverage.py
================================
Cobertura de tools/provider_coverage.py. Ningún test toca la red: el
transporte se inyecta con httpx.MockTransport.

Los tests que más importan son los de los tres estados confundibles
(SIN_CLAVE / CLAVE_RECHAZADA / NO_CUBIERTO) y el de RATE_LIMITED. Confundir
cualquiera de esos cuatro lleva a conclusiones opuestas sobre qué hacer:
configurar una clave, rotarla, cambiar de proveedor, o simplemente esperar.
"""

from __future__ import annotations

import httpx
import pytest

import tools.provider_coverage as mod
from governance.secrets import SecretKey
from ingestion.adapters import REQUIRED_COLUMNS
from tools.provider_coverage import (
    CoverageStatus,
    ProbeResult,
    describir_payload,
    main,
    probe_alphavantage,
    probe_deriv,
    probe_provider,
    probe_tiingo,
    probe_twelvedata,
    render_text,
)

CLAVE_FALSA = "clave-de-prueba-que-no-debe-filtrarse"


def cliente_con(payload, status_code: int = 200, captura: list | None = None):
    """AsyncClient cuyo transporte devuelve `payload` sin salir a la red."""
    def handler(request: httpx.Request) -> httpx.Response:
        if captura is not None:
            captura.append(request)
        if payload is None:
            return httpx.Response(status_code, text="no soy json")
        return httpx.Response(status_code, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def sin_secretos(monkeypatch):
    """Entorno sin credenciales, con el tier de Colab neutralizado — si no,
    en una máquina con Drive montado el test dependería de claves reales."""
    import governance.secrets as secrets_mod
    for clave in (SecretKey.TWELVEDATA_API_KEY, SecretKey.ALPHAVANTAGE_API_KEY,
                  SecretKey.TIINGO_API_TOKEN, SecretKey.DERIV_API_TOKEN,
                  SecretKey.DERIV_APP_ID):
        monkeypatch.delenv(clave, raising=False)
    monkeypatch.setattr(secrets_mod, "_try_colab_userdata", lambda key: None)


# ══════════════════════════════════════════════════════════════════════════
#  Los tres estados que se confunden fácil, más el cuarto
# ══════════════════════════════════════════════════════════════════════════

async def test_sin_clave_no_sondea_y_lo_dice(sin_secretos):
    """Clave ausente: no se hace ni una petición. 'Sin medir' no es 'sin
    cobertura'."""
    res = await probe_provider("twelvedata", ["BTCUSD", "NVDA"])

    assert [r.status for r in res] == [CoverageStatus.SIN_CLAVE] * 2
    assert all(SecretKey.TWELVEDATA_API_KEY in r.detail for r in res)
    assert all(r.n_points is None for r in res), "no se midió nada"


async def test_clave_rechazada_no_es_lo_mismo_que_no_cubierto():
    """401 significa 'rotá la credencial', no 'cambiá de proveedor'."""
    async with cliente_con({"code": 401, "message": "Invalid API key",
                            "status": "error"}) as c:
        res = await probe_twelvedata(c, "BTCUSD", CLAVE_FALSA)

    assert res.status == CoverageStatus.CLAVE_RECHAZADA


async def test_no_cubierto_es_clave_valida_sin_el_activo():
    """El proveedor contestó bien y no tiene el símbolo."""
    async with cliente_con({"code": 404, "message": "**symbol** not found",
                            "status": "error"}) as c:
        res = await probe_twelvedata(c, "XAUUSD", CLAVE_FALSA)

    assert res.status == CoverageStatus.NO_CUBIERTO


async def test_respuesta_vacia_es_no_cubierto_no_error():
    """Contestó, simplemente no tenía nada que dar."""
    async with cliente_con({"meta": {}, "values": [], "status": "ok"}) as c:
        res = await probe_twelvedata(c, "XAUUSD", CLAVE_FALSA)

    assert res.status == CoverageStatus.NO_CUBIERTO
    assert res.n_points is None or res.n_points == 0


@pytest.mark.parametrize("payload,code", [
    ({"code": 429, "message": "out of API credits", "status": "error"}, 200),
    ({"cualquier": "cosa"}, 429),
    ({"Information": "Thank you for using Alpha Vantage! Our standard API "
                     "rate limit is 25 requests per day."}, 200),
])
async def test_rate_limited_no_se_confunde_con_sin_cobertura(payload, code):
    """El caso que el brief marca explícitamente: topar la cuota NO es que el
    proveedor no cubra el activo. Se prueba también con HTTP 200 + prosa,
    que es como corta Alpha Vantage."""
    async with cliente_con(payload, status_code=code) as c:
        res = await probe_twelvedata(c, "BTCUSD", CLAVE_FALSA)

    assert res.status == CoverageStatus.RATE_LIMITED


async def test_error_de_transporte_no_tumba_el_sondeo_entero(monkeypatch, sin_secretos):
    """Un fallo de red es de ESA petición, no del proveedor completo."""
    monkeypatch.setenv(SecretKey.TWELVEDATA_API_KEY, CLAVE_FALSA)

    def boom(request):
        raise httpx.ConnectError("sin ruta al host", request=request)

    class ClienteRoto(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            super().__init__(transport=httpx.MockTransport(boom))

    monkeypatch.setattr(httpx, "AsyncClient", ClienteRoto)

    res = await probe_provider("twelvedata", ["BTCUSD", "NVDA"], min_interval_s=0)

    assert len(res) == 2, "sondeó los dos, no cortó en el primero"
    assert all(r.status == CoverageStatus.ERROR for r in res)


async def test_cuerpo_no_json_es_error_no_cobertura():
    async with cliente_con(None, status_code=200) as c:
        res = await probe_twelvedata(c, "BTCUSD", CLAVE_FALSA)

    assert res.status == CoverageStatus.ERROR
    assert "no-JSON" in res.detail


# ══════════════════════════════════════════════════════════════════════════
#  Extracción de esquema — contra el contrato REAL del repo
# ══════════════════════════════════════════════════════════════════════════

def test_el_contrato_de_referencia_es_el_del_repo():
    """El reporte se evalúa contra REQUIRED_COLUMNS de ingestion/adapters.py,
    no contra una lista inventada acá."""
    assert mod.REQUIRED_COLUMNS is REQUIRED_COLUMNS


def test_esquema_de_lista_de_dicts_estilo_twelvedata():
    desc = describir_payload({"meta": {"symbol": "EUR/USD"}, "values": [
        {"datetime": "2026-08-21", "open": "1.1", "high": "1.2",
         "low": "1.0", "close": "1.15"},
        {"datetime": "2026-08-22", "open": "1.15", "high": "1.2",
         "low": "1.1", "close": "1.18"},
    ], "status": "ok"})

    assert desc["n_points"] == 2
    assert desc["first_date"] == "2026-08-21"
    assert desc["last_date"] == "2026-08-22"
    assert desc["has_volume"] is False
    assert desc["missing_from_contract"] == ["volume"]


def test_esquema_de_dict_indexado_por_fecha_estilo_alphavantage():
    """Alpha Vantage numera sus campos ('1. open'); el nombre canónico se
    detecta como sufijo para no reportar como faltante algo que sí vino."""
    desc = describir_payload({"Time Series (Daily)": {
        "2026-08-21": {"1. open": "800", "2. high": "810", "3. low": "795",
                       "4. close": "805", "5. adjusted close": "805",
                       "6. volume": "1000", "8. split coefficient": "1.0"},
        "2026-08-22": {"1. open": "805", "2. high": "815", "3. low": "800",
                       "4. close": "812", "5. adjusted close": "812",
                       "6. volume": "1200", "8. split coefficient": "1.0"},
    }})

    assert desc["n_points"] == 2
    assert desc["has_volume"] is True
    assert desc["has_adjusted_close"] is True
    assert desc["missing_from_contract"] == []


def test_esquema_de_lista_plana_estilo_tiingo():
    desc = describir_payload([
        {"date": "2026-08-21", "open": 800.0, "high": 810.0, "low": 795.0,
         "close": 805.0, "volume": 1000, "adjClose": 805.0},
    ])

    assert desc["n_points"] == 1
    assert desc["has_volume"] is True
    assert desc["has_adjusted_close"] is True
    assert desc["missing_from_contract"] == []


def test_serie_anidada_estilo_tiingo_cripto():
    desc = describir_payload([
        {"ticker": "btcusd", "priceData": [
            {"date": "2026-08-21", "open": 60000, "high": 61000,
             "low": 59000, "close": 60500, "volume": 12.5},
        ]},
    ])

    assert desc["n_points"] == 1
    assert desc["has_volume"] is True


def test_serie_sin_ohlc_reporta_lo_que_falta_del_contrato():
    """El caso de GOLD_SILVER_HISTORY: solo date+price. El reporte tiene que
    decir exactamente qué del contrato no vino."""
    desc = describir_payload({"data": [
        {"date": "2026-08-21", "price": 2400.5},
        {"date": "2026-08-22", "price": 2415.0},
    ]})

    assert desc["n_points"] == 2
    assert set(desc["missing_from_contract"]) == {"open", "high", "low",
                                                  "close", "volume"}
    assert desc["has_volume"] is False


def test_payload_sin_serie_reconocible_no_lanza():
    desc = describir_payload({"mensaje": "nada por acá"})

    assert desc["n_points"] == 0
    assert desc["columns_returned"] == []


# ══════════════════════════════════════════════════════════════════════════
#  Autenticación — dónde viaja cada credencial
# ══════════════════════════════════════════════════════════════════════════

async def test_twelvedata_manda_la_clave_en_header_nunca_en_la_url():
    """Reusa la convención del TwelveDataAdapter ya auditado: un ?apikey=
    termina en logs de proxy y en los mensajes de error de httpx."""
    captura: list[httpx.Request] = []
    async with cliente_con({"values": [{"datetime": "2026-08-21", "open": "1",
                                        "high": "1", "low": "1", "close": "1"}]},
                           captura=captura) as c:
        await probe_twelvedata(c, "EURUSD", CLAVE_FALSA)

    req = captura[0]
    assert req.headers["Authorization"] == f"apikey {CLAVE_FALSA}"
    assert CLAVE_FALSA not in str(req.url)


async def test_tiingo_manda_la_clave_en_header():
    captura: list[httpx.Request] = []
    async with cliente_con([{"date": "2026-08-21", "close": 1.0}],
                           captura=captura) as c:
        await probe_tiingo(c, "NVDA", CLAVE_FALSA)

    assert captura[0].headers["Authorization"] == f"Token {CLAVE_FALSA}"
    assert CLAVE_FALSA not in str(captura[0].url)


async def test_alphavantage_no_publica_la_url_completa_en_el_reporte():
    """La API de Alpha Vantage no ofrece auth por header — la clave va como
    query param por limitación del proveedor. Por eso el endpoint del reporte
    no puede llevar la petición completa."""
    async with cliente_con({"Time Series (Daily)": {
        "2026-08-21": {"1. open": "1", "4. close": "1"}}}) as c:
        res = await probe_alphavantage(c, "NVDA", CLAVE_FALSA)

    assert CLAVE_FALSA not in (res.endpoint or "")
    assert CLAVE_FALSA not in res.detail
    assert "function=TIME_SERIES_DAILY_ADJUSTED" in res.endpoint


async def test_ninguna_credencial_aparece_en_el_reporte_renderizado():
    """Barrido: nada de lo que se imprime puede llevar la clave."""
    resultados = []
    for payload, code in (
        ({"code": 401, "message": "Invalid API key", "status": "error"}, 200),
        ({"code": 429, "message": "out of credits", "status": "error"}, 200),
        ({"values": [{"datetime": "2026-08-21", "open": "1", "high": "1",
                      "low": "1", "close": "1", "volume": "5"}]}, 200),
    ):
        async with cliente_con(payload, status_code=code) as c:
            resultados.append(await probe_twelvedata(c, "EURUSD", CLAVE_FALSA))

    texto = render_text(resultados)
    assert CLAVE_FALSA not in texto


# ══════════════════════════════════════════════════════════════════════════
#  Deriv — el adapter del repo, sin reimplementar el handshake
# ══════════════════════════════════════════════════════════════════════════

async def test_deriv_simbolo_no_mapeado_es_sin_ruta_y_no_toca_la_red():
    """DerivAdapter rechaza un símbolo fuera de su mapa ANTES de conectarse.
    Eso no es 'el proveedor no lo cubre' — la API nunca dijo nada. Se reporta
    SIN_RUTA para no afirmar algo que no se preguntó."""
    res = await probe_deriv("NVDA", "token-falso", "app-id-falso")

    assert res.status == CoverageStatus.SIN_RUTA
    assert res.adapter_mapped is False
    assert "no está en el mapa verificado" in res.detail
    assert res.n_points is None


async def test_deriv_simbolo_mapeado_reporta_cobertura_real(monkeypatch):
    """Con un símbolo mapeado sí se sondea, vía el adapter."""
    import pandas as pd
    from ingestion.adapters import DerivAdapter

    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-08-01", periods=3, freq="1D", tz="UTC"),
        "open": [1.1] * 3, "high": [1.2] * 3, "low": [1.0] * 3,
        "close": [1.15] * 3, "volume": [0.0] * 3,
    })
    df.attrs["volume_available"] = False

    async def fake_fetch(self, symbol, timeframe, limit):
        return df

    monkeypatch.setattr(DerivAdapter, "fetch_ohlcv", fake_fetch)

    res = await probe_deriv("EURUSD", "token-falso", "app-id-falso")

    assert res.status == CoverageStatus.CUBIERTO
    assert res.adapter_mapped is True
    assert res.n_points == 3
    # Se lee la bandera de attrs, no la presencia de la columna: el adapter
    # rellena `volume` con 0.0 y la columna siempre está.
    assert res.has_volume is False
    assert res.missing_from_contract == []


async def test_deriv_sin_app_id_es_sin_clave(sin_secretos, monkeypatch):
    """Deriv necesita token Y app_id; con uno solo no se sondea."""
    monkeypatch.setenv(SecretKey.DERIV_API_TOKEN, "token-falso")

    res = await probe_provider("deriv", ["EURUSD"], min_interval_s=0)

    assert res[0].status == CoverageStatus.SIN_CLAVE
    assert SecretKey.DERIV_APP_ID in res[0].detail


# ══════════════════════════════════════════════════════════════════════════
#  Rate limiting — en serie y espaciado
# ══════════════════════════════════════════════════════════════════════════

async def test_las_peticiones_van_en_serie_y_espaciadas(monkeypatch, sin_secretos):
    """Paralelizar 6 peticiones contra un plan de 8/minuto garantiza topar la
    cuota, y un RATE_LIMITED se lee igual que un NO_CUBIERTO si uno no mira
    el estado."""
    monkeypatch.setenv(SecretKey.TWELVEDATA_API_KEY, CLAVE_FALSA)
    esperas: list[float] = []

    async def fake_sleep(s):
        esperas.append(s)

    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)

    orden: list[str] = []

    def handler(request):
        orden.append(str(request.url))
        return httpx.Response(200, json={"values": [
            {"datetime": "2026-08-21", "open": "1", "high": "1",
             "low": "1", "close": "1"}]})

    class ClienteFake(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            super().__init__(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "AsyncClient", ClienteFake)

    await probe_provider("twelvedata", ["BTCUSD", "EURUSD", "NVDA"])

    assert len(orden) == 3
    # Un sleep entre peticiones: n-1 esperas para n activos.
    assert len(esperas) == 2
    assert all(s == mod.PROVIDERS["twelvedata"].min_interval_s for s in esperas)


def test_cada_proveedor_documenta_su_limite():
    """El límite documentado tiene que estar y llegar al reporte — sin él,
    un RATE_LIMITED no se puede interpretar."""
    for nombre, spec in mod.PROVIDERS.items():
        assert spec.rate_limit_doc, f"{nombre} sin límite documentado"
        assert spec.min_interval_s > 0


# ══════════════════════════════════════════════════════════════════════════
#  Contrato del proceso
# ══════════════════════════════════════════════════════════════════════════

def test_exit_0_aunque_no_haya_ninguna_cobertura(sin_secretos, capsys):
    """'Sin cobertura' es un resultado, no un fallo del proceso."""
    codigo = main(["--assets", "BTCUSD", "--providers", "twelvedata"])

    salida = capsys.readouterr().out
    assert codigo == 0
    assert CoverageStatus.SIN_CLAVE in salida


def test_exit_2_para_fallo_real_de_invocacion(sin_secretos, capsys):
    codigo = main(["--assets", "  ", "--providers", "twelvedata"])

    assert codigo == 2
    assert "ERROR" in capsys.readouterr().err


def test_proveedor_desconocido_lo_rechaza_argparse(sin_secretos):
    with pytest.raises(SystemExit):
        main(["--providers", "no_existe"])


def test_el_resumen_distingue_sin_medir_de_sin_cobertura(sin_secretos, capsys):
    main(["--assets", "BTCUSD", "--providers", "twelvedata", "tiingo"])

    salida = capsys.readouterr().out
    assert "NO es 'sin cobertura'" in salida
    assert "sin medir" in salida


def test_salida_json_parseable_con_contrato_y_limites(sin_secretos, capsys):
    import json
    main(["--assets", "BTCUSD", "--providers", "twelvedata", "--format", "json"])

    reporte = json.loads(capsys.readouterr().out)
    assert reporte["contract"] == list(REQUIRED_COLUMNS)
    assert reporte["providers"]["twelvedata"]["rate_limit_doc"]
    assert reporte["results"][0]["status"] == CoverageStatus.SIN_CLAVE


def test_el_tool_no_escribe_ningun_archivo(sin_secretos, tmp_path, monkeypatch):
    """Read-only de verdad: se fotografía el árbol antes y después."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marca.txt").write_text("x")

    def foto():
        return {p: p.stat().st_mtime_ns for p in sorted(tmp_path.rglob("*"))}

    antes = foto()
    assert main(["--assets", "BTCUSD", "--providers", "twelvedata"]) == 0
    assert foto() == antes


# ══════════════════════════════════════════════════════════════════════════
#  Reglas duras del proyecto
# ══════════════════════════════════════════════════════════════════════════

def test_no_importa_archive_ni_librerias_prohibidas():
    import ast
    import inspect
    from pathlib import Path

    arbol = ast.parse(Path(inspect.getfile(mod)).read_text())
    importados: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            importados.update(a.name for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            importados.add(nodo.module)

    raices = {m.split(".")[0] for m in importados}
    assert not ({"torch", "sklearn", "yfinance", "requests"} & raices), importados
    assert not any(m.startswith("archive") for m in importados), importados
    # httpx async, nunca requests (regla dura del proyecto).
    assert "requests" not in raices


def test_no_hay_ninguna_tabla_de_cobertura_declarada():
    """Las rutas del módulo son CANDIDATAS a probar, no cobertura afirmada.
    Un ProbeResult solo puede nacer CUBIERTO desde una respuesta real: se
    verifica que el default del dataclass no lo sea."""
    r = ProbeResult(provider="x", asset="Y", status=CoverageStatus.SIN_RUTA)

    assert r.n_points is None
    assert r.has_volume is None
    assert r.first_date is None
    assert r.columns_returned == []
