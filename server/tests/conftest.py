"""Shared fixtures.

The relay has no login, so tests mint grants the way the frontend would: sign a
JWT with the shared relay secret. That keeps the tests honest about the trust
boundary instead of reaching into internals.
"""

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt

RELAY_SECRET = "test-relay-secret"
DEVICE_SECRET = "test-device-secret"


def make_grant(
    device_id: str,
    scope: str,
    *,
    secret: str = RELAY_SECRET,
    username: str = "admin",
    expires_in: int = 30,
    jti: str = None,
) -> str:
    """Sign a grant exactly as the frontend does."""
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": username,
            "dev": device_id,
            "scp": scope,
            "jti": jti or secrets.token_urlsafe(16),
            "iat": now,
            "exp": now + timedelta(seconds=expires_in),
        },
        secret,
        algorithm="HS256",
    )


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.relay_secret", RELAY_SECRET)
    monkeypatch.setattr("app.core.config.settings.device_master_secret", DEVICE_SECRET)
    monkeypatch.setattr("app.core.config.settings.allowed_origins", "")
    # Pinned so the suite does not depend on the developer's .env. With a
    # REDIS_URL set there, these tests would try to reach a real Redis — passing
    # or failing based on whether one happens to be running locally.
    monkeypatch.setattr("app.core.config.settings.redis_url", "")
    return True


@pytest.fixture
def client(configured):
    from main import app

    # The lifespan wires up the presence backend; TestClient as a context manager
    # is what triggers it.
    with TestClient(app) as c:
        yield c
