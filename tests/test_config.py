"""Tester för Config-klassen (Uppgift 7)."""

import importlib
import os

import pytest


def _ladda_config(monkeypatch, dfs2_env, secret_key):
    """Laddar om config-modulen med givna env-värden och returnerar Config."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("DFS2_ENV", raising=False)
    if secret_key is not None:
        monkeypatch.setenv("SECRET_KEY", secret_key)
    monkeypatch.setenv("DFS2_ENV", dfs2_env)

    import config as config_mod

    # DATABASE_URL sätts av tests/conftest.py, så _bygg_database_url behöver
    # inget lösenord och _las_hemlighet för SECRET_KEY anropas bara här.
    importlib.reload(config_mod)
    return config_mod.Config


class TestConfig:
    def test_config_kraschar_utan_secret_key_i_production(self, monkeypatch):
        with pytest.raises(RuntimeError):
            _ladda_config(monkeypatch, "production", None)

    def test_config_tillater_fallback_i_test(self, monkeypatch):
        cfg = _ladda_config(monkeypatch, "test", None)
        # Fallback-nyckeln är 32 slumpmässiga bytes = 64 hex-tecken.
        assert len(cfg.SECRET_KEY) == 64

    def test_config_anvander_satt_secret_key(self, monkeypatch):
        cfg = _ladda_config(monkeypatch, "production", "mitt-super-hemliga-test")
        assert cfg.SECRET_KEY == "mitt-super-hemliga-test"

    def test_default_env_ar_production(self, monkeypatch):
        # Utan DFS2_ENV ska production användas → kräver SECRET_KEY.
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.delenv("DFS2_ENV", raising=False)
        import config as config_mod

        with pytest.raises(RuntimeError):
            importlib.reload(config_mod)