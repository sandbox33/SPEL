"""
tests/test_execution_costs.py
===============================
Cobertura de core/execution_costs.py.

POR QUÉ IMPORTA MÁS QUE UN MÓDULO DE CÁLCULO CUALQUIERA: este es el
productor del `pnl` que `execution/circuit_breaker.py` consume y que hasta
hoy nadie generaba. Un error de signo o de unidades acá no da un número
raro -- da un backtest que dice que el sistema gana.

LOS TRES QUE FIJAN DECISIONES Y NO MECÁNICA:

  · `test_el_costo_de_entrada_se_carga_en_ts_entrada` -- la regla dura del
    brief. Cargarlo en el midpoint siguiente le regala al backtest el
    intervalo donde el precio ya se movió a favor de la señal.

  · `test_funding_cobrado_puede_dejar_el_neto_por_encima_del_bruto` -- el
    signo es real en los dos sentidos. Recortar el funding a cero para que
    "los costos siempre resten" escondería un ingreso que existe.

  · `test_el_caso_del_0506_del_docstring` -- fija el ejemplo del docstring
    contra la fórmula, para que no pueda quedar mintiendo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.execution_costs import (
    EPSILON_BREAKEVEN,
    HORAS_FUNDING_UTC,
    DesgloseCostos,
    Lado,
    Outcome,
    ResultadoTrade,
    TarifaFaltanteError,
    Tarifas,
    compute_trade_costs,
    instantes_de_funding,
)


def _utc(y=2026, m=9, d=1, h=0, mi=0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


SIN_COSTOS = Tarifas(fee_taker=0.0, fee_maker=0.0, spread=0.0,
                     slippage=0.0, funding_por_periodo=0.0)


def _trade(*, tarifas=SIN_COSTOS, lado=Lado.LARGO, precio_entrada=100.0,
           precio_salida=101.0, nocional=10_000.0, ts_entrada=None,
           ts_salida=None, entrada_es_taker=True, salida_es_taker=True,
           fraccion_completada=1.0, ejecutada=True) -> ResultadoTrade:
    return compute_trade_costs(
        ts_entrada=ts_entrada or _utc(h=1),
        ts_salida=ts_salida or _utc(h=2),
        lado=lado, precio_entrada=precio_entrada, precio_salida=precio_salida,
        nocional=nocional, tarifas=tarifas,
        entrada_es_taker=entrada_es_taker, salida_es_taker=salida_es_taker,
        fraccion_completada=fraccion_completada, ejecutada=ejecutada,
    )


# ═══ Sin costos: neto == bruto ════════════════════════════════════════════

class TestSinCostos:
    def test_sin_costos_el_neto_es_igual_al_bruto(self):
        r = _trade()
        assert r.bruto == pytest.approx(100.0)   # 1% de 10.000
        assert r.neto == pytest.approx(r.bruto)
        assert r.desglose.total == 0.0

    def test_el_desglose_viene_siempre_aunque_sea_todo_cero(self):
        """"Nunca devolver solo neto". Un neto sin desglose no se puede
        auditar: no se sabe si se lo comió el fee, el spread o el funding, y
        los tres se arreglan distinto."""
        r = _trade()
        assert isinstance(r.desglose, DesgloseCostos)
        for campo in ("fee_entrada", "fee_salida", "spread", "slippage",
                      "funding", "total"):
            assert hasattr(r.desglose, campo)

    def test_el_corto_invierte_el_signo_del_bruto(self):
        largo = _trade(lado=Lado.LARGO, precio_entrada=100.0, precio_salida=101.0)
        corto = _trade(lado=Lado.CORTO, precio_entrada=100.0, precio_salida=101.0)
        assert largo.bruto == pytest.approx(100.0)
        assert corto.bruto == pytest.approx(-100.0)


# ═══ Cada componente aislado, resta exacta ════════════════════════════════

class TestComponentesAislados:
    def test_solo_fee_taker(self):
        t = Tarifas(fee_taker=0.001, fee_maker=0.0, spread=0.0,
                    slippage=0.0, funding_por_periodo=0.0)
        r = _trade(tarifas=t, entrada_es_taker=True, salida_es_taker=True)

        assert r.desglose.fee_entrada == pytest.approx(10.0)   # 10.000 * 0,001
        assert r.desglose.fee_salida == pytest.approx(10.0)
        assert r.desglose.total == pytest.approx(20.0)
        assert r.neto == pytest.approx(r.bruto - 20.0)

    def test_solo_fee_maker(self):
        t = Tarifas(fee_taker=0.0, fee_maker=0.0002, spread=0.0,
                    slippage=0.0, funding_por_periodo=0.0)
        r = _trade(tarifas=t, entrada_es_taker=False, salida_es_taker=False)

        assert r.desglose.fee_entrada == pytest.approx(2.0)
        assert r.desglose.fee_salida == pytest.approx(2.0)
        assert r.desglose.total == pytest.approx(4.0)

    def test_taker_y_maker_son_de_verdad_separados(self):
        """Si fueran un solo número con dos nombres, esto daría lo mismo en
        las dos patas."""
        t = Tarifas(fee_taker=0.001, fee_maker=0.0002, spread=0.0,
                    slippage=0.0, funding_por_periodo=0.0)
        r = _trade(tarifas=t, entrada_es_taker=True, salida_es_taker=False)

        assert r.desglose.fee_entrada == pytest.approx(10.0)   # taker
        assert r.desglose.fee_salida == pytest.approx(2.0)     # maker
        assert r.desglose.fee_entrada != r.desglose.fee_salida

    def test_solo_spread(self):
        t = Tarifas(fee_taker=0.0, fee_maker=0.0, spread=0.0005,
                    slippage=0.0, funding_por_periodo=0.0)
        r = _trade(tarifas=t, entrada_es_taker=True, salida_es_taker=True)

        assert r.desglose.spread == pytest.approx(10.0)   # 2 patas * 5
        assert r.neto == pytest.approx(r.bruto - 10.0)

    def test_solo_las_patas_taker_cruzan_el_spread(self):
        """DECISIÓN DE MODELO, no una obviedad: una pata maker se ejecuta en
        el libro y no cruza. Si el spread se cobrara igual en maker,
        separar taker de maker no significaría nada."""
        t = Tarifas(fee_taker=0.0, fee_maker=0.0, spread=0.0005,
                    slippage=0.0, funding_por_periodo=0.0)

        dos = _trade(tarifas=t, entrada_es_taker=True, salida_es_taker=True)
        una = _trade(tarifas=t, entrada_es_taker=True, salida_es_taker=False)
        cero = _trade(tarifas=t, entrada_es_taker=False, salida_es_taker=False)

        assert dos.desglose.spread == pytest.approx(10.0)
        assert una.desglose.spread == pytest.approx(5.0)
        assert cero.desglose.spread == 0.0

    def test_solo_slippage_y_aplica_a_las_dos_patas(self):
        """A diferencia del spread, el slippage aplica sea taker o maker:
        las dos patas se llenan y las dos pueden llenarse peor."""
        t = Tarifas(fee_taker=0.0, fee_maker=0.0, spread=0.0,
                    slippage=0.0003, funding_por_periodo=0.0)
        r = _trade(tarifas=t, entrada_es_taker=False, salida_es_taker=False)

        assert r.desglose.slippage == pytest.approx(6.0)   # 2 * 3
        assert r.neto == pytest.approx(r.bruto - 6.0)

    def test_los_componentes_suman_exactamente_el_total(self):
        t = Tarifas(fee_taker=0.001, fee_maker=0.0002, spread=0.0005,
                    slippage=0.0003, funding_por_periodo=0.0001)
        r = _trade(tarifas=t, ts_entrada=_utc(h=1), ts_salida=_utc(d=2, h=1))
        d = r.desglose

        assert d.total == pytest.approx(
            d.fee_entrada + d.fee_salida + d.spread + d.slippage + d.funding)
        assert r.neto == pytest.approx(r.bruto - d.total)


# ═══ Funding ══════════════════════════════════════════════════════════════

class TestFunding:
    def test_los_instantes_son_las_horas_declaradas(self):
        assert HORAS_FUNDING_UTC == (0, 8, 16)

    def test_tenencia_de_cero_periodos(self):
        """01:00 -> 02:00 no contiene ningún instante de funding."""
        r = _trade(
            tarifas=Tarifas(fee_taker=0.0, fee_maker=0.0, spread=0.0,
                            slippage=0.0, funding_por_periodo=0.001),
            ts_entrada=_utc(h=1), ts_salida=_utc(h=2))

        assert r.periodos_funding == 0
        assert r.desglose.funding == 0.0
        assert r.neto == pytest.approx(r.bruto)

    def test_tenencia_de_un_periodo(self):
        """01:00 -> 09:00 contiene solo el de las 08:00."""
        r = _trade(
            tarifas=Tarifas(fee_taker=0.0, fee_maker=0.0, spread=0.0,
                            slippage=0.0, funding_por_periodo=0.001),
            ts_entrada=_utc(h=1), ts_salida=_utc(h=9))

        assert r.periodos_funding == 1
        assert r.desglose.funding == pytest.approx(10.0)   # 10.000 * 0,001 * 1

    def test_tenencia_de_tres_periodos(self):
        """01:00 -> 01:00 del día siguiente contiene 08:00, 16:00 y 00:00."""
        r = _trade(
            tarifas=Tarifas(fee_taker=0.0, fee_maker=0.0, spread=0.0,
                            slippage=0.0, funding_por_periodo=0.001),
            ts_entrada=_utc(h=1), ts_salida=_utc(d=2, h=1))

        assert r.periodos_funding == 3
        assert r.desglose.funding == pytest.approx(30.0)

    def test_el_funding_del_borde_no_cuenta(self):
        """ESTRICTAMENTE DENTRO: en ts_entrada la posición se está abriendo
        y en ts_salida ya se cerró. Quien mantiene de 08:00 a 16:00 clavado
        no estuvo expuesto a ningún devengo."""
        assert instantes_de_funding(_utc(h=8), _utc(h=16)) == []

        # un minuto de cada lado y aparecen los dos
        dentro = instantes_de_funding(_utc(h=7, mi=59), _utc(h=16, mi=1))
        assert [t.hour for t in dentro] == [8, 16]

    def test_funding_con_signo_favorable_al_corto(self):
        """Tasa positiva: el largo PAGA y el corto COBRA. Es la convención
        de los perpetuos, y va en los dos sentidos de verdad."""
        t = Tarifas(fee_taker=0.0, fee_maker=0.0, spread=0.0,
                    slippage=0.0, funding_por_periodo=0.001)
        kw = dict(tarifas=t, ts_entrada=_utc(h=1), ts_salida=_utc(h=9))

        largo = _trade(lado=Lado.LARGO, **kw)
        corto = _trade(lado=Lado.CORTO, **kw)

        assert largo.desglose.funding == pytest.approx(10.0)    # paga
        assert corto.desglose.funding == pytest.approx(-10.0)   # cobra

    def test_funding_cobrado_puede_dejar_el_neto_por_encima_del_bruto(self):
        """NO SE RECORTA A CERO. Un costo negativo que existe y se esconde
        es un ingreso que el backtest no ve."""
        t = Tarifas(fee_taker=0.0, fee_maker=0.0, spread=0.0,
                    slippage=0.0, funding_por_periodo=0.001)
        r = _trade(lado=Lado.CORTO, precio_entrada=100.0, precio_salida=99.0,
                   tarifas=t, ts_entrada=_utc(h=1), ts_salida=_utc(h=9))

        assert r.desglose.funding < 0
        assert r.neto > r.bruto
        assert r.neto == pytest.approx(r.bruto + 10.0)

    def test_tasa_de_funding_negativa_invierte_los_dos_lados(self):
        t = Tarifas(fee_taker=0.0, fee_maker=0.0, spread=0.0,
                    slippage=0.0, funding_por_periodo=-0.001)
        kw = dict(tarifas=t, ts_entrada=_utc(h=1), ts_salida=_utc(h=9))

        assert _trade(lado=Lado.LARGO, **kw).desglose.funding == pytest.approx(-10.0)
        assert _trade(lado=Lado.CORTO, **kw).desglose.funding == pytest.approx(10.0)


# ═══ La regla dura: cuándo se carga el costo ══════════════════════════════

def test_el_costo_de_entrada_se_carga_en_ts_entrada():
    """REGLA DURA DEL BRIEF, y este test existe para que falle si alguien la
    mueve. Cargar el costo de entrada en el midpoint siguiente (o al cierre
    de la vela) le regala al backtest el intervalo donde el precio ya se
    movió a favor de la señal -- y ese regalo no se ve en ningún número,
    solo en que el backtest mejora."""
    entrada = _utc(h=1, mi=17)
    salida = _utc(h=9, mi=42)
    r = _trade(ts_entrada=entrada, ts_salida=salida)

    assert r.ts_cargo_entrada == entrada
    assert r.ts_cargo_entrada != salida
    # ni el midpoint, que es el desvío concreto contra el que se escribe
    midpoint = entrada + (salida - entrada) / 2
    assert r.ts_cargo_entrada != midpoint


def test_los_timestamps_naive_se_rechazan():
    """El funding se cuenta contra horas UTC fijas: un naive obliga a
    adivinar la zona, y adivinar mal desplaza los períodos."""
    with pytest.raises(ValueError, match="timezone-aware"):
        compute_trade_costs(
            ts_entrada=datetime(2026, 9, 1, 1, 0), ts_salida=_utc(h=9),
            lado=Lado.LARGO, precio_entrada=100.0, precio_salida=101.0,
            nocional=10_000.0, tarifas=SIN_COSTOS,
            entrada_es_taker=True, salida_es_taker=True)


def test_salida_anterior_a_entrada_se_rechaza():
    with pytest.raises(ValueError, match="anterior"):
        _trade(ts_entrada=_utc(h=9), ts_salida=_utc(h=1))


# ═══ Tarifa faltante → excepción. Cero defaults ═══════════════════════════

class TestTarifaFaltante:
    @pytest.mark.parametrize("campo", [
        "fee_taker", "fee_maker", "spread", "slippage", "funding_por_periodo"])
    def test_una_tarifa_en_none_lanza(self, campo):
        kw = dict(fee_taker=0.001, fee_maker=0.0002, spread=0.0005,
                  slippage=0.0003, funding_por_periodo=0.0001)
        kw[campo] = None
        with pytest.raises(TarifaFaltanteError, match=campo):
            Tarifas(**kw)

    @pytest.mark.parametrize("campo", [
        "fee_taker", "fee_maker", "spread", "slippage", "funding_por_periodo"])
    def test_ninguna_tarifa_tiene_default(self, campo):
        """CERO DEFAULTS: omitir el argumento tiene que romper. El legacy
        (spel_cost_model.py) caía a las tarifas de BTC con un warning, y un
        warning no detiene un backtest de 4.000 trades."""
        kw = dict(fee_taker=0.001, fee_maker=0.0002, spread=0.0005,
                  slippage=0.0003, funding_por_periodo=0.0001)
        del kw[campo]
        with pytest.raises(TypeError):
            Tarifas(**kw)

    def test_una_tarifa_no_numerica_lanza(self):
        with pytest.raises(TarifaFaltanteError, match="no es un número"):
            Tarifas(fee_taker="0.001", fee_maker=0.0, spread=0.0,
                    slippage=0.0, funding_por_periodo=0.0)

    def test_un_costo_negativo_que_no_es_funding_lanza(self):
        """El único componente con signo es el funding. Un fee negativo
        sería un rebate, y un rebate no se modela por accidente."""
        with pytest.raises(ValueError, match="fee_taker"):
            Tarifas(fee_taker=-0.001, fee_maker=0.0, spread=0.0,
                    slippage=0.0, funding_por_periodo=0.0)

    def test_el_modulo_no_guarda_ninguna_tarifa(self):
        """Prohibido inventar tarifas de Binance o Deriv. Se verifica que no
        haya un mapa de tarifas por activo como el `_COSTOS` del legacy."""
        import core.execution_costs as ec

        for nombre in dir(ec):
            valor = getattr(ec, nombre)
            if isinstance(valor, dict) and valor:
                claves = {str(k).upper() for k in valor}
                assert not claves & {"BTC", "XAU", "NVDA", "NIFTY50", "EURUSD"}, (
                    f"{nombre} trae tarifas por activo hardcodeadas")


# ═══ Fill parcial y fallido ═══════════════════════════════════════════════

class TestFills:
    def test_fill_parcial_es_proporcional_en_todo(self):
        t = Tarifas(fee_taker=0.001, fee_maker=0.0002, spread=0.0005,
                    slippage=0.0003, funding_por_periodo=0.001)
        kw = dict(tarifas=t, ts_entrada=_utc(h=1), ts_salida=_utc(h=9))

        entero = _trade(fraccion_completada=1.0, **kw)
        mitad = _trade(fraccion_completada=0.5, **kw)

        assert mitad.bruto == pytest.approx(entero.bruto * 0.5)
        assert mitad.neto == pytest.approx(entero.neto * 0.5)
        for campo in ("fee_entrada", "fee_salida", "spread", "slippage",
                      "funding", "total"):
            assert getattr(mitad.desglose, campo) == pytest.approx(
                getattr(entero.desglose, campo) * 0.5), f"{campo} no escaló"

    def test_fill_fallido_no_cobra_ningun_fee(self):
        """Un fee sobre un fill que no ocurrió es un costo inventado: el
        mismo pecado que un costo omitido, con el signo al revés."""
        t = Tarifas(fee_taker=0.001, fee_maker=0.001, spread=0.001,
                    slippage=0.001, funding_por_periodo=0.001)
        r = _trade(tarifas=t, fraccion_completada=0.0)

        assert r.outcome is Outcome.FILL_FALLIDO
        assert r.bruto == 0.0
        assert r.neto == 0.0
        assert r.desglose.total == 0.0

    def test_fill_fallido_no_es_breakeven(self):
        """Los dos tienen neto 0 y significan lo contrario: uno se operó y
        salió tablas, el otro nunca se llenó."""
        fallido = _trade(fraccion_completada=0.0)
        tablas = _trade(precio_entrada=100.0, precio_salida=100.0)

        assert fallido.neto == tablas.neto == 0.0
        assert fallido.outcome is Outcome.FILL_FALLIDO
        assert tablas.outcome is Outcome.BREAKEVEN
        assert fallido.outcome is not tablas.outcome

    def test_senal_no_ejecutada_es_su_propio_outcome(self):
        r = _trade(ejecutada=False)
        assert r.outcome is Outcome.NO_EJECUTADA
        assert r.neto == 0.0

    def test_fraccion_fuera_de_rango_lanza(self):
        for mala in (-0.1, 1.1):
            with pytest.raises(ValueError, match="fraccion_completada"):
                _trade(fraccion_completada=mala)


# ═══ Outcome ══════════════════════════════════════════════════════════════

class TestOutcome:
    def test_neto_negativo_cuando_los_costos_superan_al_bruto(self):
        t = Tarifas(fee_taker=0.01, fee_maker=0.01, spread=0.0,
                    slippage=0.0, funding_por_periodo=0.0)
        r = _trade(tarifas=t)   # bruto 100, fees 100 + 100

        assert r.bruto > 0
        assert r.neto < 0
        assert r.neto == pytest.approx(-100.0)
        assert r.outcome is Outcome.PERDEDORA

    def test_el_outcome_sale_del_neto_no_del_bruto(self):
        """Un trade que ganó en bruto y perdió por costos es PERDEDORA. Es
        el caso entero por el que existe este módulo."""
        t = Tarifas(fee_taker=0.01, fee_maker=0.0, spread=0.0,
                    slippage=0.0, funding_por_periodo=0.0)
        r = _trade(tarifas=t, entrada_es_taker=True, salida_es_taker=True)

        assert r.bruto == pytest.approx(100.0)
        assert r.outcome is Outcome.PERDEDORA

    def test_breakeven_tolera_el_ruido_de_punto_flotante(self):
        """Sin el epsilon, un neto de 1e-13 se registraría como GANADORA y
        el ledger contaría una ganadora que no existió."""
        r = _trade(precio_entrada=100.0, precio_salida=100.0)
        assert r.outcome is Outcome.BREAKEVEN
        assert abs(r.neto) < EPSILON_BREAKEVEN

    def test_el_outcome_siempre_esta_presente(self):
        """Nunca por ausencia -- es la regla del ledger."""
        casos = [
            _trade(),
            _trade(precio_salida=99.0),
            _trade(precio_salida=100.0),
            _trade(fraccion_completada=0.0),
            _trade(ejecutada=False),
        ]
        for r in casos:
            assert isinstance(r.outcome, Outcome)
            assert r.outcome.value


# ═══ El caso del docstring ════════════════════════════════════════════════

def test_el_caso_del_0506_del_docstring():
    """Fija el ejemplo del docstring del módulo contra la fórmula real, para
    que no pueda quedar mintiendo si alguien la cambia.

    El 0,506% lo trajo el brief: NO es una medición de este repo, y el fee
    de 0,04% por pata es un VALOR DE EJEMPLO, no una tarifa de Binance
    verificada."""
    t = Tarifas(fee_taker=0.0004, fee_maker=0.0, spread=0.0,
                slippage=0.0, funding_por_periodo=0.0)
    r = compute_trade_costs(
        ts_entrada=_utc(h=1), ts_salida=_utc(h=2), lado=Lado.LARGO,
        precio_entrada=100.0, precio_salida=100.506, nocional=10_000.0,
        tarifas=t, entrada_es_taker=True, salida_es_taker=True)

    assert r.bruto == pytest.approx(50.60, abs=0.01)
    assert r.desglose.fee_entrada == pytest.approx(4.00)
    assert r.desglose.fee_salida == pytest.approx(4.00)
    assert r.neto == pytest.approx(42.60, abs=0.01)

    # el titular: dos patas de 4 centésimas de punto se comen el 15,8%
    mordida = r.desglose.total / r.bruto
    assert mordida == pytest.approx(0.158, abs=0.001)


# ═══ Lo que este módulo NO hace ═══════════════════════════════════════════

def test_execution_costs_es_puro_sin_red_ni_io():
    """Funciones puras: sin red, sin I/O. Verificado sobre el AST y no con
    grep, que daría falso positivo con los docstrings."""
    import ast
    import inspect

    import core.execution_costs as ec

    arbol = ast.parse(inspect.getsource(ec))
    importados: set[str] = set()
    for n in ast.walk(arbol):
        if isinstance(n, ast.Import):
            importados.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            importados.add(n.module)

    for prohibido in ("httpx", "requests", "socket", "urllib", "os",
                      "governance.secrets", "execution",
                      "governance.persistence"):
        assert not any(m == prohibido or m.startswith(prohibido + ".")
                       for m in importados), (
            f"execution_costs importa {prohibido}: dejó de ser puro")


def test_los_resultados_son_inmutables():
    """@dataclass(frozen=True), mismo estilo que GodelScoreResult. Una fila
    del ledger que se puede mutar después de calculada es una fila que no
    prueba nada."""
    r = _trade()
    with pytest.raises(Exception):
        r.neto = 999.0          # type: ignore[misc]
    with pytest.raises(Exception):
        r.desglose.total = 0.0  # type: ignore[misc]
    with pytest.raises(Exception):
        SIN_COSTOS.fee_taker = 1.0   # type: ignore[misc]
