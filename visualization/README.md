# visualization/

Cada fuente de dato (parquet, adapter, señal) tiene un nodo en un grafo, con
su propio score de confianza visible (0-100, mismo concepto que GT-Score /
"porcentaje de oro" que ya existía). Objetivo: mirar el grafo y saber, sin
leer código, qué dato es confiable hoy y cuál no.

No construido todavía — Fase 3 del blueprint, después de que ingestion/ y
core/ estén portados y probados. Construir el grafo antes de tener datos
limpios abajo muestra un grafo bonito de datos que no sirven.
