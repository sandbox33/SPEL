# ESQUEMA DEL EXPEDIENTE — versión 2

Ubicación: `docs/expediente/ESQUEMA.md` en cada repo de proyecto. Fuente de verdad del formato que importa HumanOS.

Reglas:
- `confianza`: solo `verificado` (con evidencia: archivo:línea, commit o id de run) o `inferido`.
- `fuente`: `hechos` | `claude` | `altair`.
- Aristas: un solo origen `de` y un solo destino `a` por arista.
- IDs estables; nada se borra, cambia de estado.
- Repo público: sin datos personales, montos, capital ni compromisos con terceros.

```json
{
  "esquema": 2,
  "proyecto": "", "actualizado": "AAAA-MM-DD", "estado": "activo|pausado|congelado|terminado",
  "objetivo": "",
  "fases": [{"id":"F1","nombre":"","estado":"pendiente|en_curso|bloqueada|hecha","estimado_dias":null,"inicio":null,"fin":null,"bloqueada_por":[]}],
  "tareas": [{"id":"T1","fase":"F1","titulo":"","estado":"pendiente|en_curso|hecha|descartada","creada":"","cerrada":null}],
  "ideas": [{"id":"I1","titulo":"","detalle":"","estado":"nueva|aceptada|descartada|hecha","creada":"","motivo":null}],
  "decisiones": [{"id":"D1","fecha":"","decision":"","motivo":"","reemplaza":null,"vigente":true}],
  "riesgos": [{"id":"R1","riesgo":"","impacto":"alto|medio|bajo","mitigacion":""}],
  "briefs": [{"archivo":"","estado":"vigente|reemplazado|ejecutado","reemplazado_por":null}],

  "mapa": {
    "nodos": [{"id":"N1","tipo":"disparador|job|modulo|dato|externo","nombre":"","archivo":null,"descripcion":"","fuente":"","confianza":"","evidencia":[]}],
    "aristas": [{"de":"N1","a":"N2","tipo":"dispara|llama|produce|consume","detalle":"","fuente":"","confianza":""}]
  },
  "incidentes": [{"id":"X1","nodo":"N2","sintoma":"","causa_raiz":"","estado":"abierto|resuelto|recurrente","veces":1,"primera":"","ultima":"","evidencia":[],"fuente":"","confianza":""}],
  "deuda": [{"id":"K1","nodo":"N2","tipo":"prueba_faltante|duplicado|valor_fijo|sin_doc|fragil|otro","descripcion":"","tamano":"S|M|L","creada":"","estado":"abierta|pagada","fuente":"","confianza":""}],
  "invariantes": [{"id":"V1","regla":"","cumple":true,"evidencia":[],"fuente":"","confianza":""}],
  "estudio": [{"id":"E1","nodo":"N2","pregunta":"","respuesta_ref":"archivo:línea","orden":1}],
  "contradicciones": [],
  "siguiente_paso": ""
}
```
