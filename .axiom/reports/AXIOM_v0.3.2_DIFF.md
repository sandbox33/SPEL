# AXIOM v0.3.1 → v0.3.2 — Hotfix de gobernanza

**Sesión:** S53
**Fecha:** 2026-06-14
**Tipo:** PATCH (compatible hacia atrás)
**Autor del sello:** Abraham Fuenmayor Hernández (Altair)
**Asistente:** Claude Opus 4.7 Extra
**Disparador:** Directiva estratégica de redirección — rol de Auditor de Seguridad

---

## Resumen en una frase

v0.3.2 es un hotfix de gobernanza que declara formalmente tres pilares: el anti-patrón **EF-24 STALE_SIGNAL_LOOP** (caso forense del bot Telegram congelado 9 días), la **ley operativa de TTL obligatorio** para todo el sistema de persistencia, y el **protocolo soberano de firma con minisign Ed25519**. El XSD canónico no cambia — esto es disciplina semántica.

---

## Qué cambió concretamente

### 1. Capa Constitución — 2 leyes nuevas + 1 fatal_error nuevo

**Nuevo `<fatal_error id="EF-24">`** — Declara el anti-patrón observado en producción: consumidores de estado que leen archivos cuyo timestamp no se ha actualizado en N segundos y siguen propagando la señal como si fuera vigente. La consecuencia documentada es decisiones financieras sobre datos viejos y pérdida de confianza forense. Caso documentado: bot Telegram operando 9 días con señal stale.

**Nueva `<law id="LEY_TTL_OBLIGATORIO">`** — La ley operativa que previene EF-24. Establece que TODO archivo de estado del sistema debe contener cuatro campos obligatorios: `ts`, `producer_id`, `validity_seconds`, `integrity_sha`. Todo consumidor debe verificar TTL antes de propagar. El workflow `axiom_audit_freshness` corre horariamente y marca módulos cuyas outputs excedan TTL como `audit_status=STALE`. Clasificación IMMUTABLE.

**Nueva `<law id="LEY_FIRMA_SOBERANA">`** — Declara minisign (Ed25519) como sistema oficial de firma criptográfica del proyecto. Justificación técnica documentada en la decisión: ligereza en Termux móvil, claves públicas de una sola línea, integración limpia con GitHub Actions sin dependencias complejas (descartó GPG por pesadez y age+signify por complejidad operativa relativa). La clave privada vive exclusivamente en Termux + backup cifrado en ProtonMail. La clave pública vive en `sandbox33/AXIOM/keys/seal_authority.pub`. Clasificación IMMUTABLE.

### 2. Capa Desviaciones — 1 desviación nueva (DEV-S53-001)

Registra formalmente la decisión de aplicar este hotfix con tu firma `SEAL_ABRAHAM_FUENMAYOR_2026-06-14_PENDING_MINISIGN`. El sufijo `PENDING_MINISIGN` indica que la firma criptográfica real con minisign se aplicará una vez que tengas las claves generadas (próxima sesión). Por ahora el sello es declarativo, pero quedará reforzado con la firma binaria cuando ejecutes el protocolo.

### 3. Capa Ledger Creativo — 1 artefacto nuevo (ART-2026-06-14-S53)

Notariado IA completo de esta sesión. Resumen del input humano (directiva estratégica + 3 preguntas) + resumen del output matemático (3 inserciones quirúrgicas validadas contra XSD) + ancla de precedencia temporal contra el hashchain. Evidencia forense para defensa de autoría.

### 4. Hashchain — Bloque 3 encadenado

```
seq=1  →  GÉNESIS B1               (2026-06-09)
seq=2  →  v0.3.1 4-layer sealed    (2026-06-13)
seq=3  →  v0.3.2 hotfix EF-24+TTL+MINISIGN  (2026-06-14)
```

Cadena criptográfica íntegra. Cada bloque referencia el `curr_hash` del anterior como `prev_hash`. Verificación matemática: 3/3 bloques con linkage correcto.

