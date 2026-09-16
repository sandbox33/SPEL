"""
research/
==========
Código RETIRADO del camino caliente, no borrado.

Acá vive lo que el motor diario ya no ejecuta pero que sigue siendo válido
como hipótesis: depende de algo que todavía no existe (un LSTM entrenado),
o su tesis se midió y no sobrevivió. En los dos casos el código está
completo, con sus tests, y corre.

  · `gold_score_chain.py` -- la cadena gold_score -> godel_score ->
    val_dir -> LSTM inexistente. Movida el 16-sep-2026.
  · `price_signals.py` -- transfer entropy y backbone. Tesis direccional
    refutada el 4-sep-2026; ver el acta en decision-log.md.

POR QUÉ UN PAQUETE Y NO UNA RAMA DE ARCHIVO. Los retiros anteriores de este
repo (PR #22) se fueron a `archive/*`, donde el código deja de importarse,
deja de correr y deja de compilar contra el resto. Eso está bien para algo
que no va a volver. Esto es distinto: la condición de reversión está
escrita y es concreta (Fase 2 entrena el LSTM), así que el código tiene que
seguir siendo IMPORTABLE y CORRIBLE -- si no, el día que se retome habrá
que redescubrir si todavía funciona.

QUÉ NO ES: no es `core/`. Nada de `orchestration/` lo importa, y el test
`research/tests/test_aislamiento.py` lo verifica -- un import desde el
motor hacia acá sería reintroducir por la puerta de atrás lo que este
paquete existe para sacar.

CI: `.github/workflows/tests.yml` corre `pytest tests/`, así que estos
tests NO bloquean un merge. Hay además un job propio, `research`, con
`continue-on-error: true`: visible si se rompe, sin poder frenar el motor.
A mano: `pytest research/tests/ -q`.
"""
