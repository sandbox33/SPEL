# SPEL — ESTADO DEL PROYECTO

> **Este archivo es la única fuente de verdad sobre en qué fase estamos.**
> Se lee primero en cada chat nuevo. Se actualiza al final de cada sesión, nunca a mitad.
> Si algo en un chat contradice lo que dice acá, este archivo gana — a menos que
> el chat verifique contra GitHub real y lo actualice con evidencia.

**Última actualización:** 18 ago 2026 — cierra 17 días de desactualización real. Este
archivo llevaba congelado en el patch 0002 (14 ago) mientras `main` avanzaba hasta el
0031 sin que nadie lo reflejara acá — el mismo hallazgo de gobernanza que ya se había
señalado el 17 ago no se había corregido en el propio git hasta este patch.

**Ver también:** `FASE2_NOTAS_ARQUITECTURA_MODELO.md` (raíz del repo) — glosario y
opciones de arquitectura de modelo (LSTM vs. árboles/ensambles), separado de este
archivo a propósito para no mezclar "estado actual" con "notas de investigación".

---

## 🚦 SEMÁFORO DE FASES

```
FASE 1 — ingestion/ + core/scoring.py     🟢 INGESTION LISTA · 🟡 ORQUESTACIÓN PARCIAL
FASE 2 — Diagnóstico + entrenamiento LSTM  ⚪ NO INICIADA — ver Incógnita #1
FASE 3 — visualization/ (grafo)            ⚪ NO INICIADA
FASE 4 — execution/ + Deriv real           🟡 Actuator confirmado, gate F2 firme
FASE 5 — Escala (Supabase, Flet)           ⚪ NO INICIADA
FASE 6 — Motor streaming multi-timeframe   🟡 Infraestructura lista, señal sin construir
```

---

## 📍 MÓDULOS REALES EN `main` HOY (verificado, no listado de memoria)

242 → 392 tests desde el 17 ago (65 tests nuevos en un día de trabajo). Todo lo de
abajo corrió en clon 100% ajeno, venv limpio desde `requirements.txt`, 10 corridas
seguidas sin intermitencia.

| Módulo | Qué hace | Tests | Estado |
|---|---:|---:|---|
| `core/scoring.py` | vitality_tesla, nash_frozen_7d, godel_active, gold_score_bma, classify_gdelt_event (+ fix EURUSD) | 116 | ✅ |
| `core/monte_carlo.py` | Validación GBM — NO entrena, simulación pura en cada llamada | 22 | ✅ |
| `core/price_signals.py` | te_score (proxy TE) + backbone_score (EMA20/63) | 12 | ✅ |
| `ingestion/adapters.py` | DerivAdapter + TwelveDataAdapter + contrato de datos (`drop_unclosed_candles`, `validate_ohlcv_schema`, `AdapterResult` con metadata de calidad) | 93 | ✅ |
| `ingestion/gdelt.py` + `_aggregation` + `_series` | Pipeline GDELT completo, persistencia JSONL | 41 | ✅ |
| `ingestion/training_dataset.py` | Une OHLCV + serie GDELT, forward-fill, coverage_ratio explícito | 7 | ✅ |
| `orchestration/cycle.py` | Corre vitality/nash/godel sobre 5 activos a la vez | 10 | ✅ |
| `execution/circuit_breaker.py` + `execution_guard.py` | Guardrails duros — congelados hasta F4 | 31 | ✅ |
| `governance/persistence.py` + `secrets.py` | 4 streams, SecretKey único (13 claves) | 28 | ✅ |
| `tools/heartbeat.py` + `.github/workflows/heartbeat.yml` | Trigger `schedule:` real — **desactivado a propósito**, ver Fase 6 | — | ✅ código, 🔴 apagado |

