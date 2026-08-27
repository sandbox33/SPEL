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

## Principios que se sostuvieron toda la sesión

- Ningún número entra sin fuente verificada contra el código real (no contra memoria de sesiones anteriores, no contra texto pegado sin auditar).
- Todo commit corre pytest antes de comitear. Todo patch se verifica en clon 100% ajeno vía `git am` antes de entregarse.
- Discrepancias encontradas (memoria vs. fuente real, texto externo vs. constantes reales) se registran explícitamente, no se resuelven en silencio.
- Un hallazgo de auditoría no crítico (#6, `drive_root`) se corrigió antes de seguir agregando trabajo encima, no se dejó como nota para "después".
