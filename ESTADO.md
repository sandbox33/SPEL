# SPEL — ESTADO DEL PROYECTO

> **Este archivo es la única fuente de verdad sobre en qué fase estamos.**
> Se lee primero en cada chat nuevo. Se actualiza al final de cada sesión, nunca a mitad.
> Si algo en un chat contradice lo que dice acá, este archivo gana — a menos que
> el chat verifique contra GitHub real y lo actualice con evidencia.

**Última actualización:** 14 ago 2026, sesión "Análisis global + admin panel"
**Actualizado por:** sesión que NO tocó código, solo consolidó estado

---

## 🚦 SEMÁFORO DE FASES

```
FASE 1 — ingestion/ + core/scoring.py     🟡 EN PROGRESO (ver detalle abajo)
FASE 2 — Diagnóstico del modelo            ⚪ NO INICIADA
FASE 3 — visualization/ (grafo)            ⚪ NO INICIADA
FASE 4 — execution/ + Deriv real           ⚪ NO INICIADA (bloqueada por F2)
FASE 5 — Escala (Supabase, Flet, Actions)  ⚪ NO INICIADA
```

---

## 📍 DÓNDE ESTAMOS EXACTAMENTE — FASE 1

### Lo que SÍ está confirmado en GitHub `main` (verificado, no supuesto):

| Archivo | Estado | Tests | Última verificación |
|---|---|---|---|
| `governance/secrets.py` | ✅ En main | 6/6 | Confirmado contra clon fresco de main |
| `execution/circuit_breaker.py` | ✅ En main (no tocar hasta F4) | 14/14 | Confirmado contra clon fresco de main |
| `execution/execution_guard.py` | ✅ En main (no tocar hasta F4) | 17/17 | Confirmado contra clon fresco de main |
| `ingestion/adapters.py` (versión original, sin AdapterChain) | ✅ En main | 22/22 | Confirmado contra clon fresco de main |

### Lo que NO está confirmado en GitHub `main` — punto crítico de retomar:

| Ítem | Estado real | Qué falta |
|---|---|---|
| `AdapterResult` + `AdapterChain` (fusionados a `adapters.py`) | 🟡 Probado LOCAL (68/68 en clon fresco del sandbox — NUNCA llegó a main), patch generado | **Confirmar si el push desde Colab llegó a main real.** Última vez que se preguntó esto, no hubo respuesta confirmando. |
| Archivo `0001-fase1-adapterchain.patch` | 🟡 Generado y verificado (aplica limpio dos veces contra main fresco) | Confirmar aplicación real vía notebook de Colab |

**➡️ PRIMER PASO DEL PRÓXIMO CHAT DE CÓDIGO: re-clonar `main`, correr `pytest tests/ -v`, y confirmar si da 105/105 (68+6+14+17) o 82/82 (22+6+14+17, si el patch nunca se aplicó). Ese número solo dice qué quedó pendiente — no asumir ninguno de los dos.**

### Decisiones de arquitectura ya tomadas (no reabrir sin razón nueva):

1. **`adapters.py` es la base, no `base_adapter.py`** — excepciones tipadas, protección anti-fuga de vela, probado contra 403/timeout. De `base_adapter.py` solo se porta el patrón `AdapterResult`/`AdapterChain` (degradación con fallback), adaptado a `pandas` (no `polars`), sin `logging.basicConfig` global, sin las columnas `COLS_CANONICAS_V4`.
2. **`secrets.py` es la base, no `secrets_env_loader.py`** — único que exporta `SecretError`, `SecretKey`, `load_secret`, `secrets_status_report`.
3. **`AdapterChain.fetch()` es síncrono con `asyncio.run()` interno**, no async de punta a punta — porque el resto de Fase 1 (`core/scoring.py`) no necesita async, y no hay que meter esa complejidad sin que la pida el siguiente archivo real. **Deuda documentada, no oculta:** `asyncio.run()` no es reentrante; si Fase 4 (`execution/`) necesita llamar esto desde un event loop ya corriendo, hay que revisar este wrapper entonces, no antes.
4. **Deriv es un adapter más, no un caso especial** — mismo contrato `BaseAdapter` que cualquier otra fuente (Alpaca, GDELT). Lo que lo distingue es que declara su propia latencia medida (no estimada) como fuente candidata para HFT; `AdapterChain` decide cuál usar según esa declaración, no por hardcodeo. Esto ya es lo que hace el diseño actual — no es arquitectura nueva pendiente.
5. **`AdapterAuthError` corta el reintento temprano** (no reintenta 3 veces un token inválido) — decisión tomada fuera del diff original aprobado, marcada explícitamente en el código y en el commit.

### Pendiente de decidir (NO decidir en el aire, decidir con el archivo delante):

- **¿`ingestion/deriv.py` se separa como archivo propio, o se mantiene como clase dentro de `adapters.py`?** El blueprint original lo pide separado. Hoy vive como clase adentro. Es una decisión de 5 minutos una vez que Fase 1 esté con el push confirmado — no antes.
- **`core/scoring.py`** — candidato a portar: `spel_bayesian_core.py` (BMA, Gödel, kill-switch). NO se audita todavía, se hace con el mismo protocolo que `adapters.py`: leer el archivo completo, comparar contra si hay otros candidatos/linajes, decidir con evidencia.

---

## 🔬 FASE 2 — DIAGNÓSTICO DEL MODELO (no iniciada, no inventar causa)

