# Pre-registro H1 — tendencia en BTC con velas diarias de Deriv

**Escrito el 25-sep-2026, en el PR del Brief H1-A, antes de que exista una sola línea
de backtest.** El backtest es el Brief H1-B, que no arranca hasta siete días después de
fusionar este documento.

Este documento **no se modifica después de fusionarse.** `tests/test_preregistro_h1.py`
fija su sha256 y verifica que cada número de `core/preregistro_h1.py` esté escrito acá.
Cualquier cambio de rejilla, ventanas o reglas después de ver un resultado es un
experimento nuevo: va en `preregistro_h1_v2.md`, con su propio N, sin borrar este.

Las marcas **[INTERPRETACIÓN]** señalan una lectura del brief que el Admin aprueba al
fusionar. Las marcas **[PENDIENTE DEL ADMIN]** señalan algo que el brief no fija y que
este documento no inventa: se completan **antes** de fusionar, en este mismo PR.

---

## 1. Hipótesis principal

BTC, velas diarias de Deriv, **solo largos**.

La única candidata a pasar es el **ensemble** que promedia las señales de la rejilla.
Los lookbacks individuales se reportan como diagnóstico y **nunca se promueven**: si el
ensemble falla y un individual pasa, se registra como **evidencia de sobreajuste**, no
como un hallazgo.

**Datos.** `metrics/velas/<símbolo>/86400.jsonl` de la rama `data`, leídas con
`ingestion/velas.py::leer_velas()`. El símbolo es el BTC que califique en la sonda de
instrumentos (`ingestion/sonda_instrumentos.py`). Si califica más de uno, el Admin elige
**antes** de H1-B mirando solo la salida de la sonda —contratos, multiplicadores, stake,
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
`core/preregistro_h1.py::rejilla_soportada()` implementa la regla y no recibe precios.

La profundidad usable es la de `ingestion/velas.py`: las barras del tramo sin huecos
que termina en la última vela. No es la profundidad devuelta por la API.

## 4. Condición de parada

```
historia_usable ≥ lookback_max + 504 + 4 × (lookback_max + 126)
```

Con la rejilla completa (`lookback_max = 320`) son 2.608 barras. Si ni la rejilla
recortada alcanza, **el backtest no se corre** y se decide primero de dónde sale la
historia.

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

## 8. Comisión

Se usa el **máximo** entre:

1. la comisión de la `proposal` medida;
2. el **0,1 % del nocional, con un mínimo de 0,10 USD**, según el documento de
   multiplicadores de cripto de Deriv. **[INTERPRETACIÓN]** El brief dice "el 0,1% del
   nocional más 0,10 USD de mínimo": se lee como un mínimo, no como una suma. La cifra
   es la que citó el Admin; el documento no se pudo leer desde el sandbox.
3. las siete mediciones diarias de la sonda.

La sonda guarda `proposal.commission` **sin convertir**: el esquema oficial la describe
como porcentaje. H1-B fija la unidad mirando las respuestas crudas guardadas; si la
unidad no se puede determinar sin ambigüedad, se detiene y pregunta.

Ningún otro costo se asume en cero sin decirlo: si el documento de Deriv declara otro
cargo para multiplicadores de cripto, H1-B lo incluye.

## 9. Número de ensayos para el DSR

**N efectivo = ensayos del experimento agrupados por correlación de retornos + 3 ensayos
previos.**

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
- **Ensayos previos sobre BTC, ya fallidos y documentados:** máscara Gödel, proxy de
  transfer entropy y EMA 20/63. Suman **3**.

El reporte muestra además el DSR con **N = 10** contado ingenuamente (seis lookbacks,
el ensemble y los tres previos), como cota pesimista.

**Convenciones:**

- Sharpe por barra sobre los retornos netos diarios fuera de muestra; anualizado con
  `√A` (sección 6).
- PSR (Bailey y López de Prado, 2012):
  `PSR(SR*) = Φ( (SR − SR*) · √(T−1) / √(1 − γ₃·SR + (γ₄−1)/4 · SR²) )`, con SR por
  barra, `T` barras fuera de muestra, `γ₃` asimetría y `γ₄` curtosis (no el exceso).
- DSR (Bailey y López de Prado, 2014): `PSR(SR*)` con
  `SR* = √V · ((1−γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e)))`, donde `V` es la varianza de
  los Sharpe por barra de los ensayos del experimento, `γ` la constante de
  Euler–Mascheroni y `N` el N efectivo.

## 10. Compuertas

- **Avanzar** con Sharpe neto fuera de muestra **≥ 0,5**, **PSR(0) ≥ 0,90** y
  **DSR ≥ 0,90**. Las tres.
- Con **Sharpe < 0,3** se prueba una alternativa declarada y, si también falla, se
  detiene.

  **[PENDIENTE DEL ADMIN]** La alternativa. El brief dice "una alternativa declarada" y
  no dice cuál. Tiene que quedar escrita acá antes de fusionar: declararla después de
  ver el Sharpe es exactamente lo que este documento existe para impedir.

- **[PENDIENTE DEL ADMIN]** Qué pasa con **0,3 ≤ Sharpe < 0,5**, o con Sharpe ≥ 0,5 y
  PSR o DSR por debajo de 0,90. El brief no lo fija.

## 11. Réplica en oro

**frxXAUUSD** con **parámetros idénticos, sin reajuste**. Es una réplica, no una
variante, y **no suma al N**. Su calendario es hábil (ver `ingestion/velas.py`) y su
`A` sale de su propia serie, por la misma regla que el de BTC.

## 12. Revisión

Cualquier cambio de rejilla, ventanas o reglas **después de ver un resultado** es un
experimento nuevo, con su propio N, registrado como `preregistro_h1_v2.md` sin borrar
este.
