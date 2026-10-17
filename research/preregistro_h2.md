# Pre-registro H2 — la entropía GDELT como predictor de volatilidad

**Escrito el 29-sep-2026, en el PR-H2 del Brief final v3. Revisado dos veces, todavía sin
fusionar:**
- el 01-oct-2026, con la decisión del Admin del 29-sep sobre el rezago (sección 3);
- el 17-oct-2026, con D6, la decisión del Admin del 14-oct (brief 11). Trae el rezago de 1 día
  como secundario, los horizontes 1, 5 y 22 con MCS y Diebold–Mariano, y el día de la semana
  estimado solo en entrenamiento.

El código de evaluación va en otro PR, después de este. Para escribirlo no se calculó ninguna
relación entre entropía y volatilidad, y no se miró ningún precio. De la serie GDELT se miraron
dos veces solo lo que no la relaciona con nada:
- el 29-sep, sus **fechas**, al verificar la siembra (decision-log);
- el 17-oct, su rango, del 2013-04-01 al 2026-10-16 en `data@69a0e45`, y su primera línea, para
  conocer el formato.

Este documento **no se modifica después de fusionarse.** `tests/test_preregistro_h2.py` fija
su sha256 y verifica que cada número de `core/preregistro_h2.py` esté escrito acá.
Cualquier cambio de modelo, pérdida, test o muestra después de ver un resultado es un
experimento nuevo: `preregistro_h2_v2.md`, sin borrar este.

Las marcas **[INTERPRETACIÓN]** señalan una lectura del brief que el Admin aprueba al
fusionar. Las marcas **[PENDIENTE DEL ADMIN]** señalan algo que el brief no fija o que
choca con los datos, y que se completa **antes** de fusionar, en este mismo PR. La del
rezago la resolvió el Admin el 29-sep. **Queda una: la fuente de precios (sección 2).**

---

## 1. Pregunta

¿La entropía GDELT mejora el pronóstico de la varianza de los próximos `h` días respecto de
un HAR-RV con los mismos datos, para `h` = 1, 5 y 22? **Primero BTC; después XAU, como
réplica.**

- **La primaria** es la entropía de `t−2` (días de calendario). Es la que se conoce al abrir
  el día `t` (sección 3), y la única que decide.
- **La secundaria** es la entropía de `t−1`. Al abrir `t` todavía no está publicada, así que no
  es operativa: responde la prueba que pedía el decision-log del 25-sep ("El rezago a `t−1`")
  y **nunca decide** la conclusión. Cuenta en el N (D3).

Viene de la medición del 4-sep-2026: dentro del régimen de entropía alta, la volatilidad
de BTC fue **1,246** veces la de afuera. Esa medición fue contemporánea —entropía y
retorno del mismo día—, y el Admin la declaró no operativa hasta pasar el rezago y la
comparación contra HAR-RV (Brief H1-A, 25-sep; decision-log 2026-09-25). Este documento fija
cómo se hace esa prueba.

## 2. Datos

- **Entropía:** `entropy_shannon` de `metrics/gdelt_series/BTC.jsonl` y `XAU.jsonl` en la
  rama `data`, leída con `ingestion/gdelt_series.py::read_series()`. Un día con
  `insufficient_events` no tiene entropía.
- **Precios: [PENDIENTE DEL ADMIN]** La fuente de las velas diarias. Este documento decía "las
  velas diarias de Deriv que persiste `ingestion/velas.py`", y con ellas H2 no corre:
  - Deriv da **365 días** de historia diaria, con `end` ignorado (decision-log 2026-10-05,
    sonda §0.A-2), y la ventana de estimación arranca con **504** barras (sección 4). No queda
    ningún pronóstico fuera de muestra.
  - La rama `data` no tiene ninguna vela diaria, porque `velas.yml` nunca escribió (D8).
  - D1 (14-oct) decide que la historia larga diaria de BTC y del oro sale de TwelveData,
    validada contra el año de Deriv con una coincidencia de al menos el 90 %. El brief 14 fija
    la métrica y la hora de corte, antes de bajar precios (D1-p).

  **Propuesta de este PR, que no se aplica sin el Admin:** las velas diarias de D1 que valide
  el brief 14. Si la hora de corte que fije el brief 14 abre la barra del día `t` antes de las
  00:00 UTC, la sección 3 se revisa antes de correr: el rezago está contado para barras que
  abren a esa hora, como las de Deriv.
