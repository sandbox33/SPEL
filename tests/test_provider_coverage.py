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

import json

import httpx
import pytest

import tools.provider_coverage as mod
from governance.secrets import SecretKey
from ingestion.adapters import REQUIRED_COLUMNS
from ingestion.source_registry import DepthKind
from tools.provider_coverage import (
    CoverageStatus,
    ProbeResult,
    clasificar_profundidad,
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


# ── Doble de prueba del WebSocket de Deriv ────────────────────────────────
# Mismo idiom que el FakeWebSocket de tests/test_adapters.py: se inyecta el
# conector, no se monkeypatchea la librería `websockets` por dentro.

class _FakeDerivWS:
    """Devuelve una respuesta por cada `recv()`, en orden, y guarda lo
    enviado para poder assertar QUÉ se pidió — que es justamente lo que este
    PR necesita verificar (que `end` retrocede, que no hay authorize)."""

    def __init__(self, respuestas: list[dict]):
        self._respuestas = list(respuestas)
        self.enviados: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, payload: str) -> None:
        self.enviados.append(payload)

    async def recv(self) -> str:
        if not self._respuestas:
            return json.dumps({"candles": []})
        return json.dumps(self._respuestas.pop(0))


def _conector(ws: _FakeDerivWS):
    return lambda uri: ws


def _pagina(epoch_mas_viejo: int, cuantas: int) -> dict:
    """Una página de velas de Deriv, con epochs contiguos hacia adelante
    desde `epoch_mas_viejo`."""
    return {"candles": [
        {"epoch": epoch_mas_viejo + i, "open": 1.1, "high": 1.2,
         "low": 1.0, "close": 1.15}
        for i in range(cuantas)
    ]}


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
    """Un símbolo fuera del mapa verificado no se sondea. Eso no es 'el
    proveedor no lo cubre' — la API nunca dijo nada."""
    res = await probe_deriv("NVDA", "app-id-falso")

    assert res.status == CoverageStatus.SIN_RUTA
    assert res.adapter_mapped is False
    assert "no está en el mapa verificado" in res.detail
    assert res.n_points is None


async def test_deriv_no_llama_a_authorize():
    """LA CORRECCIÓN DEL 401. `ticks_history` está documentado como "No auth"
    y solo necesita el app_id de la URI. La versión anterior pasaba por
    DerivAdapter, que SIEMPRE autoriza, y ese authorize con token ausente era
    la causa del 401 observado."""
    ws = _FakeDerivWS([_pagina(1, 10)])

    await probe_deriv("EURUSD", "app-id-falso", connector=_conector(ws))

    enviados = [m for m in ws.enviados]
    assert not any("authorize" in m for m in enviados), enviados
    assert all("ticks_history" in m for m in enviados)


async def test_deriv_pagina_hacia_atras_hasta_que_la_api_se_queda_sin_datos():
    """El tope es de 5.000 velas por petición, así que llegar al fondo exige
    paginar con `end`. Una página con menos de 5.000 significa fondo."""
    ws = _FakeDerivWS([
        _pagina(30_000, mod.DERIV_MAX_COUNT),   # llena -> hay que seguir
        _pagina(20_000, mod.DERIV_MAX_COUNT),   # llena -> hay que seguir
        _pagina(10_000, 42),                    # corta -> fondo
    ])

    res = await probe_deriv("EURUSD", "app-id-falso", connector=_conector(ws))

    assert res.status == CoverageStatus.CUBIERTO
    assert res.pages_fetched == 3
    assert res.requests_used == 3
    assert res.n_points == mod.DERIV_MAX_COUNT * 2 + 42
    assert res.provider_truncated is False
    assert res.depth_kind == DepthKind.COMPLETA
    assert "fondo" in res.detail