### 5. Checklist — 3 ítems nuevos verificados

- `CHK-EF24-DECLARED-01` — EF-24 declarado en constitución
- `CHK-TTL-LAW-01` — Ley TTL sellada con enforcement pendiente Fase B
- `CHK-MINISIGN-LAW-01` — Ley de firma soberana sellada

---

## Qué NO cambió

**Importante para tu comprensión arquitectónica:**

- **El XSD `axiom_schema.xsd` no cambió.** Mismo namespace `https://axiom.framework/v1`, misma versión `0.2.0`. Esto es deliberado: significa que v0.3.2 es **compatible hacia atrás** con cualquier validador que ya conozca v0.3.1. Disciplina de versionado semver real.
- **Los 65 módulos del State layer no cambiaron.** Sus SHA, paths, classifications permanecen idénticos.
- **La infraestructura no cambió.** Las 3 computes + 3 storages + 2 alert_routings siguen igual.
- **Los parámetros cuantitativos no cambiaron.** P90=252, KL=0.2, ISO=0.6 inmutables.

---

## Pendientes que este hotfix DECLARA pero NO IMPLEMENTA

Honestidad operacional: las leyes están selladas, pero su enforcement real requiere código que aún no existe.

1. **Workflow `axiom_audit_freshness.yml`** — Lee todos los archivos de estado, calcula edad, marca STALE. Implementación: Fase B.
2. **Pre-commit hook de validación TTL** — Rechaza archivos de estado sin los 4 campos obligatorios. Implementación: Fase B.
3. **Generación real de claves minisign** — Necesitas correr `minisign -G -p seal_authority.pub -s seal_authority.key` en Termux. Próxima sesión te guío paso a paso.
4. **Workflow `gate_3_signature_verify.yml`** — Verifica firmas minisign en PRs. Implementación: Fase B.

Durante la ventana entre v0.3.2 y la implementación de Fase B, el sistema **declara las leyes pero aún no las enforza automáticamente**. El bot Telegram aún puede congelarse (porque su código no ha sido refactorizado para implementar TTL). Lo que ganaste con v0.3.2:

- Cualquier IA futura que lea el XML conoce las leyes y se rehúsa a generar productores/consumidores sin TTL.
- Tienes un manifiesto formal en hashchain que documenta el bug del 9-días-Telegram como caso forense de precedente.
- La elección de minisign está sellada — ninguna sesión futura debe debatir GPG vs age+signify.

---

## Estructura de directorios — qué hacer con estos archivos (síntesis)

Tu pregunta 1 de la directiva. Respuesta operacional:

```
GitHub: sandbox33/AXIOM (privado al inicio)
  ├── schema/axiom_schema.xsd               ← este archivo (estable, canónico)
  └── docs/CHANGELOG.md                     ← incorpora este DIFF como entry v0.3.2

GitHub: sandbox33/SPEL-4.0 (privado permanente)
  └── .axiom/
      ├── axiom_master.xml                  ← LA verdad del proyecto
      ├── axiom_hashchain.jsonl             ← LA cadena
      ├── handoffs/session_handoff_S53.json ← cierre de esta sesión
      └── reports/AXIOM_v0.3.2_DIFF.md      ← este documento

Google Drive: ORDEN/SPEL 4.0/AXIOM_MIRROR/
  └── (sync read-only desde GitHub, NO editar aquí)
```

---

## Próxima sesión (S54)

Cambio recomendado de motor: **Opus 4.8 Max** para Fase B (AST parsing de 8 dumps, 26K líneas, trabajo verificable pesado).

Primera acción de S54:
1. Validar hashchain (3 bloques deben estar íntegros)
2. Generar par de claves minisign en Termux
3. Subir clave pública a `sandbox33/AXIOM/keys/seal_authority.pub`
4. Re-firmar DEV-S53-001 con firma minisign real (eliminar el `PENDING_MINISIGN`)
5. Arrancar Fase B: AST parsing de los clusters
