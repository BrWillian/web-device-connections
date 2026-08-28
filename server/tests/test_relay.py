"""End-to-end: device socket through to a browser terminal session."""

from app.core.security import mint_device_token
from tests.conftest import DEVICE_SECRET, RELAY_SECRET, make_grant

DEVICE = "device-e2e"


def connect_device(client):
    """Open an authenticated device socket, returning the context manager."""
    return client.websocket_connect(f"/device/{DEVICE}")


def test_device_must_authenticate(client):
    with connect_device(client) as ws:
        ws.send_json({"type": "auth", "token": "nao-e-o-token"})
        assert ws.receive_json()["type"] == "auth_error"

    assert client.get("/devices", headers={"X-Relay-Secret": RELAY_SECRET}).json() == []


def test_device_appears_in_the_fleet_list(client):
    with connect_device(client) as ws:
        ws.send_json({"type": "auth", "token": mint_device_token(DEVICE, DEVICE_SECRET)})
        assert ws.receive_json()["type"] == "auth_ok"

        listed = client.get("/devices", headers={"X-Relay-Secret": RELAY_SECRET}).json()
        assert [d["id"] for d in listed] == [DEVICE]
        assert listed[0]["pod"]  # presence records which replica owns it

    # Disconnecting removes it again
    assert client.get("/devices", headers={"X-Relay-Secret": RELAY_SECRET}).json() == []


def test_devices_list_requires_the_relay_secret(client):
    """It is an internal call from the frontend, not a public endpoint."""
    assert client.get("/devices").status_code == 401
    assert client.get("/devices", headers={"X-Relay-Secret": "errado"}).status_code == 401


def test_terminal_relays_in_both_directions(client):
    with connect_device(client) as device_ws:
        device_ws.send_json({"type": "auth", "token": mint_device_token(DEVICE, DEVICE_SECRET)})
        assert device_ws.receive_json()["type"] == "auth_ok"

        grant = make_grant(DEVICE, "terminal")
        with client.websocket_connect(f"/terminal/{DEVICE}?grant={grant}") as term_ws:
            term_ws.send_json({"type": "start_session", "session_id": "s1"})
            assert device_ws.receive_json() == {"type": "start_session", "session_id": "s1"}

            term_ws.send_json({"type": "input", "session_id": "s1", "data": "ls\n"})
            assert device_ws.receive_json()["data"] == "ls\n"

            # ...and the device's output finds its way back to that session
            device_ws.send_json({"type": "output", "session_id": "s1", "data": "arquivo.txt\n"})
            assert term_ws.receive_json()["data"] == "arquivo.txt\n"


def test_legacy_device_route_is_closed_by_default(client):
    with client.websocket_connect("/device") as ws:
        assert ws.receive_json()["type"] == "auth_error"


def test_health_reports_replica_readiness(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "presence_backend" in body
    assert "multi_replica_ready" in body
