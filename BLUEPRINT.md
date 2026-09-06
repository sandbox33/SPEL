# BLUEPRINT — de repo limpio a motor generando ingresos

Sin ambigüedad, en orden de dependencia real. Cada fase tiene un criterio de
"terminado" verificable — no "se ve bien", sino un número o un test que pasa.

---

## Por qué se reinició (el argumento completo, una vez, para no repetirlo)

Cuatro sesiones de auditoría real (no superficial) encontraron, con
evidencia verificada, no supuesta:

- **2 archivos `spel_commons.py` distintos**, con `sha12()` de firma
  incompatible — uno coincidía con `sha_detective.py`, el otro no.
- **6 versiones de `axiom_master.xml`** sin reconciliar, GitHub con la más
  vieja (S52), Drive con la más nueva (S75) — 23 sesiones de diferencia.
- **5 implementaciones de "mandar un mensaje a Telegram"**, cada una con su
  propio manejo de errores.
- **Una inconsistencia real de nombre de variable** (`TELEGRAM_CHAOS` vs.
  `TELEGRAM_CAOS`) que yo mismo diagnostiqué mal una vez antes de
  corregirlo — la clase de error que la duplicación produce con seguridad,
  tarde o temprano.
- **`append_bma_history_robust()` nunca se llamaba** — la protección
  anti-spike de KL corría en la práctica sobre un historial vacío, sin que
  nadie lo notara porque el código "existía" y parecía completo.
- **Parches escritos y nunca integrados** (`FOREX_CHAOS_ENDPOINT`,
  `spel_memory_patch.py`) — documentados como "resueltos" en su propio
  docstring, verificado con grep que no lo estaban.
- **Un entrenamiento real con precisión de validación en ~0.50** — sin
  señal de aprendizaje, en un sistema que llevaba meses de trabajo encima.

Ninguno de estos es un problema de "falta pulir". Son la razón concreta por
la que el proyecto no llegaba de principio a fin. Se archivó, no se perdió
— vive completo en `archive/legacy-pre-20260813` y en Drive
(`SPEL_ARCHIVO_LEGADO_2026-08-13/`).

---

## Objetivo — sin ambigüedad

Generar ingresos reales, de forma sistemática y automatizada, empezando con
$10 en Deriv, escalando cuando el sistema demuestre que funciona con
capital chico antes de arriesgar más. No es "algún día" — es el criterio
de éxito de cada fase de abajo.

---

## Fase 1 — Portar lo que ya funciona (esta semana)

**Qué se porta**, desde `archive/limpieza-legado-99-pre-20260813` (código
verificado con tests reales, no legado roto):
- `base_adapter.py` → `ingestion/adapters.py` — el patrón de cadena con
  degradación elegante, ya probado.
- `DerivAdapter` → `ingestion/deriv.py` — ya wireado y probado contra
  rechazo real HTTP 403 (degrada limpio).
- `secrets_env_loader.py` + la consolidación en `spel_commons` →
  `governance/secrets.py` — una sola fuente de credenciales.
- La lógica de `spel_bayesian_core.py` (BMA, Gödel, kill-switch) →
  `core/scoring.py` — reescrita limpia, sin las 5 copias de utilidades.

**Qué NO se porta tal cual**: nada que dependa de rutas hardcodeadas de
Colab (`/content/drive/MyDrive/ORDEN/SPEL 3.0`) — se usa detección de
entorno desde el día uno en el código nuevo.

**Criterio de terminado**: `ingestion/` trae un OHLCV real de Deriv y un
GDELT real, `core/scoring.py` calcula un Gold Score real a partir de eso,
con un test que corre en CI y pasa.

