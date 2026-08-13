# execution/

Ejecución REAL de órdenes — Deriv y Alpaca. Esto es trabajo nuevo genuino:
en todo el proyecto anterior, cero archivos ejecutaban una orden real contra
un broker. La superficie FX estaba diseñada para ejecución manual (está en
el propio axiom_master.xml, Superficie B: "ejecución manual en IQ Option").

No se construye esto hasta que `core/` tenga un modelo que de verdad
aprenda algo (ver nota en core/README.md) y `ingestion/` tenga Deriv
wireado end-to-end. Orden de dependencia real, no burocracia.
