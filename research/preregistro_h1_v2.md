# Pre-registro H1 v2 — tendencia en BTC con velas diarias

**Escrito el 17-oct-2026, por el brief del Admin 11–13 (tarea 13), antes de que exista una sola
línea de backtest de H1.** Reemplaza para H1 a `research/preregistro_h1.md` (v1), que queda
sellado y sin tocar. v1 no se corrió nunca: no hubo historia suficiente (su sección 4). Para
escribir este documento no se miró ningún precio ni ningún retorno.

Este documento **no se modifica después de fusionarse.** `tests/test_preregistro_h1_v2.py`
fija su sha256 y verifica que cada número de `core/preregistro_h1_v2.py` esté escrito acá.
Cualquier cambio de rejilla, ventanas, reglas o compuertas después de ver un resultado es un
experimento nuevo: va en `preregistro_h1_v3.md`, con su propio N, sin borrar este.

Las marcas **[INTERPRETACIÓN]** señalan una lectura del brief que el Admin aprueba al
fusionar. Las marcas **[PENDIENTE DEL ADMIN]** señalan algo que el brief no fija o que choca
con los datos, y que se completa **antes** de fusionar, en este mismo PR. Las dos de v1 se
cierran acá (sección 10). **Queda una nueva: la fuente de precios (sección 1).**

---

## 0. Qué cambia respecto de v1

| | v1 | v2 | por qué |
|---|---|---|---|
| la σ del PSR | evaluada en el Sharpe observado, con `T−1` | evaluada en SR₀, con `T` y la autocorrelación ρ | D5 (sección 9) |
| Sharpe < 0,3 | se prueba una alternativa, [PENDIENTE DEL ADMIN] | se rechaza y se detiene: no hay alternativa | D12 (sección 10) |
| 0,3 ≤ Sharpe < 0,5, o PSR o DSR < 0,90 | [PENDIENTE DEL ADMIN] | no concluyente: no pasa a demo | D5 (sección 10) |
| los ensayos previos del N | escritos a mano | del registro de ensayos | D3 (sección 9) |
| el paso a real | no lo dice | DG-3, con D2 y DB-3 | v1 es anterior a DG-3 (sección 10) |
| la fuente de precios | las velas diarias de Deriv | [PENDIENTE DEL ADMIN] | Deriv da 365 días (sección 1) |
| la comisión | con las mediciones de una sonda que nunca escribió | con κ medido en la cuenta real | los datos (sección 8) |

Todo lo demás es v1, escrito acá completo para que el documento se lea solo. Los números y
las reglas que no cambian salen del mismo módulo que los de v1, `core/preregistro_h1.py`.

## 1. Hipótesis principal

BTC, velas diarias, **solo largos**.

La única candidata a pasar es el **ensemble** que promedia las señales de la rejilla.
Los lookbacks individuales se reportan como diagnóstico y **nunca se promueven**: si el
ensemble falla y un individual pasa, se registra como **evidencia de sobreajuste**, no
como un hallazgo.

**Datos: [PENDIENTE DEL ADMIN]** La fuente de las velas diarias. v1 decía
`metrics/velas/<símbolo>/86400.jsonl` de la rama `data`, las velas de Deriv, y con ellas H1 no
corre:
- Deriv da **365 días** de historia diaria (decision-log 2026-10-05), y la condición de parada
  (sección 4) pide **1.058** barras con la rejilla mínima y **2.608** con la completa;
- la rama `data` no tiene ninguna vela diaria, porque `velas.yml` nunca escribió (D8);
- v1 lo preveía en su sección 4: "se decide primero de dónde sale la historia". D1 (14-oct) lo
  decide para BTC y el oro: TwelveData, validada contra el año de Deriv con una coincidencia de
  al menos el 90 %. El brief 14 fija la métrica y la hora de corte (D1-p).

**Propuesta de este PR, que no se aplica sin el Admin:** la serie diaria de BTC de D1 que valide
el brief 14. El símbolo que se opera sigue siendo el BTC de Deriv que califique en la sonda de
instrumentos (`ingestion/sonda_instrumentos.py`). Si califica más de uno, el Admin elige
**antes** de correr, mirando solo la salida de la sonda —contratos, multiplicadores, stake,
comisión—, nunca precios ni retornos.

## 2. Señales

**[INTERPRETACIÓN]** El brief fija la salida por la regla original de las Tortugas y no
fija la entrada. Se toma la entrada de la misma regla.

Para cada lookback `L` de la rejilla, `señal_L(t)` vale 1 (comprado) o 0 (afuera):

