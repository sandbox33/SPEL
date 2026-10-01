# Pre-registro H2 — la entropía GDELT como predictor de volatilidad

**Escrito el 29-sep-2026, en el PR-H2 del Brief final v3. Revisado el 01-oct-2026, todavía
sin fusionar,** con la decisión del Admin del 29-sep sobre el rezago (sección 3). El código de evaluación va en
otro PR, después de este. Para escribirlo no se calculó ninguna relación entre entropía y
volatilidad: de la serie GDELT solo se miraron sus **fechas**, al verificar la siembra el
29-sep (decision-log).

Este documento **no se modifica después de fusionarse.** `tests/test_preregistro_h2.py` fija
su sha256 y verifica que cada número de `core/preregistro_h2.py` esté escrito acá.
Cualquier cambio de modelo, pérdida, test o muestra después de ver un resultado es un
experimento nuevo: `preregistro_h2_v2.md`, sin borrar este.

Las marcas **[INTERPRETACIÓN]** señalan una lectura del brief que el Admin aprueba al
fusionar. Las marcas **[PENDIENTE DEL ADMIN]** señalan algo que el brief no fija o que
choca con los datos, y que se completa **antes** de fusionar, en este mismo PR. La única
que hubo, el rezago de la sección 3, la resolvió el Admin el 29-sep.

---

## 1. Pregunta

¿La entropía de `t−2` (días de calendario) mejora la predicción de `|r_t|` respecto de un
HAR-RV con los mismos datos? **Primero BTC; después XAU, como réplica.**

