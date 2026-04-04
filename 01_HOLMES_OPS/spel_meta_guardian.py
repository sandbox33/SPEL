# ── spel_meta_guardian.py ────────────────────────────────────────────────────
# RESPONSABILIDAD ÚNICA: Leer SPEL_META.json UNA SOLA VEZ al arranque,
# validar que contiene z_params, y exponerlos en memoria como singleton.
#
# PRINCIPIO (Regla 26 + Sprint 6 Paso 1):
#   - Si el archivo no existe o no tiene z_params → ERROR RUIDOSO que detiene
#     la sesión ANTES de que cualquier predicción se ejecute.
#   - Una vez cargado en SPEL_META_RUNTIME, el pipeline nunca vuelve a leer
#     el disco. Esto previene el vaciado por desconexión de Drive.
#
# AUDITORIA: Cualquier archivo que use yfinance o CapaBBigQuery
#   debe marcarse como Punto de Falla y excluirse del flujo activo.
#   Esta función también produce ese reporte.
#
# Abraham Fuenmayor · Sprint 6 · 02 Mar 2026
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

# ── Singleton en memoria ──────────────────────────────────────────────────────
# Una vez cargado, vive aquí. El pipeline sólo lo lee de esta variable.
SPEL_META_RUNTIME: dict[str, Any] | None = None

# ── Puntos de Falla conocidos (Auditoría ADN Lógico) ─────────────────────────
_FAILURE_PATTERNS = [
    "yfinance",          # bloqueado en Colab, produce df vacío silencioso
    "CapaBBigQuery",     # reemplazado por BigQueryGDELTAdapter
    "capa_b_bigquery",   # módulo legacy — deprecar en Sprint 6 Paso 4
    "SPELBigQueryExtractor",  # clase del módulo legacy
]


# ══════════════════════════════════════════════════════════════════════════════
# GUARDIÁN DE ARQUITECTURA LSTM  (Regla 13 · INAMOVIBLE)
#
# PROPÓSITO: Firewall pre-load que hace RUIDOSO e INMEDIATO cualquier drift
#   en la topología LSTM antes de que torch.load() toque el disco.
#   Sin este guardián, un hidden_size=128 accidental produce:
#     1. torch.load() silenciosamente carga pesos incompatibles.
#     2. forward() produce dimensiones incorrectas.
#     3. val_dir y entropy_shannon retornan basura — SIN traceback.
#
# TOPOLOGÍA CANÓNICA (Regla 13 — inamovible mientras existan los 14 .pt):
#   input_size  = 20  ← 20 features del parquet canónico v4
#   hidden_size = 64  ← capacidad representacional calibrada en COVID test
#   num_layers  = 1   ← single-layer LSTM; stack invalida gradientes
#
# ESTRUCTURA ESPERADA en SPEL_META.json para verify_checkpoint_integrity():
#   {
#     "checkpoint_hashes": {
#       "NVDA_LSTM_v1_ep004_valloss0.0016.pt": "<sha256_hex_64_chars>",
#       "BTC_LSTM_v4_ep012_valloss0.0011.pt":  "<sha256_hex_64_chars>",
#       "XAU_LSTM_v4_ep005_valloss0.0002.pt":  "<sha256_hex_64_chars>",
#       "NIFTY50_LSTM_v4_ep001_valloss0.0001.pt": "<sha256_hex_64_chars>"
#     }
#   }
#   CRÍTICO: el SHA256 de SPEL_META.json (8216db47bf66...) es el hash del
#   ARCHIVO meta, NO de los checkpoints. Son entidades distintas.
# ══════════════════════════════════════════════════════════════════════════════

# Topología canónica de Regla 13 — definidas aquí para que el guardián sea
# la única fuente de verdad de estos valores (no importar de capa_c_inference).
_CANONICAL_INPUT_SIZE:  int = 20
_CANONICAL_HIDDEN_SIZE: int = 64
_CANONICAL_NUM_LAYERS:  int = 1

