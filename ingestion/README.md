# ingestion/

Un adapter por fuente real. Cada uno declara: nombre, columnas que entrega,
latencia típica medida (no estimada), y qué cambios rotos se le conocen.

Patrón base (ya probado, viene de `archive/limpieza-legado-99-*`):
`BaseSensorAdapter` → `fetch()` público que nunca lanza excepción, degrada
en cadena (fuente primaria → secundaria → último dato conocido), loguea cada
intento. `SPELAdapterChain` orquesta la cadena por tipo de dato.

Fuentes confirmadas y su estado (ver BLUEPRINT.md §2 para el detalle):
AlphaVantage, Tiingo, GDELT (bulk 1.0 — inmutable), Deriv (WebSocket oficial,
adapter ya construido y probado). yfinance: retirado a propósito, no se
reintroduce.

Regla: ninguna fuente se declara "correcta" en abstracto — es correcta la
que coincide con el broker donde se ejecuta de verdad.