- **Entrada:** pasa a 1 cuando el cierre de `t` supera el máximo de los máximos de las
  `L` barras anteriores (`t−L .. t−1`).
- **Salida:** canal Donchian de `L / 2` barras. Pasa a 0 cuando el cierre de `t` cae
  debajo del mínimo de los mínimos de las `L / 2` barras anteriores.
- En cualquier otro caso mantiene el valor anterior.

La señal se calcula con la vela `t` **cerrada** y se ejecuta en la apertura de `t+1`.

**Ensemble:** el promedio de las `señal_L` de la rejilla seleccionada. **[INTERPRETACIÓN]**
En la práctica, cada lookback es una sub-posición propia —un contrato MULTUP propio— con
peso `1 / n` del tamaño objetivo, que abre cuando su señal pasa a 1 y cierra cuando pasa
a 0. Así el promedio se ejecuta con contratos que Deriv permite, sin rebalancear un
contrato abierto.

La salida **no se optimiza sobre los datos de BTC.** Si el backtest muestra que una
salida más temprana o más tardía mejora el Sharpe, eso cuenta como variante nueva y
requiere un experimento separado.

## 3. Rejilla y regla de selección

Rejilla: **{10, 20, 40, 80, 160, 320}** días.

Se usa la **rejilla más larga que la profundidad usable soporte, recortando desde
arriba**: primero se quita 320, después 160, y así. La selección se hace **solo con la
longitud de la serie**. **Queda prohibido elegir mirando precios o retornos.**
`core/preregistro_h1.py::rejilla_soportada()` implementa la regla y no recibe precios; v2 usa la
misma.

La profundidad usable es la de `ingestion/velas.py`: las barras del tramo sin huecos
que termina en la última vela. No es la profundidad devuelta por la API.

## 4. Condición de parada

```
historia_usable ≥ lookback_max + 504 + 4 × (lookback_max + 126)
```

Con la rejilla completa (`lookback_max = 320`) son 2.608 barras. Si ni la rejilla
recortada alcanza, **el backtest no se corre** y se decide primero de dónde sale la
historia.

Con las velas de Deriv no alcanza ni la rejilla mínima (sección 1).

## 5. Validación

**Walk-forward anclado:** entrenamiento inicial de **504** barras, **4** folds de
**126** barras fuera de muestra, y antes de cada fold una **purga igual al lookback
máximo** de la rejilla seleccionada. Las primeras `lookback_max` barras solo calientan
los indicadores.

Las reglas no tienen parámetros que ajustar. El tramo de entrenamiento existe para que
cada fold fuera de muestra tenga la misma historia previa que tendría en vivo. Lo que se
evalúa son las **504 barras fuera de muestra concatenadas** (4 × 126).

## 6. Sizing

```
posición = capital × 0,25 / vol_realizada_20d_anualizada
```

con un **tope de apalancamiento de 2×** sobre el capital. La posición es nocional; cada
sub-posición del ensemble lleva `1 / n` de ella.

- `vol_realizada_20d` es el desvío estándar de los retornos logarítmicos de cierre de
  las **20** barras anteriores a la entrada.
- **[INTERPRETACIÓN]** Se anualiza con `√A`, donde `A` es el número medio de barras por
  año calendario en la serie usada. Sale de las fechas, no de los precios: para BTC, con
  calendario continuo, da unas 365; para el oro, con calendario hábil, menos. Mismo `A`
  para anualizar el Sharpe.
- La posición se fija al abrir el contrato y no se rebalancea hasta la salida.

## 7. Multiplicador y stop-out

En cada entrada se elige el **multiplicador más bajo disponible** —de los que la sonda
midió para ese símbolo— tal que la distancia al stop-out sea **al menos 1,5 veces** la
distancia al canal de salida. **Si ninguno cumple, la señal se salta y se cuenta.** El
reporte declara la fracción de señales saltadas.

- Distancia al canal de salida: `(precio de entrada − canal de salida) / precio de entrada`.
- **[INTERPRETACIÓN]** Distancia al stop-out con multiplicador `m`: `1/m − comisión`,
  como fracción del precio de entrada. Es el movimiento adverso que consume el stake.
  H1-B verifica el modelo contra los `limit_order.stop_out.value` que la sonda midió. Si
  el modelo no los reproduce con una tolerancia de un pip del instrumento, H1-B se
  detiene y pregunta. No se ajusta.
