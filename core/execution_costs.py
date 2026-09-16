"""
core/execution_costs.py
=========================
De un trade hipotético a un P&L neto, con el desglose de cada costo.

EL HUECO QUE CIERRA, verificado: `execution/circuit_breaker.py` consume
`TradeResult(pnl: float)` y **nadie en el repo lo produce**. El breaker que
protege la cuenta viene esperando un número que ningún módulo calcula. Este
es el productor.

Funciones puras: sin red, sin I/O, sin estado. Quien persiste es
`core/trade_ledger.py`; quien decide si operar es `execution/`, congelado
hasta Fase 4 y que este módulo no toca.

══ EL LEGACY EXISTE Y NO SE PORTA. LOS TRES MOTIVOS ══

`spel_cost_model.py` (auditado línea por línea, en las 5 ramas de archivo)
es el antecedente. Se porta su ESTRUCTURA -- costos por componente sobre
porcentajes, y un total -- y se descartan sus tres decisiones concretas:

  1. LAS UNIDADES NO CIERRAN. `pnl_neto()` hace:

         costos_totales = (costo.total_pct / 100.0) * n_trades
         neto = pnl_bruto - costos_totales

     `costos_totales` es una FRACCIÓN (0.002 para BTC) y se resta de
     `pnl_bruto` sin multiplicar por nocional en ningún lado. Si
     `pnl_bruto` son 500 dólares, le resta dos décimas de centavo. Si en
     cambio `pnl_bruto` es una fracción de retorno, entonces `pnl_neto()`
     devuelve una fracción -- pero su nombre, su comentario de uso ("USO
     donde calcules P&L") y el `TradeResult(pnl=...)` del breaker dicen
     moneda. Los dos llamadores del propio módulo lo leen distinto:
     `breakeven_trades()` divide fracción por fracción, `pnl_neto()` resta
     fracción de lo que llame "P&L". No es que esté mal calibrado: es
     dimensionalmente ambiguo, y una ambigüedad de unidades en un modelo
     de costos es el defecto que infla un backtest en silencio.
     Acá TODO costo se calcula sobre `nocional` explícito y sale en la
     misma moneda que `bruto`.

  2. FALLBACK SILENCIOSO A BTC. `get_costo()` con un activo sin mapeo
     loguea un warning y devuelve las tarifas de BTC. Un warning no
     detiene un backtest. Acá una tarifa faltante LANZA
     (`TarifaFaltanteError`): cero defaults.

  3. TARIFAS HARDCODEADAS Y SIN FUENTE. `_COSTOS` trae números por activo
     que no citan de dónde salen. Acá no vive ninguna tarifa: son
     parámetros obligatorios de quien llama. Verificarlas contra Binance o
     Deriv es otro trabajo, y mezclarlo con este haría que un número sin
     procedencia entrara al repo escondido en un módulo de cálculo.

Lo que el legacy NO tenía y acá hace falta: taker y maker separados,
funding, fills parciales, y timestamps.

══ DECISIONES DE MODELO, que son las que hay que discutir ══

CUÁNDO SE CARGA EL COSTO DE ENTRADA: en `ts_entrada`, no en el midpoint
siguiente ni en el cierre de la vela. Está expuesto como
`ts_cargo_entrada` en el resultado, no implícito, y hay un test que falla
si alguien lo mueve. Cargarlo más tarde regala al backtest la diferencia
entre los dos instantes, que es justo el intervalo donde el precio ya se
movió a favor de la señal.

QUIÉN CRUZA EL SPREAD: solo las patas TAKER. Una pata maker se ejecuta en
el libro y por definición no cruza -- si el spread se cobrara igual en
maker, separar taker de maker no significaría nada y los dos fees serían
un solo número con dos nombres. Es una decisión, no una obviedad: está
acá para que se pueda discutir en vez de quedar enterrada en una suma.

FUNDING, SIGNO Y BORDES: se cuentan los instantes de funding (00:00, 08:00
y 16:00 UTC) que caen ESTRICTAMENTE dentro de la tenencia -- ni el de
`ts_entrada` ni el de `ts_salida` cuentan, porque en esos instantes la
posición se está abriendo o ya se cerró. El signo es real y va en los dos
sentidos: con tasa positiva el largo PAGA y el corto COBRA. Un funding
cobrado entra al desglose como número negativo, y por eso `neto` puede
superar a `bruto`. No se recorta a cero: un costo negativo que existe y se
esconde es un ingreso que el backtest no ve.

FILL PARCIAL: `fraccion_completada` escala el bruto Y todos los costos
proporcionales al nocional, porque todos lo son. Con 0.0 (fill fallido) no
hay nada ejecutado y por lo tanto no hay fee que pagar: el resultado es
cero en todo, con `outcome=FILL_FALLIDO` -- que NO es lo mismo que un trade
breakeven, y el ledger los guarda distinto.

══ EL CASO DEL 0,506% ══

Número de referencia que trajo el brief -- un retorno BRUTO de 0,506% sobre
el nocional. No sale de una medición de este repo y no se presenta como
tal; sirve para mostrar qué le queda después de pasar por acá.

Con nocional 10.000, las dos patas taker, y un fee taker de 0,04% por pata
(VALOR DE EJEMPLO, NO UNA TARIFA DE BINANCE VERIFICADA -- verificar las
tarifas reales es trabajo aparte, y este módulo no guarda ninguna):

    bruto            = 10.000 × 0,00506         =  50,60
    fee entrada      = 10.000 × 0,0004          =   4,00
    fee salida       = 10.000 × 0,0004          =   4,00
    neto                                        =  42,60

O sea: dos patas de un fee de cuatro centésimas de punto se comen el 15,8%
del bruto, sin spread, sin slippage y sin funding. Con los tres, un 0,506%
bruto es candidato a terminar en rojo. Ese es el punto del módulo: que ese
número aparezca antes del backtest y no después.

Este cálculo está fijado en un test (`test_el_caso_del_0506_del_docstring`)
para que el docstring no pueda quedar mintiendo si la fórmula cambia.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from typing import Optional

#: Horas UTC en las que se devenga funding. Es el esquema de 8 horas, el
#: más común en perpetuos. Que sean TRES instantes fijos y no "cada 8 horas
#: desde la entrada" es lo que hace que dos posiciones abiertas en momentos
#: distintos paguen el mismo funding el mismo día, que es como funciona de
#: verdad.
HORAS_FUNDING_UTC: tuple[int, ...] = (0, 8, 16)

#: Umbral para llamar BREAKEVEN a un neto. Sin esto, un neto de 1e-13 por
#: error de punto flotante se registraría como GANADORA y el ledger
#: contaría una ganadora que no existió.
EPSILON_BREAKEVEN = 1e-9


class Lado(str, Enum):
    LARGO = "largo"
    CORTO = "corto"


class Outcome(str, Enum):
    """EXPLÍCITO SIEMPRE, NUNCA POR AUSENCIA. Es la regla que define al
    ledger: un trade sin outcome sería una fila que hay que interpretar, y
    una fila que hay que interpretar termina interpretada distinto por cada
    quien la lea."""
    GANADORA = "ganadora"
    PERDEDORA = "perdedora"
    BREAKEVEN = "breakeven"
    FILL_FALLIDO = "fill_fallido"
    NO_EJECUTADA = "no_ejecutada"


class TarifaFaltanteError(ValueError):
    """Una tarifa ausente o None. CERO DEFAULTS, y es deliberado que sea un
    error y no un warning: el legacy avisaba y seguía con las tarifas de
    BTC, y un warning no detiene un backtest de 4.000 trades."""


@dataclass(frozen=True)
class Tarifas:
    """
    Los cinco componentes, cada uno obligatorio. Fracciones, no porcentajes:
    0,0004 es cuatro centésimas de punto. Se eligió fracción porque es lo
    que consume la aritmética -- guardar porcentajes obliga a dividir por
    100 en cada uso, y ese `/100.0` olvidado en un lugar es un error de dos
    órdenes de magnitud que no rompe ningún test de tipos.

    NO HAY NINGUNA TARIFA GUARDADA EN ESTE MÓDULO. Estos valores los pasa
    quien llama. Verificar las de Binance o Deriv es trabajo aparte, y
    hacerlo acá metería un número sin procedencia dentro de un módulo de
    cálculo, que es donde nadie lo iría a buscar.
    """
    fee_taker: float
    fee_maker: float
    spread: float
    slippage: float
    #: CON SIGNO. Positiva = los largos pagan y los cortos cobran, que es
    #: la convención de los perpetuos. Negativa invierte los dos lados.
    funding_por_periodo: float

    def __post_init__(self) -> None:
        for nombre in ("fee_taker", "fee_maker", "spread", "slippage",
                       "funding_por_periodo"):
            valor = getattr(self, nombre)
            if valor is None:
                raise TarifaFaltanteError(
                    f"Tarifa {nombre!r} ausente. Este módulo no tiene "
                    f"defaults a propósito: un costo que se asume es un "
                    f"backtest inflado sin que nadie se entere.")
            if not isinstance(valor, (int, float)) or isinstance(valor, bool):
                raise TarifaFaltanteError(
                    f"Tarifa {nombre!r} no es un número: {valor!r}")
        for nombre in ("fee_taker", "fee_maker", "spread", "slippage"):
            if getattr(self, nombre) < 0:
                raise ValueError(
                    f"{nombre} no puede ser negativo, recibido: "
                    f"{getattr(self, nombre)}. El único costo que admite "
                    f"signo es funding_por_periodo.")


@dataclass(frozen=True)
class DesgloseCostos:
    """El desglose SIEMPRE viaja con el resultado -- nunca se devuelve solo
    el neto. Un neto sin desglose no se puede auditar: no hay forma de
    saber si se lo comió el fee, el spread o el funding, y esos tres se
    arreglan de maneras distintas."""
    fee_entrada: float
    fee_salida: float
    spread: float
    slippage: float
    #: >0 pagado, <0 cobrado. No se recorta a cero.
    funding: float
    total: float


@dataclass(frozen=True)
class ResultadoTrade:
    """Mismo estilo que GodelScoreResult: nunca un float pelado. `neto` a
    secas no distingue "perdió" de "no se ejecutó", y esas dos cosas
    significan lo contrario cuando se cuentan 200 trades."""
    bruto: float
    neto: float
    desglose: DesgloseCostos
    outcome: Outcome
    #: Instante en que se carga el costo de entrada. Es `ts_entrada`, y está
    #: expuesto para que sea verificable en vez de quedar implícito.
    ts_cargo_entrada: datetime
    periodos_funding: int
    fraccion_completada: float


def _validar_trade(
    *, precio_entrada: float, precio_salida: float, nocional: float,
    fraccion_completada: float, ts_entrada: datetime, ts_salida: datetime,
) -> None:
    if precio_entrada <= 0:
        raise ValueError(f"precio_entrada debe ser positivo: {precio_entrada}")
    if precio_salida <= 0:
        raise ValueError(f"precio_salida debe ser positivo: {precio_salida}")
    if nocional <= 0:
        raise ValueError(f"nocional debe ser positivo: {nocional}")
    if not 0.0 <= fraccion_completada <= 1.0:
        raise ValueError(
            f"fraccion_completada debe estar en [0,1]: {fraccion_completada}")
    if ts_entrada.tzinfo is None or ts_salida.tzinfo is None:
        raise ValueError(
            "ts_entrada y ts_salida deben ser timezone-aware. Un naive "
            "obliga a adivinar la zona, y el funding se cuenta contra horas "
            "UTC fijas: adivinar mal desplaza los períodos.")
    if ts_salida < ts_entrada:
        raise ValueError(
            f"ts_salida ({ts_salida}) es anterior a ts_entrada ({ts_entrada})")


def instantes_de_funding(ts_entrada: datetime, ts_salida: datetime) -> list[datetime]:
    """
    Los instantes de funding ESTRICTAMENTE dentro de la tenencia.

    Los bordes NO cuentan, y es una decisión: en `ts_entrada` la posición se
    está abriendo y en `ts_salida` ya se cerró, así que en ninguno de los
    dos instantes se está devengando. Quien mantenga de 00:00 a 08:00 clavado
    paga cero, y eso es correcto -- no estuvo expuesto a través de ningún
    devengo.
    """
    if ts_salida <= ts_entrada:
        return []

    instantes: list[datetime] = []
    dia = ts_entrada.astimezone(timezone.utc).date()
    ultimo = ts_salida.astimezone(timezone.utc).date()
    while dia <= ultimo:
        for hora in HORAS_FUNDING_UTC:
            t = datetime.combine(dia, time(hour=hora), tzinfo=timezone.utc)
            if ts_entrada < t < ts_salida:
                instantes.append(t)
        dia += timedelta(days=1)
    return instantes


def compute_trade_costs(
    *,
    ts_entrada: datetime,
    ts_salida: datetime,
    lado: Lado,
    precio_entrada: float,
    precio_salida: float,
    nocional: float,
    tarifas: Tarifas,
    entrada_es_taker: bool,
    salida_es_taker: bool,
    fraccion_completada: float = 1.0,
    ejecutada: bool = True,
) -> ResultadoTrade:
    """
    Un trade hipotético -> bruto, neto y desglose por componente.

    `ejecutada=False` es la señal que NO se operó: se registra igual, en
    ceros y con `outcome=NO_EJECUTADA`. Existe porque el ledger tiene que
    poder contar las señales descartadas -- si solo entraran las ejecutadas,
    el archivo no permitiría calcular qué fracción de las señales se
    filtró, que es exactamente la clase de sesgo que un ledger completo
    previene.

    Todos los costos salen en la MISMA MONEDA que `bruto`, calculados sobre
    `nocional`. Es la corrección del defecto de unidades del legacy (ver el
    docstring del módulo).
    """
    _validar_trade(
        precio_entrada=precio_entrada, precio_salida=precio_salida,
        nocional=nocional, fraccion_completada=fraccion_completada,
        ts_entrada=ts_entrada, ts_salida=ts_salida,
    )

    if not ejecutada:
        return _resultado_vacio(ts_entrada, Outcome.NO_EJECUTADA, 0.0)
    if fraccion_completada == 0.0:
        return _resultado_vacio(ts_entrada, Outcome.FILL_FALLIDO, 0.0)

    # El nocional REALMENTE ejecutado. Todo lo de abajo se calcula sobre
    # esto, no sobre el nocional pedido: un fill del 30% paga el fee del
    # 30%, no el del 100%.
    nocional_real = nocional * fraccion_completada

    retorno = (precio_salida - precio_entrada) / precio_entrada
    if lado is Lado.CORTO:
        retorno = -retorno
    bruto = retorno * nocional_real

    fee_entrada = nocional_real * (
        tarifas.fee_taker if entrada_es_taker else tarifas.fee_maker)
    fee_salida = nocional_real * (
        tarifas.fee_taker if salida_es_taker else tarifas.fee_maker)

    # Solo las patas taker cruzan el spread -- ver la decisión de modelo en
    # el docstring del módulo.
    patas_que_cruzan = int(entrada_es_taker) + int(salida_es_taker)
    spread = nocional_real * tarifas.spread * patas_que_cruzan

    # Slippage en las DOS patas: se llenan las dos, y las dos pueden
    # llenarse peor que el precio pedido, sea taker o maker.
    slippage = nocional_real * tarifas.slippage * 2

    periodos = len(instantes_de_funding(ts_entrada, ts_salida))
    signo = 1.0 if lado is Lado.LARGO else -1.0
    funding = nocional_real * tarifas.funding_por_periodo * periodos * signo

    total = fee_entrada + fee_salida + spread + slippage + funding
    neto = bruto - total

    return ResultadoTrade(
        bruto=bruto,
        neto=neto,
        desglose=DesgloseCostos(
            fee_entrada=fee_entrada, fee_salida=fee_salida, spread=spread,
            slippage=slippage, funding=funding, total=total,
        ),
        outcome=_clasificar(neto),
        # EN ts_entrada, no en el midpoint siguiente. Hay un test que falla
        # si alguien lo mueve.
        ts_cargo_entrada=ts_entrada,
        periodos_funding=periodos,
        fraccion_completada=fraccion_completada,
    )


def _resultado_vacio(
    ts_entrada: datetime, outcome: Outcome, fraccion: float,
) -> ResultadoTrade:
    """Nada ejecutado = nada cobrado. Un fee sobre un fill que no ocurrió
    sería un costo inventado, que es el mismo pecado que un costo omitido
    con el signo al revés."""
    cero = DesgloseCostos(fee_entrada=0.0, fee_salida=0.0, spread=0.0,
                          slippage=0.0, funding=0.0, total=0.0)
    return ResultadoTrade(
        bruto=0.0, neto=0.0, desglose=cero, outcome=outcome,
        ts_cargo_entrada=ts_entrada, periodos_funding=0,
        fraccion_completada=fraccion,
    )


def _clasificar(neto: float) -> Outcome:
    if abs(neto) < EPSILON_BREAKEVEN:
        return Outcome.BREAKEVEN
    return Outcome.GANADORA if neto > 0 else Outcome.PERDEDORA
