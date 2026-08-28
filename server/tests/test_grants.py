"""The relay must only open a session for a grant the frontend actually signed."""

from tests.conftest import make_grant

DEVICE = "device-01"


def error_of(ws):
    return ws.receive_json().get("error", "")


def test_rejects_missing_grant(client):
    """The critical regression: an unauthenticated socket must never reach a device."""
    with client.websocket_connect(f"/terminal/{DEVICE}") as ws:
        assert "autorização" in error_of(ws)


def test_rejects_garbage_grant(client):
    with client.websocket_connect(f"/terminal/{DEVICE}?grant=nao-e-um-jwt") as ws:
        assert "autorização" in error_of(ws)


def test_rejects_grant_signed_with_the_wrong_secret(client):
    """A forged grant is worthless without the shared secret."""
    forged = make_grant(DEVICE, "terminal", secret="segredo-errado")
    with client.websocket_connect(f"/terminal/{DEVICE}?grant={forged}") as ws:
        assert "autorização" in error_of(ws)


def test_rejects_expired_grant(client):
    stale = make_grant(DEVICE, "terminal", expires_in=-3600)
    with client.websocket_connect(f"/terminal/{DEVICE}?grant={stale}") as ws:
        assert "autorização" in error_of(ws)


def test_grant_is_bound_to_its_device(client):
    """A grant for device-01 must not open a session on device-02."""
    grant = make_grant("device-01", "terminal")
    with client.websocket_connect(f"/terminal/device-02?grant={grant}") as ws:
        assert "autorização" in error_of(ws)


def test_grant_is_bound_to_its_scope(client):
    """An upload grant must not be redeemable for a shell."""
    grant = make_grant(DEVICE, "upload")
    with client.websocket_connect(f"/terminal/{DEVICE}?grant={grant}") as ws:
        assert "autorização" in error_of(ws)


def test_grant_is_single_use(client):
    """Reaching the 'device not connected' message means the grant was accepted."""
    grant = make_grant(DEVICE, "terminal")

    with client.websocket_connect(f"/terminal/{DEVICE}?grant={grant}") as first:
        assert error_of(first) == "Dispositivo não conectado"

    with client.websocket_connect(f"/terminal/{DEVICE}?grant={grant}") as second:
        assert "autorização" in error_of(second)


def test_upload_and_download_check_their_own_scopes(client):
    with client.websocket_connect(
        f"/file/{DEVICE}?grant={make_grant(DEVICE, 'download')}"
    ) as ws:
        assert "autorização" in error_of(ws)

    with client.websocket_connect(
        f"/download/{DEVICE}?grant={make_grant(DEVICE, 'upload')}"
    ) as ws:
        assert "autorização" in error_of(ws)


def test_origin_is_checked_when_configured(client, monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.allowed_origins", "https://painel.exemplo.com"
    )
    grant = make_grant(DEVICE, "terminal")

    with client.websocket_connect(
        f"/terminal/{DEVICE}?grant={grant}", headers={"Origin": "https://evil.test"}
    ) as ws:
        assert "origem" in error_of(ws)


def test_allowed_origin_passes(client, monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.allowed_origins", "https://painel.exemplo.com"
    )
    grant = make_grant(DEVICE, "terminal")

    with client.websocket_connect(
        f"/terminal/{DEVICE}?grant={grant}",
        headers={"Origin": "https://painel.exemplo.com"},
    ) as ws:
        assert error_of(ws) == "Dispositivo não conectado"


def test_relay_fails_closed_without_a_secret(client, monkeypatch):
    """With no RELAY_SECRET the relay must reject everyone, not accept everyone."""
    grant = make_grant(DEVICE, "terminal")
    monkeypatch.setattr("app.core.config.settings.relay_secret", "")

    with client.websocket_connect(f"/terminal/{DEVICE}?grant={grant}") as ws:
        assert "autorização" in error_of(ws)
