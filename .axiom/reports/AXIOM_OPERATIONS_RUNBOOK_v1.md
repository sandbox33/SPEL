# AXIOM Operations Runbook v1.0

**Para:** Abraham Fuenmayor Hernández (Altair)
**Propósito:** Tu manual operacional. Cuando dudes qué hacer, abre este documento, busca tu situación, sigue los pasos.
**Generado:** 2026-06-17
**Filosofía:** Hinc Omnia Cerno — Desde aquí lo veo todo

---

## CÓMO USAR ESTE DOCUMENTO

Este runbook está organizado en 6 secciones. **No tienes que leerlo entero.** Cuando necesites algo, salta directo a la sección:

- **Sección 1** — Mapa de tus mesas de trabajo (dónde vive cada cosa)
- **Sección 2** — Tabla de búsqueda rápida "Quiero hacer X → voy a Y"
- **Sección 3** — Procedimientos detallados paso a paso
- **Sección 4** — Diagrama de flujo de datos
- **Sección 5** — Protocolo de emergencia "estoy perdido"
- **Sección 6** — Glosario (términos que uso y qué significan)

---

## SECCIÓN 1 — MAPA DE TUS MESAS DE TRABAJO

Tienes **5 mesas de trabajo**. Cada una con un propósito específico. **No mezcles propósitos** entre mesas — esa es la regla de oro.

### 1.1 Google Drive — Mesa de "trabajo en vivo"

**Propósito:** Acceso rápido desde móvil. Cache caliente. Espacio de trabajo diario.
**Qué vive aquí:**
```
Mi Drive/ORDEN/SPEL 4.0/AXIOM/
├── axiom_master.xml              ← Mirror, NO editar aquí
├── axiom_schema.xsd              ← Mirror, NO editar aquí
├── axiom_hashchain.jsonl         ← Mirror, NO editar aquí
├── handoffs/                     ← Mirror de sesiones cerradas
├── reports/                      ← Mirror de bitácoras DIFF
└── keys/                         ← VACÍA (claves nunca aquí)
```
**Regla crítica:** Drive es **solo lectura espiritual**. La verdad vive en GitHub. Drive es donde miras desde móvil sin internet pesado.

### 1.2 GitHub — Mesa de "fuente de verdad"

**Propósito:** Versionado, historial, colaboración con IA, defensa legal de autoría.
**Dos repositorios:**

**`sandbox33/AXIOM` (privado al inicio)**
```
schema/axiom_schema.xsd           ← XSD canónico v0.2.0
keys/seal_authority.pub           ← Tu clave pública minisign
docs/                              ← Documentación del framework
README.md
LICENSE                            ← AGPLv3 al publicar
```

**`sandbox33/SPEL-4.0` (privado permanente)**
```
.axiom/
├── axiom_master.xml              ← LA VERDAD del proyecto
├── axiom_hashchain.jsonl         ← LA cadena criptográfica
├── handoffs/
│   ├── session_handoff_S52.json
│   ├── session_handoff_S53.json
│   └── ...
└── reports/
    ├── AXIOM_v0.3.1_DIFF.md
    ├── AXIOM_v0.3.2_DIFF.md
    └── ...
src/                              ← Código SPEL (futuro)
.github/workflows/                ← Workflows GitHub Actions (futuro)
README.md
```
**Regla crítica:** GitHub es donde se EDITA. Drive solo sincroniza.

### 1.3 Termux — Mesa de "caja fuerte criptográfica"

**Propósito:** Custodia de tu clave privada y operaciones de firma.
**Qué vive aquí (en `~/`):**
```
~/seal_authority.pub              ← Tu clave pública
~/seal_authority.key              ← Tu clave PRIVADA (nunca sale de aquí)
~/storage/downloads/              ← Puente con Descargas de Android (tras setup)
```

**Setup inicial de Termux (hacer UNA SOLA VEZ en la vida):**
```
termux-setup-storage
```
(Concede permiso de almacenamiento. Esto crea `~/storage/` que es puente con la galería de Android.)

**Regla crítica:** Termux es donde FIRMAS, no donde editas. Solo abres Termux para 2 cosas:
1. Firmar un archivo
2. Generar nuevas claves (raro)

### 1.4 Bitwarden — Mesa de "secretos sincronizados"

**Propósito:** Backup cifrado de claves, passphrases, tokens.
**Qué vive aquí (como Secure Notes):**

