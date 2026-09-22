"""
tools/verificar_siembra.py
============================
Verifica que la siembra de la rama `data` sea la serie que se midió, antes
de que CI empiece a escribir encima.

La siembra es la única escritura a mano que admite la rama `data`: Altair
sube BTC.jsonl y XAU.jsonl por la web de GitHub, a veces desde un móvil. Es
un paso humano entre dos sistemas, y nada en la cadena lo verifica -- si un
archivo llega truncado, con una línea pegada o con el activo equivocado, CI
escribe encima sin enterarse y el defecto queda enterrado bajo días nuevos.

══ LAS CIFRAS CONTRA LAS QUE SE COMPARA ══

Las de la medición del 19-sep-2026 (ver `tools/calibrar_umbral_entropia.py`
y `config/README.md`), que es la última vez que alguien contó la serie de
Drive:

    BTC  4.880 filas, 4.880 válidos, 2013-04-01 .. 2026-09-03
    XAU  4.879 filas, 4.879 válidos, 2013-04-01 .. 2026-09-03

4.904 días de calendario en ese rango: 24 huecos en BTC, 25 en XAU. Son
deuda conocida y se listan, no se curan.

LAS CIFRAS SE CUENTAN DENTRO DEL RANGO SEMBRADO, no sobre el archivo entero.
El orden previsto es sembrar, verificar, ingerir; pero si alguien vuelve a
correr esto después de que CI escribió, contar el archivo entero daría rojo
por días que CI agregó legítimamente. El total del archivo se imprime
aparte, como dato.

EL SHA256 ES DEL ARCHIVO COMPLETO, byte por byte, y se imprime sin
comparar: no hay un hash de referencia medido del archivo de Drive, y
escribir uno de memoria sería inventar evidencia. Sirve para comparar a mano
contra `sha256sum` del archivo original, y deja de coincidir en cuanto CI
agrega el primer día -- lo cual es esperado, no una falla.

══ ROJO Y AVISO ══

ROJO (exit 1): el archivo no existe, o las filas, los válidos, el primer o
el último día no coinciden con las cifras, o hay líneas corruptas. Cualquiera
de esas es una siembra que no es la serie medida.

AVISO (exit 0): el archivo no termina en salto de línea. Antes de esta rama
eso habría costado dos días en la primera escritura de CI; desde el Brief D
`append_day()` lo agrega solo (ver ingestion/gdelt_series.py), así que se
informa y no se bloquea.

Read-only: no escribe nada, en ningún modo.

Uso:
    SPEL_DRIVE_ROOT=./data-store python tools/verificar_siembra.py
    python tools/verificar_siembra.py --assets BTC
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from governance.persistence import PersistenceStream, stream_path  # noqa: E402
from ingestion.frescura import inventariar_huecos  # noqa: E402
# `_series_file_path` es privada, y se usa igual: este tool necesita los
# BYTES del archivo (sha256, salto final, líneas físicas), que la API pública
# no expone. Rearmar la ruta acá sería una segunda definición de dónde vive
# la serie, que es justo lo que no puede divergir.
from ingestion.gdelt_series import _series_file_path, read_series  # noqa: E402


@dataclass(frozen=True)
class CifrasSiembra:
    filas: int
    validos: int
    primer_dia: str
    ultimo_dia: str


#: Medición del 19-sep-2026 sobre la serie de Drive. Ver el docstring.
CIFRAS_SIEMBRA: dict[str, CifrasSiembra] = {
    "BTC": CifrasSiembra(filas=4880, validos=4880,
                         primer_dia="2013-04-01", ultimo_dia="2026-09-03"),
    "XAU": CifrasSiembra(filas=4879, validos=4879,
                         primer_dia="2013-04-01", ultimo_dia="2026-09-03"),
}


class _ContadorDeCorruptas(logging.Handler):
    """Cuenta los warnings de línea corrupta que ya emite `read_series()`.
    Se escucha a la función de verdad en vez de volver a parsear: un segundo
    criterio de "línea corrupta" podría no coincidir con el que usa la
    lectura que alimenta al motor."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.n = 0

    def emit(self, record: logging.LogRecord) -> None:
        if "corrupta" in record.getMessage():
            self.n += 1


@dataclass
class Verificacion:
    asset: str
    existe: bool
    sha256: Optional[str] = None
    lineas_fisicas: int = 0
    lineas_corruptas: int = 0
    termina_en_salto: bool = True
    filas_totales: int = 0
    ultimo_dia_total: Optional[str] = None
    # Dentro del rango sembrado:
    filas: int = 0
    validos: int = 0
    primer_dia: Optional[str] = None
    ultimo_dia: Optional[str] = None
    huecos: list[str] = field(default_factory=list)
    discrepancias: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def rojo(self) -> bool:
        return bool(self.discrepancias)


