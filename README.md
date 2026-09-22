# Rama `data` — series diarias de SPEL

Rama **huérfana**: no comparte historia con `main` y **no se fusiona nunca**.
Contiene datos, no código. El código que la escribe y la lee vive en `main`.

Existe desde el 21-sep-2026 por la enmienda a la Decisión #14
(`decision-log.md` en `main`): las series diarias append-only —la serie GDELT
por activo y, cuando existan, las anotaciones de régimen— pasan de Drive a
esta rama.

## Qué hay acá

| ruta | qué es |
|---|---|
| `metrics/gdelt_series/{ACTIVO}.jsonl` | una línea JSON por día y activo; la escribe `ingestion/gdelt_series.py::append_day()` |
| `metrics/regimen/` | reservado para las anotaciones de régimen; hoy vacío |
| `metrics/ingesta_automatica.json` | `{"desde": ...}`: el primer día que escribió CI. Nace en `null`, lo fija la primera escritura y no se mueve más |
| `.gitattributes` | `*.jsonl -text`: git no toca los finales de línea de las series |

La estructura es la del stream METRICS de `governance/persistence.py`: la
raíz de esta rama hace de `drive_root()`. Por eso el código la lee y la
escribe sin cambios, solo con `SPEL_DRIVE_ROOT` apuntando acá.

## Quién escribe

**Solo `.github/workflows/gdelt.yml`**, como `github-actions[bot]`, una vez
por día (06:30 UTC). Un commit por corrida, y solo si hubo cambios.

- **Ningún notebook corre `run_gdelt --write`.** Ni contra esta rama ni
  contra Drive. Con dos escritores quedan dos series con días distintos, y
  la deduplicación por día no reconcilia eso.
- La carpeta de Drive `metrics/gdelt_series` quedó renombrada a
  `metrics/gdelt_series_OBSOLETO_2026-09-21`. No se lee ni se escribe.
- **La única escritura a mano permitida es la siembra**: subir `BTC.jsonl` y
  `XAU.jsonl` (la historia 2013-04-01 .. 2026-09-03) por la web de GitHub a
  `metrics/gdelt_series/`, una vez. Después se dispara `gdelt.yml` con
  `modo: verificar_siembra`, que compara contra las cifras medidas (BTC 4.880
  filas, XAU 4.879) y sale rojo si no coinciden.

Los pushes a esta rama no disparan ningún workflow.

## Cómo leer la serie desde Colab

```python
!git clone --depth 1 --branch data --single-branch https://github.com/sandbox33/SPEL.git <dir>
import os
os.environ["SPEL_DRIVE_ROOT"] = "<dir>"

from ingestion.gdelt_series import read_series
btc = read_series("BTC")
```

El repo es público (verificado el 22-sep-2026): el clon no necesita
credencial, y esta rama se lee igual que `main`.

**Cuidado:** `SPEL_DRIVE_ROOT` no mueve solo la serie. Mueve **los tres
streams de Drive** —METRICS, MODELS y TRADE_LEDGER—, porque todos se
resuelven contra `drive_root()`. Un notebook que lee la serie así y después
guarda un checkpoint lo escribiría en `<dir>`, que desaparece con la sesión
de Colab. Hay que leer la serie primero y sacar la variable
(`del os.environ["SPEL_DRIVE_ROOT"]`) antes de escribir cualquier otra cosa.

## Cómo deshacer una corrida

Con `git revert` sobre esta rama, nunca reescribiendo la historia:

```
git checkout data
git revert <sha-del-commit-del-bot>
git push origin data
```

`read_series()` deduplica por día quedándose con la **última** línea, así
que un día mal escrito también se puede corregir volviendo a bajarlo con un
dispatch de `gdelt.yml` con `since`. El revert deja el historial intacto y
es la vía cuando lo que hay que sacar es la corrida entera.
