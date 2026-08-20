# SPEL — Instrucciones de proyecto

Sistema de trading cuantitativo. Penaliza error del modelo con entropía
geopolítica (GDELT). Fase 2 (LSTM/modelo) es el bloqueador actual para
capital real — ver ESTADO.md para el estado exacto de cada fase.

## Reglas duras, sin excepción
- Nunca importar desde archive/* (legacy archivado, 5 ramas)
- Nunca Termux, Streamlit, yfinance, IQ Option, MetaTrader
- Nunca rutas hardcodeadas de Colab fuera de governance/persistence.py
- Ningún número o afirmación sin verificar contra el código real
- "Port, don't rewrite": si algo existe en legacy, auditar el archivo
  exacto antes de escribir código nuevo equivalente
- Si algo no está claro o contradice estos documentos: parar y
  preguntar, no adivinar

## No tocar sin instrucción explícita
execution/circuit_breaker.py, execution/execution_guard.py, cualquier
cosa de ejecución de órdenes o brokers — congelado hasta Fase 4.
Valores de governance/secrets.py — nunca en logs, prints, ni commits.

## Antes de dar cualquier tarea por terminada
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -q
Todo verde (339+ tests) antes de abrir PR. Nunca push directo a main
— siempre rama + PR.

## Commits
Un cambio lógico por commit. El mensaje explica el motivo real, no
solo el qué (ver git log de este repo para el estilo esperado).
