"""tests/test_secrets.py"""

from __future__ import annotations

import pytest

from governance.secrets import SecretError, SecretKey, load_secret, secrets_status_report


def test_carga_desde_variable_de_entorno(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "valor_real")
    assert load_secret(SecretKey.TELEGRAM_TOKEN) == "valor_real"


def test_requerido_y_ausente_lanza_secret_error(monkeypatch):
    monkeypatch.delenv("KEY_QUE_NO_EXISTE_XYZ", raising=False)
    with pytest.raises(SecretError):
        load_secret("KEY_QUE_NO_EXISTE_XYZ", required=True)


def test_opcional_y_ausente_devuelve_none(monkeypatch):
    monkeypatch.delenv("KEY_QUE_NO_EXISTE_XYZ", raising=False)
    assert load_secret("KEY_QUE_NO_EXISTE_XYZ", required=False) is None


def test_secrets_status_report_nunca_expone_valores(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "secreto-no-deberia-aparecer-en-el-reporte")
    monkeypatch.delenv("DERIV_API_TOKEN", raising=False)

    reporte = secrets_status_report([SecretKey.TELEGRAM_TOKEN, SecretKey.DERIV_API_TOKEN])

    assert reporte == {SecretKey.TELEGRAM_TOKEN: True, SecretKey.DERIV_API_TOKEN: False}
    assert "secreto-no-deberia-aparecer-en-el-reporte" not in str(reporte)


def test_all_keys_incluye_las_claves_nuevas_de_deriv():
    claves = SecretKey.all_keys()
    assert SecretKey.DERIV_API_TOKEN in claves
    assert SecretKey.DERIV_APP_ID in claves


def test_no_hay_alias_caos_solo_chaos():
    """Decisión de diseño explícita: un solo nombre canónico, no dos."""
    claves = SecretKey.all_keys()
    assert "TELEGRAM_CHAOS" in claves
    assert "TELEGRAM_CAOS" not in claves
