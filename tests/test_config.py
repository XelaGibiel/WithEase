"""Basic tests for the configuration system."""
import json
import pytest
from pathlib import Path


def test_default_profile_has_all_modules():
    from accessmate.core.config import DEFAULT_PROFILE
    assert "mouse" in DEFAULT_PROFILE["modules"]
    assert "keyboard" in DEFAULT_PROFILE["modules"]
    assert "macros" in DEFAULT_PROFILE["modules"]


def test_default_profile_modules_disabled():
    from accessmate.core.config import DEFAULT_PROFILE
    for module_settings in DEFAULT_PROFILE["modules"].values():
        assert module_settings.get("enabled") is False


def test_save_and_load_profile(tmp_path, monkeypatch):
    import accessmate.core.config as cfg
    monkeypatch.setattr(cfg, "PROFILES_DIR", tmp_path)
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)

    profile = {"name": "Test", "modules": {}, "actions": {}, "emergency_key": "F12"}
    cfg.save_profile("test", profile)
    loaded = cfg.load_profile("test")
    assert loaded["name"] == "Test"
