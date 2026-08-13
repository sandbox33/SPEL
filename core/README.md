# core/

La matemática que el proyecto anterior sí validó: Gödel-masking, Bayesian
Model Averaging (pesos R13: 0.40/0.30/0.30 nativos, 0.55/0.45/0.0
sintéticos), Shannon entropy + KL divergence como kill-switch, dual
accounting ($10 real / $100k canónico para que las métricas de gate sean
estadísticamente válidas con capital chico).

Esto se PORTA, no se reinventa — es la parte del proyecto anterior que
funcionaba. Se reescribe limpio (sin las 5 copias de sha12, sin los 2
spel_commons.py) pero la lógica matemática no cambia.

Nota honesta: el último entrenamiento real que vi (gráficos de la sesión
pasada) tenía precisión de validación oscilando en ~0.50 — sin señal de
aprendizaje real. Antes de portar el trainer sin revisar, diagnosticar esto.
Ver BLUEPRINT.md §4.
