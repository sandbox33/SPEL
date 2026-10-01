# Pre-registro H3 — tendencia multiactivo

**Escrito el 29-sep-2026, en el PR-H3 del Brief final v3, antes de que se fusione el PR #31.**
**Revisado el 01-oct-2026, todavía sin fusionar,** con dos decisiones del Admin del 29-sep:
el sizing (sección 6) y las condiciones del paso de demo a real (DG-3, sección 12).
Al fusionar el #31 el cron empieza a escribir los precios diarios del universo en la rama
`data`; este documento tiene que existir antes de que haya un precio que mirar. Para
escribirlo no se leyó ninguna vela ni ningún retorno: solo reglas.

**H3 es la ruta principal** de la Serie H (DG-5, decision-log 25-sep-2026).

Este documento **no se modifica después de fusionarse.** `tests/test_preregistro_h3.py` fija
su sha256 y verifica que cada número de `core/preregistro_h3.py` esté escrito acá.
Cualquier cambio de universo, rejilla, ventanas o reglas después de ver un resultado es un
experimento nuevo: va en `preregistro_h3_v2.md`, con su propio N, sin borrar este.

**Es autocontenido a propósito.** H3 usa por activo las reglas de H1, pero cuando este
documento se fusiona `research/preregistro_h1.md` todavía no está en `main`, y además
cambia en el cierre de H1-A (PR-0). Por eso las reglas que H3 toma de H1 están escritas
acá completas: lo que vale para H3 es este texto, no el de H1.

Las marcas **[INTERPRETACIÓN]** señalan una lectura del brief que el Admin aprueba al
fusionar.

---

## 1. Hipótesis

Una regla de tendencia de largo plazo, aplicada igual a cada instrumento de un universo
amplio, **solo largos**, gana dinero neto de costos en Deriv. Un contrato por activo.

## 2. Universo: por reglas, nunca por precios

Entra **todo instrumento no sintético con MULTUP según la sonda de instrumentos** y con
**historia_usable ≥ lookback_max + 756**.

- "No sintético" y "con MULTUP" son lo que registra la sonda del PR #31
  (`metrics/instrumentos/universo_<fecha>.json` en la rama `data`).
- `historia_usable` es la de `ingestion/velas.py`: las barras del tramo final sin huecos.
  Sale de las **fechas** de las velas, no de sus precios.
- **[INTERPRETACIÓN]** `lookback_max` es el mayor lookback de la rejilla que ese activo
  soporta (sección 3), así que la condición se cumple si el activo soporta al menos la
  rejilla mínima: `historia_usable ≥ 10 + 756`. La lectura alternativa —`lookback_max`
  fijo en 320 para todos— dejaría afuera a todo activo con menos de 1.076 barras usables.

**La lista concreta** sale de la **primera sonda posterior a la fusión del #31**, se
escribe en `research/universo_h3.json` y **se congela con su sha256 antes de correr
cualquier cosa**. Esa congelación va en su propio PR, con una entrada en el decision-log
que registre el sha256; ningún cálculo de H3 corre antes de que esté fusionada. Después
de congelada, el universo no cambia: un instrumento que aparezca más tarde entra, si
entra, en un experimento nuevo.

## 3. Rejilla por activo

Rejilla: **{10, 20, 40, 80, 160, 320}** días.

Cada activo usa la **rejilla más larga que su historia usable soporte, recortando desde
arriba**, con la condición `historia_usable ≥ lookback_max + 756`. La selección se hace
**solo con la longitud de la serie**. **Queda prohibido elegir mirando precios o
retornos.** `core/preregistro_h3.py::rejilla_soportada()` implementa la regla y no recibe
precios.

## 4. Señal: un contrato por activo (DG-4)

**Por lookback** —las reglas de H1, escritas acá—, `señal_L(t)` vale 1 o 0:

- **Entrada:** pasa a 1 cuando el cierre de `t` supera el máximo de los máximos de las
  `L` barras anteriores (`t−L .. t−1`).
- **Salida:** canal Donchian de `L / 2` barras. Pasa a 0 cuando el cierre de `t` cae
  debajo del mínimo de los mínimos de las `L / 2` barras anteriores.
- En cualquier otro caso mantiene el valor anterior.

**Por activo, un solo contrato:** está **largo si al menos la mitad de los lookbacks
soportados están largos**, y afuera en caso contrario. Con `n` lookbacks que votan y `a`
de ellos en 1, largo si `2·a ≥ n`.

La señal se calcula con la vela `t` **cerrada** y se ejecuta en la apertura de `t+1`.