> **🔵 FASE 1 CERRADA — 4-sep-2026, con resultado NEGATIVO medido.** El criterio de
> arriba se cumplió en su parte de pipeline: ingestion GDELT y OHLCV reales, scoring
> corriendo, 661 tests en CI. `gold_score` sigue siendo `None` por diseño (le faltan
> inputs que ningún módulo produce todavía — ver `GOLD_SCORE_BLOCKED_REASON`), así que
> esa parte del criterio no se cumplió y no se va a forzar.
>
> Lo que cerró la fase no fue el checklist sino su pregunta de fondo: **la máscara
> Gödel no discrimina dirección** (p = 0,68 en BTC, 0,42 en XAU, con n de 1.211 y 823
> — o sea, con potencia). Sí discrimina magnitud en BTC (ratio 1,246, p = 3,4×10⁻⁹).
> Ver la sección de Fase 2, reorientada por ese resultado, y `decision-log.md` para el
> método completo.
>
> Cerrada ≠ exitosa. El pipeline es correcto y está medido; la hipótesis que ese
> pipeline existía para probar, no.

### Estado real, auditado (no estimado) — post-patch 0017

**`core/scoring.py`: cumplido y más.** 8 funciones puras, cada una auditada
contra fuente legacy exacta (no memoria de sesión, no supuesto): vitality_tesla
(cascada B→A→C), nash_frozen_7d (con fix confirmado en 500 muestras —
la primera versión inflaba con micro-ruido), mass_panic_index (síntesis
de 2 fuentes en conflicto, marcado EXPERIMENTAL), entropy_fibonacci_lags
+ entropy_delta_lags, gold_score_bma (BMA real, Regla 13, con 3 kill
signals cuya prioridad se confirmó empíricamente), classify_gdelt_event,
compute_adaptive_percentile. 201 tests, benchmark A/B/C entre las 3
lógicas de kill signal.

**`ingestion/`: portado y en uso.** DerivAdapter con fetch_async() nativo
(la trampa de reentrancia de asyncio.run() documentada en sesiones
anteriores, resuelta y verificada con contador real), TwelveDataAdapter
con fixtures de capturas reales, y el contrato de datos OHLCV.

**~~GDELT: 0% portado.~~ CORREGIDO el 6-sep — la afirmación era falsa.**
El pipeline existe, corre y produjo los datos con los que se cerró Fase 1:
`ingestion/gdelt.py` (223 líneas), `gdelt_aggregation.py` (170) y
`gdelt_series.py` (167) — descarga, agregación diaria por activo y
persistencia JSONL, 41 tests. `ingestion/training_dataset.py` une esa
serie con el OHLCV. La entropía histórica de BTC y XAU (3.998 días por
activo) se importó con `tools/import_gdelt_entropy.py`.

Lo que sigue sin portarse de esa familia es el harvester masivo
(`spel_bulk_harvester.py`, 1083 líneas, corre sobre BigQuery — descartado
por costo) y la ingestion a nivel de evento individual que
`compute_mass_panic_index` necesitaría para su fórmula legacy original.
Eso es un subconjunto, no el pipeline entero.

**`execution/` + `governance/`:** circuit_breaker, execution_guard,
secrets (detección de entorno real, no rutas fijas), persistence (4
streams, con el mismo fix de detección de entorno tras un hallazgo de
auditoría — DRIVE_ROOT estaba hardcodeado, corregido).

**~~Orquestador: 0% portado.~~ CORREGIDO el 6-sep — la afirmación era
falsa.** `orchestration/cycle.py` (266 líneas, 19 tests) corre el ciclo de
scoring sobre los 5 activos con clasificación GDELT: vitality_tesla,
nash_frozen_7d y godel_active, con el criterio sellado en
`godel_criteria_version`.

Lo que NO tiene todavía, y es lo que distingue "hay orquestador" de "hay
main loop": no exporta JSON de estado, no tiene watchdog, no calcula
`gold_score` (bloqueado por inputs que no existen — ver
`GOLD_SCORE_BLOCKED_REASON`) y nada lo dispara periódicamente. Del
`spel_orchestrator_v10.py` legacy (735 líneas) está portada la parte de
scoring, no la de operación continua.

`.github/workflows/tests.yml` corre la suite en cada push, pero no
orquesta nada del sistema — es CI, no el orquestador de trading.

**Dashboard: 0% portado, a propósito.** Los 8 archivos de UI legacy
(main_ui_vFinal.py, dashboard_fx.py, spel_hud.py, spel_graph_tab.py,
spel_scalping_tab.py, spel_inventory_dashboard.py, main_ui.py,
spel_dashboard.py — 7,020 líneas) son 100% Streamlit, confirmado con
grep, sin excepción. Streamlit está fuera de las restricciones de
plataforma actuales (Android, Colab+Drive). No se portan — Fase 3 ya
decidía construir algo nuevo y más simple, no re-crear estos 8
archivos.