def verificar_activo(asset: str, esperado: CifrasSiembra) -> Verificacion:
    ruta = _series_file_path(asset)
    v = Verificacion(asset=asset, existe=ruta.exists())
    if not v.existe:
        v.discrepancias.append(f"no hay archivo en {ruta}")
        return v

    datos = ruta.read_bytes()
    v.sha256 = hashlib.sha256(datos).hexdigest()
    v.lineas_fisicas = sum(1 for l in datos.splitlines() if l.strip())
    v.termina_en_salto = datos.endswith(b"\n")
    if not v.termina_en_salto:
        v.avisos.append("el archivo no termina en salto de línea; append_day() "
                        "lo agrega en la primera escritura, no se pierde nada")

    contador = _ContadorDeCorruptas()
    log = logging.getLogger("spel.ingestion.gdelt_series")
    log.addHandler(contador)
    try:
        serie = read_series(asset)
    finally:
        log.removeHandler(contador)
    v.lineas_corruptas = contador.n
    v.filas_totales = len(serie)
    v.ultimo_dia_total = str(serie[-1].day) if serie else None

    desde = date.fromisoformat(esperado.primer_dia)
    hasta = date.fromisoformat(esperado.ultimo_dia)
    rango = [f for f in serie if desde <= f.day <= hasta]
    v.filas = len(rango)
    v.validos = sum(1 for f in rango if f.entropy_shannon is not None)
    v.primer_dia = str(serie[0].day) if serie else None
    v.ultimo_dia = str(rango[-1].day) if rango else None
    v.huecos = [str(d) for d in inventariar_huecos(asset, hasta=hasta)]

    for campo, real, pedido in (
        ("filas", v.filas, esperado.filas),
        ("válidos", v.validos, esperado.validos),
        ("primer día", v.primer_dia, esperado.primer_dia),
        ("último día del rango", v.ultimo_dia, esperado.ultimo_dia),
    ):
        if real != pedido:
            v.discrepancias.append(f"{campo}: {real}, se esperaba {pedido}")
    if v.lineas_corruptas:
        v.discrepancias.append(f"{v.lineas_corruptas} línea(s) corrupta(s)")
    return v


def render_text(verificaciones: Sequence[Verificacion]) -> str:
    out = ["═══ VERIFICACIÓN DE LA SIEMBRA ═══",
           f"serie leída de: {stream_path(PersistenceStream.METRICS)}", ""]
    for v in verificaciones:
        esperado = CIFRAS_SIEMBRA[v.asset]
        out.append(f"── {v.asset}: {'ROJO' if v.rojo else 'VERDE'}")
        if not v.existe:
            out.append(f"  {v.discrepancias[0]}")
            out.append("")
            continue
        out.append(f"  sha256 del archivo: {v.sha256}")
        out.append(f"  líneas físicas: {v.lineas_fisicas}   corruptas: "
                   f"{v.lineas_corruptas}   salto final: "
                   f"{'sí' if v.termina_en_salto else 'NO'}")
        out.append(f"  en el rango sembrado ({esperado.primer_dia} .. "
                   f"{esperado.ultimo_dia}): {v.filas} filas (esperadas "
                   f"{esperado.filas}), {v.validos} válidos (esperados "
                   f"{esperado.validos}), {v.primer_dia} .. {v.ultimo_dia}")
        out.append(f"  archivo completo: {v.filas_totales} filas, último día "
                   f"{v.ultimo_dia_total}")
        out.append(f"  huecos de calendario en el rango sembrado "
                   f"({len(v.huecos)}, deuda conocida, no se curan):")
        for k in range(0, len(v.huecos), 6):
            out.append("    " + "  ".join(v.huecos[k:k + 6]))
        for d in v.discrepancias:
            out.append(f"  ✗ {d}")
        for a in v.avisos:
            out.append(f"  · aviso: {a}")
        out.append("")
    rojos = [v.asset for v in verificaciones if v.rojo]
    out.append(f"RESULTADO: ROJO en {', '.join(rojos)} — la siembra no es la "
               f"serie medida. No correr la ingesta hasta resolverlo."
               if rojos else
               "RESULTADO: VERDE — la siembra coincide con la serie medida.")
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="verificar_siembra",
        description="Compara la siembra de la rama `data` contra la serie "
                    "medida. Read-only.")
    p.add_argument("--assets", nargs="+", default=sorted(CIFRAS_SIEMBRA),
                   help=f"Default: {', '.join(sorted(CIFRAS_SIEMBRA))}, los "
                        f"únicos con siembra.")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    desconocidos = [a for a in args.assets if a not in CIFRAS_SIEMBRA]
    if desconocidos:
        print(f"ERROR: sin cifras de siembra para {', '.join(desconocidos)}. "
              f"Solo {', '.join(sorted(CIFRAS_SIEMBRA))} tienen siembra.",
              file=sys.stderr)
        return 2
    verificaciones = [verificar_activo(a, CIFRAS_SIEMBRA[a]) for a in args.assets]
    print(render_text(verificaciones))
    return 1 if any(v.rojo for v in verificaciones) else 0


if __name__ == "__main__":
    raise SystemExit(main())
