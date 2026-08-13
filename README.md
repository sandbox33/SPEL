# SPEL — Motor Cuantitativo Sistemático

**Este repo se reinició el 2026-08-13.** El código anterior (74 módulos, 4 años
de sesiones) nunca funcionó de principio a fin — quedó documentado, auditado
y respaldado, pero no se sigue construyendo encima de él.

## ¿Dónde está el código anterior?
Nada se borró. Está en ramas `archive/*`, exactamente como quedó:
- `archive/legacy-pre-20260813` — el código completo (74 módulos)
- `archive/dashboard-data-pre-20260813`, `archive/feature-cache-pre-20260813`,
  `archive/model-cache-pre-20260813` — las 3 ramas de datos de GitHub Actions
- `archive/limpieza-legado-99-pre-20260813` — el trabajo de la última sesión
  de auditoría (adapter de Deriv, parches integrados, fix de RC-04) — **esto
  es código verificado y funcional, no legado roto. Se porta a la estructura
  nueva en la Fase 1 del blueprint, no se descarta.**

## ¿Por qué se reinició?
Duplicación real, no percibida: dos `spel_commons.py` distintos, seis
versiones de `axiom_master.xml`, cinco implementaciones de "mandar un mensaje
a Telegram", código nuevo importando símbolos de código que nunca se probó
de punta a punta. El detalle completo está en `BLUEPRINT.md`.

## Estructura de este repo (nuevo, desde cero)

| Carpeta | Qué va acá | Estado |
|---|---|---|
| `ingestion/` | Adapters de datos (OHLCV, GDELT) — un solo patrón, una sola fuente de verdad por dato | Por portar desde `archive/limpieza-legado-99-*` |
| `core/` | Motor de scoring (Gödel, BMA, entropía) — la matemática que ya probamos que funciona | Por portar desde `archive/legacy-pre-*` |
| `execution/` | Ejecución real en Deriv y Alpaca | **No existe todavía — es trabajo nuevo genuino, no migración** |
| `governance/` | `PRINCIPLES.md` — las reglas que de verdad importan, en una página. Axiom completo queda como referencia histórica, no como escritura | Ver `governance/PRINCIPLES.md` |
| `visualization/` | Grafo de cada fuente de dato con su propio score visual | Por construir — Fase 3 del blueprint |
| `infra/` | GitHub Actions + plantillas de Colab para cuando el entrenamiento exceda el margen gratis | Por construir |
| `tests/` | Uno por módulo nuevo, antes de que el módulo se dé por terminado | — |

## Regla de esta carpeta, a partir de hoy
Ningún archivo nuevo importa desde `archive/*`. Lo que se necesita de ahí se
**reescribe** acá, limpio, con su test. Ver `BLUEPRINT.md` para el plan
completo y el orden de las fases.
