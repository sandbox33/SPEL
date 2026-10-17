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

---

## 2026-09-25 — Gobernanza: DG-1 a DG-6

**Fuente:** decisiones del Admin del 25-sep-2026 (Brief final v3, §0). Se registran en el PR
de gobernanza (PR-G), antes que cualquier otro PR del brief.

| ID | Decisión |
|---|---|
| DG-1 | El **repositorio es la única fuente canónica**. `SPEL_MANUAL_OPERACION.md` (Drive, 7-sep-2026) deja de ser canónico: se regenera desde el repo o se archiva, y lo hace el Admin en Drive. Se elimina la regla "gana el manual sin verificar fecha". |
| DG-2 | El capital de referencia es de **100 USD** y reemplaza los "$10" de `BLUEPRINT.md`. |
| DG-3 | `governance/PRINCIPLES.md` #6: **6 meses de forward en demo** antes de cualquier capital real. El paso a real exige que la hipótesis apruebe sus compuertas del pre-registro **sobre histórico + forward**, con el mismo N. **Reemplazada el 29-sep-2026**: ver la entrada de esa fecha. |
| DG-4 | H3 usa **un contrato por activo**: largo si al menos la mitad de los lookbacks soportados están largos, fuera en caso contrario. |
| DG-5 | **H1 se mantiene** sin cambios de diseño, salvo los del cierre de H1-A (PR-0). H3 es la ruta principal. |
| DG-6 | El test anti-desfase de `ESTADO.md` falla si hay **más de 3 PRs fusionados** desde su commit de referencia. |

**DG-1, por qué ahora.** La regla de desempate que ponía al manual por encima del repo
decía "sin excepción y sin necesidad de verificar cuál de los dos se escribió después".
Con la ingesta escribiendo en `data` todos los días y el estado cambiando PR a PR, un
documento en Drive que nadie regenera queda atrás en días, y la regla lo hacía ganar igual.
El código en `main` manda sobre cualquier documento.

**DG-2, el motivo con los números.** Con 10 USD, el piso de comisión de 0,10 USD de los
multiplicadores de cripto sería de ~2,5 % de una posición típica. Verificado: con el sizing
del pre-registro H1 y una volatilidad del 60 % —el supuesto de la sonda—, la posición
completa es de 10 × 0,25 / 0,60 = 4,17 USD, y 0,10 USD es el **2,40 %**. Por sub-posición
del ensemble (1/6) sería el **14,4 %**. Con 100 USD: 0,24 % y 1,4 %.

**DG-3** queda escrito en `governance/PRINCIPLES.md` #6, que decía que la duración "se
decide una vez y se escribe acá": esta es esa vez. (El 29-sep el Admin la reemplazó por
condiciones medibles; ver esa entrada.)

**DG-4 y DG-5** van a `research/preregistro_h3.md` (PR-H3), que se fusiona **antes** que el
PR #31: al fusionar el #31 el cron empieza a escribir los precios del universo, y el
pre-registro de H3 tiene que existir antes de que haya un precio que mirar.

**DG-6** es `tests/test_estado_al_dia.py`. La regla vieja —punto 6 de "cómo actualizar
este archivo"— dependía de que alguien se acordara, y falló dos veces con el diagnóstico
ya escrito (16 y 17 días de desfase). Al registrarse DG-6, `dd9ea63..main` tenía **5**
merges: el test nace en rojo y lo pone en verde la actualización de `ESTADO.md` del mismo
PR. El umbral vive en `governance/estado.py::MAX_MERGES_SIN_ACTUALIZAR_ESTADO` y en
`config/constantes.json`.

---

## 2026-09-29 — La siembra, verificada: coincide con la serie medida, y la deuda tiene fecha

**Fuente:** `tools/verificar_siembra.py` corrido el 29-sep-2026 contra la rama `data` en el
commit `11e45f6`, con `SPEL_DRIVE_ROOT` apuntando a un `git archive` de esa rama. La
verificación no se corrió por el workflow: el dispatch de `gdelt.yml` con
`modo: verificar_siembra` nunca se hizo (las siete corridas de `gdelt.yml` del 19 al 28-sep
son un dispatch del 19-sep, anterior a la rama `data`, y seis del cron). Es el mismo código
sobre los mismos datos.

**Resultado: VERDE en los dos activos.**

| | filas en el rango sembrado | válidos | rango | líneas corruptas | salto final |
|---|---|---|---|---|---|
| BTC | 4.880 (esperadas 4.880) | 4.880 (4.880) | 2013-04-01 .. 2026-09-03 | 0 | sí |
| XAU | 4.879 (esperadas 4.879) | 4.879 (4.879) | 2013-04-01 .. 2026-09-03 | 0 | sí |

sha256 de los archivos **tal como se subieron** (commit de la siembra `03206fd`, 22-sep-2026),
para cotejar a mano con el original de Drive:

- `BTC.jsonl`: `57819bd8a65414ea29438d4f6d7689c7a75d1be59e28cfea294c939b3eadef1c`
- `XAU.jsonl`: `00cd613177e8b003062f2acccb9a15139e5fdd7cb0c0d6bdd6187f6e980d7eb1`

Los dos terminan en salto de línea, así que la guarda de `append_day()` no tuvo que actuar.

**Las fechas de la deuda heredada** (la entrada del 21-sep decía que esta lista la
completaba):

- **BTC, 24 huecos:** 2014-01-23, 2014-01-24, 2014-01-25, 2014-03-19, 2022-11-10,
  2023-03-23, y **2025-06-14 .. 2025-07-01** (18 días seguidos).
- **XAU, 25 huecos:** los mismos, más 2014-03-10.

**El hallazgo es el tramo de 2025:** 18 días de calendario seguidos sin fila en los dos
activos. No es ruido suelto; es un corte de la serie. Cualquier análisis que use la
entropía de esos meses —H2 en particular, que usa la entropía rezagada para pronosticar
la volatilidad— tiene que declarar antes de mirar qué hace con él. Sigue sin
rellenarse, como decidió la entrada del 21-sep.

---

## 2026-09-29 — DG-3 reemplazada: el paso de demo a real se mide, no se espera

**Fuente:** decisión del Admin del 29-sep-2026 (addendum al Brief final v3, ítem 1).
Reemplaza el texto de DG-3 del 25-sep ("6 meses de forward en demo").

El paso de demo a capital real exige **todas**:

| | condición | constante en `governance/paso_a_real.py` |
|---|---|---|
| a) | PSR ≥ 0,90 y DSR ≥ 0,90 sobre retornos diarios de cartera, en la serie combinada histórico + demo, con el mismo N | `PSR_MINIMO`, `DSR_MINIMO` |
| b) | ≥ 30 días de demo y ≥ 20 operaciones cerradas en demo | `DIAS_MINIMOS_DEMO`, `OPERACIONES_CERRADAS_MINIMAS_DEMO` |
| c) | costos observados en demo ≤ 1,25 × costos modelados | `RAZON_MAXIMA_COSTO_OBSERVADO_MODELADO` |
| d) | reconciliación demo sin discrepancias | — |

**Zona gris:** mientras falte alguna, el forward sigue, con un máximo de 6 meses
(`MESES_MAXIMOS_ZONA_GRIS`). Al vencer sin cumplirlas, el trabajo pasa a H2.

**Qué cambia.** La duración deja de ser un piso y pasa a ser un tope: con las cuatro
cumplidas, el paso puede darse desde el día 30 de demo; sin ellas, tampoco a los 6 meses.
Dos de las condiciones miden lo que solo la demo puede mostrar: los costos reales contra
los modelados (c) y lo operado contra lo registrado (d).

**Dos lecturas que no dicta el texto**, declaradas como [INTERPRETACIÓN] en el módulo:
los 6 meses se cuentan desde el primer día de demo, en meses de calendario, y el día del
vencimiento es el último en que todavía se puede cumplir; los días de demo son de
calendario.

**Dónde se aplica:** `governance/PRINCIPLES.md` #6 (con la cláusula "se decide una vez y
se escribe acá — no se negocia por sesión bajo presión de tiempo", que el texto del 25-sep
había perdido), `CLAUDE.md`, `BLUEPRINT.md`, las compuertas de `research/preregistro_h3.md`
(PR #33) y la §10 del pre-registro de H1 (rama del PR #31). Las constantes, en
`config/constantes.json`.

APTO no autoriza ninguna orden: la compuerta de la Fase 4 y la decisión del Admin siguen
haciendo falta.

---

## 2026-10-05 — DG-7 y DG-8: autonomía asimétrica y la batería fuera de execution/

**Fuente:** decisiones del Admin del 05-oct-2026 (brief de respuesta a la sonda §0.A-3 v2,
puntos 3c, 3d y 4).

| ID | Decisión |
|---|---|
| DG-7 | **Autonomía asimétrica.** El sistema puede reducir riesgo sin intervención humana: pausar una estrategia, bajar el stake, cerrar posiciones o activar el circuit breaker. Aumentar riesgo, cambiar un parámetro, reactivar una estrategia pausada o reasignar capital solo ocurre ejecutando una regla fijada antes en un pre-registro sellado con sha256. Cada regla nueva suma a N y consume presupuesto de α. **Ningún parámetro sube en ejecución.** |
| DG-8 | **Arquitectura.** La batería multi-estrategia se construye fuera de `execution/`, en paquetes nuevos (`strategies/` y un orquestador). Usa `execution/circuit_breaker.py` y `execution/execution_guard.py` sin modificarlos; su congelamiento hasta la Fase 4 se mantiene. |

**DG-7, en la práctica.** Las cuatro acciones que reducen riesgo pueden estar en el
código de ejecución sin otra condición. Las cuatro que lo aumentan solo pueden estar como
regla de un pre-registro ya sellado, y esa regla cuenta en el N del Deflated Sharpe como
cualquier otro ensayo.

**DG-8, lo que no cambia.** El congelamiento de `execution/circuit_breaker.py` y
`execution/execution_guard.py` (CLAUDE.md, "Congelados") sigue igual: la batería los llama,
no los edita. `git diff --name-only origin/main...HEAD -- execution/` sigue teniendo que estar
vacío.

**La regla de órdenes demo de CLAUDE.md, reescrita.** Citaba `authorize.is_virtual == 1`,
que pertenece a la API legacy, retirada (decision-log 2026-10-01). La API nueva no tiene
`authorize`: la cuenta se elige al pedir el OTP. La regla nueva: órdenes demo solo desde
`integracion_demo/`, con OTP emitido para una cuenta cuyo `account_type` sea `demo` según
`GET /accounts`, y conexión solo a `/ws/demo`. Las sondas en `tests/` pueden pedir OTP y
cotizar, nunca comprar, solo con autorización explícita del Admin en un brief fechado,
registrada en este decision-log.

---

## 2026-09-25 — Deriv: la API legacy no separa demo y real; la nueva sí

**Fuente:** esquemas oficiales de `deriv-com/deriv-api-docs` (`config/v3/*/send.json` y
`receive.json`, leídos el 25-sep-2026); búsqueda sobre `developers.deriv.com/docs/options/websocket/`
(el sitio está bloqueado desde el sandbox: lo que sigue de la API nueva sale del extracto del
buscador, no de la página leída); `ingestion/adapters.py`; `ingestion/deriv_ws.py`.

**La API legacy** —`wss://ws.derivws.com/websockets/v3?app_id=…`, la de `DERIV_WS_ENDPOINT`—
tiene **un endpoint único**, y **el token decide la cuenta**. Nada en la URL dice si la
conexión es demo o real.

**La API nueva de opciones separa las cuentas por ruta:**
`wss://api.derivws.com/trading/v1/options/ws/demo` y `…/ws/real`, autenticadas con un OTP de
un solo uso que se pide por REST por cuenta (válido 120 segundos, según el extracto).

**El adapter está en la legacy, y Deriv está migrando.** Lo segundo lo reportó el Admin; el
calendario de la migración no se pudo leer desde acá.

**Consecuencia en el código:** `ingestion/deriv_ws.py` exige `entorno: "demo" | "real"` sin
default en todo punto de entrada, y `real` exige además `SPEL_DERIV_PERMITIR_REAL=1`,
comprobado **antes** de abrir la conexión. Como en la legacy el entorno no se puede verificar
por la URL, se verifica contra la cuenta: con token, se llama a `authorize` y se compara
`authorize.is_virtual` ("1 or 0, indicating whether the account is a virtual-money account",
según el esquema) con el entorno pedido. Un token de cuenta real con `entorno="demo"` corta la
sesión. Migrar a la API nueva haría esa verificación estructural en vez de por respuesta.

---

## 2026-09-25 — La demo valida integración, no ejecución

**Fuente:** decisión del Admin (Brief H1-A).

Una cuenta demo **no es evidencia suficiente de deslizamiento ni de rechazos**: el
precio de llenado y la probabilidad de rechazo de una cuenta virtual no tienen por qué
parecerse a los de una real, y no hay forma de saber cuánto difieren sin operar en real.

Para lo que **sí** sirve: validar **integración** (que los mensajes sean los que la API
acepta), **idempotencia** (que una corrida repetida no duplique nada) y **reconciliación**
(que lo que el sistema cree que pasó coincida con lo que la cuenta dice que pasó).

Cualquier número de costo de ejecución que salga de la demo se reporta como tal, y no
reemplaza a la comisión del pre-registro (`research/preregistro_h1.md`, sección 8).

---

## 2026-09-25 — El 1,246× en BTC no es operativo todavía

**Fuente:** entrada del 2026-09-04 ("Magnitud — sí discrimina, y solo en BTC"); decisión del
Admin (Brief H1-A).

La razón de volatilidad de **1,246** (Mann-Whitney, p = 3,4×10⁻⁹) dentro vs. fuera del
régimen de entropía se midió **contemporáneamente**: la entropía del día `t` contra la
magnitud del retorno del día `t`. Para usarla en el dimensionamiento hace falta que prediga,
no que acompañe.

**Queda como no operativa hasta superar dos pruebas:**

1. **El rezago a `t−1`:** la entropía de `t−1` tiene que discriminar la magnitud de `t`.
2. **La comparación contra HAR-RV:** tiene que aportar algo sobre un modelo HAR de volatilidad
   realizada, que ya predice volatilidad con la propia historia de precios. Si la entropía no
   mejora lo que HAR-RV ya hace, es una forma cara de medir lo mismo.

Ninguna de las dos se corrió. El sizing del pre-registro H1 usa volatilidad realizada de 20
días, **no** la entropía.

---

## 2026-09-25 — Nada de órdenes automáticas, ni en demo, hasta que H1-B dé ventaja neta

**Fuente:** decisión del Admin (Brief H1-A); `ingestion/deriv_ws.py`; `tests/test_deriv_ws.py`.

Ninguna orden automática, **tampoco en demo**, hasta que H1-B produzca una ventaja **neta de
costos** que pase las compuertas del pre-registro.

**Cómo se sostiene, y no solo se promete:**

- `ingestion/deriv_ws.py` solo envía siete mensajes (`active_symbols`, `contracts_for`,
  `proposal`, `ticks_history`, `time`, `authorize`, `ping`). Cualquier otro se rechaza antes
  de tocar el socket.
- `proposal` sale sin `subscribe`: es una cotización, no un stream. El brief pedía
  `subscribe: 0`, pero el esquema oficial solo admite el valor 1, y omitir el campo es lo
  equivalente sin arriesgar un error de validación.
- `tests/test_deriv_ws.py` recorre por AST `core/`, `ingestion/`, `orchestration/`,
  `governance/`, `execution/`, `tools/` y `research/`, y falla si aparece como literal el
  nombre de un mensaje de orden.

**Condición de reversión:** H1-B pasa las compuertas (Sharpe neto fuera de muestra ≥ 0,5,
PSR(0) ≥ 0,90 y DSR ≥ 0,90), y un brief propio amplía la lista blanca con su decision-log.
`execution/` sigue congelado hasta Fase 4 y esto no lo toca.

---

## 2026-10-01 — La API legacy de Deriv está muerta; el WS público nuevo responde

**Fuente:** sonda de endpoints §0.A (`tests/test_deriv_endpoints_live.py`), corrida por el Admin
en *SPEL Live Tests* sobre la rama del PR #31: run `36894412401`, job `110477682413`, commit
`18c8c0b`, 01-oct-2026 16:45–16:46 UTC. El informe está en el log del job; lo que sigue
se copió de ahí, no se reconstruyó.

**Legacy, `wss://ws.derivws.com/websockets/v3?app_id=…`: NO DISPONIBLE.** El handshake
devuelve **HTTP 520** (`InvalidStatus: server rejected WebSocket connection: HTTP 520`).
Ningún mensaje llegó a enviarse. Coincide con lo que reportaba la fuente secundaria.

> **Corrección del mismo día (ver la entrada siguiente):** esta medición usó un App ID
> inválido. El 520 no prueba por sí solo que la legacy esté muerta; la decisión de migrar se
> sostiene por la documentación oficial y por el reporte de terceros.

**WS público nuevo, `wss://api.derivws.com/trading/v1/options/ws/public`: responde.**
Handshake 101 y los cuatro mensajes, con estos sha256 de la respuesta cruda:

| mensaje | resultado | sha256 |
|---|---|---|
| `time` | `1790873176` (2026-10-01 16:46:16 UTC) | `89de6289b94175e076f6d2e4b723bf60275bf540b00e56bb3caa669b52e5c3ca` |
| `active_symbols` | **89** símbolos; el código viene como `underlying_symbol` | `d358da161475bbc35fb5243dc98b8f52e55623d1c74c15f8b878cad3da160827` |
| `ticks_history` | 256 velas diarias de `OTC_AEX` | `29eb33559f8c178a0b253478f10022e8abcabf78d9178bdbdea4a0c7d46c795d` |
| `contracts_for` | `OTC_AEX`: CALL, PUT y otros; **sin MULTUP** | `c17b226ce010faa492d56f4e38ae904f4808cf7676e28b7b1e8b06addf561551` |

**Lo que el informe muestra además, y que la sonda 2 tiene que medir antes de migrar:**

- **El símbolo elegido no fue BTC.** La sonda tomó el primer no sintético en orden
  alfabético, `OTC_AEX` (mercado `indices`). Nada de esto dice todavía qué pasa con BTC y
  con el oro.
- **Una ventana de exactamente 365 días.** Con `count` 5000 y `end: latest` llegaron 256
  velas. La primera tiene época `1759337176` (2025-10-01 16:46:16 UTC), justo **31.536.000 s
  = 365 días** antes de la hora del servidor. Si ese es un tope por petición y la historia no
  se puede paginar hacia atrás, la condición de parada no se alcanza con datos de Deriv: 1.076
  velas para la rejilla completa de H3, 1.058 para la mínima de H1 y 2.608 para la completa
  de H1.
- **Velas desalineadas.** Esa primera vela no es múltiplo de 86.400: es una vela de borde,
  cortada en la hora de la corrida. La siguiente (`1759363200`, 2025-10-02 00:00) sí está
  alineada. `ingestion/velas.py` rechaza toda vela desalineada, así que la migración tiene
  que descartarlas.
- La última vela (`1790812800`, 2026-10-01 00:00) era la del día en curso, todavía abierta.
  `velas.py` ya la descarta por la hora del servidor.

**Consecuencias:**

- `ingestion/source_registry.json` marca el endpoint legacy como no disponible, con esta fecha
  y esta evidencia.
- `test_live_endpoint_legacy` deja de exigir éxito: registra el estado y se retira con la
  migración. Si siguiera fallando, el job de live-tests quedaría rojo para siempre y su rojo
  dejaría de significar algo.
- **Antes de migrar** `ingestion/deriv_ws.py` corre una segunda sonda (§0.A-2) con la
  profundidad paginada de BTC y del oro, la alineación por página, `contracts_for` de BTC, del
  oro y del control, una `proposal` de cotización en el canal público, y la autenticación REST
  de la cuenta. La migración se decide con su resultado.

---

## 2026-10-01 — Credenciales de Deriv: el App ID era inválido, un solo token, y su vencimiento no se conoce

**Fuente:** respuesta del Admin al reporte del 01-oct, verificada por el Admin en
developers.deriv.com y en el OpenAPI oficial (`deriv-com/deriv-api-schemas`). Lo que dice el
OpenAPI se volvió a leer acá, en el commit `54e3538` (release `production_v20260901_0`);
developers.deriv.com no se pudo leer desde el sandbox.

**La sonda del 01-oct midió la legacy con un App ID inválido.** El secret `DERIV_APP_ID` tenía
el nombre del token, no un App ID. El HTTP 520 de la entrada anterior no prueba por sí solo que
la legacy esté muerta para un App ID válido. **La decisión de migrar se sostiene** por la
documentación oficial de Deriv —la API legacy está retirada y los App IDs heredados ya no son
válidos— y por el reporte de terceros con HTTP 520 (`nuchukwuma/trading-bot#4`). Lo medido en
el WS público no depende del App ID: el OpenAPI no lo pide en `ws/public`.

**Credenciales nuevas.** El Admin registró una app tipo PAT, "SPEL TRADER", con recargo 0 %, y
actualizó `DERIV_APP_ID` y `DERIV_API_TOKEN`. El token tiene solo el scope `trade`: la API
nueva no tiene un scope de lectura, y el OpenAPI exige `trade` en `GET /accounts`. Según el
OpenAPI, el header `Deriv-App-ID` es obligatorio en `/accounts`, `/accounts/{accountId}/otp`,
`bulk-purchase/demo`, `bulk-purchase/real` y `legacy/*`; ninguna de las tres rutas `ws/*` lo
declara.

**Un solo token.** Se elimina `DERIV_DEMO_TRADE_TOKEN` del plan de PR-D: los PAT son por
usuario, así que un segundo token no aísla la demo de la real. Queda solo `DERIV_API_TOKEN`.
Lo que aísla es la guarda siguiente.

**Guarda nueva, a construir en la migración de `deriv_ws.py` y en PR-D:**

- un test por AST que falla si `"ws/real"` o `"bulk-purchase/real"` aparecen en el código fuera
  de `tests/`;
- OTP solo para un `accountId` cuyo `account_type` sea `demo` según `GET /accounts`.

Reemplaza a la guarda vieja de `authorize.is_virtual`, que la API nueva no tiene.

**Límite conocido: el vencimiento del token es DESCONOCIDO.** La interfaz de Deriv no mostró
una fecha al crearlo. El OpenAPI tampoco documenta un campo ni un header con el vencimiento en
`/accounts` ni en `/legacy/migration-status`; el único `expires_at` del archivo es el del
código de verificación de los agentes de pago. La sonda §0.A-2 informa cualquier header o
campo con "expir" en el nombre, por si Deriv manda uno igual. La alarma de frescura, con aviso
14 días antes, se configura cuando haya fecha. Hasta entonces, **un 401 en la parte
autenticada se reporta como posible vencimiento del token, no como un fallo de código.**

**Lo que se espera de la parte e) de la sonda §0.A-2:** con estas credenciales, `GET /accounts`
y `GET /legacy/migration-status` tienen que autenticar. Si fallan, el informe trae el código
HTTP y el cuerpo con los IDs tapados, sin reintentos: un pedido por ruta.

