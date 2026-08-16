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

**`ingestion/`: parcial.** DerivAdapter con fetch_async() nativo (la
trampa de reentrancia de asyncio.run() documentada en sesiones
anteriores, resuelta y verificada con contador real). **GDELT: 0%
portado.** Esto NO es un descuido — es el bloqueante real del criterio
de "terminado" de esta fase. `gdelt_foundation.py` (900 líneas),
`spel_bulk_harvester.py` (1083), `spel_ingest_incremental.py` (518) no
tienen equivalente en el repo nuevo todavía.

**`execution/` + `governance/`:** circuit_breaker, execution_guard,
secrets (detección de entorno real, no rutas fijas), persistence (4
streams, con el mismo fix de detección de entorno tras un hallazgo de
auditoría — DRIVE_ROOT estaba hardcodeado, corregido).

**Orquestador: 0% portado.** `spel_orchestrator_v10.py` (735 líneas) es
el main loop real del legacy — corre BMA, exporta JSON de estado,
watchdog. No existe versión nueva. `.github/workflows/tests.yml`
corre la suite en cada push, pero no orquesta nada del sistema todavía
— es CI, no el orquestador de trading.

**Dashboard: 0% portado, a propósito.** Los 8 archivos de UI legacy
(main_ui_vFinal.py, dashboard_fx.py, spel_hud.py, spel_graph_tab.py,
spel_scalping_tab.py, spel_inventory_dashboard.py, main_ui.py,
spel_dashboard.py — 7,020 líneas) son 100% Streamlit, confirmado con
grep, sin excepción. Streamlit está fuera de las restricciones de
plataforma actuales (Android, Colab+Drive). No se portan — Fase 3 ya
decidía construir algo nuevo y más simple, no re-crear estos 8
archivos.

## Fase 2 — Diagnosticar el modelo antes de portarlo ciego (esta semana)

La precisión de validación en ~0.50 no se hereda al código nuevo sin
entender por qué pasó. Tres hipótesis a descartar, en este orden (cada una
es una prueba de 15-30 minutos, no una investigación abierta):
1. Fuga de horizonte en las features (¿alguna columna usa información del
   futuro respecto al target?)
2. Arquitectura insuficiente para la señal (64 hidden units, 1 capa — ¿da
   más que ruido con datos reales, no sintéticos?)
3. La señal no está en esas 20 columnas con ese lookback — la hipótesis
   más incómoda, y la que hay que descartar con datos reales, no evitar.

**Criterio de terminado**: un diagnóstico con causa identificada y evidencia
(no "probablemente es X"), antes de escribir una línea de `execution/`.

**Estado: no arrancó.** Este es el siguiente paso genuino del plan — no
más funciones de scoring, no el dashboard, no GDELT todavía. Necesita
datos reales (no sintéticos) para las 3 hipótesis, lo cual a su vez
depende parcialmente de que exista algo de ingestion GDELT real
(Fase 1 incompleta) o de reusar el dataset legacy ya generado, si
sigue siendo válido — a confirmar antes de arrancar.

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
| GDELT / ingestion de datos | 5,773 | Pendiente — bloqueante real de Fase 1 |
| Dashboard / UI / Terminal | 7,020 | **No se porta** — 100% Streamlit, fuera de plataforma. Fase 3 construye otra cosa |
| Auditoría / guardianes / monitoreo | 4,371 | No planeado para F1-F4 |
| ML / entrenamiento | 4,303 | Bloqueado por Fase 2 |
| Scoring / matemática | 3,868 | Mayormente portado (`core/scoring.py`) |
| Setup / infra (Colab, Drive, Telegram) | 2,886 | Reemplazado por detección de entorno (patrón `secrets.py`) |
| Orquestación / main loop | 2,818 | Pendiente — 0% portado |

**Terminal institucional:** los 8 archivos de dashboard legacy
(`main_ui_vFinal.py`, `dashboard_fx.py`, `spel_hud.py`,
`spel_graph_tab.py`, `spel_scalping_tab.py`,
`spel_inventory_dashboard.py`, `main_ui.py`, `spel_dashboard.py`) usan
`import streamlit as st`, confirmado con grep, sin excepción. No hay
ningún archivo legacy que use Flet — el target de Fase 5 no tiene
prototipo previo, se construye desde cero cuando llegue el momento.

---

## Regla de todas las fases

Ningún archivo bajo `ingestion/`, `core/`, `execution/` o `visualization/`
importa desde una rama `archive/*`. Lo que se necesita de ahí se reescribe
acá, con su test, antes de darlo por portado.
