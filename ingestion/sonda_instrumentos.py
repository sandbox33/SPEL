"""
ingestion/sonda_instrumentos.py
=================================
Sonda de instrumentos de Deriv, una vez por día durante siete días (Brief
H1-A, Entregable 1). Mide lo que la API dice de cada instrumento de BTC y
del oro: qué contratos admite, qué multiplicadores, qué stake mínimo y
máximo, y cuánto cobra de comisión una `proposal` real.

NINGÚN SÍMBOLO NI PARÁMETRO DE MERCADO SE ESCRIBE A MANO. Los símbolos salen
de `active_symbols`; el stake, los multiplicadores y la comisión, de
`contracts_for` y `proposal`. Las únicas constantes con nombre de símbolo son
el CONTROL POSITIVO (`frxEURUSD`, que tiene que aparecer para que la corrida
valga) y el oro PRE-REGISTRADO (`frxXAUUSD`, que viene de
research/preregistro_h1.md y la sonda verifica contra la API, no supone).

══ QUÉ SE HACE, EN ORDEN ══

  1. `time`: la fecha de la corrida es la del SERVIDOR, nunca la del runner.
  2. `active_symbols`: se acepta el esquema documentado (`symbol`) y el que
     trae la API nueva (`underlying_symbol`), y se registra cuál respondió.
     Sin `frxEURUSD` en la lista, la corrida es INVALIDO: una lista que no
     trae el par más líquido de Deriv es una respuesta filtrada o rota, y
     nada de lo que diga sobre BTC es confiable.
  3. Candidatos: BTC = mercado cripto EXACTO y "BTC" en el símbolo; oro =
     mercado de materias primas EXACTO y "XAU" en el símbolo. Un sintético
     no entra aunque diga BTC: ni está en esos mercados, ni pasa la red
     explícita que descarta cualquier mercado llamado sintético.
  4. Por candidato, `contracts_for` (tipos de contrato, MULTUP/MULTDOWN,
     rango de multiplicadores, duración) y DOS `proposal` de MULTUP con el
     multiplicador más bajo:
       - la primera, al `default_stake` que publica `contracts_for`, solo
         para leer `validation_params.stake.min/max`. El esquema oficial
         documenta `contracts_for.min_stake/max_stake` "[Only for turbos
         options]": para multiplicadores el stake mínimo NO sale de ahí.
       - la segunda, al stake mínimo: la comisión real.
  5. Si califica más de un BTC (cripto, no sintético, con MULTUP), la sonda
     NO elige: registra todo y termina con una PETICIÓN AL ADMIN.

══ LA MONEDA ══

`proposal` exige `currency`. Sale de `authorize.currency` -- la de la cuenta
demo --, no se escribe a mano. Sin token no hay cuenta: se miden los
contratos igual, y la comisión queda SIN_MEDIR con el motivo.

══ LA UNIDAD DE LA COMISIÓN ══

El esquema oficial describe `proposal.commission` como "Commission changed
in percentage (%)". Se guarda el valor tal cual, con esa descripción, y NO
se convierte: si en la primera medición resulta ser un monto y no un
porcentaje, convertir acá habría escrito siete días de números mal
interpretados. H1-B decide la unidad mirando la respuesta cruda, que va
guardada entera.

══ SALIDA ══

`metrics/instrumentos/<símbolo>_<fecha>.json`, uno por símbolo y día, con
las respuestas crudas de `contracts_for` y de las dos `proposal`, y el
sha256 de cada una (el de `active_symbols` también, aunque de esa respuesta
solo se guarda el ítem del símbolo). Más `resumen_<fecha>.json` con el
estado de la corrida.

Si ya hay un resumen de la fecha del servidor, no se escribe nada. Con siete
resúmenes, la sonda está COMPLETA y no abre conexión.

Uso:
    python ingestion/sonda_instrumentos.py --entorno demo
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from governance.persistence import PersistenceStream, stream_path  # noqa: E402
from governance.secrets import SecretKey, load_secret  # noqa: E402
from ingestion.deriv_ws import Entorno, SesionDeriv, abrir_sesion  # noqa: E402

SCHEMA_VERSION = "1.0.0"

#: Tiene que estar en `active_symbols` para que la corrida valga. Es el par
#: más líquido de Deriv y el primero del mapa verificado de DerivAdapter.
CONTROL_POSITIVO = "frxEURUSD"

#: El oro de la réplica, tal como lo fija research/preregistro_h1.md. La
#: sonda verifica que exista en la API; no lo supone.
ORO_PREREGISTRADO = "frxXAUUSD"

#: El valor de `active_symbols[].market` para cripto. Si Deriv lo nombrara
#: distinto, la sonda no encuentra ningún BTC y lo dice (SIN_BTC, con la
#: lista de mercados que sí vio): falla en voz alta, no elige otra cosa.
MERCADO_CRIPTO = "cryptocurrency"

#: Una medición por día durante esta cantidad de días (Brief H1-A).
DIAS_DE_SONDA = 7

#: El valor de `active_symbols[].market` para el oro. Mismo criterio que
#: MERCADO_CRIPTO: el filtro es por mercado EXACTO, así que un índice cesta
#: o derivado con XAU en el código (Deriv los tiene) no entra aunque su
#: mercado tenga un nombre que este módulo no conoce.
MERCADO_METALES = "commodities"


class Estado:
    OK = "OK"
    PETICION_ADMIN = "PETICION_ADMIN"
    SIN_BTC = "SIN_BTC"
    INVALIDO = "INVALIDO"
    YA_MEDIDO_HOY = "YA_MEDIDO_HOY"
    COMPLETA = "COMPLETA"


# ══════════════════════════════════════════════════════════════════════════
#  active_symbols
# ══════════════════════════════════════════════════════════════════════════

def codigo(item: dict) -> tuple[str, str]:
    """(código, esquema). Acepta `symbol` (el documentado) y
    `underlying_symbol` (el de la API nueva)."""
    for esquema in ("symbol", "underlying_symbol"):
        if item.get(esquema):
            return item[esquema], esquema
    raise ValueError(f"ítem de active_symbols sin symbol ni underlying_symbol: "
                     f"claves {sorted(item)}")


def es_sintetico(item: dict) -> bool:
    """Red explícita, además del filtro por mercado exacto de `seleccionar`:
    cualquier mercado que se llame sintético se descarta y se reporta."""
    return "synthetic" in str(item.get("market", "")).lower()


@dataclass
class Seleccion:
    esquemas: list[str]
    control_presente: bool
    btc: list[dict]
    oro: list[dict]
    sinteticos_descartados: list[str]
    mercados_vistos: list[str]


def seleccionar(items: Sequence[dict]) -> Seleccion:
    esquemas, btc, oro, descartados, mercados = set(), [], [], [], set()
    codigos = set()
    for it in items:
        cod, esq = codigo(it)
        esquemas.add(esq)
        codigos.add(cod)
        mercados.add(str(it.get("market", "")))
        quiere_btc = "BTC" in cod.upper()
        quiere_oro = "XAU" in cod.upper()
        if not (quiere_btc or quiere_oro):
            continue
        if es_sintetico(it):
            descartados.append(cod)
            continue
        if quiere_btc and it.get("market") == MERCADO_CRIPTO:
            btc.append(it)
        elif quiere_oro and it.get("market") == MERCADO_METALES:
            oro.append(it)
    sel = Seleccion(sorted(esquemas), CONTROL_POSITIVO in codigos,
                    sorted(btc, key=lambda i: codigo(i)[0]),
                    sorted(oro, key=lambda i: codigo(i)[0]),
                    sorted(descartados), sorted(mercados))
    # Red de seguridad: si la lógica de arriba cambia, un sintético no puede
    # llegar a medirse sin que esto lo corte.
    for it in sel.btc + sel.oro:
        if es_sintetico(it):
            raise AssertionError(f"sintético seleccionado: {codigo(it)[0]}")
    return sel


# ══════════════════════════════════════════════════════════════════════════
#  contracts_for y proposal
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class Contratos:
    tipos: list[str]
    multup: bool
    multdown: bool
    multiplicadores: list[float]
    default_stake: Optional[float]
    duracion: list[dict]
    cancelacion: list[Any]


def resumir_contratos(cf: dict) -> Contratos:
    disponibles = cf.get("available") or []
    tipos = sorted({c.get("contract_type") for c in disponibles if c.get("contract_type")})
    mult = [c for c in disponibles if c.get("contract_type") in ("MULTUP", "MULTDOWN")]
    multup = [c for c in mult if c.get("contract_type") == "MULTUP"]
    multiplicadores = sorted({float(m) for c in multup for m in (c.get("multiplier_range") or [])})
    stakes = [c.get("default_stake") for c in multup if c.get("default_stake") is not None]
    duracion = [{k: c.get(k) for k in ("contract_type", "expiry_type", "start_type",
                                        "min_contract_duration", "max_contract_duration")}
                for c in mult]
    cancel = sorted({str(x) for c in mult for x in (c.get("cancellation_range") or [])})
    return Contratos(tipos, "MULTUP" in tipos, "MULTDOWN" in tipos, multiplicadores,
                     float(stakes[0]) if stakes else None, duracion, cancel)


def _num(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


@dataclass
class Medicion:
    simbolo: str
    esquema: str
    item_active_symbols: dict
    contratos: Contratos
    moneda: Optional[str] = None
    multiplicador_usado: Optional[float] = None
    stake_min: Optional[float] = None
    stake_max: Optional[float] = None
    comision: Optional[float] = None
    comision_descripcion: str = ("proposal.commission, tal cual: el esquema "
                                 "oficial la describe como 'Commission changed "
                                 "in percentage (%)'. No se convirtió.")
    stop_out: Optional[dict] = None
    spot: Optional[float] = None
    sin_medir: Optional[str] = None
    crudos: dict[str, str] = field(default_factory=dict)
    sha256: dict[str, str] = field(default_factory=dict)


async def medir(s: SesionDeriv, item: dict, *, sha_active_symbols: str) -> Medicion:
    cod, esq = codigo(item)
    cf = await s.pedir({"contracts_for": cod})
    m = Medicion(cod, esq, item, resumir_contratos(cf.datos.get("contracts_for") or {}))
    m.crudos["contracts_for"] = cf.crudo
    m.sha256 = {"active_symbols": sha_active_symbols, "contracts_for": cf.sha256}

    moneda = (s.cuenta or {}).get("currency")
    if not m.contratos.multup:
        m.sin_medir = "el instrumento no ofrece MULTUP"
        return m
    if not moneda:
        m.sin_medir = ("sin token no hay cuenta, y sin cuenta no hay moneda para "
                       "la proposal: no se escribe una a mano")
        return m
    if m.contratos.default_stake is None or not m.contratos.multiplicadores:
        m.sin_medir = "contracts_for no trae default_stake o multiplier_range para MULTUP"
        return m

    m.moneda = moneda
    m.multiplicador_usado = m.contratos.multiplicadores[0]
    base = {"proposal": 1, "basis": "stake", "contract_type": "MULTUP",
            "currency": moneda, "symbol": cod, "multiplier": m.multiplicador_usado}

    p1 = await s.pedir({**base, "amount": m.contratos.default_stake})
    m.crudos["proposal_sondeo"], m.sha256["proposal_sondeo"] = p1.crudo, p1.sha256
    stake = ((p1.datos.get("proposal") or {}).get("validation_params") or {}).get("stake") or {}
    m.stake_min, m.stake_max = _num(stake.get("min")), _num(stake.get("max"))
    if m.stake_min is None:
        m.sin_medir = "proposal sin validation_params.stake.min"
        return m

    p2 = await s.pedir({**base, "amount": m.stake_min})
    m.crudos["proposal_stake_min"], m.sha256["proposal_stake_min"] = p2.crudo, p2.sha256
    prop = p2.datos.get("proposal") or {}
    m.comision = _num(prop.get("commission"))
    m.spot = _num(prop.get("spot"))
    m.stop_out = (prop.get("limit_order") or {}).get("stop_out")
    return m


# ══════════════════════════════════════════════════════════════════════════
#  La corrida
# ══════════════════════════════════════════════════════════════════════════

def dir_instrumentos() -> Path:
    return Path(stream_path(PersistenceStream.METRICS)) / "instrumentos"


def fechas_medidas() -> list[str]:
    d = dir_instrumentos()
    if not d.exists():
        return []
    return sorted(p.stem.removeprefix("resumen_") for p in d.glob("resumen_*.json"))


@dataclass
class Resultado:
    estado: str
    fecha: Optional[str] = None
    motivo: str = ""
    seleccion: Optional[Seleccion] = None
    mediciones: list[Medicion] = field(default_factory=list)
    btc_califican: list[str] = field(default_factory=list)
    oro_preregistrado_presente: Optional[bool] = None
    escritos: list[str] = field(default_factory=list)


async def sondear(
    entorno: Entorno,
    *,
    app_id: str,
    token: Optional[str] = None,
    connector: Any = None,
    write: bool = False,
) -> Resultado:
    medidas = fechas_medidas()
    if len(medidas) >= DIAS_DE_SONDA:
        return Resultado(Estado.COMPLETA, motivo=(
            f"{len(medidas)} días medidos ({medidas[0]} .. {medidas[-1]}): la "
            f"sonda de {DIAS_DE_SONDA} días está completa. No se abrió conexión."))

    async with abrir_sesion(entorno, app_id=app_id, token=token,
                            connector=connector) as s:
        t = await s.pedir({"time": 1})
        fecha = datetime.fromtimestamp(t.datos["time"], tz=timezone.utc).date().isoformat()
        if fecha in medidas:
            return Resultado(Estado.YA_MEDIDO_HOY, fecha,
                             f"ya hay resumen de {fecha} (fecha del servidor)")

        a = await s.pedir({"active_symbols": "brief"})
        sel = seleccionar(a.datos.get("active_symbols") or [])
        res = Resultado(Estado.OK, fecha, seleccion=sel)
        if not sel.control_presente:
            res.estado = Estado.INVALIDO
            res.motivo = (f"{CONTROL_POSITIVO} no aparece en active_symbols: la "
                          f"respuesta no es confiable. No se escribe nada.")
            return res

        for item in sel.btc + sel.oro:
            res.mediciones.append(await medir(s, item, sha_active_symbols=a.sha256))

    res.btc_califican = [m.simbolo for m in res.mediciones
                         if m.simbolo in {codigo(i)[0] for i in sel.btc} and m.contratos.multup]
    res.oro_preregistrado_presente = ORO_PREREGISTRADO in {codigo(i)[0] for i in sel.oro}
    if not sel.btc:
        res.estado = Estado.SIN_BTC
        res.motivo = (f"ningún símbolo con BTC en el mercado {MERCADO_CRIPTO!r}. "
                      f"Mercados vistos: {', '.join(sel.mercados_vistos)}")
    elif len(res.btc_califican) > 1:
        res.estado = Estado.PETICION_ADMIN
        res.motivo = (f"califican {len(res.btc_califican)} símbolos de BTC "
                      f"({', '.join(res.btc_califican)}). La sonda no elige: el "
                      f"Admin decide cuál es el de H1-B.")

    if write:
        _escribir(res, entorno=entorno, cuenta=s.cuenta)
    return res


def _escribir(res: Resultado, *, entorno: str, cuenta: Optional[dict]) -> None:
    d = dir_instrumentos()
    d.mkdir(parents=True, exist_ok=True)
    # De la cuenta se guarda lo que describe el instrumento medido, no quién
    # es: ni loginid ni email.
    cuenta_pub = {k: (cuenta or {}).get(k) for k in
                  ("is_virtual", "currency", "landing_company_name")}
    for m in res.mediciones:
        doc = {"schema_version": SCHEMA_VERSION, "fecha_servidor": res.fecha,
               "entorno": entorno, "cuenta": cuenta_pub, **asdict(m)}
        ruta = d / f"{m.simbolo}_{res.fecha}.json"
        ruta.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        res.escritos.append(ruta.name)
    resumen = {"schema_version": SCHEMA_VERSION, "fecha_servidor": res.fecha,
               "entorno": entorno, "estado": res.estado, "motivo": res.motivo,
               "esquemas_de_simbolo": res.seleccion.esquemas,
               "btc_candidatos": [codigo(i)[0] for i in res.seleccion.btc],
               "btc_califican": res.btc_califican,
               "oro_candidatos": [codigo(i)[0] for i in res.seleccion.oro],
               "oro_preregistrado": ORO_PREREGISTRADO,
               "oro_preregistrado_presente": res.oro_preregistrado_presente,
               "sinteticos_descartados": res.seleccion.sinteticos_descartados}
    ruta = d / f"resumen_{res.fecha}.json"
    ruta.write_text(json.dumps(resumen, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    res.escritos.append(ruta.name)


# ══════════════════════════════════════════════════════════════════════════
#  Reporte y CLI
# ══════════════════════════════════════════════════════════════════════════

def render_text(res: Resultado) -> str:
    out = ["═══ SONDA DE INSTRUMENTOS DERIV ═══",
           f"estado: {res.estado}   fecha del servidor: {res.fecha or '—'}"]
    if res.motivo:
        out.append(f"  {res.motivo}")
    if res.seleccion:
        s = res.seleccion
        out.append(f"esquema de símbolo que respondió: {', '.join(s.esquemas)}   "
                   f"control {CONTROL_POSITIVO}: {'presente' if s.control_presente else 'AUSENTE'}")
        if s.sinteticos_descartados:
            out.append(f"sintéticos descartados: {', '.join(s.sinteticos_descartados)}")
    for m in res.mediciones:
        c = m.contratos
        out.append(f"── {m.simbolo}")
        out.append(f"  MULTUP: {'sí' if c.multup else 'no'}   MULTDOWN: "
                   f"{'sí' if c.multdown else 'no'}   multiplicadores: "
                   f"{c.multiplicadores or '—'}")
        out.append(f"  stake mín/máx: {m.stake_min} / {m.stake_max} {m.moneda or ''}   "
                   f"comisión (proposal.commission, sin convertir): {m.comision}")
        for d in c.duracion:
            out.append(f"  duración {d['contract_type']}: {d['min_contract_duration']} .. "
                       f"{d['max_contract_duration']} ({d['expiry_type']})")
        if m.sin_medir:
            out.append(f"  SIN_MEDIR: {m.sin_medir}")
    if res.oro_preregistrado_presente is False:
        out.append(f"AVISO: {ORO_PREREGISTRADO}, el oro del pre-registro, no está "
                   f"en active_symbols.")
    if res.escritos:
        out.append(f"escritos: {', '.join(res.escritos)}")
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sonda_instrumentos",
                                description="Sonda de instrumentos de Deriv (H1-A).")
    p.add_argument("--entorno", required=True, choices=("demo", "real"),
                   help="Obligatorio, sin default.")
    p.add_argument("--write", action="store_true",
                   help="Escribe en metrics/instrumentos/. Sin esto, solo reporta.")
    return p


def main(argv: Optional[Sequence[str]] = None, *, connector: Any = None) -> int:
    args = build_parser().parse_args(argv)
    app_id = load_secret(SecretKey.DERIV_APP_ID, required=False)
    if not app_id:
        print(f"ERROR: falta {SecretKey.DERIV_APP_ID}.", file=sys.stderr)
        return 2
    res = asyncio.run(sondear(args.entorno, app_id=app_id,
                              token=load_secret(SecretKey.DERIV_API_TOKEN, required=False),
                              connector=connector, write=args.write))
    print(render_text(res))
    return 1 if res.estado in (Estado.INVALIDO, Estado.SIN_BTC) else 0


if __name__ == "__main__":
    raise SystemExit(main())
