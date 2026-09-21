"""
tests/test_registro_constantes.py
===================================
Verifica `config/constantes.json` contra el código real, en dos direcciones
que son INDEPENDIENTES y que fallan por motivos distintos:

  · FIDELIDAD  -- lo que el registro dice que vale, ¿vale eso?
  · COBERTURA  -- lo que el código define, ¿está en el registro?

Un solo test que hiciera las dos daría un mensaje ambiguo justo cuando más
importa: "el registro y el código no coinciden" no dice si alguien cambió un
número o si alguien agregó una constante y no la anotó, y esas dos cosas se
arreglan al revés una de la otra.

══ POR QUÉ LA COBERTURA SE HACE POR AST Y SIN IMPORTAR ══

El descubrimiento NO puede depender de que un módulo importe limpio. Si
`core/scoring.py` tuviera un `ImportError` un día, un barrido basado en
`importlib` reportaría cero constantes en ese módulo y el test pasaría en
verde habiendo dejado de mirar el archivo más grande del repo. El AST lee el
texto: un módulo roto se sigue parseando.

Por lo mismo el recorrido es `rglob` sobre cada paquete y no una lista de
archivos: un módulo nuevo entra en la cobertura sin que nadie lo registre en
ningún lado, que es el punto de un test de cobertura.

`execution/` SE RECORRE aunque esté congelado hasta Fase 4. Hoy aporta
CERO constantes de módulo -- sus umbrales son parámetros de constructor
(`CircuitBreaker(starting_equity=..., max_consecutive_losses=...)`), no
valores de módulo. Eso no es un hueco del barrido: se verificó, y hay un
test abajo que lo fija. Si alguien agrega una constante ahí el día que F4
descongele, la cobertura la va a exigir sola.

══ LA TABLA DE NORMALIZACIÓN VIVE ACÁ ══

20 de las 55 constantes no son literales JSON. La conversión está en
`normalizar()` y es la misma que usó el generador del registro. Vive en el
test y no en un módulo del paquete a propósito: es una regla de
VERIFICACIÓN, y ponerla en `core/` o `governance/` la haría parte del
runtime del motor para no servirle a nadie en producción.

  tuple            -> lista (el orden significa)
  frozenset / set  -> lista ordenada, comparada COMO CONJUNTO
  dict con Enum    -> objeto con Enum.value de clave
  Path             -> string POSIX
  None             -> null
  Enum suelto      -> su .value
  dataclass        -> objeto con sus campos

Los dos últimos renglones son EXTENSIONES sobre la tabla del brief, y las
dos hicieron falta de verdad: `DRIVE_STREAMS` es un `frozenset` DE Enums (ni
un dict con claves Enum ni un frozenset de strings), y
`tools.provider_coverage.PROVIDERS` es un dict de dataclasses -- un `repr()`
de dataclass en el JSON sería ilegible y se rompería con cualquier cambio de
formato de repr.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import json
import pathlib
from enum import Enum

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
REGISTRO = RAIZ / "config" / "constantes.json"

#: Los paquetes bajo cobertura. `execution` entra: se recorre, no se toca.
#: `tools` entró el 20-sep-2026: sus constantes deciden qué mide el sistema
#: sobre sí mismo, y una que se despega de producción hace que un reporte
#: diga medir algo que no midió -- que es el defecto que este repo ya tuvo
#: una vez, con el tool midiendo un P90 contra una máscara en P66.
PAQUETES = ("core", "ingestion", "orchestration", "governance", "execution",
            "tools")

#: Categorías y niveles de evidencia admitidos. Cerrados a propósito: un
#: valor nuevo tiene que pasar por acá y por config/README.md, no colarse
#: como string libre.
CATEGORIAS = frozenset({"parametro", "etiqueta", "derivada"})
EVIDENCIAS = frozenset({"legacy_citado", "medido", "provisional_sin_evidencia"})


def normalizar(v):
    """Valor de Python -> su forma JSON. Ver la tabla en el docstring."""
    if isinstance(v, Enum):
        return v.value
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        # `tools.provider_coverage.PROVIDERS` es un dict de ProviderSpec.
        # Se aplana a sus campos: un `repr()` de dataclass en el JSON sería
        # ilegible y se rompería con cualquier cambio de formato de repr.
        return {f.name: normalizar(getattr(v, f.name))
                for f in dataclasses.fields(v)}
    if isinstance(v, pathlib.PurePath):
        return v.as_posix()
    if isinstance(v, (frozenset, set)):
        return sorted(normalizar(x) for x in v)
    if isinstance(v, (tuple, list)):
        return [normalizar(x) for x in v]
    if isinstance(v, dict):
        return {str(normalizar(k)): normalizar(x) for k, x in v.items()}
    return v


def es_constante(nombre: str) -> bool:
    """Mayúsculas ignorando el `_` inicial: `_DERIV_SYMBOL_MAP` cuenta. Un
    privado sigue siendo una constante que alguien puede cambiar sin querer,
    y el guion bajo solo dice que no es API pública."""
    n = nombre.lstrip("_")
    return bool(n) and n.upper() == n and any(c.isalpha() for c in n)


def constantes_del_codigo() -> dict[tuple[str, str], int]:
    """{(modulo, nombre): lineno}, por AST y SIN importar nada."""
    out: dict[tuple[str, str], int] = {}
    for paq in PAQUETES:
        for py in sorted((RAIZ / paq).rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            modulo = py.relative_to(RAIZ).with_suffix("").as_posix().replace("/", ".")
            if modulo.endswith(".__init__"):
                modulo = modulo[: -len(".__init__")]
            for n in ast.parse(py.read_text(encoding="utf-8")).body:
                if isinstance(n, ast.Assign):
                    objetivos = [t.id for t in n.targets if isinstance(t, ast.Name)]
                elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                    objetivos = [n.target.id]
                else:
                    continue
                for nom in objetivos:
                    if es_constante(nom):
                        out[(modulo, nom)] = n.lineno
    return out


@pytest.fixture(scope="module")
def registro() -> dict:
    return json.loads(REGISTRO.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def por_clave(registro) -> dict[tuple[str, str], dict]:
    """LA CLAVE ES (modulo, nombre), NUNCA el nombre solo. Dos módulos
    pueden declarar `SCHEMA_VERSION` y significar cosas distintas -- hoy
    mismo hay `SCHEMA_VERSION` en ingestion.source_registry y nada impide
    que mañana aparezca otro."""
    return {(e["modulo"], e["nombre"]): e for e in registro["constantes"]}


# ═══ Forma del archivo ════════════════════════════════════════════════════

def test_el_registro_existe_y_declara_su_esquema(registro):
    assert registro["schema_version"] == "1.1.0"
    assert registro["actualizado"]
    assert isinstance(registro["constantes"], list)


def test_la_clave_modulo_mas_nombre_es_unica(registro, por_clave):
    """Si dos entradas colisionaran, una taparía a la otra en silencio y la
    cobertura seguiría dando verde con una constante sin verificar."""
    assert len(por_clave) == len(registro["constantes"])


@pytest.mark.parametrize("campo", ["nombre", "modulo", "categoria", "evidencia",
                                   "afecta_resultado", "fuente", "usado_en",
                                   "notas"])
def test_todas_las_entradas_traen_los_campos_obligatorios(registro, campo):
    faltan = [f'{e.get("modulo")}.{e.get("nombre")}'
              for e in registro["constantes"] if campo not in e]
    assert not faltan, f"sin '{campo}': {faltan}"


def test_categoria_y_evidencia_son_de_los_conjuntos_cerrados(registro):
    """`evidencia` no tiene default y no puede ser un string libre: el punto
    del campo es que alguien tenga que elegir, y 'provisional_sin_evidencia'
    es una respuesta válida -- la que no vale es no contestar."""
    for e in registro["constantes"]:
        etiqueta = f'{e["modulo"]}.{e["nombre"]}'
        assert e["categoria"] in CATEGORIAS, f"{etiqueta}: {e['categoria']}"
        assert e["evidencia"] in EVIDENCIAS, f"{etiqueta}: {e['evidencia']}"
        assert e["fuente"].strip(), f"{etiqueta}: fuente vacía"


def test_las_derivadas_traen_expresion_y_no_valor(registro):
    """Una derivada con `valor` fijado sería mentira en cuanto cambie de lo
    que deriva -- REGISTRY_PATH es absoluta y depende de dónde esté clonado
    el repo."""
    for e in registro["constantes"]:
        etiqueta = f'{e["modulo"]}.{e["nombre"]}'
        if e["categoria"] == "derivada":
            assert "expresion" in e and e["expresion"].strip(), etiqueta
            assert "valor" not in e, f"{etiqueta}: derivada con valor fijado"
        else:
            assert "valor" in e, etiqueta
            assert "expresion" not in e, etiqueta


# ═══ Fidelidad: lo registrado coincide con lo real ════════════════════════

def test_fidelidad_de_todos_los_valores(registro):
    """Importa cada módulo y compara aplicando la tabla de normalización.
    Los `frozenset` se comparan COMO CONJUNTO: el JSON los guarda ordenados
    para que el archivo sea estable en el diff, pero el orden no es parte
    del valor y exigirlo haría fallar por un detalle de serialización."""
    desvios = []
    for e in registro["constantes"]:
        etiqueta = f'{e["modulo"]}.{e["nombre"]}'
        modulo = importlib.import_module(e["modulo"])

        if not hasattr(modulo, e["nombre"]):
            desvios.append(f"{etiqueta}: NO EXISTE en el módulo")
            continue
        if e["categoria"] == "derivada":
            continue  # su valor no se compara -- pero el atributo sí existe

        real = normalizar(getattr(modulo, e["nombre"]))
        registrado = e["valor"]

        # frozenset/set: el registro guarda una lista ordenada; se compara
        # como conjunto. Se detecta por el valor REAL, no por el JSON.
        if isinstance(getattr(modulo, e["nombre"]), (frozenset, set)):
            if sorted(real) != sorted(registrado):
                desvios.append(f"{etiqueta}: registrado={registrado} real={real}")
        elif real != registrado:
            desvios.append(f"{etiqueta}: registrado={registrado!r} real={real!r}")

    assert not desvios, (
        "El registro no coincide con el código. Si el valor cambió a "
        "propósito, actualiza config/constantes.json en el MISMO commit:\n  "
        + "\n  ".join(desvios))


def test_las_derivadas_igual_tienen_que_existir(por_clave, registro):
    """No comparar su valor no es no mirarlas: si alguien borra
    GODEL_MASK_PERCENTILE, esto lo dice."""
    derivadas = [e for e in registro["constantes"] if e["categoria"] == "derivada"]
    assert derivadas, "el registro dejó de tener derivadas: revisar"
    for e in derivadas:
        modulo = importlib.import_module(e["modulo"])
        assert hasattr(modulo, e["nombre"]), f'{e["modulo"]}.{e["nombre"]} no existe'


# ═══ Cobertura: lo que el código define está registrado ═══════════════════

def test_cobertura_toda_constante_del_codigo_esta_registrada(por_clave):
    """EL TEST QUE JUSTIFICA EL REGISTRO. Sin esto, el archivo se
    desactualiza igual que cualquier documento: alguien agrega una constante,
    nadie la anota, y seis meses después el registro dice 55 cuando hay 60.

    Falla NOMBRANDO la que falta, con su archivo y su línea: un "faltan 3"
    obliga a un diff manual contra 55 entradas."""
    registradas = set(por_clave)
    sin_registrar = [f"{mod}.{nom}  ({mod.replace('.', '/')}.py:{ln})"
                     for (mod, nom), ln in sorted(constantes_del_codigo().items())
                     if (mod, nom) not in registradas]

    assert not sin_registrar, (
        "Constantes en el código que no están en config/constantes.json. "
        "Agrégalas con su `evidencia` -- "
        "'provisional_sin_evidencia' es una respuesta válida:\n  "
        + "\n  ".join(sin_registrar))


def test_el_barrido_mira_los_seis_paquetes_de_verdad():
    """Contraprueba: si `constantes_del_codigo()` devolviera un dict vacío
    por un error de rutas, el test de cobertura pasaría en verde sin haber
    mirado nada. Es el modo de falla silencioso de todo barrido."""
    halladas = constantes_del_codigo()
    assert len(halladas) > 80, f"solo {len(halladas)} constantes halladas"

    modulos = {mod for mod, _ in halladas}
    for esperado in ("core.scoring", "ingestion.adapters",
                     "governance.persistence", "orchestration.cycle",
                     "tools.measure_godel_samples"):
        assert esperado in modulos, f"{esperado} quedó fuera del barrido"


def test_execution_se_recorre_aunque_hoy_no_aporte_ninguna():
    """`execution/` está congelado hasta F4 y no tiene constantes de módulo:
    sus umbrales son parámetros de constructor. Esto FIJA las dos mitades --
    que el paquete se recorre, y que su aporte cero es un hecho verificado y
    no un barrido que no llegó hasta ahí."""
    archivos = [p for p in (RAIZ / "execution").rglob("*.py")
                if "__pycache__" not in p.parts]
    assert len(archivos) >= 3, "el barrido no está viendo execution/"

    de_execution = {(m, n) for (m, n) in constantes_del_codigo()
                    if m.startswith("execution")}
    assert de_execution == set(), (
        f"execution/ ya tiene constantes de módulo: {sorted(de_execution)}. "
        f"Regístralas -- el paquete está congelado para CAMBIOS, no para "
        f"quedar fuera del registro.")


def test_no_hay_entradas_huerfanas(registro):
    """La tercera comprobación, barata: ninguna entrada apunta a una
    constante que ya no exista. Se separa de la fidelidad porque el arreglo
    es el contrario -- acá sobra una entrada, allá falta un valor."""
    huerfanas = []
    for e in registro["constantes"]:
        try:
            modulo = importlib.import_module(e["modulo"])
        except ImportError:
            huerfanas.append(f'{e["modulo"]}: el módulo no existe')
            continue
        if not hasattr(modulo, e["nombre"]):
            huerfanas.append(f'{e["modulo"]}.{e["nombre"]}')

    assert not huerfanas, (
        "Entradas del registro que apuntan a constantes inexistentes "
        "(¿se retiró o se renombró algo?):\n  " + "\n  ".join(huerfanas))


# ═══ Los tres tipos problemáticos, un caso cada uno ═══════════════════════

def test_un_frozenset_se_compara_como_conjunto(por_clave):
    """FX_GOBIERNO_ONLY_ASSETS. El JSON guarda una lista y el valor real es
    un frozenset: sin la regla, la comparación directa falla siempre."""
    from core.scoring import FX_GOBIERNO_ONLY_ASSETS

    e = por_clave[("core.scoring", "FX_GOBIERNO_ONLY_ASSETS")]
    assert isinstance(e["valor"], list)
    assert isinstance(FX_GOBIERNO_ONLY_ASSETS, frozenset)
    assert set(e["valor"]) == set(FX_GOBIERNO_ONLY_ASSETS)


def test_un_dict_con_claves_enum_se_normaliza_por_value(por_clave):
    """PERSISTENCE_RELATIVE_PATHS. Sus claves son PersistenceStream, que no
    es serializable: se guardan por `.value`."""
    from governance.persistence import PERSISTENCE_RELATIVE_PATHS, PersistenceStream

    e = por_clave[("governance.persistence", "PERSISTENCE_RELATIVE_PATHS")]
    assert all(isinstance(k, PersistenceStream) for k in PERSISTENCE_RELATIVE_PATHS)
    assert all(isinstance(k, str) for k in e["valor"])
    assert e["valor"] == {k.value: v for k, v in PERSISTENCE_RELATIVE_PATHS.items()}


def test_un_frozenset_de_enums_tambien(por_clave):
    """DRIVE_STREAMS. La combinación que la tabla del brief no cubría: no es
    un dict con claves Enum ni un frozenset de strings, sino las dos cosas a
    la vez."""
    from governance.persistence import DRIVE_STREAMS

    e = por_clave[("governance.persistence", "DRIVE_STREAMS")]
    assert set(e["valor"]) == {s.value for s in DRIVE_STREAMS}


def test_una_derivada_no_fija_su_valor(por_clave):
    """GODEL_MASK_PERCENTILE deriva de ENTROPY_STATE_PERCENTILES[1]. Fijar
    66.0 en el registro haría que cambiar los terciles dejara al registro
    mintiendo sin que ningún test lo note -- que es exactamente lo que el
    registro existe para evitar."""
    from core.scoring import ENTROPY_STATE_PERCENTILES, GODEL_MASK_PERCENTILE

    e = por_clave[("core.scoring", "GODEL_MASK_PERCENTILE")]
    assert e["categoria"] == "derivada"
    assert "valor" not in e
    assert e["expresion"] == "ENTROPY_STATE_PERCENTILES[1]"
    assert GODEL_MASK_PERCENTILE == ENTROPY_STATE_PERCENTILES[1]


# ═══ Lo que el registro dice de sí mismo ══════════════════════════════════

def test_ninguna_constante_tiene_evidencia_medida_sin_decir_qué_se_midió(registro):
    """`medido` exige que la fuente diga QUÉ se midió y cuándo. Sin eso es
    una etiqueta más prestigiosa que 'provisional' y con el mismo contenido,
    que es peor que no tener el campo."""
    for e in registro["constantes"]:
        if e["evidencia"] == "medido":
            assert any(a in e["fuente"] for a in ("2026", "2025")), (
                f'{e["modulo"]}.{e["nombre"]}: evidencia "medido" sin fecha '
                f'en `fuente`')


def test_el_registro_cubre_el_stream_config_que_estaba_vacio():
    """`governance/persistence.py` declara CONFIG -> "config/" desde el
    patch 0010 y el directorio no existía. Este es su primer contenido
    real."""
    from governance.persistence import PersistenceStream, stream_path

    assert stream_path(PersistenceStream.CONFIG) == "config/"
    assert (RAIZ / "config").is_dir()
    assert REGISTRO.is_file()


# ═══ afecta_resultado: ortogonal a categoria ══════════════════════════════

def test_afecta_resultado_es_booleano_en_todas(registro):
    """Booleano de verdad, no un string "true". Un campo que a veces es bool
    y a veces string se filtra por `if e["afecta_resultado"]` y el string
    "false" evalúa a True."""
    for e in registro["constantes"]:
        assert isinstance(e["afecta_resultado"], bool), (
            f'{e["modulo"]}.{e["nombre"]}: {e["afecta_resultado"]!r}')


def test_hay_etiquetas_que_si_afectan_el_resultado(por_clave):
    """EL PUNTO DEL CAMPO, y por qué no alcanzaba con `categoria`. Las cinco
    de abajo son `etiqueta` -- son listas de identificadores, no magnitudes --
    y cambiar cualquiera cambia qué eventos pasan el filtro, qué activos
    corre el ciclo o qué símbolo se le pide al proveedor.

    Sin este campo, un lector que filtrara por `categoria == "parametro"`
    para saber qué tocar con cuidado se saltearía exactamente las que más
    mueven el sistema."""
    for modulo, nombre in [
        ("core.scoring", "CORE_COUNTRY_FILTERS"),
        ("core.scoring", "GOBIERNO_COUNTRY_FILTERS"),
        ("core.scoring", "FX_GOBIERNO_ONLY_ASSETS"),
        ("ingestion.adapters", "_DERIV_SYMBOL_MAP"),
        ("orchestration.cycle", "DEFAULT_CYCLE_ASSETS"),
    ]:
        e = por_clave[(modulo, nombre)]
        assert e["categoria"] == "etiqueta", f"{nombre} dejó de ser etiqueta"
        assert e["afecta_resultado"] is True, f"{nombre} debería afectar"


def test_lo_que_no_afecta_el_resultado_es_solo_texto(registro):
    """La contracara: `afecta_resultado=False` tiene que ser excepcional y
    justificable. Si esto crece, el campo dejó de discriminar."""
    no_afectan = [f'{e["modulo"]}.{e["nombre"]}'
                  for e in registro["constantes"] if not e["afecta_resultado"]]

    assert len(no_afectan) <= 8, (
        f"demasiadas constantes declaradas sin efecto ({len(no_afectan)}): "
        f"{no_afectan}. El campo dejó de discriminar.")
    assert "core.scoring.GODEL_CRITERIA_VERSION" in no_afectan, (
        "el sello de criterio pasó a afectar el resultado: si ya existe la "
        "comprobación que recalcula al detectar una versión vieja, "
        "actualiza el registro")


# ═══ Las dos derivadas de tools/, verificadas de verdad ═══════════════════

def test_el_umbral_zscore_es_phi_inversa_del_percentil_de_la_mascara():
    """RECALCULADO POR BISECCIÓN sobre la CDF normal, no comparado contra
    una constante copiada. Si alguien mueve GODEL_MASK_PERCENTILE y no
    actualiza el umbral del modo ZSCORE, el tool mide un percentil y
    producción usa otro -- que es el defecto exacto que este repo ya tuvo
    cuando el tool medía un P90 contra una máscara que operaba en P66.

    Bisección y no `NormalDist.inv_cdf` a secas para que el test verifique
    la RELACIÓN (Φ(x) = p) y no reproduzca la misma llamada que produciría
    el valor -- si `inv_cdf` tuviera un bug, comparar contra sí misma no lo
    vería."""
    from statistics import NormalDist

    from core.scoring import GODEL_MASK_PERCENTILE
    from tools.measure_godel_samples import ZSCORE_UMBRAL_GLOBAL_DEFAULT

    objetivo = GODEL_MASK_PERCENTILE / 100.0
    cdf = NormalDist().cdf

    bajo, alto = -10.0, 10.0
    for _ in range(200):
        medio = (bajo + alto) / 2.0
        if cdf(medio) < objetivo:
            bajo = medio
        else:
            alto = medio
    z = (bajo + alto) / 2.0

    assert abs(ZSCORE_UMBRAL_GLOBAL_DEFAULT - z) < 1e-9, (
        f"ZSCORE_UMBRAL_GLOBAL_DEFAULT={ZSCORE_UMBRAL_GLOBAL_DEFAULT} pero "
        f"Φ⁻¹({objetivo}) = {z}. ¿Cambió GODEL_MASK_PERCENTILE sin que se "
        f"actualizara el umbral del modo ZSCORE?")
    assert abs(cdf(ZSCORE_UMBRAL_GLOBAL_DEFAULT) - objetivo) < 1e-9


def test_la_ventana_del_tool_es_LA_de_produccion_no_una_copia(por_clave):
    """`ROLLING_WINDOW_DEFAULT` se importa de core.scoring en vez de repetir
    el 252. `is` y no `==`: dos literales iguales pasarían un `==` y se
    separarían silenciosamente en cuanto uno de los dos cambie."""
    from core.scoring import GODEL_ROLLING_WINDOW_DAYS
    from tools.measure_godel_samples import ROLLING_WINDOW_DEFAULT

    assert ROLLING_WINDOW_DEFAULT is GODEL_ROLLING_WINDOW_DAYS

    e = por_clave[("tools.measure_godel_samples", "ROLLING_WINDOW_DEFAULT")]
    assert e["categoria"] == "derivada"
    assert e["expresion"] == "core.scoring.GODEL_ROLLING_WINDOW_DAYS"


def test_un_dict_de_dataclasses_se_aplana_por_campos(por_clave):
    """`tools.provider_coverage.PROVIDERS`. La segunda extensión de la tabla
    de normalización, y la encontró este mismo test al ampliarse a tools/."""
    from tools.provider_coverage import PROVIDERS

    e = por_clave[("tools.provider_coverage", "PROVIDERS")]
    assert set(e["valor"]) == set(PROVIDERS)
    for nombre, spec in PROVIDERS.items():
        assert e["valor"][nombre]["secret_key"] == spec.secret_key
        assert "ProviderSpec(" not in json.dumps(e["valor"][nombre])
