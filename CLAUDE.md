# SPEL — Instrucciones para agentes

SPEL es un sistema de trading cuantitativo de un solo operador (Altair). Fase 1
cerró con resultado negativo medido: la entropía geopolítica (GDELT) predice
magnitud de volatilidad, no dirección. La investigación avanza por hipótesis
pre-registradas (serie H, ver BLUEPRINT.md). Estado actual: ESTADO.md.

## Fuentes y contradicciones
- El repositorio es la única fuente canónica (DG-1). El código en `main`
  manda sobre cualquier documento.
- Si dos documentos se contradicen, o un brief contradice el código o los
  datos: detente y reporta la contradicción con evidencia. No elijas.
- Ningún número entra sin fuente: medido en el código, en datos reales o en
  documentación oficial citada con fecha. Lo no verificado se escribe como tal.

## Tu trabajo es cazar errores
Las tareas de ingesta, modelado y backtesting sobre datos públicos son trabajo
estándar de ingeniería: hazlas. Tu escrutinio va al código y a los datos:
- Verifica cada supuesto del brief contra el código antes de implementarlo.
- Todo número que cruza un umbral se verifica contra una referencia
  independiente: en este proyecto, cada vez que uno lo cruzó, fue un defecto.
- Todo cambio de criterio lleva un test que lo separe del anterior.
- Toda lógica nueva lleva mutantes; un mutante que sobrevive es un test flojo
  o código muerto.
- Si un test existente codifica el comportamiento viejo, repórtalo; no lo edites.

## Reglas duras
- Portar, no reescribir: audita la implementación legacy exacta (no su
  comentario) antes de escribir algo equivalente.
- Nunca importar desde `archive/*`. Nunca Termux, Streamlit, yfinance,
  IQ Option, MetaTrader.
- Nunca rutas de Colab fuera de `governance/persistence.py`. Secretos solo
  vía `governance/secrets.py`; nunca en logs, prints ni commits.
- Una sola implementación por concepto; antes de escribir una función,
  busca si ya existe.
- APIs oficiales únicamente.

## Brókers y órdenes
- Deriv es el único bróker para capital real. Alpaca: solo paper.
- Capital de referencia: 100 USD (DG-2).
- Órdenes reales: prohibidas hasta que una hipótesis H apruebe sus
  compuertas, cumpla DG-3 en demo (`governance/paso_a_real.py`: PSR y DSR
  ≥ 0,90 sobre histórico + demo, ≥ 30 días y ≥ 20 operaciones cerradas,
  costos ≤ 1,25 × modelados, reconciliación limpia; máximo 6 meses) y
  llegue la Fase 4.
- Órdenes demo: solo desde `integracion_demo/`, con OTP emitido para una
  cuenta cuyo `account_type` sea `demo` según `GET /accounts`, y conexión solo
  a `/ws/demo`. Fuera de ahí, el acceso a Deriv es de solo lectura.
- Las sondas en `tests/` pueden pedir OTP y cotizar (nunca comprar) solo con
  autorización explícita del Admin en un brief fechado, registrada en el
  decision-log.

## Congelados (no tocar sin brief explícito)
- `execution/circuit_breaker.py`, `execution/execution_guard.py`: hasta Fase 4.
- `core/execution_costs.py`, `core/trade_ledger.py`: acta del 21-sep-2026;
  se reabren solo por sus condiciones de reversión, con entrada en el
  decision-log.
- Documentos de pre-registro ya fusionados: fijados por sha256 en su test.
- `git diff --name-only origin/main...HEAD -- execution/` tiene que estar vacío.

## Datos
- Rama `data`: series diarias y manifiestos; su único escritor es CI.
  Drive: datos pesados. Todo append-only; las revisiones se registran aparte;
  los huecos no se rellenan.
- Deriv no entrega volumen (`volume_available=False`). Nada depende de él.
- GDELT tiene ciclo semanal fuerte: controla por día de la semana. Nunca
  `n_events` crudo.
- Filtrar filas y después recalcular retornos, nunca al revés.
- Velas: solo cerradas, hora del servidor, granularidad declarada, nunca
  inferida del espaciado.

## Investigación
- El criterio de aceptación se fija antes de ver resultados (pre-registro).
- Cada variante probada cuenta en el N del Deflated Sharpe.
- SciPy entra con H1-B y con su entrada en el decision-log; torch no está
  en requirements (Decisión #7) salvo decisión registrada.
- Todo trabajo pesado de Colab se entrega como `.ipynb` completo generado
  por el repo; el Admin solo lo ejecuta.

## Proceso
- Siempre rama + PR. Nunca push a `main`, nunca fusionar, nunca disparar
  workflows: eso lo hace el Admin.
- Antes de abrir el PR:
  pip install -r requirements.txt -r requirements-dev.txt
  pytest tests/ -q   (todo verde; el conteo se mide, no se copia)
  Dos clones limpios, 10 corridas.
- ESTADO.md se actualiza en el mismo PR que cambia el estado; el test
  anti-desfase falla si quedan más de 3 PRs sin actualizarlo (DG-6).
- Un cambio lógico por commit; el mensaje explica el porqué.
- Español neutro con tú. Nunca voseo (hay un test que lo revisa).