**No existe todavía, confirmado por ausencia real (no supuesto):** grep de
`^class.*Adapter` en `ingestion/adapters.py` da **dos** implementaciones concretas de
`BaseAdapter` — `DerivAdapter` y `TwelveDataAdapter` — más la base abstracta y las
excepciones. Cero `AlpacaAdapter`, cero `TiingoAdapter`. De los 4 activos del legacy,
TwelveData cubre BTC (`BTC/USD`) y abre la puerta a acciones (`AAPL` verificado); **XAU
y NIFTY50 siguen sin fuente**, y XAU específicamente quedó fuera del mapa por falta de
evidencia de que el plan gratuito lo cubra, no por olvido. Ningún trainer de LSTM.
Ningún ruteo de órdenes de ningún tipo.

**Los fixtures de TwelveData ya son capturas reales** (2026-08-23, literales): EUR/USD,
BTC/USD y AAPL en 1day. Reemplazan a los sintéticos con los que se escribió el adapter, y
**trajeron un hallazgo que desmiente el supuesto anterior: BTC/USD NO trae `volume`.** De
los tres instrumentos, el único con volumen es AAPL — la regla intuitiva "forex no,
cripto y acciones sí" es falsa.

El adapter no necesitó ni un cambio, y eso no es suerte: `volume_available` se deriva de
si la clave vino en la respuesta, nunca de una regla por clase de activo. Verificado por
mutación —sustituir la derivación observada por una regla declarada deja **4 de 28 tests
en rojo**, y el primero en caer es el que parsea la captura real de BTC. Una regla
declarada habría marcado BTC con volumen disponible y el relleno `0.0` habría entrado al
pipeline como si fuera un dato.

Quedan dos fixtures sintéticos, marcados como tales y con motivo: un contrafáctico
deliberado (forex *con* volumen — su valor está en que no puede existir en la realidad
observada, y es el que atrapa la regla declarada en la dirección inversa) y uno intradía
por necesidad (las tres capturas son diarias, y hay dos comportamientos intradía que
probar que dependen del argumento `timeframe`, no del payload).

🟡 **Sigue amarillo, no verde**, y por una razón más chica que antes: el test `live`
(marker `live`, skipif sobre la credencial) todavía no corrió nunca con clave real. Lo que
falta confirmar contra la API viva es el camino que ningún fixture ejercita — el cliente
httpx que el adapter abre y cierra solo (los tests offline inyectan el suyo), la
autenticación aceptada de verdad, y la forma intradía.

**Hallazgo relacionado — no hay ningún punto de composición.** No es que falte un
adapter: falta el lugar donde algo se arma y corre de verdad. Verificado por grep,
las tres mitades del mismo hueco: (1) nada instancia un adapter fuera de tests —
`DerivAdapter(` no aparece en ningún archivo de producción, solo su propia definición
de clase; (2) `load_secret()` no se llama desde producción — los únicos dos hits fuera
de `tests/` son menciones en docstrings de `governance/persistence.py`, no llamadas;
(3) los secretos de proveedores están registrados en `SecretKey` pero ningún workflow
los inyecta — cero `secrets.` y cero `env:` en `.github/workflows/`. Es decir: las
piezas existen y están probadas, pero nadie las conecta todavía. Eso es lo que
`orchestration/` tiene que cerrar, y es una brecha distinta de "faltan adapters".

---

## 🔒 CONTRATO DE DATOS OHLCV

Tres reglas que cualquier adapter nuevo tiene que respetar. No son estilo — cada una
existe porque su ausencia produce un fallo silencioso, que es la clase de bug que ya
costó meses en este proyecto.

**1. `require_closed` tiene semántica condicional, y es a propósito.** No significa
"siempre valida el cierre", significa "valida el cierre cuando es posible saberlo". Con
`granularity_s` presente rechaza velas abiertas; con `granularity_s=None` omite *solo*
esa verificación — columnas, tipos, UTC, orden, NaN y OHLC siguen activas. La
granularidad **nunca se infiere del espaciado entre timestamps**: un dataset con huecos
legítimos (fin de semana forex, feriados) daría una inferencia equivocada, y una
granularidad equivocada rechaza velas buenas o acepta abiertas. El call site que obliga
a que `None` sea válido es real: `ingestion/training_dataset.py:119` valida un dataset ya
construido, sin acceso a la granularidad original.