```
AXIOM_PUBLIC_KEY                  ← Tu clave pública (para tenerla a mano)
AXIOM_PRIVATE_KEY                 ← Contenido completo de seal_authority.key
AXIOM_PASSPHRASE                  ← La frase de 33 chars que protege la privada
GITHUB_TOKEN                      ← Personal Access Token (cuando lo crees)
TELEGRAM_BOT_TOKENS               ← Tokens de tus bots (cuando los cifremos)
API_KEYS_TRADING                  ← Tiingo, AlphaVantage, NewsData (cuando los cifremos)
```

**Regla crítica:** Bitwarden es el ÚNICO sitio cloud donde viven secretos. Nunca pegues secretos en Drive, GitHub, Claude, Telegram, Notas de Google.

### 1.5 Claude (este chat / Claude Projects) — Mesa de "asistencia técnica"

**Propósito:** Generar artefactos, auditar, decidir arquitectura.
**Qué NUNCA debe vivir aquí:**
- Tu clave privada
- Tu passphrase de minisign
- API keys reales
- Tokens GitHub
- Balance real de cuentas trading

**Qué SÍ puede pasar por aquí:**
- El XML maestro completo (lo verás muchas veces)
- La clave pública (es pública por definición)
- Discusiones de arquitectura
- Código que después tú revisas y sellas

**Claude Projects:** sube `axiom_master.xml` + `axiom_schema.xsd` al knowledge del Project AXIOM. Así cada chat nuevo dentro del Project tiene contexto sin re-subir.

---

## SECCIÓN 2 — TABLA DE BÚSQUEDA RÁPIDA

**"Quiero hacer X → voy a Y → ejecuto Z"**

| Quiero hacer | Voy a | Ejecuto |
|---|---|---|
| Ver el estado actual de AXIOM | GitHub `sandbox33/SPEL-4.0/.axiom/` | Abrir `axiom_master.xml` y leer atributo `project_version` |
| Descargar el XML maestro a mi móvil | GitHub vía navegador | Botón "Download raw file" en el archivo |
| Editar el XML (cambio pequeño) | GitHub web móvil | Botón ✏ "Edit" en el archivo, commit con mensaje claro |
| Editar el XML (cambio grande) | Pedirle a Claude que genere versión nueva | Subir XML actual al chat, pedir cambio específico, recibir nueva versión, subir a GitHub |
| Firmar un cambio crítico | Termux | Ver Procedimiento P-01 en Sección 3 |
| Verificar que una firma sea válida | Cualquier sistema con minisign | Ver Procedimiento P-02 |
| Rotar mis claves criptográficas | Termux + GitHub + Bitwarden | Ver Procedimiento P-03 |
| Backup de claves nuevo (post-rotación) | Termux + Bitwarden | Ver Procedimiento P-04 |
| Recuperar claves en móvil nuevo | Termux nuevo + Bitwarden | Ver Procedimiento P-05 |
| Crear nueva sesión con Claude | App Claude → Project AXIOM | Adjuntar handoff último + escribir "S<N>. Continúo desde priority queue." |
| Cerrar sesión con Claude | Pedir handoff JSON al final del chat | Guardar archivo en GitHub `.axiom/handoffs/` |
| Auditar hashchain íntegro | Visual o Colab | Ver Procedimiento P-06 |
| Buscar qué dice una ley específica | XML maestro | Buscar `<law id="LEY_..."` en el XML |
| Saber qué módulos están rotos | XML maestro | Buscar `audit_status="CONFLICT"` |
| Cambiar un parámetro (p90, KL, etc.) | XML maestro, sección `<parameter>` | Editar valor + commit + idealmente firmar |
| Crear nuevo bot Telegram | Telegram @BotFather + agregar a XML | Ver Procedimiento P-07 |
| Documentar una decisión nueva | XML, sección `<deviations>` | Agregar `<deviation>` con tu firma |

---

## SECCIÓN 3 — PROCEDIMIENTOS DETALLADOS

### P-01 — Firmar el XML maestro con minisign

**Cuándo:** Cualquier cambio a leyes `IMMUTABLE`, módulos `CRITICAL`, o desviaciones.

**Pasos:**

1. Descargar el XML actual desde GitHub a tu carpeta `Descargas` del móvil:
   - Abre GitHub → `sandbox33/SPEL-4.0/.axiom/axiom_master.xml`
   - Botón "Download raw file"
   - El archivo va a `Descargas/`

2. Abrir Termux. Ejecutar:
   ```
   cp ~/storage/downloads/axiom_master.xml ~/
   ```

3. Firmar el archivo:
   ```
   minisign -S -s seal_authority.key -m axiom_master.xml
   ```

4. Cuando pida `Password:`, pegar tu passphrase desde Bitwarden:
   - Mantener pulsado en la terminal
   - Tocar "PASTE"
   - Presionar Enter
   - Pantalla en blanco es normal