**Interpretaciones de la sonda §0.A-2 y de los pre-registros, aprobadas por el Admin:**

- La moneda de la `proposal` sale de la cuenta demo de `/accounts`.
- La paginación usa `start = end − 5000 × 86400`, y el host REST es `api.derivws.com`.
- Los umbrales de historia diaria de BTC: **1.058** (H1, rejilla mínima), **1.076** (H3,
  rejilla completa, la condición de parada del addendum) y **2.608** (H1, rejilla completa).
  El informe compara contra los tres, con velas alineadas.
- PR #34: `TOLERANCIA_ASOF_DIAS = 0`.
- PR #33: los pesos vigentes son las posiciones abiertas más la nueva, antes de `s`, con 60
  días de calendario. Para pasar a real, la lectura estricta: Sharpe ≥ 0,5 y benchmark sobre
  la serie combinada.
- PR #32: los 6 meses son de calendario desde el primer día de demo, y el día del vencimiento
  cuenta.

---

## 2026-10-05 — Sonda §0.A-2: la REST autentica, y la historia diaria de Deriv es de 365 días

**Fuente:** sonda §0.A-2 (`tests/test_deriv_sonda2_live.py`), corrida por el Admin en
*SPEL Live Tests* sobre la rama del PR #31: run `36915129174`, job `110547122004`, commit
`e5ca13e`, 01-oct-2026 19:33–19:34 UTC, con las credenciales nuevas (app PAT "SPEL TRADER").
Los cinco tests `live` pasaron. Lo que sigue se copió del log del job.

**REST, `https://api.derivws.com`: autentica.**

| ruta | resultado | sha256 del cuerpo |
|---|---|---|
| `GET /trading/v1/options/accounts` | HTTP 200; dos cuentas: `DOT********` **demo** y `ROT********` **real** | `8a57245d401aa4fec4e52ace68c8c8150bb5eb6496ab02b189e8afd1b3098c00` |
| `GET /trading/v1/options/legacy/migration-status` | HTTP 200; `status: not_applicable` | `8d3e483568f67bed6eea9c2e194f6402b30b9b6019eb2f686acab230ba1cf5f5` |

**El `header:expires` del informe no es el vencimiento del token.** La sonda informó
`expires: Mon, 03 Oct 2016 19:33:52 GMT` en `/accounts`: es la cabecera HTTP de caché
(RFC 9111 §5.3), y una fecha pasada es la forma de decir "no cachear". Se eliminó de la
detección (`CABECERAS_HTTP_DE_CACHE`). **El vencimiento del token sigue DESCONOCIDO.**

**WS público: los símbolos se resolvieron por mercado, sin literal.** `active_symbols`: 89
símbolos en cinco mercados (`commodities`, `cryptocurrency`, `forex`, `indices`,
`synthetic_index`), sha256 `c8aadca843934a5ef29ca26d565e47c9bced76eecfd02a52943dc68b6b671cf8`.
BTC = **`cryBTCUSD`** (el único candidato con BTC en `cryptocurrency`), oro =
**`frxXAUUSD`** (el único con XAU en `commodities`), y el control `frxEURUSD` presente.

**Profundidad diaria: 365 días, y `end` se ignora.**

| símbolo | página | `end` pedido | velas | primera época | última época | desalineadas | sha256 |
|---|---|---|---|---|---|---|---|
| `cryBTCUSD` | 1 | `latest` | 366 | `1759347233` | `1790812800` | 1 | `ed70ce0c1fad9c19acbdd9ccf58b89a08cdcf3e43b5222193d5eac72f24d879d` |
| `cryBTCUSD` | 2 | `1759347232` | 366 | `1759347233` | `1790812800` | 1 | `8f6d021ceb340756ead8c2d4726dfbbaf0038f6be0b4891f9bf238b7e7c2a12f` |
| `frxXAUUSD` | 1 | `latest` | 259 | `1759347233` | `1790812800` | 1 | `b5de0c3d7f3653332f10a7a96f19f2ba0ec4eb46b746ac22dd83f903684b7154` |
| `frxXAUUSD` | 2 | `1759347232` | 259 | `1759347233` | `1790812800` | 1 | `f3fdd8df31f1de5e3d98656ec31e30d163dc06ee32e25a551c5011dbc4d13998` |

La página 2 pidió `end` = primera época − 1 y devolvió la misma ventana: **Deriv ignora
`end`** y la sonda cortó por "la página no retrocedió". La primera época es la hora de la
corrida menos 31.536.000 s (365 días), desalineada; el resto está en múltiplos de 86.400. En
total: **365 velas alineadas de BTC y 258 del oro.**

**Umbrales: BTC no llega a ninguno.** 365 < 1.058 (H1, rejilla mínima) < 1.076 (H3, rejilla
completa) < 2.608 (H1, rejilla completa). La sonda marcó `detener: true`. Por el ítem 8 del
addendum del 29-sep, la condición de parada de H1 y H3 es inalcanzable con datos diarios de
Deriv y la decide el Admin. El brief del 02-oct abre la sonda §0.A-3 sobre una candidata
intradía; no dice nada de H1 ni de H3, y este registro tampoco.

**Contratos: MULTUP y MULTDOWN en los tres símbolos**, con `multiplier_range`
**{100, 200, 300, 500, 800}**, `default_stake` 2 y `cancellation_range` vacío.

| símbolo | sha256 de `contracts_for` | `proposal` MULTUP ×100, stake 2 USD: `commission` | sha256 de la `proposal` |
|---|---|---|---|
| `cryBTCUSD` | `bc75c06431b423aa967c5c9313a65da274e7659c798b1fe3ee92dc76662eb7e9` | 0,14 | `322069e5932841bcfcbdb3efabc94c9536577ecebec0fa1775a1384361f3ecff` |
| `frxXAUUSD` | `27577a30cf9d013270af050b1638e1d7eae30b48b7242a32dba6d74d1179991f` | 0,04 | `252561f27716779bfcb2f4a829b7802b0ce578c6558ab5d18d2cf054f528d3d1` |
| `frxEURUSD` | `c8a47a164233c46d7bac22869499fcf5ae2142f16e373fd726312ef6ba344b08` | 0,05 | `d2bd3d25167ff7c3fc8a771624f3c1f6a376156dedf0aafa81c3fb3069f319bf` |

La `proposal` respondió en `ws/public` con `underlying_symbol` y la moneda de la cuenta demo
(USD). `validation_params.stake`: mínimo 1,00 y máximo 500,00 USD en los tres.

**La unidad de `commission` no está resuelta.** El esquema la describe como "Commission
changed in percentage (%)". Si fuera un monto, 0,14 USD sobre un nocional de 200 USD es
0,07 %; si fuera un porcentaje, serían 0,28 USD. La sonda §0.A-3 cotiza a tres stakes y dos
multiplicadores para separar las dos lecturas.

**La legacy, con el App ID nuevo:** HTTP 520 en el handshake otra vez.

---

## 2026-10-05 — Sonda §0.A-3 v2: sin pata convexa, costos proporcionales, y TwelveData no siempre en UTC

**Fuente:** sonda §0.A-3 v2 (`tests/test_deriv_sonda3_live.py` y
`tests/test_fuentes_sonda3_live.py`), corrida por el Admin en *SPEL Live Tests* sobre la rama
del PR #31: run `37318228891`, job `111790308511`, commit `c85ea5e`, 05-oct-2026 13:37–13:45
UTC. Pasaron los 9 tests `live`.

**Qué parte del log se pudo leer.** El log del job tiene 12.624 líneas. Desde el sandbox se
leyeron las últimas 5.000, que es lo que devuelve la herramienta de GitHub; el archivo
completo está en un host que el proxy del sandbox bloquea. Quedaron legibles el canal
**demo** entero, las **diferencias** público-demo, el final del intradía público (oro M15),
TwelveData, Alpha Vantage y cTrader. Quedaron fuera la sonda de la legacy, las cuentas y el
canal **público** (escaneo, cotizaciones y la mayor parte del intradía). Las cifras de esa
parte se marcan como **lectura del Admin** y no llevan sha256. La sonda §0.A-3b publica cada
informe en una sola línea, para que entre entero en lo legible.

**a) Vanillas y turbos solo existen en 13 índices sintéticos.** En el canal demo
(`active_symbols` con 89 símbolos, sha256
`2ffc9b9d30a6bf3755b1fe107877a8430069219d1ddbddf6abe9f73209912270`), VANILLALONGCALL/PUT y
TURBOSLONG/SHORT aparecen en `1HZ100V`, `1HZ10V`, `1HZ15V`, `1HZ25V`, `1HZ30V`, `1HZ50V`,
`1HZ75V`, `1HZ90V`, `R_10`, `R_100`, `R_25`, `R_50` y `R_75`, todos sintéticos, y en
**ningún** símbolo no sintético. El escaneo del canal público no difiere del demo: la lista
de diferencias no tiene ninguna clave `escaneo:`. **La pata convexa queda descartada**
(decisión del Admin). Multiplicadores no sintéticos: `cryBTCUSD`, `cryETHUSD`, 14 pares
`frx…`, `frxXAGUSD` y `frxXAUUSD`.

**b) Los multiplicadores de BTC, oro y EUR/USD aceptan `limit_order.stop_loss`.** Las 18
cotizaciones demo con stop-loss (tres símbolos × tres stakes × 0,3 % y 0,6 % del nocional)
fueron aceptadas, y en el canal público también: ninguna de sus claves está entre las
diferencias de aceptación.

| símbolo | stake | stop-loss | devuelto | sha256 |
|---|---|---|---|---|
| `cryBTCUSD` | 1 | 0.30 | `-0.30` | `b77a46a30ed3104990dad11fe06895141166f5c6586c5acaaf8fc405d9acdca2` |
| `cryBTCUSD` | 1 | 0.60 | `-0.60` | `32e6a45771a52754ffdb81d343c134c242e29c5fbff4f346774c6cb04f5ec65d` |
| `cryBTCUSD` | 1.5 | 0.45 | `-0.45` | `8b171b6218ab0e61619f4b16a514b87a175a11cdbbc7d4d9a79fe801f18233a4` |
| `cryBTCUSD` | 1.5 | 0.90 | `-0.90` | `510fea9e895fc609ec73539cee564ba15a547520149445efc420f209738e66b7` |
| `cryBTCUSD` | 2 | 0.60 | `-0.60` | `c04b1d8cc4959e71ee7f501e7e5853f68547cdcb8d52014dd4a095f1b5055ff8` |
| `cryBTCUSD` | 2 | 1.20 | `-1.20` | `43b7f3dd487b39b836cf95d47854ebc2b72584bef882283b104a0322375d0d49` |
| `frxXAUUSD` | 1 | 0.30 | `-0.30` | `e70462b60f8fff5c64d5a5262c22c6802d8db054f7bf010d4b58c918bd57e98a` |
| `frxXAUUSD` | 1 | 0.60 | `-0.60` | `66aec2669bd625049682bb99b54aecc93b0e81a927a059d97af098c76e6675db` |
| `frxXAUUSD` | 1.5 | 0.45 | `-0.45` | `06c9b4bbfbc9fa422591bde5d3bb1f342d030290d4f5f65ace220381275b7ef8` |
| `frxXAUUSD` | 1.5 | 0.90 | `-0.90` | `01695abf538b636f3da0f307dadeb7b7864b1a33613e877cef52d3d92054a9ea` |
| `frxXAUUSD` | 2 | 0.60 | `-0.60` | `fe20bad3f1aaf4b4eec7ebf5398a8fb96d545efb6052c9c6fd6df694604121a4` |
| `frxXAUUSD` | 2 | 1.20 | `-1.20` | `485a587aacf3603e2ff9a1e424ba7bc5b5cf50de9a668e3be3f482329a4df0fe` |
| `frxEURUSD` | 1 | 0.30 | `-0.30` | `7e57fe196ad1809bc722675bb6d9bae6107c9fa2aab7b0297fc6f1f4a718a093` |
| `frxEURUSD` | 1 | 0.60 | `-0.60` | `8b70cea3372b0bae3f24f4f100b001175041896e430c6fae023c36a403e04aaa` |
| `frxEURUSD` | 1.5 | 0.45 | `-0.45` | `31dbb8956df4bf991ce270db7c91b3a2d428b825c4baba09616ccab04fa7231c` |
| `frxEURUSD` | 1.5 | 0.90 | `-0.90` | `8c81554867f9fa73e262d9f0d2a0ac02c5fa3f0ea486f7b060953703385c354d` |
| `frxEURUSD` | 2 | 0.60 | `-0.60` | `9990a08aa859ba392614bc14f1e0df367f12bc4792df368f3e87c2df34b14851` |
| `frxEURUSD` | 2 | 1.20 | `-1.20` | `29bcacc1258248d48eae76341f3f81914723e6ed0ba37e4965dc59dfda1051f2` |

*El monto del stop incluye la comisión.* Con el spot que implica el stop-out de la misma
corrida (×100, stake 1), la distancia al precio del stop de 0,30 USD es 0,2842 % en el oro y
0,2805 % en EUR/USD: coincide con (0,30 − 0,02) / 100 = 0,28 % y no con 0,30 %. Lo mismo a
0,60 USD (0,5848 % y 0,5807 % contra 0,58 %). En BTC el cálculo no es concluyente: el spot
se movió entre las cotizaciones.

*El mínimo.* `validation_params.stop_loss.min` fue **0,10 USD** en todas las cotizaciones
demo salvo BTC ×200: **0,12 = comisión 0,11 + 0,01**. El Admin lo leyó como "mínimo 0,10 USD
(en BTC, comisión + 0,01)". En lo legible, ninguna cotización de BTC tiene un mínimo de 0,10
igual a la comisión + 0,01: lo que muestra es un mínimo de max(0,10; comisión + 0,01).

**c) La comisión es proporcional al nocional, sin mínimo fijo.** Demo, con el stop-out de
d):

| símbolo | mult. | stake | comisión | % del nocional | mínimo del stop-loss | error del stop-out | sha256 |
|---|---|---|---|---|---|---|---|
| `cryBTCUSD` | ×100 | 1 | 0.03 | 0.0300 % | 0.10 | 0.20 % | `3ebf5013effa340645bece7d1b0bc73dd8b744c821da3c3fae3e777e540c701a` |
| `cryBTCUSD` | ×100 | 1.5 | 0.04 | 0.0267 % | 0.10 | 0.14 % | `09f59e906ad15696895af297a32f5eff5deaa3ba6a369c105f31cc8adb804eba` |
| `cryBTCUSD` | ×100 | 2 | 0.06 | 0.0300 % | 0.10 | 0.20 % | `c143dc79e96336e36d7ff9d6dd29d123c9cbe39605d58e05bf5be9c7d0a57268` |
| `cryBTCUSD` | ×200 | 2 | 0.11 | 0.0275 % | 0.12 | 0.11 % | `b88bb77388a82a14a7bfa82ca34c7630af002ce415217b98b6a3e520a89ce780` |
| `frxXAUUSD` | ×100 | 1 | 0.02 | 0.0200 % | 0.10 | 0.08 % | `d8e4c729d6129f4ca6277a626d02e9b4bec142a00a6ecdb50d7ab859481affd0` |
| `frxXAUUSD` | ×100 | 1.5 | 0.03 | 0.0200 % | 0.10 | 0.08 % | `bffaa528ba3c39278ad4f11ceefb727e334a1cab844c4fb53efb236aab908cb8` |
| `frxXAUUSD` | ×100 | 2 | 0.04 | 0.0200 % | 0.10 | 0.08 % | `f38482550c84db11273e377787f643e66faaa1ffbc841a7fef9d39a2507c04ad` |
| `frxXAUUSD` | ×200 | 2 | 0.08 | 0.0200 % | 0.10 | 0.19 % | `1f232835bb3340b917d5892af23e9ecf6bbf3c808dc8c99509557500a403209a` |
| `frxEURUSD` | ×100 | 1 | 0.02 | 0.0200 % | 0.10 | 0.01 % | `f08d72b809346f1174bc1709562ab9672dd82c59ae411b333b52ab4f2665995a` |
| `frxEURUSD` | ×100 | 1.5 | 0.03 | 0.0200 % | 0.10 | 0.10 % | `7ed2d2e601bee7355cac2e908a8d606c330bf9e6549a2bb0a56dabbf96b8d766` |
| `frxEURUSD` | ×100 | 2 | 0.04 | 0.0200 % | 0.10 | 0.10 % | `dc16416788e0d30db6ab17b85beba6f5bc3d9ce91a57f6289566e6fc044ca9f3` |
| `frxEURUSD` | ×200 | 2 | 0.08 | 0.0200 % | 0.10 | 0.14 % | `8133ef166b56588c3ba12eff69297d626b9165a65a37ca2288eeac5643fb87bc` |

Canal público, tomado de las diferencias público-demo (cotizaciones de la misma corrida, no
del mismo instante):

- BTC: 0,08 / 100 = **0,080 %**; 0,11 / 150 = **0,073 %**; 0,15 / 200 = **0,075 %**;
  0,31 / 400 = **0,078 %**. El Admin lo resumió como 0,075–0,08 %; el valor de 150 USD queda
  en 0,073 % por el redondeo a centavos.
- Oro: 0,03 / 100 = **0,030 %**; 0,05 / 150 = **0,033 %**; 0,06 / 200 = **0,030 %**.
- EUR/USD: igual al demo, **0,02 %**.

Demo: BTC **0,027–0,030 %**, oro **0,02 %**, EUR/USD **0,02 %**.

**El canal público rechaza ×200 en el oro y en EUR/USD** con `ContractBuyValidationError`; el
mensaje literal ("Accepts 50,100,150,250,500") es lectura del Admin. El demo lo acepta, y su
`validation_params.stake.max` es **2.000 USD** en todas las cotizaciones (el público decía
500 en la sonda 2). Nota: `contracts_for` publica `multiplier_range` {100, 200, 300, 500,
800} en los dos canales, así que el rango que acepta la `proposal` pública no es el que
publica `contracts_for`.

**d) Stop-out verificado: distancia = 1/m − comisión/nocional.** En las 12 cotizaciones
demo, la lectura más cercana fue la de la comisión como **monto**, con un error relativo de
entre 0,01 % y 0,20 %. Las tres de stake 1 a ×100 son ambiguas por construcción (nocional
100). La unidad de `commission` queda resuelta: es un monto en la moneda de la cuenta, no un
porcentaje, aunque el esquema diga "percentage".

**e) Las velas intradía de Deriv tienen el mismo tope de 365 días.** En lo legible, oro M15:
23.152 velas, **23.123 alineadas**, de `1759708800` (2025-10-06 00:00 UTC) a `1791207000`, y
las 12 páginas legibles respetan `end`. BTC M5 con **105.037** alineadas y oro M5 con **69.352** son
lectura del Admin.

**f) TwelveData, plan Basic, diario:**

| símbolo | `earliest_timestamp` | sha256 | filas | páginas (sha256) |
|---|---|---|---|---|
| BTC/USD | 2017-08-28 | `ab43cf45ca799f89ea8e1ef1e816f30c0af42556732b4cf34d900aadb141f131` | **3.326**, OHLC | `58b1f13e58469495487bcb0a8dd4f7af64b3dddd0c1e9c8a8fe72100c4063e0d`; fin: 404 "Data not found" `5043c1af5d2100923002ba840ce0522cb9298e8402022bf1bd0a6ea906b026f2` |
| XAU/USD | 1979-12-26 | `a756fdc4ff44d4e1b1203e958418b70bbfbeb9fef3c79154d58f8de5ece135bf` | **12.288**, OHLC | `6f11f6430a1379425480f01ca37af7f1131ead252f13fd88c8593589fae9967f`, `7932ba5d332d9792ed696b220ec6ae6024c65b625c73e5beb73ce9ade6d39c08`, `57f6859f320b5930400516f22b45d0cf4ac58840b62766bd6c04817c51e4a9ca`; fin: 404 `ec8e83467a8f38fb4ee1fab0aa2d6badc2b1921f39661354b7c0d5fc4c4f7fd4` |

Sin columna de volumen en ninguno. La página que pasa el fondo devuelve **404 "Data not
found"**, no una respuesta vacía. Los headers muestran el límite por minuto: `api-credits-left`
bajó de 7 a 0 y volvió a 7. Se usaron 12 créditos.

**g) Las velas intradía de XAU/USD de TwelveData NO vienen en UTC.** En una corrida del 05-oct
de 13:37 a 13:45 UTC, la última vela de 15min fue `2026-10-06 00:30:00` (sha256
`f739368a2df6ebc915151cefabcad3ec1c7a94d4404ba017619d545730a21456`) y la de 5min
`2026-10-06 00:40:00` (`166ad4f4b838dde2ad7014f3dcfbfb29d2102144e265bfde1f5f0dcc02edce5b`): unas
11 horas en el futuro. BTC/USD sí cerró en la hora de la corrida (13:30 y 13:40). **Toda
llamada intradía futura a TwelveData lleva `timezone=UTC`; 1d no la lleva** (decisión del
Admin; el texto decía "toda llamada", y el Admin lo precisó el 06-oct: ver esa entrada).

**h) Alpha Vantage está operativo; el oro solo trae un precio.**

| llamada | filas | primera fecha | campos | sha256 |
|---|---|---|---|---|
| FX_DAILY EUR/USD, full | 5.000 | 2007-08-03 | open, high, low, close | `55f6e8792074878ed4ff18cd25df0a6fac54c6ff990b10861462206ba5fbda5a` |
| DIGITAL_CURRENCY_DAILY BTC | 5.925 | 2010-07-17 | open, high, low, close, volume | `616b9ccc3e188ebf9402af186b830236f3df70b1a35e079f0d736a9ce2261911` |
| GOLD_SILVER_HISTORY GOLD, daily | 5.415 | 2011-06-01 | `date`, `price` | `39eaf4de509bb99b346ab59d77dbc302476274a2dbf2bd0cd0ead6f7db124913` |

**La legacy sigue muerta con un App ID válido**: lectura del Admin para esta corrida, y ya
medido en la del 01-oct (run `36915129174`, HTTP 520 con la app PAT).

**cTrader:** los tres secrets ausentes, "pendiente de registro".

**Un defecto de la sonda, corregido para la 3b.** Las diferencias de `contracts_for` de oro y
EUR/USD entre canales son solo barreras de contratos diarios (por ejemplo `4185.90` contra
`4182.91`), que siguen al spot: la huella de comparación no excluía las barreras.

---

## 2026-10-05 — Respuesta del Admin a la sonda §0.A-3 v2: κ, cTrader y la cotización real

**Fuente:** brief del Admin del 05-oct-2026.

- **Interpretaciones 1 a 7 de la sonda §0.A-3 v2: aprobadas tal como están.**
- **Modelo de costos (κ).** κ por activo = el **máximo medido en la cuenta real** (sonda
  §0.A-3b, punto 5d). Si esa medición falla, el máximo entre público y demo. Va en
  `config/constantes.json` con su procedencia (run y sha256). **Pendiente:** se registra
  cuando corra la 3b.