## Fase 2 — REORIENTADA (6-sep-2026): dimensionamiento, no dirección

**La hipótesis 3 quedó confirmada, con datos reales.** Este plan listaba tres
hipótesis para explicar la accuracy de ~0.50, y la tercera —"la señal no
está ahí"— era la más incómoda. La validación del 4-sep la contestó, y no
hizo falta entrenar nada para hacerlo.

Medido sobre datos reales, criterio `4.0.0-entropy_state_p66`, con `n` post-máscara
de **1.211** (BTC, 5/5 folds) y **823** (XAU, 4/5) — o sea con potencia suficiente,
no por falta de muestras:

| | dirección (chi²) | magnitud (Mann-Whitney) |
|---|---|---|
| BTC | 51,81% vs 52,53%, **p = 0,68** | ratio **1,246**, **p = 3,4×10⁻⁹** |
| XAU | 50,98% vs 52,60%, **p = 0,42** | ratio 1,115, p = 0,56 |

**La máscara no discrimina dirección. Sí discrimina magnitud, y solo en BTC.**
Coincide con la literatura sobre índices de incertidumbre basados en noticias, que
predicen magnitud y no signo (el EPU correlaciona 0,73 con el VIX). Detalle completo
en `decision-log.md`.

### Qué cambia en el plan

**El modelo NO debe ser un clasificador direccional filtrado por entropía.** Esa era
la arquitectura implícita: entrenar un LSTM para predecir el signo del retorno, usando
la máscara Gödel para quedarse con los días "predecibles". La medición dice que esos
días no son direccionalmente más predecibles que el resto.

**La vía con fundamento medido es dimensionamiento de posición.** Un régimen que
multiplica la volatilidad por 1,25 es información accionable para decidir *cuánto*
arriesgar. No lo es para decidir *de qué lado*.

Eso reordena lo que viene: la pregunta ya no es "¿qué arquitectura predice mejor el
signo?" sino **"¿qué métrica valida un modelo de dimensionamiento?"** — porque la
accuracy direccional deja de aplicar y no hay reemplazo obvio. Esa pregunta hay que
contestarla antes de escribir código.

**Advertencia sobre el `n`:** 1.211 y 823 se midieron para una pregunta direccional
(binomial sobre aciertos, +5pp sobre 0,50). Una pregunta sobre magnitud tiene otra
potencia y otro umbral. **El `n` no se hereda entre preguntas distintas** — hay que
volver a dimensionarlo.

**Criterio de terminado (nuevo)**: una métrica de validación definida y justificada
para dimensionamiento, con su `n` mínimo calculado, antes de escribir una línea de
trainer o de `execution/`.

**Lo que sigue siendo válido del diagnóstico original:** las hipótesis 1 y 2 (fuga de
horizonte, arquitectura insuficiente) nunca se descartaron formalmente. Ya no bloquean
—porque la vía direccional se abandona— pero si alguna vez se retoma un modelo
direccional, siguen pendientes.

### Los checkpoints legacy NO son base operativa (2026-08-24)

Los 14 `.pt` del legacy están **deprecados**: fuera del flujo activo de
entrenamiento e inferencia, sin valor como baseline ni como término de
comparación. Se preservan solo como histórico. **Cualquier port futuro
parte de modelos reentrenados desde cero** — entrada completa, con las
razones verificadas contra el código, en `decision-log.md` (2026-08-24).

Tres cosas de esta fase cambian por eso:

- **La hipótesis 2 ya no arrastra `64 hidden units, 1 capa` como dado.**
  Esa topología era canónica por el guardián `enforce_lstm_architecture()`,
  que declaraba su vigencia textual como *"inamovible mientras existan los
  14 .pt"*. Al salir los checkpoints, la condición deja de cumplirse por su
  propia letra. El guardián **no se porta**.