- `r_t` es el retorno logarítmico de cierre a cierre entre dos velas consecutivas del
  calendario del activo (continuo, 24/7, para BTC (D6); hábil para XAU, donde el lunes abarca
  el fin de semana). Si falta una vela que el calendario esperaba, el retorno que la cruzaría
  **no se usa**.
- **Muestra:** la intersección de las dos series. **Se excluye** todo día sin alguna de las
  dos entropías que usan los modelos, la de `t−2` y la de `t−1`. La exclusión se decide acá,
  antes de mirar: incluye los huecos heredados de la serie GDELT —el mayor es un tramo de 18
  días, del 14-jun al 1-jul-2025— y los días con `insufficient_events`. No se rellena nada.

## 3. Qué entropía usa cada retorno

**Decisión del Admin del 29-sep-2026: la entropía con fecha `d` se usa solo para retornos
de días `≥ d + 2`, en días de calendario.** Es la lectura **primaria**. Para el retorno del día
`t`, la entropía es la de la fecha `t − 2` días, unida por fecha (*as-of* sobre `fecha − 2
días`), **no** por un corrimiento de barras: en XAU, el lunes usa la entropía del **sábado**,
no la del jueves que daría correr dos velas hábiles.

Por qué 2: el archivo del día `t−1` sale **durante** el día `t`, no a su comienzo.
`ingestion/run_gdelt.py` lo fija en `DIAS_DE_RETRASO_DE_PUBLICACION = 1`, y la reconciliación
de 404 existe porque a las 06:30 UTC a veces todavía no está. Las velas diarias de Deriv van de
00:00 a 00:00 UTC, así que al abrir el día `t` la entropía de `t−1` no se conoce y la de `t−2`
sí. Con esto la prueba responde si la entropía sirve con la información disponible a la hora
de decidir.

**La lectura secundaria (D6)** usa la entropía de la fecha `t − 1`, con la misma unión por fecha
y la misma tolerancia. Es una entropía que al abrir `t` todavía no se publicó, así que **no es
operativa**: dice si la entropía de la víspera discrimina la volatilidad del día —la prueba
del decision-log del 25-sep—, no si sirve para decidir. Por eso nunca decide la conclusión
(sección 9). La revisión del 01-oct la había descartado, y D6 la devuelve como secundaria.

En el módulo: `REZAGO_ENTROPIA_DIAS_CALENDARIO = 2` es la primaria y
`REZAGO_SECUNDARIO_DIAS_CALENDARIO = 1` la secundaria. Ningún otro rezago es válido, y el
módulo rechaza cualquier otro: en particular el 0, la entropía del mismo día.

**[INTERPRETACIÓN] Tolerancia cero** (`TOLERANCIA_ASOF_DIAS = 0`): si la fecha del rezago no
tiene entropía —un hueco de la serie o un día con `insufficient_events`—, el retorno de
`t` **se excluye**; no se toma una entropía más vieja. Un *as-of* hacia atrás sin
tolerancia arrastraría la entropía del 13-jun-2025 a lo largo de todo el tramo de 18 días
sin datos, y la sección 2 fija que no se rellena nada.

## 4. Modelos

Los tres se estiman igual y sobre la misma muestra. Solo difieren en la entropía.

**Varianza diaria de los regresores** —Garman–Klass, con la vela `t` del activo—:
`v_t = 0,5 · ln(H_t / L_t)² − (2·ln 2 − 1) · ln(C_t / O_t)²`.

**[INTERPRETACIÓN]** "HAR-RV con los mismos datos": con velas diarias no hay varianza
realizada intradía, y el estimador de rango de Garman–Klass es lo más cercano que dan las
mismas velas. Sus componentes, en barras del calendario del activo:

| calendario | diario | semanal | mensual |
|---|---|---|---|
| continuo (BTC) | **1** | **7** | **30** |
| hábil (XAU) | **1** | **5** | **22** |

