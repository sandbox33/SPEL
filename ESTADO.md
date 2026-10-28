# SPEL — ESTADO DEL PROYECTO

> **El repositorio es la única fuente canónica (DG-1, decision-log 25-sep-2026).** El
> código en `main` manda sobre cualquier documento, este incluido.
> `SPEL_MANUAL_OPERACION.md` (Drive, 7-sep-2026) dejó de ser canónico: el Admin lo
> regenera desde el repo o lo archiva en Drive. La regla anterior —"si este archivo y el
> manual divergen, gana el manual", sin verificar cuál se escribió después— quedó
> eliminada.
>
> Este archivo se lee primero en cada chat nuevo. Frente a un chat que lo contradiga sin
> evidencia, gana el archivo; frente al código, gana el código.

**Última actualización:** 28 oct 2026 — brief del Admin 16a en la rama `claude/brief-16a-compuerta-rango`, **detenido en la conexión del paso 3 a la corrida: espera la autorización del Admin para editar 4 casos de test y un fixture de `main`**.

La corrida 39209167984 del 19-oct (`main` en `952d082`) dio la compuerta oficial del paso 2:
- **BTC:** verde, con 364 de 364 pares y 365 días comunes (2025-10-19 a 2026-10-18).
- **Oro:** verde, con 245 de 253 pares y 257 días comunes (2025-10-20 a 2026-10-16). La sensibilidad de 24:00 también da verde (237 de 253): el veredicto no cambia y la opción 2 queda.

Este PR (brief 16a) agrega cuatro piezas, todavía sin conectarlas a la corrida:
- **El paso 3** (`tools/concordancia_d1_paso3.py`): el máximo y el mínimo diarios, y la señal de ruptura para L ∈ {10, 20, 40}. Sus constantes se fijaron antes de leer una vela real.
- **Deriv en vivo** (`velas_en_vivo`): los últimos 365 días, sin guardar nada.
- **El informe para `data`:** `metrics/compuertas/concordancia_d1/<fecha>_<run_id>.json`, append-only, con el sha256 de los archivos de TwelveData y la atribución.
- **El interruptor `SPEL_INGESTA_CRUDA`:** si la variable no vale exactamente `true`, el workflow no baja ni commitea velas.

**Detenido:** falta conectar las piezas a la corrida:
- el paso 3 a la compuerta (candado 3);
- Deriv en vivo;
- guardar el informe.

Hacerlo exige editar 4 casos de test de `main` y su fixture, y CLAUDE.md dice "repórtalo; no lo edites". Con esas ediciones, el prototipo pasa la suite entera. Lo decide el Admin; mientras tanto, el PR queda en borrador.

**Hallazgo:** con la ingesta apagada, el oro tiene menos de 250 días comunes desde el 29-oct (249, y uno menos por día hábil), y su paso 2 queda rojo por falta de días.

**Sigue abierto, para el Admin:** si el horario debe leer el evento "Fridays: Closes early (at 20:55)".
- El #48 se fusionó sin leerlo, y la corrida 39209167984 cerró el viernes con la vela que termina a las 21:00.
- Ninguna entrada del decision-log lo cierra.
- Ahora alcanza también al máximo y al mínimo del oro en el paso 3, que usan las mismas velas.

Detalle en el decision-log del 28-oct.