**Lo único confirmado:** accuracy de validación estancado en ~0.50 (visto en gráficos de entrenamiento propios). Esto bloquea Fase 4 — no entra capital real hasta que se resuelva.

**Hipótesis planteadas (ninguna confirmada todavía, cada una necesita su propia prueba de 15-30 min, no investigación abierta):**
1. Fuga de horizonte en features — ¿alguna columna usa información futura respecto al target?
2. Arquitectura insuficiente (64 unidades ocultas, 1 capa) — ¿alcanza para superar el ruido con datos reales?
3. La señal no está en esas columnas / ese lookback específico

**Esto NO se toca hasta que Fase 1 tenga push confirmado.**

---

## 🗂️ CÓDIGO LEGACY — dónde vive, qué es seguro portar

```
archive/legacy-pre-20260813              → 74 módulos originales, intactos, con historia
archive/dashboard-data-pre-20260813      → cache de datos de GitHub Actions
archive/feature-cache-pre-20260813       → cache de features
archive/model-cache-pre-20260813         → cache de modelos
archive/limpieza-legado-99-pre-20260813  → última sesión de limpieza previa al reinicio
```

**Regla fija:** nada en `ingestion/`, `core/`, `execution/`, `visualization/` importa desde ninguna rama `archive/*`. Lo que se necesita de ahí se reescribe con su test antes de darse por portado.

**Candidatos ya identificados en `archive/limpieza-legado-99` para portar cuando corresponda:**
- `spel_bayesian_core.py` → candidato para `core/scoring.py` (Fase 1, pendiente auditar)
- `base_adapter.py` → ya se extrajo lo útil (`AdapterResult`/`AdapterChain`), no se necesita más de acá para Fase 1

**Explícitamente NO portar (fuera de alcance permanente o por decisión):**
- `spel_forex_iq.py` — usa IQOption, prohibido (sin API oficial)
- Cualquier variante de `spel_orchestrator_v*.py` — múltiples versiones del mismo concepto, viola modularidad estricta
- `axiom_master.xml` (6 versiones sin reconciliar) — reemplazado por `governance/PRINCIPLES.md`, queda como referencia histórica en Drive, no como texto gobernante

---

## 🔐 SEGURIDAD — incidentes y estado actual

- **2 tokens de GitHub fueron pegados en texto plano en chats distintos** (uno en una sesión anterior a esta, uno en este chat). **Ambos fueron revocados por el usuario.** Ningún chat de Claude usó nunca un token para hacer push directamente — el sandbox de Claude puede clonar (lectura pública) pero nunca tuvo un token cargado para escribir.
- **Regla vigente, establecida por el usuario:** ningún token se pega en texto de chat, bajo ninguna circunstancia, incluso con autorización explícita en el momento. Los tokens se manejan vía Colab Secrets (`userdata.get()`) o se pegan directamente en el flujo de Colab, nunca en un mensaje de Claude.
- **Token de Deriv:** ya almacenado en Colab Secrets, según confirmó el usuario. No pasó nunca por ningún chat.

---

## 🖥️ ENTORNO DE TRABAJO — restricciones fijas

```
✅ Google Colab (notebook — admin panel + desarrollo)
✅ Google Drive (almacenamiento, un solo patch a la vez, no duplicar código)
✅ GitHub (github.com/sandbox33/SPEL — fuente de verdad única)
✅ Colab Secrets (userdata.get()) para tokens — NUNCA getpass()/input() en Android, falla con StdinNotImplementedError
✅ Deriv (API oficial, WebSocket)

❌ Termux — descartado explícitamente, complejo, alto consumo de RAM en el teléfono (2GB disponibles de 8GB)
❌ Streamlit — descartado explícitamente
❌ IQOption — sin API oficial, reemplazado por Deriv
❌ Rutas hardcodeadas de Colab (/content/drive/...) en el código de producción
```

**Restricción de contexto entre chats (auto-impuesta):**
- Máx. ~50 turnos por chat → cerrar con resumen y actualizar este archivo antes de seguir
- 1 chat = 1 fase o 1 bloque bien definido dentro de una fase — nunca mezclar
- Si en 15 turnos no bajó código nuevo a un archivo real, es señal de circularidad → cortar, actualizar este archivo, empezar de nuevo con foco

---

## ▶️ PRÓXIMO PASO CONCRETO (una sola cosa, no una lista)

**Re-clonar `main` desde GitHub real y correr `pytest tests/ -v`.**

Resultado posible A: 105/105 → el push llegó, Fase 1 avanza a decidir `deriv.py` + auditar `core/scoring.py`.
Resultado posible B: 82/82 (sin AdapterChain) → el push no llegó nunca, hay que aplicar el patch de nuevo desde el notebook de Colab.

**No asumir cuál de los dos es. Correrlo y ver.**

---

## 📝 CÓMO ACTUALIZAR ESTE ARCHIVO

Al final de cada sesión de código (no a mitad):
1. Actualizar la tabla de "Dónde estamos exactamente" con lo que se verificó contra GitHub real
2. Mover cualquier decisión nueva a "Decisiones de arquitectura ya tomadas"
3. Actualizar "Próximo paso concreto" — una sola cosa, no una lista de deseos
4. Commitear este archivo junto con el código de esa sesión, mismo commit o el siguiente inmediato
5. Nunca dejar este archivo diciendo algo que no se verificó — si algo quedó a medias, decirlo explícitamente como 🟡, no como ✅