**Lookbacks no ejecutables.** Un lookback que salta **más del 50 %** de sus señales por
falta de multiplicador o de stake es no ejecutable: **se excluye del voto de ese activo y
se reporta**. **[INTERPRETACIÓN]** En H3 el contrato es uno por activo, así que un
lookback no "salta" señales por sí mismo; lo que se evalúa es qué pasaría si sus señales
se ejecutaran como las de H1: para cada activo y cada lookback, se aplica a sus señales la
regla de multiplicador y stake de H1 —el multiplicador más bajo disponible tal que la
distancia al stop-out sea al menos 1,5 veces la distancia a su canal de salida, y un stake
dentro del rango medido—, y se cuenta la fracción de señales que no tendrían contrato.
Si pasa del 50 %, ese lookback no vota en ese activo. Si en un activo no vota ninguno, el
activo no se opera y se reporta.

## 5. Evaluación

Las reglas no tienen parámetros que ajustar: **toda la historia posterior al calentamiento
es fuera de muestra**. Cada activo entra a la cartera cuando completa sus primeras
`lookback_max` barras.

- La cartera se evalúa en **días de calendario**: un activo sin barra ese día (fin de
  semana del forex, feriado) aporta retorno cero.
- **[INTERPRETACIÓN]** Se anualiza con `√A`, donde `A` es el número medio de barras por
  año calendario de la serie que se anualiza: la de la cartera para su Sharpe; la de cada
  activo para su volatilidad. Sale de las fechas, no de los precios.

## 6. Sizing

Un **target de volatilidad de cartera de 0,25**, repartido entre los K activos como
`0,25 / √K`, con un **escalador de cartera `s`** y un **tope de 2× por activo** sobre el
capital (decisión del Admin del 29-sep-2026):

```
nocional_activo = capital × (0,25 / √K) × s / vol_realizada_20d_anualizada_del_activo
s = min(1 ; 0,25 / σ̂_cartera)
```

- `vol_realizada_20d` es el desvío estándar de los retornos logarítmicos de cierre de las
  **20** barras anteriores a la entrada.
- `K` es la cantidad de activos de la cartera con el calentamiento completo ese día.
- `σ̂_cartera` es el desvío estándar anualizado de los últimos **60** días de retornos
  diarios de la cartera, calculados con los pesos vigentes.
- `0,25 / √K` alcanza el target si los activos son independientes y lo supera si están
  correlacionados; `s` lo corrige con la correlación medida: si la cartera de los últimos
  60 días habría tenido más de 0,25 de volatilidad, todo nocional nuevo se achica en esa
  proporción. `s` nunca agranda: su tope es 1.
- **`s` se aplica solo al abrir un contrato.** Una posición abierta no se reescala.
- **[INTERPRETACIÓN] Los pesos vigentes** son los de la cartera tal como quedaría al abrir:
  las posiciones abiertas, con el nocional que fijaron al abrir, más la que se abre con su
  nocional antes de `s`, cada uno dividido por el capital. Con esos pesos fijos se
  reconstruyen los 60 retornos diarios de la cartera.
- **[INTERPRETACIÓN] Los 60 días son de calendario**, igual que la evaluación (sección 5):
  un activo sin barra ese día aporta retorno cero. Se anualiza con el `√A` de la serie de
  la cartera. Si `σ̂_cartera` es cero, `s = 1`.
- En cada activo se usa el **multiplicador más bajo disponible** según la sonda.
- Stake = nocional / multiplicador.
- La posición se fija al abrir el contrato y no se rebalancea hasta la salida.
- No hay tope de apalancamiento de la cartera además del de cada activo y de `s`. Con
  100 USD, un nocional por activo de ese tamaño va a quedar muchas veces por debajo del
  stake mínimo, y esos activos no se operan (sección 9).

## 7. Stop-out y reentrada

- **[INTERPRETACIÓN]** El contrato toca el stop-out cuando el movimiento adverso llega a
  `1/m − comisión`, como fracción del precio de entrada, con `m` el multiplicador. Es el
  modelo de H1, que se verifica contra los `limit_order.stop_out.value` que mide la
  sonda; si no los reproduce con una tolerancia de un pip, el backtest se detiene y
  pregunta.
- **[INTERPRETACIÓN]** Si toca el stop-out y el voto del activo sigue largo, **reentra en la
  apertura siguiente** pagando comisión.
- Si `max_contract_duration` es finita, al vencer cierra y reabre pagando comisión.
- El stop-out se evalúa contra el `low` de cada barra. Si el orden dentro de la barra no se
  puede resolver, se asume el peor caso.

**El backtest modela solo lo que Deriv permite ejecutar.**

## 8. Costos

Se usa el **máximo** entre la comisión medida por la sonda —todas sus mediciones— y la
referencia del **KID oficial de Deriv para Multiplicadores** del mercado del activo:

- **Cripto** (KID actualizado al 27-ago-2026): tasa de **0,001** sobre el nocional, con un
  mínimo de **0,10** USD.
- **Forex** (KID actualizado al 27-jul-2026): el ejemplo del KID para EURUSD es
  **0,000199**.
- Los dos KID declaran "one-off costs only": **sin costos por mantener la posición**.

**Piso de 0,10 USD** por contrato en todos los mercados.