# Mapa completo de checkpoints canónicos (14 .pt — Sección 1 del log v23)
_CANONICAL_CHECKPOINTS: frozenset[str] = frozenset({
    "NVDA_LSTM_v1_ep004_valloss0.0016.pt",
    "BTC_LSTM_v4_ep012_valloss0.0011.pt",
    "XAU_LSTM_v4_ep005_valloss0.0002.pt",
    "NIFTY50_LSTM_v4_ep001_valloss0.0001.pt",
    # Los restantes 10 checkpoints alternativos registrados en SPEL_MODELOS
    # se añaden aquí a medida que se confirman en producción (Regla 28).
})

# Tamaño de chunk para lectura de archivos grandes (64 KB — eficiente para
# archivos .pt típicos de 1–50 MB sin cargar todo en RAM)
_SHA256_CHUNK_BYTES: int = 65_536


def enforce_lstm_architecture(config_dict: dict) -> None:
    """
    Valida que un config_dict cumple la topología canónica de Regla 13.

    Firewall de arquitectura: debe llamarse ANTES de cualquier torch.load()
    o instanciación de SPELLSTMModel. Hace RUIDOSO e INMEDIATO cualquier
    drift de hiperparámetros en lugar de dejarlo propagar silenciosamente
    hasta el paso de inferencia.

    Parámetros
    ----------
    config_dict : dict
        Debe contener las claves 'input_size', 'hidden_size', 'num_layers'.
        Acepta LSTMConfig.__dict__, vars(config), o un dict plano.
        Tipos esperados: int. Un valor string '64' también se detecta como
        violación de tipo.

    Lanza
    -----
    RuntimeError
        Si CUALQUIER parámetro viola Regla 13, el mensaje lista TODAS las
        violaciones juntas para un diagnóstico completo en una sola lectura.
        No lanza TypeError separado — todo va al mismo RuntimeError crítico.
    TypeError
        Si config_dict no es un dict.

    Ejemplo de uso correcto::

        from spel_meta_guardian import enforce_lstm_architecture
        enforce_lstm_architecture(vars(LSTMConfig()))  # pasa
        enforce_lstm_architecture({"input_size": 20, "hidden_size": 64, "num_layers": 1})  # pasa

    Ejemplo de violación::

        enforce_lstm_architecture({"input_size": 20, "hidden_size": 128, "num_layers": 1})
        # → RuntimeError: VIOLACIÓN REGLA 13 — hidden_size = 128 ...
    """
    if not isinstance(config_dict, dict):
        raise TypeError(
            f"enforce_lstm_architecture() espera dict, recibió: "
            f"{type(config_dict).__name__}"
        )

    # Especificación canónica: (nombre_param, valor_esperado)
    canonical: list[tuple[str, int]] = [
        ("input_size",  _CANONICAL_INPUT_SIZE),
        ("hidden_size", _CANONICAL_HIDDEN_SIZE),
        ("num_layers",  _CANONICAL_NUM_LAYERS),
    ]

    violations: list[str] = []

    for param, expected in canonical:
        actual = config_dict.get(param)

        if actual is None:
            violations.append(
                f"  • '{param}' AUSENTE en config_dict "
                f"(canónico: {expected})"
            )
            continue

        # Verificación de tipo: rechazar strings, floats, etc.
        if not isinstance(actual, int) or isinstance(actual, bool):
            violations.append(
                f"  • '{param}' tiene tipo incorrecto: "
                f"{type(actual).__name__}({actual!r}) — "
                f"se requiere int({expected})"
            )
            continue

        if actual != expected:
            violations.append(
                f"  • '{param}' = {actual} ≠ {expected} (canónico) — "
                f"cambio invalida los {len(_CANONICAL_CHECKPOINTS)}+ "
                f"checkpoints .pt entrenados"
            )

    if violations:
        raise RuntimeError(
            "\n❌  VIOLACIÓN REGLA 13 — ARQUITECTURA LSTM NO CANÓNICA\n"
            + "\n".join(violations)
            + "\n\n"
            "  Topología inamovible mientras existan los checkpoints .pt:\n"
            f"    input_size={_CANONICAL_INPUT_SIZE} · "
            f"hidden_size={_CANONICAL_HIDDEN_SIZE} · "
            f"num_layers={_CANONICAL_NUM_LAYERS}\n\n"
            "  Para cambiar la arquitectura: re-entrenar TODOS los activos,\n"
            "  actualizar SPEL_META.json y regenerar checkpoint_hashes.\n"
            "  NO continuar con la carga de checkpoints actuales."
        )


