# =============================================================================
# SPEL 4.0 — secrets_env_loader.py
# Cargador soberano de secretos desde variables de entorno
# NUNCA importar credenciales directas. SIEMPRE usar este módulo.
#
# CONSOLIDACIÓN (auditoría de ingesta): este archivo NO reimplementa detección
# de entorno ni carga de Colab userdata — delega enteramente en
# spel_commons.load_secrets(), que es la fuente única real (detecta GH Actions
# vs. Colab vs. local, R37/Ley2 compliant). Antes existían 4 implementaciones
# distintas de "cargar un secreto" en el proyecto; esta es ahora un wrapper
# fino sobre una de ellas, no una quinta versión paralela.
#
# La firma pública de load_secret(key, fallback) se mantiene IDÉNTICA a la
# original para no romper a nada que ya la importe.
# =============================================================================
from __future__ import annotations
import os


def load_secret(key: str, fallback: str | None = None) -> str:
    """
    Carga un secreto usando spel_commons.load_secrets() como fuente única.
    Prioridad (definida en spel_commons, no aquí):
        1. Variable de entorno (GH Actions) / Colab Secrets (userdata)
        2. fallback si se proporciona
    Lanza RuntimeError si el secreto no existe y no hay fallback.

    Nota de implementación: spel_commons.load_secrets() es un cargador batch
    (carga `required` + `_OPTIONAL_KEYS` de una vez, con efecto colateral de
    poblar os.environ). Pedir una sola key aquí carga también las opcionales
    de paso — barato y sin efectos secundarios dañinos, pero vale saberlo si
    se llama a load_secret() en un loop ajustado en latencia.
    """
    try:
        from spel_commons import load_secrets as _load_secrets_batch
    except ImportError as exc:
        raise RuntimeError(
            "[SPEL4 SECURITY] No se pudo importar spel_commons — "
            "secrets_env_loader.py depende de él como fuente única. "
            f"Verificar que esté en el mismo directorio / sys.path. ({exc})"
        ) from exc

    secrets = _load_secrets_batch(required=[key])
    val = secrets.get(key, "") or os.environ.get(key, "")

    if not val and fallback is not None:
        return fallback

    if not val:
        raise RuntimeError(
            f"[SPEL4 SECURITY] Secret '{key}' ausente. "
            f"Configura en Colab Secrets (panel ☰ izq.) o en GitHub Actions Secrets."
        )
    return val


# ── Constantes de acceso rápido (no almacenan el valor, solo el key) ─────────
class SecretKey:
    TG_BOT_TOKEN          = "TG_BOT_TOKEN"
    TG_CHANNEL_CHAOS      = "TG_CHANNEL_CHAOS"
    TG_CHANNEL_MAIN       = "TG_CHANNEL_MAIN"
    TIINGO_API_KEY        = "TIINGO_API_KEY"
    ALPHAVANTAGE_API_KEY  = "ALPHAVANTAGE_API_KEY"
    NEWSDATA_API_KEY      = "NEWSDATA_API_KEY"
    GITHUB_TOKEN          = "GITHUB_TOKEN"
    GITHUB_REPO_OWNER     = "GITHUB_REPO_OWNER"
    GITHUB_REPO_NAME      = "GITHUB_REPO_NAME"
    DERIV_API_TOKEN       = "DERIV_API_TOKEN"   # Paso 1 — nuevo
    DERIV_APP_ID          = "DERIV_APP_ID"      # Paso 1 — nuevo


if __name__ == "__main__":
    # Smoke test — no revela valores, solo confirma presencia/ausencia.
    print("=== secrets_env_loader — smoke test ===")
    for name, key in vars(SecretKey).items():
        if name.startswith("_"):
            continue
        try:
            load_secret(key)
            print(f"  ✅ {key}")
        except RuntimeError:
            print(f"  ⚪ {key} (ausente — normal si no está configurado aún)")