- **La hipótesis 3 ya no da por fijas "esas 20 columnas".** El canon de 20
  features queda abierto a redefinición, y esa decisión es **posterior a la
  medición de `n`**: elegir el ancho del espacio de entrada antes de saber
  cuántas muestras hay es precisamente cómo se llega a un modelo que no
  puede aprender nada.
- **El `~0.50` de arriba deja de tener un baseline histórico contra el cual
  compararse.** Las accuracies legacy (BTC 0.528, XAU 0.547, NVDA 0.550,
  NIFTY50 0.625) no son baseline: con el umbral de aborto del legacy
  (`n_val < 5`) ninguna tuvo la evidencia para distinguirse del azar —
  habrían hecho falta entre 134 y 2563 muestras de validación (binomial
  exacto, dos colas, α=0.05, potencia 80%). El baseline pasa a ser el
  trivial: la clase mayoritaria.

Sigue en pie lo que ya decía esta fase: **sin `torch` en `requirements.txt`**
(Decisión #7, sin ML en F1) y sin código de modelos hasta que el
diagnóstico tenga causa identificada con evidencia.

## Fase 3 — Visualización (después de Fase 1 y 2, no antes)

Grafo con un nodo por fuente de dato, score de confianza visible (0-100).
Construir esto antes de tener `ingestion/` y `core/` limpios muestra un
grafo bonito de datos que todavía no son confiables — por eso va tercero,
no primero, aunque sea lo más visible.

**Criterio de terminado**: abrir el grafo y saber, sin leer código, cuáles
de las fuentes activas están sanas hoy.

## Fase 4 — Ejecución real, $10 en Deriv (después de que Fase 2 tenga un
modelo que de verdad aprenda algo — no antes, sin excepción)

Esto es construcción nueva, no migración — nunca existió código de
ejecución automática real en el proyecto anterior. Incluye: conexión de
órdenes Deriv vía WebSocket oficial, el patrón de dual accounting ($10 real
/ $100k canónico para métricas válidas, ya probado en Alpaca, se porta el
patrón) y el gate de paper trading — la duración se decide una vez, se
escribe en `governance/PRINCIPLES.md`, no se renegocia bajo presión de
tiempo cada sesión.

**Decisión de prioridad de broker (17 ago 2026, confirmada por Altair):**
Deriv es el único broker autorizado para capital real en el arranque de
Fase 4 — depósito ahí no requiere verificación de identidad, a diferencia
de Alpaca. **Alpaca queda en modo paper/testing exclusivamente hasta
nuevo aviso explícito** — el código de Alpaca se mantiene funcional y se
sigue probando, pero cualquier lógica de ruteo de órdenes que se escriba
en Fase 4 debe bloquear duro (no-IA, guardrail) cualquier orden real
hacia Alpaca mientras este estado siga vigente. Este es un requisito para
el código que se escriba en Fase 4, no algo ya implementado — todavía no
existe ruteo de órdenes de ningún tipo.

**Criterio de terminado**: una orden real ejecutada en Deriv, de punta a
punta, con log de auditoría, sin intervención manual.

## Fase 5 — Escala (Supabase, Flet, GitHub Actions vs. Colab)

Con `ingestion/`, `core/`, `visualization/` y `execution/` ya probados:
Supabase como bus de señales entre el motor y un frontend Flet auditable.
GitHub Actions para el ciclo de reentrenamiento (medido: 0.27 min para 4
activos × 50 épocas — sobra margen); Colab como respaldo solo si un
benchmark con datos reales (no sintéticos) dice que hace falta.

---

## Auditoría cuantitativa legacy vs. repo nuevo (post-patch 0017)

32,577 líneas en el legacy (`/mnt/project`, 76 archivos). 2,358 líneas en
el repo nuevo (7.2%). No es la métrica que importa por sí sola — importa
DESGLOSADA, porque la mayoría de esas líneas no debían portarse nunca:

| Categoría | Líneas legacy | Decisión |
|---|---:|---|
| GDELT / ingestion de datos | 5,773 | **Portado lo que se usa** — pipeline completo (descarga, agregación, persistencia, join). Sin portar: harvester BigQuery e ingestion a nivel de evento |
| Dashboard / UI / Terminal | 7,020 | **No se porta** — 100% Streamlit, fuera de plataforma. Fase 3 construye otra cosa |
| Auditoría / guardianes / monitoreo | 4,371 | No planeado para F1-F4 |
| ML / entrenamiento | 4,303 | Bloqueado por Fase 2, y **reorientado**: la vía direccional quedó descartada con medición (6-sep) |
| Scoring / matemática | 3,868 | Mayormente portado (`core/scoring.py`) |
| Setup / infra (Colab, Drive, Telegram) | 2,886 | Reemplazado por detección de entorno (patrón `secrets.py`) |
| Orquestación / main loop | 2,818 | **Parcial** — `orchestration/cycle.py` corre el ciclo de scoring; falta operación continua (watchdog, export de estado, disparo periódico) |

**Terminal institucional:** los 8 archivos de dashboard legacy
(`main_ui_vFinal.py`, `dashboard_fx.py`, `spel_hud.py`,
`spel_graph_tab.py`, `spel_scalping_tab.py`,
`spel_inventory_dashboard.py`, `main_ui.py`, `spel_dashboard.py`) usan
`import streamlit as st`, confirmado con grep, sin excepción. No hay
ningún archivo legacy que use Flet — el target de Fase 5 no tiene
prototipo previo, se construye desde cero cuando llegue el momento.

---

## Fase 6 — Motor de streaming multi-timeframe (propuesta de Altair, 17 ago 2026) — EN EVALUACIÓN, NADA CONSTRUIDO TODAVÍA

Altair propuso una segunda vía, en paralelo a la macro (GDELT diario, Fases 1-5): un motor que
opere en 1/5/15/30 min sobre Deriv (forex + herramientas propias) y, en paralelo, un motor
símil sobre Alpaca para ejecución rápida. Objetivo explícito: generar ingresos que no dependan
de esperar días a que una noticia confirme una tesis — motivo declarado: gastos, deudas y falta
de empleo actuales. Esto se investigó (web, no solo criterio) antes de escribir una sola línea,
seis búsquedas, siguiendo el mismo protocolo de auditoría del resto de este documento.

### Hallazgo 1 — Deriv no es un mercado, son dos, con física distinta

**Índices sintéticos / Volatility / Crash-Boom / Step**: generados por un generador de números
aleatorios criptográficamente seguro. Deriv lo dice explícito en su propio marketing: *"mimic
real markets but are unaffected by real-world news or market volatility"*. GDELT no tiene nada
que decirle a un mercado diseñado para ser inmune a las noticias — cero, por diseño, no por
limitación de datos.

**Forex real (90+ pares) y CFDs de oro/commodities**: mercado real, sí se mueve con noticias —
acá SÍ aplica el motor GDELT existente.

**Consecuencia para el diseño**: no hace falta forzar `core/scoring.py` (construido en unidades
de DÍAS) a operar en minutos. El motor rápido de Deriv corre sobre índices sintéticos con señal
técnica pura (momentum, volatilidad, Monte Carlo) — un motor genuinamente distinto, no una
versión acelerada del motor GDELT. El motor GDELT sigue existiendo, sin tocar, para forex/oro
real. Dos motores, dos mercados, cada uno con la física que le corresponde — no una
reconciliación forzada.

### Hallazgo 2 — "HFT" no es lo que Alpaca (ni nada gratis) puede dar

HFT institucional real es microsegundos, con servidores co-ubicados físicamente en el exchange —
no existe versión gratuita de eso, para nadie, en ningún broker retail. Lo que sí ofrece Alpaca
gratis: WebSocket de cripto en tiempo real (trades/quotes/orderbook), 200 req/min en el plan
free, sin comisión en acciones/opciones, comisión pequeña por volumen en cripto. Es decir: **el
motor rápido sobre Alpaca es trading intradía automatizado de segundos-a-minutos, no HFT en el
sentido técnico** — la etiqueta no cambia lo que se puede construir (que es real y gratis), pero
sí las expectativas de qué tipo de ventaja es capturable.

### Hallazgo 3 — el bloqueante real no es de estrategia, es de infraestructura