**[INTERPRETACIÓN]** Para un mercado sin KID citado acá, la referencia no existe y el costo
es el máximo de lo medido, con el piso. Las cifras de los KID son las que citó el Admin
en el brief; los documentos no se pudieron leer desde el sandbox.

La unidad de `proposal.commission` —el esquema oficial la describe como porcentaje— se fija
con las respuestas crudas que guarda la sonda. Si no se puede determinar sin ambigüedad,
el backtest se detiene y pregunta.

## 9. Capital

**100 USD** (DG-2). Un activo cuyo stake queda por debajo del stake mínimo medido **no se
opera y se reporta**; uno que queda por encima del máximo se recorta al máximo y se
cuenta.

**Reporte obligatorio a 1.500 USD**, como diagnóstico: con qué fracción del universo se
podría operar con más capital. **No es compuerta y no suma al N.**

## 10. Benchmark

Una **cartera siempre-larga** con el mismo universo, el mismo sizing (`0,25 / √K` con el
escalador `s` sobre sus propios 60 días, vol de 20 barras, tope de 2× por activo), los
mismos costos, el mismo capital y el multiplicador
más bajo disponible en cada activo. Si toca el stop-out, reentra en la apertura siguiente
pagando comisión; si `max_contract_duration` es finita, cierra y reabre al vencer, pagando
comisión.

No es un ensayo y **no suma al N**.

## 11. Número de ensayos para el DSR

**El N de H3 incluye los ensayos de H1 y cada variante de H3.**

- Los de H1, con el método que declara su pre-registro: los ensayos de su rejilla y su
  ensemble agrupados por la razón de participación de su matriz de correlación de
  retornos, `⌈k² / Σᵢⱼ ρᵢⱼ²⌉`, más los **3** ensayos previos sobre BTC ya fallidos
  (máscara Gödel, proxy de transfer entropy, EMA 20/63).
- Las variantes de H3: **una**, la cartera de la sección 4.

**H1 queda como componente de H3**: si BTC entra al universo por las reglas de la
sección 2, sus señales por lookback son las de H1. Se declara así, y por eso los ensayos
de H1 suman al N de H3.

El reporte muestra además el DSR con **N = 11** contado ingenuamente (los seis lookbacks
y el ensemble de H1, los tres previos y la cartera de H3), como cota pesimista.

**Convenciones**, las mismas de H1:

- Sharpe por barra sobre los retornos netos diarios de la cartera; anualizado con `√A`.
- PSR (Bailey y López de Prado, 2012):
  `PSR(SR*) = Φ( (SR − SR*) · √(T−1) / √(1 − γ₃·SR + (γ₄−1)/4 · SR²) )`, con SR por
  barra, `T` barras evaluadas, `γ₃` asimetría y `γ₄` curtosis (no el exceso).
- DSR (Bailey y López de Prado, 2014): `PSR(SR*)` con
  `SR* = √V · ((1−γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e)))`, donde `V` es la varianza de
  los Sharpe por barra de los ensayos, `γ` la constante de Euler–Mascheroni y `N` el de
  arriba.

## 12. Compuertas

- **Avanzar a forward:** Sharpe neto **≥ 0,5**, **PSR(0) ≥ 0,90** y **DSR ≥ 0,90**, **y** la
  cartera supera al benchmark en Sharpe neto **o** en Calmar (CAGR / MaxDD). "Avanzar"
  significa **forward en demo** con las reglas congeladas, no capital real.
- **Detener → H2:** Sharpe neto **< 0,3**. No se prueba ninguna variante de tendencia.
- **Zona gris:** cualquier otro caso. También va a forward en demo con las reglas
  congeladas, con la misma salida que el caso de avanzar.
- **Paso de demo a real (DG-3, decision-log 29-sep-2026).** Exige **todas**:
  a) **PSR ≥ 0,90** y **DSR ≥ 0,90** sobre retornos diarios de cartera, en la serie
  combinada histórico + demo, con el mismo N (sección 11); b) **≥ 30 días** de demo y
  **≥ 20 operaciones cerradas** en demo; c) costos observados en demo **≤ 1,25 ×** costos
  modelados (sección 8); d) reconciliación demo sin discrepancias. El forward sigue hasta
  cumplirlas, con un **máximo de 6 meses**; al vencer sin cumplirlas, H2. Sin prórroga.
- **[INTERPRETACIÓN]** Además de a)–d), el paso a real exige que la serie combinada
  histórico + demo apruebe también el resto de la compuerta de avanzar: Sharpe neto
  ≥ 0,5 y superar al benchmark en Sharpe neto o en Calmar. Es la lectura más estricta: el
  texto anterior de este pre-registro pedía "las mismas compuertas sobre histórico +
  forward", y DG-3 no dice que se eliminen.

## 13. Revisión

Cualquier cambio de universo, rejilla, ventanas o reglas **después de ver un resultado**
es un experimento nuevo, con su propio N, registrado como `preregistro_h3_v2.md` sin
borrar este.