D6 fija las ventanas de 1, 7 y 30 días, porque BTC opera 24/7. **[INTERPRETACIÓN]** En XAU son
las mismas tres —un día, una semana, un mes— contadas en barras hábiles, porque D6 no nombra a
XAU.

**Horizontes (D6): `h` = 1, 5 y 22 barras del calendario del activo.** El objetivo de horizonte
`h` es la varianza media de las `h` barras que empiezan en `t`:
`v̄_{t,h} = (1/h) · Σ_{j=0}^{h−1} v_{t+j}`. **[INTERPRETACIÓN]** D6 da los números y no la
unidad. Son barras: en BTC, días de calendario, así que 5 y 22 no son una semana ni un mes, a
diferencia de las ventanas; en XAU, días hábiles.

- **Modelo A (HAR-RV):**
  `ln v̄_{t,h} = a + b_d · ln v_(d) + b_w · ln v_(w) + b_m · ln v_(m) + día_de_la_semana + ε`,
  donde cada `v_(·)` es el promedio de `v` en las barras de esa ventana que terminan en
  `t−1`.
- **Modelo B₂ (primario):** el modelo A más `c · e_(t−2)`, con `t−2` la fecha de la sección 3
  y `e` la entropía **desestacionalizada**: su valor menos el promedio de su mismo día de la
  semana en la ventana de estimación.
- **Modelo B₁ (secundario):** el modelo A más `c · e_(t−1)`, desestacionalizada igual.

**Estimación:** mínimos cuadrados, un modelo por horizonte, ventana **expansiva** que arranca
con **504** barras y se reestima en cada barra. El pronóstico que se hace al abrir `t` usa solo
las filas cuyo objetivo ya terminó: `τ + h − 1 ≤ t − 1`.

**Pronóstico** de la varianza media de `r_t … r_{t+h−1}`: `H_{t,h} = exp(ŷ_{t,h}) · s_h`, con
`ŷ` el valor ajustado de `ln v̄` y `s_h` el promedio de `r̄²_{τ,h} / exp(ŷ_{τ,h})` en la ventana
de estimación, donde `r̄²_{τ,h} = (1/h) · Σ_{j=0}^{h−1} r²_{τ+j}`. Ese factor corrige a la vez el
sesgo de la exponencial y la diferencia de escala entre el rango de la vela y el retorno de
cierre a cierre, y se estima igual en los tres modelos.

## 5. Control por día de la semana

GDELT tiene un ciclo semanal fuerte: la cantidad de noticias, y con ella la entropía,
cambia con el día. La volatilidad también. Sin control, la entropía podría "predecir" solo
que es fin de semana. Por eso, las dos cosas:

- **indicadoras del día de la semana del día `t` en los tres modelos**: lo que el día de la
  semana explique de la volatilidad lo explica también el modelo A;
- **la entropía entra desestacionalizada** en B₂ y B₁, con los promedios por día de la semana
  de la ventana de estimación.

**Todo lo del día de la semana se estima solo en entrenamiento (D6).** Los coeficientes de las
indicadoras y los promedios de la entropía salen de las filas de la ventana de estimación de
cada pronóstico, y se recalculan con ella. Nunca de la muestra completa ni del tramo que se
pronostica.

Nunca se usa `n_events` crudo.

## 6. Pérdida

**QLIKE**, fuera de muestra, contra el proxy `r̄²_{t,h}`:

```
L(H_{t,h}, r̄²_{t,h}) = ln H_{t,h} + r̄²_{t,h} / H_{t,h}
```

**Se elige QLIKE y no MSE.** `r̄²_{t,h}` es el proxy insesgado de la varianza media de los `h`
retornos, que es lo que un pronóstico de su magnitud tiene que acertar; y con un proxy ruidoso,
QLIKE ordena los pronósticos igual que la varianza verdadera (Patton, 2011). Esta forma de
la pérdida difiere de la usual en términos que no dependen del pronóstico, y admite
`r̄²_{t,h} = 0`.

## 7. Comparación: Diebold–Mariano y MCS (D6)

**Diebold–Mariano, unilateral**, por activo, rezago y horizonte, sobre `d_t = L_A,t − L_B,t`,
con `B` el modelo de ese rezago. La hipótesis alternativa es que el modelo con entropía pierde
menos: `E[d_t] > 0`.