async def test_deriv_retrocede_de_verdad_en_cada_pagina():
    """Cada petición tiene que pedir hacia ATRÁS de la anterior. Si `end` no
    retrocediera, se pediría la misma página para siempre."""
    ws = _FakeDerivWS([
        _pagina(30_000, mod.DERIV_MAX_COUNT),
        _pagina(20_000, 5),
    ])

    await probe_deriv("EURUSD", "app-id-falso", connector=_conector(ws))

    ends = [json.loads(m)["end"] for m in ws.enviados]
    assert ends[0] == "latest"
    assert ends[1] == 30_000 - 1, "la 2ª página arranca justo antes de la 1ª"


async def test_deriv_topa_en_max_pages_y_lo_dice_como_piso():
    """Si se acaban las páginas antes que los datos, lo reportado es un PISO.
    Decir 'esto es todo' ahí sería el error que el tool existe para evitar."""
    ws = _FakeDerivWS([_pagina(50_000 - i * 5000, mod.DERIV_MAX_COUNT)
                       for i in range(10)])

    res = await probe_deriv("EURUSD", "app-id-falso",
                            max_pages=3, connector=_conector(ws))

    assert res.pages_fetched == 3
    assert res.provider_truncated is True
    assert res.depth_kind == DepthKind.TOPE_DE_PETICION
    assert "SIN llegar al fondo" in res.detail
    assert "--deriv-max-pages" in res.detail


async def test_deriv_pagina_vacia_es_fondo_no_error():
    ws = _FakeDerivWS([_pagina(30_000, mod.DERIV_MAX_COUNT), {"candles": []}])

    res = await probe_deriv("EURUSD", "app-id-falso", connector=_conector(ws))

    assert res.status == CoverageStatus.CUBIERTO
    assert res.provider_truncated is False
    assert "fondo" in res.detail


async def test_deriv_sin_progreso_corta_en_vez_de_colgarse():
    """Si la API deja de retroceder, seguir pidiendo repetiría la misma
    página para siempre."""
    ws = _FakeDerivWS([_pagina(30_000, mod.DERIV_MAX_COUNT)] * 6)

    res = await probe_deriv("EURUSD", "app-id-falso", connector=_conector(ws))

    assert res.pages_fetched == 2, "cortó apenas detectó que no avanzaba"
    assert "dejó de retroceder" in res.detail


async def test_deriv_error_de_auth_se_reporta_como_clave_rechazada():
    ws = _FakeDerivWS([{"error": {"code": "InvalidAppID", "message": "bad app_id"}}])

    res = await probe_deriv("EURUSD", "app-id-falso", connector=_conector(ws))

    assert res.status == CoverageStatus.CLAVE_RECHAZADA
    assert "InvalidAppID" in res.detail


async def test_deriv_reporta_que_no_trae_volumen():
    """Deriv entrega open/high/low/close/epoch y nunca volumen."""
    ws = _FakeDerivWS([_pagina(1, 10)])

    res = await probe_deriv("EURUSD", "app-id-falso", connector=_conector(ws))

    assert res.has_volume is False
    assert res.missing_from_contract == ["volume"]


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


# ══════════════════════════════════════════════════════════════════════════
#  PROFUNDIDAD — las tres cosas que el reporte anterior confundía
#
#  La versión previa mandaba `outputsize: 30` fijo, así que los seis activos
#  de TwelveData salieron con 30 puntos: no porque el proveedor tuviera 30,
#  sino porque se pidieron 30. El rango reportado era la ventana del tool.
# ══════════════════════════════════════════════════════════════════════════

def test_recibir_exactamente_lo_pedido_es_un_tope_no_la_historia():
    """Pedir 5.000 y recibir 5.000 exactos no dice cuánto hay: dice cuánto
    deja pedir."""
    kind, truncado, nota = clasificar_profundidad(5000, points_requested=5000)

    assert kind == DepthKind.TOPE_DE_PETICION
    assert truncado is True
    assert "PISO" in nota


def test_recibir_menos_de_lo_pedido_significa_que_el_tope_no_fue_el_limite():
    kind, truncado, nota = clasificar_profundidad(1200, points_requested=5000)

    assert kind == DepthKind.COMPLETA
    assert truncado is False