- **cTrader queda fuera del alcance** por ahora y se retira de las sondas.
- **DG-7 y DG-8** se registran en el PR de gobernanza contra `main`.
- **Autorización de un solo uso, para la sonda §0.A-3b:** UN OTP para la cuenta cuyo
  `account_type` sea `real` según `GET /accounts`, y una conexión a `/ws/real` **solo para
  cotizar**, entre el 05-oct-2026 00:00 y el 09-oct-2026 23:59 UTC. Reglas, sin excepciones:
  exactamente una cuenta real en `GET /accounts`; saldo 0 antes del OTP, o, si `/accounts`
  no lo expone, `balance` como primer mensaje y cierre sin enviar nada más si es mayor que 0;
  esquema `wss`, host `api.derivws.com` y ruta `/trading/v1/options/ws/real` verificados antes
  de conectar; lista blanca de `time`, `balance`, `contracts_for` y `proposal` sin
  `subscribe`, que lanza antes de enviar cualquier otro tipo; una sola conexión, máximo 40
  mensajes, cierre al terminar; la URL con el OTP nunca va al informe. Es la autorización
  explícita que pide la regla nueva de CLAUDE.md para que una sonda en `tests/` pida un OTP.

---

## 2026-10-06 — Sonda §0.A-3b: real y demo cuestan lo mismo, κ entra al registro, y decisiones del Admin

**Fuente:** sonda §0.A-3b (`tests/test_deriv_cotizacion_real_live.py` y `tests/test_sonda3b_live.py`),
corrida por el Admin en *SPEL Live Tests* sobre la rama del PR #31: run `37404371657`, job
`112078531190`, commit `6bc0795`, 06-oct-2026 a las 02:29 UTC. Seis tests `live` pasaron. El log
tiene 381 líneas y se leyó **entero**: los informes de la 3b salen en una sola línea. El Admin lo
auditó.

### Decisiones del Admin

- **TwelveData.** La regla queda: **toda llamada intradía a TwelveData lleva `timezone=UTC`; 1d no
  la lleva, porque es una barra de solo fecha.** `TwelveDataAdapter` no se toca: ya hacía eso. La
  regla del 05-oct decía "toda llamada" y quedó corregida. Los tests existentes del adapter la
  fijaban para 1d y 1h; `tests/test_twelvedata_timezone.py` la fija para cada timeframe, 5m y 15m
  incluidos.
- **κ.** Entra el **máximo medido en la cuenta real** por activo. Si en un activo la medición real
  falla, va el máximo entre público y demo, marcado como respaldo. **Reemplaza "el mayor medido en
  5d"** que usó 5c.
- **DG-8.** La batería va en un **paquete nuevo**; no usa `orchestration/`. El nombre se fija en el
  brief de la batería, y el paquete no se crea antes.
- **Aprobados:** las interpretaciones de la 3b, el retiro de las sondas 2 y 3 del job live, y la
  salida de `ALPHAVANTAGE_API_KEY` y de cTrader.

### a) Canal real: saldo 0, 21 mensajes, cerrado, ninguna orden

`GET /accounts` (HTTP 200, sha256 `678171c4f008f891c56baba0da0c776711969c35b0890c4e21045c35084262c2`): una cuenta demo
(`DOT********`) y una real (`ROT********`), las dos activas. Saldo real **0 según `GET /accounts`**,
así que no hizo falta el mensaje `balance`. Un OTP real (HTTP 200), una conexión, **21 mensajes**
(las 21 `proposal`), conexión **cerrada** al terminar 5d. **Ninguna orden**: la lista blanca del canal
real no deja pasar nada que no sea `time`, `balance`, `contracts_for` o una `proposal` MULTUP sin
`subscribe`. Un OTP demo, también una conexión. Las 21 combinaciones se cotizaron en el mismo
minuto UTC en los tres canales, con a lo sumo 0,91 s entre el primero y el último.

### b) Real y demo dan lo mismo; el canal público es distinto

| símbolo | stake | mult. | público | demo | real | sha256 (real) |
|---|---|---|---|---|---|---|
| `cryBTCUSD` | 1 | ×50 | 0.04 | rechazo: 100,200,300,500,800 | rechazo: 100,200,300,500,800 | `5c5c1c054463a29f68cb379da068738f116d013e00af13872844fecbb189e87b` |
| `cryBTCUSD` | 1 | ×100 | 0.07 | 0.03 | 0.03 | `158a5167875bceafd5956a9bbcaa5564741b7bf81dd21dc8b60a6baec573a865` |
| `cryBTCUSD` | 1 | ×200 | 0.14 | 0.05 | 0.05 | `091593b931c6af2d9984524511faff2aa7b937845732d69a3ad3b179682ab964` |
| `cryBTCUSD` | 2 | ×50 | 0.07 | rechazo: 100,200,300,500,800 | rechazo: 100,200,300,500,800 | `7f49c8141ed9ee00850ff9872a23e4e479408e94e3523dd074c1fda7b38a381b` |
| `cryBTCUSD` | 2 | ×100 | 0.14 | 0.05 | 0.05 | `5b29492162c944314682311bb08727fbc38ec5388ab864eb4da5ad6e23857cde` |
| `cryBTCUSD` | 2 | ×200 | 0.28 | 0.10 | 0.10 | `e2b5034fee654691c791483c75f9be8e2f107458d0c35b17f83aa27aa18afe2c` |
| `cryBTCUSD` | 1 | ×100 + SL 0,30 | 0.07 | 0.03 | 0.03 | `0a1e73ab4b88301e6077cb80c7e12f18fa4f48a148cb45812db32e301e12fa2e` |
| `frxXAUUSD` | 1 | ×50 | 0.02 | rechazo: 100,200,300,500,800 | rechazo: 100,200,300,500,800 | `19844950094c5ad3a695326b5f231f26df8c39f36bd9a84f0f781a244699b320` |
| `frxXAUUSD` | 1 | ×100 | 0.02 | 0.02 | 0.02 | `7d88c8b3ba7851801c80d96894e79412e39acd56583cef80a514a6a6afdf2f5d` |
| `frxXAUUSD` | 1 | ×200 | rechazo: 50,100,150,250,500 | 0.02 | 0.02 | `8488e70d3a3ef876ab8edd19a9052c2430268642ba08f34ae7cddc463b9d2b62` |
| `frxXAUUSD` | 2 | ×50 | 0.02 | rechazo: 100,200,300,500,800 | rechazo: 100,200,300,500,800 | `436756dd9a2b05c0e66ec7c0fdfcfbd353e2b34241f7a79ee7da994b77512f4c` |
| `frxXAUUSD` | 2 | ×100 | 0.04 | 0.02 | 0.02 | `84b1e2a1df288880da9222e7c2e921759a1e83bfdd8a17f1afd2a776977ea3eb` |
| `frxXAUUSD` | 2 | ×200 | rechazo: 50,100,150,250,500 | 0.05 | 0.05 | `f99d2291496e45b80a8e3ed562cd125dbcc0353c5f421f7a369329b3aeeba79a` |
| `frxXAUUSD` | 1 | ×100 + SL 0,30 | 0.02 | 0.02 | 0.02 | `e82ec6c294166cfcb2a41d64585e6e96247718e98d821b059a6e163a03d480b5` |
| `frxEURUSD` | 1 | ×50 | 0.02 | rechazo: 100,200,300,500,800 | rechazo: 100,200,300,500,800 | `db84232c55050a4ed118c08b177ffe42ec3b3161169c2069c73f71644d9e89e2` |
| `frxEURUSD` | 1 | ×100 | 0.02 | 0.02 | 0.02 | `0855a6643212653769318d038b4acea3c1163ee89d15907877bda1b3479917d7` |
| `frxEURUSD` | 1 | ×200 | rechazo: 50,100,150,250,500 | 0.05 | 0.05 | `747f54c44aa6a6855474b0e920e8ee145af1d6197e41858bf6340dab25099d52` |
| `frxEURUSD` | 2 | ×50 | 0.02 | rechazo: 100,200,300,500,800 | rechazo: 100,200,300,500,800 | `d7525bce485efeb6ec6f31e7a7fa7cc746f560a99132cae64740168ef26d3cac` |
| `frxEURUSD` | 2 | ×100 | 0.05 | 0.05 | 0.05 | `ad7a04b2d10759d423723365fd3f0a36546eaee35f88505e343498b7fbab323e` |
| `frxEURUSD` | 2 | ×200 | rechazo: 50,100,150,250,500 | 0.09 | 0.09 | `35158844bb91beb10ba24526f3ef71c1e69d7e387b68d78e577a02e7e4cca6ce` |
| `frxEURUSD` | 1 | ×100 + SL 0,30 | 0.02 | 0.02 | 0.02 | `d878f4494525cfcbffe1ebf37944fe790675f626d6f6b1d78d038a659c6b46e3` |

`commission`, límites de stake (1–2.000 en demo y real; 1–500 en el público), límites de stop-loss
y multiplicadores aceptados son **idénticos entre real y demo en las 21 combinaciones**. El
`stop_out` también, salvo en una: BTC ×200 con stake 2, `85188.895` en demo y `85188.898` en
real, la diferencia de un spot cotizado 0,3 s después. Los rechazos de ×50 de demo y real tienen
el **mismo sha256**. **Queda descartado que la demo sea más barata que la real** para cryBTCUSD,
frxXAUUSD y frxEURUSD a esta fecha. El público cobra más en BTC (0,07 contra 0,03 a ×100 con stake
1) y en oro con stake 2 a ×100 (0,04 contra 0,02).

### c) Multiplicadores aceptados

Real y demo aceptan **{100, 200, 300, 500, 800}** y rechazan ×50 ("Multiplier is not in
acceptable range. Accepts 100,200,300,500,800."): **en real el mínimo es ×100**. El público acepta
**{50, 100, 150, 250, 500}** en oro y EUR/USD (rechaza ×200 con ese mensaje) y acepta ×50, ×100 y
×200 en BTC.

### d) κ entra en `config/constantes.json`

`ingestion/kappa_deriv.py`: `KAPPA_DERIV` = **cryBTCUSD 0,0003, frxXAUUSD 0,0002, frxEURUSD
0,00025**, los tres con origen "real".

| símbolo | κ | de qué cotización | sha256 |
|---|---|---|---|
| cryBTCUSD | 0,0003 | 0,03 / 100 (stake 1 ×100, con y sin stop-loss) | `158a5167875bceafd5956a9bbcaa5564741b7bf81dd21dc8b60a6baec573a865`, `0a1e73ab4b88301e6077cb80c7e12f18fa4f48a148cb45812db32e301e12fa2e` |
| frxXAUUSD | 0,0002 | 0,02 / 100 (stake 1 ×100, con y sin stop-loss) | `7d88c8b3ba7851801c80d96894e79412e39acd56583cef80a514a6a6afdf2f5d`, `e82ec6c294166cfcb2a41d64585e6e96247718e98d821b059a6e163a03d480b5` |
| frxEURUSD | 0,00025 | 0,05 / 200 (stake 1 ×200 y stake 2 ×100) | `747f54c44aa6a6855474b0e920e8ee145af1d6197e41858bf6340dab25099d52`, `ad7a04b2d10759d423723365fd3f0a36546eaee35f88505e343498b7fbab323e` |

**La comisión viene redondeada a centavos, y con stake de 1–2 USD κ es una cota medida, no una tasa
exacta:** en el oro, nocional 100 y 200 pagan los mismos 0,02 USD (κ 0,0002 y 0,0001).
`tests/test_kappa_deriv.py` reconstruye cada máximo desde las 15 cotizaciones reales.
`core/execution_costs.py` sigue sin tocar (acta del 21-sep).

### e) 5c, con κ público (medido) y con κ real (recalculado)

5c usó `kappa_5c`, el máximo de los tres canales, que fue el del **canal público**. Se recalculó
offline, sin correr nada nuevo, el costo en fracción de R y el umbral con κ real, usando las
funciones del código (`costo_en_fraccion_de_r` y `umbral_pct` de `tests/test_sonda3b_live.py`). El
costo es decreciente en el rango, así que su percentil p sale del percentil 100 − p del rango:
recalculado así con κ público, reproduce los valores medidos a la cuarta decimal (BTC p90: 0,3739
medido, 0,3738 recalculado, por la interpolación). **La fracción de días bajo el umbral no se puede
recalcular sin las velas: queda la medida con κ público, marcada.** Lo que sí se sabe: con κ
real el umbral sube (1/m − κ crece cuando κ baja), así que la fracción con κ real es **mayor o
igual** que la medida.

**frxXAUUSD** (08:00 Europe/London; 258 días con las tres velas, 107 sin ellas; 69463 velas M5)

| | p10 | p25 | p50 | p75 | p90 |
|---|---|---|---|---|---|
| rango, % del precio | 0,118 | 0,146 | 0,194 | 0,257 | 0,386 |
| costo en fracción de R, κ público 0,0004 (medido) | 0,094 | 0,135 | 0,171 | 0,215 | 0,253 |
| costo en fracción de R, κ real 0,0002 (recalculado) | 0,049 | 0,072 | 0,093 | 0,120 | 0,145 |

| | ×50 | ×100 |
|---|---|---|
| umbral (0,6 × stop-out), %, κ público (medido) | 1,176 | 0,576 |
| umbral, %, κ real (recalculado) | 1,188 | 0,588 |
| fracción de días con rango ≤ umbral, **κ público (medido; sin recalcular)** | 0,992 | 0,973 |

**cryBTCUSD** (09:30 America/New_York; 365 días con las tres velas, 0 sin ellas; 105037 velas M5)

| | p10 | p25 | p50 | p75 | p90 |
|---|---|---|---|---|---|
| rango, % del precio | 0,134 | 0,328 | 0,538 | 0,821 | 1,145 |
| costo en fracción de R, κ público 0,0008 (medido) | 0,065 | 0,089 | 0,129 | 0,196 | 0,374 |
| costo en fracción de R, κ real 0,0003 (recalculado) | 0,026 | 0,035 | 0,053 | 0,084 | 0,183 |

| | ×50 | ×100 |
|---|---|---|
| umbral (0,6 × stop-out), %, κ público (medido) | 1,152 | 0,552 |
| umbral, %, κ real (recalculado) | 1,182 | 0,582 |
| fracción de días con rango ≤ umbral, **κ público (medido; sin recalcular)** | 0,907 | 0,515 |

### f) 5a: el intradía de TwelveData empieza en 2020

| símbolo | intervalo | `earliest_timestamp` (UTC) | sha256 |
|---|---|---|---|
| BTC/USD | 5min | 2020-03-25 04:20:00 | `e82385e2d41a47805e52ea11c587e4324e7966470107124e9927b86368754241` |
| BTC/USD | 15min | 2020-02-19 08:00:00 | `410c1ba5595da51450907ec44bfb3d5b9f152cd15ce15e8b870432709a32c85e` |
| XAU/USD | 5min | 2020-03-16 01:10:00 | `c6250399d925f4aea07c9a0ce8b528bcf33b44a1cbf90a2b575655401dbab3cf` |
| XAU/USD | 15min | 2020-01-24 02:00:00 | `ef50428efa32d175c427097f1e9a3ab3054f243aa5a8a71567dc0b91e7add067` |

| símbolo | intervalo | `end_date` | resultado | sha256 |
|---|---|---|---|---|
| BTC/USD | 5min | 2018-06-30 | 404 "Data not found" | `d2228951ae71f0c6daa139c067d8d2e1ad792b34998b86120df267a14766ad4e` |
| BTC/USD | 5min | 2021-06-30 | 5000 filas, 2021-06-12 15:25:00 .. 2021-06-30 00:00:00 | `e852b2280bd4ec23c0205b93ddf190a7268f5cbb212484b5f47ff0fdfc331cf3` |
| BTC/USD | 5min | 2024-06-30 | 5000 filas, 2024-06-12 13:55:00 .. 2024-06-30 00:00:00 | `c9c8d7c58afeeab23e9c2f860e90943fd042bfbbd923ae3381638035778d13c7` |
| BTC/USD | 15min | 2018-06-30 | 404 "Data not found" | `54265e4202319b1b57fe8e703af16bf663a160047f40640bc491f3e7b1b85e3d` |
| BTC/USD | 15min | 2021-06-30 | 5000 filas, 2021-05-08 22:00:00 .. 2021-06-30 00:00:00 | `fe30ebc9bd5eafada8ac44271c68a3ef6936c2aaa9191c2e0744cd5deb8db8be` |
| BTC/USD | 15min | 2024-06-30 | 5000 filas, 2024-05-08 21:30:00 .. 2024-06-30 00:00:00 | `f1105cf770da42559896b6fecd738ccfe6b5dc15d3ea68cf805e7b91495a4171` |
| XAU/USD | 5min | 2018-06-30 | 404 "Data not found" | `8a4539c48e1642736133b81ed157f970d545a867de890f129c01e17fd18555e8` |
| XAU/USD | 5min | 2021-06-30 | 5000 filas, 2021-06-03 20:25:00 .. 2021-06-30 00:00:00 | `6ac4c7d4883165bf255d7b035bc23d1c91c133a1c6a3fbcee8294766ae81e795` |
| XAU/USD | 5min | 2024-06-30 | 5000 filas, 2024-06-04 12:50:00 .. 2024-06-28 20:55:00 | `0d257c302291a8284ce594d69cf824c6e667606a733011cefc9d799832b96725` |
| XAU/USD | 15min | 2018-06-30 | 404 "Data not found" | `b5823989301cfb84759d87a842e4a0697200eb412e34975ad3c1bcbc4c89cad6` |
| XAU/USD | 15min | 2021-06-30 | 5000 filas, 2021-04-14 04:00:00 .. 2021-06-30 00:00:00 | `5fb3b900cb9ab96d103732888e7dd5460ef40eb3953fce7373e5e59fb33e1a74` |
| XAU/USD | 15min | 2024-06-30 | 5000 filas, 2024-04-12 20:00:00 .. 2024-06-28 20:45:00 | `849292dfaeb2be88024ad507b5ac3503f820894c8c8c3a30ff8a6d1d0357d06d` |

2018 da **404**. De las tres comprobaciones de UTC, dos dan verde en todas las páginas con datos
(ninguna vela pasa el `end_date`, todas en la grilla). La tercera **no se pudo medir**:
`meta.exchange_timezone` vino `null` en todas. 33 créditos de TwelveData.

### g) 5b: desfase 0 en los dos; el oro queda ABIERTO

| | velas Deriv | velas TwelveData | comunes | close p50/p90/p99 % | high p50/p90/p99 % | low p50/p90/p99 % | close, mediana con signo % | correlación de retornos M15 | desfase (velas) |
|---|---|---|---|---|---|---|---|---|---|
| BTC/USD | 35019 | 35040 | 35019 | 0,047 / 0,059 / 0,074 | 0,043 / 0,057 / 0,082 | 0,050 / 0,061 / 0,072 | 0,047 | 0.9983 | 0 |
| XAU/USD | 23160 | 33605 | 23160 | 0,017 / 0,095 / 0,433 | 0,014 / 0,084 / 0,437 | 0,015 / 0,093 / 0,440 | 0,001 | 0.9508 | 0 |

Páginas de TwelveData (M15, último año, `timezone=UTC`), BTC/USD: `a5cbdfb6829aa013806c7d7b158259db7a24221398134eadea782eb078071613`, `bee5d88a152fb18fba9aabb2c00780e2a7a27294cd75bddbed975e281a186901`, `9be9b2a05519b757c22a982a3a63b8e1866c4adf0373c197e9e3537bfeb1ece6`, `9064943d0da35d5cc6e0fd550edb8d6aa46a0f4c3c62d37ced939f4386baaf9f`, `06c8e57bc0329d3b192dd4d736d76010211aaae47ba0860162662713b297ccf3`, `08bbd7b00c3881c1e613dd76847eb1cd44c556fc28a276e02ab72d07e9274b4f`, `f94dcba7e561c1b4194aa0a3fe9b53c23fc351d1e0cecc60b8ae454def508599`, `b775f79de24e566feeb54653422db75fe7391164541dad2f9161d57b16cd3542`, `246e0446aa6ce25a8d629b58342134f4705ec469647919ba40c138760347502f`. XAU/USD: `733d203f326634eff6d659c276d2cd3ed8667a93b993401b2faecd3a3bda63ef`, `d129b7d9324ef0cb204793f33a274f86707d665dc75a855c3fce048ef6ff3a44`, `b39dbe1fe1ed084aee423960fad289c5b059b59cc874f0735a99573c8ad71db4`, `0708296bcc633f525296b1ab7686535553bf439dea8bfec6291d1e5fe00305f4`, `7e17ad37ba83b8f8306b2ec04673f8940d12a10bbba67e3f3bc4670956bbe3c5`, `37058d4b0079383203e3d30b112dcaa539466b8fa71a0ec3fc7a114e4763cee0`, `7a46ba782d817e32124971897217ab3b32cbb783ce3a06974f572fff3d02ccdf`, `dbe9d52fe8234b12ac9541053bb645103202a3feecafd710232749845857193b`.
La última página de cada serie, que pasa el inicio del año, devuelve 400 "No data is available on
the specified dates", no 404.

**BTC:** correlación 0,998 y sesgo de **+0,047 %** (TwelveData por encima de Deriv). **Oro:**
correlación 0,951, p50 0,017 %, p99 0,43 %, con 23.160 velas en Deriv y 33.605 en TwelveData.
**Queda ABIERTO, sin investigar todavía:** dónde caen las colas del oro y qué son las velas de más
de TwelveData.

### h) La legacy sigue en HTTP 520

`wss://ws.derivws.com/websockets/v3`: `InvalidStatus: server rejected WebSocket connection: HTTP
520` en el handshake, otra vez con el App ID válido.


---

## 2026-10-06 — `integracion_demo/`: ejecución y registro demo para Deriv Multipliers, sin estrategia

**Fuente:** brief del Admin del 06-oct-2026 (3). Precondición verificada: el PR #31 está fusionado
en `main` (merge `a84778b`, 07-oct-2026 00:53 UTC); la rama sale de ahí.

### Decisiones del Admin

- **a) El acta del 21-sep sigue.** `core/execution_costs.py` y `core/trade_ledger.py` no se tocan.
  Su condición de reversión no se cumple: los multiplicadores de Deriv cobran una comisión única,
  no taker/maker ni funding. El registro demo es un módulo nuevo, `integracion_demo/registro.py`,
  que **porta** el patrón de escritura y lectura de `trade_ledger.py` —con la línea de origen
  citada en cada comentario— y no lo copia ni lo importa.
- **b) Formato JSONL.** Sin Parquet ni pyarrow.
- **c) Sin cierre parcial.** El trailing por `contract_update` solo existe como regla de un
  pre-registro sellado; moverlo a favor reduce riesgo (DG-7).
