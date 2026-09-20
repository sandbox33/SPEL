# `config/` — el stream CONFIG

`governance/persistence.py` declara el stream `CONFIG → "config/"` desde el
patch 0010: *"versionado, con historial de git, revisable en PRs"*. El
directorio no existía. Esto es su primer contenido.

## `constantes.json` — qué es

El registro de **las 55 constantes de módulo** de `core/`, `ingestion/`,
`orchestration/`, `governance/` y `execution/`. Una entrada por constante,
con su valor, de dónde salió, y qué tan sostenido está ese valor.

**No es documentación.** Un documento se desactualiza en silencio;
`tests/test_registro_constantes.py` corre en cada PR y falla si el registro
y el código se separan — en las dos direcciones.

La clave de una entrada es **`modulo` + `nombre`**, nunca el nombre solo. Dos
módulos pueden declarar `SCHEMA_VERSION` y significar cosas distintas.

## `categoria`

Describe **la naturaleza del valor**, no su impacto. Casi toda constante
mueve algún resultado, así que "¿cambia un resultado?" no discrimina nada.

| | qué es | ejemplos |
|---|---|---|
| `parametro` | una magnitud que entra en aritmética o en una comparación | `GODEL_ROLLING_WINDOW_DAYS`, `N_TONE_BINS`, `HORAS_FUNDING_UTC` |
| `etiqueta` | un identificador, un nombre, una ruta, o un mapa hacia identificadores | `ENTROPY_STATE_LOW/MID/HIGH`, `GODEL_CRITERIA_VERSION`, `LEDGER_FILENAME`, `CORE_COUNTRY_FILTERS` |
| `derivada` | se calcula de otra constante o del entorno | `MINUTES_PER_DAY`, `GODEL_MASK_PERCENTILE`, `REGISTRY_PATH` |

Los casos de frontera se resolvieron con esa regla y no con intuición.
`GDELT_COL_INDICES` son enteros pero son posiciones dentro de un CSV, no
magnitudes: `etiqueta`. `HORAS_FUNDING_UTC` son enteros que se comparan
contra un `datetime` para contar períodos: `parametro`.

Una `derivada` **no lleva `valor`**, lleva `expresion`. Fijar `66.0` para
`GODEL_MASK_PERCENTILE` haría que cambiar `ENTROPY_STATE_PERCENTILES` dejara
al registro mintiendo sin que ningún test lo note — que es justo lo que el
registro existe para evitar. `REGISTRY_PATH` es el caso extremo: su valor
absoluto depende de dónde esté clonado el repo.

## `evidencia`

Campo **obligatorio y sin default**. El punto es que alguien tenga que
elegir; `provisional_sin_evidencia` es una respuesta válida, no contestar no
lo es.

- **`legacy_citado`** — el valor viene del legacy y se puede citar dónde.
  *Ej.:* `MIN_EVENTS_FOR_VALID_DAY` ← `gdelt_foundation.py::compute_daily_signals`,
  `"if ... < 5: return None"`.
- **`medido`** — hay una medición con **fecha**, y `fuente` dice qué se
  midió. Hay un test que rechaza un `medido` sin fecha: una etiqueta más
  prestigiosa que `provisional` y con el mismo contenido es peor que no
  tener el campo.
- **`provisional_sin_evidencia`** — el valor está ahí y funciona, pero nada
  externo lo respalda. Incluye tanto las elecciones de ingeniería
  documentadas (`DEFAULT_MAX_DAYS`, `EPSILON_BREAKEVEN`) como las que el
  propio código declara sin calibrar (`HYBRID_WEIGHT_GLOBAL`).

**Citar una fuente no es tener evidencia**, y la distinción cambia entradas
concretas. `SUCCESS_SCORE_THRESHOLD` cita `spel_bayesian_core.py`
textualmente (`">= 850/1000 trayectorias con gold_score > 0.85"`) y aun así
es `provisional_sin_evidencia`: el docstring de `core/monte_carlo.py:34`
dice que el número está *"pendiente de calibración post Gate R30"* y sin
backtest. El legacy tenía el valor; nadie lo midió. Lo mismo
`DEFAULT_SENSITIVITY_MAP` y `FALLBACK_SENSITIVITY`.

### El estado hoy

| evidencia | constantes |
|---|---|
| `provisional_sin_evidencia` | **37** |
| `legacy_citado` | **16** |
| `medido` | **2** |

Las dos `medido` son `GDELT_BASE_URL` y `FOLLOW_REDIRECTS`, y lo que se
midió fue el **comportamiento de la fuente** (GDELT migró a HTTPS), no un
valor numérico. **Ningún número del sistema tiene evidencia medida.** Eso no
lo introduce este registro: lo hace visible.

## Normalización de valores

20 de las 55 no son literales JSON. La conversión vive en
`tests/test_registro_constantes.py::normalizar()` — es una regla de
verificación, y ponerla en un módulo del paquete la haría parte del runtime
del motor sin servirle a nadie en producción.

| Python | JSON | cómo compara el test |
|---|---|---|
| `tuple` | lista | el orden significa |
| `frozenset` / `set` | lista ordenada | como conjunto |
| `dict` con claves `Enum` | objeto con `Enum.value` de clave | por clave normalizada |
| `Path` | string POSIX | igualdad de string |
| `None` | `null` | igualdad estricta |
| `Enum` suelto | su `.value` | igualdad |
| `categoria: "derivada"` | `expresion` en vez de `valor` | **no compara valor** |

El renglón del `Enum` suelto es una extensión: `DRIVE_STREAMS` es un
`frozenset` **de** `Enum`, ni un dict con claves Enum ni un frozenset de
strings.

El JSON guarda los conjuntos **ordenados** para que el archivo sea estable
en el diff, pero el orden no es parte del valor y el test no lo exige.

## Agregar una constante

1. Escríbela en su módulo, con su comentario `#:` como siempre.
2. Corre `pytest tests/test_registro_constantes.py`. **Va a fallar**,
   nombrando tu constante con archivo y línea. Eso es el mecanismo, no un
   estorbo.
3. Agrega su entrada a `constantes.json` con los siete campos. Si no sabes
   de dónde salió el número, `provisional_sin_evidencia` — es la respuesta
   honesta y el registro la admite.
4. `usado_en` se llena con los llamadores reales (`modulo.funcion`), no con
   los que deberían usarla.

Si cambias el **valor** de una constante, actualiza el registro **en el
mismo commit**. El test falla mostrando el registrado y el real.

## `execution/`

Se recorre, no se toca. Hoy aporta **cero** constantes de módulo: sus
umbrales son parámetros de constructor
(`CircuitBreaker(starting_equity=..., max_consecutive_losses=...)`), que es
mejor diseño y por eso no hay nada que registrar. Eso está verificado y
fijado en un test — el cero es un hecho, no un barrido que no llegó hasta
ahí. Cuando Fase 4 descongele el paquete y aparezca una constante, la
cobertura la va a exigir sola.
