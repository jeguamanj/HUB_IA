# =============================================================================
# tests/test_config.py — A0.1 `boot-settings`
# -----------------------------------------------------------------------------
# Valida la centralización de configuración: singleton `settings`, read-typed
# desde entorno, validación de credenciales obligatorias y retro-compatibilidad
# de los identificadores de módulo preexistentes (ZOOM_CONFIG, etc.).
# =============================================================================

import dataclasses
import importlib

import pytest

from API import config as config_module
from API.config import (
    Settings,
    get_settings,
    validate_artemis,
    validate_credentials,
    validate_zoom,
)


class TestSettingsLoading:
    def test_singleton_shape(self):
        """get_settings() devuelve una Settings completa y reutilizable."""
        cfg = get_settings()
        assert isinstance(cfg, Settings)
        # Defaults tipados correctos.
        assert cfg.port == 10000
        assert cfg.llm_soft_timeout == 3.5
        assert cfg.llm_hard_timeout == 12.0
        assert cfg.llm_max_concurrent == 2
        assert cfg.openrouter_models_cache_ttl == 1800
        assert cfg.ollama_endpoint == "http://localhost:11434"

    def test_typed_read_from_env(self, monkeypatch):
        """Los valores numéricos y booleanos se leen tipados del entorno."""
        monkeypatch.setenv("PORT", "8888")
        monkeypatch.setenv("LLM_SOFT_TIMEOUT", "2.5")
        monkeypatch.setenv("LLM_MAX_CONCURRENT", "4")
        monkeypatch.setenv("ARTEMIS_ENABLED", "true")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

        reloaded = config_module.reload_settings()
        assert reloaded.port == 8888
        assert reloaded.llm_soft_timeout == 2.5
        assert reloaded.llm_max_concurrent == 4
        assert reloaded.artemis_enabled is True
        assert reloaded.openrouter_api_key == "sk-test"

    def test_relaxed_env_fallbacks(self, monkeypatch):
        """Valores inválidos caen al default sin romper el boot."""
        monkeypatch.setenv("PORT", "no-un-numero")
        monkeypatch.setenv("LLM_SOFT_TIMEOUT", "abc")
        reloaded = config_module.reload_settings()
        assert reloaded.port == 10000
        assert reloaded.llm_soft_timeout == 3.5


class TestCredentialValidation:
    def test_missing_reported_non_strict(self):
        """Sin credenciales, `validate_credentials` reporta la clave del LLM."""
        empty = Settings()  # todos los campos vacíos
        missing = validate_credentials(empty, strict=False)
        assert len(missing) == 1
        assert "OPENROUTER_API_KEY" in missing[0]

    def test_missing_reported_strict(self):
        """En modo estricto reporta todas las credenciales definidas."""
        empty = Settings()
        missing = validate_credentials(empty, strict=True)
        assert len(missing) >= 1
        joined = " ".join(missing)
        for token in ("OPENROUTER_API_KEY", "ZOOM_ACCOUNT_ID", "CANVAS_API_URL"):
            assert token in joined

    def test_ok_when_complete(self):
        """Con credenciales presentes no se reportan faltantes."""
        ok = dataclasses.replace(
            Settings(),
            openrouter_api_key="k",
            zoom_account_id="a",
            zoom_client_id="c",
            zoom_client_secret="s",
        )
        assert validate_credentials(ok, strict=False) == []
        assert validate_zoom(ok) == []

    def test_validate_zoom_reports_missing(self):
        """validate_zoom lista cada componente de credencial faltante."""
        empty = Settings()
        zoom_missing = validate_zoom(empty)
        assert any("ZOOM_ACCOUNT_ID" in m for m in zoom_missing)
        assert any("ZOOM_CLIENT_SECRET" in m for m in zoom_missing)

    def test_artemis_only_when_enabled(self):
        """Artemis solo se valida si está habilitado."""
        off = Settings(artemis_enabled=False)
        assert validate_artemis(off) == []
        on_broken = Settings(artemis_enabled=True, artemis_host="")
        assert any("ARTEMIS_HOST" in m for m in validate_artemis(on_broken))


class TestBackwardCompatAliases:
    def test_module_aliases_present(self):
        """Los identificadores preexistentes siguen exportados."""
        import API.config as cfg

        assert hasattr(cfg, "ACTIVE_SERVICES")
        assert hasattr(cfg, "ARTEMIS_CONFIG")
        assert hasattr(cfg, "LLM_PROVIDERS")
        assert hasattr(cfg, "ZOOM_CONFIG")
        assert cfg.ZOOM_CONFIG["base_url"] == "https://api.zoom.us/v2"
        assert cfg.ACTIVE_SERVICES  # no vacío

    def test_zoom_license_imports(self):
        """`zoom_license_management` sigue importándose con el alias de config."""
        importlib.import_module("API.services.zoom_license_management")
        import API.config as cfg