- **d) `R_usd` = el `stop_loss` confirmado en la respuesta de Deriv**, no el solicitado ni el
  stake. La comisión se registra aparte.
- **e) Squeeze-to-Expansion: descartada como prioridad; no suma a N.** Verificado por búsqueda en
  el repo: no hay pre-registro, código ni documento que la nombre, así que no hay nada que
  retirar. Capital de referencia: DG-2 (100 USD), sin cambios.
- **Parte B, autorizada en el brief:** UNA orden en la cuenta demo —frxXAUUSD, MULTUP, stake
  mínimo, ×100, `stop_loss` 0,10 USD, sin TP, `sell` a los 60 s si sigue abierta—, con
  `GET /accounts` y balance antes y después, entre el 07-oct-2026 00:00 y el 16-oct-2026 23:59 UTC.
  El test (`tests/test_integracion_demo_orden_live.py`) va en `live-tests.yml` con
  `workflow_dispatch`; lo dispara el Admin.

### Lo que se construyó

- `integracion_demo/otp.py`: `emitir_otp_demo`, `otp_de`, `motivo_para_no_conectar`, `elegir_demo`,
  `leer_cuentas` y el enmascarado, **movidos** desde las sondas §0.A-2 y §0.A-3 sin cambiar su
  cuerpo; las sondas los importan de ahí y sus tests no cambiaron. Nuevo: `url_demo_nueva`, un OTP
  y la URL solo si es `/ws/demo`.
- `conexion.py`: un OTP nuevo por conexión y por cada intento de reconexión, backoff 1-2-4-8-16 s;
  un pedido a la vez, emparejado por `req_id`; ningún mensaje se reintenta solo; el `ping` de
  aplicación es un parámetro, y el socket se abre sin el ping de protocolo de `websockets` para
  que el cierre por inactividad se pueda medir.
- `ejecucion.py`: lista blanca de los ocho mensajes del brief, con las claves de su esquema
  oficial (commit 54e3538) y sin `subscribe` ni `passthrough`; `buy` solo con `parameters`,
  MULTUP/MULTDOWN y `limit_order.stop_loss` > 0.
- `registro.py`: JSONL append-only, `row_hash = sha256(prev_hash + fila canónica)`, crudo aparte.
- `reconciliar.py`: cruza con `profit_table` y `statement` y agrega una fila; nunca edita.
- Guardas AST (`tests/test_guarda_integracion_demo.py`) y 86 mutantes (59 de la lógica, 10 de las
  guardas, 15 de la parte B y 2 del cierre tolerante), todos atrapados
  después de reforzar dos tests.

### Interpretaciones (lo que el brief no fija)

1. **Dos columnas más** que la lista del punto 3d: `evento` (envío, compra, seguimiento,
   actualización, venta, cierre, rechazo, no enviada, sin respuesta, reconciliación) y
   `evidencia` (la del outcome, y la de la marca de reconciliación). Las filas de eventos
   intermedios llevan `outcome` nulo; las terminales lo exigen.
2. **Outcome.** Un `sell` propio confirmado da `sell_tiempo` o `sell_manual`. Si no, se compara el
   `exit_spot` con los niveles que Deriv devolvió (`stop_loss`, `stop_out` y `take_profit`), y
   entra el ÚNICO que cruzó. Si no cruzó ninguno o cruzó más de uno, el outcome es `desconocido`.
   Deriv no documenta un campo que diga qué cerró el contrato.
3. **Un `buy` sin respuesta no se reintenta:** queda `sin_respuesta` / `desconocido` y lo resuelve
   la reconciliación, que busca compras en `statement` a ±120 s del envío.
4. **`contract_update`** exige el sha256 de un pre-registro y un `stop_loss` menor que el
   confirmado. Quitar el SL (`null`) se rechaza siempre.
5. **Dónde vive el registro:** `<stream TRADE_LEDGER>/demo/`, un subdirectorio y no un stream
   nuevo: `tests/test_persistence.py` fija el conjunto exacto de streams. En el fallback local
   lanza, como `trade_ledger.py`. Agregar lee la cola del archivo (la cadena lo necesita) y se
   niega a escribir sobre un registro roto.
6. **La cadena no detecta que se borre la ÚLTIMA fila.** Para eso existe `Registro.cabeza`
   (`seq` y `row_hash`), que el informe de la parte B publica.
7. **Reconciliación:** montos iguales a menos de medio centavo; la comisión no aparece en
   `profit_table` ni en `statement`, así que no se reconcilia.
8. **Parte B:** el balance sale de `GET /accounts`, porque el mensaje `balance` no está en la lista
   blanca; el stake mínimo, de `validation_params.stake.min` de una cotización a 1 USD.
   "Una sola orden" se lee como una por autorización: si `statement` ya muestra una compra desde
   el inicio de la ventana, no compra. El job `orden_demo` es aparte del de sondas y no corre dos
   veces en paralelo.
9. **Guarda de mensajes de orden:** fuera de `integracion_demo/` no hay excepción en el código; en
   `tests/` solo los seis archivos listados, que prueban el rechazo o hacen de Deriv.

### Retirado

- **La entrada `live` de `tests/test_deriv_cotizacion_real_live.py`.** La autorización del canal
  real era de un solo uso y se usó el 06-oct, pero el test seguía activo hasta el 09-oct: un
  despacho de las sondas antes de esa fecha habría pedido un segundo OTP real. Las funciones y sus
  tests offline quedan.

### Pendiente

- **El parser de `proposal_open_contract` se fija con el informe de la parte B.** Hasta entonces
  tolera claves faltantes y guarda todo lo crudo.
- `integracion_demo` no está en los paquetes que recorre `tests/test_registro_constantes.py`, así
  que sus constantes no entran a `config/constantes.json`. No se editó ese test.
- **Para que el Admin lo resuelva, sin elegir:** CLAUDE.md dice que las sondas en `tests/` pueden
  pedir OTP y cotizar, "nunca comprar", y que las órdenes demo salen "solo desde
  `integracion_demo/`". La parte B es un test en `tests/` que compra, aunque el `buy` lo arma, lo
  valida y lo envía `integracion_demo/ejecucion.py`. Se escribió así porque el brief lo ordena
  explícitamente; si la regla tiene que nombrar este caso, va en un PR de gobernanza.

---

## 2026-10-07 — Velas M5 del oro de TwelveData y Deriv, y concordancia de su rango de apertura

**Fuente:** brief del Admin del 06-oct-2026 (4). Rama nueva desde `main` (`a84778b`). Nada se
disparó: el workflow `velas_intradia.yml` lo corre el Admin.

### Contradicción reportada y resuelta por el Admin

El brief pedía `TwelveDataAdapter` para XAU/USD con la paginación de la 3b. El código lo impedía:
el adapter dejaba XAU/USD fuera de su mapa "hasta que una respuesta real lo confirme", no
paginaba (una sola página, sin `start_date` ni `end_date`), y
`tests/test_twelvedata_adapter.py::test_simbolo_no_mapeado_es_value_error` fijaba la exclusión.
Se reportó antes de tocar nada. El Admin autorizó editar el adapter y ese test. Al reportarlo
se nombró también `tests/test_adapters.py:363`; era un error: ese test es de `DerivAdapter` y no
se tocó.

- XAUUSD entra al mapa con la respuesta real de la sonda §0.A-3b (run 37404371657,
  `earliest_timestamp` XAU/USD 5min 2020-03-16 01:10:00 UTC, sha256 `c6250399…`).
- `fetch_pagina`: una página entre `start_date` y `end_date`, `timezone=UTC` en el intradía,
  cierre de vela por la cabecera `Date` de TwelveData (nunca el reloj del runner), y el 404
  "data not found" como página vacía.
- El test de símbolo no mapeado pasa a XAGUSD. El tope de 5000 filas se movió de
  `tools/provider_coverage.py` al adapter (`TWELVEDATA_MAX_OUTPUTSIZE`), para que haya uno solo.

### Portado de las sondas, sin reescribir

- `ingestion/deriv_publico.py`: el canal, `profundidad` y `desalineadas` de la sonda §0.A-2, y la
  pausa de la §0.A-3. El canal toma la lista blanca como parámetro: las sondas conservan la de
  `deriv_ws`, y la ingesta usa una propia de solo lectura (`time`, `active_symbols`,
  `trading_times`, `ticks_history`).
- `ingestion/limitador.py`: el `Limitador` de la sonda §0.A-3.
- `tools/concordancia_xauusd.py::rangos_de_apertura`: el de 5c.
- Las sondas importan los cuatro de ahí; sus tests no cambiaron.

### Interpretaciones (lo que el brief no fija)

1. **Inicio de TwelveData:** 2020-03-16 00:00 UTC. **Tope por corrida:** 300 créditos, para unas
   95 páginas.
2. **Solo se escribe una descarga contigua.** Se pagina hacia atrás. Si se corta antes de llegar
   a lo ya guardado (o al fondo, la primera vez), no se escribe nada de esa fuente: el append
   puro no podría llenar el hueco después.
3. **Calendario:** `trading_times` de la fecha del servidor, guardado crudo con su sha256 en
   `metrics/calendario/frxXAUUSD/trading_times.jsonl`. Ese horario se aplica a todos los días.
   Las franjas:
   - **Fin de semana:** día fuera de `trading_days`, viernes desde el primer cierre, o domingo
     antes de abrir.
   - **Pausa diaria:** fuera de los tramos en un día hábil.
   - **Feriado:** dentro del horario, pero en un día sin ninguna vela de Deriv, dentro del tramo
     de fechas que Deriv cubre.
4. **Ventana de ruptura:** velas que empiezan al terminar el rango y cierran a las 12:00 de
   Londres a más tardar. Una vela que falta se salta.
5. **Fracción de d:** |Δ| / (high − low) del rango de **Deriv**. "Una rompe y la otra no" se mide
   sobre los días con ruptura en alguna fuente (la base de la compuerta), y también sobre todos
   los días comunes.
6. **Los 20 peores días:** primero los de dirección distinta o ruptura de una sola fuente; después
   los de misma dirección y otra vela. Cada fuente lista sus velas solo hasta SU vela de ruptura.
7. **La guarda de desenlaces** (`tests/test_guarda_desenlaces.py`) aplica el vocabulario a los
   cuatro módulos nuevos. La regla de "precios solo en los lectores" aplica al módulo del ORB: la
   ingesta lee todas las velas para guardarlas, y eso no es calcular un ORB.

### Mutantes

41 sobre la lógica nueva, más 5 de un segundo pase. Todos atrapados salvo uno, **equivalente**:
empezar la ventana de ruptura en el ancla. El cierre de una vela del rango no puede salir del
rango, porque la ingesta rechaza toda vela con OHLC incoherente. Cuatro sobrevivían por tests
flojos y se reforzaron. Uno destapó una regla de más (el sábado era fin de semana aunque
`trading_days` lo incluyera), que se corrigió.

### Pendiente

- Correr el workflow (lo dispara el Admin). El informe publica cuatro bloques de una línea:
  datos, calendario, concordancia y compuerta.
- El PR #36 (`integracion_demo/`) y este agregan entradas al final de este archivo y cambian el
  encabezado de ESTADO.md. El que se fusione segundo tiene un conflicto de texto en esos dos
  archivos.

---

## 2026-10-07 — Merge de `main` (PR #36) en el PR #37, y un cierre de conexión que podía colgarse

**Fuente:** brief del Admin del 07-oct-2026.