`.github/workflows/tests.yml` (patch 0011) fue diseñado asumiendo que GitHub Actions con
`schedule:` sería el host de la automatización recurrente. Investigado esta sesión: **GitHub
Actions tiene un piso duro de 5 minutos en `cron`** — cualquier intervalo menor se acepta en el
YAML, no da error, y simplemente nunca dispara. Además, en horas de carga alta, los `schedule:`
documentadamente llegan con 10-30 min de atraso (a veces más de una hora) — GitHub no garantiza
la puntualidad. **Esto no es apto para un motor de 1 minuto ni para mantener una conexión
WebSocket persistente** (los jobs son efímeros, se apagan al terminar el step). El plan de F5
("GitHub Actions vs. Colab") necesita revisión antes de construir el motor rápido: sirve para
ingestion diaria de GDELT y para CI, no sirve como host del motor de streaming.

**Investigado y resuelto, sin costo:** Oracle Cloud "Always Free" da una VM ARM siempre encendida
(2 OCPU / 12GB RAM tras el recorte de junio 2026 — antes 4/24, la tendencia es a la baja, no
garantizado a perpetuidad, pero hoy es real y gratis) — suficiente de sobra para un cliente
Python liviano con conexión WebSocket persistente a Deriv/Alpaca. Alternativa de respaldo:
Northflank (2 servicios, 1 vCPU/1GB, sin cold-starts, plan gratis genuino). **Esto resuelve el
"sacrificar el teléfono" sin sacrificarlo**: la VM gratuita en la nube corre 24/7 sin depender de
la RAM del teléfono ni reabrir la discusión de Termux (que sigue descartada por las razones ya
documentadas — esto es una VM en la nube, no Termux en el dispositivo, son cosas distintas).
GitHub Actions se queda para lo que ya hace bien: CI, ingestion diaria de GDELT, reentrenamiento
programado.

### Hallazgo 4 — Cerebro (DRL + ONNX): investigado, veredicto con condiciones

