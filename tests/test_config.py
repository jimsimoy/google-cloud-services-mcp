"""load_config and get_credentials — no real service-account file or network."""

import json

import pytest

import google_cloud_services as gcs


def test_load_config_reads_an_existing_absolute_path(tmp_path):
    config_file = tmp_path / "test-config.json"
    config_file.write_text(json.dumps({"service_account_key": "key.json", "scopes": ["a"]}))
    result = gcs.load_config(str(config_file))
    assert result == {"service_account_key": "key.json", "scopes": ["a"]}


def test_load_config_exits_with_a_helpful_message_when_file_is_missing(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    with pytest.raises(SystemExit) as excinfo:
        gcs.load_config(str(missing))
    assert "Config not found" in str(excinfo.value)
    assert "config.example.json" in str(excinfo.value)


def test_get_credentials_passes_key_path_and_scopes_through(monkeypatch, tmp_path):
    seen = {}

    def fake_from_service_account_file(key_path, scopes):
        seen["key_path"] = key_path
        seen["scopes"] = scopes
        return "fake-credentials-object"

    monkeypatch.setattr(
        gcs.Credentials, "from_service_account_file", staticmethod(fake_from_service_account_file)
    )

    config = {
        "service_account_key": str(tmp_path / "key.json"),
        "scopes": ["https://www.googleapis.com/auth/documents"],
    }
    result = gcs.get_credentials(config)

    assert result == "fake-credentials-object"
    assert seen["key_path"] == config["service_account_key"]
    assert seen["scopes"] == config["scopes"]