- **Merge, sin rebase.** `main` (con el PR #36) se trajo a la rama del #37 con un merge. Los
  conflictos de este archivo y de ESTADO.md se resolvieron conservando lo de los dos PRs, en
  orden cronológico.
- **Guarda de mensajes de orden.** `tests/test_velas_intradia.py` entra a
  `ARCHIVOS_DE_TEST_PERMITIDOS`, bajo "Prueban que la lista blanca de solo lectura los rechaza":
  su `{"buy": "1"}` prueba que la lista blanca pública lo rechaza. Nada más de la guarda cambió.
- **Defecto encontrado al verificar, y corregido.** La suite se colgaba de forma intermitente
  en `test_el_ping_es_un_parametro`. `ConexionDemo` (PR #36) tenía dos fallas al cerrar con ping
  activo:
  1. En Python 3.11, `asyncio.wait_for` se traga una cancelación que llega con la respuesta ya
     lista (corregido en 3.12). El bucle del ping no terminaba nunca.
  2. `__aexit__` atrapaba `CancelledError` al esperar al ping, y con eso se tragaba también la
     cancelación de quien cerraba.

  Ahora el bucle sale por una bandera que prende `__aexit__`, y el cierre espera con
  `asyncio.wait`. Fuera de pytest se colgaba en la primera iteración; con el arreglo, 3000
  aperturas y cierres pasan sin cuelgue. La parte B corre sin ping y no estaba expuesta. Lleva
  dos tests deterministas y tres mutantes atrapados.

---

## 2026-10-08 — TwelveData: con `start_date`, el fondo de la paginación llega como 400

**Fuente:** brief del Admin del 07-oct-2026 (6). Run `37721686439` de *SPEL velas intradía del
oro* (job `113130550645`, commit `056055e`, 08-oct-2026 03:13–03:29 UTC). Su log se leyó entero.

### El hecho

- **TwelveData no escribió nada.**
  - 101 llamadas; 497.348 velas bajadas en memoria.
  - La primera página (`end_date` 2026-10-08 03:13:44) trajo 5000 filas: 4999 cerradas y una abierta.
  - La página con `end_date` 2020-03-26 13:49 trajo **2.349** filas (sha256
    `ea7c316d65bb91de78a6585177c3c294703a8c3993fc39e37dd350ea493b6b19`). Son menos que el
    `outputsize`: llegó al inicio de la historia.
  - La siguiente (`end_date` 2020-03-16 01:09) volvió con `code` 400 y el mensaje literal
    "No data is available on the specified dates. Try setting different start/end dates.".
  - `fetch_pagina` solo reconocía como vacío el 404 "data not found", así que el corte quedó en
    "error" con `completo=false`, y la regla anti-hueco no escribió nada.
  - Cabeceras de créditos de la última respuesta: `api-credits-used` 2, `api-credits-left` 6,
    `api-credits-request` 1.
- **Deriv sí escribió.**
  - 69.462 velas M5 de frxXAUUSD, del 2025-10-08 03:30 al 2026-10-08 03:20 UTC: 108 páginas, corte
    "la página no retrocedió", sha256 del archivo
    `4302c77183ea6c1c59e808ab4187ba47105d52a9db242fe8410b18be4465e9cc`.
  - Horario de `trading_times` del 2026-10-08 (sha256
    `1bda35267b35f88efee9a286a1320b1265c6a026763c0631b0f4580d327a4242`): lunes a viernes, tramos
    00:00–21:00 y 22:00–23:59:59 tal como los devuelve (el esquema no declara la zona), y un
    evento que dice que el viernes cierra a las 20:55. La serie no tiene velas en domingo
    (medido sobre el archivo de la rama `data`, mismo sha256), consistente con ese horario.
- **Concordancia:** "sin datos", porque no hubo velas de TwelveData.

### Un error mío

El 400 ya estaba registrado. La sonda §0.A-3b lo vio al paginar con `start_date` y `end_date`
(5b, entrada del 2026-10-06, g): "La última página de cada serie […] devuelve 400 'No data is
available on the specified dates', no 404". Al escribir `fetch_pagina` porté esa paginación,
pero tomé la señal de fin del 404 "Data not found", que es como termina la paginación SIN
`start_date` (entrada del 2026-10-05, f).

### La regla

1. **Adapter.** Un 400 es página vacía SOLO si el mensaje empieza exactamente con "No data is
   available on the specified dates" (`TWELVEDATA_SIN_DATOS_400`, en el registro de constantes).
   Cualquier otro 400 sigue siendo error. El 404 "data not found" no cambia. La página vacía
   guarda el `code` y el mensaje literal.
2. **Ingesta.** Una página vacía cierra la descarga de TwelveData como completa solo si se cumple
   una de dos:
   - la página anterior trajo menos filas que el `outputsize`;
   - el `end_date` pedido ya es <= `desde`.

   Si no se cumple ninguna, es error y no se escribe nada.
3. **Decisión del Admin del 08-oct:** una página vacía en el PRIMER pedido, que no tiene página
   anterior (por ejemplo, una corrida incremental sin velas nuevas), cierra como completa sin
   velas. El test `test_td_sin_nada_nuevo_es_completa_y_no_escribe_nada` sigue igual.

### Interpretaciones

- **Las filas de la página anterior son las que devolvió la API, la vela abierta incluida.** La
  primera página del run trajo 5000 filas y 4999 cerradas. Contando solo las cerradas, habría
  pasado por corta, y una vacía espuria justo después habría dado la descarga por completa con
  solo esas velas.
- **La fixture del 400 tiene el `code` y el mensaje literal del log.** El adapter los escribe
  como "error 400: <mensaje>". El cuerpo JSON entero no quedó en el log, y el status HTTP
  tampoco. Por eso se prueba con HTTP 400 y 200, y con y sin la clave `status`.

### Además, fuera del brief

Una página que trae velas pero ninguna nueva cerraba la descarga como completa ("sin velas
nuevas"). Con `start_date` y `end_date` = primera vela anterior − 60 s, una página correcta
nunca repite velas: si las repite, la API ignoró el `end_date`, y darla por completa podría dejar
el mismo hueco. Ahora es error desde la segunda página. Una primera página que solo trae la vela
abierta sigue cerrando como completa sin velas. Va en un commit aparte.

### Mutantes

17, todos atrapados: 7 del adapter, 8 de la regla de la ingesta y 2 del caso de la ventana
repetida.

### Pendiente

Volver a correr el workflow (lo dispara el Admin). TwelveData vuelve a empezar desde 2020, unas
101 llamadas. Deriv sigue de forma incremental desde la última vela guardada.

## 2026-10-14 — Tarea D de la tanda del 14-oct: runners en `ubuntu-24.04` y acciones en Node 24

**Contexto:** brief del Admin del 14-oct-2026 (9), tarea D.

### Los hechos, verificados

- **Las anotaciones de los runs 38413375042 (GDELT, 13-oct) y 38628997322 (tests, 14-oct)**
  traen dos avisos:
  - "The ubuntu-latest label will migrate to Ubuntu 26 beginning October 19, 2026. For more
    information, see https://github.com/actions/runner-images/issues/14748".
  - "Node.js 20 is deprecated. The following actions target Node.js 20 but are being forced to
    run on Node.js 24: actions/checkout@v4, actions/setup-python@v5".
- **El log del job `orden_demo`** (run 37716067033, 08-oct) dice `Image: ubuntu-24.04`, versión
  20261004.327.1, para un job con `runs-on: ubuntu-latest`. El runner es la versión 2.337.0.

### Las versiones, verificadas en los repositorios oficiales

Leídas con `git` sobre github.com/actions/checkout y github.com/actions/setup-python, el
14-oct-2026. La API de releases no está habilitada en esta sesión, así que la fecha es la del
commit de cada tag.

| acción | versión | `runs.using` en su action.yml | tag (fecha del commit) | nota del cambio |
|---|---|---|---|---|
| checkout | v4 (v4.3.1) | node20 | 13-nov-2025 | — |
| checkout | **v5.0.0** | **node24** | 11-ago-2025 | CHANGELOG: "Update actions checkout to use node 24" (PR #2226); README: "Checkout v5 now supports Node.js 24" |
| checkout | v6.0.0 | node24 | 20-nov-2025 | CHANGELOG: "Persist creds to a separate file" (PR #2286) |
| checkout | v7.0.0 | node24 | 17-jun-2026 | CHANGELOG: módulo ESM, bloquea checkout de forks en `pull_request_target` |
| setup-python | v5 (v5.6.0) | node20 | 23-abr-2025 | — |
| setup-python | **v6.0.0** | **node24** | 03-sep-2025 | commit del tag: "Upgrade to node 24 (#1164)" |
| setup-python | v7.0.0 | node24 | 19-jul-2026 | — |

### El cambio

- `runs-on: ubuntu-24.04` en los 10 jobs de los 7 workflows.
- `actions/checkout@v4` → `@v5` (15 usos) y `actions/setup-python@v5` → `@v6` (10 usos).
- El diff de `.github/workflows/` son solo esas 35 líneas: sin cambios en triggers, crons,
  secretos ni pasos.
- `tests/test_workflows_runners.py` (23 tests) falla en tres casos:
  - si un workflow usa `ubuntu-latest` o un job corre en otro runner;
  - si checkout o setup-python vuelven a una mayor en Node 20, o se fijan por algo que no es `vN`;
  - si aparece otra acción, cuyo Node habría que mirar.

### Interpretación: "la versión mayor que soporte Node 24"

Se lee como la **primera** versión mayor con Node 24: checkout v5 y setup-python v6. Las últimas
(v7 y v7) también corren en Node 24, pero traen más cambios.
- **checkout v6** guarda las credenciales en un archivo aparte, y cuatro workflows hacen push a
  `data` con las credenciales que deja el checkout.
- **setup-python v6.0.0** suma, entre v5.6.0 y v6.0.0, la entrada `pip-version` y lecturas
  nuevas de `.python-version` y Pipfile. Ninguna aplica: todos los pasos fijan
  `python-version: "3.12"`, y el repo no tiene esos archivos.

Subir a v7 es otro PR, si el Admin lo quiere.

### No verificado

- El contenido del issue actions/runner-images#14748, más allá del texto del aviso.
- Que todo corra igual en las versiones nuevas: ningún workflow corrió todavía en esta rama, y
  la tanda prohíbe dispararlos. El primer run de `SPEL Tests` sobre el PR lo va a mostrar.

### Mutantes

7, todos atrapados:
- un job de vuelta en `ubuntu-latest`;
- otro en `ubuntu-22.04`;
- `runs-on` como lista;
- checkout@v4 en un solo job de `gdelt.yml`;
- setup-python@v5;
- checkout fijado por sha;
- una acción nueva.

## 2026-10-14 — Tarea A de la tanda del 14-oct: `test_el_ping_es_un_parametro` era intermitente por el test, no por `conexion.py`

**Contexto:** brief del Admin del 14-oct-2026 (9), tarea A. La corrida 37729552559 de
`SPEL Tests` (push a `main`, `6c1d29c`) falló en el intento 1 con `1 failed, 1675 passed`: en
`tests/test_integracion_demo_ejecucion.py:314`, `assert len(pings) >= 2` dio 1. El intento 2
pasó con 1676. El test pedía al menos 2 pings en 80 ms reales con `intervalo_ping_s=0.01`.

### Diagnóstico (medido, Python 3.11 local; CI corre 3.12)

- **El período real del ping es el intervalo más lo que tarda el pedido.** `_latir` duerme
  `intervalo_ping_s` y después hace `pedir({"ping": 1})`, que termina con `al_recibir`. En los
  tests `al_recibir` es `Registro.guardar_crudo`, que hace `fsync` dentro del bucle de eventos
  (`integracion_demo/registro.py`, `_agregar_linea`). 30 corridas del test original:
  - `fsync` p50 0,39 ms, p99 3,57 ms; pedido completo p50 0,73 ms, p99 3,95 ms;
  - período entre pings p50 10,97 ms, p99 22,36 ms; entre 4 y 7 pings en 80 ms, nunca menos de 2.
- **Con un `fsync` más lento, el fallo se reproduce.** Sumando una espera al `fsync`: con 20 ms
  llegan 3 pings; con 40 ms, 2; con 60 ms, 1 en las 30 de 30 corridas.
- **El `continue` de `_latir` no interviene:** en este test nadie más pide, y el lock estuvo libre
  en las 210 revisiones de 30 corridas.
- **El `_Falso` responde sin esperar nada:** `send` encola la respuesta y `recv` la saca.
- **En la corrida de CI** el test entero duró 138 ms (timestamps del log: 04:52:47.7656 →
  04:52:47.9038), consistente con las dos ventanas de 80 y 50 ms del test más poco más. El
  bucle del test no estuvo trabado; lo que no alcanzó fue el segundo ping. El `fsync` del runner
  no está medido: la causa en CI es una inferencia, no una medición.

**El defecto está en el test**, que suponía que un ping cuesta mucho menos que 10 ms en cualquier
disco. `conexion.py` hace lo que dice su docstring: guarda cada respuesta cruda antes de
parsearla y duerme el intervalo entre pings. No se tocó.

**Observación, sin cambio:** como el `fsync` corre dentro del bucle de eventos, en vivo el
intervalo efectivo del ping es el pedido más el `fsync` del disco del runner. Para un ping de
mantenimiento no cambia nada mientras el intervalo quede lejos del cierre por inactividad.

### El cambio

- `test_el_ping_es_un_parametro` espera hasta 2 pings con un tope de 2 s, en vez de dormir 80 ms.
  El criterio es el mismo: con `intervalo_ping_s=0.01` llegan al menos 2 pings. La mitad "sin
  intervalo no hay ping" quedó igual.
- Test nuevo, `test_el_ping_llega_aunque_guardar_el_crudo_tarde`: con un `fsync` de 100 ms, en
  80 ms llega un solo ping (el criterio viejo fallaba ahí) y esperando llegan 2.

### Mutantes (sobre `conexion.py`, los cuatro atrapados)

No latir nunca, de dos formas (sin crear la tarea y con el bucle vacío); latir una sola vez;
latir aunque no haya intervalo.

## 2026-10-14 — Tarea C de la tanda del 14-oct: reintentos con Retry-After en la ingesta (issue #39)

**Contexto:** brief del Admin del 14-oct-2026 (9), tarea C, e issue #39 (08-oct). Un 429 o un
5xx transitorio de TwelveData cortaba la descarga de velas en "error" a la primera, sin escribir
nada (la regla anti-hueco del PR #38).

### Auditoría previa (lo que ya existía)

- **`TwelveDataAdapter._raise_if_error`** traduce el `code` del cuerpo: 401 a
  `AdapterAuthError`, 429 a `AdapterConnectionError`, 404 a `AdapterDataError` y cualquier otro a
  `AdapterConnectionError`. No mira el status HTTP. Un 5xx con cuerpo HTML salía como
  `AdapterDataError` ("no es JSON").
- **`fetch_pagina`** no reintentaba. **`AdapterChain._fetch_con_reintentos_async`** reintenta
  tres veces, con 2 y 4 s, cualquier `AdapterException` salvo la de autenticación, pero solo
  envuelve `fetch_ohlcv`, no `fetch_pagina`.
- **`Limitador`** (`ingestion/limitador.py`) cuenta créditos y aplica el tope por minuto, y
  `descargar_td` le pide turno antes de cada página.

### La regla

1. **Se reintenta:** HTTP 429 o 500/502/503/504, sea el status HTTP o el `code` del cuerpo
   (TwelveData manda errores con HTTP 200); timeouts y cortes de red (`httpx.TimeoutException`,
   `NetworkError`, `RemoteProtocolError`).
2. **No se reintenta nada más.** El 400 "No data is available…" sigue siendo la página vacía del
   PR #38, sin reintento. Tampoco el 401, el 403, el 404, el 501 ni el 505, ni un error de red
   que no es transitorio (`UnsupportedProtocol`, `LocalProtocolError`).
3. **Espera:**
   - la del `Retry-After`, en segundos o como fecha HTTP contra la cabecera `Date` del servidor
     (contra el reloj de pared solo si falta `Date`);
   - si no viene, 60 s para un 429 (issue #39) y backoff de 2, 4, 8… s con tope de 30 s para lo
     demás.
4. **Topes:** 3 reintentos y 180 s de espera sumada. Una espera que pasaría el tope no se hace:
   se lanza en el acto.
5. **Créditos:** cada reintento pide turno al mismo `Limitador` que cuenta las páginas, así que
   consume crédito y respeta el tope por minuto. Si el limitador no da turno, se lanza.
6. **Agotados los reintentos**, sale un `AdapterConnectionError` explícito con el motivo, los
   reintentos y la espera sumada. `descargar_td` lo trata como cualquier error: corte "error" y
   nada escrito. `descargar_td` no cambió.
7. **Cada reintento deja una línea de log en JSON** (fuente, número, motivo, espera, origen de la
   espera, espera acumulada), sin la key ni el mensaje del proveedor.

### Interpretaciones

- **Sin limitador, `fetch_pagina` no reintenta.** El brief pide que cada reintento consuma
  crédito en el Limitador; sin limitador no hay dónde contarlo. Así, los tests existentes y
  cualquier otro uso del adapter quedan como estaban. `velas_intradia.ingerir` le pasa al adapter
  el mismo limitador que a la descarga.
- **De los 5xx, solo 500, 502, 503 y 504.** 501 y 505 dicen que el servidor no soporta el pedido.
- **Los valores (3, 180 s, 60 s, 2 s, 30 s) son provisionales**, sin evidencia: el issue deja el
  número de reintentos "a confirmar". Están en el registro de constantes como
  `provisional_sin_evidencia`.

### Sin verificar

- Que TwelveData mande `Retry-After`. No se llamó a la API (regla de la tanda), y su
  documentación no se consultó.
- **Qué 429 manda TwelveData cuando se acaban los créditos del día.** Si es el mismo 429 que el
  del tope por minuto, el reintento gasta hasta 3 créditos y 180 s antes de cortar. Está
  acotado, pero no sirve de nada en ese caso.

### Dos implementaciones del concepto "reintento"

`AdapterChain._fetch_con_reintentos_async` sigue con su reintento lineal propio para
`fetch_ohlcv`. Es otra semántica: reintenta cualquier error de datos y después cae a otra fuente.
No se tocó, porque el issue exige que los tests existentes pasen sin cambiar sus expectativas.
Unificarlo con `ingestion/reintentos.py` queda pendiente de un brief.

### Mutantes

29, todos atrapados:
- el parseo de `Retry-After`;
- cada rama de la espera;
- los dos topes y sus bordes;
- el limitador y el sueño;
- el log;
- el conjunto de status;
- el `code` frente al status HTTP;
- la clasificación de red;
- el camino sin limitador;
- el cableado de la ingesta.

## 2026-10-14 — Tarea B de la tanda del 14-oct: la reconciliación de la parte B, DETENIDA

**Contexto:** brief del Admin del 14-oct-2026 (9), tarea B. La regla de la tanda: si algo del
brief contradice el código, los datos o el decision-log, la tarea se detiene, se reporta en un PR
borrador y se pasa a la siguiente. **No se cambió código.** Este PR solo trae este informe y las
filas de ESTADO.md que estaban mal (punto 4).

### El informe de la parte B (run 37716067033, job `orden_demo`, 08-oct, `056055e`)

Leído del log del job (el test lo imprime como una línea JSON). 1 passed en 124,73 s.

- **Cuentas:** `GET /accounts` devolvió una demo (`DOT********`) y una real (`ROT********`),
  las dos activas. OTP pedido para la demo; 1 OTP y 1 conexión.
- **Orden:** MULTUP sobre frxXAUUSD, stake 1 (el mínimo que dio la cotización de sondeo),
  multiplicador 100, stop loss 0,10 USD. Comisión cotizada 0,02 (float en `proposal`).
  `contract_id` 16063879879, latencia del `buy` 288 ms.
- **Cierre:** `sell` propio a los 62 s (`purchase_time` 1791425094, `sell_time` 1791425156),
  outcome `sell_tiempo`, profit de Deriv −0,03 USD, r_multiple −0,3, `commission_usd` 0,02 igual a
  la modelada (`cost_ratio` 1,0). En el contrato la comisión llegó como string ("0.02").
- **Balance:** 10000,00 → 9999,97 USD.
- **Registro:** íntegro, 6 filas (envío, compra, seguimiento, venta, cierre, reconciliación), cabeza
  `bccd4fab…`.
- **Reconciliación:** `reconciliado: ok`, sin problemas, con la nota "la comisión no aparece en
  profit_table ni en statement: no se reconcilia".
- **Esquema medido sobre todo lo crudo:** `commission` aparece solo en `proposal` y en
  `proposal_open_contract`. Las respuestas crudas de `profit_table` (sha256 `6ee6140f…`) y
  `statement` (`14d631ac…`) no la traen.
- **Cierre por inactividad:** `cierre_inactivo_s` 59,705 s, con tope de 300 s.

### Por qué se detuvo

**1. El punto 2 contradice la DG-3 del 29-sep.** El brief pide que la reconciliación sea limpia
solo si `reconciliado_profit` y `reconciliado_comision` son `ok`, y que una comisión que no se
puede contrastar sea `no_verificable`, nunca `ok`.
- La DG-3 del 29-sep (entrada "DG-3 reemplazada") dice "d) reconciliación demo sin
  discrepancias". `governance/paso_a_real.py` lo implementa así:
  `discrepancias_reconciliacion != 0` incumple d). Una comisión `no_verificable` no es una
  discrepancia: con la DG-3 vigente, d) se cumple; con la regla del brief, no.
- **La consecuencia la fijan los datos:** la comisión no aparece en `profit_table` ni en
  `statement`. Lo dicen los esquemas oficiales (entrada del 06-oct, interpretación 7) y ahora
  también las respuestas crudas de la parte B. Con la regla del brief, TODA operación tendría la
  comisión `no_verificable`, d) no se cumpliría nunca, y toda hipótesis terminaría en
  `VENCIDO_PASA_A_H2` a los 6 meses. Eso cambia el efecto de la decisión del 29-sep, y lo decide
  el Admin.

**2. El punto 1 choca con tests existentes que codifican el comportamiento viejo.** CLAUDE.md:
"Si un test existente codifica el comportamiento viejo, repórtalo; no lo edites."
- `tests/test_integracion_demo_registro.py::test_los_campos_del_brief_estan_todos` fija que las
  columnas son las del brief del 06-oct (3) más `evento` y `evidencia`, y nada más. Dos columnas
  nuevas lo rompen.
- Cuatro tests del mismo archivo esperan `reconciliado == "ok"` en un trade cerrado cuya
  comisión no se contrastó: `test_reconciliar_ok_agrega_una_linea_y_no_edita`,
  `test_reconciliar_tolera_medio_centavo_y_no_mas`, `test_una_discrepancia_se_vuelve_a_reconciliar`
  y `test_reconciliar_no_repite_un_ok`, que además fija que un `ok` no se vuelve a reconciliar.
  Ese es justamente el comportamiento que el brief corrige.
- Un quinto, `test_una_rechazada_con_una_compra_cerca_es_discrepancia`, espera `ok` para una
  orden sin contrato y sin compras cerca. Ahí no hay comisión que contrastar, y el brief no dice
  qué marca lleva: es otra decisión pendiente.

### Lo que el Admin tiene que decidir

- Si d) pasa a exigir comisión `ok`, cómo se contrasta la comisión. Sin un dato de Deriv que la
  separe, d) queda inalcanzable. Si en cambio d) sigue siendo "sin discrepancias", con
  `no_verificable` declarado aparte.
- Autorizar la edición de los tests nombrados arriba, o el esquema de las columnas nuevas.

### Punto 3: 59,7 s frente a ~122 s (solo diagnóstico)

- **59,705 s** es `cierre_inactivo_s`: `ConexionDemo.medir_cierre_inactivo` empieza a contar
  después de la última respuesta pedida (`statement`) y para cuando `recv` falla porque el
  servidor cerró. Es el tiempo **sin ningún mensaje** hasta el cierre. El socket se abre sin el
  ping de protocolo de `websockets` (`ping_interval=None`).
- **122,465 s** es lo que da el `historial` de la conexión en el mismo informe: `conectada` en
  `t_ms` 1791425094011 y `descartada` ("cierre por inactividad") en 1791425216476. Es la **vida
  entera de la conexión**: cotización, compra, 62 s de seguimiento, venta, reconciliación y los
  59,7 s de silencio.
- Las dos cuadran: 1791425216,476 − 59,705 = 1791425156,771, menos de un segundo después del
  `sell_time` (1791425156), que es cuando terminaron la venta y las dos consultas de la
  reconciliación. El `historial` usa el reloj local y `sell_time` el del servidor.
- **No encontré otro registro de ~122 s.** Busqué "122", "inactiv" y `cierre_inactivo` en el
  decision-log, ESTADO.md y el código. Si la cifra del brief viene del `historial`, las dos cifras
  no son dos mediciones del mismo fenómeno: miden cosas distintas.
- **No verificado:**
  - si Deriv cuenta la inactividad desde el último mensaje del cliente o desde el último del
    servidor;
  - si ~60 s es un límite fijo. Es una sola medición, y Deriv no lo documenta.

## 2026-10-16 — Brief 9-R, tarea 3 (DB-1, DB-2): auditoría de la comisión, y por qué se detiene

**Contexto:** brief del Admin 9-R, tarea 3, con las decisiones DB-1 y DB-2 del 14-oct-2026:
- columnas `reconciliado_profit` y `reconciliado_comision`;
- `reconciliado_comision` es `ok` si "la comisión del contrato, la cotizada y la registrada
  coinciden" a medio centavo;
- `no_verificable` si falta alguno de los tres valores;
- `no_verificable` nunca es `ok`.

El punto 1 pide auditar antes dónde quedan hoy las tres. **No se cambió código.**

### Auditoría: dónde está cada comisión hoy

| comisión | dónde la recibe el código | qué guarda el registro |
|---|---|---|
| **del contrato** (`proposal_open_contract.commission`) | `registro.derivados_de_cierre(poc)` | la fila de `cierre`: `commission_cruda` = `str(poc["commission"])` y `commission_usd` = `num(...)` si la moneda es USD |
| **cotizada** (`proposal.commission`) | `Ejecutor.cotizar` devuelve la respuesta entera; nadie la guarda en una fila | **nada**: solo la respuesta cruda en `crudo.jsonl`, sin `trade_uuid` ni enlace a la compra. En la parte B la lee el test live, no `integracion_demo/` |
| **registrada** | — | no hay una tercera fuente. `commission_usd` y `commission_cruda` salen del mismo `poc`. `commission_modelada` es κ·nocional, el modelo, no un valor de Deriv |

`reconciliar.py` solo recibe el registro, `profit_table` y `statement`, y en `profit_table` y
`statement` no viene ninguna comisión (entrada del 14-oct).

### La contradicción

La regla `ok` de DB-2 compara tres valores, y el registro tiene uno:
- **La cotizada no llega a la reconciliación por ningún camino.** Con el código de hoy, toda
  operación real quedaría con la comisión `no_verificable`, y la parte B no podría dar `ok`
  como pide el punto 6.
- **"La registrada" no tiene un referente propio:** comparar `commission_cruda` con
  `commission_usd` compara un valor consigo mismo.

Para cumplir DB-2 hay que decidir un esquema que el brief no fija: dónde se registra la
cotizada y cómo se enlaza con la compra (una o dos cotizaciones antes de cada `buy`; en la
parte B hubo dos). Eso es elegir, y la regla de la tanda dice detenerse y reportar.

### Propuesta para el Admin (no implementada)

1. **`Ejecutor.comprar` recibe la comisión de la cotización que usó** y la escribe en la fila de
   `envío`, en una columna nueva `commission_cotizada` (crudo, como string). Así queda junto al
   `trade_uuid` y antes de la compra.
2. **"Registrada" = `commission_usd`** de la fila de cierre (el valor con que el registro calcula
   costos y `pnl_recalculado`). **"Del contrato" = `commission_cruda`.** Si el Admin entiende
   otra cosa por "registrada" (por ejemplo, `commission_modelada`), la regla cambia: comparar
   contra el modelo mide el costo contra κ, no la reconciliación.
3. Con eso, el resto de DB-2 se implementa tal cual:
   - Decimal para el string del contrato y para el float de la cotización;
   - `no_aplica` sin contrato;
   - `no_verificable` se vuelve a reconciliar;
   - un `ok` no se repite.

Hacerlo exige autorizar, además de los cinco tests nombrados, la columna nueva y el cambio de
firma de `comprar` (y su uso en `tests/test_integracion_demo_orden_live.py`).

### Punto 6

El crudo de la parte B (run 37716067033) **no está** en el repo ni en la rama `data`:
- `git ls-tree` de `data` no tiene nada de la demo;
- `git log -S 16063879879` solo encuentra el decision-log;
- el test live escribe el registro en un directorio temporal del runner, que "se pierde con el
  job" (docstring de `tests/test_integracion_demo_orden_live.py`).

No se reconstruyó. Lo que queda es el informe del log: la cotización trajo 0.02 como float y el
contrato "0.02" como string.

### Lo que sí se hizo en esta rama

- El informe de la parte B y las filas corregidas de ESTADO.md del PR #41, rebasados sobre la
  tarea C.
- El "Próximo paso" de ESTADO.md reescrito con la cola del brief 9-R (tarea 4).
- `governance/paso_a_real.py` no se tocó.

## 2026-10-17 — DB-1 y DB-2 implementadas: la comisión cotizada contra la del contrato (brief 9-R2)

**Contexto:** brief del Admin 9-R2. Aprueba la propuesta del 16-oct, con una corrección: DB-1
compara **dos** comisiones, no tres.
- `commission_usd` sale del mismo contrato que `commission_cruda`.
- `commission_modelada` es el modelo κ, que vigila la condición c), no la d).

### El cambio

- **`Ejecutor.comprar(..., commission_cotizada=None)`**, con el mismo patrón que
  `quote_at_signal`.
  - Guarda el valor en el `Trade` y lo escribe crudo en la fila de `envío`, como `str(valor)`
    sin redondear. El float 0.02 queda "0.02", y 0.1+0.2−0.28 queda "0.020000000000000018".
  - `Registro.agregar` rechaza una `commission_cotizada` que no sea str.
- **Columnas nuevas:** `commission_cotizada`, `reconciliado_profit` y `reconciliado_comision`.
  `SCHEMA_VERSION` pasa de 1 a 2.
- **`Reconciliado` suma `no_verificable`.** `reconciliado_profit` solo acepta ok o
  discrepancia. `ReconciliadoComision` acepta ok, discrepancia, no_verificable y no_aplica.
- **`reconciliar`** compara, en Decimal desde el texto, la cotizada (fila de `envío` del mismo
  `trade_uuid`) con `commission_cruda` (fila de cierre):
  - `ok`: diferencia ≤ 0,005;
  - `discrepancia`: más que eso;
  - `no_verificable`: falta alguna o no es un número finito;
  - `no_aplica`: no hubo contrato y no hay compra cerca.
- **Marca global:** `ok` solo si profit es ok y la comisión es ok o no_aplica; `discrepancia`
  si cualquiera lo es; `no_verificable` en todo lo demás.
- La evidencia de la fila trae la cotizada, la del contrato y la diferencia. Se quitó la nota
  vieja "la comisión no se reconcilia".
- **La parte B** (`tests/test_integracion_demo_orden_live.py`) le pasa a `comprar`
  `commission_cotizada=prop.get("commission")`.
- `governance/paso_a_real.py` y `execution/` no se tocaron.

### Interpretaciones

- **Una orden sin `contract_id` CON una compra cerca en `statement`:** la comisión es
  `no_verificable`, no `no_aplica`. El contrato pudo existir y su comisión no se ve. El global
  ya es discrepancia por el profit.
- **Un `ok` escrito antes de DB-2** (fila de reconciliación sin `reconciliado_comision`) no
  cuenta como ok: el trade se reconcilia de nuevo en una fila nueva, que da `no_verificable`
  porque el envío viejo no trae la cotizada. Las filas viejas no se tocan ni se rellenan.
- **`no_verificable` se vuelve a reconciliar**, como una discrepancia, y un `ok` no se repite.
- **`SCHEMA_VERSION` 2** marca las filas con las columnas nuevas. Las de la versión 1 no las
  tienen, y así se distinguen.

### Tests editados: el assert viejo y el nuevo

Todos en `tests/test_integracion_demo_registro.py`.

| test | assert viejo | assert nuevo |
|---|---|---|
| `test_los_campos_del_brief_estan_todos` | `set(CAMPOS) - set(brief) == {"evento", "evidencia"}` | `== {"evento", "evidencia", "commission_cotizada", "reconciliado_profit", "reconciliado_comision"}` |
| `test_reconciliar_ok_agrega_una_linea_y_no_edita` | `[n["reconciliado"] for n in nuevas] == ["ok"]` | `[(reconciliado, reconciliado_profit, reconciliado_comision) …] == [("ok", "ok", "ok")]` |
| `test_reconciliar_tolera_medio_centavo_y_no_mas` | `…["reconciliado"] == "ok"` y `== "discrepancia"` | `(reconciliado, reconciliado_profit) == ("ok", "ok")` y `== ("discrepancia", "discrepancia")` |
| `test_una_discrepancia_se_vuelve_a_reconciliar` | `marcas == ["discrepancia", "ok"]` | `marcas == [("discrepancia", "discrepancia", "ok"), ("ok", "ok", "ok")]` (global, profit, comisión) |
| `test_una_rechazada_con_una_compra_cerca_es_discrepancia` | `reconciliar(r2, …)[0]["reconciliado"] == "ok"` | `(reconciliado, reconciliado_profit, reconciliado_comision) == ("ok", "ok", "no_aplica")`; y para la compra cerca, se agregó `(profit, comisión) == ("discrepancia", "no_verificable")` |

**Además, un cambio fuera de los cinco, que se reporta:** el fixture `_cerrado` del mismo archivo.
- Desde ahora arma la fila de `envío` con `commission_cotizada="0.02"` y el cierre con
  `commission_cruda="0.02"` (parametrizables).
- Sin eso, ningún trade cerrado llega a `ok`, y `test_reconciliar_no_repite_un_ok` (que el brief
  9-R mantiene) fallaría.
- No se cambió ningún assert fuera de los cinco tests.

### Tests nuevos (criterio viejo contra nuevo)