- Stake = nocional / `m`. Si queda debajo del stake mínimo medido, la señal se salta y
  se cuenta; si queda encima del máximo, se recorta al máximo y se cuenta.

**El backtest modela solo lo que Deriv permite ejecutar.**

**Lo que ya está medido.** La sonda de instrumentos no escribió nunca (D8). Los multiplicadores,
los stakes y el stop-out de BTC están medidos en las sondas §0.A-3 (decision-log 2026-10-05 y
2026-10-06), y el modelo de stop-out, `1/m − comisión/nocional`, está verificado contra las
cotizaciones (2026-10-05, d). **[INTERPRETACIÓN]** Si al correr la sonda de instrumentos sigue
sin datos, H1 usa esas mediciones: son las mismas respuestas de la API, medidas antes.

## 8. Comisión

Se usa el **máximo** entre:

1. **κ medido en la cuenta real**, como fracción del nocional: `ingestion/kappa_deriv.py`, que
   da **0,0003** para `cryBTCUSD` (decision-log 2026-10-06, d);
2. el **0,1 % del nocional, con un mínimo de 0,10 USD**, según el documento de multiplicadores
   de cripto de Deriv, la cifra que citó el Admin para v1.

Ningún otro costo se asume en cero sin decirlo. Los multiplicadores cobran una comisión única,
sin funding (decisión a) del Admin del 06-oct; D4-c).

**Lo que cambia respecto de v1, por los datos y no por un criterio:**
- v1 sumaba al máximo "las siete mediciones diarias de la sonda": no existen, porque la sonda
  nunca escribió (D8). Si existen antes de correr, entran al máximo.
- v1 dejaba la unidad de `proposal.commission` para el backtest: ya está fijada, y es un monto
  en la moneda de la cuenta (decision-log 2026-10-05, d).

## 9. Número de ensayos y estadística del Sharpe

**N efectivo = ensayos del experimento agrupados por correlación de retornos + los ensayos
previos fallidos del registro de ensayos.**

- **Ensayos del experimento:** los seis lookbacks y el ensemble (siete con la rejilla
  completa; menos si se recorta).
- **Método de agrupamiento, declarado acá:** con `C` la matriz de correlación de los
  retornos diarios fuera de muestra de los `k` ensayos, el número efectivo es la razón
  de participación de sus autovalores, redondeada hacia arriba:

  ```
  N_exp = ⌈ (Σ λᵢ)² / Σ λᵢ² ⌉ = ⌈ k² / Σᵢⱼ ρᵢⱼ² ⌉
  ```

  Vale `k` si los ensayos son independientes y 1 si son idénticos. No tiene umbral
  libre: no hay un corte de correlación que elegir después de ver los datos.
- **Ensayos previos:** los fallidos de hipótesis previa a la Serie H que cuentan en el N del
  registro de ensayos (D3, `governance.ensayos.previos_fallidos()`): máscara Gödel, proxy de
  transfer entropy y EMA 20/63. Suman **3**.

El reporte muestra además el DSR con **N = 10** contado ingenuamente (seis lookbacks,
el ensemble y los tres previos), como cota pesimista.

**La estadística del Sharpe (D5)** sale de `core/estadistica_sharpe.py`, la única
implementación (brief 10), sobre los retornos netos diarios fuera de muestra:

- `T` barras fuera de muestra; Sharpe por barra `SR = r̄ / σ`; anualizado con `√A` (sección 6).
- Los momentos de la serie, con `T` en el denominador: `m_k = (1/T) · Σ (r − r̄)^k`,
  `σ² = m₂`, `γ₃ = m₃ / m₂^(3/2)` y `γ₄ = m₄ / m₂²` (la curtosis, no el exceso).
- **ρ, estimado de la serie:** `ρ = Σ_{t=2}^{T} (r_t − r̄)(r_{t−1} − r̄) / Σ_{t=1}^{T} (r_t − r̄)²`, la
  autocorrelación de orden 1 de los mismos retornos.
- **PSR(0)** = `psr(SR, 0, t=T, asimetria=γ₃, curtosis=γ₄, rho=ρ)`: la σ se evalúa en SR₀ = 0.
- **DSR** = `dsr(SR, t=T, asimetria=γ₃, curtosis=γ₄, varianza_ensayos=V, n_efectivo=N, rho=ρ)`, es
  decir PSR(SR*), con `SR* = √V · ((1−γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e)))`, donde `V` es la
  varianza de los Sharpe por barra de los ensayos del experimento, `γ` la constante de
  Euler–Mascheroni y `N` el N efectivo.
