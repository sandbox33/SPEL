# SPEL — Notas de arquitectura de modelo (Fase 2)

> Esto es investigación y glosario, NO un plan decidido. Nada acá compromete a
> nada — es exactamente lo que Altair pidió el 18 ago: un documento donde quedan
> las dudas, los términos, y las opciones, para que cualquier sesión futura (yo
> mismo, leyendo esto desde Drive) pueda retomarlo sin que se pierda lo aprendido.
> "Modo Investigador" aplica acá tanto como en el código: esto presenta opciones
> con su costo/beneficio real, Altair decide cuál (o ninguna) cuando llegue el
> momento de construir.

---

## Primero, lo que ya existe y está BLOQUEADO — hay que partir de acá

El legacy tiene una arquitectura LSTM con un guardián real que la protege
(`spel_meta_guardian.py::enforce_lstm_architecture()`), no solo una convención:

```
input_size  = 20   (20 features del parquet canónico v4)
hidden_size = 64   (calibrado en el test de COVID)
num_layers  = 1
```

Ese guardián lanza `RuntimeError` si cualquiera de esos 3 números cambia — porque
existen **14 checkpoints `.pt` ya entrenados** atados exactamente a esa forma. Un
`hidden_size=128` "por accidente" no da un error claro: carga pesos incompatibles
en silencio y produce `val_dir`/`entropy_shannon` basura sin traceback. Por eso el
guardián existe.

**Lo que esto significa para las opciones de abajo:** explorar Random Forest,
XGBoost, o cualquier otra familia de modelo NO es una extensión de esos 14
checkpoints — es una vía paralela. Los checkpoints existentes solo sirven si se
sigue entrenando LSTM. Cambiar de familia de modelo es empezar de cero en cuanto a
pesos entrenados (aunque no en cuanto a features — `ingestion/training_dataset.py`
sirve para cualquiera de las dos vías).

---

## Glosario — en tus términos, con SPEL como ejemplo donde aplica

### Árbol de Decisión (la pieza base de todo lo demás)

