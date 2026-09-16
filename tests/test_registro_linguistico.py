"""
tests/test_registro_linguistico.py
====================================
Fija el registro del español del repo: ningún string ni docstring de un
`.py` usa formas voseantes.

POR QUÉ UN TEST Y NO UNA CONVENCIÓN ESCRITA. Se arreglaron cinco
ocurrencias a mano en tres sesiones distintas y volvieron igual, porque
nada las miraba: el voseo entra de a una palabra en un mensaje de error que
nadie relee. Una convención en CLAUDE.md no falla en rojo; esto sí.

QUÉ MIRA: los literales de string del AST -- docstrings incluidos, porque
un docstring ES un literal. No mira comentarios (`#`), que el AST descarta:
son para quien lee el código, no texto que el usuario ve. No mira `.md`:
ESTADO.md y decision-log.md son notas de trabajo de Altair y su registro es
suyo, no del producto.

══ LAS TRES REGLAS, Y POR QUÉ NO ES UNA SOLA ══

El acento final no alcanza como criterio, y eso se midió sobre este repo
antes de escribir la regla -- no se supuso:

  · `-á` final (imperativo de los verbos en -ar: "pasá", "usá", "exportá").
    NINGUNA otra forma verbal del español termina en `á` tónica, así que la
    regla es automática y solo necesita descartar tres palabras que no son
    verbos. Es la clase más grande y la que más entra.

  · `-ás/-és/-ís` final (presente indicativo: "querés", "podés", "usás").
    También automática. El repo entero tiene NUEVE palabras distintas con
    esa terminación y ocho son de la lista de abajo -- señal/ruido
    excelente, y la lista es cerrada: son adverbios y sustantivos comunes,
    no una categoría que crezca.

  · `-é/-í` final (imperativo de -er/-ir: "corré", "definí"). ACÁ NO SE
    PUEDE PATRONEAR, y por eso van en una lista explícita. "definí" es a la
    vez imperativo voseante ("definí la variable") y primera persona del
    pretérito perfecto ("yo definí"), que es prosa perfectamente correcta y
    que este repo usa: hoy mismo hay `busqué`, `encontré`, `afirmé` y
    `preferí` en docstrings. Una regla por acento los pondría en rojo y
    entrenaría a editar prosa legítima para callar al linter, que es peor
    que el voseo.

LIMITACIÓN CONOCIDA, dicha acá y no descubierta después: por lo anterior,
un imperativo de -ir nuevo que no esté en `IMPERATIVOS_EXPLICITOS` pasa sin
que este test lo note. Su forma de indicativo ("definís") sí cae por la
segunda regla. Se acepta el hueco a cambio de cero falsos positivos.

══ `execution/` QUEDA EXCLUIDO, Y ES A PROPÓSITO ══

`execution/circuit_breaker.py:115` dice "si querés que consecutive_losses NO
se resetee solo". Es voseo y no se toca: `execution/` está CONGELADO hasta
Fase 4 por regla de CLAUDE.md, y su md5 se verifica en cada PR.

Sin esta exclusión el test saldría rojo en main el día que se fusione, y un
rojo en main empuja exactamente a lo que la regla prohíbe -- editar un
archivo congelado para poner la suite en verde.

LA EXCLUSIÓN SE LEVANTA CUANDO FASE 4 DESCONGELE `execution/`, NO ANTES.
Hay un test abajo que verifica que la ocurrencia sigue estando donde se
dice: si alguien limpia ese archivo, el recordatorio sale en rojo y la
exclusión se borra en el mismo patch. Una exclusión que sobrevive a su
motivo es cómo un archivo queda fuera de cobertura para siempre.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent

#: Directorios que no son código del proyecto.
_IGNORADOS = (".git", ".venv", "venv", "__pycache__", ".pytest_cache",
              ".spel_drive_stream", "build", "dist")

#: CONGELADO HASTA FASE 4 -- ver el docstring del módulo. Esto no es una
#: lista de archivos "difíciles de arreglar": es un prefijo, y el único.
EXCLUIDO_POR_CONGELADO = ("execution",)

#: La ocurrencia concreta que motiva la exclusión. Se nombra para que la
#: exclusión sea auditable: si esto cambia, hay que revisar la exclusión.
VOSEO_CONGELADO = ("execution/circuit_breaker.py", "querés")

#: Este archivo escribe las formas como DATO. Excluirse a sí mismo es la
#: única forma de que un test que busca palabras no se encuentre solo.
EXCLUIDO_POR_SER_LA_DEFINICION = ("tests/test_registro_linguistico.py",)

#: Palabras terminadas en `-á` que NO son verbos.
#:
#: LA PRIMERA VERSIÓN TENÍA TRES PALABRAS -- exactamente las que el barrido
#: encontraba en el repo ese día -- mientras que la lista de abajo ya se
#: había extendido a términos que todavía no aparecían pero son plausibles
#: en español técnico. Esa asimetría era un descuido, no un criterio: las
#: dos listas existen para lo mismo, así que las dos se pueblan igual, por
#: categoría y no por inventario.
#:
#: La categoría que hacía falta es la que este proyecto garantiza: SPEL
#: penaliza error de modelo con entropía geopolítica de GDELT, y sus filtros
#: se escriben por país. Los nombres de país con `-á` tónica van a aparecer
#: en prosa antes o después, y el día que aparezcan pondrían la suite en
#: rojo por una palabra que nadie escribió mal.
NO_VERBOS_EN_A = frozenset({
    # adverbios y deícticos
    "acá", "allá", "está", "quizá", "ojalá",
    # topónimos con -á tónica. GDELT clasifica por país: esto no es
    # hipotético, es el vocabulario del dominio.
    "canadá", "panamá", "bogotá", "paraná",
})

#: Palabras terminadas en `-ás/-és/-ís` que no son voseo. Ocho salieron del
#: barrido del repo; el resto son las mismas categorías que arriba --
#: términos de español técnico y gentilicios, que en un proyecto que
#: clasifica eventos por país son tan previsibles como los topónimos.
NO_VERBOS_EN_AS_ES_IS = frozenset({
    # las ocho que el barrido encontró en el repo
    "después", "además", "atrás", "país", "demás", "revés", "detrás",
    "jamás",
    # adverbios y español técnico
    "quizás", "través", "interés", "mes", "compás", "estrés", "anís",
    "análisis", "crisis", "tesis", "praxis",
    # gentilicios y topónimos: misma categoría que canadá/panamá arriba
    "inglés", "francés", "japonés", "portugués", "libanés", "holandés",
    "danés", "irlandés", "escocés", "taiwanés", "bangladés",
})

#: Imperativos de -er/-ir, que no se pueden detectar por patrón (ver el
#: docstring). Lista explícita, necesariamente incompleta, y eso está
#: documentado como limitación aceptada.
IMPERATIVOS_EXPLICITOS = frozenset({
    "corré", "poné", "hacé", "tené", "leé", "vení", "ponete", "hacete",
    "fijate", "acordate", "quedate", "movete", "andate",
    "definí", "seguí", "escribí", "elegí", "medí", "subí", "corregí",
    "abrí", "salí", "decí", "sentí", "pedí",
})

_FIN_EN_A = re.compile(r"\b[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ]{2,}á\b")
_FIN_EN_AS_ES_IS = re.compile(r"\b[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ]{2,}[áéí]s\b")
_PALABRA = re.compile(r"\b[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ]+\b")


def formas_voseantes(texto: str) -> list[str]:
    """Las formas voseantes de un texto, por las tres reglas. Pública para
    que sea probable por sí sola, sin pasar por el barrido del repo."""
    hallazgos = []
    for m in _FIN_EN_A.finditer(texto):
        if m.group(0).lower() not in NO_VERBOS_EN_A:
            hallazgos.append(m.group(0))
    for m in _FIN_EN_AS_ES_IS.finditer(texto):
        if m.group(0).lower() not in NO_VERBOS_EN_AS_ES_IS:
            hallazgos.append(m.group(0))
    for m in _PALABRA.finditer(texto):
        if m.group(0).lower() in IMPERATIVOS_EXPLICITOS:
            hallazgos.append(m.group(0))
    return hallazgos


def _relativa(p: pathlib.Path) -> str:
    return p.relative_to(RAIZ).as_posix()


def archivos_py_del_repo() -> list[pathlib.Path]:
    """Todos los .py del repo menos los excluidos. Se calcula sobre el
    árbol real: un archivo nuevo entra en la cobertura sin que nadie lo
    registre en ningún lado, que es el punto de un test repo-wide."""
    out = []
    for py in sorted(RAIZ.rglob("*.py")):
        if any(parte in _IGNORADOS for parte in py.parts):
            continue
        rel = _relativa(py)
        if rel.split("/")[0] in EXCLUIDO_POR_CONGELADO:
            continue
        if rel in EXCLUIDO_POR_SER_LA_DEFINICION:
            continue
        out.append(py)
    return out


def literales_de(py: pathlib.Path) -> list[tuple[int, str]]:
    """Todos los literales de string, docstrings incluidos: un docstring
    es un literal y no hay razón para tratarlo distinto."""
    try:
        arbol = ast.parse(py.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    return [(n.lineno, n.value) for n in ast.walk(arbol)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


# ═══ La regla ═════════════════════════════════════════════════════════════

def test_ningun_string_del_repo_usa_voseo():
    """EL TEST DEL BRIEF. Barre el árbol real, así que un archivo nuevo
    queda cubierto sin registrarlo en ningún lado."""
    ofensas = []
    for py in archivos_py_del_repo():
        for lineno, texto in literales_de(py):
            for forma in formas_voseantes(texto):
                ofensas.append(f"{_relativa(py)}:{lineno}  {forma!r}")

    assert not ofensas, (
        "Formas voseantes en strings o docstrings. El registro del repo es "
        "español neutro (\"pasa\", no \"pasá\"):\n  " + "\n  ".join(ofensas))


def test_el_barrido_mira_una_cantidad_creible_de_archivos():
    """Contraprueba del anterior: si `archivos_py_del_repo()` devolviera
    lista vacía por un error de rutas, el test de arriba pasaría en verde
    sin haber mirado nada. Es el modo de falla que hace inútil a un test
    repo-wide, y es silencioso."""
    archivos = archivos_py_del_repo()

    assert len(archivos) > 30, f"solo {len(archivos)} archivos barridos"
    nombres = {_relativa(p) for p in archivos}
    for esperado in ("ingestion/run_gdelt.py", "core/scoring.py",
                     "tools/heartbeat.py", "governance/persistence.py"):
        assert esperado in nombres, f"{esperado} quedó fuera del barrido"


def test_los_docstrings_tambien_se_miran_no_solo_los_mensajes():
    """Un docstring es un literal del AST. Se verifica de verdad: las dos
    ocurrencias que se arreglaron en tools/ y tests/ vivían en un docstring
    y en un mensaje de error, respectivamente."""
    fuente = 'def f():\n    """Definilo: pasá el flag."""\n    return 1\n'
    arbol = ast.parse(fuente)
    textos = [n.value for n in ast.walk(arbol)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)]

    assert textos, "el AST no expuso el docstring"
    assert any(formas_voseantes(t) for t in textos)


# ═══ Las reglas, una por una ══════════════════════════════════════════════

class TestReglas:
    @pytest.mark.parametrize("forma", [
        "pasá", "usá", "exportá", "rotá", "cambiá", "mirá", "dejá", "agregá",
        "revisá", "tomá", "probá", "borrá", "verificá", "mandá", "andá"])
    def test_imperativos_en_a_se_detectan_por_patron(self, forma):
        """Sin lista: ninguna otra forma verbal del español termina en `á`
        tónica. `exportá` y `rotá` no estaban en ninguna lista mía y el
        patrón las encontró igual -- que es exactamente para lo que está."""
        assert formas_voseantes(f"Entonces {forma} el valor.") == [forma]

    @pytest.mark.parametrize("forma", [
        "querés", "podés", "tenés", "sabés", "hacés", "usás", "pasás",
        "definís", "seguís", "escribís"])
    def test_presente_indicativo_se_detecta_por_patron(self, forma):
        assert formas_voseantes(f"Si {forma} eso, falla.") == [forma]

    @pytest.mark.parametrize("forma", ["corré", "poné", "definí", "fijate"])
    def test_imperativos_de_er_ir_salen_de_la_lista_explicita(self, forma):
        assert formas_voseantes(f"{forma} el archivo.") == [forma]


class TestSinFalsosPositivos:
    @pytest.mark.parametrize("palabra", [
        "acá", "allá", "está", "así", "aquí", "ahí", "qué", "porqué", "esté",
        "después", "además", "atrás", "país", "demás", "revés", "detrás",
        "jamás", "más"])
    def test_las_palabras_normales_no_se_marcan(self, palabra):
        """Las 18 que aparecen de verdad en el repo. `acá` sale 104 veces:
        un falso positivo acá haría el test inútil desde el primer día."""
        assert formas_voseantes(f"Y {palabra} termina la frase.") == []

    @pytest.mark.parametrize("forma", ["busqué", "encontré", "afirmé", "preferí"])
    def test_el_preterito_en_primera_persona_no_es_voseo(self, forma):
        """LA RAZÓN ENTERA POR LA QUE `-é/-í` NO SE PATRONEA. Las cuatro
        están hoy en docstrings del repo. Marcarlas entrenaría a editar
        prosa correcta para callar al linter."""
        assert formas_voseantes(f"Lo {forma} y no estaba.") == []

    @pytest.mark.parametrize("palabra", [
        "Canadá", "Panamá", "Bogotá", "Paraná", "quizá", "ojalá"])
    def test_toponimos_y_adverbios_en_a_no_son_voseo(self, palabra):
        """LA ASIMETRÍA QUE SE CORRIGIÓ. `NO_VERBOS_EN_A` tenía tres
        palabras -- exactamente las que el barrido encontraba ese día --
        mientras que la lista de `-ás/-és/-ís` ya cubría términos que
        todavía no aparecían. Las dos listas existen para lo mismo, así que
        se pueblan igual: por categoría, no por inventario.

        Y la categoría que faltaba es la que este proyecto garantiza: SPEL
        clasifica eventos GDELT por país. "Canadá" en una prosa sobre
        filtros de país habría puesto la suite en rojo por una palabra que
        nadie escribió mal."""
        assert formas_voseantes(f"El caso de {palabra} es distinto.") == []

    @pytest.mark.parametrize("palabra", [
        "japonés", "portugués", "libanés", "bangladés", "taiwanés"])
    def test_los_gentilicios_tampoco(self, palabra):
        """Misma categoría que los topónimos, del otro lado de la regla."""
        assert formas_voseantes(f"El mercado {palabra} cerró.") == []

    def test_las_dos_listas_se_poblaron_por_categoria_no_por_inventario(self):
        """Fija la simetría en sí, no una palabra concreta: las dos listas
        tienen que ir más allá de lo que el repo contiene hoy. Si una vuelve
        a quedarse en el inventario del día, esto sale en rojo.

        El número es deliberadamente bajo -- no mide calidad, detecta el
        modo de falla concreto: una lista con las 3 palabras que el grep
        devolvió."""
        assert len(NO_VERBOS_EN_A) >= 8, (
            "NO_VERBOS_EN_A volvió a ser el inventario del barrido")
        assert len(NO_VERBOS_EN_AS_ES_IS) >= 8

        # y las dos cubren la categoría del dominio: nombres de lugar
        assert {"canadá", "panamá", "bogotá"} <= NO_VERBOS_EN_A
        assert {"inglés", "francés"} <= NO_VERBOS_EN_AS_ES_IS

    def test_no_marca_dentro_de_otra_palabra(self):
        """`venía`/`decía` contienen `vení`/`decí`. Sin límites de palabra
        el test marcaría imperfectos, que son prosa normal."""
        assert formas_voseantes("Eso venía de antes y decía otra cosa.") == []

    def test_un_texto_tecnico_normal_no_dispara_nada(self):
        assert formas_voseantes(
            "El país está más allá del análisis: después de la crisis, "
            "acá se ve al revés y además atrás quedó el interés.") == []


# ═══ La exclusión de execution/, y su fecha de vencimiento ════════════════

class TestExclusionDeExecution:
    def test_execution_no_entra_en_el_barrido(self):
        nombres = {_relativa(p) for p in archivos_py_del_repo()}
        assert not any(n.startswith("execution/") for n in nombres)

    def test_el_voseo_congelado_sigue_ahi_o_hay_que_levantar_la_exclusion(self):
        """LA EXCLUSIÓN TIENE FECHA DE VENCIMIENTO Y ESTE TEST ES EL
        RECORDATORIO. Si alguien limpia circuit_breaker.py (o Fase 4 lo
        descongela y se reescribe), esto sale en rojo y la exclusión se
        borra en el mismo patch.

        Sin esto, la exclusión sobrevive a su motivo -- que es exactamente
        cómo un directorio queda fuera de cobertura para siempre."""
        archivo, forma = VOSEO_CONGELADO
        ruta = RAIZ / archivo
        assert ruta.exists(), f"{archivo} ya no existe: revisar la exclusión"

        encontrado = any(
            forma in texto for _, texto in literales_de(ruta))
        assert encontrado, (
            f"Ya no hay {forma!r} en {archivo}. Si execution/ se limpió o si "
            f"Fase 4 lo descongeló, borra EXCLUIDO_POR_CONGELADO y deja que "
            f"el barrido lo cubra como a todo lo demás.")

    def test_la_exclusion_es_solo_execution(self):
        """Que no se convierta en el cajón donde va a parar todo lo que
        molesta."""
        assert EXCLUIDO_POR_CONGELADO == ("execution",)
        assert len(EXCLUIDO_POR_SER_LA_DEFINICION) == 1