**Commit de referencia:** `952d082` (merge del PR #48, el brief 15).

**Tests:** **2416** recolectados en `tests/`, en 72 archivos, medido con
`pytest --collect-only -q tests/` el 28-oct-2026 sobre esta rama. Más 74 en `research/tests/`,
que no bloquean.

Desfase: **ninguno**. El commit de referencia es el merge del PR #48, el último de `main`, y esta
actualización entra con el PR del brief 16a. `tests/test_estado_al_dia.py` falla si hay **más de 3
PRs fusionados** desde el commit de referencia de arriba (DG-6).


**Ver también:** `FASE2_NOTAS_ARQUITECTURA_MODELO.md` (raíz del repo) — glosario y
opciones de arquitectura de modelo (LSTM vs. árboles/ensambles), separado de este
archivo a propósito para no mezclar "estado actual" con "notas de investigación".

---

## 🚦 SEMÁFORO DE FASES

```
FASE 1 — ingestion/ + core/scoring.py     🔵 CERRADA — checklist cumplido, resultado NEGATIVO
FASE 2 — Modelo                            ⚪ NO INICIADA — reorientada, ver abajo
FASE 3 — visualization/ (grafo)            ❄️ CONGELADA (D9) — no iniciada
FASE 4 — execution/ + Deriv real           🟡 Actuator confirmado; gate: modelo F2 o hipótesis H + DG-3
FASE 5 — Escala (Supabase, Flet)           ❄️ CONGELADA (D9) — no iniciada
FASE 6 — Motor streaming multi-timeframe   ❄️ CONGELADA (D9) — infraestructura lista, señal sin construir
SERIE H                                    🟡 H1 v2 (#45) y H2 (#34) pre-registradas, esperan las velas de D1 (P1): el paso 2 de la compuerta dio verde en BTC y en el oro (corrida 39209167984, 19-oct); el paso 3 (máximos, mínimos y señal, brief 16a) está escrito, y conectarlo a la compuerta espera la autorización del Admin; H3 (#33) pre-registrada y ⏸ BLOQUEADA hasta tener historia validada para su universo (P2); H1 v1 sellada; H-ORB-oro ⏸ PAUSADA (D11)
```

⏸ **H-ORB-oro, pausada (D11).** Su compuerta de datos dio roja: TwelveData y Deriv coinciden
en dirección y vela de la primera ruptura en el **62,74 %** de 263 días, contra un umbral del
**90 %** fijado antes de medir (rama `data` en `bc2597a`, decision-log 17-oct). Se reabre con
**756 días hábiles de M5 de Deriv** (D11-u, `governance/ensayos.py`). El 14-oct había 263.

❄️ **Fases 3, 5 y 6, congeladas (D9, 14-oct).** Nada de infraestructura sin edge demostrado, y
GDELT queda solo como proxy de volatilidad (decision-log 17-oct). La ingesta diaria de GDELT
sigue: es el dato de H2.

🔵 **CERRADA no es 🟢 LISTA.** Fase 1 se cierra porque su pregunta quedó contestada, y
la respuesta fue que no. El pipeline funciona, está medido y tiene `n` suficiente; lo
que no funciona es la hipótesis que ese pipeline existía para probar.

**Y el 16-sep se sacó la consecuencia.** El criterio de cierre de Fase 1 era que el
Gold Score *se calculara*, no que predijera — y se calculaba. Pero calcularlo todos
los días y emitirlo con una advertencia de cinco líneas pegada para que nadie lo usara
no era una salida del sistema: era ruido con escolta. La cadena se retiró a
`research/` (PR #27). El ciclo diario emite ahora el **régimen medido** y nada más.
Eso no reabre Fase 1 ni cambia su resultado; ordena el código para que coincida con
él.

---

## 📍 MÓDULOS REALES EN `main` HOY (verificado, no listado de memoria)

**1912 recolectados en `tests/`, en 60 archivos**, contados con `pytest --collect-only -q`
el 17-oct-2026 sobre la rama `claude/brief-10-gobernanza`, no copiados de ningún
documento. Sin credenciales se saltan 6, cada uno por su propio `skipif`: el test `live` de
TwelveData, el guardián de secretos de `tests/test_sources.py`, las dos sondas de endpoints de
Deriv, la 3b de TwelveData y la orden demo de la parte B.

**Más 74 en `research/tests/`, que NO corren en el job que bloquea** — son los tests del
código retirado (ver la sección de `research/` más abajo). Mezclarlos en una sola cifra
escondería la distinción que el retiro del 16-sep existe para marcar.

Este archivo decía **725**, de la actualización del 16 de septiembre.

Todo lo de abajo corrió en clon 100% ajeno, venv limpio desde `requirements.txt`, 10
corridas seguidas sin intermitencia.

| Módulo | Qué hace | Tests | Estado |
|---|---|---:|---|
| `core/scoring.py` | `entropy_state` (Capa 1), `godel_active`, `compute_godel_p66`, `compute_vitality_tesla`, `classify_gdelt_event`, `compute_adaptive_percentile` | 101 | ✅ |
| `core/monte_carlo.py` | Validación GBM — NO entrena, simulación pura en cada llamada | 22 | ✅ |
| `core/estadistica_sharpe.py` | La estadística del Sharpe en un solo lugar: σ² generalizada (ρ, γ₃, γ₄), PSR con σ[SR₀], MinTRL con z de parámetro, SR* de §9 y DSR = PSR(SR*); sin numpy ni scipy | 54 | ✅ offline; 🟡 fórmula UNVERIFIED contra el paper (sí contra el código de réplica de los autores) |
| `core/execution_costs.py` + `trade_ledger.py` | P&L neto con desglose de costos; ledger append-only en el stream TRADE_LEDGER | 46 + 33 | ✅ código, 🧊 congelados (acta del 21-sep) |
| `ingestion/adapters.py` | DerivAdapter + TwelveDataAdapter + contrato de datos | 65 + 29 | ✅ |
| `ingestion/sources.py` | **Punto de composición** — `build_price_sources()`; `SourceInventory` distingue capacidad ausente de error | 11 | ✅ |
| `ingestion/gdelt.py` + `_aggregation` + `_series` | Pipeline GDELT completo, persistencia JSONL | 14 + 15 + 18 | ✅ |
| `ingestion/run_gdelt.py` + `.github/workflows/gdelt.yml` | Ingesta diaria; CI escritor único en la rama `data`; reintenta los días que GDELT no había publicado | 59 + 12 | ✅ corre sola desde el 23-sep: seis corridas del cron, todas verdes; marca de inicio 2026-09-04 |
| `ingestion/frescura.py` | Alarma de la serie: rojo solo por hueco interno posterior a la marca de inicio | 18 | ✅ |
| `tools/verificar_siembra.py` | Compara BTC/XAU sembrados contra la serie medida | 14 | ✅ verde contra la siembra real (corrido el 29-sep, ver decision-log) |
| `ingestion/deriv_ws.py` | Sesión de solo lectura con Deriv: `entorno` obligatorio, `real` con permiso aparte, lista blanca de siete mensajes | 29 | 🟡 habla con el endpoint legacy, muerto desde el 01-oct (HTTP 520): falta migrar al WS público |
| `ingestion/sonda_instrumentos.py` + `.github/workflows/sonda.yml` | Contratos, multiplicadores, stake y comisión de BTC y oro, desde la API, siete días | 19 + 6 | 🟡 usa `deriv_ws.py`, así que depende de la misma migración; 🔴 cron apagado en GitHub por el Admin el 14-oct (D8), tras 7 corridas en rojo. El YAML y `test_son_diarios` no se tocan: lo reactiva el brief de migración de `deriv_ws.py` (D8-r) |
| `ingestion/velas_intradia.py` + `.github/workflows/velas_intradia.yml` | Velas M5 del oro en `data`: TwelveData XAU/USD 5min desde 2020-03-16 y Deriv frxXAUUSD M5 por `/ws/public`, con el patrón de `velas.py`; horario de `trading_times` crudo con sha256; una página vacía de TwelveData es el fondo solo tras una página corta (brief 07-oct (6)). Desde la 14b, la descarga y la escritura reciben la serie; sin parámetros, todo sigue siendo el M5 | 43 + 6 | ✅ segundo run el 14-oct (38610611624, rama `data` en `bc2597a`): TwelveData 499.252 velas M5 desde 2020-03-16 y Deriv 70.718 desde 2025-10-08; el primero (08-oct) se había cortado en el 400 del fondo |
| `ingestion/deriv_publico.py` + `ingestion/limitador.py` | Canal público de Deriv y paginación de velas, y el limitador de TwelveData, portados de las sondas. Desde el 16a, `velas_en_vivo`: las velas de Deriv de los últimos 365 días según la hora del servidor, solo cerradas y válidas, sin guardar nada (los API Terms de Deriv, según el brief) | (en los de arriba) + 9 | ✅ offline; 🟡 `velas_en_vivo` nunca pidió en vivo |
| `ingestion/velas_d1.py` + `.github/workflows/concordancia_d1.yml` | Velas diarias de D1 en `data`, con la descarga y la escritura de `velas_intradia.py` parametrizadas por serie: TwelveData 1day de BTC/USD y XAU/USD, toda la historia (D1-p); Deriv 86400 de cryBTCUSD y frxXAUUSD por `/ws/public`; la muestra 1h de BTC, 60 días. Guarda el sha256 de cada página. Publica los créditos estimados (≤ 9) antes del primer pedido. Workflow solo manual, sin inputs (D8). Desde el brief 15 pone al día el M5 del oro de las dos fuentes, después de las diarias, con sus créditos aparte. Desde el 16a, el interruptor `SPEL_INGESTA_CRUDA`: si no vale exactamente `true`, el workflow no baja ni commitea velas, por las licencias de TwelveData y Deriv (la gobernanza de los datos crudos, brief 20) | 32 + 6 + 14 | ✅ corridas 39129395827 (18-oct, `data` en `c2a7b19`: 5 créditos de 9 estimados) y 39209167984 (19-oct); 🟡 el interruptor nunca corrió |
| `tools/concordancia_d1.py` | Por activo (D1-i). Paso 1: en BTC, la hora de corte de TwelveData; en el oro, el corte de Deriv (su 86400 contra su última vela M5); un activo que no llega al 95 % (o, en BTC, no corta a las 00:00 UTC) se detiene sin su compuerta y el otro sigue. Paso 2, la compuerta: r de cierre a cierre, signo (o los dos < 1 pb) y \|Δr\| ≤ 0,2 × mediana(\|r_Deriv\|), verde con ≥ 90 % y ≥ 250 días comunes; la regla de `41cf853`, fijada con un test. La diaria de TwelveData del oro sale de su M5, solo de días terminados y con las velas dentro del horario de Deriv (D1-o, opción 2 del Admin), con la regla de 24:00 como sensibilidad; su 1day no se lee (D1-x). Ningún desenlace (guarda AST portada). Desde el 16a: el informe para `data` (`informe_para_data`, `guardar_informe`, `resumen_markdown`), append-only, con el sha256 de los archivos de TwelveData y la atribución, y `faltan_archivos_twelvedata` | 78 + 19 + 6 | ✅ corrida 39209167984 (19-oct, `main` en `952d082`): BTC verde (364 de 364 pares, 365 días comunes); el oro verde (245 de 253, 257 días) y su sensibilidad de 24:00 también (237 de 253). 🟡 el informe para `data` no está conectado a la corrida (brief 16a, detenido) |
| `tools/concordancia_d1_paso3.py` | Paso 3 de D1 (brief 16a), sobre los días comunes del paso 2. Rango: el máximo y el mínimo diarios coinciden a `TOL_RANGO` = 0,10 del rango de Deriv; verde con ≥ 90 % de los días con rango, y los de rango cero se cuentan aparte. Señal: la ruptura del máximo de las L barras anteriores para L ∈ {10, 20, 40}, verde con ≥ 90 % en cada L con ≥ 10 días con ruptura (si no, "sin potencia"). El oro arma su máximo y su mínimo desde el M5, con las velas de D1-o. La compuerta del activo es verde solo con el paso 2 y el paso 3. Lee solo el máximo y el mínimo (guarda AST propia) y no abre archivos | 46 + 7 | 🟡 escrito, con mutantes; no conectado a la compuerta (espera la autorización del Admin); nunca corrió con velas reales |
| `tools/concordancia_xauusd.py` | Rango de apertura 08:00 Londres y primera ruptura hasta las 12:00 por fuente, filtro de calendario, métricas y compuerta del 90 %; ningún desenlace (guarda AST + serie espía). Desde el brief 15, `franja` deja el viernes desde el primer cierre fuera del horario (antes quedaba dentro de 22:00 a 23:55; la compuerta no cambia), y el horario vigente lo usa también la concordancia diaria | 43 + 24 | 🔴 compuerta roja con datos reales: 62,74 % contra 90 % (data@bc2597a, 14-oct) |
| `ingestion/adapters.py::TwelveDataAdapter.fetch_pagina` | XAU/USD en el mapa (evidencia 3b, autorización del 06-oct (4)); página con `start/end_date` y cierre por la hora del servidor; vacías: el 404 "data not found" y el 400 "No data is available on the specified dates" (prefijo exacto) | 30 | ✅ offline |
| `ingestion/reintentos.py` | Reintentos de `fetch_pagina` (issue #39): 429, 500/502/503/504 y cortes de red; espera del `Retry-After` o, si no viene, 60 s para un 429 y backoff 2·2ⁿ (tope 30 s); a lo sumo 3 reintentos y 180 s de espera; cada reintento consume un turno del `Limitador`; sin limitador no reintenta | 63 | ✅ offline; 🟡 ningún 429 ni 5xx real visto todavía; que TwelveData mande `Retry-After` no está verificado |
| `ingestion/velas.py` + `.github/workflows/velas.yml` | Velas diarias de BTC y oro en la rama `data`, profundidad usable, `leer_velas()` en polars; desde el 16a, "solo cerradas" vive en `esta_cerrada()` | 33 + 6 | 🟡 ídem; además, Deriv entrega 365 días de historia (sonda §0.A-2); 🔴 cron apagado en GitHub por el Admin el 14-oct (D8), tras 7 corridas en rojo. El YAML y `test_son_diarios` no se tocan: lo reactiva el brief de migración de `deriv_ws.py` (D8-r) |
| `core/preregistro_h1.py` + `research/preregistro_h1.md` | Reglas del experimento H1 v1, fijadas antes del backtest | 26 | 🔵 sellado y nunca corrido; H1 v2 cierra sus dos cláusulas pendientes |
| `core/preregistro_h1_v2.py` + `research/preregistro_h1_v2.md` | H1 v2: sin alternativa (D12), lo intermedio no concluyente (D5), PSR y DSR de `core/estadistica_sharpe.py` con σ en SR₀ y el ρ de la serie; lo que no cambia, importado de v1 | 74 | 🟡 pre-registrada, no corrida; corre con la serie diaria de BTC de D1, cuando el brief 14 la valide (P1). Si sale rojo para BTC, no hay veredicto |
| `core/preregistro_h2.py` + `research/preregistro_h2.md` | H2 según D6: entropía de `t−2` primaria y de `t−1` secundaria, horizontes 1, 5 y 22, Diebold–Mariano con Holm y MCS, el día de la semana solo en entrenamiento | 58 | 🟡 pre-registrada, no corrida; corre con las velas diarias de D1, cuando el brief 14 las valide (P1). Si sale rojo para BTC, no hay conclusión |
| `core/preregistro_h3.py` + `research/preregistro_h3.md` | H3: la regla de tendencia de H1 en cada activo de un universo por reglas, voto por activo, sizing 0,25/√K por volatilidad con escalador de cartera; D7 declara el oro que ya está en `data`; el paso a real según D2 y DB-3 | 84 | ⏸ pre-registrada y bloqueada hasta que haya historia validada para su universo (P2); su N, las estrategias del registro (P3); su Sharpe, el de H1 v2 (P4); la zona gris, no concluyente y sin demo (P5) |
| `ingestion/kappa_deriv.py` | κ = comisión/nocional de MULTUP por activo, máximo medido en la cuenta real (sonda §0.A-3b, decision-log 06-oct) | 6 | ✅ dato medido, sin consumidor todavía |
| `ingestion/source_registry.py` | Registro versionado de cobertura por fuente | 34 | ✅ |
| `tools/measure_godel_samples.py` | Mide el `n` post-máscara | 75 | ✅ |
| `tools/provider_coverage.py` + `import_gdelt_entropy.py` + `audit_data_lake.py` | Inventario de proveedores, import histórico de entropía, auditoría del lake | 58 + 36 + 32 | ✅ |
| `tools/calibrar_umbral_entropia.py` | Umbral de entropía por activo, con procedencia | 22 | ✅ código, 🟡 `config/calibracion_activos.json` todavía no se generó |
| `ingestion/training_dataset.py` | Une OHLCV + serie GDELT, forward-fill, coverage_ratio explícito | 7 | ✅ |
| `orchestration/cycle.py` | Emite el **régimen medido** sobre 5 activos (`entropy_state`, `godel_active`, `compute_godel_p66`, vitality); sella `godel_criteria_version`. Sin `gold_score` desde el 16-sep | 18 | ✅ |
| `execution/circuit_breaker.py` + `execution_guard.py` | Guardrails duros — congelados hasta F4 | 14 + 17 | ✅ |
| `governance/persistence.py` + `secrets.py` | 5 streams (TRADE_LEDGER desde el PR #25), SecretKey único | 19 + 10 | ✅ |
| `governance/estado.py` + `tests/test_estado_al_dia.py` | Control anti-desfase de este archivo (DG-6) | 16 | ✅ |
| `integracion_demo/otp.py` + `conexion.py` | OTP solo para la cuenta que `GET /accounts` dice demo, URL solo `/ws/demo`, un OTP nuevo por conexión, reconexión con backoff, ping como parámetro | 113 (con `ejecucion.py`) | ✅ offline; ✅ conectó en vivo una vez: parte B, 08-oct, run 37716067033 (1 OTP, 1 conexión; el servidor la cerró a los 59,7 s sin mensajes) |
| `integracion_demo/ejecucion.py` | Lista blanca de ocho mensajes; `buy` solo MULTUP/MULTDOWN con `stop_loss`; un `buy` sin respuesta no se reintenta | ídem | ✅ offline; ✅ una orden demo en vivo (parte B, 08-oct): MULTUP frxXAUUSD, stake 1 ×100, SL 0,10, vendida a los 62 s, profit −0,03 USD |
| `integracion_demo/registro.py` + `reconciliar.py` | Registro demo JSONL con cadena de hashes y crudo aparte (porta `trade_ledger.py`, no lo importa); reconciliación con `profit_table` y `statement`, y comisión cotizada contra la del contrato (DB-1/DB-2, `SCHEMA_VERSION` 2): `reconciliado_profit`, `reconciliado_comision` y un global en que `no_verificable` nunca es `ok`; `conteo_de_marcas` da los tres conteos de d) de DG-3, con el mismo `ok_vigente` que `reconciliar` | 120 | ✅ offline; 🟡 parser de `proposal_open_contract` sin fijar |
| `tests/test_integracion_demo_orden_live.py` | Parte B: UNA orden demo, 07..16-oct-2026, job `orden_demo` de `live-tests.yml` | 13 | ✅ corrió una vez: 08-oct, run 37716067033, job `orden_demo`, 1 passed en 124,7 s; informe en el decision-log del 14-oct |
| `tests/test_guarda_integracion_demo.py` | `buy`/`sell`/`contract_update` solo en `integracion_demo/`; el paquete no nombra el canal real ni importa `execution/` ni lo congelado de `core/` | 34 | ✅ |
| `tests/test_reglas_ordenes_demo.py` | La regla de órdenes demo de CLAUDE.md (OTP para una cuenta demo según `GET /accounts`, solo `/ws/demo`) y DG-7/DG-8 en el decision-log | 3 | ✅ |
| `governance/paso_a_real.py` | DG-3 con D2 y DB-3: a) PSR y DSR solo sobre el histórico; d) sin discrepancias, no_verificables ni trades terminados sin reconciliar, contados desde el registro demo (`reconciliar.conteo_de_marcas`), y `incumplidas_d` dice cuál falta; la latencia, sin umbral (`sin_evaluar`); zona gris de 6 meses | 43 | ✅; 🟡 la latencia de D2 sin umbral medido |
| `governance/ensayos.py` + `ensayos.jsonl` | Registro único de ensayos (D3), append-only: los 3 previos fallidos, la concordancia del oro (no cuenta en el N), los dos rezagos de H2, la cartera de H3 y H1 v2, pre-registrados; `ENSAYOS_PREVIOS_FALLIDOS` de H1 y de H3 se deriva de acá. Qué ensayos son estrategias y suman al N del DSR (P3): los de H2 no. Un ensayo por id: un cambio de estado repite el id y no suma, y todo conteo sale de `vigentes()`, el último estado de cada id (14a). Un estado terminal no vuelve a uno abierto (brief 15) | 76 | ✅ |
| `config/constantes.json` | Registro de las 138 constantes de módulo, verificado contra el código en las dos direcciones | 30 | ✅ |
| `tests/test_registro_linguistico*.py` | Español neutro: sin voseo en `.py` y en `.md` | 84 + 5 | ✅ |
| `tools/heartbeat.py` + `.github/workflows/heartbeat.yml` | Trigger `schedule:` real — **desactivado a propósito**, ver Fase 6 | 19 | ✅ código, 🔴 apagado |
| `.github/workflows/*.yml` (runners y acciones) | Los 10 jobs de los 7 workflows en `ubuntu-24.04` fijo, no `ubuntu-latest` (que migra a Ubuntu 26 desde el 19-oct); `actions/checkout@v5` y `actions/setup-python@v6`, las primeras mayores con `using: node24` | 23 | ✅ en la rama; 🟡 ningún run todavía con las versiones nuevas |

`core/price_signals.py` ya no está en `core/`: se retiró a `research/` el 16-sep (PR #27),
con sus tests. Ver la sección de `research/`.

**No existe todavía, confirmado por ausencia real (no supuesto):** grep de
`^class.*Adapter` en `ingestion/adapters.py` da **dos** implementaciones concretas de
`BaseAdapter` — `DerivAdapter` y `TwelveDataAdapter` — más la base abstracta y las
excepciones. Cero `AlpacaAdapter`, cero `TiingoAdapter`. De los 4 activos del legacy,
TwelveData cubre BTC (`BTC/USD`) y abre la puerta a acciones (`AAPL` verificado); **XAU
y NIFTY50 siguen sin fuente**, y XAU específicamente quedó fuera del mapa por falta de
evidencia de que el plan gratuito lo cubra, no por olvido. Ningún trainer de LSTM.
Ningún ruteo de órdenes de ningún tipo.

**Los fixtures de TwelveData ya son capturas reales** (2026-08-23, literales): EUR/USD,
BTC/USD y AAPL en 1day. Reemplazan a los sintéticos con los que se escribió el adapter, y
**trajeron un hallazgo que desmiente el supuesto anterior: BTC/USD NO trae `volume`.** De
los tres instrumentos, el único con volumen es AAPL — la regla intuitiva "forex no,
cripto y acciones sí" es falsa.

El adapter no necesitó ni un cambio, y eso no es suerte: `volume_available` se deriva de
si la clave vino en la respuesta, nunca de una regla por clase de activo. Verificado por
mutación —sustituir la derivación observada por una regla declarada deja **4 de 28 tests
en rojo**, y el primero en caer es el que parsea la captura real de BTC. Una regla
declarada habría marcado BTC con volumen disponible y el relleno `0.0` habría entrado al
pipeline como si fuera un dato.

Quedan dos fixtures sintéticos, marcados como tales y con motivo: un contrafáctico
deliberado (forex *con* volumen — su valor está en que no puede existir en la realidad
observada, y es el que atrapa la regla declarada en la dirección inversa) y uno intradía
por necesidad (las tres capturas son diarias, y hay dos comportamientos intradía que
probar que dependen del argumento `timeframe`, no del payload).

🟡 **Sigue amarillo, no verde**, y por una razón más chica que antes: el test `live`
(marker `live`, skipif sobre la credencial) todavía no corrió nunca con clave real. Lo que
falta confirmar contra la API viva es el camino que ningún fixture ejercita — el cliente
httpx que el adapter abre y cierra solo (los tests offline inyectan el suyo), la
autenticación aceptada de verdad, y la forma intradía.

**Desde PR-4 ya hay cómo correrlo:** cargar `TWELVEDATA_API_KEY` en los Secrets del repo
y disparar `live-tests.yml` a mano (Actions → SPEL Live Tests → Run workflow). Es una
acción de un minuto, y es lo único que separa a este adapter de ✅.

**~~Hallazgo relacionado — no hay ningún punto de composición.~~ CERRADO (PR-4).** El
hallazgo del 18 ago decía: no es que falte un adapter, falta el lugar donde algo se arma
y corre de verdad. Eran tres mitades del mismo hueco, y las tres están cerradas:

| Mitad del hueco | Cómo estaba | Cómo está |
|---|---|---|
| Nada instancia un adapter fuera de tests | `DerivAdapter(` solo en su propia definición | `ingestion/sources.py::build_price_sources()` construye los dos |
| `load_secret()` no se llama desde producción | los 2 hits fuera de `tests/` eran docstrings | `sources.py` es el primer —y único— llamador real |
| Ningún workflow inyecta secretos | cero `secrets.` y cero `env:` en `.github/workflows/` | `live-tests.yml` inyecta `TWELVEDATA_API_KEY` por `env:` de job |

**El punto de composición es UNO, y hay un test que lo mantiene así.** Los adapters
reciben credenciales por constructor y nunca leen el entorno; `test_sources.py` verifica
**con AST** (no con grep, que daría falso positivo con los docstrings que hablan del tema)
que `ingestion/adapters.py` no importe `governance.*` ni `os`. Si mañana hay tres lugares
que resuelven credenciales, "¿por qué no arrancó tal fuente?" vuelve a ser una búsqueda en
vez de una lectura.

**Una fuente sin credencial es capacidad ausente, no error.** `build_price_sources()` nunca
lanza por una credencial faltante: devuelve un `SourceInventory` con lo que sí se pudo
construir y, para lo demás, el motivo nombrando la variable exacta
(`"faltan DERIV_API_TOKEN, DERIV_APP_ID"`). Deriv necesita las dos credenciales y el motivo
dice cuál falta — tener el token y que falte el `app_id` es un caso real, y un
"credenciales incompletas" obligaría a adivinar entre las dos.

**Lo que sigue faltando** es distinto y más chico: nada llama todavía a
`build_price_sources()` desde un ciclo real. `orchestration/cycle.py` corre scoring sobre
datos que recibe, no sobre datos que va a buscar. Esa es la conexión que falta ahora — ya
no "no hay dónde armar las piezas", sino "las piezas armadas no se usan todavía".

---

## 🔵 FASE 1 CERRADA — dos lecturas del mismo cierre

El checklist se cumplió **y** la hipótesis de fondo se refutó. Las dos cosas son ciertas
a la vez y ninguna sustituye a la otra, así que van las dos.

### 1. El criterio de BLUEPRINT.md: el Gold Score se calcula

`BLUEPRINT.md` fija el criterio textual:

> "`ingestion/` trae un OHLCV real de Deriv y un GDELT real, `core/scoring.py` calcula un
> **Gold Score real** a partir de eso, con un test que corre en CI y pasa."

El PR #19 (`compute_godel_score`) cerró la última pieza. Los tres componentes tienen
función real: `compute_godel_score`, `compute_transfer_entropy_proxy` y
`compute_backbone_score` — hoy en `research/` (`gold_score_chain.py` y `price_signals.py`),
retirados del motor el 16-sep. El test de cierre corre en CI y
verifica **el valor** —0.539239 sobre cierres deterministas— no que no lance excepción.

**La salvedad, y no es letra chica: el criterio exige que el Gold Score SE CALCULE, no
que PREDIGA.**

- `te_score` y `backbone_score` **fueron medidos y no son significativos**: cero
  supervivientes a Bonferroni y a Benjamini-Hochberg, holdout p = 0,4133 y p = 0,5921,
  y un backtest de reversión que perdió el 99,2% del capital en BTC. Acta completa en
  `decision-log.md`, entrada del 6-sep.
- `godel_score` vale **0.0** mientras no exista un LSTM que sirva `val_dir`.
- Los pesos **0.40/0.30/0.30 nunca se calibraron**. La etiqueta "inamovible (Regla 13)"
  del legacy documenta gobernanza, no un ajuste empírico.

Y un hallazgo estructural del propio PR #19: el término `w_godel * godel_score` **no
puede aportar a ningún gold_score distinto de cero**, ni con un `val_dir` de 1.0 —
cuando la máscara dispara, el kill por `godel_active` pone el score en 0.0; cuando no
dispara, el componente vale 0.0 por definición. El peso de 0.40/0.55 está muerto. La
causa es una rama de kill que agregó el port y que ninguna de las dos fuentes legacy
tiene; queda como tarea aparte, con test que la fija.

### 2. El resultado de la validación: la máscara no discrimina dirección

Medido el **4-sep-2026** sobre datos reales, criterio `4.0.0-entropy_state_p66`.
Detalle completo y método en `decision-log.md`.

**El `n` alcanzó.** BTC **1.211** post-máscara (5/5 folds estables), XAU **823** (4/5).
Los dos superan el umbral de `DEFENDIBLE`. Eso importa para leer el resultado: no es
"no había muestras suficientes para saber", es un negativo medido con potencia.

**Dirección — la máscara no discrimina.**

| activo | dentro del régimen | fuera | p |
|---|---|---|---|
| BTC | 51,81% [49,14–54,47] | 52,53% [50,79–54,26] | 0,68 |
| XAU | 50,98% [47,78–54,18] | 52,60% [50,58–54,61] | 0,42 |

Los intervalos se solapan casi por completo y, en los dos activos, la tasa **dentro** del
régimen es más baja que fuera. Autocorrelación de retornos en BTC: −0,028 dentro,
−0,026 fuera.

**Magnitud — sí discrimina, y solo en BTC.** Mann-Whitney: BTC ratio de volatilidad
**1,246**, p = 3,4×10⁻⁹. XAU ratio 1,115, p = 0,56.

**Qué significa.** Coincide con la literatura sobre índices de incertidumbre construidos
desde noticias: predicen magnitud, no signo — el EPU correlaciona 0,73 con el VIX, que
es un índice de volatilidad. La entropía geopolítica mide **cuánta turbulencia hay**, no
**hacia dónde va el precio**. La hipótesis original estaba mal formulada: se le pedía a
la señal algo que este tipo de índice no hace.

**Lo que NO invalida.** El pipeline de ingestion, la persistencia, el contrato de datos,
la integridad temporal y la máscara como tal siguen siendo correctos y medidos. Lo que
cae es el uso que se les estaba dando.

### Cerrada ≠ exitosa

La fase cierra porque su checklist se cumplió. Lo que el sistema producía al cerrarla era
un número calculado de punta a punta con funciones reales, y **no una señal operativa**.
Desde el 16-sep ese número ya no se emite: el ciclo diario emite el régimen medido y nada
más, y la cadena `gold_score` vive en `research/`.

---

## 🔒 CONTRATO DE DATOS OHLCV

Tres reglas que cualquier adapter nuevo tiene que respetar. No son estilo — cada una
existe porque su ausencia produce un fallo silencioso, que es la clase de bug que ya
costó meses en este proyecto.

**1. `require_closed` tiene semántica condicional, y es a propósito.** No significa
"siempre valida el cierre", significa "valida el cierre cuando es posible saberlo". Con
`granularity_s` presente rechaza velas abiertas; con `granularity_s=None` omite *solo*
esa verificación — columnas, tipos, UTC, orden, NaN y OHLC siguen activas. La
granularidad **nunca se infiere del espaciado entre timestamps**: un dataset con huecos
legítimos (fin de semana forex, feriados) daría una inferencia equivocada, y una
granularidad equivocada rechaza velas buenas o acepta abiertas. El call site que obliga
a que `None` sea válido es real: `ingestion/training_dataset.py:119` valida un dataset ya
construido, sin acceso a la granularidad original.

**2. `df.attrs` es transporte de UN SOLO SALTO, nunca almacenamiento.** El adapter
escribe la metadata de calidad en `attrs`; `AdapterChain` la levanta a `AdapterResult`
inmediatamente después del fetch, y ahí muere. Medido en pandas 3.0.5, no supuesto:

| Operación | `attrs` sobrevive |
|---|---|
| `copy()` | ✅ |
| `sort_values()` | ✅ |
| `concat()` | ✅ |
| `reset_index()` | ✅ |
| `merge()` | 🔴 **se pierde** |

`training_dataset.py` hace exactamente un join OHLCV↔GDELT. Usar `attrs` como
almacenamiento durable perdería la procedencia justo donde más importa —al armar el
dataset de entrenamiento— y en silencio, sin excepción ni warning. Por eso los campos
viven en el dataclass.

**3. `pandas>=2.2,<4` es la única dependencia pineada.** `attrs` es API documentada como
experimental por pandas y el diseño depende de su comportamiento exacto; sin pin, una
resolución distinta en CI podría cambiarlo sin que nadie toque el código. `numpy`,
`httpx`, `websockets` y `pytest` siguen sin pinear — nada del contrato depende de ellos.

**Red de seguridad armada.** `DerivAdapter.fetch_ohlcv()` llama a `validate_ohlcv_schema()`
con `granularity_s` aunque `_to_dataframe()` ya corrió el filtro: es redundante a
propósito, y en el camino normal no debería dispararse nunca. Si dispara, el filtro falló
o alguien lo removió. Verificado por mutación: quitar el argumento del call site deja
**64 de 65 tests en verde**; el único que lo detecta es
`test_deriv_arma_la_red_de_seguridad_pasando_granularity_s`, que verifica el argumento y
no el efecto justamente por eso.

**Nomenclatura — cuatro términos que no son sinónimos:**

| Término | Qué es | Ejemplo |
|---|---|---|
| `source` | el PROVEEDOR de los datos | `"deriv"`, `"twelvedata"` |
| `symbol` | el INSTRUMENTO | `"EURUSD"`, `"VOL75"` |
| `adapter_name` | qué adapter produjo el resultado (`AdapterResult`) | `"deriv"` |
| `is_fallback` | vino de una fuente de respaldo — **distinto de `is_degraded`**: un respaldo puede funcionar perfecto | `True` + `is_degraded=False` |

Ni `source` ni `symbol` deben recibir jamás algo derivado de un secreto: los dos se
escriben en el log.

---

## 🔬 FASE 2 — REORIENTADA por el resultado de la validación

**El modelo NO debe ser un clasificador direccional filtrado por entropía.** Esa era la
arquitectura implícita en todo lo anterior, y la medición del 4-sep la descarta: la
máscara no separa días direccionalmente predecibles de días que no lo son (p = 0,68 y
0,42).

**La vía con fundamento medido es dimensionamiento de posición.** Un régimen que
multiplica la volatilidad por 1,25 con p = 3,4×10⁻⁹ es información accionable para
decidir *cuánto* arriesgar. No lo es para decidir *de qué lado*. Todo lo que sigue en
esta sección se escribió antes de esa medición y hay que leerlo con eso en mente: sigue
siendo cierto como auditoría del legacy, y ya no describe el plan.

**Advertencia sobre el `n`:** el `n` de 1.211 y 823 fue medido para una pregunta
direccional (binomial sobre aciertos). Una pregunta sobre magnitud tiene otra potencia y
otro umbral; el `n` no se hereda entre preguntas distintas.

Auditado contra el legacy real (18 ago), sin cambios desde entonces:

- `te_score` y `backbone_score`: **listos**, portados con 2 bugs reales corregidos
  (hoy en `research/price_signals.py`: sin poder predictivo, acta del 6-sep).
- `godel_score`: depende de `val_dir`, salida de un **LSTM entrenado** — arquitectura
  canónica "Regla 13", **bloqueada por guardián real** (`enforce_lstm_architecture()`
  en el legacy lanza `RuntimeError` ante cualquier desvío de `input_size=20,
  hidden_size=64, num_layers=1` — existen 14 checkpoints `.pt` atados a esa forma
  exacta). No hay nada que portar hasta que ese modelo aprenda algo real.
- Accuracy base histórica confirmada (legacy, `LSTM_BASE_ACCURACY`): NVDA 0.550, BTC
  0.528, XAU 0.547, NIFTY50 0.625 — el "~0.50" que bloquea Fase 4 puede ser ese mismo
  techo bajo original, no necesariamente una regresión nueva.
- Causa raíz real del estancamiento (Altair, 18 ago): parquets fuente con columnas de
  fecha en formatos distintos entre sí ('/' vs '-') — datos no normalizados
  alimentando el entrenamiento sin detectarse a tiempo. **Ya no puede repetirse en la
  parte nueva**: Deriv entrega epoch (sin ambigüedad de formato posible),
  `validate_ohlcv_schema()` rechaza cualquier timestamp que no sea UTC estricto y
  ordenado — verificado con test de regresión directo.
- Clase de bug MÁS AMPLIA encontrada en el legacy (`spel_trainer_audit.py`,
  BUG-LA-01): normalizar con estadísticas del dataset completo en vez de solo train,
  o mezclar datos temporales antes del split. Documentado en
  `ingestion/training_dataset.py` para cuando se escriba el trainer — no resuelto
  todavía porque ese código no existe.
- `ingestion/training_dataset.py` ya construido: une OHLCV + serie GDELT con
  forward-fill, listo para alimentar cualquier arquitectura que se elija.

**Ver `FASE2_NOTAS_ARQUITECTURA_MODELO.md` para las opciones reales (LSTM vs.
Random Forest/XGBoost/etc.) — nada decidido todavía, es investigación, no un plan.**

---

## 🗂️ CÓDIGO LEGACY — sin cambios, ya estaba correcto

```
archive/legacy-pre-20260813              → 74 módulos originales, intactos
archive/dashboard-data-pre-20260813      → cache de GitHub Actions
archive/feature-cache-pre-20260813       → cache de features
archive/model-cache-pre-20260813         → cache de modelos (14 checkpoints .pt)
archive/limpieza-legado-99-pre-20260813  → última limpieza previa al reinicio
```

Regla fija sin excepción: nada bajo `ingestion/`, `core/`, `execution/`,
`orchestration/` o `visualization/` importa desde ninguna rama `archive/*`.

---

## ✅ GOBERNANZA DE DOCUMENTOS — resuelta el 25-sep (DG-1)

El hallazgo del 17 de agosto era que había varios documentos que se creían la fuente de
verdad y ninguno cedía. El 9-sep se resolvió a medias declarando canónico a
`SPEL_MANUAL_OPERACION.md`, en Drive, con este archivo como espejo. **DG-1 lo resuelve del
todo y en la otra dirección: el repositorio es la única fuente canónica.** El manual se
regenera desde el repo o se archiva; lo hace el Admin en Drive.

Queda en Drive, fuera del repo: archivar `SPEL_PERSISTENCE_STATE.md` y
`SPEL_PERSISTENCIA_v2.md`, que ya quedaban por debajo del manual y ahora quedan por debajo
del repo.

**05-oct: DG-7 y DG-8** (decision-log 2026-10-05). DG-7: el sistema puede reducir riesgo
solo; aumentarlo, cambiar un parámetro, reactivar una estrategia o reasignar capital solo
ocurre por una regla de un pre-registro sellado, que suma a N. DG-8: la batería
multi-estrategia va fuera de `execution/` (`strategies/` y un orquestador) y usa el circuit
breaker y el guard sin modificarlos. La regla de órdenes demo de CLAUDE.md dejó de citar
`authorize.is_virtual`, de la API retirada: ahora es OTP para una cuenta demo según
`GET /accounts` y conexión solo a `/ws/demo`.

---

## ❓ INCÓGNITAS REALES — sin resolver, no inventadas para llenar espacio

1. ~~**¿GitHub Actions ya verificó una descarga real de GDELT?**~~ **CERRADA. Sí, y
   todos los días.** Desde el PR #30 (21-sep) `.github/workflows/gdelt.yml` descarga GDELT
   con el cron diario y escribe en la rama `data` como escritor único. Medido el 29-sep
   sobre `data` en `11e45f6`: seis corridas del cron del 23 al 28-sep, las seis en verde;
   la marca de inicio quedó en 2026-09-04; BTC y XAU llevan 24 días escritos por CI sobre
   la siembra, y NVDA, NIFTY50 y EURUSD 15. El cron está programado a las 06:30 UTC y arrancó entre las 11:36 y las
   14:16 UTC: GitHub lo demora entre 5 y 8 horas, y no pasa nada, porque la ingesta decide
   qué día falta por lo que hay en la serie, no por la hora.

   *(La versión anterior de esta incógnita, del 9-sep, decía que ningún workflow invocaba
   GDELT porque `ingestion/run_gdelt.py` no existía. Era cierto entonces: el entry point
   llegó con el PR #24 y la escritura con el #30.)*
2. **¿GDELT tiene cobertura completa de 2026?** El auditor legacy
   (`spel_auditoria_total.py`) tenía un chequeo específico para esto
   (`GDELT_GAP_2026`) — no se corrió el equivalente contra el pipeline nuevo.
3. **¿El motor GDELT (Fases 1-5) sigue siendo el principal, o el motor rápido
   (Fase 6) cambia la prioridad?** Pregunta abierta desde el 17 ago, todavía sin
   que Altair la confirme. **D9 (14-oct):** la Fase 6 está congelada y GDELT queda solo
   como proxy de volatilidad. Cerrar esta incógnita es del Admin.
4. **`p66_entropy_global_default`** (necesario para `godel_active` vía
   `orchestration/cycle.py`): sin valor por defecto a propósito — `compute_adaptive_percentile`
   documenta que para este umbral no hay default legacy confirmado. Cada llamada real
   necesita decidir qué número usar. *(Se llamaba `p90_entropy_global_default` hasta la
   versión 4.0.0 del criterio; el nombre viejo describía un término que nunca cambió un
   resultado — ver `decision-log.md`, 6-sep.)*
5. **DEU como único proxy de "Eurozona/BCE"** en `GOBIERNO_COUNTRY_FILTERS`: ¿alcanza
   solo, o hace falta sumar FRA/ITA? Sin backtest, documentado como pendiente desde
   que se escribió `classify_gdelt_event`.
6. **GBPUSD/USDJPY/USDCHF/AUDUSD** ya tienen precio real (`DerivAdapter`) pero NO
   tienen clasificación GDELT — `FX_GOBIERNO_ONLY_ASSETS` solo cubre EURUSD. Decidir
   qué país no-USA representa a cada banco central (BoE/BoJ/SNB/RBA) sigue pendiente.
7. **Transfer Entropy real (Schreiber) vs. el proxy portado**: existe una versión más
   rigurosa en `spel_math_engine.py` (con Hurst, backend `pyinform`), pero ese archivo
   tiene 177 referencias rotas a numpy/polars sin auditar — no se tocó esta sesión.
8. **Índices de Volatilidad (VOL10-VOL100)**: tienen precio real, cero vía de scoring
   — GDELT no aplica por diseño (Fase 6), pero no existe todavía una alternativa
   técnica pura para ellos. `orchestration/cycle.py` los excluye a propósito.
9. **Isolation Forest**: el legacy ya lo usaba para detectar anomalías en la entropía
   GDELT (`PARAM_ISO`, umbral 0.6) — es de la misma familia que Random Forest. Nunca
   se portó al repo nuevo. Ver `FASE2_NOTAS_ARQUITECTURA_MODELO.md`.
10. **Alpaca**: cero código en el repo nuevo, solo la decisión de mantenerlo en modo
    paper (17 ago). Cuando el amigo de Altair complete la verificación con Banco
    Pichincha, hace falta construir el adapter desde cero — no existe ni un stub.

11. ~~**¿Hace falta un chequeo automático de desactualización de este archivo?**~~
    **CERRADA por DG-6 (25-sep).** Sí: `tests/test_estado_al_dia.py` falla si hay más de
    3 PRs fusionados desde el commit de referencia del encabezado, si ese commit no es
    ancestro de HEAD, o si el clon es superficial. Nació en rojo —5 merges desde
    `dd9ea63`— y lo puso en verde la actualización de este archivo en el mismo PR.

---

## 🖥️ SPEL_Control_Panel.ipynb — mejoras concretas para producción

No auditado línea por línea esta sesión (34.6 KB, no se justificó el costo de leerlo
completo todavía) — estas son mejoras identificadas por los síntomas reales que ya
aparecieron, no una revisión exhaustiva:

1. **Menú 4 (aplicar patch)**: defenderse de `.git/rebase-apply still exists`
   corriendo `git am --abort` automáticamente si ese directorio existe, antes de
   intentar aplicar — ya pasó una vez esta sesión, se resolvió reintentando a mano.
2. **Menú 7 (ver estado/historial)**: que lea el contenido real de este archivo en
   vez de reconstruir el estado a mano desde git log — reduce el riesgo de que
   ESTADO.md y lo que el panel muestra diverjan otra vez.
3. **Descarga de datos históricos para entrenamiento** (nuevo requisito, 18 ago):
   el flujo actual de `DerivAdapter` trae velas recientes para scoring en vivo, no
   un backfill masivo de meses/años para entrenar. **Contestado:** `ticks_history`
   tiene un tope de 5.000 velas por petición y se pagina hacia atrás con `end`
   (medido por `tools/provider_coverage.py::probe_deriv`, `DERIV_MAX_COUNT`).
4. **Separar "modo patch" de "modo entrenamiento"**: cuando exista un trainer real,
   correrlo desde el mismo panel de aplicar-patches mezcla dos flujos de trabajo
   distintos (uno es minutos, el otro puede ser horas). Mejor un menú aparte, no
   una opción más en la lista actual.

---

## 📦 `research/` — EL CÓDIGO RETIRADO (nuevo, 16 sep 2026)

Paquete nuevo en la raíz. Guarda lo que el motor diario **ya no ejecuta** pero que
sigue siendo válido como hipótesis, con sus tests, importable y corrible.

| módulo | qué | por qué salió |
|---|---|---|
| `gold_score_chain.py` | `compute_gold_score_bma`, `compute_godel_score`, `compute_nash_frozen_7d` y sus tipos | depende de un LSTM que no existe; y el término Gödel no puede aportar (hallazgo PR #19) |
| `price_signals.py` | `compute_transfer_entropy_proxy`, `compute_backbone_score` | tesis direccional refutada el 4-sep |
| `cycle_gold_score.py` | el cableado que los componía dentro de `run_scoring_cycle` | viajó con lo que componía |

**No es `archive/*`.** Los retiros del PR #22 fueron a ramas de archivo, donde el
código deja de correr — correcto para algo que no vuelve. Acá la **condición de
reversión está escrita**: si Fase 2 entrena el LSTM que produce `val_dir`, la cadena
vuelve. Por eso tiene que seguir compilando contra el motor vivo.

`research/tests/test_aislamiento.py` fija la dirección: **el motor nunca importa de
`research/`**. Sin ese test el retiro sería una afirmación sobre carpetas, no sobre
quién llama a quién.

CI: `tests.yml` tiene un job `research` con `continue-on-error: true` — visible si se
rompe, sin poder frenar un merge. A mano: `pytest research/tests/ -q`.

Ver la entrada del 16-sep en `decision-log.md` para el razonamiento completo y la
salvedad que hay que resolver **antes** de revertir.

---

## ▶️ PRÓXIMO PASO CONCRETO

**Brief 16a: que el Admin decida si autoriza editar los 4 casos de test y el fixture de `main`**
que codifican la compuerta sin el paso 3 (decision-log del 28-oct, 16a.5). Con la autorización,
la conexión entra en este PR y el PR sale de borrador. Recién después conviene fusionarlo y
disparar "SPEL concordancia diaria D1" sin definir `SPEL_INGESTA_CRUDA`.

Antes de disparar, el Admin decide también qué hace con el oro: con la ingesta apagada tiene menos
de 250 días comunes desde el 29-oct (decision-log del 28-oct, 16a.6).

H1 v2, H2 y H3 están pre-registradas y ninguna corrió. H1 v2 y H2 corren con las velas de D1 del
activo que pase la compuerta, con el paso 3 (P1). H3 queda bloqueada hasta que haya historia
validada para su universo (P2).

La cola, en orden:

1. Brief 16a: autorizar las ediciones, fusionar el PR y disparar la concordancia con la ingesta
   apagada.
2. Con la compuerta en verde: H1 v2 y H2, sobre las velas de D1 del activo que pase.
3. La migración de `ingestion/deriv_ws.py` al WS público nuevo. La legacy está muerta desde el
   01-oct, y su 520 dejó en rojo a `velas.yml`; esa migración reactiva los dos crons (D8-r).
4. Si H1 v2 o H2 muestran algo: el brief que extiende D1 al universo de H3, activo por activo,
   con la compuerta del brief 14 (P2).

La gobernanza definitiva de los datos crudos en `data` la decide el Admin en el brief 20.

**H-ORB-oro: pausada (D11)** hasta tener 756 días hábiles de M5 de Deriv (D11-u); ver el
semáforo.

*(Lo que estaba acá —"decidir qué mide el éxito de Fase 2"— lo contestan los
pre-registros de la Serie H —Sharpe neto fuera de muestra, PSR, DSR y la comparación contra
un benchmark—, que llegan con los PRs de H1 y H3. Ver `BLUEPRINT.md`, "Serie H".)*

---

## 📝 CÓMO ACTUALIZAR ESTE ARCHIVO

Al final de cada sesión de código (no a mitad):
1. Actualizar la tabla de módulos con lo que se verificó contra GitHub real.
2. Mover cualquier decisión nueva a `decision-log.md`, no acá — este archivo es
   estado, no bitácora de decisiones.
3. Actualizar "Próximo paso concreto" — una sola cosa, no una lista de deseos.
4. Commitear este archivo junto con el código de esa sesión, mismo commit o el
   siguiente inmediato — la lección del 17 ago fue exactamente no hacer esto.
5. Nunca dejar este archivo diciendo algo que no se verificó — si algo quedó a
   medias, decirlo explícitamente como 🟡, no como ✅.
6. Actualizar el **encabezado** —fecha, commit de referencia (el de `main`) y conteo de
   tests medido con `pytest --collect-only -q`— en el mismo PR que cambia el estado.
   `tests/test_estado_al_dia.py` falla si quedan **más de 3 PRs** fusionados sin hacerlo
   (DG-6). La regla vieja —"más de ~5 días sin tocarse"— era un recordatorio y falló dos
   veces; esta corre en cada PR.
