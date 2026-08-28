"""Upload and download behaviour, including the limits the device enforces."""

import asyncio
import hashlib
import os

import pytest

from wdc_client import protocol
from wdc_client.transfer import FileReceiver, FileSender


@pytest.fixture
def receiver(roots, sandbox, monkeypatch):
    # On a real device the home dir sits inside the allowed roots; the sandbox
    # stands in for it so the default destination resolves.
    monkeypatch.setenv("HOME", str(sandbox))
    return FileReceiver(roots, max_file_size=1024)


def _upload(receiver, name, payload, target=None, declared_size=None):
    """Drive a full upload and return the final payload the device would send."""
    err = receiver.begin(name, target, declared_size)
    if err is not None:
        return err
    for i in range(0, len(payload), 16):
        err = receiver.write_chunk(payload[i:i + 16])
        if err is not None:
            break
    receiver.end()
    return receiver.verify_checksum(hashlib.sha256(payload).hexdigest())


def test_a_good_upload_lands_and_is_acknowledged(receiver, sandbox):
    payload = b"hello device" * 10
    result = _upload(receiver, "a.txt", payload)
    assert result["type"] == protocol.MSG_FILE_PUT_OK
    assert (sandbox / "a.txt").read_bytes() == payload


def test_a_wrong_checksum_is_reported(receiver, sandbox):
    receiver.begin("a.txt", None)
    receiver.write_chunk(b"data")
    receiver.end()
    result = receiver.verify_checksum("0" * 64)
    assert result["error_code"] == protocol.ERR_CHECKSUM_MISMATCH


def test_a_missing_checksum_is_reported(receiver):
    receiver.begin("a.txt", None)
    receiver.write_chunk(b"data")
    receiver.end()
    assert receiver.verify_checksum(None)["error_code"] == protocol.ERR_CHECKSUM_MISMATCH


def test_a_destination_outside_the_roots_is_refused(receiver):
    result = receiver.begin("evil", "/etc/cron.d/evil")
    assert result["error_code"] == protocol.ERR_PATH_NOT_ALLOWED
    assert not os.path.exists("/etc/cron.d/evil")


def test_an_oversized_declared_file_is_refused_before_any_write(receiver, sandbox):
    result = receiver.begin("big.bin", None, declared_size=99999)
    assert result["error_code"] == protocol.ERR_TOO_LARGE
    assert not (sandbox / "big.bin").exists()


def test_a_file_that_outgrows_the_limit_mid_stream_is_aborted(receiver, sandbox):
    """A lying `size` must not let the stream fill the device's disk."""
    result = _upload(receiver, "big.bin", b"x" * 4096, declared_size=10)
    assert result["error_code"] == protocol.ERR_TOO_LARGE
    assert not (sandbox / "big.bin").exists()


def test_chunks_after_a_failure_are_ignored(receiver):
    receiver.begin("big.bin", None, declared_size=99999)
    assert receiver.write_chunk(b"more") is None


def test_cancel_removes_the_partial_file(receiver, sandbox):
    receiver.begin("a.txt", None)
    receiver.write_chunk(b"half")
    result = receiver.cancel()
    assert result["error_code"] == protocol.ERR_CANCELED
    assert not (sandbox / "a.txt").exists()


def test_a_new_upload_discards_an_interrupted_one(receiver, sandbox):
    receiver.begin("first.txt", None)
    receiver.write_chunk(b"partial")
    _upload(receiver, "second.txt", b"complete")
    assert not (sandbox / "first.txt").exists()
    assert (sandbox / "second.txt").read_bytes() == b"complete"


def test_the_receiver_is_reusable_after_a_failure(receiver, sandbox):
    receiver.begin("evil", "/etc/cron.d/evil")
    receiver.verify_checksum(None)
    assert _upload(receiver, "ok.txt", b"fine")["type"] == protocol.MSG_FILE_PUT_OK


class FakeSocket:
    """Collects what the device would put on the wire."""

    def __init__(self):
        self.json = []
        self.chunks = []

    async def send_json(self, payload):
        self.json.append(payload)

    async def send_bytes(self, chunk):
        # Yields to the loop like a real socket under backpressure, so a
        # transfer can actually be cancelled part-way through.
        await asyncio.sleep(0)
        self.chunks.append(chunk)

    def types(self):
        return [m["type"] for m in self.json]


@pytest.fixture
def wire():
    return FakeSocket()


def _sender(roots, wire):
    return FileSender(roots, 16, wire.send_json, wire.send_bytes)


async def test_sending_a_file_streams_info_chunks_and_checksum(roots, wire, sandbox):
    payload = b"device logs\n" * 20
    (sandbox / "log.txt").write_bytes(payload)

    sender = _sender(roots, wire)
    assert sender.start(str(sandbox / "log.txt")) is None
    await sender._task

    assert wire.types() == [
        protocol.MSG_FILE_PULL_INFO,
        protocol.MSG_FILE_PULL_CHECKSUM,
        protocol.MSG_FILE_PULL_END,
    ]
    assert b"".join(wire.chunks) == payload
    assert wire.json[1]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert wire.json[0]["size"] == len(payload)


async def test_reading_outside_the_roots_is_refused(roots, wire):
    result = _sender(roots, wire).start("/etc/shadow")
    assert result["error_code"] == protocol.ERR_PATH_NOT_ALLOWED


async def test_a_missing_file_is_reported(roots, wire, sandbox):
    result = _sender(roots, wire).start(str(sandbox / "nope.txt"))
    assert result["error_code"] == protocol.ERR_NOT_FOUND


async def test_a_directory_is_not_a_file(roots, wire, sandbox):
    result = _sender(roots, wire).start(str(sandbox))
    assert result["error_code"] == protocol.ERR_NOT_A_FILE


async def test_an_empty_path_is_reported(roots, wire):
    assert _sender(roots, wire).start(None)["error_code"] == protocol.ERR_NO_PATH


async def test_cancelling_reports_the_cancellation(roots, wire, sandbox):
    (sandbox / "big.bin").write_bytes(b"x" * 100000)
    sender = _sender(roots, wire)
    sender.start(str(sandbox / "big.bin"))
    await asyncio.sleep(0)
    sender.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sender._task
    assert wire.json[-1]["error_code"] == protocol.ERR_CANCELED