def test_completa_no_se_afirma_como_garantia():
    """'El tope no fue el límite' NO es 'esta es toda la historia': puede ser
    un tope del PLAN, invisible en la respuesta."""
    _, _, nota = clasificar_profundidad(1200, points_requested=5000)

    assert "PLAN" in nota
    assert "otra petición" in nota


def test_sin_conteo_ni_tope_conocido_es_no_medida():
    """El brief es explícito: si no se puede saber sin agotar la cuota, se
    reporta como no medible en vez de estimarlo."""
    kind, truncado, nota = clasificar_profundidad(
        3000, points_requested=None, tope_observado=None)

    assert kind == DepthKind.NO_MEDIDA
    assert truncado is None
    assert "no medible" in nota.lower()


def test_el_tope_observado_detecta_truncamiento_sin_conteo():
    """Alpha Vantage no acepta un conteo, así que la única forma de detectar
    que cortó es reconocer su tope. FX_DAILY devolvió exactamente 5.000."""
    kind, truncado, _ = clasificar_profundidad(
        5000, points_requested=None, tope_observado=5000)

    assert kind == DepthKind.TOPE_DE_PETICION
    assert truncado is True


def test_el_vocabulario_de_profundidad_es_el_del_registro():
    """Compartido con ingestion/source_registry.py, no uno nuevo: el registro
    se actualiza a mano con esta salida y dos vocabularios divergen."""
    assert mod.DepthKind is DepthKind


async def test_twelvedata_pide_el_maximo_no_una_ventana_fija():
    """El bug original: `outputsize: 30` fijo."""
    captura: list[httpx.Request] = []
    async with cliente_con({"values": [
        {"datetime": "2026-08-21", "open": "1", "high": "1",
         "low": "1", "close": "1"}]}, captura=captura) as c:
        res = await probe_twelvedata(c, "EURUSD", CLAVE_FALSA)

    assert captura[0].url.params["outputsize"] == str(mod.TWELVEDATA_MAX_OUTPUTSIZE)
    assert res.points_requested == mod.TWELVEDATA_MAX_OUTPUTSIZE
    assert res.window_requested is not None
    assert res.requests_used == 1


async def test_twelvedata_con_5000_exactos_reporta_tope():
    valores = [{"datetime": f"2026-01-{(i % 28) + 1:02d}", "open": "1",
                "high": "1", "low": "1", "close": "1"}
               for i in range(mod.TWELVEDATA_MAX_OUTPUTSIZE)]
    async with cliente_con({"values": valores}) as c:
        res = await probe_twelvedata(c, "EURUSD", CLAVE_FALSA)

    assert res.n_points == mod.TWELVEDATA_MAX_OUTPUTSIZE
    assert res.provider_truncated is True
    assert res.depth_kind == DepthKind.TOPE_DE_PETICION


async def test_alphavantage_pide_outputsize_full_no_compact():
    """`compact` devuelve 100 puntos y el rango volvería a ser la ventana
    del tool."""
    captura: list[httpx.Request] = []
    async with cliente_con({"Time Series (Daily)": {
        "2026-08-21": {"1. open": "1", "4. close": "1"}}}, captura=captura) as c:
        await probe_alphavantage(c, "NVDA", CLAVE_FALSA)

    assert captura[0].url.params["outputsize"] == "full"


async def test_alphavantage_fx_con_5000_filas_se_marca_como_tope():
    """El tope observado de FX_DAILY. 5.000 es demasiado redondo para ser el
    fondo del archivo."""
    from datetime import date as _date, timedelta as _td
    inicio = _date(2007, 6, 29)
    serie = {(inicio + _td(days=i)).isoformat():
             {"1. open": "1", "2. high": "1", "3. low": "1", "4. close": "1"}
             for i in range(5000)}
    async with cliente_con({"Time Series FX (Daily)": serie}) as c:
        res = await probe_alphavantage(c, "EURUSD", CLAVE_FALSA)

    assert res.depth_kind == DepthKind.TOPE_DE_PETICION
    assert res.provider_truncated is True