**2. `df.attrs` es transporte de UN SOLO SALTO, nunca almacenamiento.** El adapter
escribe la metadata de calidad en `attrs`; `AdapterChain` la levanta a `AdapterResult`
inmediatamente después del fetch, y ahí muere. Medido en pandas 3.0.5, no supuesto:

| Operación | `attrs` sobrevive |
|---|---|
| `copy()` | ✅ |
| `sort_values()` | ✅ |
| `concat()` | ✅ |
| `reset_index()` | ✅ |
| `merge()` | 🔴 **se pierde** |

`training_dataset.py` hace exactamente un join OHLCV↔GDELT. Usar `attrs` como
almacenamiento durable perdería la procedencia justo donde más importa —al armar el
dataset de entrenamiento— y en silencio, sin excepción ni warning. Por eso los campos
viven en el dataclass.

**3. `pandas>=2.2,<4` es la única dependencia pineada.** `attrs` es API documentada como
experimental por pandas y el diseño depende de su comportamiento exacto; sin pin, una
resolución distinta en CI podría cambiarlo sin que nadie toque el código. `numpy`,
`httpx`, `websockets` y `pytest` siguen sin pinear — nada del contrato depende de ellos.

**Red de seguridad armada.** `DerivAdapter.fetch_ohlcv()` llama a `validate_ohlcv_schema()`
con `granularity_s` aunque `_to_dataframe()` ya corrió el filtro: es redundante a
propósito, y en el camino normal no debería dispararse nunca. Si dispara, el filtro falló
o alguien lo removió. Verificado por mutación: quitar el argumento del call site deja
**64 de 65 tests en verde**; el único que lo detecta es
`test_deriv_arma_la_red_de_seguridad_pasando_granularity_s`, que verifica el argumento y
no el efecto justamente por eso.

**Nomenclatura — cuatro términos que no son sinónimos:**

| Término | Qué es | Ejemplo |
|---|---|---|
| `source` | el PROVEEDOR de los datos | `"deriv"`, `"twelvedata"` |
| `symbol` | el INSTRUMENTO | `"EURUSD"`, `"VOL75"` |
| `adapter_name` | qué adapter produjo el resultado (`AdapterResult`) | `"deriv"` |
| `is_fallback` | vino de una fuente de respaldo — **distinto de `is_degraded`**: un respaldo puede funcionar perfecto | `True` + `is_degraded=False` |

Ni `source` ni `symbol` deben recibir jamás algo derivado de un secreto: los dos se
escriben en el log.

---

## 🔬 FASE 2 — el bloqueo real, ahora entendido con precisión

No es "escribir 3 funciones". Auditado contra el legacy real esta sesión:

- `te_score` y `backbone_score`: **listos**, portados con 2 bugs reales corregidos
  (`core/price_signals.py`).
- `godel_score`: depende de `val_dir`, salida de un **LSTM entrenado** — arquitectura
  canónica "Regla 13", **bloqueada por guardián real** (`enforce_lstm_architecture()`
  en el legacy lanza `RuntimeError` ante cualquier desvío de `input_size=20,
  hidden_size=64, num_layers=1` — existen 14 checkpoints `.pt` atados a esa forma
  exacta). No hay nada que portar hasta que ese modelo aprenda algo real.
- Accuracy base histórica confirmada (legacy, `LSTM_BASE_ACCURACY`): NVDA 0.550, BTC
  0.528, XAU 0.547, NIFTY50 0.625 — el "~0.50" que bloquea Fase 4 puede ser ese mismo
  techo bajo original, no necesariamente una regresión nueva.
- Causa raíz real del estancamiento (Altair, 18 ago): parquets fuente con columnas de
  fecha en formatos distintos entre sí ('/' vs '-') — datos no normalizados
  alimentando el entrenamiento sin detectarse a tiempo. **Ya no puede repetirse en la
  parte nueva**: Deriv entrega epoch (sin ambigüedad de formato posible),
  `validate_ohlcv_schema()` rechaza cualquier timestamp que no sea UTC estricto y
  ordenado — verificado con test de regresión directo.