- **`tests/test_integracion_demo_registro.py`:**
  - str contra float;
  - un float largo comparado por su texto;
  - la frontera de medio centavo (0,015, 0,025, 0,0149, 0,0251), y que en float sería
    discrepancia;
  - los cuatro valores;
  - la marca global en las 8 combinaciones;
  - `no_verificable` que se vuelve a reconciliar;
  - el envío del mismo trade;
  - un registro de la versión 1 sin la columna, y su `ok` viejo;
  - las marcas fuera de su conjunto.
- **`tests/test_integracion_demo_ejecucion.py`:** `comprar` guarda la cotizada cruda solo en el
  envío, y la operación entera reconcilia la comisión.
- **`tests/test_integracion_demo_orden_live.py`:** la parte B, contra el falso, reconcilia la
  comisión en `ok` (el profit da discrepancia porque el falso no trae nada en `profit_table`).

### Mutantes

22 en total, 21 atrapados:
- frontera `<`;
- Decimal desde el float binario y resta en float;
- falta uno es ok;
- NaN aceptado;
- compra cerca como no_aplica y no_aplica como no_verificable;
- tres de la marca global;
- un ok viejo que cuenta y no_verificable que no se repite;
- envío de otro trade;
- la columna sin escribir;
- los tres controles del registro;
- la versión;
- `comprar` que no escribe o redondea;
- la parte B que no la pasa.

**El que sobrevive es equivalente.** Quitar `profit == ok` de la regla de ok no cambia nada,
porque la discrepancia se descarta antes y el profit solo puede valer ok o discrepancia. La
condición queda explícita, tal como la escribe DB-2.

### Punto 6 de 9-R

Sigue sin crudo de la parte B en el repo ni en `data`, y no se reconstruyó. El test contra el
falso fija que la parte B escribe y compara la cotizada.

## 2026-10-17 — Briefs 10 y 10-B: las decisiones del 14 y del 17-oct, una entrada cada una

**Fuente que pedía el brief 10:** `claude/SPEL_memo_traspaso_2026-10-14.md` §2 y
`claude/SPEL_plan_y_briefs_2026-10-14.md` §2, con D1–D9 (respondidas por el Admin el 14-oct) y
D11, D11-u, DB-1 y DB-2 (delegadas en el auditor).

**Esos dos documentos no estaban al alcance, y siguen sin estarlo.** Lo verifiqué así:
- no están en el repo ni en ninguna rama (`git ls-tree` de todas las ramas remotas);
- no están en el sistema de archivos;
- la búsqueda en Google Drive no los encuentra.

El brief 10 registró solo lo que traía su propio texto: D2, D3, D11 y D11-u, con la razón
pendiente. **El brief 10-B (17-oct) trae el contenido y la razón de las demás**, completa esas
razones y suma D12 (17-oct), DB-3 (del auditor bajo delegación, 17-oct) y el criterio del Admin
para decidir en su ausencia.

| decisión | entrada |
|---|---|
| criterio del Admin para decidir en su ausencia | la siguiente |
| D1 | historia larga diaria desde TwelveData |
| D2 | DG-3 cambia en a) y en d) |
| D3 | el registro único de ensayos |
| D4 | H-ORB-oro exploratoria: **contradice el decision-log del 06-oct, sin resolver** |
| D5 | H1 v2 |
| D6 | H2 (#34) |
| D7 | H3 (#33) |
| D8 | los crons de velas y sonda, apagados |
| D9 | las Fases 3, 5 y 6, congeladas |
| D11, D11-u | H-ORB-oro pausada, y cuándo se reabre |
| D12 | H1 sin alternativa declarada |
| DB-1, DB-2 | ya registradas: entrada del 2026-10-17, "DB-1 y DB-2 implementadas". Se citan, no se repiten |
| DB-3 | un trade terminado sin reconciliar incumple d) |

**D10 no aparece** ni en el brief 10 ni en el 10-B. Tampoco en el repo: no hay "D10" en los
mensajes de commit ni en ningún `.md`, `.py` o `.json` de ninguna rama, salvo un handoff legacy
de las ramas `archive/*` que no tiene relación. Si existe, su entrada falta.

## 2026-10-17 — El criterio del Admin para decidir en su ausencia (14-oct)

**Fuente:** brief del Admin 10-B, punto 1, que pide registrarlo.

Lo que se decide sin el Admin se decide con esto:
- rigor estadístico;
- pre-registro antes de mirar datos;
- ningún criterio se relaja después de ver un resultado;
- no sobre-parametrizar;
- edge demostrado antes que infraestructura;
- cero ruido operativo.

**Las decisiones irreversibles o de capital siguen siendo del Admin.**

En este registro, las decisiones del auditor bajo delegación son D11, D11-u, DB-1, DB-2 y DB-3.

## 2026-10-17 — D1 (14-oct): historia larga diaria de BTC y del oro desde TwelveData

**Fuente:** brief del Admin 10-B, punto 1.

**Decisión:**
- la historia larga diaria de BTC y del oro sale de TwelveData;
- se valida contra el año de Deriv, con una coincidencia de al menos el 90 %;
- antes, una sonda que mida solo profundidad.

**Razón del Admin:** sin 3–5 años de histórico no se valida nada, y la fuente externa tiene que
ser un proxy fiel del precio ejecutable.

**Contra lo que ya está en el registro:**
- **Deriv da un año.** Su historia diaria es de 365 días, con `end` ignorado, y las velas
  intradía tienen el mismo tope (entradas del 2026-10-05, sonda §0.A-2 y sonda §0.A-3 v2, e).
  Con eso BTC no llega a ningún umbral de H1 ni de H3: 365 < 1.058, el menor.
- **La profundidad diaria de TwelveData ya tiene una medición**, del 05-oct, en el plan Basic
  (sonda §0.A-3 v2, f): BTC/USD desde el 2017-08-28, 3.326 filas; XAU/USD desde el 1979-12-26,
  12.288 filas. Si alcanza o hace falta la sonda nueva, lo decide el Admin. La cola de
  `ESTADO.md` tiene la sonda de profundidad diaria (brief 8) en el segundo lugar.

**Dos cosas que D1 no fija, y que se fijan antes de mirar:**
- **La métrica de la coincidencia.** El 90 % de la concordancia del oro M5 mide otra cosa: la
  dirección y la vela de la primera ruptura del rango de apertura.
- **Dónde corta el día cada fuente.** Las velas diarias de Deriv caen en múltiplos de 86.400 s,
  a las 00:00 UTC (sonda §0.A-2). La barra 1d de TwelveData es de solo fecha y va sin
  `timezone=UTC` (decisión del Admin del 06-oct): su fecha es la convención del día de mercado.
  Qué ventana cubre esa barra no está medido.

## 2026-10-17 — D2 (14-oct): DG-3 cambia en a) y en d), y nombra la latencia

**Fuente:** brief del Admin 10, punto 4; la razón, del brief 10-B.

**Razón del Admin:** la demo valida infraestructura, fricciones, latencia y reconciliación. 30 días
de demo exigirían un Sharpe ≈ 4,5.

**El 4,5, verificado** con `core/estadistica_sharpe.py`. En SR₀ = 0 la varianza del Sharpe no
depende de la asimetría ni de la curtosis, y con ρ = 0 vale 1/T. Con T = 30 retornos diarios,
PSR(0) ≥ 0,90 pide un Sharpe diario ≥ 0,2340: anualizado con √365 da **4,47**, y con √252, 3,71.
[INTERPRETACIÓN] Va √365 porque los días de demo son de calendario (`paso_a_real.py`). Para
PSR(0) ≥ 0,95 serían 0,3003 diario, 5,74 y 4,77.

**Para el Admin: un roce con el 25-sep.** La razón dice que la demo valida fricciones. La entrada
del 2026-09-25 "La demo valida integración, no ejecución" dice que una cuenta demo "no es evidencia
suficiente de deslizamiento ni de rechazos". c) de DG-3 ya compara los costos de la demo con los
modelados desde el 29-sep, así que D2 no cambia ningún criterio. Si lo del 25-sep sigue en pie
para el deslizamiento, lo dice el Admin.

**Qué cambia respecto de la DG-3 del 29-sep:**
- **a)** PSR ≥ 0,90 y DSR ≥ 0,90 se miden **solo sobre el backtest histórico**. Deja de ser la
  serie combinada histórico + demo. El N es el del registro de ensayos (D3).
- **b) y c)** no cambian.
- **d)** exige **0 discrepancias y 0 no_verificables** (DB-1). Los dos se cuentan desde la marca
  global `reconciliado` del registro demo.
- **La latencia:** D2 la nombra, y no tiene umbral medido. No se inventó una constante: queda
  pendiente sin número.

**Auditoría previa de d).** `discrepancias_reconciliacion` llegaba a `paso_a_real.py` solo como
un campo de `EvidenciaDemo`, que arma quien llama. En el repo no lo arma ningún código, solo los
tests, y ninguna función lo contaba desde el registro.

**Implementación** (`governance/paso_a_real.py` e `integracion_demo/reconciliar.py`):
- `EvidenciaDemo.psr` y `.dsr` pasan a llamarse `psr_historico` y `dsr_historico`. Un llamador
  que todavía piense en la serie combinada falla con TypeError en vez de pasarla en silencio.
- `EvidenciaDemo.no_verificables_reconciliacion` es el campo nuevo, y llega por el mismo camino
  que las discrepancias. d) se incumple si cualquiera de los dos es distinto de 0.
- `reconciliar.conteo_de_marcas(directorio)` cuenta, por trade terminado, la marca global de su
  **última** fila de reconciliación: ok, discrepancia, no_verificable y sin_reconciliar. Un trade
  abierto no cuenta.
- `Evaluacion.sin_evaluar = ("latencia",)` en cada veredicto: un APTO no cubre la latencia.
- `governance/PRINCIPLES.md` #6, `CLAUDE.md` y `BLUEPRINT.md` dicen la DG-3 nueva, y
  `tests/test_paso_a_real.py` verifica que ninguno diga "histórico + demo".

**Tests de paso_a_real editados (autorizados por el brief).** No cambió ningún `assert`.
Cambiaron las entradas, porque los campos se renombraron, y se sumaron casos:

| línea vieja | línea nueva |
|---|---|
| `psr=PSR_MINIMO, dsr=DSR_MINIMO, inicio_demo=INICIO,` (`_BORDE`) | `psr_historico=PSR_MINIMO, dsr_historico=DSR_MINIMO, inicio_demo=INICIO,` |
| `discrepancias_reconciliacion=0,` (`_BORDE`) | `discrepancias_reconciliacion=0, no_verificables_reconciliacion=0,` |
| `({"psr": 0.8999}, "a")` y `({"dsr": 0.8999}, "a")` | `({"psr_historico": 0.8999}, "a")` y `({"dsr_historico": 0.8999}, "a")` |
| — | `({"no_verificables_reconciliacion": 1}, "d")` (caso nuevo) |
| `replace(_BORDE, psr=0.5, …)` (tres tests) | `replace(_BORDE, psr_historico=0.5, …)` |
| — | `{"no_verificables_reconciliacion": -1}` (caso nuevo de evidencia imposible) |

**Lo que D2 no decía:** un trade cerrado que todavía no tiene ninguna fila de reconciliación
(`sin_reconciliar`). El brief 10 contaba discrepancias y no_verificables, y nada más, así que esos
trades quedaron fuera de d) y `conteo_de_marcas` los informaba aparte. **Lo decide DB-3**
(última entrada): incumplen d).

## 2026-10-17 — D3 (14-oct): el registro único de ensayos

**Fuente:** brief del Admin 10, punto 3; la razón, del brief 10-B.

**Razón del Admin:** el sesgo de pruebas múltiples. Ocultar intentos falsea el DSR (el N entra en
SR*: `core/estadistica_sharpe.py::sr_estrella`).

**Implementación:** `governance/ensayos.py` y `governance/ensayos.jsonl`.
- **Formato:** append-only, una línea JSON por ensayo, con id, hipótesis, fecha, sha256 del
  pre-registro, activo, estado, cuenta_en_n, fuente y nota.
- **Siembra:**
  - `previo-mascara-godel` (decision-log 2026-09-04);
  - `previo-proxy-transfer-entropy` y `previo-ema-20-63` (decision-log 2026-09-06, acta de
    `core/price_signals.py`).

  Los tres son fallidos, con hipótesis "previa a la Serie H", sin pre-registro, y cuentan en el
  N. A esos se suma `concordancia-orb-oro`, con `cuenta_en_n: false`: no es un ensayo de
  trading, y su nota lo dice.
- **`ENSAYOS_PREVIOS_FALLIDOS`** (`core/preregistro_h1.py`) pasa a ser
  `previos_fallidos()`, derivado del registro. En `config/constantes.json` pasa de `parametro`
  con valor 3 a `derivada`. `tests/test_ensayos.py` exige que valga 3 y que no vuelva a ser un
  literal. **Es el único cambio en `core/preregistro_*`.** `research/preregistro_h1.md` no se
  tocó, y su test de sha256 sigue igual.

**Interpretaciones:**
- "Activo" es el activo medido. Los tres previos se midieron sobre BTC y XAU, y así lo dice el
  registro. El pre-registro de H1 los cuenta como "ensayos previos sobre BTC".
- "Previos" son los de hipótesis "previa a la Serie H". Un fallido futuro de H2 o H3 no cambia el
  N sellado de H1.
- Lo sembrado queda fijado por sha256 (`f1da9722…`). Lo que se agregue va después.

## 2026-10-17 — D4 (14-oct): H-ORB-oro exploratoria, y una contradicción con el 06-oct

**Fuente:** brief del Admin 10-B, punto 1.

**Decisión:**
- H-ORB-oro es exploratoria, con un solo rango de 15 min;
- su barra: retorno neto ≥ 2 × las fricciones totales en Deriv, que D4 define como spread +
  slippage + swap.

**Razón del Admin:** no sobre-parametrizar, y una barra de filtrado robusta.

**CONTRADICCIÓN, sin resolver.** Las fricciones de D4 no son las que el registro ya tiene para
los multiplicadores de Deriv:
- **Falta la comisión.** La decisión a) del Admin del 06-oct (entrada "`integracion_demo/`")
  dice: "los multiplicadores de Deriv cobran una comisión única, no taker/maker ni funding". Esa
  comisión está medida: es proporcional al nocional (entrada del 2026-10-05, sonda §0.A-3 v2, c),
  y κ del oro vale 0,0002 en `ingestion/kappa_deriv.py` (entrada del 2026-10-06, d). D4 no la
  nombra.
- **Sobra el swap**, si es financiamiento: la misma decisión dice que no hay funding.
  [INTERPRETACIÓN] Que el swap de D4 sea el funding del 06-oct es una lectura.
- **El spread y el slippage no tienen medición** para los multiplicadores en el registro, y del
  deslizamiento la entrada del 2026-09-25 dice que una demo no es evidencia suficiente.

No se elige entre D4 y el 06-oct: lo decide el Admin. Nada de este PR implementa D4. H-ORB-oro
está pausada (D11), y sus fricciones se fijan en su pre-registro, antes de mirar, cuando se
reabra.

**Lo que sí coincide:** el rango de 15 min es el de la concordancia, 3 velas M5 desde las 08:00
de Europe/London (`tools/concordancia_xauusd.py`, `VELAS_DEL_RANGO = 3`). D4 no dice la hora de
anclaje.

## 2026-10-17 — D5 (14-oct): H1 v2, con los resultados intermedios no concluyentes

**Fuente:** brief del Admin 10-B, punto 1.

**Decisión**, para H1 v2:
- los resultados intermedios son **no concluyentes**;
- la varianza del Sharpe es la de la fórmula generalizada.

**Razón del Admin:** que lo dudoso no pase a demo, y solidez frente a retornos no normales.

**Contra el código y los documentos:**
- La fórmula generalizada ya está en `core/estadistica_sharpe.py`, con la σ evaluada en SR₀
  (entrada "Brief 10, puntos 2 y 5").
- H1 v1 sigue sellada con la fórmula de 2012 (`research/preregistro_h1.md` §9, fijada por
  sha256). D5 habla de v2, y v1 no se toca.
- "Intermedio" contesta, para v2, el segundo [PENDIENTE DEL ADMIN] de §10 de v1: qué pasa con
  0,3 ≤ Sharpe < 0,5, o con Sharpe ≥ 0,5 y PSR o DSR por debajo de 0,90.
  [INTERPRETACIÓN] Intermedio es lo que ni avanza (las tres compuertas de §10) ni se rechaza
  (Sharpe < 0,3, D12). El pre-registro de v2 lo escribe antes de mirar.

## 2026-10-17 — D6 (14-oct): H2 (#34), con dos rezagos y tres horizontes

**Fuente:** brief del Admin 10-B, punto 1.

