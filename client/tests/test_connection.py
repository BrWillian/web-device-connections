"""Handshake, dispatch and reconnection policy."""

import json

import pytest

from wdc_client import protocol
from wdc_client.connection import AuthRejected, Backoff, DeviceClient, build_ssl_context, device_metadata


class FakeWS:
    """Minimal stand-in for a relay socket."""

    def __init__(self, replies=()):
        self.sent = []
        self._replies = list(replies)

    async def send(self, message):
        self.sent.append(message)

    async def recv(self):
        return self._replies.pop(0)

    def payloads(self):
        return [json.loads(m) for m in self.sent if isinstance(m, str)]


# ---- Backoff -------------------------------------------------------------


def test_backoff_grows_and_is_capped():
    b = Backoff(1, 10, jitter=0, rng=lambda: 0.5)
    assert [b.next_delay() for _ in range(6)] == [1, 2, 4, 8, 10, 10]


def test_backoff_resets_after_a_good_connection():
    b = Backoff(1, 10, jitter=0, rng=lambda: 0.5)
    b.next_delay()
    b.next_delay()
    b.reset()
    assert b.next_delay() == 1


def test_backoff_jitter_stays_around_the_delay():
    """A whole fleet must not retry on the same beat after an outage."""
    low = Backoff(10, 60, jitter=0.3, rng=lambda: 0.0).next_delay()
    high = Backoff(10, 60, jitter=0.3, rng=lambda: 1.0).next_delay()
    assert low == pytest.approx(7)
    assert high == pytest.approx(13)


def test_the_auth_floor_overrides_a_short_delay():
    """A refused token does not get better in five seconds."""
    b = Backoff(5, 60, jitter=0, rng=lambda: 0.5)
    assert b.next_delay(floor=300) == 300


def test_the_auth_floor_never_shortens_a_longer_delay():
    b = Backoff(600, 900, jitter=0, rng=lambda: 0.5)
    assert b.next_delay(floor=300) == 600


# ---- TLS -----------------------------------------------------------------


def test_no_context_unless_explicitly_insecure():
    assert build_ssl_context("wss://relay/device/x", False) is None


def test_insecure_flag_is_ignored_on_a_plaintext_url():
    """websockets rejects a context on ws://, so a mismatch must not be fatal."""
    assert build_ssl_context("ws://relay/device/x", True) is None


def test_insecure_context_relaxes_verification():
    import ssl

    ctx = build_ssl_context("wss://relay/device/x", True)
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_NONE


# ---- Handshake -----------------------------------------------------------


def test_metadata_reports_the_client_version(settings):
    from wdc_client import __version__

    meta = device_metadata(settings)
    assert meta["client_version"] == __version__
    assert meta["hostname"] and meta["system"]


async def test_a_successful_handshake_sends_the_token_and_metadata(settings):
    ws = FakeWS([json.dumps({"type": protocol.MSG_AUTH_OK, "pod": "relay-0"})])
    await DeviceClient(settings)._authenticate(ws)
    hello = ws.payloads()[0]
    assert hello["token"] == "token"
    assert hello["meta"]["client_version"]


async def test_a_refused_token_raises(settings):
    ws = FakeWS([json.dumps({"type": protocol.MSG_AUTH_ERROR, "error": "token inválido"})])
    with pytest.raises(AuthRejected):
        await DeviceClient(settings)._authenticate(ws)


async def test_a_garbage_handshake_reply_raises(settings):
    with pytest.raises(AuthRejected):
        await DeviceClient(settings)._authenticate(FakeWS(["not json"]))


# ---- Dispatch ------------------------------------------------------------


@pytest.fixture
def client(settings, sandbox, monkeypatch):
    monkeypatch.setenv("HOME", str(sandbox))
    return DeviceClient(settings)


async def test_non_json_text_is_ignored(client):
    await client._handle_text(FakeWS(), "hello")


async def test_an_unknown_message_type_is_ignored(client):
    await client._handle_text(FakeWS(), json.dumps({"type": "who_knows"}))


async def test_revocation_mid_session_raises(client):
    """The relay revokes connected devices too, not only at handshake time."""
    with pytest.raises(AuthRejected):
        await client._handle_text(FakeWS(), json.dumps({"type": protocol.MSG_AUTH_ERROR}))


async def test_start_session_without_an_id_does_not_kill_the_connection(client):
    """`data["session_id"]` used to raise straight through the serve loop."""
    await client._handle_text(FakeWS(), json.dumps({"type": protocol.MSG_START_SESSION}))


async def test_a_failing_handler_does_not_kill_the_connection(client, monkeypatch):
    async def boom(ws, data):
        raise RuntimeError("handler exploded")

    monkeypatch.setattr(client, "_on_input", boom)
    await client._handle_text(FakeWS(), json.dumps({"type": protocol.MSG_INPUT}))


async def test_a_refused_upload_destination_is_reported_to_the_relay(client):
    ws = FakeWS()
    await client._handle_text(ws, json.dumps({
        "type": protocol.MSG_FILE_BEGIN,
        "filename": "evil",
        "target_path": "/etc/cron.d/evil",
    }))
    assert ws.payloads()[0]["error_code"] == protocol.ERR_PATH_NOT_ALLOWED


async def test_a_good_upload_round_trip_is_acknowledged(client, sandbox):
    import hashlib

    ws = FakeWS()
    payload = b"telemetry"
    await client._handle_text(ws, json.dumps({"type": protocol.MSG_FILE_BEGIN, "filename": "t.bin"}))
    client.receiver.write_chunk(payload)
    await client._handle_text(ws, json.dumps({"type": protocol.MSG_FILE_END}))
    await client._handle_text(ws, json.dumps({
        "type": protocol.MSG_FILE_CHECKSUM,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }))
    assert ws.payloads()[-1]["type"] == protocol.MSG_FILE_PUT_OK
    assert (sandbox / "t.bin").read_bytes() == payload