Reinforcement Learning para trading es un área activa 2024-2026, pero la literatura (arXiv,
guías especializadas) es consistente en un punto: los resultados auditables y de producción real
están en *deep hedging, algoritmos de ejecución y market making* — no en "generar alpha puro
desde el precio", que sigue siendo difícil y con alto riesgo de sobreajuste al backtest. El
riesgo #1, citado en cada fuente seria: un agente que memoriza ruido histórico y parece brillante
en backtest, y pierde en vivo. La mitigación estándar (walk-forward, out-of-sample real, penalizar
agentes sobreajustados como hipótesis estadística) es exactamente el mismo espíritu que ya rige
este proyecto (Tamiz #2, integridad temporal) — no hay que aprender una disciplina nueva, hay que
aplicar la que ya existe a un tipo de modelo nuevo.

**Veredicto: sí, vale la pena investigar más a fondo cuando llegue el momento — no ahora.**
Orden correcto: Fase 2 (diagnosticar por qué el LSTM actual da ~0.50) antes que un agente DRL
nuevo — no tiene sentido construir un segundo modelo sin entender por qué el primero no aprende.
Si Fase 2 confirma que el problema es de arquitectura/señal (no de fuga de datos), un agente DRL
para el motor rápido (que sí tiene la cadencia de datos — ticks/minutos, no días — que DRL
necesita para tener suficientes transiciones de estado) es un candidato razonable *ahí*, no para
el motor GDELT macro.

**ONNX: relevante, pero después de que exista un modelo que exportar.** El beneficio real medido
en la literatura (2-3x más rápido en CPU, footprint de instalación mucho menor que PyTorch
completo) importa específicamente para correr inferencia en un runner de GitHub Actions o una VM
chica sin instalar PyTorch entero en cada corrida — encaja con "gratis e inteligente". No hay
nada que exportar todavía (Decisión #7: sin ML en F1). Se revisita en Fase 2/DRL, no antes.

### Hallazgo 5 — colisión de nombres, resuelta sin costo

El legacy ya usa `RL_01`..`RL_05` para *Risk Limits* (`axiom_master.xml`, capital máximo por
trade, pérdida diaria, exposición total, posiciones concurrentes, kill-switch). El componente de
*Reinforcement Learning* se nombra **DRL** (Deep Reinforcement Learning) en todo el proyecto —
es además el término que usa la propia literatura especializada, no una convención inventada acá.
Cero código ni tests referencian "RL" como aprendizaje todavía — el cambio de nombre es gratis
hoy y deja de serlo en cuanto exista una sola línea de código con el nombre viejo.

### Hallazgo 6 — el "chat de pensamiento" ya casi existe, no hay que construirlo de cero

Altair pidió que el sistema registre por qué hizo cada operación. Tamiz #1 de este proyecto
("transparencia matemática — cero cajas negras") ya obliga a que cada score sea descomponible:
`gold_score_bma()` ya devuelve `weights_used` y `kill_reason`; `godel_active()` ya es una
condición explícita, no una caja negra. **El "chat de pensamiento" no necesita un LLM narrando
en vivo (eso cuesta dinero y latencia, rompe "gratis") — necesita que `governance/persistence.py`
(stream METRICS) registre, por cada operación, la descomposición completa que la función ya
calcula.** Es un consumidor nuevo de infraestructura que ya existe, no una pieza nueva de
infraestructura.

### Hallazgo 7 — precedente legacy real, ya escrito, nunca portado

No hay que inventar de cero. `archive/legacy-pre-20260813` ya tiene:
- `spel_bayesian_core.py::run_monte_carlo_validation()` — GBM vectorizado, 1000 trayectorias de
  15 min sobre `gold_score`, <50ms con NumPy puro, umbral ≥850/1000 → `MC_APPROVE`. Esto es
  literalmente el "Monte Carlo a su conveniencia" que Altair describió — ya calculado, ya
  diseñado, nunca portado al repo nuevo.
- `spel_trading_router.py::SPELTradingRouter` — ya router-ea MODO_INSTITUCIONAL (score≥90,
  diario, Kelly completo, RR 2.5x) vs. MODO_SCALPING (score 70-89, 15/30min, Kelly al 50%, RR
  1.5x, máx. 3 trades/sesión) vs. MODO_FLAT. Es precedente directo del motor multi-timeframe —
  la lógica de "cuándo operar más chico y más rápido" ya está pensada.
- `spel_forex_iq.py` tiene una lógica de confluencia de 4 capas (macro GDELT 40pts + estructura
  diaria 25pts + VWAP de sesión 20pts + sesión activa 15pts) — el archivo en sí no se porta
  (`yfinance`, prohibido), pero el diseño de scoring por capas es reusable con Deriv como fuente.

### Tensión abierta — necesita tu confirmación explícita, no se resolvió acá

¿El motor GDELT (Fases 1-5, la tesis original de SPEL — Socio-Political Entropy *Loss*) sigue
siendo el motor principal, y el motor rápido es un *segundo* motor en paralelo sobre índices
sintéticos? ¿O el foco cambia hacia el motor rápido como prioridad, y GDELT pasa a ser una señal
secundaria solo para forex real? Son dos proyectos legítimos — la arquitectura de carpetas
(`core/`, `ingestion/`) puede alojar ambos sin pelearse entre sí, pero el orden de qué se
construye primero cambia según cuál elijas. No se asumió ninguno de los dos.

### Riesgo real a declarar (auditoría, no validación)

El principio #6 de `governance/PRINCIPLES.md` ("capital real solo después de que el paper trading
lo demuestre") existe precisamente para el escenario que motiva esta fase: presión por ganancias
inmediatas es la causa número uno, documentada en toda la literatura de trading algorítmico, de
cuentas reventadas por meter capital real antes de tiempo. Un motor de 1 minuto con búsqueda
automática de configuración (Monte Carlo, DRL) es, además, el tipo de sistema con mayor riesgo de
sobreajuste — exactamente donde la disciplina de paper trading importa más, no menos. Esto no
cambia el objetivo (ingresos reales, rápido); cambia qué se valida antes de arriesgar el primer
dólar real en el motor rápido específicamente.

---

## Regla de todas las fases

Ningún archivo bajo `ingestion/`, `core/`, `execution/` o `visualization/`
importa desde una rama `archive/*`. Lo que se necesita de ahí se reescribe
acá, con su test, antes de darlo por portado.
