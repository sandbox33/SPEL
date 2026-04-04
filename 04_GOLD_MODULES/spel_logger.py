# ── SPEL · spel_logger.py ────────────────────────────────────────────────────
# Logger centralizado para todos los módulos SPEL
# Autor: Abraham Fuenmayor · v1.0 · 07 Mar 2026
#
# USO:
#   from spel_logger import get_logger
#   logger = get_logger('capa_c', activo='NVDA')
#   logger.info("val_dir calculado: 0.723")
#
# NIVELES:
#   logger.debug()    → detalles internos (z-params, shapes, bindings)
#   logger.info()     → eventos normales (carga activo, val_dir, Gödel)
#   logger.warning()  → algo raro pero no fatal (EF-01, NaN detectado)
#   logger.error()    → fallo con contexto (checkpoint no carga, etc.)
#   logger.critical() → fallo que detiene el sistema
# ─────────────────────────────────────────────────────────────────────────────

import atexit
import atexit
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone


def get_logger(nombre_modulo: str, activo: str = None) -> logging.Logger:
    """
    Retorna un logger SPEL con binding explícito al activo.

    Args:
        nombre_modulo: nombre del módulo que llama (ej: 'capa_c', 'orchestrator')
        activo: símbolo del activo si aplica (ej: 'NVDA', 'BTC'). None si es global.

    Returns:
        logging.Logger configurado con handlers de consola y archivo.

    Ejemplos:
        logger = get_logger('capa_c', activo='NVDA')
        # → nombre: SPEL.NVDA.capa_c
        # → archivo: logs/spel_20260307.log

        logger = get_logger('guardian')
        # → nombre: SPEL.guardian
        # → archivo: logs/spel_20260307.log
    """
    # Nombre jerárquico — binding explícito previene PC-B
    if activo:
        logger_name = f"SPEL.{activo}.{nombre_modulo}"
    else:
        logger_name = f"SPEL.{nombre_modulo}"

    logger = logging.getLogger(logger_name)

    # Evitar duplicar handlers si se llama dos veces (común en Colab)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # ── Formato ──────────────────────────────────────────────────────────────
    fmt = logging.Formatter(
        fmt='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%S'
    )
    # FIX-2: Añadir converter UTC para que asctime use UTC, no local time
    import time as _time
    fmt.converter = _time.gmtime  # fuerza UTC en todos los handlers

    # ── Handler 1: Consola — INFO y superior ─────────────────────────────────
    consola = logging.StreamHandler(sys.stdout)
    consola.setLevel(logging.INFO)
    consola.setFormatter(fmt)
    logger.addHandler(consola)

    # ── Handler 2: Archivo — todo DEBUG ──────────────────────────────────────
    try:
        # FIX-3: Ruta absoluta — logs persisten en Drive entre reinicios
        _drive_root = Path('/content/drive/MyDrive/SPEL-v1.1')
        logs_dir = (_drive_root / 'logs') if _drive_root.exists() else Path('logs')
        logs_dir.mkdir(exist_ok=True)
        fecha_hoy = datetime.now(timezone.utc).strftime('%Y%m%d')
        log_path = logs_dir / f'spel_{fecha_hoy}.log'

        archivo = logging.FileHandler(log_path, encoding='utf-8')
        archivo.setLevel(logging.DEBUG)
        archivo.setFormatter(fmt)
        logger.addHandler(archivo)
    except Exception as e:
        # Si no puede escribir el archivo (ej: Drive no montado todavía),
        # sigue funcionando con solo la consola — nunca falla silenciosamente
        logger.warning(f"No se pudo crear archivo de log: {e} — usando solo consola")

    # FIX-4: Garantizar flush de todos los handlers al salir
    # Cubre crash, KeyboardInterrupt y exit normal
    atexit.register(logging.shutdown)

    # Evitar que el logger propague al root logger de Python (evita duplicados)
    atexit.register(logging.shutdown)  # FIX-4: flush on exit/crash
    logger.propagate = False

    return logger


# ── Loggers pre-construidos para los módulos principales ─────────────────────
# Importar directamente en cada módulo:
#   from spel_logger import logger_guardian, logger_orchestrator
#
# O crear uno propio con activo:
#   from spel_logger import get_logger
#   logger = get_logger('capa_c', activo=self._activo_cargado)

def get_logger_guardian() -> logging.Logger:
    """Logger del Guardian — sin activo porque es global."""
    return get_logger('guardian')

def get_logger_orchestrator() -> logging.Logger:
    """Logger del Orchestrator — sin activo porque maneja todos."""
    return get_logger('orchestrator')

def get_logger_capa_c(activo: str) -> logging.Logger:
    """Logger de capa_c_inference — siempre con activo para detectar PC-B."""
    return get_logger('capa_c', activo=activo)

def get_logger_score() -> logging.Logger:
    """Logger del Score de Oro."""
    return get_logger('score_de_oro')


# ── Test de instalación ───────────────────────────────────────────────────────
if __name__ == '__main__':
    print("=== Test spel_logger.py ===\n")

    # Test 1: logger con activo
    log_nvda = get_logger('capa_c', activo='NVDA')
    log_nvda.info("Logger NVDA inicializado correctamente")
    log_nvda.debug("Este mensaje solo aparece en el archivo de log")
    log_nvda.warning("⚠️ Ejemplo de warning — val_dir sospechoso")

    # Test 2: logger sin activo
    log_guardian = get_logger('guardian')
    log_guardian.info("Guardian logger OK")

    # Test 3: llamar dos veces el mismo — no debe duplicar
    log_nvda_2 = get_logger('capa_c', activo='NVDA')
    log_nvda_2.info("Segunda llamada — no debe aparecer duplicado arriba")

    # Test 4: diferentes activos son loggers distintos
    log_btc = get_logger('capa_c', activo='BTC')
    log_btc.info("Logger BTC — nombre distinto a NVDA, confirma binding")

    print("\n✅ Si ves 4 mensajes INFO sin duplicados, spel_logger funciona correctamente")
    print("📁 Revisa la carpeta logs/ — debe existir spel_YYYYMMDD.log con mensajes DEBUG")
