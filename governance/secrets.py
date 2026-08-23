"""
governance/secrets.py
========================
Fuente única de credenciales para el repo nuevo. No importa nada de
archive/* — el spel_commons.py legado tenía 2 versiones incompatibles
entre sí (encontrado y fusionado la sesión pasada); acá no se arrastra
esa historia, se define UNA vez, limpio.

Prioridad de carga: variable de entorno (GH Actions) -> Colab userdata ->
ausente. Sin el truco de ofuscar el import de google.colab que tenía el
legado ("burlar el escáner EF-COLAB") — ese patrón quedó específicamente
señalado como algo a no repetir.

DECISIÓN TOMADA ACÁ, no arrastrada: el legado tenía TELEGRAM_CHAOS y
TELEGRAM_CAOS conviviendo sin resolver (8 archivos vs 3, encontrado por
grep). Repo nuevo, código nuevo, cero call sites legados que respetar ->
se define UN nombre canónico (TELEGRAM_CHAOS) y no se carga el alias.
Si algo en Colab/GH Secrets todavía usa TELEGRAM_CAOS, hay que renombrar
la variable de entorno, no el código.
"""

from __future__ import annotations

import os
from typing import Optional


class SecretError(Exception):
    """Un secreto requerido no está disponible en ningún tier de carga."""


class SecretKey:
    """Un solo lugar con los nombres de variable — si se necesita un
    secreto nuevo, se agrega acá, no se hardcodea el string en otro
    archivo.

    CONVENCIÓN DE NOMBRES, decisión explícita: el sufijo espeja cómo el
    proveedor llama a su propia credencial en su documentación oficial.
    TwelveData y AlphaVantage la llaman `apikey` -> `_API_KEY`; Tiingo y
    Deriv la llaman `token` -> `_API_TOKEN`; Alpaca emite un par ->
    `_API_KEY` + `_SECRET_KEY`. El motivo es práctico: buscar el nombre
    de la variable en la doc del proveedor tiene que dar resultado.
    Uniformar a la fuerza (todo a `_API_KEY`, por ejemplo) rompería esa
    correspondencia y haría más difícil auditar cada adapter contra su
    documentación real.

    Las 3 claves de proveedores de OHLCV se registran ANTES de que exista
    su adapter, a propósito: este registro es la lista de secretos que el
    proyecto reconoce, no la de los que ya se usan. `secrets_status_report()`
    los muestra ausentes hasta que se configuren, que es exactamente la
    visibilidad que hacía falta.
    """
    TELEGRAM_TOKEN       = "TELEGRAM_TOKEN"
    TELEGRAM_SISTEMA     = "TELEGRAM_SISTEMA"
    TELEGRAM_SENALES     = "TELEGRAM_SENALES"
    TELEGRAM_BACKUP      = "TELEGRAM_BACKUP"
    TELEGRAM_CHAOS       = "TELEGRAM_CHAOS"
    DERIV_API_TOKEN      = "DERIV_API_TOKEN"
    DERIV_APP_ID         = "DERIV_APP_ID"
    ALPACA_API_KEY       = "ALPACA_API_KEY"
    ALPACA_SECRET_KEY    = "ALPACA_SECRET_KEY"
    GITHUB_TOKEN         = "GITHUB_TOKEN"
    TWELVEDATA_API_KEY   = "TWELVEDATA_API_KEY"
    ALPHAVANTAGE_API_KEY = "ALPHAVANTAGE_API_KEY"
    TIINGO_API_TOKEN     = "TIINGO_API_TOKEN"

    @classmethod
    def all_keys(cls) -> list[str]:
        return [v for k, v in vars(cls).items() if not k.startswith("_") and isinstance(v, str)]


def _try_colab_userdata(key: str) -> Optional[str]:
    """Sin ofuscación de import -- si algún escáner necesita bloquear
    Colab en cierto contexto, que sea una regla explícita del CI, no un
    truco de string concatenado en el código de producción."""
    try:
        from google.colab import userdata  # type: ignore
    except ImportError:
        return None
    try:
        return userdata.get(key) or None
    except Exception:
        return None


def load_secret(key: str, *, required: bool = True) -> Optional[str]:
    """
    Carga UN secreto. Prioridad: os.environ -> Colab userdata -> None.

    required=True (default): lanza SecretError si no se encuentra.
    required=False: devuelve None silenciosamente -- para secretos
    genuinamente opcionales (ej. un canal de Telegram secundario).
    """
    val = os.environ.get(key)
    if val:
        return val

    val = _try_colab_userdata(key)
    if val:
        return val

    if required:
        raise SecretError(
            f"'{key}' no encontrado en variables de entorno ni en Colab "
            f"userdata. Configurar en GitHub Actions Secrets o en el "
            f"panel de Colab Secrets (ícono de llave, izquierda)."
        )
    return None


def secrets_status_report(keys: Optional[list[str]] = None) -> dict[str, bool]:
    """
    Visibilidad real del estado de configuración -- NUNCA expone valores,
    solo presencia/ausencia. Esto es lo que resuelve 'necesito más orden
    de los secretos': una función que responde '¿qué tengo configurado
    ahora mismo?' sin tener que grepear código ni arriesgar imprimir un
    token por accidente.
    """
    keys = keys if keys is not None else SecretKey.all_keys()
    return {k: load_secret(k, required=False) is not None for k in keys}


if __name__ == "__main__":
    print("=== Estado de secretos (presencia, nunca valores) ===")
    for key, present in secrets_status_report().items():
        marca = "✅" if present else "⚪"
        print(f"  {marca} {key}")