5. Si fue exitoso, se creó `axiom_master.xml.minisig` en tu home.

6. Copiar la firma de vuelta a Descargas:
   ```
   cp axiom_master.xml.minisig ~/storage/downloads/
   ```

7. Subir el `.minisig` a GitHub al lado del XML:
   - GitHub `sandbox33/SPEL-4.0/.axiom/`
   - "Add file" → "Upload files"
   - Subir `axiom_master.xml.minisig`
   - Commit message: "Sign axiom_master.xml v<X.Y.Z>"

8. Limpieza en Termux:
   ```
   rm axiom_master.xml axiom_master.xml.minisig
   ```

### P-02 — Verificar la firma de un archivo

**Cuándo:** Quieres confirmar que un XML está firmado por ti y no fue alterado.

**Opción A — Desde Termux (más confiable):**
```
cp ~/storage/downloads/axiom_master.xml ~/
cp ~/storage/downloads/axiom_master.xml.minisig ~/
minisign -V -p seal_authority.pub -m axiom_master.xml
```

**Resultado esperado:**
```
Signature and comment signature verified
Trusted comment: ...
```

**Opción B — Desde Colab (cuando lo tengamos):** lo haremos en S55.

### P-03 — Rotar las claves criptográficas

**Cuándo:** Sospechas que la passphrase se expuso, perdiste el móvil, cambias de dispositivo principal, o cada 12-18 meses como higiene.

**Pasos:**

1. En Termux, archivar las claves viejas (NO borrar):
   ```
   cd ~
   mv seal_authority.key seal_authority.key.OLD_$(date +%Y%m%d)
   mv seal_authority.pub seal_authority.pub.OLD_$(date +%Y%m%d)
   ```

2. Generar passphrase NUEVA en Bitwarden (33 chars, solo alfanumérico).

3. Crear nuevo par de claves:
   ```
   minisign -G -p seal_authority.pub -s seal_authority.key
   ```
   Pegar la nueva passphrase dos veces a ciegas.

4. Ver la nueva pública:
   ```
   cat seal_authority.pub
   ```

5. Actualizar en Bitwarden:
   - Renombrar item viejo a `AXIOM_PRIVATE_KEY_REVOKED_<fecha>`
   - Crear item nuevo `AXIOM_PRIVATE_KEY_v<N>` con la nueva privada y passphrase
   - Mismo patrón para `AXIOM_PUBLIC_KEY`

6. Actualizar en GitHub `sandbox33/AXIOM/keys/seal_authority.pub`:
   - Editar el archivo
   - Pegar contenido nuevo
   - Commit: "Rotate seal_authority key — old key archived as REVOKED"

7. Crear deviation en XML documentando la rotación (sección `<deviations>` del `axiom_master.xml`).

### P-04 — Hacer backup de claves en Bitwarden

**Cuándo:** Inmediatamente después de generar claves nuevas. Una sola vez por par de claves.

**Pasos:**

1. En Termux, mostrar contenido público:
   ```
   cat seal_authority.pub
   ```
   Seleccionar todo el output (3 líneas). Copiar.

2. Bitwarden → New item → Secure Note:
   - Name: `AXIOM_PUBLIC_KEY_v<N>`
   - Notes: pegar contenido

3. En Termux, mostrar contenido privado:
   ```
   cat seal_authority.key
   ```
   Seleccionar todo el output (5 líneas). Copiar.

4. Bitwarden → New item → Secure Note:
   - Name: `AXIOM_PRIVATE_KEY_v<N>`
   - Notes: pegar contenido. Al final agregar:
     ```
     Passphrase: <tu passphrase>
     Generated: <fecha>
     Device: <móvil donde la generaste>
     ```

5. **Inmediatamente después:** copiar la palabra "limpio" en cualquier app de notas para vaciar portapapeles.

### P-05 — Recuperar claves en móvil nuevo

**Cuándo:** Móvil viejo se rompió, perdió, o cambiaste de dispositivo.

**Pasos:**

1. Instalar Termux en móvil nuevo (desde F-Droid recomendado, NO Play Store).

2. En Termux:
   ```
   pkg update
   pkg install minisign
   termux-setup-storage
   ```

3. Abrir Bitwarden en móvil nuevo. Buscar item `AXIOM_PRIVATE_KEY_v<N>`.

4. Copiar contenido del Notes.

5. En Termux, crear el archivo:
   ```
   cd ~
   nano seal_authority.key
   ```
   Pegar el contenido. Ctrl+X, Y, Enter para guardar.