Un modelo que hace preguntas de sí/no en cascada sobre los datos ("¿entropy_shannon
> 0.7? → si sí, ¿n_events > 50? → ...") hasta llegar a una predicción. Solo, un
árbol se sobreajusta fácil (memoriza el ruido de sus datos de entrenamiento en vez
de aprender el patrón real). Por eso casi nunca se usa un árbol solo — se combinan
muchos, y ahí es donde entran los términos que preguntaste.

### Bagging → Random Forest

**Bagging** (Bootstrap Aggregating): entrenar muchos árboles, cada uno sobre una
muestra distinta (con repetición) de los mismos datos, y promediar sus respuestas.
La idea: cada árbol individual se equivoca distinto, el promedio cancela buena
parte del error.

**Random Forest** es Bagging + una vuelta extra: cada árbol, además de ver una
muestra distinta de FILAS, en cada pregunta solo puede elegir entre un subconjunto
al azar de COLUMNAS (features). Esto fuerza a que los árboles del bosque sean más
distintos entre sí — si una sola columna fuera muy predictiva, todos los árboles la
usarían primero y terminarían pareciéndose demasiado.

### Boosting → AdaBoost → Gradient Boosting → XGBoost / LightGBM

Familia distinta, no una mejora de Random Forest — la filosofía es opuesta:

- **AdaBoost** (el más viejo): entrena árboles chicos en secuencia. Cada árbol
  nuevo presta más atención a los casos que el árbol anterior falló (les sube el
  peso). El resultado final es una combinación ponderada de todos.
- **Gradient Boosting**: generaliza la idea de AdaBoost — cada árbol nuevo no
  predice el target directo, predice el ERROR (residuo) que dejó el conjunto de
  árboles anterior. Es como corregir una y otra vez el mismo dibujo.
- **XGBoost / LightGBM**: son implementaciones optimizadas de Gradient Boosting,
  no algoritmos distintos entre sí en el fondo. XGBoost fue el primero en
  popularizarse (por velocidad y por manejar datos faltantes solo); LightGBM es
  más rápido todavía en datasets grandes porque construye los árboles distinto
  (por hoja, no por nivel). Para el tamaño de datos que SPEL maneja (días, no
  millones de filas), la diferencia de velocidad entre los dos no importa mucho —
  la elección entre uno u otro es más gusto/documentación disponible que técnica.

### Random Forest vs. XGBoost — la comparación que trajiste, verificada y ordenada

| | Random Forest | XGBoost |
|---|---|---|
| Construye árboles | En paralelo, independientes | En secuencia, cada uno corrige al anterior |
| Qué reduce mejor | Varianza (menos overfitting) | Sesgo (aprende patrones más finos) |
| Sensibilidad a hiperparámetros | Baja — funciona bien "de fábrica" | Alta — necesita ajuste fino o sobreajusta |
| Datos faltantes | Hay que imputarlos antes | Los maneja solo, internamente |
| Cuándo conviene | Modelo base rápido, poco tiempo para calibrar | Cuando hay tiempo para ajustar y se busca el máximo de precisión |

Para SPEL específicamente: dado que `ingestion/training_dataset.py` produce una
fila por día con un puñado de features ya resumidas (no series crudas de miles de
puntos), el volumen de datos de entrenamiento va a ser modesto (cientos a pocos
miles de filas, no millones) — en ese régimen, Random Forest suele ser más difícil
de tirar al agua que XGBoost, que necesita más cuidado para no sobreajustar con
poco dato. Esto no es una recomendación cerrada, es la razón técnica de por qué
"empezar por RF" suele ser el camino más corto cuando el dataset es chico.

### Overfitting (sobreajuste)

El modelo memoriza el ruido específico de los datos de entrenamiento en vez de
aprender el patrón general — rinde excelente en los datos que ya vio, mal en datos
nuevos. Es el enemigo #1 de cualquier modelo con muchos parámetros. La disciplina
de `spel_trainer_audit.py` (BUG-LA-01, ver `ESTADO.md`) existe exactamente para
cazar una de las formas más traicioneras de esto: cuando el propio proceso de
preparar los datos ya se "asomó" al futuro sin querer.

### Hiperparámetros

Los "diales" del modelo que TÚ eliges antes de entrenar (no los aprende el modelo
solo) — cuántos árboles, qué tan profundos, qué tasa de aprendizaje. Elegirlos mal
es la causa más común de overfitting o de un modelo que no aprende nada.

**Random Forest**, los que más importan:
- `n_estimators`: cuántos árboles (100-500 típico). Más árboles = más estable,
  más memoria.
- `max_depth`: qué tan profundo puede crecer cada árbol. Sin límite, se sobreajusta.
- `min_samples_split`: mínimo de datos para que un nodo se pueda dividir en dos.
- `max_features`: cuántas columnas puede considerar cada árbol en cada pregunta
  (`sqrt` o `log2` del total son los valores típicos).

**XGBoost**, los que más importan:
- `n_estimators`: cantidad de árboles secuenciales (100-1000 típico).
- `learning_rate` (`eta`): qué tan grande es el paso de corrección en cada árbol
  nuevo (0.01-0.3). Más chico = más lento pero más robusto, necesita más árboles.
- `max_depth`: acá los árboles suelen ser MUCHO más chicos que en Random Forest
  (3-10) — son "aprendices débiles" a propósito, la fuerza está en la secuencia,
  no en cada árbol individual.
- `subsample`: fracción de filas usada por árbol (0.6-0.9) — anti-overfitting.
- `colsample_bytree`: fracción de columnas usada por árbol (0.6-0.9).

### Importancia de variables (feature importance)

Después de entrenar, tanto Random Forest como XGBoost pueden decirte qué features
pesaron más en las predicciones. Para SPEL, esto sería directamente útil para
validar (o refutar) el diseño actual: si `entropy_shannon` sale como la variable
más importante, valida la tesis central del proyecto (SPEL = Socio-Political
Entropy Loss). Si sale con importancia baja y `backbone_score` domina, es una señal
real de que el edge está viniendo del precio, no de la geopolítica — información
valiosa cualquiera sea el resultado.

### Validación cruzada (cross-validation)

Partir los datos en varios pliegues, entrenar en unos y validar en otro,
rotando — para tener una medida de rendimiento más confiable que un solo split.
**Advertencia específica para SPEL**: la validación cruzada estándar (k-fold
aleatorio) mezcla el orden temporal — exactamente el tipo de fuga que
`spel_trainer_audit.py` cazaba (BUG-LA-01). Para series de tiempo como esta, hace
falta **validación cruzada temporal** (`TimeSeriesSplit` en scikit-learn, no
`KFold` normal) — entrena siempre con el pasado, valida siempre con el futuro
inmediato, nunca al revés.

### Redes neuronales (el LSTM ya es esto)

Familia completamente distinta a los árboles — en vez de preguntas de sí/no,
combina las entradas con pesos numéricos ajustables, capa por capa, y aprende esos
pesos por descenso de gradiente. El LSTM (Long Short-Term Memory) es un tipo
especializado en SECUENCIAS — recuerda información de pasos anteriores en el
tiempo, lo cual tiene sentido si el modelo mira una ventana de días consecutivos.
Los árboles (RF/XGBoost) no tienen esa noción de secuencia — ven cada fila como un
punto independiente, con sus features ya resumidas (que es exactamente lo que
`ingestion/training_dataset.py` produce).

### Herramientas

- **Scikit-learn**: la librería estándar de Python para Random Forest, árboles,
  validación cruzada, y decenas de utilidades — es probablemente el punto de
  entrada más simple para probar RF sin escribir nada desde cero.
- **pandas / numpy**: ya en uso en todo el repo nuevo (`ingestion/adapters.py`,
  `core/price_signals.py`, etc.) — no hay nada nuevo que instalar para estos dos.
- **H2O.ai**: plataforma de AutoML que prueba muchos modelos (incluye Random
  Forest, Gradient Boosting, redes) automáticamente y compara — más pensada para
  no tener que elegir hiperparámetros a mano. Trae su propio runtime (Java por
  debajo), más pesado de instalar que scikit-learn — a evaluar si vale la pena
  contra el requisito de "todo gratis y liviano" ya establecido para el resto del
  proyecto.
- **R (paquete `randomForest`)**: la implementación original de Random Forest
  (Breiman & Cutler) — el proyecto entero es Python, así que usar R implicaría un
  segundo lenguaje en el stack. Mencionado acá para que quede registrado, sin
  recomendarlo dado el resto de la arquitectura.

### Isolation Forest — esto SÍ ya estaba en el legacy

No lo preguntaste directo, pero es de la misma familia (bosques de árboles) y
YA fue parte del diseño: `PARAM_ISO` (`isolation_forest_threshold`, 0.6) se usaba
para detectar anomalías en la entropía GDELT entrante (`RB_02` del legacy). Es
distinto a Random Forest/XGBoost en el objetivo — no predice una dirección de
precio, aísla puntos raros (outliers) más rápido que los normales. Nunca se portó
al repo nuevo. Si se retoma Random Forest para el gold_score, Isolation Forest para
detectar GDELT anómalo antes de que entre al pipeline es una extensión natural y
barata (misma librería, `sklearn.ensemble.IsolationForest`).

---

## Cuándo esto se vuelve una decisión real (no antes)

Este documento no elige nada. Cuando Fase 2 arranque de verdad, las preguntas
concretas a resolver, en orden, van a ser:

1. ¿Seguir con LSTM (reentrenar sobre datos limpios, mismos 14 checkpoints como
   punto de partida posible) o abrir una vía nueva con árboles?
2. Si es árboles: ¿Random Forest primero (más simple, menos ajuste) o directo a
   XGBoost (más trabajo, potencialmente mejor techo)?
3. ¿Los dos en paralelo, comparados con el mismo dataset de
   `ingestion/training_dataset.py`, y quedarse con el que gane en validación
   temporal? Esto es técnicamente simple de montar una vez que exista un split
   train/val por fecha — no hace falta elegir a ciegas.

No se construye nada de esto ahora. Este documento existe para que, cuando llegue
el momento, no haya que volver a investigar los mismos términos desde cero.