def _sha256_file(path: Path) -> str:
    """
    Computa SHA256 hex digest de un archivo en chunks de 64 KB.

    Seguro para archivos .pt de cualquier tamaño (1 MB – 2 GB) sin
    cargar el contenido completo en memoria.

    Parámetros
    ----------
    path : Path
        Ruta al archivo. Debe existir; no se verifica aquí.

    Retorna
    -------
    str
        SHA256 hexdigest en minúsculas, 64 caracteres.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_SHA256_CHUNK_BYTES), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_checkpoint_integrity(
    checkpoint_path: Path,
    meta_dict: dict,
) -> None:
    """
    Valida el SHA256 de un archivo .pt contra el hash registrado en
    SPEL_META.json. Firewall de integridad pre-carga.

    La verificación es necesaria porque torch.load() con weights_only=False
    ejecuta código arbitrario del pickle — cargar un checkpoint corrupto
    o manipulado es un vector de RCE además de producir val_dir incorrecto.

    Parámetros
    ----------
    checkpoint_path : Path
        Ruta absoluta al archivo .pt. Se normaliza a Path internamente.
    meta_dict : dict
        Contenido de SPEL_META.json ya cargado en memoria (SPEL_META_RUNTIME
        o dict equivalente). Debe contener la clave 'checkpoint_hashes'.

        Estructura requerida en meta_dict::

            {
              "checkpoint_hashes": {
                "NVDA_LSTM_v1_ep004_valloss0.0016.pt": "abc123...64chars",
                ...
              }
            }

    Lanza
    -----
    FileNotFoundError
        El archivo .pt no existe en la ruta indicada.
    KeyError
        El nombre del checkpoint no tiene entrada en 'checkpoint_hashes'.
        Acción: ejecutar `register_checkpoint_hash()` para registrarlo.
    RuntimeError
        El SHA256 calculado no coincide con el registrado. Indica corrupción,
        re-entrenamiento accidental o manipulación. NO cargar.
    TypeError
        meta_dict no es dict.

    Notas
    -----
    - La comparación es case-insensitive en el hash (lower().strip()).
    - El hash parcial '8216db47bf66' en el log v23 corresponde al SHA256 de
      SPEL_META.json, NO de los checkpoints. Son hashes distintos.
    - Para archivos .pt grandes (>500 MB): la lectura en chunks de 64 KB
      garantiza uso de RAM constante independientemente del tamaño.
    """
    if not isinstance(meta_dict, dict):
        raise TypeError(
            f"meta_dict debe ser dict, recibido: {type(meta_dict).__name__}"
        )

    path = Path(checkpoint_path)

    # ── Verificación 1: el archivo existe ─────────────────────────────────────
    if not path.exists():
        raise FileNotFoundError(
            f"\n❌  Checkpoint no encontrado: {path}\n"
            "  Verificar que SPEL_MODELOS apunta al directorio correcto\n"
            "  y que el checkpoint fue transferido íntegro a Drive."
        )

    fname = path.name

    # ── Verificación 2: el hash está registrado en meta ───────────────────────
    checkpoint_hashes: dict = meta_dict.get("checkpoint_hashes", {})

    if not isinstance(checkpoint_hashes, dict):
        raise KeyError(
            "\n❌  'checkpoint_hashes' en meta_dict no es un dict.\n"
            f"  Tipo encontrado: {type(checkpoint_hashes).__name__}\n"
            "  Regenerar SPEL_META.json con la estructura correcta."
        )

    if fname not in checkpoint_hashes:
        available = list(checkpoint_hashes.keys())
        raise KeyError(
            f"\n❌  SHA256 de '{fname}' NO registrado en SPEL_META.json.\n"
            f"  Entradas disponibles en 'checkpoint_hashes' "
            f"({len(available)}): {available or '(vacío)'}\n\n"
            "  Acción inmediata — añadir al SPEL_META.json:\n"
            f'    "checkpoint_hashes": {{"{fname}": "<sha256_real>"}}\n\n'
            "  Para obtener el hash real del archivo:\n"
            "    python -c \"import hashlib,sys; "
            "h=hashlib.sha256(); "
            "[h.update(c) for c in iter(lambda:open(sys.argv[1],'rb')"
            ".read(65536),b'')]; print(h.hexdigest())\" "
            f"<ruta/{fname}>"
        )

    expected_sha: str = str(checkpoint_hashes[fname]).lower().strip()

    # Validar que el hash registrado tiene el formato correcto (64 hex chars)
    if len(expected_sha) != 64 or not all(c in "0123456789abcdef" for c in expected_sha):
        raise ValueError(
            f"\n❌  Hash registrado para '{fname}' tiene formato inválido.\n"
            f"  Valor: '{expected_sha}' (longitud: {len(expected_sha)})\n"
            "  Un SHA256 válido tiene exactamente 64 caracteres hexadecimales.\n"
            "  NOTA: el hash '8216db47bf66...' en el log es el SHA del archivo\n"
            "  SPEL_META.json, NO de los checkpoints .pt.\n"
            "  Regenerar hash con el comando indicado en el KeyError anterior."
        )

    # ── Verificación 3: SHA256 del archivo vs hash registrado ─────────────────
    actual_sha: str = _sha256_file(path)

    if actual_sha != expected_sha:
        raise RuntimeError(
            f"\n❌  INTEGRIDAD COMPROMETIDA — SHA256 no coincide para '{fname}':\n"
            f"  Registrado : {expected_sha}\n"
            f"  Calculado  : {actual_sha}\n\n"
            "  Causas posibles (en orden de probabilidad):\n"
            "    1. Corrupción durante transferencia a Drive (parcial upload).\n"
            "    2. Re-entrenamiento accidental sin actualizar checkpoint_hashes.\n"
            "    3. Bit rot en el sistema de archivos de Drive.\n"
            "    4. Sustitución maliciosa del archivo .pt.\n\n"
            "  Acción: Restaurar checkpoint desde backup del teléfono.\n"
            "  NO ejecutar torch.load() sobre este archivo."
        )


def register_checkpoint_hash(
    checkpoint_path: Path,
    meta_dict: dict,
) -> tuple[str, str]:
    """
    Calcula el SHA256 de un checkpoint .pt y retorna (filename, hash) para
    insertar en SPEL_META.json['checkpoint_hashes'].

    Función de utilidad para el proceso de registro inicial — no modifica
    meta_dict ni escribe el archivo (operación de solo lectura).

    Parámetros
    ----------
    checkpoint_path : Path
        Ruta al checkpoint .pt a registrar.
    meta_dict : dict
        Solo se usa para verificar que el hash ya no está registrado
        (previene sobreescritura accidental).

    Retorna
    -------
    tuple[str, str]
        (nombre_archivo, sha256_hex) listo para insertar en meta_dict.

    Lanza
    -----
    FileNotFoundError : El archivo no existe.
    ValueError        : El checkpoint ya tiene hash registrado en meta_dict.
                        Pasar force=True no es opción — cambiar hash de un
                        checkpoint existente requiere decisión explícita.

    Ejemplo::

        fname, sha = register_checkpoint_hash(Path("NVDA_LSTM_v1.pt"), meta)
        print(f'"{fname}": "{sha}"')
        # Copiar output manualmente a SPEL_META.json → checkpoint_hashes
    """
    path = Path(checkpoint_path)
    if not path.exists():
        raise FileNotFoundError(f"Checkpoint no encontrado: {path}")

    fname = path.name
    existing = meta_dict.get("checkpoint_hashes", {})
    if fname in existing:
        raise ValueError(
            f"'{fname}' ya tiene hash registrado: {existing[fname]}\n"
            "Para re-registrar, eliminar la entrada manualmente de "
            "SPEL_META.json y volver a llamar esta función."
        )

    sha = _sha256_file(path)
    print(f'  ✅ SHA256 calculado para {fname}:\n     "{sha}"')
    print(
        f'\n  Añadir a SPEL_META.json bajo "checkpoint_hashes":\n'
        f'    "{fname}": "{sha}"'
    )
    return fname, sha


# ── Función principal de arranque ─────────────────────────────────────────────
def cargar_meta_en_memoria(ruta_meta: str | Path) -> dict[str, Any]:
    """
    Lee SPEL_META.json, valida z_params y carga en el singleton SPEL_META_RUNTIME.
    Lanzar ANTES de cualquier celda que use el motor de inferencia.

    Parámetros
    ----------
    ruta_meta : str | Path
        Ruta absoluta a SPEL_META.json (ej. MyDrive/SPEL_v8/SPEL_META.json).

    Retorna
    -------
    dict con el contenido completo del META.

    Lanza
    -----
    FileNotFoundError  – si el archivo no existe.
    KeyError           – si falta la key 'z_params'.
    ValueError         – si z_params está vacío o no tiene los vectores esperados.
    """
    global SPEL_META_RUNTIME

    ruta = Path(ruta_meta)

    # ── Verificación 1: el archivo existe ─────────────────────────────────────
    if not ruta.exists():
        raise FileNotFoundError(
            f"\n❌  SPEL_META.json NO encontrado en: {ruta}\n"
            "    NO continuar — el motor de inferencia no puede funcionar sin él.\n"
            "    Acción: Restaurar desde backup del teléfono (Regla 26)."
        )

    # ── Verificación 2: parseable ─────────────────────────────────────────────
    try:
        with open(ruta, "r", encoding="utf-8") as fh:
            meta: dict[str, Any] = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"\n❌  SPEL_META.json está corrupto (JSON inválido): {exc}\n"
            "    Restaurar desde backup."
        ) from exc

    # ── Verificación 3: z_params presente y no vacío ─────────────────────────
    if "z_params" not in meta:
        raise KeyError(
            "\n❌  SPEL_META.json existe pero NO contiene 'z_params'.\n"
            f"    Versión detectada: {meta.get('version', 'desconocida')}\n"
            "    Esto indica que el META fue sobreescrito por 00_setup_workspace.py.\n"
            "    Restaurar el META calibrado (version v1.3) desde el backup del teléfono."
        )

    z_params: dict = meta["z_params"]
    if not z_params:
        raise ValueError(
            "\n❌  'z_params' está presente pero VACÍO.\n"
            "    El motor producirá predicciones incorrectas. Restaurar backup."
        )

    # ── Verificación 4: vectores esperados (mean / std por activo) ────────────
    activos_esperados = {"NVDA", "BTC", "XAU", "NIFTY50"}
    activos_presentes = set(z_params.keys())
    faltantes = activos_esperados - activos_presentes
    if faltantes:
        print(
            f"\n⚠️   z_params incompleto — faltan activos: {faltantes}\n"
            "     El motor usará los activos disponibles pero los demás estarán degradados."
        )

    # ── Todo OK: cargar en memoria ────────────────────────────────────────────
    SPEL_META_RUNTIME = meta
    version = meta.get("version", "desconocida")
    print(f"✅  SPEL_META.json cargado en memoria — versión: {version}")
    print(f"    z_params activos: {list(z_params.keys())}")
    print("    Disco desconectado del pipeline — los parámetros viven en RAM.")

    return SPEL_META_RUNTIME


def get_z_params(activo: str) -> dict[str, float]:
    """
    Retorna los z_params (mean, std) de un activo desde el singleton en memoria.
    Nunca vuelve a leer el disco.

    Lanza RuntimeError si cargar_meta_en_memoria() no fue llamado primero.
    """
    if SPEL_META_RUNTIME is None:
        raise RuntimeError(
            "SPEL_META_RUNTIME no inicializado.\n"
            "Llamar a cargar_meta_en_memoria(ruta) al inicio de la sesión."
        )
    z = SPEL_META_RUNTIME.get("z_params", {}).get(activo)
    if z is None:
        raise KeyError(f"z_params no contiene el activo: {activo}")
    return z


# ── Auditoría de Puntos de Falla en un archivo ────────────────────────────────
def auditar_archivo(ruta_py: str | Path) -> dict[str, Any]:
    """
    Analiza un archivo .py y detecta si usa patrones bloqueados
    (yfinance, CapaBBigQuery, etc.).

    Retorna dict con:
        - ruta: str
        - es_punto_de_falla: bool
        - patrones_encontrados: list[str]
        - accion: str
    """
    ruta = Path(ruta_py)
    if not ruta.exists():
        return {"ruta": str(ruta), "error": "Archivo no encontrado"}

    contenido = ruta.read_text(encoding="utf-8", errors="replace")
    encontrados = [p for p in _FAILURE_PATTERNS if p in contenido]

    es_falla = bool(encontrados)
    accion = (
        "🔴 MARCAR para eliminación del flujo activo — no importar en pipeline ni dashboard"
        if es_falla
        else "✅ Sin obstrucciones detectadas"
    )

    return {
        "ruta": str(ruta),
        "es_punto_de_falla": es_falla,
        "patrones_encontrados": encontrados,
        "accion": accion,
    }


def auditar_directorio(directorio: str | Path) -> list[dict[str, Any]]:
    """
    Audita todos los .py de un directorio y retorna la lista de resultados.
    Imprime un resumen en consola.
    """
    base = Path(directorio)
    archivos = sorted(base.rglob("*.py"))

    print("=" * 65)
    print(f"  🔍  AUDITORÍA ADN LÓGICO — {base}")
    print("=" * 65)

    resultados = []
    for f in archivos:
        r = auditar_archivo(f)
        resultados.append(r)
        icono = "🔴" if r.get("es_punto_de_falla") else "✅"
        nombre = f.relative_to(base)
        print(f"  {icono}  {nombre}")
        if r.get("patrones_encontrados"):
            for p in r["patrones_encontrados"]:
                print(f"       └── BLOQUEADO: `{p}`")

    puntos_de_falla = [r for r in resultados if r.get("es_punto_de_falla")]
    print("=" * 65)
    print(f"  Puntos de Falla encontrados: {len(puntos_de_falla)}/{len(archivos)}")
    if puntos_de_falla:
        print("  Acción: Excluir del flujo activo. Mover a archivo_legacy/.")
    print("=" * 65)

    return resultados


# ── Verificación de integridad del directorio SPEL_v8 ────────────────────────
def verificar_estructura_v8(base_v8: str | Path) -> None:
    """
    Verifica que la carpeta SPEL_v8 tiene la estructura canónica.
    Solo lectura — no crea ni modifica nada.
    """
    base = Path(base_v8)
    rutas_obligatorias = [
        "SPEL_META.json",
        "shared_volumes/data_lake",
        "shared_volumes/modelos",
        "shared_volumes/logs",
    ]
    print("\n  🏗️   Verificando estructura SPEL_v8/")
    todo_ok = True
    for ruta in rutas_obligatorias:
        existe = (base / ruta).exists()
        icono = "✅" if existe else "❌"
        print(f"    {icono}  {ruta}")
        if not existe:
            todo_ok = False

    if not todo_ok:
        print("\n  ⚠️   Estructura incompleta. Ejecutar Celda S del Launcher.")
    else:
        print("\n  ✅  Estructura SPEL_v8 íntegra.")


# ── Uso desde Celda 1 del Launcher ───────────────────────────────────────────
if __name__ == "__main__":
    # Ejemplo de uso en Celda 1:
    import os
    SPEL_V8 = os.environ.get("SPEL_V8", "/content/drive/MyDrive/SPEL_v8")
    meta = cargar_meta_en_memoria(f"{SPEL_V8}/SPEL_META.json")
    verificar_estructura_v8(SPEL_V8)
    auditar_directorio(SPEL_V8)