**Decisión**, para H2 (PR #34):
- ventanas 1/7/30;
- la entropía de hace 2 días como primaria, y la de hace 1 día como secundaria;
- horizontes 1, 5 y 22.

**Razón del Admin:** BTC opera 24/7.

**Contra el PR #34** (sin fusionar; `feature/preregistro-h2` en `3dae9c9`):
- **Ventanas:** coinciden en BTC. Su sección 4 usa 1/7/30 en el calendario continuo (BTC) y
  1/5/22 en el hábil (XAU). D6 no nombra a XAU.
- **Rezago de 2 días, primario:** coincide. La sección 3 une la entropía de `t − 2` días de
  calendario, por la decisión del Admin del 29-sep.
- **Rezago de 1 día, secundario: no está, y el #34 lo descarta.** Su sección 3 dice que la
  entropía de `d` se usa "solo para retornos de días ≥ d + 2" y que "la lectura de rezago 1 queda
  descartada": el archivo GDELT de `t − 1` sale durante el día `t`, no a su comienzo
  (`DIAS_DE_RETRASO_DE_PUBLICACION = 1` en `ingestion/run_gdelt.py`). Esa decisión del 29-sep
  está escrita solo en el #34, no en el decision-log de `main`. La entrada del 2026-09-25 ("El
  1,246× en BTC no es operativo todavía") pide justamente la prueba con `t − 1`.
- **Horizontes 5 y 22: no están.** El #34 pronostica solo la varianza de `r_t`, a un día.

El #34 se ajusta antes de fusionarse, y no es parte de este PR. Lo que ese ajuste tiene que
fijar, y D6 no dice:
- con dos rezagos y tres horizontes hay seis pruebas por activo: cuál es la primaria y cómo se
  corrige la multiplicidad;
- en qué unidad van los horizontes en BTC, donde la semana tiene 7 barras y no 5;
- que la lectura de rezago 1 usa una entropía que al abrir el día `t` todavía no se publicó:
  sirve como prueba, no como señal operativa.

## 2026-10-17 — D7 (14-oct): H3 (#33) declara las velas del oro que ya están en `data`

**Fuente:** brief del Admin 10-B, punto 1.

**Decisión:** antes de fusionarse, el pre-registro de H3 (PR #33) declara las velas del oro que
ya están en la rama `data`.

**Razón del Admin:** inmunidad al sesgo retrospectivo, y trazabilidad.

**Por qué hace falta.** El #33 (`feature/preregistro-h3` en `4eadf83`) dice que "este documento
tiene que existir antes de que haya un precio que mirar" y que "para escribirlo no se leyó
ninguna vela ni ningún retorno". Hoy hay precios del oro en `data`, y se calcularon cosas con
ellos.

**Lo que hay** (medido en `data@69a0e45`, el 17-oct; los cuatro archivos no cambian desde
`bc2597a`, el 14-oct):

| archivo | contenido |
|---|---|
| `metrics/velas/frxXAUUSD/300.jsonl` | M5 de Deriv: 70.718 velas, del 2025-10-08 03:30 al 2026-10-14 18:00 UTC |
| `metrics/velas/frxXAUUSD/revisiones_300.jsonl` | las revisiones de esas velas |
| `metrics/velas/td_XAUUSD/300.jsonl` | M5 de TwelveData: 499.252 velas, del 2020-03-16 01:10 al 2026-10-14 17:45 UTC |
| `metrics/calendario/frxXAUUSD/trading_times.jsonl` | el calendario de Deriv |

Entraron con dos corridas de `velas_intradia.yml`, las dos manuales: la `37721686439` del 08-oct
(tres commits) y la `38610611624` del 14-oct (uno).

**Lo que se calculó con ellas:** la concordancia del rango de apertura
(`tools/concordancia_xauusd.py`). Corrió en esas dos corridas, y otra vez el 17-oct para el
brief 10 (entrada "La compuerta de concordancia del oro, roja"). Mira la dirección y la vela de
la primera ruptura; no calcula el retorno de ninguna regla.

**Lo que no hay:** ninguna vela diaria, del oro ni de otro activo, y nada en
`metrics/instrumentos/`, porque `velas.yml` y `sonda.yml` nunca escribieron en `data` (D8). El
universo de H3 sale de la primera sonda posterior al PR #31 (sección 2 del #33), y esa sonda
todavía no dejó nada.

El #33 se ajusta antes de fusionarse, y no es parte de este PR.

## 2026-10-17 — D8 (14-oct): los crons de "SPEL velas" y "SPEL sonda", apagados

**Fuente:** brief del Admin 10-B, punto 1. **Lo hizo el Admin el 14-oct.**

**Decisión:** apagar los crons de `velas.yml` y `sonda.yml`.

**Razón del Admin:** un rojo diario normaliza el fallo.

**Verificado** el 17-oct, con `GET /repos/sandbox33/SPEL/actions/workflows` y sus corridas:

| workflow | estado | desde | corridas |
|---|---|---|---|
| `velas.yml` | `disabled_manually` | 14-oct, 19:44:42 UTC | 7 programadas, del 08 al 14-oct, las 7 en rojo |
| `sonda.yml` | `disabled_manually` | 14-oct, 19:45:05 UTC | 7 programadas, del 08 al 14-oct, las 7 en rojo |

La última de `velas.yml` (run `38513551134`) cayó en el handshake de `ingestion/deriv_ws.py`:
"server rejected WebSocket connection: HTTP 520", el mismo 520 de la API legacy, muerta desde el
01-oct. La última de `sonda.yml` (run `38516631885`) cayó al commitear, porque
`metrics/instrumentos` no existía. Ninguna de las dos dejó nada en `data`.

**Para el Admin: el repo todavía dice lo contrario.** El apagado vive en la configuración de
GitHub, no en el repo (DG-1):
- `velas.yml` y `sonda.yml` conservan su `schedule:` (`15 0 * * *` y `45 0 * * *`);
- `tests/test_workflows_deriv.py::test_son_diarios` exige ese cron diario;
- el encabezado de `sonda.yml` dice "El cron puede quedar activo".

Ese test codifica el comportamiento viejo: se reporta, no se edita. Si los workflows se reactivan
desde GitHub, los crons vuelven tal cual.

## 2026-10-17 — D9 (14-oct): las Fases 3, 5 y 6, congeladas; GDELT, solo proxy de volatilidad

**Fuente:** brief del Admin 10-B, puntos 1 y 3.

**Decisión:**
- las Fases 3 (visualización), 5 (escala) y 6 (motor multi-timeframe) quedan congeladas;
- GDELT se usa solo como proxy de volatilidad.

**Razón del Admin:** nada de infraestructura sin edge demostrado.

**Aplicado sin código** (punto 3 del brief): ni `BLUEPRINT.md` ni el semáforo de `ESTADO.md`
decían que estuvieran congeladas. Ahora cada una de esas secciones de BLUEPRINT lleva la marca, y
el semáforo dice "❄️ CONGELADA (D9)".

**Lo que no cambia:** la ingesta diaria de GDELT sigue (`gdelt.yml`, activo; en `data` hay
commits del 15, 16 y 17-oct). Es el dato de H2, que prueba la entropía justamente como predictor
de volatilidad. La incógnita 3 de `ESTADO.md` (¿el motor GDELT o el motor rápido?) queda con D9
anotada; cerrarla es del Admin.

## 2026-10-17 — D11 y D11-u (14-oct, delegadas en el auditor): H-ORB-oro pausada, y cuándo se reabre

**Fuente:** brief del Admin 10, punto 1; las razones, del brief 10-B.

- **D11:** H-ORB-oro queda **pausada**. La compuerta de concordancia dio roja (entrada
  siguiente). **Razón:** la compuerta se fijó antes de los datos y no se relaja.
- **D11-u:** se reabre con **756 días hábiles de velas M5 de Deriv**
  (`governance.ensayos.DIAS_HABILES_M5_DERIV_PARA_REABRIR_H_ORB_ORO`, y
  `h_orb_oro_se_puede_reabrir`). **Razón:** la de D1, sin 3 años de histórico no se valida
  nada. Deriv da una ventana móvil de ~1 año (entradas del 2026-10-05: 365 días, en diario y en
  intradía), así que hay que acumular. [INTERPRETACIÓN] 756 = 3 × 252 días hábiles.
- **Medido sobre la rama `data` en `bc2597a`:** Deriv tiene **263** días hábiles con velas M5,
  del 2025-10-08 al 2026-10-14 (fechas UTC distintas con al menos una vela, todas de lunes a
  viernes). Faltan 493.

## 2026-10-17 — La compuerta de concordancia del oro, roja: 62,74 % contra el 90 %

**Recalculado antes de escribir**, como pide el brief: `python tools/concordancia_xauusd.py`
con `SPEL_DRIVE_ROOT` sobre un worktree de la rama `data` en `bc2597a` (el commit del run
38610611624 del 14-oct). El tool no cambió desde `639b2fd`.

| | |
|---|---|
| TwelveData `td_XAUUSD/300.jsonl` | 499.252 velas, 2020-03-16 01:10 → 2026-10-14 17:45 UTC, sha256 `368b1975…` |
| Deriv `frxXAUUSD/300.jsonl` | 70.718 velas, 2025-10-08 03:30 → 2026-10-14 18:00 UTC, sha256 `79488b6a…` |
| `trading_times` | del 2026-10-14, sha256 `4fe64afb…`; 30.214 velas de TwelveData fuera del horario de Deriv (26.842 de fin de semana, 2.544 de pausa diaria, 828 de feriado) |
| días comunes / con ruptura en alguna | 263 / 263 |
| **misma dirección y misma vela** | **62,74 %** (165 días) |
| misma dirección | 92,02 % (242 días) |
| una rompe y la otra no | 1,14 % (3 días) |
| \|Δhigh\| p50 / p90 / p99 | 0,668 / 2,973 / 17,692 USD (en fracción de d: 0,077 / 0,282 / 2,119) |
| \|Δlow\| p50 / p90 / p99 | 0,722 / 3,044 / 17,775 USD (en fracción de d: 0,088 / 0,256 / 2,105) |
| umbral | 90 % → **compuerta roja** |

Las dos cifras del auditor (62,74 % y 92,02 %) **coinciden**.

Derivado de las mismas cuentas: rompen las dos fuentes en 260 días. En 18 rompen en dirección
contraria, y en 77 en la misma dirección pero en otra vela.

**Una precisión sobre el umbral.** El brief dice "umbral fijado por 639b2fd el 07-oct". El valor
`UMBRAL_COMPUERTA = 0.90` entró en `ba857da` (07-oct, 03:17 UTC). `639b2fd`, tres minutos
después y en el mismo PR #37, es el último cambio del tool, y cambia la regla del fin de semana,
no el umbral. Los dos son anteriores al primer run con datos (08-oct). Así que el umbral se fijó
antes de medir, que es lo que importa.

La concordancia no es un ensayo de trading, así que **no suma al N**
(`governance/ensayos.jsonl`, `concordancia-orb-oro`).

## 2026-10-17 — D12 (17-oct): H1 no tiene alternativa declarada

**Fuente:** brief del Admin 10-B, punto 1. La razón no viene escrita aparte.

**Decisión:** la alternativa declarada de H1 es **ninguna**. Si el Sharpe de la serie base es
< 0,3, la estrategia se rechaza, el proceso se detiene, y no suma ensayos (N) de optimización
correctiva.

**Contra el pre-registro sellado.** `research/preregistro_h1.md` §10 dice: "Con Sharpe < 0,3 se
prueba una alternativa declarada y, si también falla, se detiene". La alternativa quedó como
[PENDIENTE DEL ADMIN] ("Tiene que quedar escrita acá antes de fusionar"), y el documento se
fusionó sin ella. Está fijado por sha256, así que D12 la contesta desde acá. No lo contradice:
sin alternativa, "se detiene" aplica directo.

- [INTERPRETACIÓN] "La serie base" es la de BTC: §11 dice que el oro es una réplica, no una
  variante, y que no suma al N.
- H1 v2 (D5) escribe D12 en su propio texto.

## 2026-10-17 — Brief 10, puntos 2 y 5: la estadística del Sharpe y las constantes nuevas

**`core/estadistica_sharpe.py`**, la única implementación. Solo usa `math` y
`statistics.NormalDist`: nada de numpy, scipy ni torch.
- `varianza_sharpe`: la σ² generalizada.
- `psr`: σ evaluada en SR₀.
- `min_trl`: z como parámetro.
- `sr_estrella`: portado de `research/preregistro_h1.md` §9.
- `dsr`: PSR(SR*).

**La fórmula queda UNVERIFIED contra el paper.** `papers.ssrn.com` no resuelve desde este
entorno, así que no se pudo leer ni SSRN 5520741 ni 5950754, y no hay página citada. Sí se
contrastó contra el código de réplica de los autores (github.com/zoonek/2025-sharpe-ratio,
`functions.py::sharpe_ratio_variance`, commit `6199ecb`, 05-feb-2026). Ahí:
- a = 1 + 2ρ/(1−ρ), b = 1 + ρ/(1−ρ) + ρ²/(1−ρ²) y c = 1 + 2ρ²/(1−ρ²), que son algebraicamente
  (1+ρ)/(1−ρ), (1+ρ+ρ²)/(1−ρ²) y (1+ρ²)/(1−ρ²);
- γ₄ es la curtosis, no el exceso;
- el PSR evalúa la varianza en SR₀.

Su `test_numeric_example` usa el mismo vector del brief, con ρ = 0 y con ρ = 0,2. El código no
se ejecutó: se leyó.

**Tests contra el vector del brief** (ρ = 0,2, T = 24, SR̂ = 0,45570, z = Φ⁻¹(0,95)):
- σ[0] = 0,2500, PSR(0) = 0,9658, MinTRL = 19,543;
- σ[0,1] = 0,2770, PSR(0,1) = 0,9005, MinTRL = 39,369;
- σ[SR̂] = 0,3795, fijado aparte: usar esa σ en el PSR daría 0,885.

Con ρ = 0, los números coinciden con los tests de los autores: 0,329, 13,029, 0,987 y 0,939.

**Para el Admin: H1 v1 sellado.** Con ρ = 0, la varianza es la de 2012 salvo T en vez de T−1, y
un test lo fija. Pero el PSR sellado en `preregistro_h1.md` §9 evalúa la σ en el SR observado,
no en SR₀. Con el vector y ρ = 0, el PSR(0) de H1 v1 da 0,913 y el de este módulo 0,987. **Este
módulo no evalúa las compuertas de H1 v1 tal como están selladas.** El pre-registro no se tocó, y
H1 v2 está en la cola.

**Diferencias declaradas:**
- El DSR no ajusta la varianza por el máximo de K ensayos, como sí hace el código de los
  autores (`moments_Mk`). Se portó §9 tal cual.
- `min_trl` lanza si SR̂ ≤ SR₀.
- `sr_estrella` exige un N entero ≥ 2, porque con N = 1 Φ⁻¹(0) no existe.

**Constantes nuevas en `config/constantes.json`:**
- `core.estadistica_sharpe.EULER_MASCHERONI`: medido, verificado contra Σ1/k − ln n.
- `governance.ensayos`:
  - `RUTA_ENSAYOS` (derivada);
  - `CAMPOS_ENSAYO`, `ESTADOS_ENSAYO` y `HIPOTESIS_PREVIA_A_LA_SERIE_H`;
  - `DIAS_HABILES_M5_DERIV_PARA_REABRIR_H_ORB_ORO` = **756**, con fuente D11-u.
- `core.preregistro_h1.ENSAYOS_PREVIOS_FALLIDOS` pasa a `derivada`.
- Las fuentes de `PSR_MINIMO` y `DSR_MINIMO` de `governance.paso_a_real` citan D2.

**Mutantes:** 56, todos atrapados.
- 24 sobre la estadística del Sharpe:
  - signo de γ₃, curtosis como exceso, el /4;
  - cada coeficiente en ρ, T−1;
  - σ en el observado;
  - las ramas de MinTRL, de SR* y del DSR;
  - Euler–Mascheroni;
  - cada control de entrada.
- 20 sobre el registro de ensayos:
  - cada filtro de `previos_fallidos`;
  - append-only, ids repetidos y cada control de validación;
  - el 756 y su borde;
  - el literal 3 en H1;
  - lo sembrado.
- 12 sobre DG-3 y d):
  - cada mitad de a) y de d);
  - la latencia;
  - el conteo: primera marca, trades abiertos, sin reconciliar como ok;
  - los tres documentos.

## 2026-10-17 — DB-3 (del auditor bajo delegación, 17-oct): un trade terminado sin reconciliar incumple d)

**Fuente:** brief del Admin 10-B, punto 2. Contesta lo que la entrada de D2 dejó sin decidir.

**Decisión:** un trade terminado `sin_reconciliar` incumple d) de DG-3. Entra como un campo propio
de `EvidenciaDemo`, `sin_reconciliar`, y no se suma a los no_verificables, para que el veredicto
diga cuál falta.

**Implementación** (`governance/paso_a_real.py`):
- `EvidenciaDemo.sin_reconciliar` es obligatorio. Sin valor por defecto, un llamador que no lo
  pase falla con TypeError en vez de cumplir d) por omisión. Un negativo lanza, como los otros
  conteos.
- d) se incumple si cualquiera de los tres conteos es distinto de 0.
- `Evaluacion.incumplidas_d` nombra lo que falta de d), por campo de `EvidenciaDemo` y en orden:
  discrepancias, no_verificables, sin reconciliar. Vacío si d) se cumple.
- `governance/PRINCIPLES.md` #6, `CLAUDE.md` y `BLUEPRINT.md` dicen la d) nueva, y un test lo
  exige.

**Antes, un defecto de este mismo PR.** `conteo_de_marcas` contaba como ok la última marca ok de
un trade aunque no trajera `reconciliado_comision`. `reconciliar()` no la cuenta así desde DB-2:
un ok anterior a DB-2 se reconcilia de nuevo. Con las dos reglas distintas, d) podía cumplirse con
un trade cuya comisión nunca se contrastó. Ahora las dos funciones usan un solo predicado,
`reconciliar.ok_vigente`, y ese ok cuenta como sin reconciliar hasta que `reconciliar()` le agrega
su fila. `reconciliar()` se comporta igual que antes.

**Tests de paso_a_real editados**, con la autorización del brief 10, punto 4, para este mismo PR,
que el 10-B completa. Ningún assert viejo cambió: cambiaron entradas, y se sumaron casos y
asserts.

| línea vieja | línea nueva |
|---|---|
| `discrepancias_reconciliacion=0, no_verificables_reconciliacion=0,` (`_BORDE`) | `discrepancias_reconciliacion=0, no_verificables_reconciliacion=0, sin_reconciliar=0,` |
| `discrepancias_reconciliacion=0, no_verificables_reconciliacion=0)` (`test_a_se_mide_solo_sobre_el_historico`) | la misma, más `sin_reconciliar=0`, para que el TypeError venga solo de `psr` y `dsr` |
| `no_verificables_reconciliacion=conteo["no_verificable"]))` (`test_d_cuenta_desde_la_ultima_marca_global_del_registro`) | la misma, más `sin_reconciliar=conteo["sin_reconciliar"]`, y un assert nuevo sobre `incumplidas_d` |
| — | `({"sin_reconciliar": 1}, "d")` y `{"sin_reconciliar": -1}` (casos nuevos) |

**Tests nuevos:**
- el que separa DB-3 de D2: un solo trade, cerrado y sin reconciliar. Con la d) de D2 el
  veredicto era APTO, y ahora es ZONA_GRIS, con `incumplidas_d == ("sin_reconciliar",)`;
- cada parte de d) se nombra sola, y las tres juntas van en orden;
- si d) se cumple, `incumplidas_d` está vacío;
- `sin_reconciliar` no tiene valor por defecto;
- los tres documentos dicen "trades terminados sin reconciliar";
- en `tests/test_integracion_demo_registro.py`: el ok viejo de un registro v1 cuenta como sin
  reconciliar; la tabla de `ok_vigente`; y lo que el conteo da por ok es justo lo que
  `reconciliar()` no vuelve a reconciliar.

**Mutantes:** 20, todos atrapados.
- 12 sobre DB-3:
  - la d) de D2, que ignora a los sin reconciliar. También la atrapa por sí solo el test que
    separa las dos: `assert 'APTO' == 'ZONA_GRIS'`;
  - sumar los sin reconciliar a los no_verificables;
  - un negativo que no lanza;
  - un valor por defecto de 0;
  - otro orden de las partes de d);
  - un veredicto que no dice qué falta;
  - el umbral de cada conteo, y una d) que no se agrega cuando falta una sola parte;
  - las discrepancias fuera de d);
  - cada uno de los tres documentos sin la d) nueva.
- 8 sobre `ok_vigente`:
  - el conteo con la regla vieja, atrapado por una aserción;
  - el ok viejo contado como no_verificable;
  - `ok_vigente` sin mirar la comisión, con `[]` en vez de `get`, o aceptando no_verificable;
  - `reconciliar()` sin `ok_vigente`;
  - el conteo con la primera marca;
  - un trade sin fila contado como ok.

## 2026-10-17 — Resoluciones del auditor (17-oct) a lo que el PR #44 dejó sin resolver

**Fuente:** brief del Admin 11–13, tarea 0. Resuelven lo que el #44 reportó en las entradas de
D1, D2, D4 y D8, y en el índice de los briefs 10 y 10-B.

**D4-c, las fricciones de H-ORB-oro.** Son spread + slippage + la comisión κ medida: κ del oro
vale 0,0002 del nocional (entrada del 2026-10-06, d). El swap no aplica: los multiplicadores
cobran una comisión única, sin funding (decisión a) del Admin del 06-oct). **Es una corrección de
hecho, no un cambio de criterio.** No lleva código, porque H-ORB-oro está pausada (D11).

