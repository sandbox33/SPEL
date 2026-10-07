"""
integracion_demo/
=================
El único lugar del repo desde el que se pueden mandar órdenes, y solo a la
cuenta demo de Deriv (CLAUDE.md, «Brókers y órdenes»; brief del Admin del
06-oct-2026 (3)). Sin estrategias, señales ni indicadores: esto ejecuta y
registra lo que se le pida, y nada más.

  otp.py         OTP solo para una cuenta demo según GET /accounts, y URL
                 solo de /ws/demo. Portado de las sondas §0.A-2 y §0.A-3.
  conexion.py    Una conexión persistente a /ws/demo, con reconexión con
                 backoff y un OTP nuevo por conexión.
  ejecucion.py   Lista blanca propia de mensajes; `buy` exige stop-loss y
                 solo MULTUP/MULTDOWN. Toda respuesta cruda se guarda antes
                 de parsearla.
  registro.py    Una línea JSONL por evento de contrato, append-only, con
                 cadena de hashes. Porta el patrón de core/trade_ledger.py;
                 no lo importa.
  reconciliar.py Cruza el registro con profit_table y statement y agrega una
                 línea de reconciliación; nunca edita una fila.

No importa nada de execution/, core/trade_ledger.py ni
core/execution_costs.py (tests/test_guarda_integracion_demo.py).
"""