6. Igual para la pública (de Bitwarden item `AXIOM_PUBLIC_KEY_v<N>`):
   ```
   nano seal_authority.pub
   ```
   Pegar. Ctrl+X, Y, Enter.

7. Verificar:
   ```
   ls -la seal_authority.*
   echo "test" > test.txt
   minisign -S -s seal_authority.key -m test.txt
   ```
   Si pide passphrase y firma correctamente, recuperación exitosa.

8. Limpiar:
   ```
   rm test.txt test.txt.minisig
   ```

### P-06 — Auditar hashchain íntegro

**Cuándo:** Empezando una sesión nueva con Claude, o si sospechas que algo se modificó.

**Verificación manual (móvil):**

1. Abrir `axiom_hashchain.jsonl` en GitHub.
2. Para cada línea (cada bloque):
   - Tomar `curr_hash` del bloque N
   - Compararlo con `prev_hash` del bloque N+1
   - **Deben ser idénticos**
3. Si todos coinciden → cadena íntegra
4. Si alguno difiere → cadena rota, **NO continúes operando**, sube ambos al chat de Claude y resolvemos

**Verificación automática:** la haremos en Colab cuando esté listo (S55).

### P-07 — Crear nuevo bot Telegram y registrarlo en AXIOM

**Cuándo:** Quieres un nuevo canal de alertas, o reemplazar un bot existente.

**Pasos:**

1. Abrir Telegram, buscar @BotFather.
2. Comando `/newbot`
3. Nombre del bot (lo que ven los usuarios): ej `AXIOM Audit Alerts`
4. Username del bot (técnico, termina en `bot`): ej `axiom_audit_alerts_bot`
5. BotFather te da un TOKEN. **Cópialo inmediatamente a Bitwarden** como Secure Note `TG_BOT_<nombre>_TOKEN`.
6. Iniciar chat con tu bot, mandarle `/start`.
7. Obtener tu chat_id:
   - Visitar `https://api.telegram.org/bot<TOKEN>/getUpdates` en navegador
   - Buscar `"chat":{"id":...}` — ese número es tu chat_id
   - Guardar también en Bitwarden
8. Agregar al XML maestro en sección `<infrastructure><alert_routing>`:
   ```xml
   <alert_routing id="TG_<NOMBRE>" name="Telegram <Descripción>">
     <endpoint><chat_id></endpoint>
     <protocol>Telegram Bot API</protocol>
     <severity_filter>CRITICAL_ONLY o lo que aplique</severity_filter>
   </alert_routing>
   ```
9. Commit + firmar XML con P-01.

---

## SECCIÓN 4 — DIAGRAMA DE FLUJO DE DATOS

```
                    ┌─────────────────┐
                    │     CLAUDE      │
                    │  (asistencia)   │
                    └────────┬────────┘
                             │ propone
                             ▼
                    ┌─────────────────┐
                    │     GITHUB      │  ← fuente de verdad
                    │ sandbox33/      │
                    │ SPEL-4.0/.axiom │
                    └────┬────────┬───┘
                         │        │
              sync mirror│        │descarga p/firma
                         ▼        ▼
                ┌─────────────┐  ┌──────────────────┐
                │GOOGLE DRIVE │  │ MÓVIL/DESCARGAS  │
                │(cache hot)  │  │  (temporal)      │
                └─────────────┘  └────────┬─────────┘
                                          │
                                          ▼
                                 ┌──────────────────┐
                                 │     TERMUX       │
                                 │ (firma + claves) │
                                 └────────┬─────────┘
                                          │ firma
                                          ▼
                                 ┌──────────────────┐
                                 │ DESCARGAS móvil  │
                                 │ + .minisig file  │
                                 └────────┬─────────┘
                                          │ upload
                                          ▼
                                 ┌──────────────────┐
                                 │     GITHUB       │ ← vuelve a verdad
                                 │   firmado ✅     │
                                 └──────────────────┘

         ┌─────────────────────────────────────────┐
         │  BITWARDEN (paralelo, fuera del flujo)  │
         │  Backup de claves, passphrases, tokens  │
         └─────────────────────────────────────────┘
```

**Regla del flujo:** los archivos viajan SIEMPRE en la dirección de las flechas. **Nunca editar Drive directamente** — eso rompe el flujo y crea divergencia.

---

## SECCIÓN 5 — PROTOCOLO DE EMERGENCIA "ESTOY PERDIDO"

Si en algún momento te sientes perdido, sigue ESTE protocolo en orden:

### Nivel 1 — Confusión leve

1. Abre este runbook
2. Ve a Sección 2 (tabla de búsqueda rápida)
3. Encuentra tu acción
4. Sigue el procedimiento

