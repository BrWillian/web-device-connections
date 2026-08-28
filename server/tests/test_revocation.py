"""Device revocation, and the endpoint that hands the frontend a device token.

Revocation is published by the frontend into shared state; the relay only reads it.
These tests drive the read side by swapping in a presence backend that reports a
given device as revoked, which is what Redis would be doing in a real deployment.
"""

import json

import pytest

from app.core.presence import MemoryPresence
from tests.conftest import DEVICE_SECRET, RELAY_SECRET
from app.core.security import mint_device_token


class RevokingPresence(MemoryPresence):
    """MemoryPresence that reports one device id as revoked."""

    def __init__(self, revoked_id):
        super().__init__()
        self._revoked_id = revoked_id

    async def is_revoked(self, device_id):
        return device_id == self._revoked_id


@pytest.fixture
def revoke(monkeypatch):
    """Mark a device as revoked for the duration of a test."""
    def _revoke(device_id):
        import app.core.presence as presence_module
        monkeypatch.setattr(presence_module, "presence", RevokingPresence(device_id))
    return _revoke


def test_revoked_device_is_refused(client, revoke):
    revoke("robo-banido")

    with client.websocket_connect("/device/robo-banido") as ws:
        ws.send_text(json.dumps({
            "type": "auth", "token": mint_device_token("robo-banido", DEVICE_SECRET)
        }))
        reply = json.loads(ws.receive_text())

    assert reply["type"] == "auth_error"
    assert "revogado" in reply["error"]


def test_revocation_does_not_affect_other_devices(client, revoke):
    revoke("robo-banido")

    with client.websocket_connect("/device/robo-ok") as ws:
        ws.send_text(json.dumps({
            "type": "auth", "token": mint_device_token("robo-ok", DEVICE_SECRET)
        }))
        reply = json.loads(ws.receive_text())

    assert reply["type"] == "auth_ok"


def test_revocation_is_checked_after_the_token(client, revoke):
    """A bad token must look the same whether or not the device is revoked.

    Otherwise the error message tells an unauthenticated caller which device ids
    exist and have been revoked.
    """
    revoke("robo-banido")

    with client.websocket_connect("/device/robo-banido") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": "token-errado"}))
        reply = json.loads(ws.receive_text())

    assert reply["error"] == "token inválido"


def test_memory_presence_never_revokes():
    """Single-process fallback has nothing writing revocations into it."""
    import asyncio
    assert asyncio.run(MemoryPresence().is_revoked("qualquer-um")) is False


# --------------------------------------------------------------------------
# Device token endpoint
# --------------------------------------------------------------------------

def test_device_token_requires_the_relay_secret(client):
    assert client.get("/devices/device-01/token").status_code == 401
    assert client.get(
        "/devices/device-01/token", headers={"X-Relay-Secret": "errado"}
    ).status_code == 401


def test_device_token_matches_what_the_device_must_present(client):
    response = client.get(
        "/devices/device-01/token", headers={"X-Relay-Secret": RELAY_SECRET}
    )
    assert response.status_code == 200

    body = response.json()
    assert body["device_id"] == "device-01"
    # The whole point: what the endpoint returns is what authenticates the device.
    assert body["token"] == mint_device_token("device-01", DEVICE_SECRET)


def test_device_token_fails_closed_without_the_master_secret(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.device_master_secret", "")
    response = client.get(
        "/devices/device-01/token", headers={"X-Relay-Secret": RELAY_SECRET}
    )
    assert response.status_code == 503
