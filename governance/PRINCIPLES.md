# Principios — no escritura, herramienta de trabajo

Esto reemplaza `axiom_master.xml` (802 líneas, 6+ versiones sin reconciliar)
como referencia del día a día. Axiom completo sigue existiendo — en Drive,
`SPEL_ARCHIVO_LEGADO_2026-08-13/` — como documentación histórica de por qué
se tomó cada decisión. Se consulta cuando hace falta contexto, no se cita
como ley que bloquea avanzar.

Si un principio de acá deja de servir, se edita este archivo y se sigue.
No hace falta una sesión de "sellado" ni una firma soberana para actualizar
una página de reglas prácticas.

## Los 7 que de verdad sostienen el sistema

1. **Nunca fabricar valores criptográficos.** Un hash, una firma, un ID de
   transacción — si no se calculó de verdad (hashlib, librería real), no se
   inventa un valor que parezca plausible. Placeholder explícito o nada.

2. **Escritura atómica siempre.** `.tmp` + `os.replace()`. Nunca un archivo
   a medio escribir si el proceso muere a mitad de camino.

3. **Archivar, nunca borrar.** No es solo una regla de higiene — es lo que
   hizo posible limpiar el repo esta sesión con confianza real en vez de
   miedo a perder trabajo. `git mv` a una rama `archive/`, o `shutil.move`
   a una carpeta de cuarentena. `os.remove` no se usa sobre nada que
   represente trabajo hecho.

4. **Una sola implementación por concepto.** Si existe `sha12()`, hay una.
   Si existe "mandar mensaje a Telegram", hay una función, no cinco. Antes
   de escribir una función nueva: buscar si ya existe una versión de esto
   en `core/` o `ingestion/`.

5. **Ninguna fuente de dato es "correcta" en abstracto.** Es correcta la
   que coincide con el broker donde se ejecuta de verdad. Todo lo demás es
   aproximación de entrenamiento — se declara así.

6. **Capital real solo después de que el paper trading lo demuestre.**
   Cuánto tiempo/cuántos ciclos, se decide una vez y se escribe acá — no se
   negocia por sesión bajo presión de tiempo.

7. **APIs oficiales únicamente.** Si un broker o fuente no tiene API
   oficial, no entra al sistema, sin excepción — es la razón real por la
   que IQ Option salió y Deriv entró.

## Lo que NO es un principio — es una decisión de sesión, se revisa seguido

- Qué activos están activos (`core/README.md` los lista)
- Qué timeframes se operan
- Estructura exacta de carpetas (esta misma puede cambiar si deja de servir)

La diferencia entre esta lista y axiom_master.xml no es el contenido — es
que esto tiene 7 puntos, no 8 leyes con sub-reglas, enforcement,
rationale y capas de XML. Se lee en dos minutos. Esa es la prueba de que
sirve: si hay que preguntar "¿esto en qué ley está?", ya falló el propósito.