### Nivel 2 — No encuentro qué hacer

1. Abre GitHub `sandbox33/SPEL-4.0/.axiom/handoffs/`
2. Abre el handoff más reciente (mayor número de sesión)
3. Ve la sección `next_session_priority_queue` — ese es tu próximo paso

### Nivel 3 — El sistema parece roto

1. NO toques nada
2. Abre Claude
3. Inicia chat dentro del Project AXIOM
4. Sube los 3 archivos: handoff último + `axiom_master.xml` + `axiom_hashchain.jsonl`
5. Escribe: "Sistema en estado incierto. Audita y dime el estado real antes de cualquier acción."

### Nivel 4 — Perdí acceso a algo (móvil, cuenta, etc.)

1. Recurre a Bitwarden — todo está ahí cifrado
2. Si perdiste Bitwarden, usa los recovery codes que guardaste en papel
3. Si perdiste el papel, regenerar es la única opción — ve a P-03 (rotación)

### Nivel 5 — Pánico generalizado

1. Cierra todo
2. Toma un café
3. Recuerda: **la verdad vive en GitHub**. Mientras GitHub esté ahí, nada se perdió.
4. Vuelve al Nivel 1

---

## SECCIÓN 6 — GLOSARIO

**Hashchain:** Cadena de bloques donde cada bloque referencia criptográficamente al anterior. Si alguien altera un bloque viejo, el siguiente queda inconsistente y se detecta. Tu cadena local, no blockchain Ethereum.

**Minisign:** Sistema de firma criptográfica ligero usando Ed25519. Tu llave soberana.

**Passphrase:** La frase que protege tu clave privada. Sin passphrase, la clave es inútil aunque la tengan.

**XSD:** XML Schema Definition. El validador que define qué estructura debe tener un XML para ser válido. Tu "llave" del framework.

**Handoff:** Archivo JSON que cierra una sesión de trabajo con Claude y prepara la siguiente. Memoria persistente entre sesiones.

**Deviation:** En AXIOM, una decisión arquitectónica documentada formalmente con tu firma. Te protege contra "lo decidí en la madrugada y se me olvidó por qué".

**EF (Ej. EF-23, EF-24):** Errores Fatales declarados en la constitución de AXIOM. Patrones que NUNCA debes repetir.

**LEY:** Reglas inmutables del sistema. Su violación detiene la operación.

**Cluster:** Agrupación lógica de módulos del código SPEL por función (00_INFRAESTRUCTURA, 01_HOLMES_CORE, etc.).

**Audit_status:** Estado de verificación de un módulo. Valores: VERIFIED, UNVERIFIED, LOG_ONLY, CONFLICT, OBSOLETE, STALE.

**Classification:** Nivel de secretismo de un módulo. Valores: PUBLIC, INTERNAL, CRITICAL, IMMUTABLE, SOVEREIGN_CIVIL_USE, PROPRIETARY_TOTAL_PRIVATE.

**Sovereignty residue:** Riesgo de soberanía declarado honestamente. Ej: "uso Google Drive como cache aunque no es soberano, lo migraré después".

**Vibe coding:** Programar con asistencia de IA entendiendo conceptos pero sin escribir código manualmente. Tu estilo de trabajo válido.

---

## SECCIÓN FINAL — RUTINA DIARIA RECOMENDADA

Cuando vuelvas a trabajar en AXIOM o SPEL, sigue este protocolo:

**Apertura de sesión (5 minutos):**
1. Abre Claude → Project AXIOM → "Nueva conversación"
2. Sube handoff de última sesión + XML maestro + hashchain
3. Escribe: "S<N>. Audita estado. ¿Qué sigue en priority queue?"
4. Claude verifica y te dice el próximo paso firme

**Trabajo (variable):**
- Hacer SOLO lo de la priority queue
- No abrir nuevos frentes sin terminar el actual
- Si descubres algo nuevo, anótalo como ítem pendiente en handoff, no lo persigas ahora

**Cierre de sesión (5 minutos):**
1. Pedir a Claude: "Genera handoff S<N> con estado actual y priority queue actualizada"
2. Descargar el handoff
3. Subir a GitHub `.axiom/handoffs/`
4. Cerrar Claude

**Esta rutina te garantiza:**
- Cero contexto perdido entre sesiones
- Cero ansiedad de "dónde quedé"
- Cero dependencia de tu memoria — todo está en archivos auditables

---

**Fin del Runbook v1.0**

Si este documento se siente incompleto o algo te genera dudas, vuelve a Claude y pide:
> "Actualiza el Operations Runbook con la sección X."
