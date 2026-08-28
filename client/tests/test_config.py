import os

import pytest

from wdc_client.config import ConfigError, Settings


def test_uri_appends_the_device_id():
    s = Settings(device_id="device-01", server_url="ws://relay:8000/device")
    assert s.uri == "ws://relay:8000/device/device-01"


def test_uri_tolerates_a_trailing_slash():
    s = Settings(device_id="device-01", server_url="ws://relay:8000/device/")
    assert s.uri == "ws://relay:8000/device/device-01"


def test_allowed_roots_are_expanded_and_resolved(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    s = Settings(allowed_roots="~, /var/tmp ,")
    roots = s.allowed_root_list
    assert roots[0] == os.path.realpath(str(tmp_path))
    assert roots[1] == os.path.realpath("/var/tmp")


def test_false_is_read_as_false(monkeypatch):
    """The old client tested truthiness, and the string "false" is truthy."""
    monkeypatch.setenv("WS_INSECURE_TLS", "false")
    assert Settings.from_env(use_dotenv=False).ws_insecure_tls is False
    monkeypatch.setenv("WS_INSECURE_TLS", "yes")
    assert Settings.from_env(use_dotenv=False).ws_insecure_tls is True


def test_a_nonsense_boolean_is_rejected(monkeypatch):
    monkeypatch.setenv("WS_INSECURE_TLS", "maybe")
    with pytest.raises(ConfigError):
        Settings.from_env(use_dotenv=False)


def test_a_nonsense_number_is_rejected(monkeypatch):
    monkeypatch.setenv("MAX_SESSIONS", "many")
    with pytest.raises(ConfigError):
        Settings.from_env(use_dotenv=False)


def test_out_of_range_numbers_are_rejected(monkeypatch):
    monkeypatch.setenv("MAX_SESSIONS", "0")
    with pytest.raises(ConfigError):
        Settings.from_env(use_dotenv=False)


def test_shell_prefers_the_configured_command():
    assert Settings(shell_command="/usr/bin/fish").shell == "/usr/bin/fish"


def test_shell_falls_back_to_something_that_exists(monkeypatch):
    """Images without bash must still get a usable shell, not a failed exec."""
    monkeypatch.setenv("SHELL", "/bin/nonexistent-shell")
    assert os.path.exists(Settings().shell)


def test_motd_is_skipped_when_disabled():
    assert Settings(shell_motd=False).motd_command == ""