- Clase de bug MÁS AMPLIA encontrada en el legacy (`spel_trainer_audit.py`,
  BUG-LA-01): normalizar con estadísticas del dataset completo en vez de solo train,
  o mezclar datos temporales antes del split. Documentado en
  `ingestion/training_dataset.py` para cuando se escriba el trainer — no resuelto
  todavía porque ese código no existe.
- `ingestion/training_dataset.py` ya construido: une OHLCV + serie GDELT con
  forward-fill, listo para alimentar cualquier arquitectura que se elija.

**Ver `FASE2_NOTAS_ARQUITECTURA_MODELO.md` para las opciones reales (LSTM vs.
Random Forest/XGBoost/etc.) — nada decidido todavía, es investigación, no un plan.**

---

## 🗂️ CÓDIGO LEGACY — sin cambios, ya estaba correcto

```
archive/legacy-pre-20260813              → 74 módulos originales, intactos
archive/dashboard-data-pre-20260813      → cache de GitHub Actions
archive/feature-cache-pre-20260813       → cache de features
archive/model-cache-pre-20260813         → cache de modelos (14 checkpoints .pt)
archive/limpieza-legado-99-pre-20260813  → última limpieza previa al reinicio
```

Regla fija sin excepción: nada bajo `ingestion/`, `core/`, `execution/`,
`orchestration/` o `visualization/` importa desde ninguna rama `archive/*`.

---

## ⚠️ GOBERNANZA DE DOCUMENTOS — hallazgo del 17 ago, TODAVÍA sin resolver

Sigue pendiente, no se resolvió solo con el tiempo: `SPEL_PERSISTENCE_STATE.md` y
`SPEL_PERSISTENCIA_v2.md` en Drive raíz siguen ahí, sin archivar, compitiendo con
este archivo. Este patch reemplaza el contenido de `SPEL_PERSISTENCE_STATE.md` con
un espejo literal de este archivo (marcado como espejo, no editable ahí) — pero
`SPEL_PERSISTENCIA_v2.md` sigue sin archivar. Acción pendiente para la próxima
sesión, no lo resolví en esta.

---

## ❓ INCÓGNITAS REALES — sin resolver, no inventadas para llenar espacio

1. **¿GitHub Actions ya verificó una descarga real de GDELT?** Nunca confirmado —
   cada intento de chequear la pestaña Actions vía API chocó con rate limit sin
   autenticar. Sigue siendo la verificación más urgente pendiente.
2. **¿GDELT tiene cobertura completa de 2026?** El auditor legacy
   (`spel_auditoria_total.py`) tenía un chequeo específico para esto
   (`GDELT_GAP_2026`) — no se corrió el equivalente contra el pipeline nuevo.
3. **¿El motor GDELT (Fases 1-5) sigue siendo el principal, o el motor rápido
   (Fase 6) cambia la prioridad?** Pregunta abierta desde el 17 ago, todavía sin
   que Altair la confirme.
4. **`p90_entropy_global_default`** (necesario para `godel_active` vía
   `orchestration/cycle.py`): sin valor por defecto a propósito — el propio
   `compute_adaptive_percentile` documenta que P90 "no tiene default legacy
   confirmado". Cada llamada real necesita decidir qué número usar.
5. **DEU como único proxy de "Eurozona/BCE"** en `GOBIERNO_COUNTRY_FILTERS`: ¿alcanza
   solo, o hace falta sumar FRA/ITA? Sin backtest, documentado como pendiente desde
   que se escribió `classify_gdelt_event`.
6. **GBPUSD/USDJPY/USDCHF/AUDUSD** ya tienen precio real (`DerivAdapter`) pero NO
   tienen clasificación GDELT — `FX_GOBIERNO_ONLY_ASSETS` solo cubre EURUSD. Decidir
   qué país no-USA representa a cada banco central (BoE/BoJ/SNB/RBA) sigue pendiente.