async def test_tiingo_pide_desde_una_fecha_anterior_a_cualquier_serie():
    """Tiingo no acepta conteo: acepta startDate. Se pide desde tan atrás que
    el límite lo ponga el proveedor y no la petición."""
    captura: list[httpx.Request] = []
    async with cliente_con([{"date": "2026-08-21", "close": 1.0}],
                           captura=captura) as c:
        res = await probe_tiingo(c, "NVDA", CLAVE_FALSA)

    assert captura[0].url.params["startDate"] == mod.TIINGO_START_DATE
    assert res.points_requested is None, "no expresa la profundidad como conteo"


# ══════════════════════════════════════════════════════════════════════════
#  Cuota — medir profundidad gasta más que sondear existencia
# ══════════════════════════════════════════════════════════════════════════

def test_cada_proveedor_documenta_cuanto_gasta_por_activo():
    for nombre, spec in mod.PROVIDERS.items():
        assert spec.requests_per_asset, f"{nombre} sin costo documentado"


def test_el_costo_de_alphavantage_nombra_su_cuota_diaria():
    """25/día es el cuello real: seis activos son 6 de 25."""
    assert "25" in mod.PROVIDERS["alphavantage"].requests_per_asset


def test_el_reporte_suma_las_peticiones_consumidas(sin_secretos, capsys):
    # sync: main() usa asyncio.run() por dentro y no puede correr dentro de
    # un loop ya activo (asyncio_mode=auto haría async este test).
    main(["--assets", "BTCUSD", "--providers", "twelvedata"])

    assert "Peticiones consumidas" in capsys.readouterr().out


async def test_deriv_declara_su_credencial_real(sin_secretos, capsys):
    """La corrección del gate: Deriv necesita app_id, NO el token."""
    assert mod.PROVIDERS["deriv"].secret_key == SecretKey.DERIV_APP_ID

    res = await probe_provider("deriv", ["EURUSD"], min_interval_s=0)

    assert res[0].status == CoverageStatus.SIN_CLAVE
    assert SecretKey.DERIV_APP_ID in res[0].detail
    assert "NO necesita" in res[0].detail


async def test_deriv_se_sondea_con_solo_app_id(sin_secretos, monkeypatch):
    """Sin token, y aun así se sondea: antes esto devolvía SIN_CLAVE para los
    seis activos sin llegar a mirar el app_id."""
    monkeypatch.setenv(SecretKey.DERIV_APP_ID, "app-id-falso")
    monkeypatch.delenv(SecretKey.DERIV_API_TOKEN, raising=False)

    llamadas = []

    async def fake_probe(asset, app_id, *, max_pages=12, connector=None):
        llamadas.append((asset, app_id))
        return ProbeResult("deriv", asset, CoverageStatus.CUBIERTO)

    monkeypatch.setattr(mod, "probe_deriv", fake_probe)

    res = await probe_provider("deriv", ["EURUSD", "GBPUSD"], min_interval_s=0)

    assert len(llamadas) == 2, "se sondeó sin token"
    assert [r.status for r in res] == [CoverageStatus.CUBIERTO] * 2


def test_deriv_max_pages_invalido_es_fallo_de_invocacion(sin_secretos, capsys):
    codigo = main(["--assets", "EURUSD", "--providers", "deriv",
                   "--deriv-max-pages", "0"])

    assert codigo == 2
    assert "deriv-max-pages" in capsys.readouterr().err


def test_el_reporte_avisa_cuando_algo_corto(sin_secretos, capsys):
    resultados = [
        ProbeResult("twelvedata", "EURUSD", CoverageStatus.CUBIERTO,
                    n_points=5000, provider_truncated=True,
                    depth_kind=DepthKind.TOPE_DE_PETICION, requests_used=1),
    ]

    salida = render_text(resultados)

    assert "CORTARON en el tope" in salida
    assert "PISO" in salida
    assert "EURUSD/twelvedata" in salida
