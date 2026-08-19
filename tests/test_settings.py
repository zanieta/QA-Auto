"""Unit tests for agent/settings.py — SettingsStore."""

from __future__ import annotations

from agent.settings import SettingsStore


def test_defaults_are_empty(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    assert store.get("target_url") == ""
    assert store.get("login_username") == ""
    assert store.get("login_password") == ""
    assert store.credentials_dict() == {"login_username": "", "has_password": False}


def test_set_credentials_round_trip_persists_across_fresh_store(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.set_credentials("qa@duke", "s3cret")

    fresh = SettingsStore(path)
    assert fresh.get("login_username") == "qa@duke"
    assert fresh.get("login_password") == "s3cret"
    assert fresh.credentials_dict() == {"login_username": "qa@duke", "has_password": True}


def test_set_credentials_username_only_keeps_stored_password(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.set_credentials("qa@duke", "s3cret")

    # Fixing a typo'd username with an empty password must not clear the secret.
    store.set_credentials("qa2@duke", "")
    assert store.get("login_username") == "qa2@duke"
    assert store.get("login_password") == "s3cret"
    assert store.credentials_dict() == {"login_username": "qa2@duke", "has_password": True}


def test_set_credentials_both_empty_clears(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.set_credentials("qa@duke", "s3cret")

    store.set_credentials("", "")
    assert store.get("login_username") == ""
    assert store.get("login_password") == ""
    assert store.credentials_dict() == {"login_username": "", "has_password": False}


def test_settings_json_persists_password_in_plaintext_on_disk(tmp_path):
    """settings.json carries the same trust level as .env / manual_sessions/ —
    the password IS written to disk in plaintext there. Never in an HTTP
    response or log line (covered in test_server.py), but the file itself
    must actually hold it or a restart would lose the setting."""
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.set_credentials("qa@duke", "s3cret")
    assert "s3cret" in path.read_text(encoding="utf-8")