7. **Transfer Entropy real (Schreiber) vs. el proxy portado**: existe una versión más
   rigurosa en `spel_math_engine.py` (con Hurst, backend `pyinform`), pero ese archivo
   tiene 177 referencias rotas a numpy/polars sin auditar — no se tocó esta sesión.
8. **Índices de Volatilidad (VOL10-VOL100)**: tienen precio real, cero vía de scoring
   — GDELT no aplica por diseño (Fase 6), pero no existe todavía una alternativa
   técnica pura para ellos. `orchestration/cycle.py` los excluye a propósito.
9. **Isolation Forest**: el legacy ya lo usaba para detectar anomalías en la entropía
   GDELT (`PARAM_ISO`, umbral 0.6) — es de la misma familia que Random Forest. Nunca
   se portó al repo nuevo. Ver `FASE2_NOTAS_ARQUITECTURA_MODELO.md`.
10. **Alpaca**: cero código en el repo nuevo, solo la decisión de mantenerlo en modo
    paper (17 ago). Cuando el amigo de Altair complete la verificación con Banco
    Pichincha, hace falta construir el adapter desde cero — no existe ni un stub.

---

## 🖥️ SPEL_Control_Panel.ipynb — mejoras concretas para producción

No auditado línea por línea esta sesión (34.6 KB, no se justificó el costo de leerlo
completo todavía) — estas son mejoras identificadas por los síntomas reales que ya
aparecieron, no una revisión exhaustiva:

1. **Menú 4 (aplicar patch)**: defenderse de `.git/rebase-apply still exists`
   corriendo `git am --abort` automáticamente si ese directorio existe, antes de
   intentar aplicar — ya pasó una vez esta sesión, se resolvió reintentando a mano.
2. **Menú 7 (ver estado/historial)**: que lea el contenido real de este archivo en
   vez de reconstruir el estado a mano desde git log — reduce el riesgo de que
   ESTADO.md y lo que el panel muestra diverjan otra vez.
3. **Descarga de datos históricos para entrenamiento** (nuevo requisito, 18 ago):
   el flujo actual de `DerivAdapter` trae velas recientes para scoring en vivo, no
   un backfill masivo de meses/años para entrenar. Antes de que Fase 2 pueda
   arrancar de verdad, hace falta confirmar si `ticks_history` de Deriv soporta
   pedir historia profunda con `count` alto, o si hace falta paginar con `start`/`end`
   — no verificado todavía, es la primera pregunta técnica real cuando se retome
   Fase 2.
4. **Separar "modo patch" de "modo entrenamiento"**: cuando exista un trainer real,
   correrlo desde el mismo panel de aplicar-patches mezcla dos flujos de trabajo
   distintos (uno es minutos, el otro puede ser horas). Mejor un menú aparte, no
   una opción más en la lista actual.

---

## ▶️ PRÓXIMO PASO CONCRETO

**Confirmar la Incógnita #1** (¿corrió ya GDELT real en GitHub Actions?) desde la
pestaña Actions directamente — es lo único que decide si Fase 1 se puede dar por
cerrada del lado de ingestion, antes de seguir construyendo sobre esa base sin
saberlo con certeza.

---

## 📝 CÓMO ACTUALIZAR ESTE ARCHIVO

Al final de cada sesión de código (no a mitad):
1. Actualizar la tabla de módulos con lo que se verificó contra GitHub real.
2. Mover cualquier decisión nueva a `decision-log.md`, no acá — este archivo es
   estado, no bitácora de decisiones.
3. Actualizar "Próximo paso concreto" — una sola cosa, no una lista de deseos.
4. Commitear este archivo junto con el código de esa sesión, mismo commit o el
   siguiente inmediato — la lección del 17 ago fue exactamente no hacer esto.
5. Nunca dejar este archivo diciendo algo que no se verificó — si algo quedó a
   medias, decirlo explícitamente como 🟡, no como ✅.
6. Si este archivo lleva más de ~5 días sin tocarse mientras hay patches nuevos en
   `main`, esa es la misma señal de circularidad que ya dispara "cortar y empezar de
   nuevo con foco" — trátese como tal.