**D2-n, qué mide la demo.** La demo mide las fricciones de la demo. Que sirvan de proxy del
deslizamiento real queda **UNVERIFIED** (entrada del 2026-09-25, "La demo valida integración, no
ejecución"). c) de DG-3 se mantiene como está, y no cambia ningún criterio.

**D8-r, los crons apagados.** Los crons de `velas.yml` y `sonda.yml` quedan apagados en GitHub.
Los YAML y `tests/test_workflows_deriv.py::test_son_diarios` no se tocan: los reactiva el brief de
migración de `ingestion/deriv_ws.py`. `ESTADO.md` lo dice en la fila de cada workflow.

**D1-p, la profundidad de TwelveData.** La profundidad diaria ya está medida (entrada del
2026-10-05, sonda §0.A-3 v2, f): BTC desde el 2017-08-28, 3.326 barras; XAU desde el 1979-12-26,
12.288. **La sonda del brief 8 se cancela.** La métrica de coincidencia y la hora de corte las fija
el brief 14, antes de bajar precios.

**Interpretaciones confirmadas:**
- la serie base de D12 es BTC;
- "intermedio" en D5 es lo que ni avanza ni se rechaza;
- 756 = 3 × 252 (D11-u).

**D10 no existe:** la numeración del memo de traspaso saltó de D9 a D11.

**Aplicado en este PR:**
- `ESTADO.md`: D8-r en las filas de `velas.yml` y `sonda.yml`, y la cola sin la sonda del brief 8
  (D1-p);
- `config/constantes.json`: el 756 de D11-u deja de estar marcado como interpretación.

D4-c y D2-n no llevan código.

## 2026-10-17 — Tarea 11: H2 según D6, y la fuente de precios que no alcanza

**Fuente:** brief del Admin 11–13, tarea 11. Rama `feature/preregistro-h2` (PR #34), rebasada
sobre `main` en `2ae82df`.

**El rebase.** Los dos commits de H2 chocaban con `main` en `config/constantes.json` y
`config/README.md`. Las constantes se resolvieron con las de `main` más las de H2. El README
arrastraba un desfase previo: decía **138** constantes con el registro de `main` en **172**. Se
corrigió en un commit aparte, con un test que compara los dos números con el registro.

**Auditoría del documento contra D6, antes de cambiarlo:**

| D6 | el documento del 01-oct | |
|---|---|---|
| entropía de hace 2 días, primaria | sección 3: `t − 2`, por la decisión del 29-sep | coincide |
| la de hace 1 día, secundaria, que cuenta en el N | "La lectura de rezago 1 queda descartada." | **difiere** |
| ventanas de 1, 7 y 30 días | 1/7/30 en BTC; 1/5/22 en XAU | coincide en BTC; D6 no nombra a XAU |
| horizontes 1, 5 y 22, con MCS y Diebold–Mariano | un solo horizonte (la varianza de `r_t`) y solo Diebold–Mariano | **difiere** |
| el día de la semana, solo en entrenamiento | las indicadoras y los promedios salen de la ventana de estimación | coincide; no estaba escrito como regla |
| calendario 24/7 para BTC | "continuo para BTC" | coincide |

**Lo que se aplicó** (`research/preregistro_h2.md`, `core/preregistro_h2.py`):
- la secundaria, con su propio rezago en el módulo, y un rechazo a cualquier otro rezago (el 0
  sería la entropía del mismo día);
- los horizontes, con la varianza media de las `h` barras como objetivo y sin usar filas cuyo
  objetivo no terminó;
- Diebold–Mariano por rezago y horizonte, con Holm sobre las 6 pruebas de cada activo;
- el MCS sobre `{A, B₂, B₁}`;
- la regla del día de la semana, escrita;
- H2 en el registro de ensayos (D3), con dos ensayos que cuentan en el N, pre-registrados y no
  corridos.

No se duplicó ninguna fórmula: H2 no usa la estadística del Sharpe, y su N es el del registro.

**Interpretaciones nuevas**, que el Admin aprueba al fusionar:
- las ventanas de XAU son 1/5/22 barras hábiles;
- los horizontes son barras: en BTC, 5 y 22 no son semana ni mes;
- la multiplicidad se ajusta por Holm;
- los parámetros del MCS: nivel 0,90, `T_max`, bloques móviles del largo de los rezagos de
  Newey–West, 10.000 réplicas y semilla 20261017;
- si Diebold–Mariano y el MCS discrepan, es no concluyente, el criterio de D5.

La definición de `T_max` se cita de memoria. El paper no se pudo leer, y el PR de evaluación la
contrasta.

**CONTRADICCIÓN, sin resolver: la fuente de precios.** El documento decía "las velas diarias de
Deriv que persiste `ingestion/velas.py`":
- Deriv da 365 días (entrada del 2026-10-05), y la ventana de estimación arranca con 504 barras:
  no queda ningún pronóstico;
- la rama `data` no tiene velas diarias (D8);
- D1 dice que la historia larga sale de TwelveData, validada por el brief 14.

La sección 2 queda **[PENDIENTE DEL ADMIN]**, con una propuesta: las velas de D1 que valide el
brief 14, y revisar el rezago si la hora de corte abre la barra antes de las 00:00 UTC. El
pendiente se completa antes de fusionar. La serie GDELT no es la restricción: va del 2013-04-01
al 2026-10-16. Para escribir esto se leyó su rango y su primera línea; ningún precio.

**Tests editados.** El cambio de criterio obliga a tocar el test del documento, que está en este
mismo PR:

| línea vieja | línea nueva |
|---|---|
| `SHA256_PREREGISTRO = "e4e1d7a2…"` | `SHA256_PREREGISTRO = "7b2c5cea…"` |
| `def test_el_rezago_es_de_calendario_y_la_lectura_de_rezago_1_no_esta():` | `def test_el_rezago_es_de_calendario_y_el_de_1_dia_es_secundario():` |
| `assert "La lectura de rezago 1 queda descartada." in texto` | `… not in texto`, y la lectura secundaria tiene que estar |

Además, tres tests de `main` (`tests/test_ensayos.py`, del brief 10) fijaban el tamaño total del
registro, y registrar H2, que el brief pide, los rompe por construcción. Se editaron **sin
autorización explícita**, en un commit aparte y revertible, sin cambiar lo que verifican:

| línea vieja | línea nueva |
|---|---|
| `assert len(en.que_cuentan()) == 3` | de las 4 sembradas, las que cuentan son las de los 3 previos |
| `assert len(en.que_cuentan(registro)) == 4` | `== antes + 1`, con `antes` medido |
| `match=r"ensayos\.jsonl:5"` | `match=rf"ensayos\.jsonl:{n + 1}\b"`, con `n` las líneas del archivo |

Pasan con el registro de `main` y con el nuevo.

**Mutantes:**
- 18 de H2: el rezago sin validar, True como 1, la secundaria por defecto, el orden de los
  rezagos, el rezago pedido ignorado, los rezagos HAC, la familia como literal o sin la
  secundaria, el horizonte, el MCS, la tolerancia y el registro;
- 3 del README;
- 2 nuevos del registro, más los 20 del brief 10 vueltos a correr.

Todos atrapados.

## 2026-10-17 — Resoluciones del auditor (17-oct) a los pendientes de H2, H3 y H1 v2

**Fuente:** brief del Admin 11–13-B. Son decisiones del auditor bajo el criterio delegado del
Admin (entrada "El criterio del Admin para decidir en su ausencia"), y cierran lo que las tareas
11, 12 y 13 dejaron pendiente.

**P1 (H2, H3, H1 v2), la fuente de precios.** Las velas diarias de D1 (TwelveData), y solo
después de que el brief 14 las valide con su compuerta. Si el brief 14 sale rojo para un activo,
la hipótesis no corre sobre ese activo: no hay fuente de reemplazo sin validar, y la compuerta no
se relaja.

**P2 (H3), el universo.** H3 conserva su regla de universo (sección 2) y queda **bloqueada**
hasta que haya historia validada para el universo. No se reduce a K = 2: con BTC como componente
H1 y el oro como réplica, H3 con K = 2 sería H1 contado dos veces. La ruta prevista: la migración
de `ingestion/deriv_ws.py` revive la sonda, el universo se congela, y D1 se extiende activo por
activo con la compuerta del brief 14. Esa extensión es un brief propio, que se decide si H1 v2 o
H2 muestran algo.

**P3 (H3), el N.** Suman los ensayos del registro que son estrategias: los 3 previos, los de H1 y
la cartera de H3. Los de H2 no suman: prueban un predictor de volatilidad con Diebold–Mariano y
MCS, no un Sharpe seleccionado. La regla se declara en `governance/ensayos.py`, con test.

**P4 (H3), la estadística del Sharpe.** La de H1 v2: `core/estadistica_sharpe.py`, con la σ en
SR₀ y con ρ. Reemplaza las convenciones de 2012 del documento, que declara el cambio y su razón.

**P5 (H3), la zona gris.** No concluyente, y no va a demo, igual que lo intermedio de H1 v2 (D5).
La razón: con D2, a) se mide solo sobre el histórico, así que la demo no puede rescatar una zona
gris, y mandarla a demo es ruido operativo.

**P6, interpretaciones aprobadas:**
- en H2: las ventanas de XAU, los horizontes en barras, Holm, los parámetros del MCS, y que un
  desacuerdo entre Diebold–Mariano y el MCS sea no concluyente;
- en H1 v2: el Sharpe de las compuertas es el anualizado.

Y, tal cual:

> H1 v2 es menos estricto que v1 en el vector del brief 10 (PSR(0) 0,913 → 0,987). Se acepta:
> es una corrección de método, declarada antes de los datos.

**Contra el código y el decision-log, antes de aplicarlas.** Ninguna los contradice:
- **P1** es la fuente de D1, con la compuerta que fija el brief 14 (D1-p). La historia usable no
  depende de la fuente: `ingestion/velas.py::profundidad` la mide con las fechas de cualquier
  serie. El adaptador de TwelveData ya pide la barra diaria sin `timezone` (decisión del Admin
  del 06-oct).
- **P3** no cambia `cuenta_en_n`. El brief 11–13 pedía que la secundaria de H2 contara "en el N
  (D3)", y cuenta: está en el registro con `cuenta_en_n: true` y en la familia de Holm de H2. P3
  dice en cuál: en el de su familia de pruebas, no en el del DSR de una estrategia. El docstring
  de `governance/ensayos.py`, que dice "si suma al N del DSR", se corrige con la regla, en el #33.
- **P5** es la zona gris de las compuertas de H3, la del backtest. La de DG-3 es otra cosa
  (`governance/paso_a_real.py`: el forward en demo, de 6 meses como máximo, mientras falte alguna
  condición), y no cambia.
- **Para el Admin: un roce de P2 con DG-5.** DG-5 (25-sep) dice "H3 es la ruta principal". Con P2,
  H3 queda bloqueada, y la extensión que la desbloquea se decide si H1 v2 o H2 muestran algo. No
  es una contradicción: H3 sigue siendo la principal y la bloquean los datos, porque D1 da historia
  solo a BTC y al oro. Pero el orden de hecho pasa a ser H1 v2 y H2 primero. Si DG-5 pedía otra
  cosa, lo dice el Admin. El documento de H3 dice las dos.

**Dónde se aplica cada una:** P1 y P6 de H2, en el #34; P1 a P5, en el #33; P1 y P6 de H1 v2, en
el #45.

## 2026-10-17 — Brief 11–13-B en H2 (#34): P1 y P6

**Fuente:** brief del Admin 11–13-B. Rama `feature/preregistro-h2`.

**Lo aplicado** (`research/preregistro_h2.md`):
- la sección 2 dice la fuente de P1, y el documento ya no tiene ninguna marca de pendiente del
  Admin;
- la sección 3 deja de suponer las barras de Deriv: el rezago está contado para barras de 00:00 a
  00:00 UTC, y las de D1 cortan donde fije el brief 14;
- las cinco interpretaciones de P6 dicen **[INTERPRETACIÓN aprobada (P6)]**. La tolerancia cero y
  Garman–Klass, que P6 no nombra, siguen como interpretaciones que el Admin aprueba al fusionar.

**Dos interpretaciones nuevas**, que el Admin aprueba al fusionar:
- **Sin BTC no hay conclusión.** Si el brief 14 sale rojo para BTC, H2 no corre sobre BTC (P1), y
  la sección 9 concluye con la primaria de BTC. XAU es una réplica y no ocupa su lugar.
- **La hora de corte.** P1 no la nombra. El rezago de 2 días está contado para barras que abren a
  las 00:00 UTC. Si el brief 14 fija un corte que abre la barra de `t` antes, la entropía de `t−2`
  podría no estar publicada al abrir: su archivo sale durante `t−1`, y a las 06:30 UTC a veces
  todavía no está. La evaluación se detiene y pregunta antes de correr. No se decide un rezago
  nuevo: la propuesta de la tarea 11 era revisar la sección 3 en ese caso, y eso es del Admin.

**El registro de ensayos.** `h2-rezago-2` y `h2-rezago-1` apuntan al sha256 nuevo del documento.
Son líneas de este PR, que no están en `main`: el registro de `main` sigue siendo un prefijo del de
la rama, y lo sembrado conserva su sha256.

**El registro de constantes.** Las notas de `HAR_VENTANAS_HABIL`, `HORIZONTES`, `NIVEL_MCS`,
`REPLICAS_MCS` y `SEMILLA_MCS` dejan de decir [INTERPRETACIÓN] y dicen que la aprobó el auditor
(P6), como el 756 en la tarea 0.

**Tests.** El brief pide el test actualizado con cada cambio de un documento fijado:

| línea vieja | línea nueva |
|---|---|
| `SHA256_PREREGISTRO = "7b2c5cea…"` | `SHA256_PREREGISTRO = "ab369612…"` |
| — | 4 tests nuevos (8 casos): ninguna marca de pendiente; la fuente de P1; las cinco aprobadas; solo esas |

Ningún test viejo cambió de criterio.

**Mutantes:** 10, cada uno atrapado por su propio test y no solo por el sha256:
- la marca de pendiente, de vuelta;
- una fuente de reemplazo, y P1 sin la compuerta del brief 14;
- la hora de corte, sin detener;
- Holm y el MCS, sin aprobar;
- tres interpretaciones que P6 no nombra, marcadas como aprobadas;
- el registro, con el sha viejo.

## 2026-10-17 — Tarea 12: H3 según D7 y D2, con tres pendientes del Admin

**Fuente:** brief del Admin 11–13, tarea 12. Rama `feature/preregistro-h3` (PR #33), rebasada
sobre la tarea 11 (`716bb8c`).

**El rebase.** Los dos commits de H3 chocaban con la tarea 11 en `config/constantes.json` y
`config/README.md`. Se resolvieron con lo de la tarea 11 más las 24 constantes de H3, y el
README con los conteos medidos, que su test nuevo exige.

**D7, verificado contra `data` antes de escribirlo.** El brief dice "Deriv desde el 08-oct,
TwelveData desde el 14-oct, commit bc2597a", y coincide:
- el M5 de Deriv entró el 08-oct en tres commits (`852e5ce`, `54f63fc` y `db491b4`), y siguió
  el 14-oct;
- el de TwelveData entró recién el 14-oct, en `bc2597a`, con las revisiones de Deriv.

La sección 2 lo declara con lo medido: 70.718 y 499.252 velas, con sus rangos. Lo único que se
calculó con esas velas es la concordancia del rango de apertura, y ninguna regla de H3. Se
declara además el cruce EMA 20/63 del acta del 06-sep, una regla de tendencia previa sobre el
data lake legacy que ya está en el registro.

**D2 en la sección 12:**
- a) va solo sobre el backtest histórico;
- d) suma los no_verificables y los trades sin reconciliar (DB-3);
- la latencia queda sin umbral;
- cae la interpretación que pedía aprobar la compuerta de avanzar sobre histórico + demo.

Queda escrita una consecuencia: una zona gris no tiene camino a real, porque a) no cambia con
el forward. **Para el Admin:** el forward de una zona gris solo valida infraestructura, y al
vencer pasa a H2. D5 trata lo intermedio de H1 v2 como no concluyente y sin demo, y H3 todavía
lo manda a demo.

**El sizing.** La sección 6 justifica el escalado por volatilidad frente al nocional igual por
activo, con tres razones:
- el riesgo lo pondría el activo más volátil;
- el Sharpe mediría la composición, no la regla;
- el sizing no mira el futuro.

La versión sin escalar no se corre: sería otra variante.

**Tres contradicciones, sin resolver.** Quedan como [PENDIENTE DEL ADMIN] en el documento, cada
una con su propuesta:
1. **La historia del universo** (sección 2). Deriv da 365 días y la rejilla mínima pide 766
   barras; la sonda nunca escribió el universo (D8); y D1 cubre solo BTC y el oro. Hay tres
   salidas: limitar H3 a K = 2, extender D1 a todo el universo o acumular velas de Deriv.
2. **El N** (sección 11). D3 hace del registro la única fuente del N, y el registro ya tiene a
   H2. La propuesta es contar los ensayos que cuentan y son estrategias, sin los de H2.
3. **La estadística del Sharpe** (sección 11). El documento usa la de H1 v1: σ en el SR
   observado y `T−1`. D5 pasa H1 v2 al módulo único, y con la de v1 H3 necesitaría una segunda
   implementación. La propuesta es la de H1 v2.

**Dos observaciones, sin cambio en la sección 8:**
- dice que la unidad de `commission` se fija con las respuestas crudas, y ya se fijó: es un
  monto (decision-log 2026-10-05, d);
- su piso de 0,10 USD por contrato es una cota pesimista, porque la comisión medida no tiene
  mínimo fijo (2026-10-05, c).

**H3 en el registro de ensayos:** `h3-cartera`, pre-registrada, que cuenta en el N y apunta al
sha256 del documento.

**Tests.** El brief autoriza actualizar el de H3:

| línea vieja | línea nueva |
|---|---|
| `SHA256_PREREGISTRO = "71826ed2…"` | `SHA256_PREREGISTRO = "40a05d96…"` |
| — | 5 tests nuevos: D7; D2 y DB-3 en la sección 12; las cifras de DG-3 iguales a las de `governance/paso_a_real.py`; el sizing; el registro |

**Mutantes:** 13, cada uno atrapado por su propio test y no solo por el sha256.

## 2026-10-17 — Brief 11–13-B en H3 (#33): P1 a P5

**Fuente:** brief del Admin 11–13-B. Rama `feature/preregistro-h3`, rebasada sobre el #34 nuevo
(`50f4c11`). La entrada de la tarea 12, más arriba, describe el documento antes de P1 a P5.

**El rebase.** Chocaron tres archivos:
- el registro de ensayos: `h3-cartera` va después de las dos líneas de H2, que cambiaron de
  sha256;
- el decision-log: la entrada de la tarea 12 va al final;
- `ESTADO.md`.

Se resolvieron a mano, sin perder nada de ninguno de los dos lados.

**P3 en `governance/ensayos.py`:**
- `HIPOTESIS_DE_ESTRATEGIA` = {previa a la Serie H, H1, H3} y `HIPOTESIS_QUE_NO_SON_ESTRATEGIA` =
  {H2}, disjuntas;
- `es_estrategia(e)` da la clase por la hipótesis;
- `que_cuentan_en_el_dsr()` devuelve los que cuentan y son estrategias: los únicos que pueden sumar
  al N de un DSR.

Cuáles suman lo dice cada pre-registro. El de H3 los suma todos, que es lo que fija P3. El de H1,
en v1 sellado y en v2, suma los suyos y los previos (sección 9), y P3 no lo cambia: no incluye la
cartera de H3, que no corrió.

Es un criterio y no un campo, porque un campo nuevo obligaría a reescribir las líneas sembradas,
fijadas por sha256. `cuenta_en_n` no cambia en ninguna línea; cambia lo que dice el docstring.
[INTERPRETACIÓN] Una hipótesis que cuenta sin estar clasificada hace fallar el conteo, en vez de
sumar o restar en silencio. H-ORB-oro queda sin clasificar: su única línea no cuenta, y se
clasifica cuando se reabra.

**Para el Admin: el registro cuenta líneas, no ensayos.** `que_cuentan_en_el_dsr()`, como
`que_cuentan()`, devuelve líneas. El registro dice que un ensayo que cambia de estado suma una línea
nueva con otro id, y no tiene un campo que diga cuál reemplaza a cuál. El primer cambio de estado
—`h1-v2` cuando H1 v2 corra, por ejemplo— dejaría dos líneas del mismo ensayo, y contarlas daría
un N de más. Hoy no pasa: ninguno cambió de estado. Queda escrito en el docstring, sin código, y lo
resuelve el PR que registre el primer cambio de estado.

**En el documento** (`research/preregistro_h3.md`):
- **P1 y P2, sección 2.**
  - Los precios son las velas diarias de D1 que valide el brief 14, y un activo en rojo no corre.
  - La regla de universo no cambia, y H3 queda bloqueada.
  - Ya no hay tres salidas: no se reduce a K = 2.
  - Está escrita la ruta de P2.
  - El encabezado dice las dos cosas: H3 es la ruta principal (DG-5) y está bloqueada (P2).
- **P3, sección 11.** El N son los ensayos de `que_cuentan_en_el_dsr()`, y los de H2 no suman.
- **P4, sección 11.**
  - La estadística es la de H1 v2, escrita completa: los momentos con `T`, el ρ de la serie y las
    llamadas a `psr` y `dsr`.
  - El cambio respecto de 2012 queda declarado con sus tres razones.
  - Queda escrito que no es más estricto en todos los casos (0,913 → 0,987 con ρ = 0).
- **P5, sección 12.** La zona gris es no concluyente y no va a demo. La zona gris de DG-3, el
  forward de 6 meses como máximo de lo que avanzó, no cambia.

**Una interpretación nueva en el documento**, que el Admin aprueba al fusionar: dejar afuera un
activo en rojo en el brief 14 no es elegir por precios. La compuerta compara dos fuentes del mismo
precio (D1) y no mira ninguna regla de H3.

**En el módulo** (`core/preregistro_h3.py`): `ENSAYOS_PREVIOS_FALLIDOS` sale de
`previos_fallidos()`, como el de H1 desde el brief 10. Era un 3 escrito a mano, justo lo que D3 vino
a quitar. Sigue valiendo 3, y `N_INGENUO` sigue en 11. En `config/constantes.json` pasa a
`derivada`.

**El registro de ensayos.** `h3-cartera` apunta al sha256 nuevo. Su activo deja de decir
"pendiente del Admin", y su nota dice que está bloqueada (P2). Es una línea de este PR, que no está
en `main`.

**Tests.** Cambian dos del #33, de este mismo PR, porque codificaban lo que P3 y P5 cambian:

| test | línea vieja | línea nueva |
|---|---|---|
| — | `SHA256_PREREGISTRO = "40a05d96…"` | `SHA256_PREREGISTRO = "53a1d5a0…"` |
| `test_el_paso_a_real_es_el_de_d2_y_db3` | `assert "**Con D2 no tiene camino a real:**" in s12` | se quita: P5 saca la zona gris de la demo, y la frase cae con ella |
| `test_el_modulo_no_importa_nada_que_haga_un_backtest` | `assert importados <= {"__future__", "math"}` | `assert importados <= {"__future__", "math", "governance.ensayos"}` |

Tests nuevos:
- 8 en el de H3: ninguna marca de pendiente, P1, P2, P3, los previos desde el registro, P4, P5 y la
  línea del registro;
- 6 en `tests/test_ensayos.py`, con 9 casos, que separan el criterio viejo (todo lo que cuenta
  suma al DSR) del de P3. De ese archivo, que está en `main`, solo se agregaron tests y un párrafo
  del docstring: ninguno de los que había cambió.

**Mutantes:** 28, todos atrapados.
- 12 sobre la regla de P3: cada clase, H2 como estrategia, H-ORB-oro clasificada sin decirlo, una
  hipótesis sin clasificar tomada como estrategia o como no, y el conteo sin el filtro o sin
  `cuenta_en_n`. Los 6 de las clases, también sin el test que compara los conjuntos.
- 16 sobre el documento, el módulo y el registro de H3, cada uno atrapado por su propio test y no
  solo por el sha256.

## 2026-10-17 — Tarea 13: H1 v2, con D5 y D12, y v1 sellado

**Fuente:** brief del Admin 11–13, tarea 13. Rama `feature/preregistro-h1-v2`, apilada sobre la
tarea 12 (`8e098d4`). `research/preregistro_h1.md` no se tocó: su sha256 sigue siendo el que fija
su test, y un test de v2 lo vuelve a exigir.

**Las dos cláusulas de v1, cerradas:**
- **La alternativa (D12): ninguna.** Con un Sharpe de la serie base (BTC) debajo de 0,3, la
  estrategia se rechaza, el proceso se detiene y no suma ensayos de optimización correctiva.
  Como no hay alternativa, no hay nada que declarar ni que sumar al N.
- **Lo intermedio (D5): no concluyente.** Es el caso de 0,3 ≤ Sharpe < 0,5, o de Sharpe ≥ 0,5
  con PSR o DSR debajo de 0,90, y no pasa a demo. `veredicto()` implementa la tabla.

**La estadística (D5).** Sale de `core/estadistica_sharpe.py`, con la σ en SR₀ = 0, con `T` y con
el ρ de la serie. Los estimadores quedan fijados antes de mirar: momentos con `T` en el
denominador, y ρ la autocorrelación de orden 1. El documento declara el cambio respecto de v1,
que evaluaba la σ en el Sharpe observado y con `T−1`, y da la razón: el PSR prueba la nula, así
que su desvío es el de la nula, y ρ es la solidez frente a retornos no normales.

**Para el Admin: el cambio no es más estricto en todos los casos.** Con el vector del brief 10,
v1 da PSR(0) = 0,913 y v2 da 0,987, o 0,9658 con ρ = 0,2. Queda escrito en el documento.

**Lo que no cambia** se importa de `core/preregistro_h1.py`: la rejilla, el walk-forward, el
sizing, el multiplicador, los umbrales y los previos. Un test exige que sean los mismos objetos
y que v2 no reescriba ninguno.

**Cambios por datos, no por criterio:**
- **La comisión.** v1 sumaba "las siete mediciones diarias de la sonda", y no existen (D8). v2
  usa κ medido en la cuenta real (0,0003 en `cryBTCUSD`), y sigue el máximo con el 0,1 % y el
  mínimo de 0,10 USD. La unidad de `commission` ya está fijada (05-oct).
- **El stop-out** ya está verificado (05-oct, d). [INTERPRETACIÓN] Si al correr la sonda de
  instrumentos sigue sin datos, se usan las mediciones de §0.A-3.
- **El paso a real** es DG-3 con D2 y DB-3, que v1 no decía.
- **El Sharpe de las compuertas es el anualizado.** [INTERPRETACIÓN] Las compuertas de v1 no lo
  dicen.

**CONTRADICCIÓN, sin resolver: la fuente de precios.** Es la misma de H2 y H3. v1 usaba las velas
de Deriv, que dan 365 días, y la condición de parada pide 1.058 barras con la rejilla mínima y
2.608 con la completa. v1 mismo lo preveía ("se decide primero de dónde sale la historia"), y D1
da TwelveData para BTC y el oro. Queda [PENDIENTE DEL ADMIN], con esa propuesta.

**H1 v2 en el registro de ensayos:** `h1-v2`, una línea, pre-registrada, que cuenta en el N. Sus
ensayos se cuentan al correr, porque dependen de la rejilla que la historia soporte. v1 no se
registra: no se corrió nunca.

**Mutantes:** 19, todos atrapados. En la primera pasada sobrevivió uno, el signo de la asimetría,
porque las series calculadas a mano eran simétricas. Se sumó una serie sesgada calculada a mano.

## 2026-10-17 — Brief 11–13-B en H1 v2 (#45): P1 y P6

**Fuente:** brief del Admin 11–13-B. Rama `feature/preregistro-h1-v2`, rebasada sobre el #33 nuevo.
La entrada de la tarea 13, más arriba, describe el documento antes de P1 y P6.

**El rebase.** Chocaron tres archivos:
- el registro de ensayos: `h1-v2` va después de `h3-cartera`;
- el decision-log: la entrada de la tarea 13 va al final;
- `ESTADO.md`.

`config/README.md` no chocó, pero quedó mal: los dos lados pasaban el conteo de 209 a 211, uno por
las dos constantes de P3 y otro por las dos de H1 v2, y git se quedó con 211. Son 213. Se corrigió en
el commit de H1 v2 con el conteo medido, que es lo que exige su test.

**Lo aplicado** (`research/preregistro_h1_v2.md`):
- la sección 1 dice la fuente de P1, y la propuesta de la tarea 13 deja de serlo;
- la réplica en oro usa la serie del oro de D1, con la misma compuerta (sección 11);
- el Sharpe anualizado de las compuertas dice **[INTERPRETACIÓN aprobada (P6)]** (sección 10);
- la sección 9 dice que el auditor aceptó que v2 no sea más estricto en todos los casos;
- las dos cláusulas de v1 se nombran sin la marca, y el documento no tiene ninguna.

**P6, tal cual** (entrada de las resoluciones): "H1 v2 es menos estricto que v1 en el vector del
brief 10 (PSR(0) 0,913 → 0,987). Se acepta: es una corrección de método, declarada antes de los
datos." Un test verifica que este decision-log lo diga así.

**Una interpretación nueva**, que el Admin aprueba al fusionar: si el brief 14 sale rojo para BTC,
no hay veredicto, porque las compuertas son de la serie base (D12) y el oro es una réplica. Si sale
rojo para el oro, no hay réplica.

**El N de H1 v2 no cambia con P3.** Sigue siendo el de su sección 9: sus ensayos, agrupados por
correlación, más los previos. No incluye la cartera de H3, que no corrió. P3 fija el N de H3, y ese
sí incluye los de H1.

**El registro de ensayos.** `h1-v2` apunta al sha256 nuevo. Es una línea de este PR, que no está en
`main`.

**Tests.**

| línea vieja | línea nueva |
|---|---|
| `SHA256_PREREGISTRO = "7ffc6150…"` | `SHA256_PREREGISTRO = "95588ea6…"` |
| — | 4 tests nuevos: ninguna marca de pendiente; la fuente de P1; P6 aprueba solo el anualizado; P6 acepta el cambio, en el documento y tal cual en el decision-log |

Ningún test viejo cambió de criterio.

**Mutantes:** 9, cada uno atrapado por su propio test y no solo por el sha256.