Viene de la medición del 4-sep-2026: dentro del régimen de entropía alta, la volatilidad
de BTC fue **1,246** veces la de afuera. Esa medición fue contemporánea —entropía y
retorno del mismo día—, y el Admin la declaró no operativa hasta pasar el rezago y la
comparación contra HAR-RV (Brief H1-A, 25-sep; la entrada del decision-log llega con el
PR #31). Este documento fija cómo se hace esa prueba.

## 2. Datos

- **Entropía:** `entropy_shannon` de `metrics/gdelt_series/BTC.jsonl` y `XAU.jsonl` en la
  rama `data`, leída con `ingestion/gdelt_series.py::read_series()`. Un día con
  `insufficient_events` no tiene entropía.
- **Precios:** las velas diarias de Deriv que persiste `ingestion/velas.py`, leídas con
  `leer_velas()`. BTC es el mismo símbolo que use H1; XAU es `frxXAUUSD`.
- `r_t` es el retorno logarítmico de cierre a cierre entre dos velas consecutivas del
  calendario del activo (continuo para BTC; hábil para XAU, donde el lunes abarca el fin
  de semana). Si falta una vela que el calendario esperaba, el retorno que la cruzaría
  **no se usa**.
- **Muestra:** la intersección de las dos series. **Se excluye** todo día sin la entropía
  que el modelo necesita. La exclusión se decide acá, antes de mirar: incluye los
  huecos heredados de la serie GDELT —el mayor es un tramo de 18 días, del 14-jun al
  1-jul-2025— y los días con `insufficient_events`. No se rellena nada.

## 3. Qué entropía usa cada retorno

**Decisión del Admin del 29-sep-2026: la entropía con fecha `d` se usa solo para retornos
de días `≥ d + 2`, en días de calendario.** Para el retorno del día `t`, la entropía es la
de la fecha `t − 2` días, unida por fecha (*as-of* sobre `fecha − 2 días`), **no** por un
corrimiento de barras: en XAU, el lunes usa la entropía del **sábado**, no la del jueves
que daría correr dos velas hábiles.

Por qué 2 y no 1: el archivo del día `t−1` sale **durante** el día `t`, no
a su comienzo. `ingestion/run_gdelt.py` lo fija en `DIAS_DE_RETRASO_DE_PUBLICACION = 1`, y
la reconciliación de 404 existe porque a las 06:30 UTC a veces todavía no está. Las velas
diarias de Deriv van de 00:00 a 00:00 UTC, así que al abrir el día `t` la entropía de
`t−1` no se conoce y la de `t−2` sí. Con esto la prueba responde si la entropía sirve con
la información disponible a la hora de decidir. La lectura de rezago 1 queda descartada.

`REZAGO_ENTROPIA_DIAS_CALENDARIO = 2` en el módulo.

**[INTERPRETACIÓN] Tolerancia cero** (`TOLERANCIA_ASOF_DIAS = 0`): si la fecha `t − 2` no
tiene entropía —un hueco de la serie o un día con `insufficient_events`—, el retorno de
`t` **se excluye**; no se toma una entropía más vieja. Un *as-of* hacia atrás sin
tolerancia arrastraría la entropía del 13-jun-2025 a lo largo de todo el tramo de 18 días
sin datos, y la sección 2 fija que no se rellena nada.

## 4. Modelos

Los dos se estiman igual y sobre la misma muestra. Solo difieren en la entropía.

**Varianza diaria de los regresores** —Garman–Klass, con la vela `t` del activo—:
`v_t = 0,5 · ln(H_t / L_t)² − (2·ln 2 − 1) · ln(C_t / O_t)²`.

**[INTERPRETACIÓN]** "HAR-RV con los mismos datos": con velas diarias no hay varianza
realizada intradía, y el estimador de rango de Garman–Klass es lo más cercano que dan las
mismas velas. Sus componentes, en barras del calendario del activo:

| calendario | diario | semanal | mensual |
|---|---|---|---|
| continuo (BTC) | **1** | **7** | **30** |
| hábil (XAU) | **1** | **5** | **22** |

- **Modelo A (HAR-RV):**
  `ln v_t = a + b_d · ln v_(d) + b_w · ln v_(w) + b_m · ln v_(m) + día_de_la_semana + ε`,
  donde cada `v_(·)` es el promedio de `v` en las barras de esa ventana que terminan en
  `t−1`.
- **Modelo B (HAR-RV + entropía):** el modelo A más `c · e_(t−2)`, con `t−2` la fecha de
  la sección 3 y `e` la entropía **desestacionalizada**: su valor menos el promedio de su
  mismo día de la semana en la ventana de estimación.

**Estimación:** mínimos cuadrados, ventana **expansiva** que arranca con **504** barras y se
reestima en cada barra. **Pronóstico** de la varianza de `r_t`:
`h_t = exp(ln v̂_t) · s`, donde `s` es el promedio de `r² / exp(ln v̂)` en la ventana de
estimación. Ese factor corrige a la vez el sesgo de la exponencial y la diferencia de
escala entre el rango de la vela y el retorno de cierre a cierre, y se estima igual en
los dos modelos.

## 5. Control por día de la semana

GDELT tiene un ciclo semanal fuerte: la cantidad de noticias, y con ella la entropía,
cambia con el día. La volatilidad también. Sin control, la entropía podría "predecir" solo
que es fin de semana. Por eso, las dos cosas:

- **indicadoras del día de la semana del día `t` en los dos modelos**: lo que el día de la
  semana explique de la volatilidad lo explica también el modelo A;
- **la entropía entra desestacionalizada** en el modelo B, con los promedios por día de
  la semana de la ventana de estimación, nunca de la muestra completa.

Nunca se usa `n_events` crudo.

## 6. Pérdida

**QLIKE**, fuera de muestra, contra el proxy `r_t²`:

```
L(h_t, r_t) = ln h_t + r_t² / h_t
```

**Se elige QLIKE y no MSE.** `r_t²` es el proxy insesgado de la varianza de `r_t`, que es lo
que un pronóstico de la magnitud `|r_t|` tiene que acertar; y con un proxy ruidoso,
QLIKE ordena los pronósticos igual que la varianza verdadera (Patton, 2011). Esta forma de
la pérdida difiere de la usual en términos que no dependen del pronóstico, y admite
`r_t = 0`.

## 7. Test

**Diebold–Mariano, unilateral**, sobre `d_t = L_A,t − L_B,t`. La hipótesis alternativa es
que el modelo B pierde menos: `E[d_t] > 0`.

- **Alfa fijado antes: 0,05.**
- Varianza de `d̄` por Newey–West, con ancho de banda `⌊4 · (T/100)^(2/9)⌋` y la corrección
  de muestra chica de Harvey, Leybourne y Newbold (1997).
- `T` es la cantidad de pronósticos fuera de muestra: todos los días de la muestra
  posteriores a las primeras 504 barras.

## 8. Réplica en XAU

El mismo diseño, **sin cambiar ninguna elección** después de ver BTC: los mismos modelos,
la misma pérdida, el mismo test y el mismo alfa, con las ventanas del calendario hábil.
Es una réplica, no un segundo intento: se reporta pase lo que pase y no mueve la
conclusión de BTC.

## 9. Qué se concluye

- **BTC con `p < 0,05`:** la entropía tiene contenido predictivo sobre la volatilidad más
  allá de HAR-RV. El 1,246× pasa a ser candidato para el dimensionamiento, con su propio
  pre-registro. La réplica en XAU dice si generaliza.
- **BTC con `p ≥ 0,05`:** la entropía no mejora a HAR-RV con estos datos y no entra al
  dimensionamiento. Se reporta el tamaño del efecto igual.

## 10. Revisión

Cualquier cambio de modelo, pérdida, test, alfa, rezago o muestra **después de ver un
resultado** es un experimento nuevo, registrado como `preregistro_h2_v2.md` sin borrar
este.
