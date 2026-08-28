import pytest

from app.core.presence import MemoryPresence
from app.core.security import (
    DeviceAuthUnavailable,
    mint_device_token,
    origin_allowed,
    verify_device_token,
    verify_relay_secret,
)

SECRET = "test-master-secret"


def test_token_is_stable_for_a_device():
    assert mint_device_token("device-01", SECRET) == mint_device_token("device-01", SECRET)


def test_each_device_gets_a_different_token():
    """A token leaked from one device must not unlock another."""
    assert mint_device_token("device-01", SECRET) != mint_device_token("device-02", SECRET)


def test_rotating_the_master_secret_invalidates_tokens():
    assert mint_device_token("device-01", SECRET) != mint_device_token("device-01", "other")


def test_minting_without_a_secret_is_refused():
    with pytest.raises(DeviceAuthUnavailable):
        mint_device_token("device-01", "")


def test_verify_rejects_missing_token(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.device_master_secret", SECRET)
    assert verify_device_token("device-01", None) is False
    assert verify_device_token("device-01", "") is False
    assert verify_device_token("device-01", "wrong") is False
    assert verify_device_token("device-01", mint_device_token("device-01", SECRET)) is True


def test_verify_fails_closed_when_no_secret_is_configured(monkeypatch):
    """With no master secret the server must reject everyone, not accept everyone."""
    monkeypatch.setattr("app.core.config.settings.device_master_secret", "")
    assert verify_device_token("device-01", "anything") is False


@pytest.mark.asyncio
async def test_a_grant_id_burns_once():
    store = MemoryPresence()
    assert await store.burn_grant("jti-1", ttl=30) is True
    assert await store.burn_grant("jti-1", ttl=30) is False


@pytest.mark.asyncio
async def test_spent_ids_are_forgotten_after_their_window(monkeypatch):
    """Otherwise the set of spent ids would grow without bound."""
    store = MemoryPresence()
    assert await store.burn_grant("jti-1", ttl=30) is True

    import app.core.presence as presence_module

    real_monotonic = presence_module.time.monotonic
    monkeypatch.setattr(presence_module.time, "monotonic", lambda: real_monotonic() + 3600)
    assert await store.burn_grant("jti-2", ttl=30) is True
    assert store._spent_grants.keys() == {"jti-2"}


def test_relay_secret_comparison(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.relay_secret", "s3gr3d0")
    assert verify_relay_secret("s3gr3d0") is True
    assert verify_relay_secret("outro") is False
    assert verify_relay_secret(None) is False

    # Fails closed when unconfigured, rather than accepting an empty header.
    monkeypatch.setattr("app.core.config.settings.relay_secret", "")
    assert verify_relay_secret("") is False


def test_origin_check(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.allowed_origins", "")
    assert origin_allowed(None) is True          # unset: no check
    assert origin_allowed("https://evil.test") is True

    monkeypatch.setattr("app.core.config.settings.allowed_origins", "https://ok.test")
    assert origin_allowed("https://ok.test") is True
    assert origin_allowed("https://evil.test") is False
    assert origin_allowed(None) is False         # a browser always sends Origin