- **Alfa fijado antes: 0,05.** Vale para la familia de cada activo (abajo).
- Varianza de `d̄` por Newey–West, con `⌊4 · (T/100)^(2/9)⌋` rezagos, o `h` si `h` es mayor:
  los pronósticos de horizonte `h` se solapan. Y la corrección de muestra chica de Harvey,
  Leybourne y Newbold (1997).
- `T` es la cantidad de pronósticos fuera de muestra de ese horizonte.

**Familia y multiplicidad.** Por activo hay **6** pruebas de Diebold–Mariano: 2 rezagos × 3
horizontes. La secundaria cuenta en el N (D6, D3), así que entra en la misma familia que la
primaria. **[INTERPRETACIÓN]** Los 6 p-valores se ajustan por **Holm**, y una prueba rechaza si
su p ajustado queda debajo de 0,05.

**Model Confidence Set** de Hansen, Lunde y Nason (2011), por activo y horizonte, sobre
`{A, B₂, B₁}` y con las mismas pérdidas. **[INTERPRETACIÓN]** Lo que D6 no fija queda fijado
acá, antes de mirar:
- nivel **0,90**, es decir α del MCS = 0,10;
- el estadístico `T_max`, con su regla de eliminación;
- bootstrap de bloques móviles, con bloques del mismo largo que los rezagos de Newey–West;
- **10.000** réplicas, con semilla **20261017**.

La definición de `T_max` se cita de memoria: el paper no se pudo leer desde este entorno. El PR
de evaluación la contrasta contra el texto antes de correr.

## 8. Réplica en XAU

El mismo diseño, **sin cambiar ninguna elección** después de ver BTC: los mismos modelos,
horizontes, pérdida, pruebas, familia y MCS, con las ventanas del calendario hábil. Es una
réplica, no un segundo intento: se reporta pase lo que pase, no mueve la conclusión de BTC y no
suma al N.

## 9. Qué se concluye

Por horizonte, con la **primaria** de BTC. La lectura del MCS:
- **a favor:** A queda fuera del MCS y B₂ dentro;
- **en contra:** A queda dentro;
- **neutra:** quedan fuera A y B₂, y solo sobrevive B₁.

| Diebold–Mariano de B₂ contra A (Holm) | MCS | conclusión en ese horizonte |
|---|---|---|
| rechaza | a favor | **la entropía aporta** |
| no rechaza | en contra | **no aporta** |
| cualquier otra combinación | | **no concluyente** |

**[INTERPRETACIÓN]** D6 pide comparar con MCS y con Diebold–Mariano, y no dice qué pasa si
discrepan. Lo dudoso no cuenta como hallazgo: es el criterio que D5 fija para H1 v2.

- **Aporta en al menos un horizonte:** la entropía tiene contenido predictivo sobre la
  volatilidad más allá de HAR-RV, en ese horizonte. El 1,246× pasa a ser candidato para el
  dimensionamiento, con su propio pre-registro. La réplica en XAU dice si generaliza.
- **No aporta en ninguno:** la entropía no mejora a HAR-RV con estos datos y no entra al
  dimensionamiento. Se reporta el tamaño del efecto igual.
- **La secundaria** se reporta con la misma tabla, con B₁ en lugar de B₂, y **no cambia la
  conclusión**. Si aporta y la primaria no, dice que la entropía de la víspera discrimina la
  volatilidad pero no llega a tiempo para decidir.

## 10. Registro de ensayos (D3)

H2 entra en `governance/ensayos.jsonl` como **pre-registrada, no corrida**, con dos ensayos,
los dos con `cuenta_en_n: true`:
- `h2-rezago-2`, la primaria;
- `h2-rezago-1`, la secundaria (D6).

Los tres horizontes son la misma prueba en tres plazos, ajustada por Holm, y la réplica en XAU
no es un ensayo aparte. Cuando H2 corra, cada ensayo suma una línea nueva con su estado: las de
hoy no se editan.

## 11. Revisión

Cualquier cambio de modelo, pérdida, test, alfa, rezago o muestra **después de ver un
resultado** es un experimento nuevo, registrado como `preregistro_h2_v2.md` sin borrar
este.
