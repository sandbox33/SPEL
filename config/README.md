# `config/` — el stream CONFIG

`governance/persistence.py` declara el stream `CONFIG → "config/"` desde el
patch 0010: *"versionado, con historial de git, revisable en PRs"*. El
directorio no existía. Esto es su primer contenido.

## `constantes.json` — qué es

El registro de **las 108 constantes de módulo** de `core/`, `ingestion/`,
`orchestration/`, `governance/`, `execution/` y `tools/`. Una entrada por
constante, con su valor, de dónde salió, qué tan sostenido está ese valor, y
si cambiarlo cambia algo.

`tools/` entró el 20-sep-2026, con 36 constantes más. No es alcance
decorativo: sus valores deciden **qué mide el sistema sobre sí mismo**, y uno
que se despega de producción hace que un reporte diga medir algo que no
midió. Este repo ya tuvo ese defecto —`measure_godel_samples.py` midiendo un
P90 contra una máscara que operaba en P66— y no lo notó nadie, porque los dos
números existían y ninguno era absurdo.

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

## `afecta_resultado`

Booleano **obligatorio**, y **ortogonal a `categoria`**. El criterio es:
*¿cambiar este valor cambia algún número o alguna rama que el sistema
produce, o solo cambia texto que un humano lee?*

Hace falta aparte porque `categoria` describe la NATURALEZA del valor y no su
efecto, y las dos cosas se cruzan de formas que sorprenden. `CORE_COUNTRY_FILTERS`,
`GOBIERNO_COUNTRY_FILTERS`, `FX_GOBIERNO_ONLY_ASSETS`, `_DERIV_SYMBOL_MAP` y
`DEFAULT_CYCLE_ASSETS` son todas `etiqueta` —son listas de identificadores,
no magnitudes— y cambiar cualquiera cambia qué eventos pasan el filtro, qué
activos corre el ciclo o qué instrumento se le pide al proveedor.

**Sin este campo, alguien que filtrara por `categoria == "parametro"` para
saber qué tocar con cuidado se saltearía exactamente las que más mueven el
sistema.**

`false` es excepcional: hoy son **7 de 108**, y hay un test que falla si pasan
de 8. El caso que más enseña es `GODEL_CRITERIA_VERSION`: el sello se
registra en `AssetCycleResult` y **nada ramifica sobre él** —el propio campo
documenta que "la comprobación no existe todavía"—, así que hoy es `false` y
pasa a `true` el día que alguien compare la versión de un artefacto contra la
del módulo y recalcule si difieren.

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
| `dataclass` | objeto con sus campos | por campo |
| `categoria: "derivada"` | `expresion` en vez de `valor` | **no compara valor** |

Los dos últimos renglones de valor son extensiones, y las dos hicieron falta
de verdad: `DRIVE_STREAMS` es un `frozenset` **de** `Enum` (ni un dict con
claves Enum ni un frozenset de strings), y
`tools.provider_coverage.PROVIDERS` es un dict de dataclasses —un `repr()` de
dataclass en el JSON sería ilegible y se rompería con cualquier cambio de
formato de `repr`—. La segunda la encontró el propio test al ampliarse a
`tools/`.

El JSON guarda los conjuntos **ordenados** para que el archivo sea estable
en el diff, pero el orden no es parte del valor y el test no lo exige.

## Agregar una constante

1. Escríbela en su módulo, con su comentario `#:` como siempre.
2. Corre `pytest tests/test_registro_constantes.py`. **Va a fallar**,
   nombrando tu constante con archivo y línea. Eso es el mecanismo, no un
   estorbo.
3. Agrega su entrada a `constantes.json` con los ocho campos. Si no sabes
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


---

# `calibracion_activos.json` — el umbral por activo

Lo produce `tools/calibrar_umbral_entropia.py`. Es el **primer parámetro del
sistema con procedencia**: el registro de arriba deja a la vista que ningún
valor numérico de SPEL tiene evidencia medida, y este es el primero que sí.

## El umbral publicado NO es el de producción

Para un activo con historia suficiente manda la **ventana móvil de 252 días**
que `compute_godel_p66()` recalcula en cada corrida, y el valor de este
archivo **nunca se lee**. Se publica por dos motivos, y ninguno es "usarlo
como umbral":

1. **Respaldo de arranque en frío.** `run_scoring_cycle` exige
   `p66_entropy_global_default` sin default, y el único número que circulaba
   era un fixture de `tests/test_scoring.py` (1,19).
2. **Evidencia de la distribución.** Que BTC y XAU difieran en 0,167 y por
   qué es un hecho del sistema que no estaba escrito en ningún lado.

La advertencia viaja en el docstring, en el reporte y dentro del propio JSON.
Las tres, porque las tres se leen por separado: quien copie el archivo a otro
repo no va a leer el docstring.

## Por qué por activo

| activo | p66 | n |
|---|---|---|
| BTC | 1,131801 | 4.880 |
| XAU | 1,298946 | 4.879 |

La diferencia de **0,167** no es una propiedad de los activos: sale de
`CORE_COUNTRY_FILTERS["XAU"] = ()`. Sin filtro de país, XAU agrega el dataset
GDELT completo, y la entropía de Shannon crece con la riqueza del soporte.
**El umbral es por activo por construcción del filtro.**

## Veredicto

| `n_validos` | `estado` | publica umbral |
|---|---|---|
| ≥ 252 | `medido` | sí |
| 100 – 251 | `parcial` | sí, marcado |
| < 100 | `insuficiente` | no |
| serie ausente | `sin_serie` | no |

`n_validos` cuenta **días con entropía**, nunca filas: un día con
`insufficient_events=True` trae `entropy_shannon=None` y no entra a un
percentil. Publicar el conteo de filas inflaría la confianza en la medición
con días que no aportaron ningún número.

El `sha256_serie` hashea el par *(día, entropía)* de los días medidos. Es lo
que permite saber después si una medición corresponde a la serie que hoy está
en disco o a una anterior — **sin eso el archivo envejece sin avisar**.

## Quién lo genera

**No se commitea desde un sandbox sin datos.** El archivo del repo lo genera
Altair desde Colab, donde la serie real está montada:

```
python tools/calibrar_umbral_entropia.py --write
```

Sin `--write` el tool es read-only y no toca disco. Con la serie ausente sale
con **exit 0** reportando `sin_serie` para los cinco activos: que funcione sin
datos es parte del contrato, no una degradación.
