# infra/

`workflows/` — GitHub Actions. Medido en la sesión anterior: el cómputo del
entrenamiento LSTM es trivial (0.27 min para 4 activos × 50 épocas) — el
presupuesto gratis (2,000 min/mes, repo privado, sin GPU) alcanza de sobra
para el modelo. Si algo excede el margen, `colab_templates/` tiene la
plantilla de respaldo — no antes de medirlo de nuevo con datos reales.

`colab_templates/` — notebook de entrenamiento, para cuando GH Actions no
alcance.
