# SPEL — Decision Log

Auditoría de decisiones de arquitectura (stream `DECISION_LOG`, Decisión #14). Cada entrada: fuente verificada, hallazgo, decisión tomada, validación pendiente. No se registran cosas ya cubiertas por docstring de código — esto es para decisiones que cruzan más de un archivo o que alguien en una sesión futura necesita encontrar sin tener que releer 5 patches.

---

## 2026-08-24 — Deprecación de los 14 checkpoints LSTM legacy

**Fuente:** ramas `archive/*` (código legacy real, leído para esta entrada, no citado de
memoria), `tools/audit_data_lake.py` y `ESTADO.md` del repo nuevo.

**Estado de facto que esto formaliza:** el repo nuevo **nunca** referenció los `.pt` ni
`torch`. Verificado por grep en `core/`, `ingestion/`, `orchestration/`, `governance/`,
`tools/` y `execution/`: los dos únicos hits son prosa en docstrings, ninguna importación
ni ruta de código. `requirements.txt` lo dice explícito ("NO incluye PyTorch/ML —
Decisión #7: sin ML en F1"). Esta entrada no cambia código: pone por escrito una decisión
que Git ya venía sosteniendo en silencio, para que no se revierta por inercia el día que
alguien encuentre los `.pt` en Drive y los tome por una base disponible.

### La decisión, en cuatro puntos

1. Los 14 checkpoints `.pt` **salen del flujo activo** de entrenamiento e inferencia.
2. **No se usan como baseline operativo** ni como término de comparación entre modelos.
3. **Se preservan como histórico/auditoría**, fuera de cualquier ruta activa (Principio #3:
   archivar, nunca borrar).
4. Toda comparación futura de modelos parte de **modelos reentrenados desde cero**.

El movimiento físico de los archivos lo hace Altair en Colab — viven en Drive, no en el
repo. Acá queda el registro, que es lo que sobrevive a la sesión.

### Las razones (independientes: cada una alcanza por sí sola)

**(a) Entrenados sobre el parquet canónico v4, con columnas de fecha en formatos
inconsistentes (`'/'` vs `'-'`).** Es la causa raíz documentada el 2026-08-18, y está
registrada en el repo nuevo, no solo en la memoria de una sesión: `ESTADO.md:196` y
`tools/audit_data_lake.py:8-9` y `:336`. Unos pesos entrenados sobre ese defecto lo
propagan: el modelo aprendió lo que el defecto le mostró.

**(b) Su lista de features no es auditable desde el código.**
`04_GOLD_MODULES/capa_c_inference.py:252` la lee de `meta["feature_columns"]`, y si esa
clave falta cae a un fallback que toma **todas las columnas numéricas del parquet**
(excluyendo `date`/`symbol`) truncadas a `_INPUT_SIZE`. O sea: qué 20 features vio
realmente cada checkpoint depende de un JSON externo que puede no estar, y el
comportamiento sin él es silencioso, no un error. No se puede reproducir un entrenamiento
cuyo espacio de entrada no se puede reconstruir.

> **Corrección de la línea citada:** la tarea indicaba `capa_c_inference.py:295`. El
> mecanismo es exactamente el descrito, pero está en la **línea 252** (la 295 es
> `_obtener_p90`). Se corrige acá para que la cita sirva a quien la vaya a buscar.

**(c) — NO SE PUDO VERIFICAR COMO SE ENUNCIÓ; ver más abajo.** La razón propuesta era que
el P90 de la máscara Gödel salía de `SHA_REGISTRY.json`, calculado sobre el dataset
completo, con leakage en la selección de muestras. Dos cosas no cierran contra el archivo
real:

- **`SHA_REGISTRY.json` no guarda umbrales, guarda hashes.** Es un manifiesto de
  integridad (ruta → `sha256`/`size`/`ts_validated`). Los valores del P90 viven en
  `00_VAULT/godel_thresholds_v2.json`, que el registry únicamente *hashea*. Es la misma
  distinción que el propio legacy advierte en `spel_meta_guardian.py`: *"el SHA256 de
  SPEL_META.json es el hash del ARCHIVO meta, NO de los checkpoints. Son entidades
  distintas."*
- **El script que calcula esos percentiles usa corte temporal, no el dataset completo.**
  `04_GOLD_MODULES/spel_p90_recalibrate.py` imprime *"Calculando percentiles con datos
  <= 2023-12-31"*, opera sobre `n_filas_train` y reporta qué porcentaje del total usó. Eso
  es exactamente lo contrario de calcular sobre todo el dataset.

Lo que **sí** queda como sospecha razonable y sin resolver: el nombre del archivo
(`_v2`) y el del script (`recalibrate`) sugieren que hubo una versión anterior de los
umbrales que se recalibró justamente para arreglar algo. Si los 14 `.pt` se entrenaron
contra los umbrales *previos*, el leakage habría sido real para ellos. **No se pudo
determinar cuál de las dos versiones vieron los checkpoints**, y no se registra como
hecho lo que no se verificó.

**Esto no debilita la decisión:** las razones son independientes y (a) y (b) están
confirmadas contra el código. La deprecación se sostiene sobre esas dos.

### Consecuencia 1 — el canon de 20 features queda ABIERTO

`01_HOLMES_OPS/spel_meta_guardian.py` declara su propia condición de vigencia, textual:

> `# TOPOLOGÍA CANÓNICA (Regla 13 — inamovible mientras existan los 14 .pt):`
> `#   input_size  = 20  ← 20 features del parquet canónico v4`
> `#   hidden_size = 64  ← capacidad representacional calibrada en COVID test`
> `#   num_layers  = 1   ← single-layer LSTM; stack invalida gradientes`

El guardián se ató a la existencia de los checkpoints, no a una verdad sobre el problema.
Al salir los `.pt` del flujo activo, **la condición que hacía inamovible a `input_size=20`
deja de cumplirse por su propia letra** — no hay que derogar nada, se derogó solo.

El canon de 20 features queda **abierto a redefinición**. La decisión de con cuántas y
cuáles se trabaja es de Altair, y es **posterior a la medición de `n`**: elegir el ancho
del espacio de entrada antes de saber cuántas muestras hay es lo que produce un modelo que
no puede aprender nada. `enforce_lstm_architecture()` **no se porta** al repo nuevo.

### Consecuencia 2 — las accuracies históricas dejan de ser baseline

BTC 0.528, XAU 0.547, NVDA 0.550, NIFTY50 0.625 **no son un baseline**. El baseline pasa a
ser el trivial: la clase mayoritaria.

Con el umbral de aborto del legacy (`n_val < 5`), ninguna de las cuatro tuvo la evidencia
necesaria para ser distinguible del azar. Cuántas muestras de validación habrían hecho
falta para detectar esa ventaja sobre 0.5, con α=0.05 y potencia 80%:

| Activo | Accuracy | Binomial exacto, 2 colas | Binomial exacto, 1 cola |
|---|---:|---:|---:|
| BTC | 0.528 | 2563 | 2034 |
| XAU | 0.547 | 919 | 730 |
| NVDA | 0.550 | 820 | 654 |
| NIFTY50 | 0.625 | 134 | 111 |

> **Corrección de las cifras:** la tarea daba 1991 / 705 / 620 / 102 rotuladas como
> "binomial exacto". Recalculadas de cero para esta entrada, esos valores **no** son el
> binomial exacto: corresponden a una **aproximación normal de una cola** (que da
> 1969 / 698 / 616 / 97, dentro del ~1%). El binomial exacto a dos colas —el default
> conservador— pide bastante más: 2563 para BTC, un 29% por encima de la cifra original.
>
> **La conclusión no se mueve ni un poco:** bajo cualquiera de los cuatro métodos, el
> requisito está en los cientos o los miles, y el umbral que el legacy aceptaba era
> `n_val < 5`. La brecha es de dos a tres órdenes de magnitud. Se corrigen las cifras
> porque van a un registro permanente, no porque cambien nada.

### Lo que esto NO hace

No agrega `torch` a `requirements.txt`. No porta `enforce_lstm_architecture()` ni ningún
guardián de arquitectura. No escribe código de modelos. No toca
`execution/circuit_breaker.py` ni `execution_guard.py` (congelados hasta F4). El stream
`MODELS` de `governance/persistence.py` sigue existiendo y apuntando a Drive: lo que
cambia no es dónde pueden vivir unos pesos, sino que **ningún camino activo lee estos**.

**Validación pendiente:** el movimiento físico de los 14 `.pt` a su ubicación de archivo lo
hace Altair en Colab — hasta que ocurra, la deprecación está registrada pero no ejecutada.
Y queda sin resolver cuál versión de los umbrales Gödel vieron los checkpoints (ver (c)):
si alguien quiere cerrarlo, el rastro empieza comparando `godel_thresholds_v2.json` con lo
que haya de la versión anterior en el historial de la rama archive.

---

## 2026-08-23 (PR-4) — Punto de composición: dónde se resuelven las credenciales, y una sola vez

**Fuente:** el hallazgo de gobernanza registrado en `ESTADO.md` el 18 ago ("no hay ningún
punto de composición"), verificado entonces por grep en tres mitades y re-verificado ahora
antes de cerrarlo.

**Hallazgo 1 — el hueco no era "falta un adapter", era "falta dónde armarlos".** Las tres
mitades: nada instanciaba un adapter fuera de tests; `load_secret()` no se llamaba desde
producción (los dos hits fuera de `tests/` eran docstrings de `persistence.py`); ningún
workflow inyectaba secretos (cero `secrets.` y cero `env:` en `.github/workflows/`). Las
piezas existían y estaban probadas, y nadie las conectaba. Es una brecha de otra clase que
"faltan adapters", y se cierra con otra clase de trabajo.

**Hallazgo 2 — la regla que hacía falta proteger ya existía, sin dueño.** Los adapters
reciben credenciales por constructor y no leen el entorno (Decisión 6 de PR-3). Eso los
hace testeables, pero deja abierta la pregunta de quién las lee — y una regla que nadie
verifica se rompe con el primer adapter que "por comodidad" haga `os.environ.get()` en su
`__init__`. Verificado hoy que el invariante se sostiene: `ingestion/adapters.py` no
importa `governance.*` ni `os`.

**Hallazgo 3 — offline no se puede distinguir "no hay secretos" de "los secretos no
llegaron".** Sin credenciales, la ausencia es el estado esperado y todos los tests `live`
se saltean por su propio `skipif`. Es decir: un secreto cargado en GitHub pero mal escrito
en el `env:` del workflow produce un workflow **verde que no probó nada**. Ningún test
offline puede ver eso, porque offline se ve idéntico a un entorno de desarrollo sano.

**Decisiones:**

1. **`ingestion/sources.py` es el punto de composición, y es UNO.** Único lugar de
   producción donde se llama a `load_secret()` y se construyen adapters. Con tres lugares,
   "¿por qué no arrancó tal fuente?" vuelve a ser una búsqueda en vez de una lectura.
2. **Una fuente sin credencial es capacidad ausente, no error.** `build_price_sources()`
   nunca lanza por una credencial faltante: devuelve `SourceInventory` con lo construible
   y el motivo del resto. Un motor que se cae entero al arrancar porque falta una clave
   opcional es peor que uno que arranca degradado y lo dice.
3. **El motivo nombra la variable exacta.** `"falta DERIV_APP_ID"`, no "credenciales
   incompletas". Deriv necesita dos credenciales y tener el token con el `app_id` faltante
   es un caso real y frecuente (el token se rota, el `app_id` se olvida en el otro
   entorno); un motivo genérico obliga a adivinar entre las dos.
4. **`SourceInventory` es `frozen`.** Es una foto del momento en que se levantó, no un
   registro al que se le agregan fuentes después. Si cambian las credenciales se vuelve a
   llamar y se obtiene una foto nueva — parchear la vieja es como se llega a dos partes
   del sistema creyendo cosas distintas sobre qué hay conectado.
5. **El test de arquitectura usa AST, no grep.** Los docstrings de `adapters.py` hablan
   explícitamente de `governance/secrets.py` y de `os.environ` (para decir que NO los usa),
   así que un grep daría un falso positivo permanente y el test se terminaría borrando por
   inútil. El AST solo ve imports reales.
6. **`live-tests.yml` separado de `tests.yml`, no un job más adentro.** `tests.yml` corre
   en `pull_request`, y un PR desde un fork ejecuta el workflow del fork: con los secretos
   declarados ahí, bastaría un PR que cambie un paso por `echo "$TWELVEDATA_API_KEY"`.
   GitHub mitiga esto no pasando secretos a PRs de forks, pero **la mitigación es una
   política del proveedor, no una propiedad de este repo** — y el día que alguien agregue
   `pull_request_target` "para que funcione", desaparece sin que nadie lo note. En un
   archivo que no se dispara por PR, ese error requiere editar el archivo a propósito.
7. **`SPEL_EXPECT_SECRETS=1` + un test guardián** (Hallazgo 3). El workflow afirma "acá los
   secretos deberían estar", y el guardián falla si el inventario queda vacío. Invierte la
   pregunta que offline no se puede hacer. Verificado en los dos sentidos: con la variable
   en 1 y sin credenciales falla con un mensaje que nombra las variables faltantes; con
   una credencial presente pasa.
8. **Credenciales por `env:` de job, nunca como argumento de comando.** Los argumentos
   aparecen completos en el log del runner y en la lista de procesos; una variable de
   entorno no. A nivel de job y no de step porque los tests live van a ser más, y repetir
   el bloque en cada step es la forma segura de que a uno se le olvide.
9. **`workflow_dispatch` solamente; el `schedule:` queda comentado.** Cada corrida gasta
   cuota real del plan gratuito. El schedule se deja escrito y razonado —lo que detecta es
   que un proveedor cambie la forma de su respuesta sin avisar— para que el día que se
   active no haya que volver a pensarlo; antes hay que confirmar que la cuota semanal
   alcanza para el conjunto de tests live, que hoy es 2 y va a crecer.

**Descartado:** capturar excepciones genéricas en `build_price_sources()` — un `ValueError`
por pasarle una key vacía a un adapter es un bug de ese archivo, no una capacidad ausente,
y taparlo lo convertiría en "esa fuente no estaba disponible", que es justo el fallo
silencioso que `ingestion/` existe para no producir. También se descartó un `timeout_s` por
proveedor: es una decisión de operación, y sin un motivo medido para diferenciarlos, uno
solo.

**Validación pendiente:** nada llama todavía a `build_price_sources()` desde un ciclo real
— `orchestration/cycle.py` corre scoring sobre series ya persistidas (`read_series`), no
sobre datos que va a buscar. El hueco que queda ya no es "no hay dónde armar las piezas"
sino "las piezas armadas no se usan todavía". Y el workflow `live-tests.yml` **nunca
corrió**: hasta que alguien cargue `TWELVEDATA_API_KEY` en los Secrets y lo dispare a mano,
la tercera mitad está escrita pero no ejercitada.

---

## 2026-08-23 (PR-3) — TwelveDataAdapter: segunda fuente OHLCV, y primera prueba real del contrato

**Fuente:** documentación oficial de TwelveData (endpoint `time_series`) y auditoría del
contrato ya en `main` (PR-2). **No hay capturas reales de la API detrás de esta entrada**
— ver "Validación pendiente", que es la parte más importante de este registro.

**Hallazgo 1 — el vocabulario del proveedor no puede filtrarse aguas arriba.** TwelveData
nombra los pares con barra (`EUR/USD`); el proyecto no (`EURUSD`). La barra es un detalle
del proveedor: si se deja viajar, cada consumidor aguas abajo tiene que saber de qué
fuente vino su símbolo para escribirlo bien. Muere en `_TWELVEDATA_SYMBOL_MAP`, igual que
`frxEURUSD` muere en el mapa de Deriv.

**Hallazgo 2 — si viene o no `volume` depende del INSTRUMENTO, no del plan.** Un par de
forex no trae la clave; una acción sí. No es algo que se pueda saber de antemano por
configuración, así que la bandera `volume_available` se deriva de lo que de verdad llegó
(`any("volume" in v for v in values)`), no de una regla por tipo de símbolo. Una regla
declarada se rompe en silencio con el primer instrumento que no encaje; una observación no.

> **Corrección con evidencia, 2026-08-23 (mismo día, tras conseguir las capturas
> reales):** la formulación de arriba —"forex no, acciones sí"— era una hipótesis
> razonable y es **falsa**. La captura real de `BTC/USD` (exchange Binance, Digital
> Currency) tampoco trae `volume`: de los tres instrumentos capturados, el único con
> volumen es AAPL. No hay regla por clase de activo que sirva.
>
> Lo que importa acá no es que la hipótesis fuera equivocada, sino que **la decisión no
> dependía de ella**: el código nunca declaró la regla, la observó. Verificado por
> mutación — sustituir `any("volume" in v ...)` por una regla declarada deja 4 de 28
> tests en rojo, y el primero en caer es el que parsea la captura real de BTC. Con una
> regla declarada, BTC habría quedado marcado con volumen disponible y el relleno `0.0`
> habría entrado al pipeline como si fuera un dato real, sin que nada lo señalara.

**Hallazgo 3 — la barra diaria no tiene hora, y eso cambia dos cosas a la vez.** El
`datetime` diario viene como fecha sola (`"2026-08-22"`). Primero: mandar `timezone` en un
intervalo sin hora no reinterpreta nada e invita a que el proveedor corra la fecha un día.
Segundo: ese timestamp es la CONVENCIÓN del día de mercado, no el instante real de nada —
que es exactamente lo que `timestamp_is_convention` (PR-2) existe para marcar. El mismo
`frozenset` decide las dos cosas, porque son la misma propiedad del dato.

**Hallazgo 4 — TwelveData señaliza errores en el CUERPO, con HTTP 200.** Mirar el status
HTTP deja pasar el error como si fuera un payload bueno. Y el mismo `code: 404` cubre dos
causas que hay que tratar distinto: símbolo inexistente vs. símbolo real que el plan de la
cuenta no cubre. El mensaje nombra el plan en el segundo caso.

**Decisiones:**

1. **Símbolos: solo los verificados.** `EURUSD`, `BTCUSD`, `AAPL`. **`XAU/USD` queda
   afuera** — no se pudo confirmar que el plan gratuito lo cubra, y "probablemente esté"
   no es evidencia. Un símbolo que el plan rechaza degrada la cadena en runtime por algo
   que se sabía de antemano. Entra cuando haya una respuesta real que lo confirme.
2. **Timeframes: las mismas 8 claves que Deriv**, ni una más. TwelveData ofrece
   intervalos que Deriv no tiene (`45min`, `8h`), y Deriv no tiene `5h` ni TwelveData
   tampoco: agregar de un lado lo que el otro no soporta rompe el fallback justo cuando
   hace falta. `_TWELVEDATA_GRANULARITY_SECONDS` duplica los segundos de Deriv a
   propósito en vez de importarlos — son dos proveedores independientes que hoy
   coinciden, y si mañana uno cambia, el mapa del otro no debe moverse con él.
3. **Observado > declarado** para `volume_available` (Hallazgo 2).
4. **`timezone` condicional** vía `_INTERVALOS_SIN_TIMEZONE` (Hallazgo 3).
5. **Errores por `code` del cuerpo**, no por status HTTP: 401→`AdapterAuthError`,
   429→`AdapterConnectionError` (la cuota es transitoria por definición, así que el retry
   y el fallback de `AdapterChain` deben tratarla como tal), 404 con "plan" en el
   mensaje→`AdapterDataError` de límite de cuenta, 404 sin él→símbolo no encontrado.
6. **La key va en el header `Authorization`, nunca como query param.** Un `?apikey=...`
   termina escrito en logs de proxy, historiales de shell y en los mensajes de error de
   httpx, que incluyen la URL. Y **la key se recibe por constructor**, no se lee de
   `os.environ` dentro del adapter: la fuente única de credenciales es
   `governance/secrets.py` (PR-1), y un adapter que lee el entorno por su cuenta es un
   segundo lugar donde buscar cuando algo falla, además de intesteable sin ensuciar el
   entorno del proceso.
7. **`health_check()` gasta una vela real de EUR/USD** en vez de un endpoint de
   referencia: la doc no confirma que `/stocks` o `/forex_pairs` tengan coste cero de
   cuota, y en el plan gratuito una suposición equivocada ahí se paga con las peticiones
   que necesita el motor. Una vela es el costo mínimo que sí se conoce.
8. **`order=ASC` se pide Y se reordena localmente.** Pedirlo no es garantizarlo.

**Descartado:** inferir `volume_available` del tipo de símbolo (Hallazgo 2); confiar en el
status HTTP para detectar errores (Hallazgo 4); leer la key del entorno (Decisión 6);
agregar `XAU/USD` sin evidencia (Decisión 1).

**Fixtures — resuelto el mismo día, con capturas reales.** El adapter se escribió con
fixtures sintéticos porque el entorno no podía capturar nada (sin `TWELVEDATA_API_KEY`, y
`api.twelvedata.com` fuera de la política de red del sandbox: 403 al CONNECT, denegación
de política, no error del servidor). Se dejaron rotulados como sintéticos en vez de
firmarlos como reales. **Ya fueron reemplazados por las tres capturas del 2026-08-23**
(EUR/USD, BTC/USD, AAPL en 1day, literales).

La predicción que se dejó escrita entonces —"pegar las capturas reales debería dejar la
suite en verde sin tocar una aserción"— **se cumplió a medias, y la mitad que falló es la
más valiosa**: la forma de la petición y la traducción de errores no se movieron, pero uno
de los dos supuestos de forma resultó falso (BTC sin volumen, ver la corrección en
Hallazgo 2). El conteo no cambió: 28 offline + 1 live, antes y después.

Sobreviven dos fixtures sintéticos, marcados y con motivo declarado:
- **contrafáctico deliberado** (forex *con* volumen): su valor depende de que NO sea real
  — es el que atrapa una regla por clase de activo en la dirección que las capturas
  reales no cubren.
- **intradía por necesidad**: las tres capturas son diarias y hay dos comportamientos
  intradía que probar; ambos dependen del argumento `timeframe` y de la petición emitida,
  no de los valores del payload. Se reemplaza cuando haya una captura intradía real.

**Validación pendiente:** el test `live` (marker `live` + skipif sobre la credencial)
**todavía no corrió nunca con clave real**. Lo que sigue sin confirmar contra la API viva
es exactamente lo que ningún fixture puede cubrir, por real que sea: el cliente httpx que
el adapter abre y cierra solo (los tests offline inyectan el suyo, así que esa rama de
`_get()` nunca se ejecuta), la autenticación aceptada de verdad, y la forma intradía.
Hasta entonces este adapter es 🟡 y no ✅.

---

## 2026-08-23 (PR-1) — Convención de nombres de secretos por proveedor

**Fuente:** documentación oficial de cada proveedor, y auditoría de `governance/secrets.py`
contra el repo real (grep, no memoria).

**Hallazgo:** los proveedores no llaman igual a su credencial. TwelveData y AlphaVantage
la llaman `apikey`; Tiingo y Deriv la llaman `token`; Alpaca emite un par (key + secret).
Uniformar los nombres del registro a la fuerza — todo a `_API_KEY`, por ejemplo — habría
sido más prolijo de leer y peor de auditar: buscar el nombre de la variable en la doc del
proveedor dejaría de dar resultado, que es justo lo que hace falta para verificar cada
adapter contra su documentación real.

Segundo hallazgo, del mismo grep: **no hay ningún punto de composición en el repo.** Nada
instancia un adapter fuera de tests, `load_secret()` no se llama desde producción (los dos
hits fuera de `tests/` son docstrings de `persistence.py`), y ningún workflow inyecta
secretos (cero `secrets.` y cero `env:` en `.github/workflows/`). Las piezas existen y
están probadas; nadie las conecta.

**Decisión:** el sufijo de cada clave espeja el nombre que el proveedor usa en su propia
doc (`_API_KEY` para apikey, `_API_TOKEN` para token, `_API_KEY` + `_SECRET_KEY` para el
par de Alpaca). Se registran `TWELVEDATA_API_KEY`, `ALPHAVANTAGE_API_KEY` y
`TIINGO_API_TOKEN` **antes** de que exista su adapter, a propósito: el registro es la
lista de secretos que el proyecto reconoce, no la de los que ya se usan —
`secrets_status_report()` los muestra ausentes hasta que se configuren, que es exactamente
la visibilidad que faltaba.

**No se tocó `load_secret()`.** La auditoría no encontró defecto: la prioridad
env → Colab → `SecretError` es correcta, no expone valores en el mensaje de error, y no
tiene la ofuscación de import que sí tenía el legado. Un test nuevo
(`test_secret_error_de_proveedor_no_incluye_el_valor`) fija esa última propiedad por
escrito, para que un "mejoremos el mensaje agregando contexto" falle en CI y no cuando un
token real llegue a un log.

**Descartado:** un nombre uniforme para todas las claves (ver Hallazgo). También se
descartó agregar los secretos al workflow en este PR — inyectar un secreto que ningún
código lee todavía es superficie de exposición sin beneficio; entra cuando entre el
adapter que lo consume.

**Validación pendiente:** las 3 claves no se probaron contra el endpoint real de ningún
proveedor — no hay adapter que las use todavía (PR-2). Que el nombre sea el correcto está
verificado contra la doc, no contra una respuesta HTTP 200.

---

## 2026-08-23 (PR-2) — Contrato de datos OHLCV: velas cerradas, metadata de calidad y transporte de attrs

**Fuente:** auditoría de `ingestion/adapters.py` contra el repo real, más comportamiento
de pandas medido en la versión instalada (3.0.5), no citado de memoria ni de la doc.

**Hallazgo 1 — la salvaguarda anti-fuga-temporal era un método privado de un solo
adapter.** `DerivAdapter._drop_unclosed_candle` funcionaba bien, pero como método privado
depende de que cada adapter nuevo se acuerde de reimplementarla. Un adapter que
simplemente no la tenga deja pasar la vela en formación disfrazada de dato histórico, sin
que nada lo detecte — que es exactamente la hipótesis #1 que Fase 2 tiene que descartar
(fuga de horizonte).

**Hallazgo 2 — `_to_dataframe` tenía un parámetro con el nombre equivocado.** Se llamaba
`source` y su único call site le pasaba el símbolo del usuario
(`_to_dataframe(..., source=symbol)`). El log resultante sí mostraba el instrumento
(`[deriv] ... para EURUSD`), así que no había pérdida de información hoy — el problema es
que el nombre decía "proveedor" y el valor era el instrumento, y al promover el filtro a
una función que recibe AMBOS por separado, arrastrar ese nombre habría hecho que el
proveedor se registrara como el símbolo. Es privado y ningún test lo invoca directo, así
que el rename salió gratis; en cuanto hubiera un segundo call site dejaría de serlo.

**Hallazgo 3 — un 0.0 de volumen es ambiguo y nada lo desambiguaba.** Deriv nunca reporta
volumen (su doc oficial define la vela con exactamente close/epoch/high/low/open), así
que el `0.0` que escribe el adapter es relleno. Sin una bandera explícita, ese relleno y
un 0.0 legítimo de "no se operó en esta vela" son indistinguibles aguas abajo, y
cualquier feature que promedie volumen mezcla las dos cosas en silencio.

**Hallazgo 4 — `df.attrs` sirve como transporte y NO como almacenamiento, medido.** En
pandas 3.0.5: sobrevive `copy()`, `sort_values()`, `concat()` y `reset_index()`, y **se
pierde en `merge()`**. `ingestion/training_dataset.py` hace exactamente un join
OHLCV↔GDELT, o sea que apoyarse en `attrs` para procedencia durable la perdería
justo al armar el dataset de entrenamiento, sin excepción ni warning.

**Decisiones:**

1. `drop_unclosed_candles()` pasa a ser función de módulo, con `source` obligatorio
   (proveedor), `symbol` opcional (instrumento) y `now_utc` inyectable. Lo último no es
   comodidad: sin reloj fijo, un fixture con una vela deliberadamente abierta se vuelve
   cerrada cuando el reloj avanza, y el test se vuelve intermitente.
2. `validate_ohlcv_schema()` gana `require_closed` / `granularity_s` / `now_utc`
   keyword-only, con defaults que no rompen ninguna llamada existente. Semántica
   condicional: valida el cierre *cuando es posible saberlo*. **La granularidad nunca se
   infiere del espaciado** — un dataset con huecos legítimos (fin de semana forex,
   feriados) daría una inferencia equivocada, y una granularidad equivocada es peor que
   ninguna. La verificación va al final, después de las estructurales: si falta
   `timestamp` o no es UTC, ese error es más informativo.
3. `AdapterResult` gana cuatro campos con default (`volume_available`,
   `timestamp_is_convention`, `is_fallback`, `provider_status`) en vez de un tipo nuevo.
   `is_fallback` es deliberadamente independiente de `is_degraded`: un respaldo puede
   funcionar perfecto. `AdapterChain` levanta los attrs con `.get()` y default
   conservador — un adapter que no escribe attrs no está incumpliendo el contrato, está
   no reportando esa dimensión.
4. Los cuatro campos entran también al log de auditoría persistido: sin ellos el log dice
   de dónde vino cada dato pero no con qué calidad, y esa es justo la pregunta que hay que
   poder contestar hacia atrás cuando un modelo se comporta raro.
5. `pandas>=2.2,<4` — única dependencia pineada, porque `attrs` es API que pandas
   documenta como experimental y el contrato depende de su comportamiento exacto. Rango
   amplio, no `==`: fija el límite donde una major podría romperlo, sin obligar a
   actualizar un número a mano cada mes.

**Estilo de anotaciones:** las firmas nuevas usan `X | None` (sintaxis moderna). Las 5
preexistentes con `Optional[X]` se dejan como están — migrarlas sería ruido en el diff de
este PR, sin ganancia funcional. El archivo ya tiene `from __future__ import annotations`,
así que las dos formas conviven sin problema.

**Descartado:** inferir la granularidad del espaciado entre timestamps (ver Hallazgo/
Decisión 2). También se descartó crear un tipo `DataQuality` separado para los cuatro
campos: son cuatro banderas planas, y un tipo nuevo agregaría una capa de indirección sin
resolver nada que el dataclass no resuelva ya.

**Validación pendiente:** la red de seguridad de `fetch_ohlcv()` es redundante a
propósito y en el camino normal no dispara nunca — está verificada por mutación (quitar
`granularity_s` del call site deja 64/65 tests en verde, solo la detecta el test que
inspecciona el argumento), pero no se observó dispararse contra un feed real. Y ningún
adapter escribe todavía `timestamp_is_convention=True` ni un `provider_status` distinto de
`"ok"`: esos dos caminos están probados con dobles, no contra un proveedor real — entra
con TwelveData (PR-3).

---

## 2026-08-16 — Fix: `nash_frozen_7d` normalizaba con la misma ventana del std

**Fuente:** confirmado con números, no solo argumentado — 500 muestras aleatorias de micro-ruido (rango real ~0.0015).

**Hallazgo:** normalizar con min/max de los mismos 7 días usados para el std fuerza el rango a [0,1] siempre, sin importar la magnitud real de la variación. Con referencia de 7 días: 500/500 casos daban falso "no congelado". Con referencia de 60+ días: 500/500 correctos.

**Decisión:** desacoplar `entropy_window` (referencia, tan larga como haya historia) de `window_days` (cola fija de 7 para el std). Campo nuevo `insufficient_reference` cuando la referencia es < 3x `window_days`.

**Descartado:** coeficiente de variación (alternativa legacy en `spel_ingest_incremental.py`) — hubiera roto la calibración de `NASH_FROZEN_THRESHOLD=0.15`, hecha sobre escala normalizada [0,1], no sobre CV.

**Validación pendiente:** ¿3x es el múltiplo correcto, o hace falta más referencia en la práctica? Sin backtest.

---

## 2026-08-16 — Reincorporación: `legacy_entropy_threshold` en `gold_score_bma`

**Fuente:** `spel_bayesian_core.py::SHANNON_KILL_THRESHOLD=0.42`.

**Hallazgo:** `godel_active()` depende de `p90_entropy`, que en frío (poca historia) puede venir de `compute_adaptive_percentile()` en modo GLOBAL — un default sin backtest. Si ese default está mal calibrado, `godel_active()` puede fallar en dejar pasar entropías moderadas-altas.

**Decisión:** `legacy_entropy_threshold=0.42` como red de seguridad INDEPENDIENTE, no reemplazo. Prioridad confirmada empíricamente en las 3 combinaciones cruzadas: `godel_active > legacy_entropy_threshold > drift_control`. `None` desactiva.

**Nota histórica:** en el patch anterior (0007) se había reemplazado el umbral fijo por `godel_active()` puro, razonando Tamiz 3 (una implementación por concepto). Esta sesión revirtió parcialmente esa decisión — no por error de razonamiento, sino porque el caso de uso real (calibración en frío) no se había considerado. Ver benchmark A/B/C (0016) para los 5 escenarios donde A, B y C divergen.

**Validación pendiente:** benchmark A/B/C usa datos sintéticos. Falta correr contra datos reales cuando exista ingestion GDELT.

---

## 2026-08-16 — Adición: `compute_entropy_delta_lags` (no reemplaza niveles)

**Fuente:** ninguna — sin precedente legacy para esta forma específica (deltas vs. niveles).

**Hallazgo:** colinealidad entre los 7 lags Fibonacci cercanos era una hipótesis razonable (ya documentada como pendiente en el patch 0006), pero confirmarla necesita matriz de correlación sobre entropía real, que no existe (sin ingestion GDELT corriendo).

**Decisión:** función ADICIONAL (`ΔE_k = E_t - E_{t-k}`), no reemplazo ni reducción a subconjunto `{1,5,21}`. Elegir un subconjunto ahora hubiera sido un número sin evidencia — exactamente lo que este proyecto evita en cada decisión. Niveles y deltas coexisten para que F2 compare con datos reales.

**Validación pendiente:** comparar poder predictivo de niveles vs. deltas con datos reales antes de elegir default del pipeline de features.

---

## 2026-08-16 — Refactor: `AdapterChain.fetch_async()` nativo

**Fuente:** trampa de reentrancia documentada desde sesiones anteriores en el docstring de la clase.

**Hallazgo (medido, no argumentado):** la versión anterior llamaba `asyncio.run()` una vez POR INTENTO de reintento (hasta 3 por adapter). Confirmado con contador real en auditoría: la nueva hace exactamente 1 llamada total, sin importar reintentos internos.

**Decisión:** `fetch_async()` es la lógica real (async nativo, `await asyncio.sleep()` en vez de `time.sleep()` bloqueante). `fetch()` es wrapper delgado — detecta loop activo con `asyncio.get_running_loop()`, lanza `RuntimeError` explícito señalando `fetch_async()` si lo hay.

**Validación pendiente:** ninguna conocida — refactor ya verificado con test de reentrancia real (el propio test corre dentro de un loop activo, no un mock).

---

## 2026-08-16 — Fix: `drive_root()` hardcodeado en `governance/persistence.py`

**Fuente:** hallazgo de auditoría (#6), no reportado por el usuario — encontrado revisando coherencia contra `governance/secrets.py`.

**Hallazgo:** `DRIVE_ROOT` estaba fijo a la ruta de Colab de Altair, sin condicional. No rompía nada en el momento (el módulo solo declaraba rutas, sin I/O real), pero violaba el principio ya establecido en `secrets.py` (detección de entorno, nunca ruta fija) y hubiera fallado en cuanto GitHub Actions necesitara el stream.

**Decisión:** `drive_root()` sigue el mismo orden de prioridad que `secrets.py::load_secret()`: env var `SPEL_DRIVE_ROOT` → detección de Colab → fallback local (`.spel_drive_stream`, marcado explícitamente). Se evalúa en cada llamada, no al importar.

**Validación pendiente:** ninguna — comportamiento probado en los 3 niveles con `monkeypatch`, no solo declarado.

---

## 2026-08-17 — Confirmación: `execution/` (Actuator) entra a producción en Fase 4

**Fuente:** decisión directa de Altair, no un hallazgo de auditoría.

**Decisión:** `execution/circuit_breaker.py` y `execution/execution_guard.py` (31 tests,
congelados desde su construcción) quedan confirmados para pasar a uso activo cuando arranque
Fase 4 — no antes, la compuerta de Fase 2 sigue firme (capital real solo después de que el
paper trading lo demuestre, `governance/PRINCIPLES.md` #6). Esto no adelanta Fase 4, formaliza
qué pasa cuando llegue.

**Validación pendiente:** ninguna nueva — sigue dependiendo de que Fase 2 cierre primero.

---

## 2026-08-17 — Investigación: motor de streaming multi-timeframe (Deriv + Alpaca), DRL + ONNX

**Fuente:** propuesta de Altair + 6 búsquedas web verificadas esta sesión (no opinión sin
respaldo). Detalle completo en `BLUEPRINT.md`, Fase 6.

**Hallazgo:** los índices sintéticos de Deriv están diseñados para ser inmunes a noticias reales
(confirmado con la propia documentación de Deriv) — GDELT no aplica ahí, sí aplica en forex/oro
real. GitHub Actions tiene piso de 5 min en `cron` y no garantiza puntualidad — no sirve como
host de un motor de 1 minuto. Reinforcement Learning para trading tiene resultados de producción
reales en ejecución/hedging, no en alpha puro desde precio — alto riesgo de sobreajuste sin
walk-forward real.

**Decisión:** motor rápido (Deriv sintéticos + Alpaca) se trata como un Fase 6 en evaluación,
paralelo al motor GDELT (Fases 1-5), no un reemplazo. Host propuesto para el motor rápido: VM
gratuita Oracle Cloud Always Free (no Termux, no GitHub Actions). Componente de aprendizaje se
nombra `DRL` (no `RL`, que ya está tomado por Risk Limits en `axiom_master.xml`). ONNX se
revisita cuando exista un modelo entrenado que exportar, no antes.

**Descartado:** construir el motor rápido sobre `core/scoring.py` tal cual (día como unidad de
tiempo) — física de datos incompatible con 1-30 min, no es un ajuste de parámetro.

**Validación pendiente:** Altair debe confirmar si GDELT sigue siendo el motor principal con el
motor rápido en paralelo, o si cambia la prioridad — no se asumió ninguna de las dos en esta
sesión.

---

## 2026-08-17 — Deriv primero y único para capital real; Alpaca a paper hasta nuevo aviso

**Fuente:** decisión directa de Altair, motivada por una restricción real, no de ingeniería:
Deriv acepta depósito sin verificación de identidad; la cuenta que sí quedará verificada (para
poder retirar ganancias) depende de un tercero de confianza y toma tiempo. Detalle personal
completo en memoria de usuario, no en este repo — acá solo la consecuencia técnica.

**Decisión:** Deriv es el único broker autorizado para capital real al arrancar Fase 4. Alpaca
se mantiene funcional (se sigue construyendo y probando) pero **gateado a paper trading
exclusivamente** hasta que Altair confirme explícitamente lo contrario. Reflejado en
`BLUEPRINT.md`, Fase 4.

**Requisito para código futuro (no implementado todavía, no existe ruteo de órdenes de ningún
tipo aún):** cuando se escriba la lógica de ruteo de Fase 4, el bloqueo de órdenes reales hacia
Alpaca debe ser un guardrail duro (no una bandera de configuración que se pueda tocar por
accidente) — mismo principio que ya rige `execution_guard.py` y `circuit_breaker.py` (capa
no-IA, no depende de que el modelo "decida bien").

**Corrección de una entrada anterior de este log:** la entrada del 17 ago sobre Fase 6 proponía
Oracle Cloud Always Free como host del motor rápido — probado por Altair esa misma sesión, no
viable (fricción de signup). Reemplazado por un segundo trigger en GitHub Actions
(`.github/workflows/heartbeat.yml`, patch 0024) — ver ese patch para el detalle real, no la
entrada original de este log.

---

## 2026-09-04 — La máscara Gödel no discrimina dirección. Fase 1 cierra con resultado negativo

**Fuente:** validación sobre datos reales, medida el 4-sep-2026 con el criterio
`4.0.0-entropy_state_p66`. `n` post-máscara: **1.211** en BTC (5/5 folds estables) y **823** en
XAU (4/5). Los dos superan el umbral de `DEFENDIBLE`, así que el resultado **no** es "no había
muestras": es un negativo medido con potencia suficiente.

### Dirección — no discrimina

Chi-cuadrado, tasa de aciertos direccionales dentro vs. fuera del régimen:

| activo | dentro del régimen | fuera | p |
|---|---|---|---|
| BTC | 51,81% [49,14–54,47] | 52,53% [50,79–54,26] | **0,68** |
| XAU | 50,98% [47,78–54,18] | 52,60% [50,58–54,61] | **0,42** |

Los intervalos de confianza se solapan casi por completo, y en los dos activos la tasa *dentro*
del régimen es **más baja** que fuera. Autocorrelación de retornos en BTC: −0,028 dentro y
−0,026 fuera — indistinguibles.

### Magnitud — sí discrimina, y solo en BTC

Mann-Whitney sobre la magnitud de los retornos:

| activo | ratio de volatilidad | p |
|---|---|---|
| BTC | 1,246 | **3,4×10⁻⁹** |
| XAU | 1,115 | 0,56 |

En BTC el efecto es grande y la significancia no deja lugar a duda. En XAU el ratio apunta en la
misma dirección pero no alcanza significancia.

### Interpretación: la hipótesis original estaba mal formulada

Esto **coincide con la literatura** sobre índices de incertidumbre construidos a partir de
noticias: predicen magnitud, no signo. El EPU (Economic Policy Uncertainty) correlaciona 0,73
con el VIX, que es un índice de volatilidad, no de dirección.

Dicho de otro modo: la entropía geopolítica de GDELT mide **cuánta turbulencia hay**, no **hacia
dónde va el precio**. Pedirle que filtre días direccionalmente predecibles era pedirle algo que
ese tipo de índice no hace. El resultado negativo no invalida la señal — invalida el uso que se
le estaba dando.

### Consecuencia para Fase 2

La vía con fundamento medido es **dimensionamiento de posición**, no clasificación direccional
filtrada por entropía. Un régimen que multiplica la volatilidad por 1,25 es información
accionable para decidir *cuánto* arriesgar; no lo es para decidir *de qué lado*. Reflejado en
`BLUEPRINT.md`.

### Nota metodológica: el umbral de dos colas

`OOF_MIN_DEFENDIBLE = 620` corresponde a una prueba de **una cola** (H1 unilateral, "el edge es
positivo"). El equivalente de **dos colas** —que es el que corresponde para auditoría, cuando no
se fija de antemano el signo del edge— es **786** con el mismo método (binomial exacto, alfa
0,05, potencia 80%, +5pp sobre 0,50).

> **Recalculado, no citado.** El número que suele circular para dos colas es 783, que sale de la
> **aproximación normal**; esa misma aproximación da 616 para una cola, no 620. La diferencia no
> es un error: es que 620 salió del binomial exacto y 783 de la normal, y emparejarlos mezcla dos
> métodos. Los pares consistentes son **620/786** (exacto) o **616/783** (normal).

Los dos activos superan ambos umbrales (1.211 y 823 contra 786), así que la distinción **no
cambia este veredicto**. Sí cambiaría el de una corrida futura cuyo OOF caiga entre 620 y 785.
La constante no se movió: cambiar un umbral de veredicto es una decisión de criterio con su
propia medición, no un ajuste de documentación.

---

## 2026-09-06 — La máscara Gödel operó de facto con P66, no con P90

**Fuente:** `git show origin/archive/legacy-pre-20260813:03_BRAIN_INTERNALS/gdelt_foundation.py`
(`add_nash_and_tesla`, líneas 449-489) y
`04_GOLD_MODULES/harvesters/spel_ingest_incremental.py` (`compute_entropy_features`,
líneas 249-252). Leídos para portar; ningún módulo importa de `archive/*`.

**Este es el hallazgo que hay que encontrar sin releer cinco patches:** durante todo el
proyecto, la condición de activación se escribió y se documentó como

```
godel_active = (entropy_shannon >= p90_entropy) OR (vitality_tesla == 9)
```

y el primer término **nunca cambió un resultado**. Bajo la definición del legacy,
`vitality_tesla == 9` es exactamente `entropy > p66`. Como `p90 >= p66` por definición de
percentil, la primera rama está contenida en la segunda:

```
(e >= p90)  ⟹  (e > p66)        =>        A ∨ B  =  B
```

Medido sobre 5.000 días sintéticos con la definición del legacy: la rama del P90 dispara el
**10,0%** de los días, la del tercil superior el **34,0%**, el OR el **34,0%**, y los días que
disparan por P90 sin disparar ya por el tercil son **cero**.

El nombre del parámetro (`p90_entropy`) describía un término inerte, y eso ocultó durante todo
el proyecto qué criterio estaba corriendo de verdad. Cualquier razonamiento pasado sobre "el
umbral P90" del sistema hay que releerlo con esto en la mano.

**Segundo hallazgo, que es la causa del primero:** el legacy tiene **dos fórmulas incompatibles**
para `vitality_tesla`, y este repo portó la que no generó los datos.

| fuente legacy | fórmula | coincidencia con la columna real de los parquets (3.998 días) |
|---|---|---|
| `gdelt_foundation.py::add_nash_and_tesla` | tercil de **entropy_shannon** | **99,8%** |
| `spel_ingest_incremental.py::compute_entropy_features` | tercil de **n_events** | 44,8% / 49,3% |

Los parquets salieron de `gdelt_foundation.py`. El port eligió la segunda citándola como "la
única con evidencia empírica real" — apuntaba a la fórmula que no produjo esos datos.

Consecuencia medida: con la fórmula del legacy el solape entre las dos ramas del OR es del
100% (un respaldo redundante, que es lo que la documentación decía que era). Con n_events cayó
a 43-51% y esa rama pasó a aportar el **72% de los disparos** — un respaldo convertido en la
señal dominante sin que nadie lo decidiera.

**Decisión (versión de criterio `4.0.0-entropy_state_p66`):** se separa el ESTADO del FILTRO.

1. `entropy_state()` es la Capa 1: tercil de entropía sobre ventana móvil causal, función pura,
   sin decisiones de trading. Devuelve `None` en warm-up — un día sin ventana no tiene estado.
2. `godel_active()` pierde `vitality_tesla` y su parámetro pasa a llamarse `p66_entropy`, que es
   lo que siempre fue. El comportamiento **efectivo** de la máscara no cambia; el nombre sí.
3. `compute_vitality_tesla()` se conserva como señal y deja de alimentar el filtro.

**Desviación del legacy, deliberada y documentada** (misma disciplina que `nash_frozen_7d`): el
legacy calcula los terciles sobre **todo el año en batch**, lo cual es look-ahead — admisible en
un pipeline de etiquetado histórico, no en algo que alimenta una decisión en vivo. Acá la ventana
es móvil y causal, y termina el día anterior.

**Validación pendiente:** nada de esto se corrió sobre los datos reales de GDELT/OHLCV — este
entorno no los tiene y los proveedores están bloqueados por política de red. Falta medir el `n`
que produce la máscara con el umbral correctamente nombrado, y recalcular la tabla anual de
distribución de estados.

---

## 2026-09-06 — Acta: `core/price_signals.py` no tiene poder predictivo demostrado

**Fuente:** medición sobre `SPEL_DATA_LAKE_V2`. BTC 5.892 filas, XAU 4.848.

Esta entrada es el **acta del resultado negativo**. Existe porque el número ya se está
citando en tres lugares del código (`core/scoring.py`, `orchestration/cycle.py` ×2) y no
tenía dónde apoyarse: quien lea esa advertencia y quiera verificarla, llega acá.

### Método

- **Walk-forward de 5 folds sobre el 80% inicial.** El **holdout del 20% final se reservó
  ANTES de cualquier cálculo** — no es un corte elegido después de ver resultados.
- **Baseline de magnitud:** `vol5`, `vol10`, `vol21`, `mag_ayer`.
  **Baseline de dirección:** `lr_ayer`, `vol10`.
  Las señales no se evaluaron contra cero: contra un baseline que ya usa la información
  barata. Lo que se mide es el **incremento** sobre eso.
- **Señales:** `compute_transfer_entropy_proxy` y `compute_backbone_score`, con lookbacks
  21, 42, 63 y 126, calculadas de forma **causal**.
- **Sanitización por calendario**, con los retornos **recalculados después** de filtrar —
  no antes, que habría dejado retornos que cruzan huecos eliminados.
- **Prueba:** Diebold-Mariano como t-test pareado sobre errores cuadráticos,
  `alternative='greater'`.
- **32 pruebas**, y el número no es arbitrario: 2 activos × 2 targets × 2 señales ×
  4 lookbacks.

> La configuración por defecto del módulo está DENTRO del barrido: `compute_transfer_entropy_proxy`
> usa `lookback_days=63` y `compute_backbone_score` usa EMA 20/63. O sea que no se midió una
> variante marginal y se absolvió a la que corre en producción.

### Training — lo mejor que apareció

| activo | target | señal | delta | p |
|---|---|---|---:|---:|
| XAU | magnitud | bb42 | +0.00206 | 0.0406 |
| BTC | dirección | bb126 | +0.00189 | 0.0625 |
| BTC | dirección | bb63 | +0.00306 | 0.0731 |

**Bonferroni (α = 0.05/32 = 0.00156): cero supervivientes.
Benjamini-Hochberg: cero.**

Vale la pena decirlo sin corrección alguna, porque es más contundente: de las 32 pruebas,
**una sola** (XAU magnitud bb42, p = 0.0406) cruza el 0.05 sin corregir. Las dos de
dirección ni siquiera llegan a eso.

### Holdout — el 20% reservado de entrada

| activo | señal | Δ R² | p | Durbin-Watson | obs |
|---|---|---:|---:|---:|---:|
| BTC | bb63 | +0.0001 | 0.4133 | 2.0018 | 1166 |
| BTC | bb126 | −0.0001 | 0.5921 | 2.0023 | 1154 |
| XAU | bb42 | — | — | — | **0** |

El Δ R² de BTC es de **cuatro decimales**: +0.0001 y −0.0001. Uno de los dos es negativo.
Con p de 0.41 y 0.59, ninguno se distingue de cero.

**XAU nunca se validó.** Cero observaciones por desalineación de fechas entre la señal y
el holdout. Eso **no es "no significativa": es no medida**, y la distinción importa —
justamente la señal con el mejor p en training es la que no llegó a probarse fuera de
muestra. Cualquier lectura futura de este acta tiene que tratar a XAU/bb42 como pendiente,
no como refutada.

### Backtest de reversión sobre la señal líder

| activo | Sharpe estrategia | Sharpe Buy&Hold | retorno acumulado | B&H |
|---|---:|---:|---:|---:|
| BTC | **−0.553** | +0.814 | **0.0081** | — |
| XAU | −0.019 | — | 0.857 | 3.849 |

BTC terminó con el **0,81% del capital inicial: perdió el 99,2%**. XAU terminó en 0.857×,
o sea **−14,3% en términos absolutos**, además de quedar 2.99× por debajo de comprar y
esperar. Los dos Sharpe son negativos.

### Caveats obligatorios — los tres van juntos

**1. El t-test pareado sin error estándar HAC subestima la varianza** cuando los errores
están autocorrelacionados. O sea: la prueba era **anti-conservadora**, tenía el sesgo a
favor de encontrar algo, y aun así no encontró nada. Eso **refuerza** el resultado
negativo en vez de debilitarlo. (Los Durbin-Watson de ~2.002 sugieren autocorrelación
residual baja en el holdout, pero el caveat se sostiene igual: no se corrigió.)

**2. Bonferroni sobre 32 pruebas correlacionadas es demasiado estricto** — los lookbacks
21/42/63/126 de la misma señal no son independientes entre sí. Por eso se corrió también
Benjamini-Hochberg, que controla FDR en vez de FWER y es el procedimiento apropiado acá.
Tampoco encontró nada.

**3. El Random Forest posterior NO es evidencia en contra, y no es citable.** Puso `bb21`
segundo en feature importance (0.1447 contra 0.1468 de `vol21`). Tres razones
independientes por las que eso no contradice lo anterior:
  - Es **in-sample**: mide qué usa el modelo al ajustar, no poder predictivo fuera de
    muestra. Son preguntas distintas.
  - La importancia por impureza **reparte el crédito entre variables correlacionadas**;
    con `bb21` y `vol21` correlacionadas, el ranking entre ellas no dice cuál aporta.
  - Esa corrida tuvo **`adjusted_close` con 5.892 nulos** y un **`TypeError` por la columna
    de fecha entrando como feature**. Está rota, aparte de todo lo anterior.

### Conclusión que queda escrita

**`core/price_signals.py` no tiene poder predictivo demostrado.**

Queda en el sistema porque `compute_gold_score_bma` lo consume, **no porque prediga**. Que
un módulo esté importado, testeado y corriendo no es evidencia de que sirva para lo que su
nombre sugiere, y este acta existe para que nadie lo deduzca de su presencia.

Consecuencia ya implementada: la advertencia de `compute_gold_score_bma` y el campo
`gold_score_warning` de `AssetCycleResult` citan estos números, y viajan pegados a todo
gold_score calculado.

**Lo que NO dice este acta:** que las señales sean inútiles en cualquier forma o
configuración. Dice que **estas dos, con estos lookbacks, sobre estos dos activos, con
este método, no superaron a su baseline**. XAU/bb42 sigue sin medirse fuera de muestra.

---

## 2026-09-09 — Retiro de cuatro funciones sin consumidor en `core/scoring.py`

**Fuente:** auditoría del 8-sep sobre `5c9a723`, reverificada sobre `92ef999` antes de
tocar nada. Ninguna de las cuatro tiene una sola llamada fuera de `tests/`: todas las
referencias en código son menciones en docstrings.

**El código completo vive en la rama `archive/core-scoring-pre-retiro-20260909`**, creada
a la altura de `main` antes del retiro. Principio #3: archivar, nunca borrar.

| función | líneas | por qué se retira |
|---|---:|---|
| `compute_godel_p90` | 92 | Gemela de `compute_godel_p66`, idéntica salvo por el percentil. Quedó muerta al fusionar el PR #17, cuando la máscara pasó a P66. Dos funciones que difieren en una constante invitan a que alguien use la que no corresponde. |
| `compute_mass_panic_index` | 76 | Sin consumidor, y con **4,1% de coincidencia** con el legacy (auditoría del PR #17). Tres causas sistemáticas acumuladas: ventana auto-referencial vs. histórica, `ddof` distinto, y que el legacy devuelve un índice clipado mientras el port devolvía un bool. |
| `compute_entropy_fibonacci_lags` | 56 | Sin consumidor, y con **0,0% de coincidencia**: el legacy `add_fibonacci_lags` desplaza `log_return`, el port desplazaba entropía. No es imprecisión, son series distintas. El port siguió un comentario de `gdelt_foundation.py` en vez de la implementación. |
| `compute_entropy_delta_lags` | 54 | Sin consumidor. Existía como complemento del anterior; sin él no queda nada que complementar. |

**278 líneas de función.** Con los símbolos que solo existían para alimentarlas
—`MIN_WINDOW_FOR_ZSCORE`, los dos umbrales `Z_*`, `MassPanicComponent`,
`MassPanicResult`, `_zscore_last`, `FIBONACCI_LAG_DAYS`, `FibonacciLagResult`,
`DeltaLagResult`— el total real es **350 líneas, el 20,4% del módulo**. Verificado que
ninguno de esos nueve símbolos tenía otro usuario: dejarlos habría sido cambiar cuatro
funciones muertas por nueve tipos muertos. El archivo pasa de 1.714 a 1.384 líneas —el
neto es 330 y no 350 porque se agregaron ~20 de documentación explicando qué se retiró y
dónde encontrarlo.

### Lo que NO se borró, y por qué

**El bloque de tests de `compute_godel_p90` se PORTÓ a `compute_godel_p66`, no se
eliminó.** Esos 15 tests no prueban la función retirada: prueban el **contrato de la
ventana móvil** —que se toma del final, que termina el día anterior, el warm-up, que
`window < 1` lanza— que las dos gemelas compartían. `compute_godel_p66`, que es la que
usa producción, tenía solo dos tests propios: el percentil que pide y que llame a
`_ventana_movil`. Nada fijaba lo demás.

Borrarlos con la función habría dejado el umbral real de la máscara sin guardas sobre su
propio contrato. Verificado por mutación después de portarlos: recortar desde el
principio en vez del final pone **19 tests en rojo**, y quitarle el recorte a
`compute_godel_p66` pone **15**.

Al portarlos hubo que cambiar una fixture, y el motivo es informativo: el test de
contaminación usaba una historia plana con un outlier, que **movía el P90 y no mueve el
P66** —un solo valor no alcanza para correr un percentil 66 en una ventana de 10, haría
falta que superara el 34% superior—. Con una rampa el efecto sí se ve, porque lo que
mueve el umbral no es la magnitud del día sino que la ventana se desplace al incluirlo.

**Los hallazgos #1 y #3 del docstring del módulo se conservan.** Son auditorías del
LEGACY —el conflicto de dos fórmulas para `mass_panic_index`, y que los lags son en
días— y siguen siendo ciertas después de retirar el port. Le sirven a quien retome esos
features. Lo que sí se corrigió es el #3, que afirmaba lo que el port hacía: se agregó
que el legacy desplaza `log_return`, no entropía.

### Menciones colgando

Se limpiaron las once referencias en docstrings que quedaban apuntando a funciones
inexistentes. Un docstring que apunta a algo que no está es peor que no tener docstring:
manda a leer código que no se puede leer. Las cinco menciones que sobreviven a
`compute_godel_p90` son deliberadas y están marcadas como históricas ("retirada el
9-sep"), igual que la cita de `mass_panic_index` y `fibonacci_lags` dentro de
`compute_godel_score`, que las nombra como precedente de auditoría y no como código vivo.

**Suite: 676 → 654 tests** (676 pasaban antes; ahora 654 pasan y 2 se saltan). El neto
de −22 se descompone así: desaparecen **37** definiciones de test y reaparecen **15**
renombradas de `p90` a `p66`. O sea, se borran 22 tests reales —los de las tres funciones
sin contrato compartido— y los 15 de la ventana móvil siguen ahí, apuntando ahora a la
función que usa producción.

---

## Principios que se sostuvieron toda la sesión

- Ningún número entra sin fuente verificada contra el código real (no contra memoria de sesiones anteriores, no contra texto pegado sin auditar).
- Todo commit corre pytest antes de comitear. Todo patch se verifica en clon 100% ajeno vía `git am` antes de entregarse.
- Discrepancias encontradas (memoria vs. fuente real, texto externo vs. constantes reales) se registran explícitamente, no se resuelven en silencio.
- Un hallazgo de auditoría no crítico (#6, `drive_root`) se corrigió antes de seguir agregando trabajo encima, no se dejó como nota para "después".

---

## 2026-09-16 — Retiro de la cadena `gold_score` a `research/`

**Fuente:** el mapa de consumidores reales verificado sobre el código, no supuesto.
Consumidores de `core/scoring.py` fuera de `tests/`: `orchestration/cycle.py`,
`tools/measure_godel_samples.py`, `ingestion/gdelt_aggregation.py`. Solo el primero
tocaba la cadena.

### Qué se movió

De `core/scoring.py` a `research/gold_score_chain.py` (521 líneas, sin una sola
línea de lógica modificada):

- `NashFrozenSource`, `NashFrozenResult`, `compute_nash_frozen_7d`
- `GodelScoreResult`, `compute_godel_score`, `VAL_DIR_SIN_INFERENCIA`
- `GoldScoreRegime`, `GoldScoreAction`, `GoldScoreKillReason`, `GoldScoreResult`,
  `compute_gold_score_bma`
- Las ocho constantes que solo ellas usaban (`NASH_*`, `BMA_WEIGHTS`,
  `KL_DIVERGENCE_THRESHOLD`, `NATIVE_ASSETS`, `SHANNON_KILL_THRESHOLD`,
  `MIN_REFERENCE_MULTIPLIER`, `MIN_WINDOW_FOR_NASH`)

`core/price_signals.py` → `research/price_signals.py` (`git mv`, historia intacta).
El cableado que los componía dentro de `run_scoring_cycle` →
`research/cycle_gold_score.py`.

### El motivo, que son dos y el segundo es el que decide

**Uno:** `compute_gold_score_bma` → `compute_godel_score` → `val_dir` → un LSTM
entrenado que no existe. Eso ya haría de la cadena un camino muerto.

**Dos, y es peor porque está medido** (hallazgo del PR #19): el término
`w_godel * godel_score` **no puede aportar a ningún `gold_score` distinto de cero**.
`compute_gold_score_bma` mata el score a 0.0 cuando la máscara Gödel dispara, y
`compute_godel_score` vale 0.0 cuando la máscara NO dispara. Cuando el componente
tendría valor, el kill lo anula; cuando el kill no actúa, el componente vale cero.
No hay entrada posible que haga aportar a ese término.

`price_signals` se fue por un motivo distinto y del mismo tipo: su tesis direccional
se midió el 4-sep-2026 y se refutó (ver el acta de esa fecha).

Consecuencia sobre el ciclo diario: venía calculando todos los días un número que no
podía significar nada, y lo emitía con una advertencia de cinco líneas pegada
(`GOLD_SCORE_SIN_PODER_PREDICTIVO`) para que nadie lo usara. **Un número que hay que
acompañar de un cartel que dice "no usar" no es una salida del sistema.** Se retiró
el número y se retiró el cartel.

### Por qué `research/` y no `archive/*`

Los retiros anteriores (PR #22) se fueron a ramas `archive/*`, donde el código deja
de importarse, de correr y de compilar contra el resto. Eso es correcto para algo
que no va a volver.

Esto es distinto porque **la condición de reversión está escrita y es concreta**. El
código tiene que seguir siendo importable y corrible: si no, el día que se retome
habrá que redescubrir si todavía funciona. `research/tests/test_aislamiento.py` fija
la dirección de la dependencia — el motor nunca importa de `research/`, y `research/`
sí importa del motor, para correr contra el código vivo y no contra una copia
congelada que se desactualiza sin que nadie se entere.

### CONDICIÓN DE REVERSIÓN

**Si Fase 2 entrena el LSTM que produce `val_dir`, la cadena vuelve.** No hace falta
reescribir nada: mover los símbolos de `research/` a `core/` y restituir las cinco
líneas de import de `orchestration/cycle.py`.

Con una salvedad que hay que resolver ANTES de revertir, porque revertir sin
resolverla devuelve el mismo problema: el hallazgo del PR #19 sigue en pie. Tener
`val_dir` hace que `compute_godel_score` devuelva un número real, pero **no** arregla
que el kill por máscara lo anule. La reversión exige decidir qué pasa con esa
contradicción — no alcanza con que exista el modelo.

### Qué NO se movió, y por qué

`compute_vitality_tesla` y `VitalityResult`/`VitalityTier` se quedan en `core/`:
`tools/measure_godel_samples.py:74` los importa. No están muertos aunque el ciclo
diga que no son compuerta. Igual `godel_active`, `entropy_state`, `compute_godel_p66`,
`compute_adaptive_percentile`, `classify_gdelt_event` y los filtros por país — son la
anotación de régimen, que es lo que el ciclo emite ahora.

### Validación

Conteo exacto, partiendo de los **778** que pasaban en `main` (`dd9ea63`):

| | tests |
|---|---|
| `pytest tests/` — el job que bloquea | **723** |
| `pytest research/tests/` — no bloquea | **74** |

**Ningún test se borró.** Se movieron **68**: 48 de `test_scoring.py`, 8 de
`test_cycle.py` y 12 de `test_price_signals.py`. 778 − 68 = 710, que es lo que
quedaría en `tests/` si nada más hubiera cambiado.

Los **19** restantes (710 → 723 en `tests/`, y 68 → 74 en `research/`) son nuevos y
se declaran uno por uno porque el brief pedía que la suma cerrara:

- **+6** `research/tests/test_aislamiento.py` — fija que el motor nunca importe de
  `research/`. Sin él, el retiro es una afirmación sobre carpetas y no sobre quién
  llama a quién, y se revierte con un import que nadie mira.
- **+13** `tests/test_registro_linguistico.py` — netos, de un defecto que este PR
  destapó y tuvo que arreglar; ver abajo.

Dos tests de `tests/test_cycle.py` se **actualizaron, no se borraron**
(`..._calcula_los_3_reales` → `..._calcula_la_anotacion_de_regimen`, y el que
construía `AssetCycleResult` con los campos viejos): afirmaban sobre `nash_frozen` y
`gold_score`, que salieron del ciclo. Cambiaron porque cambió el contrato.

### Efecto colateral: el barrido de voseo marcaba todo el futuro de indicativo

El docstring de `research/__init__.py` escribió "habrá que redescubrir" y puso en rojo
a `test_ningun_string_del_repo_usa_voseo` (PR #26). **No era voseo: era un defecto de
la regla, y mío.**

La regla `-á` afirmaba que "ninguna otra forma verbal del español termina en á
tónica". Es falso — el **futuro de indicativo entero** termina en á: *será, habrá,
tendrá, calculará, permitirá*. La regla los marcaba a todos. No se vio al escribirla
porque en ese momento ningún docstring del repo usaba un futuro: el barrido daba verde
por suerte, no por estar bien.

El recorte es exacto y no una lista de excepciones: **todo futuro español termina en
`-rá`** (los regulares son infinitivo + á, y todo infinitivo termina en r; los
irregulares —*habrá, tendrá, podrá, sabrá, dirá, hará, querrá, pondrá, vendrá, saldrá,
valdrá, cabrá*— también). `-rá` sale del patrón automático.

El precio: los imperativos voseantes de verbos con raíz en r (*mirá, borrá, entrá*)
caen del lado del futuro y pasan a la lista explícita — mismo trato que ya tenían los
de -er/-ir, y por el mismo motivo: homografía real. Los +13 tests son las dos mitades
de eso, y están para que la próxima versión no lo rompa de nuevo.

Se arregló acá y no en un PR aparte porque bloqueaba: la alternativa era reescribir la
prosa para callar al linter, que es exactamente lo que el docstring de ese test dice
que no hay que hacer.

---

## 2026-09-20 — El registro de constantes, y tres hechos sobre el umbral que no existe

**Fuente:** barrido por AST de `core/`, `ingestion/`, `orchestration/`,
`governance/` y `execution/`; más tres mediciones que corrió Altair sobre la serie
completa (ver la salvedad de reproducibilidad al final).

### Lo que se construyó

`config/constantes.json` — las **55** constantes de módulo del repo, con valor,
procedencia y nivel de evidencia. Primer contenido del stream `CONFIG`, que
`governance/persistence.py` declaraba desde el patch 0010 sobre un directorio que
no existía.

No es documentación: `tests/test_registro_constantes.py` corre en cada PR y falla
en las dos direcciones — si un valor registrado deja de coincidir, y si aparece una
constante en el código que nadie anotó. El descubrimiento es por AST y sin importar
nada, para que un módulo con un `ImportError` no desaparezca del barrido en
silencio.

**El estado que el registro deja a la vista:** 37 de 55 son
`provisional_sin_evidencia`, 16 `legacy_citado`, y 2 `medido` — y las dos `medido`
son sobre el comportamiento de una fuente (GDELT migró a HTTPS), no sobre un
número. **Ningún valor numérico del sistema tiene evidencia medida.** El registro no
introduce ese hecho; lo hace imposible de no ver.

Una distinción que quedó escrita porque cambia entradas concretas: **citar una
fuente no es tener evidencia.** `SUCCESS_SCORE_THRESHOLD` cita textualmente
`spel_bayesian_core.py` (`">= 850/1000 trayectorias con gold_score > 0.85"`) y aun
así es `provisional_sin_evidencia`, porque `core/monte_carlo.py:34` dice que el
número está "pendiente de calibración post Gate R30" y sin backtest. El legacy tenía
el valor; nadie lo midió nunca.

### Hecho 1 — El empalme legacy/pipeline está verificado

Sobre 8 días (BTC y XAU, 2026-08-30 a 09-03), `n_events` y `entropy_shannon` salen
**idénticos hasta el último decimal** comparando la serie importada del parquet
contra `aggregate_day()`.

Importa para la ventana móvil de 252 días: como cruza el borde entre lo importado y
lo calculado en vivo, si las dos mitades no fueran la misma metodología el percentil
estaría mezclando dos poblaciones y nadie lo vería. No las mezcla.

### Hecho 2 — No existe un `p66_entropy_global_default` histórico

Se cierra como hallazgo, no como pendiente. `orchestration/cycle.py` lo exige sin
default desde que existe, figura como Incógnita #4 en `ESTADO.md`, y **nunca se
guardó ningún valor en ninguna parte**.

El único número que circulaba —**1,19**— es un fixture de `tests/test_scoring.py`.
Hay que dejar de tratarlo como candidato: no salió de una medición, salió de
alguien que necesitaba un número para que un test corriera.

### Hecho 3 — No hay un umbral global defendible, y se sabe por qué

p66 medido el 19-sep-2026 sobre la serie completa:

| activo | p66 | n |
|---|---|---|
| BTC | 1,131801 | 4.880 |
| XAU | 1,298946 | 4.879 |

La diferencia de **0,167** no es una propiedad de los activos. Se explica por
`CORE_COUNTRY_FILTERS["XAU"] = ()` — sin filtro de país, XAU agrega ~117k
eventos/día contra ~45k de BTC, y la entropía de Shannon crece con la riqueza del
soporte: más eventos distintos, más bins de tono poblados, más `H`.

**El umbral es por activo por construcción del filtro, no por naturaleza del
activo.** La consecuencia práctica es que buscar "el" default global es buscar algo
que no puede existir mientras los filtros sean distintos entre sí — y el de XAU es
vacío a propósito, portado literal del legacy (`gdelt_foundation.py::ASSET_COUNTRY_FILTERS`),
no por descuido.

### Salvedad de reproducibilidad, explícita

**Las tres mediciones no se reprodujeron en esta sesión.** El sandbox no tiene el
data lake ni la serie GDELT montados: `drive_root()` resuelve a
`.spel_drive_stream`, que no existe, y `read_series("BTC")` y `read_series("XAU")`
devuelven **0 días**. Los números de arriba son los que midió Altair y se registran
como tales, no como algo verificado acá.

Lo que sí se verificó en esta sesión es todo lo demás: las 55 constantes, sus
valores, y que el test falla cuando debe (entrada borrada, valor alterado, constante
nueva sin registrar, entrada huérfana — los cuatro comprobados uno por uno).

---

## 2026-09-21 — Enmienda a la Decisión #14: las series diarias viven en la rama `data`

**Fuente:** decisión del Admin del 21-sep-2026 (Brief D v2); `governance/persistence.py`
(`drive_root()`, `DRIVE_STREAMS`); `ingestion/gdelt_series.py`; `.github/workflows/gdelt.yml`.

**Lo que decía la Decisión #14:** METRICS es un stream de Drive, "NO versionado en git",
porque cambia todo el tiempo y no es código.

**Lo que cambia:** las series diarias **append-only** —la serie GDELT por activo y,
cuando existan, las anotaciones de régimen— pasan a una rama huérfana de este repo,
`data`, que **no se fusiona nunca** con `main`. CI (`gdelt.yml`) es su **escritor
único**. Drive deja de escribirlas: la carpeta `metrics/gdelt_series` se renombra a
`metrics/gdelt_series_OBSOLETO_2026-09-21`, y **ningún notebook vuelve a correr
`run_gdelt --write`**.

**Lo que no cambia:** los streams siguen siendo los mismos y `persistence.py` los declara
igual. METRICS sigue siendo un stream de Drive; lo que cambia es **dónde vive
físicamente una parte** de él. No hizo falta tocar una línea de persistencia: la raíz de
la rama hace de `drive_root()`, y `SPEL_DRIVE_ROOT` —que `drive_root()` resuelve
primero— apunta ahí.

**Por qué git sirve para ESTO y la regla de la #14 sigue valiendo para lo demás.** La
#14 excluía de git lo que cambia en cada corrida y crece sin techo. Una serie diaria
append-only es la excepción justa:

| | cifra | de dónde |
|---|---|---|
| bytes por línea | 242 (brief); **239–251, media 245** | medido el 22-sep con `_result_to_line()` real y floats de precisión completa |
| línea de un día vacío | 185 | ídem |
| un activo, 4.880 días | **1,19 MB** | 4.880 × 245 |
| cinco activos, 20 años | **8,9 MB** (brief: 8,8 con 242) | 5 × 7.305 × 245 |
| diff diario | **5 líneas**, una por activo | una fila por día y activo |

Un diff de cinco líneas por día es revisable a ojo, `git revert` deshace una corrida
entera, y el historial dice qué día escribió qué. Nada de eso existía en Drive. Las
cifras de tamaño **no se midieron sobre la serie real** —el sandbox no la tiene—, sino
con el serializador real y valores sintéticos.

**El motivo del escritor único no cambió; cambió cuál se elimina.** `append_day()` es
append puro y `read_series()` deduplica por día: el par tolera duplicados, no
divergencia. Hasta hoy el escritor único era Drive, y CI corría en dry-run. Lo que
impedía que fuera CI —Colab bajó 640 días en 17 minutos; a `--max-days 10` eran 64
corridas— se resuelve con la **siembra**: la historia 2013-04-01 .. 2026-09-03 de BTC y
XAU se sube a mano, una vez, por la web. Es la excepción permitida a la directiva de
autodeterminismo (entrada siguiente): subir un archivo no es escribir código.

**Hallazgo que el brief no preveía:** `SPEL_DRIVE_ROOT` no mueve solo METRICS, mueve
**los tres** streams de Drive (METRICS, MODELS, TRADE_LEDGER), porque todos se resuelven
contra `drive_root()`. En CI no importa: la ingesta solo escribe METRICS. En Colab sí:
un notebook que apunta la variable al clon de `data` para leer la serie y después guarda
un checkpoint lo pierde al cerrar la sesión. El README de la rama lo dice; si F2 lo
necesita resolver de raíz, es un override por stream en `persistence.py`, y es una
decisión aparte.

**Riesgo del brief que se corrigió al verificarlo:** el brief advertía que un archivo
sembrado con CRLF rompería el parseo. No lo rompe: `read_series()` hace `strip()`. El
riesgo real era el **salto final ausente**: la primera escritura de CI pegaba su línea a
la última sembrada y se perdían dos días en silencio. `append_day()` ahora lo agrega
(commit `gdelt_series: append_day agrega el salto final...`), y la rama nace con
`*.jsonl -text` para que git no toque los finales de línea.

**Reversión:** renombrar la carpeta de Drive de vuelta, quitar el `--write` de
`gdelt.yml`, y copiar la serie de la rama a Drive. La rama queda como archivo.

---

## 2026-09-21 — El nombre de la rama es `data`, no `data/gdelt-series`

**Fuente:** el docstring de `ingestion/run_gdelt.py` (PR #24), que proponía
`data/gdelt-series` como destino; decisión del Admin del 21-sep.

Dos motivos. El primero es de alcance: la rama no guarda solo la serie GDELT; guarda las
series diarias append-only, y las anotaciones de régimen (`metrics/regimen/`) son la
segunda. Un nombre por serie pediría una rama por serie.

El segundo es de git: una rama `data/gdelt-series` **impide** que exista una rama
`data` (una ref no puede ser a la vez archivo y directorio), y cualquier segunda serie
quedaría como `data/otra` —una rama más que clonar, que permisar y que el workflow
tendría que conocer—. Con una sola rama `data`, la estructura interna es la de
`metrics/` que `persistence.py` ya declara.

`tests/test_run_gdelt.py` fijaba el nombre viejo en el docstring; ahora fija el nuevo
y que el viejo no aparezca.

---

## 2026-09-21 — Directiva de autodeterminismo, y protocolo de notebooks para F2

**Fuente:** decisión del Admin del 21-sep-2026.

**La directiva:** toda tarea que exija Colab por peso (entrenamiento, backfills, lectura
de la serie completa) la genera el repo o un workflow como un **`.ipynb` completo**. El
Admin solo lo ejecuta. No edita celdas, no pega código, no decide parámetros en el
momento.

**La excepción, y por qué no la rompe:** la siembra de la rama `data`. Subir un archivo
por la web no es escribir código: no hay lógica que se pueda equivocar, y lo que se
subió se verifica después (`gdelt.yml`, `modo: verificar_siembra`, contra las cifras
medidas).

**Protocolo para los notebooks de F2** (el generador está fuera del alcance de este
brief y queda pendiente):

1. El notebook sale del repo, versionado. Se regenera, no se edita a mano.
2. Lee la serie clonando la rama `data`, nunca de la carpeta `_OBSOLETO` de Drive.
3. **No escribe la serie.** Nunca corre `run_gdelt --write`.
4. Si apunta `SPEL_DRIVE_ROOT` al clon para leer, lo saca antes de escribir modelos o
   ledger (ver la entrada de la enmienda: la variable mueve los tres streams de Drive).

---

## 2026-09-21 — Límite conocido: GitHub desactiva el cron tras 60 días sin actividad

**Fuente:** documentación de GitHub Actions sobre workflows programados; visibilidad del
repo verificada el 22-sep-2026 (**público**).

En un repo público, GitHub desactiva los `schedule:` cuando el repo pasa **60 días sin
actividad**. SPEL es público, así que aplica. Lo que no está claro es si los commits de
`github-actions[bot]` en `data` cuentan como actividad; se asume que **no**, que es el
caso que no sorprende.

**Lo que esto significa:** si durante dos meses nadie hace un PR a `main`, la ingesta
se apaga sola, y **la alarma de frescura no lo puede ver**: corre dentro del mismo
workflow, así que se apaga con él. Una alarma no puede detectar su propia muerte.

**Mitigación:**

- La actividad de PRs en `main`: seis fusiones entre el 14 y el 21-sep-2026.
- El `dias_de_retraso` por activo que `ingestion/frescura.py` calcula, para que el Brief C
  lo lea **desde fuera del workflow de ingesta** (el ciclo diario, o quien consuma la
  serie). Un retraso que crece es la firma de un cron desactivado.
- Reactivarlo es un clic en la pestaña Actions; no se pierde nada, porque la ingesta es
  incremental y cierra el gap sola en las corridas siguientes.

---

## 2026-09-21 — Acta de congelamiento: `core/execution_costs.py` y `core/trade_ledger.py`

**Fuente:** PR #25 (los dos módulos); decisión del Admin del 21-sep-2026.

Los dos módulos quedan **congelados** tal como están en `main`: sin consumidores nuevos,
sin funciones nuevas, sin tarifas. Sus tests siguen corriendo en cada PR. No se retiran
a `research/`: no están muertos, están esperando algo que costear.

**Motivo:** hoy no hay nada a qué aplicarlos. `execution_costs` calcula P&L neto sobre
tarifas que recibe como parámetro obligatorio (no vive ninguna tarifa en el módulo), y
no hay ni instrumento elegido cuyas tarifas cargar ni estrategia que backtestear.
Seguir construyendo sobre ellos sería construir un backtester sin nada que probar.

**Briefs 4 y 5** (el backtester y lo que dependía de él) **se descartan por ahora.**

**CONDICIÓN DE REVERSIÓN**, cualquiera de las dos:

1. F2.7 identifica un instrumento de Deriv cuyos costos cubren estos módulos (taker/maker,
   funding, fills parciales) y hay tarifas verificadas para él.
2. F2 produce una estrategia que backtestear.

---

## 2026-09-21 — Deuda heredada: los huecos de la serie sembrada no se curan

**Fuente:** cifras de la medición del 19-sep-2026; `tools/verificar_siembra.py`;
`ingestion/frescura.py::inventariar_huecos`.

Entre 2013-04-01 y 2026-09-03 hay **4.904 días de calendario**. BTC tiene 4.880 filas y
XAU 4.879: **24 huecos en BTC y 25 en XAU**. Las cifras cierran entre sí, y hay un test
que lo verifica (`test_las_cifras_cierran_contra_el_calendario`).

**Las fechas no están acá**, y no por descuido: el sandbox no tiene la serie
(`drive_root()` resuelve a `.spel_drive_stream`, que no existe, y `read_series()` devuelve
0 días). La primera corrida de `gdelt.yml` con `modo: verificar_siembra` las imprime una
por una; esa lista es la que completa esta entrada.

**Decisión:** son deuda conocida y **no se rellenan**. La alarma de frescura no las
evalúa —solo mira desde la marca de inicio `metrics/ingesta_automatica.json`, el primer
día que escribió CI— y la reconciliación de 404 tampoco las reintenta. Rellenarlas sería
un backfill con `--since`, que reescribe, y es trabajo aparte con su propio brief si
alguna vez hace falta.