- **MinTRL**, como diagnóstico y no como compuerta: las barras que harían falta para que PSR(0)
  llegue a 0,90 con el Sharpe observado, `min_trl(SR, 0, …, z = Φ⁻¹(0,90))`.

**El cambio respecto de v1, a propósito y antes de ver datos.** v1 escribía el PSR de Bailey y
López de Prado (2012): `Φ( (SR − SR*) · √(T−1) / √(1 − γ₃·SR + (γ₄−1)/4 · SR²) )`, con la σ
evaluada en el Sharpe **observado** y `T−1`. v2 la evalúa en **SR₀**, con `T` y con ρ, por dos
razones:
- **El PSR prueba la hipótesis nula SR = SR₀**, y su denominador es el desvío del estimador bajo
  esa nula: el de SR₀, no el del Sharpe que salió. Así lo calcula el código de réplica de López
  de Prado, Lipton y Zoonekynd, contra el que se contrastó el módulo; el texto del paper sigue
  UNVERIFIED desde este entorno (brief 10).
- **ρ corrige la varianza por la autocorrelación** de los retornos diarios, que una regla de
  tendencia puede tener. Es la solidez frente a retornos no normales que pide D5.

**No es más estricto en todos los casos.** Con el vector del brief 10 (γ₃ = −2,448,
γ₄ = 10,164, T = 24, SR = 0,45570), v1 da PSR(0) = 0,913. v2 da 0,987 con ρ = 0 y 0,9658 con
ρ = 0,2 (`tests/test_estadistica_sharpe.py`). Se cambia porque es el desvío de la nula, no
porque endurezca la compuerta; lo decide D5, antes de mirar.

## 10. Compuertas

Con el Sharpe neto fuera de muestra **anualizado** de la **serie base, BTC**, porque el oro es
una réplica (sección 11). **[INTERPRETACIÓN]** Las compuertas de v1 no dicen si el Sharpe es por
barra o anualizado; sus convenciones lo anualizan con `√A` (sección 9).

| resultado | veredicto |
|---|---|
| Sharpe **≥ 0,5**, **PSR(0) ≥ 0,90** y **DSR ≥ 0,90**, las tres | **avanza** a forward en demo |
| Sharpe **< 0,3** | **se rechaza** |
| cualquier otro caso: 0,3 ≤ Sharpe < 0,5, o Sharpe ≥ 0,5 con PSR o DSR debajo de 0,90 | **no concluyente** |

- **Rechazo (D12, decisión del Admin del 17-oct).** La alternativa declarada es **ninguna**. La
  estrategia se rechaza, el proceso se detiene y no suma ensayos (N) de optimización
  correctiva. Cierra el primer [PENDIENTE DEL ADMIN] de v1. Como no hay alternativa, no hay
  nada que sumar al N por ella.
- **No concluyente (D5).** No pasa a demo: lo dudoso no se trata como hallazgo. Cierra el
  segundo [PENDIENTE DEL ADMIN] de v1. Qué sigue lo decide el Admin, y una corrida nueva con más
  datos es otro experimento.
- **Avanza.** Forward en demo con las reglas congeladas. El paso de demo a real es DG-3, tal
  como está en `governance/paso_a_real.py` (decision-log 29-sep, con D2 del 14-oct y DB-3 del
  17-oct). Su a) se mide **solo sobre el backtest histórico** de esta sección, y la demo valida
  infraestructura, fricciones, latencia y reconciliación: no vuelve a medir el Sharpe.

`core/preregistro_h1_v2.py::veredicto()` implementa la tabla, y no recibe nada más que esos
tres números.

## 11. Réplica en oro

**frxXAUUSD** con **parámetros idénticos, sin reajuste**. Es una réplica, no una
variante, y **no suma al N**. Su calendario es hábil (ver `ingestion/velas.py`) y su
`A` sale de su propia serie, por la misma regla que el de BTC.

## 12. Registro de ensayos (D3)

H1 v2 entra en `governance/ensayos.jsonl` como **pre-registrada, no corrida**, con una línea,
`h1-v2`, que cuenta en el N. Sus ensayos —el ensemble y los lookbacks que la historia
soporte— se cuentan al correr (sección 9), y cada uno suma su línea. v1 no se registra: se
reemplaza antes de correrse, sin ningún resultado.

## 13. Revisión

Cualquier cambio de rejilla, ventanas, reglas o compuertas **después de ver un resultado** es
un experimento nuevo, con su propio N, registrado como `preregistro_h1_v3.md` sin borrar
este.
