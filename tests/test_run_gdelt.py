"""
tests/test_run_gdelt.py
=========================
Cobertura de ingestion/run_gdelt.py.

NINGÚN TEST TOCA LA RED. Se inyecta un adapter falso por el parámetro
`adapter=` de `run_ingesta()` -- que existe para esto y no para inyectar por
inyectar: el adapter real abre una conexión, y un entry point que solo se
puede probar con red es un entry point que no se prueba.

La serie va contra un `drive_root()` temporal, con el mismo patrón de
monkeypatch que ya establecieron test_persistence.py y test_gdelt_series.py
(env var SPEL_DRIVE_ROOT + tmp_path). No un mecanismo nuevo.

LOS TRES QUE MÁS IMPORTAN, porque fijan decisiones y no mecánica:

  · `test_un_dia_sin_datos_se_escribe_igual_y_last_day_avanza` -- si el día
    404 se salteara, `last_day()` quedaría clavado y la ingesta golpearía el
    mismo día muerto en cada corrida. Es el test de que la ingesta no se
    puede atascar.

  · `test_un_dia_sin_datos_no_copia_el_dia_anterior` -- contra el legacy,
    que hace exactamente eso (`spel_ingest_incremental.py`: `day_data =
    hist_window[-1].copy()`). Portar esa línea habría escrito un dato que
    nadie midió.

  · `test_sin_serie_el_total_dice_no_medido_y_nunca_cero` -- un gap
    desconocido impreso como 0 es un cero que parece medición. Es la
    situación PERMANENTE de CI, no un borde raro.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import governance.persistence as persistence_module
import ingestion.run_gdelt as rg
from governance.persistence import DRIVE_ROOT_ENV_VAR
from ingestion.adapters import AdapterConnectionError, AdapterDataError
from ingestion.gdelt import GdeltDayResult
from ingestion.gdelt_aggregation import MIN_EVENTS_FOR_VALID_DAY
from ingestion.gdelt_series import append_day, last_day, read_series
from ingestion.run_gdelt import (
    DEFAULT_MAX_DAYS,
    build_parser,
    main,
    planificar_asset,
    render_text,
    run_ingesta,
    ultimo_dia_disponible,
)

HOY = date(2026, 9, 14)
AYER = date(2026, 9, 13)


@pytest.fixture(autouse=True)
def _entorno_aislado(monkeypatch, tmp_path):
    """Serie temporal y sin pausas de rate limiting. La pausa es real en
    producción (port del legacy) pero acá solo haría lentos los tests."""
    monkeypatch.setenv(DRIVE_ROOT_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(persistence_module, "_is_colab", lambda: False)
    monkeypatch.setattr(rg, "PAUSA_ENTRE_DIAS_S", 0.0)


# ─── Dobles ───────────────────────────────────────────────────────────────

def _evento(country1="USA", tone=-2.0, sources=3, goldstein=1.0, country2=None):
    # country2 espeja a country1 salvo que se pida otra cosa:
    # classify_gdelt_event() mira LOS DOS actores, así que dejar country2
    # fijo en "USA" haría que un evento "de India" siguiera pasando el
    # filtro de BTC (que incluye USA) -- el filtro se vería inerte.
    return {
        "date_int": 20260913, "country1": country1,
        "country2": country1 if country2 is None else country2,
        "goldstein": goldstein, "num_mentions": 10, "num_sources": sources,
        "num_articles": 8, "avg_tone": tone,
    }


def _dia_con_eventos(n=10):
    """n eventos USA, que pasan el filtro CORE de BTC, XAU y NVDA."""
    return [_evento(tone=-5.0 + i) for i in range(n)]


class AdapterFalso:
    """Registra qué días se pidieron -- así el test de descarga compartida
    verifica el CONTEO de descargas, no una consecuencia indirecta."""

    def __init__(self, *, eventos_por_dia=None, sin_datos=(), falla_en=None,
                 excepcion=AdapterConnectionError):
        self.pedidos: list[date] = []
        self._eventos = eventos_por_dia if eventos_por_dia is not None else _dia_con_eventos()
        self._sin_datos = set(sin_datos)
        self._falla_en = falla_en
        self._excepcion = excepcion

    async def fetch_day(self, day: date) -> GdeltDayResult:
        self.pedidos.append(day)
        if self._falla_en is not None and day == self._falla_en:
            raise self._excepcion(f"falla simulada en {day}")
        if day in self._sin_datos:
            return GdeltDayResult(day=day, events=[], available=False, no_data=True)
        return GdeltDayResult(day=day, events=list(self._eventos),
                              available=True, no_data=False)


def _sembrar(asset: str, dias: list[date], n_events=10) -> None:
    from ingestion.gdelt_aggregation import aggregate_day
    for d in dias:
        append_day(aggregate_day(_dia_con_eventos(n_events), asset, d))


async def _correr(assets, **kw):
    kw.setdefault("hoy", HOY)
    return await run_ingesta(assets, **kw)


# ═══ Incremental: de dónde arranca ════════════════════════════════════════

class TestIncremental:
    def test_arranca_en_last_day_mas_uno_no_en_cero(self):
        """El requisito literal del brief, y el patrón de
        spel_ingest_incremental.py::ingest_asset (leer último día, +1)."""
        _sembrar("BTC", [date(2026, 9, 8), date(2026, 9, 9)])

        run, dias = planificar_asset("BTC", hoy=HOY, max_days=DEFAULT_MAX_DAYS)

        assert run.last_day == "2026-09-09"
        assert dias[0] == date(2026, 9, 10), "no arrancó en last_day + 1"
        assert dias[-1] == AYER

    def test_el_dia_de_hoy_no_se_pide_nunca(self):
        """GDELT publica el archivo de un día DESPUÉS de que terminó. Pedir
        el de hoy devuelve 404, que este módulo escribiría como día vacío --
        envenenando la serie con un día que sí va a existir mañana."""
        assert ultimo_dia_disponible(HOY) == AYER

        _sembrar("BTC", [date(2026, 9, 10)])
        _, dias = planificar_asset("BTC", hoy=HOY, max_days=DEFAULT_MAX_DAYS)

        assert HOY not in dias
        assert max(dias) == AYER

    @pytest.mark.asyncio
    async def test_serie_al_dia_sale_cero_y_lo_dice(self, capsys):
        """"Si la serie está al día, sale con código 0 y lo dice" -- las dos
        mitades: el código Y el decirlo. Un 0 silencioso es indistinguible
        de un 0 por no haber hecho nada."""
        _sembrar("BTC", [AYER])
        adapter = AdapterFalso()

        report = await _correr(["BTC"], adapter=adapter)

        assert adapter.pedidos == [], "bajó algo teniendo la serie al día"
        assert report.assets[0].status == "AL_DIA"
        assert report.assets[0].gap_days == 0

        texto = render_text(report)
        assert "AL_DIA" in texto
        assert "al día" in texto

    def test_serie_al_dia_por_main_sale_cero(self, monkeypatch, capsys):
        _sembrar("BTC", [AYER])
        monkeypatch.setattr(rg, "GDELTDailyAdapter", lambda **kw: AdapterFalso())

        assert main(["--assets", "BTC"], hoy=HOY) == 0
        assert "AL_DIA" in capsys.readouterr().out


# ═══ El gap, que es lo que convierte el dry-run en monitor ════════════════

class TestGap:
    def test_el_gap_es_exacto_por_activo(self):
        _sembrar("BTC", [date(2026, 9, 3)])
        _sembrar("XAU", [date(2026, 9, 11)])

        run_btc, _ = planificar_asset("BTC", hoy=HOY, max_days=DEFAULT_MAX_DAYS)
        run_xau, _ = planificar_asset("XAU", hoy=HOY, max_days=DEFAULT_MAX_DAYS)

        # 2026-09-03 -> 2026-09-13 son 10 días de distancia
        assert run_btc.gap_days == 10
        assert run_xau.gap_days == 2

    def test_el_gap_no_se_confunde_con_los_dias_de_la_corrida(self):
        """Dos números distintos, y el reporte muestra los dos: colapsarlos
        haría que un gap de 640 días se lea como 10 y el monitor no serviría
        para lo único que tiene que servir."""
        _sembrar("BTC", [HOY - timedelta(days=100)])

        run, dias = planificar_asset("BTC", hoy=HOY, max_days=3)

        assert run.gap_days == 99
        assert run.days_planned == 3 == len(dias)
        assert any("excede --max-days" in n for n in run.notes)

    def test_reporta_cuantas_corridas_harian_falta(self):
        _sembrar("BTC", [HOY - timedelta(days=100)])
        run, _ = planificar_asset("BTC", hoy=HOY, max_days=10)
        # 99 días de gap a 10 por corrida = 10 corridas (techo)
        assert any("10 corridas" in n for n in run.notes)

    @pytest.mark.asyncio
    async def test_sin_serie_el_total_dice_no_medido_y_nunca_cero(self):
        """LA SITUACIÓN PERMANENTE DE CI, no un borde raro: en Actions
        drive_root() cae al fallback local y no hay serie que leer. Sumar
        los gaps medidos e ignorar el resto imprimiría "gap acumulado: 0"
        cuando la verdad es "no se pudo medir ninguno" -- un cero que parece
        una medición."""
        report = await _correr(["BTC", "XAU"], adapter=AdapterFalso(), max_days=1)
        texto = render_text(report)

        assert all(r.gap_days is None for r in report.assets)
        assert all(r.status != "AL_DIA" for r in report.assets)
        assert "NO MEDIDO" in texto
        assert "gap acumulado 0" not in texto
        assert "No es un gap de cero" in texto

    @pytest.mark.asyncio
    async def test_con_serie_parcial_separa_medidos_de_no_medidos(self):
        _sembrar("BTC", [date(2026, 9, 11)])
        report = await _correr(["BTC", "XAU"], adapter=AdapterFalso(), max_days=1)
        texto = render_text(report)

        assert "gap acumulado 2 día(s) sobre 1 activo(s) medido(s)" in texto
        assert "Sin gap medible (1): XAU" in texto

    @pytest.mark.asyncio
    async def test_el_reporte_nombra_donde_busco_la_serie(self, tmp_path):
        """Sin este dato, "sin serie" se lee como "la serie está vacía" en
        vez de como "no estoy mirando donde vive la serie", que es lo que
        pasa en Actions."""
        report = await _correr(["BTC"], adapter=AdapterFalso(), max_days=1)
        assert str(tmp_path) in report.series_root
        assert report.series_root in render_text(report)


# ═══ Reanudable ═══════════════════════════════════════════════════════════

class TestReanudable:
    @pytest.mark.asyncio
    async def test_escribe_dia_por_dia_no_al_final(self):
        """Lo que hace que sea reanudable. Se verifica sobre el ESTADO de la
        serie en el momento en que falla el tercer día: si se escribiera al
        final, los dos primeros se habrían perdido."""
        _sembrar("BTC", [date(2026, 9, 9)])
        adapter = AdapterFalso(falla_en=date(2026, 9, 12))

        report = await _correr(["BTC"], adapter=adapter, write=True, max_days=5)

        assert report.chain_error is not None
        assert last_day("BTC") == date(2026, 9, 11), (
            "los días anteriores al fallo no quedaron escritos")
        assert [r.day for r in read_series("BTC")] == [
            date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11)]

    @pytest.mark.asyncio
    async def test_la_corrida_siguiente_sigue_donde_quedo(self):
        _sembrar("BTC", [date(2026, 9, 9)])

        primera = await _correr(
            ["BTC"], adapter=AdapterFalso(falla_en=date(2026, 9, 12)),
            write=True, max_days=5)
        assert primera.chain_error is not None

        adapter2 = AdapterFalso()
        await _correr(["BTC"], adapter=adapter2, write=True, max_days=5)

        assert adapter2.pedidos[0] == date(2026, 9, 12), (
            "la segunda corrida no retomó en el día que falló")
        assert last_day("BTC") == AYER

    @pytest.mark.asyncio
    async def test_dos_corridas_seguidas_no_reprocesan_lo_mismo(self):
        _sembrar("BTC", [date(2026, 9, 10)])

        a1 = AdapterFalso()
        await _correr(["BTC"], adapter=a1, write=True, max_days=2)
        a2 = AdapterFalso()
        await _correr(["BTC"], adapter=a2, write=True, max_days=2)

        assert set(a1.pedidos) & set(a2.pedidos) == set(), (
            "la segunda corrida volvió a bajar días ya guardados")


# ═══ Días sin datos: el anti-port ═════════════════════════════════════════

class TestDiasSinDatos:
    @pytest.mark.asyncio
    async def test_un_dia_sin_datos_se_escribe_igual_y_last_day_avanza(self):
        """SI SE SALTEARA, `last_day()` quedaría clavado en el día anterior
        y cada corrida volvería a pedir el mismo día muerto para siempre.
        Este es el test de que la ingesta no se puede atascar."""
        _sembrar("BTC", [date(2026, 9, 11)])
        adapter = AdapterFalso(sin_datos={date(2026, 9, 12)})

        await _correr(["BTC"], adapter=adapter, write=True, max_days=1)

        assert last_day("BTC") == date(2026, 9, 12), (
            "el día sin datos no se escribió: la ingesta queda atascada")

    @pytest.mark.asyncio
    async def test_un_dia_sin_datos_no_copia_el_dia_anterior(self):
        """CONTRA EL LEGACY, que hace exactamente eso:
        `spel_ingest_incremental.py` -> `day_data = hist_window[-1].copy()`
        cuando GDELT no devuelve datos, y devuelve constantes inventadas
        (goldstein 1.845, tone_variance 117.0) cuando falta una columna.
        Portar esa línea habría escrito un dato que nadie midió, y en el
        JSONL sería indistinguible de uno real."""
        _sembrar("BTC", [date(2026, 9, 11)], n_events=40)
        previo = read_series("BTC")[-1]
        assert previo.entropy_shannon is not None   # el día previo SÍ tiene señal

        adapter = AdapterFalso(sin_datos={date(2026, 9, 12)})
        await _correr(["BTC"], adapter=adapter, write=True, max_days=1)

        vacio = read_series("BTC")[-1]
        assert vacio.day == date(2026, 9, 12)
        assert vacio.n_events == 0
        assert vacio.insufficient_events is True
        assert vacio.entropy_shannon is None, "se copió la señal del día anterior"
        assert vacio.goldstein_mean is None
        assert vacio.tone_variance is None

    @pytest.mark.asyncio
    async def test_dia_con_pocos_eventos_se_escribe_marcado(self):
        """Menos de MIN_EVENTS_FOR_VALID_DAY tras filtrar: se guarda con
        insufficient_events=True, no se saltea -- mismo motivo que el 404."""
        pocos = [_evento() for _ in range(MIN_EVENTS_FOR_VALID_DAY - 1)]
        _sembrar("BTC", [date(2026, 9, 11)])

        report = await _correr(["BTC"], adapter=AdapterFalso(eventos_por_dia=pocos),
                               write=True, max_days=1)

        assert report.assets[0].days_insufficient == 1
        assert read_series("BTC")[-1].insufficient_events is True
        assert last_day("BTC") == date(2026, 9, 12)

    @pytest.mark.asyncio
    async def test_el_reporte_distingue_sin_datos_de_pocos_eventos(self):
        _sembrar("BTC", [date(2026, 9, 11)])
        report = await _correr(["BTC"], adapter=AdapterFalso(sin_datos={date(2026, 9, 12)}),
                               max_days=1)

        assert report.assets[0].days_no_data == 1
        texto = render_text(report)
        assert "GDELT no publicó nada" in texto
        assert "no se saltean" in texto


# ═══ Descarga compartida entre activos ════════════════════════════════════

class TestDescargaCompartida:
    @pytest.mark.asyncio
    async def test_un_dia_se_baja_una_vez_para_todos_los_activos(self):
        """El archivo diario de GDELT es UNO para todos: el filtro por país
        se aplica al agregar. El legacy lo bajaba una vez POR ACTIVO."""
        for a in ("BTC", "XAU", "NVDA"):
            _sembrar(a, [date(2026, 9, 11)])

        adapter = AdapterFalso()
        report = await _correr(["BTC", "XAU", "NVDA"], adapter=adapter, max_days=2)

        assert adapter.pedidos == [date(2026, 9, 12), date(2026, 9, 13)]
        assert len(adapter.pedidos) == 2, "bajó el mismo día más de una vez"
        assert report.downloads == 2
        assert all(r.days_processed == 2 for r in report.assets)

    @pytest.mark.asyncio
    async def test_activos_con_gaps_distintos_comparten_los_dias_comunes(self):
        _sembrar("BTC", [date(2026, 9, 10)])
        _sembrar("XAU", [date(2026, 9, 12)])

        adapter = AdapterFalso()
        await _correr(["BTC", "XAU"], adapter=adapter, max_days=10)

        # union: BTC pide 11,12,13; XAU pide 13. El 13 se baja una sola vez.
        assert adapter.pedidos == [date(2026, 9, 11), date(2026, 9, 12), AYER]

    @pytest.mark.asyncio
    async def test_cada_activo_agrega_con_su_propio_filtro(self):
        """La descarga se comparte; el filtro por país NO. Un evento de IND
        cuenta para NIFTY50 y no para BTC."""
        eventos = [_evento(country1="IND") for _ in range(10)]
        for a in ("BTC", "NIFTY50"):
            _sembrar(a, [date(2026, 9, 12)])

        report = await _correr(["BTC", "NIFTY50"],
                               adapter=AdapterFalso(eventos_por_dia=eventos),
                               write=True, max_days=1)

        por_activo = {r.asset: r for r in report.assets}
        assert por_activo["NIFTY50"].days_insufficient == 0
        assert por_activo["BTC"].days_insufficient == 1, (
            "eventos de IND pasaron el filtro CORE de BTC")


# ═══ --max-days ═══════════════════════════════════════════════════════════

class TestMaxDays:
    def test_el_default_es_bajo(self):
        """Actions tiene techo de tiempo y GDELT tarda ~3 s por día. Un
        default alto convierte cada corrida en una apuesta contra el
        timeout."""
        assert DEFAULT_MAX_DAYS == 10
        assert build_parser().parse_args([]).max_days == 10

    @pytest.mark.asyncio
    async def test_acota_de_verdad_las_descargas(self):
        _sembrar("BTC", [HOY - timedelta(days=60)])
        adapter = AdapterFalso()

        await _correr(["BTC"], adapter=adapter, max_days=4)

        assert len(adapter.pedidos) == 4

    @pytest.mark.asyncio
    async def test_toma_los_dias_mas_viejos_primero(self):
        """Al revés (los recientes primero) `last_day()` saltaría al final en
        la primera corrida y los días del medio no se pedirían nunca."""
        _sembrar("BTC", [date(2026, 9, 1)])
        adapter = AdapterFalso()

        await _correr(["BTC"], adapter=adapter, max_days=3)

        assert adapter.pedidos == [date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4)]

    def test_max_days_invalido_sale_dos(self, capsys):
        assert main(["--max-days", "0"], hoy=HOY) == 2
        assert "ERROR" in capsys.readouterr().err


# ═══ dry-run por defecto ══════════════════════════════════════════════════

class TestDryRun:
    def test_dry_run_es_el_default_sin_pasar_nada(self):
        args = build_parser().parse_args([])
        assert args.dry_run is True
        assert args.write is False

    @pytest.mark.asyncio
    async def test_dry_run_no_escribe_ni_un_byte(self, tmp_path):
        _sembrar("BTC", [date(2026, 9, 10)])
        antes = read_series("BTC")

        report = await _correr(["BTC"], adapter=AdapterFalso(), max_days=3)

        assert read_series("BTC") == antes
        assert report.assets[0].days_written == 0
        assert last_day("BTC") == date(2026, 9, 10)

    @pytest.mark.asyncio
    async def test_dry_run_igual_baja_parsea_y_agrega(self):
        """"Prueba la cadena completa hasta antes de escribir". Un dry-run
        que no baja nada no prueba nada: acá se verifica que la descarga y
        la agregación SÍ ocurrieron aunque no se escriba."""
        _sembrar("BTC", [date(2026, 9, 10)])
        adapter = AdapterFalso()

        report = await _correr(["BTC"], adapter=adapter, max_days=3)

        assert adapter.pedidos, "el dry-run no bajó nada"
        assert report.downloads == 3
        assert report.assets[0].days_processed == 3
        assert report.assets[0].days_written == 0

    @pytest.mark.asyncio
    async def test_con_write_si_escribe(self):
        _sembrar("BTC", [date(2026, 9, 10)])
        report = await _correr(["BTC"], adapter=AdapterFalso(), write=True, max_days=3)

        assert report.assets[0].days_written == 3
        assert last_day("BTC") == AYER

    def test_dry_run_y_write_juntos_son_contradiccion_no_preferencia(self, capsys):
        """--dry-run ya es el default, así que pasarlo junto a --write no es
        ambiguo: es contradictorio. Elegir uno en silencio sería adivinar."""
        assert main(["--dry-run", "--write"], hoy=HOY) == 2
        assert "se contradicen" in capsys.readouterr().err

    def test_el_reporte_dice_en_que_modo_corrio(self):
        for write, esperado in ((False, "DRY-RUN"), (True, "MODO ESCRITURA")):
            report = rg.RunReport(write=write, today=str(HOY),
                                  latest_available=str(AYER), max_days=10)
            assert esperado in render_text(report)


# ═══ Códigos de salida ════════════════════════════════════════════════════

class TestCodigosDeSalida:
    def test_cadena_rota_sale_uno_para_que_el_workflow_se_ponga_rojo(
            self, monkeypatch, capsys):
        """Es a propósito que este script PUEDA salir != 0, a diferencia de
        heartbeat.py y de import_gdelt_entropy.py, que salen 0 pase lo que
        pase porque son reportes. Este además chequea que la fuente siga
        viva, y un chequeo que nunca falla no es un chequeo."""
        _sembrar("BTC", [date(2026, 9, 12)])
        monkeypatch.setattr(
            rg, "GDELTDailyAdapter",
            lambda **kw: AdapterFalso(falla_en=AYER))

        assert main(["--assets", "BTC"], hoy=HOY) == 1
        assert "cadena se rompió" in capsys.readouterr().err

    def test_formato_cambiado_tambien_sale_uno(self, monkeypatch, capsys):
        """AdapterDataError = la fuente respondió pero el contenido no sirve
        (ZIP corrupto, columnas que cambiaron). Mismo tratamiento que un
        fallo de red: el workflow tiene que enterarse."""
        _sembrar("BTC", [date(2026, 9, 12)])
        monkeypatch.setattr(
            rg, "GDELTDailyAdapter",
            lambda **kw: AdapterFalso(falla_en=AYER, excepcion=AdapterDataError))

        assert main(["--assets", "BTC"], hoy=HOY) == 1
        assert "AdapterDataError" in capsys.readouterr().out

    def test_un_gap_grande_no_es_un_fallo_del_proceso(self, monkeypatch, capsys):
        """Mismo razonamiento que heartbeat.py: un rojo permanente entrena a
        ignorar el rojo. El gap es el dato que el monitor reporta, no un
        fallo."""
        _sembrar("BTC", [HOY - timedelta(days=500)])
        monkeypatch.setattr(rg, "GDELTDailyAdapter", lambda **kw: AdapterFalso())

        assert main(["--assets", "BTC", "--max-days", "2"], hoy=HOY) == 0
        assert "499" in capsys.readouterr().out

    def test_since_invalido_sale_dos(self, capsys):
        assert main(["--since", "el martes"], hoy=HOY) == 2
        assert "ISO" in capsys.readouterr().err

    def test_corrida_normal_sale_cero(self, monkeypatch, capsys):
        _sembrar("BTC", [date(2026, 9, 11)])
        monkeypatch.setattr(rg, "GDELTDailyAdapter", lambda **kw: AdapterFalso())

        assert main(["--assets", "BTC", "--max-days", "2"], hoy=HOY) == 0
        capsys.readouterr()


# ═══ --since ══════════════════════════════════════════════════════════════

class TestSince:
    def test_since_manda_sobre_last_day_pero_el_gap_sigue_siendo_el_real(self):
        """El origen se elige a mano; el gap reportado NO se maquilla para
        que coincida, porque el gap es el dato del monitor."""
        _sembrar("BTC", [date(2026, 9, 1)])

        run, dias = planificar_asset("BTC", hoy=HOY, max_days=3,
                                     since=date(2026, 9, 10))

        assert dias[0] == date(2026, 9, 10)
        assert run.gap_days == 12, "el gap se recalculó desde --since"
        assert run.status == "DESDE_EXPLICITO"

    @pytest.mark.asyncio
    async def test_since_permite_apuntar_la_prueba_a_dias_recientes(self):
        """El caso de uso real: con un gap de cientos de días, la cadena se
        probaría siempre contra archivos viejos y nunca contra el de ayer."""
        _sembrar("BTC", [date(2026, 1, 1)])
        adapter = AdapterFalso()

        await _correr(["BTC"], adapter=adapter, max_days=2, since=date(2026, 9, 12))

        assert adapter.pedidos == [date(2026, 9, 12), AYER]


# ═══ Activos ══════════════════════════════════════════════════════════════

class TestActivos:
    def test_el_default_son_los_activos_del_ciclo(self):
        """Reuso de DEFAULT_CYCLE_ASSETS, no una segunda lista que puede
        divergir de la del ciclo de scoring."""
        from orchestration.cycle import DEFAULT_CYCLE_ASSETS
        assert build_parser().parse_args([]).assets == list(DEFAULT_CYCLE_ASSETS)

    @pytest.mark.asyncio
    async def test_procesa_los_cinco_activos_del_ciclo(self):
        from orchestration.cycle import DEFAULT_CYCLE_ASSETS
        report = await _correr(list(DEFAULT_CYCLE_ASSETS),
                               adapter=AdapterFalso(), max_days=1)
        assert {r.asset for r in report.assets} == set(DEFAULT_CYCLE_ASSETS)

    @pytest.mark.asyncio
    async def test_eurusd_no_lanza_pese_a_no_tener_filtro_core(self):
        """EURUSD se clasifica solo por GOBIERNO (FX_GOBIERNO_ONLY_ASSETS).
        Es el activo que más chance tenía de romper este bucle."""
        report = await _correr(["EURUSD"], adapter=AdapterFalso(), max_days=1)
        assert report.assets[0].days_processed == 1


# ═══ Lo que este módulo NO debe tocar ═════════════════════════════════════

def test_run_gdelt_no_importa_secretos_ni_execution():
    """Verificado sobre el AST y no con grep, que daría falso positivo con
    los docstrings. Mismo patrón que test_heartbeat.py."""
    import ast
    import inspect

    arbol = ast.parse(inspect.getsource(rg))
    importados: set[str] = set()
    for n in ast.walk(arbol):
        if isinstance(n, ast.Import):
            importados.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            importados.add(n.module)

    for prohibido in ("governance.secrets", "execution",
                      "execution.circuit_breaker", "execution.execution_guard"):
        assert not any(m == prohibido or m.startswith(prohibido + ".")
                       for m in importados), f"run_gdelt importa {prohibido}"


def test_escribe_solo_por_la_api_publica_de_la_serie():
    """`append_day()` es el único camino de escritura -- misma regla que
    tools/import_gdelt_entropy.py. Formatear JSONL acá duplicaría
    `_result_to_line()` y crearía dos formatos que pueden divergir."""
    import inspect

    fuente = inspect.getsource(rg)
    assert "append_day(" in fuente
    for prohibido in ("_result_to_line", "_series_file_path", '.open("a"'):
        assert prohibido not in fuente, (
            f"run_gdelt usa {prohibido}: está escribiendo por fuera de la API")


def test_el_docstring_documenta_la_rama_de_datos_como_destino():
    """Requisito explícito: cuando CI necesite escribir, la vía es
    `data/gdelt-series` con CI como escritor único, y exige entrada en
    decision-log.md. Si esto no está escrito, la próxima sesión reabre la
    discusión desde cero."""
    doc = rg.__doc__ or ""
    assert "data/gdelt-series" in doc
    assert "decision-log.md" in doc
    assert "escritor" in doc
