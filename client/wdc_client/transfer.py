"""File transfers in both directions.

Receiving (browser -> device) is a stream of binary frames bracketed by
`file_begin` / `file_end` / `file_checksum`. Sending (device -> browser) is the
mirror image, driven by a cancellable task. Both are bounded now: the
destination has to resolve inside an allowed root, and an upload that outgrows
`max_file_size` is aborted instead of being written until the device's disk is
full.
"""

import asyncio
import contextlib
import hashlib
import logging
import os

from . import protocol
from .paths import PathNotAllowed, resolve_download_source, resolve_upload_destination

logger = logging.getLogger(__name__)


class FileReceiver:
    """Tracks one in-progress upload (browser -> device)."""

    def __init__(self, allowed_roots, max_file_size):
        self._allowed_roots = allowed_roots
        self._max_file_size = max_file_size
        self.file_name = None
        self.path = None
        self._fp = None
        self._hasher = None
        self._written = 0
        self._failure = None  # (code, message), reported when the stream ends

    # ---- lifecycle -------------------------------------------------------

    def begin(self, filename, target_path, declared_size=None):
        """Open the destination. Returns an error payload, or None on success.

        A payload comes back rather than being sent from here so the transport
        stays in one place: this class never touches the socket.
        """
        self._reset(remove_partial=True)
        self.file_name = filename or "received.bin"

        if declared_size is not None and self._exceeds_limit(declared_size):
            # Refused before a single byte is written, so an oversized file
            # costs the device nothing but the handshake.
            return self._fail(protocol.ERR_TOO_LARGE, self._size_message(declared_size))

        try:
            dest = resolve_upload_destination(filename, target_path, self._allowed_roots)
        except PathNotAllowed as exc:
            logger.warning("[file] Refused upload destination: %s", exc)
            return self._fail(protocol.ERR_PATH_NOT_ALLOWED)

        try:
            parent = os.path.dirname(dest)
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._fp = open(dest, "wb")
        except OSError as exc:
            logger.error("[file] Cannot open %s: %s", dest, exc)
            return self._fail(protocol.ERR_WRITE_ERROR, f"cannot open destination: {exc.strerror or exc}")

        self.path = dest
        self._hasher = hashlib.sha256()
        self._written = 0
        logger.info("[file] Receiving %s to %s", self.file_name, dest)
        return None

    def write_chunk(self, chunk):
        """Append a chunk. Returns an error payload the first time it fails."""
        if self._fp is None:
            return None  # nothing open: either failed already, or a stray frame

        if self._exceeds_limit(self._written + len(chunk)):
            logger.warning("[file] %s exceeded the %s byte limit; aborting",
                           self.file_name, self._max_file_size)
            return self._fail(protocol.ERR_TOO_LARGE, self._size_message(self._written + len(chunk)))

        try:
            self._fp.write(chunk)
        except OSError as exc:
            logger.error("[file] Write error on %s: %s", self.file_name, exc)
            return self._fail(protocol.ERR_WRITE_ERROR, f"write failed: {exc.strerror or exc}")

        self._written += len(chunk)
        if self._hasher is not None:
            self._hasher.update(chunk)
        return None

    def end(self):
        """Flush the stream. The checksum message decides success."""
        if self._fp is None:
            return
        try:
            self._fp.flush()
            os.fsync(self._fp.fileno())
        except OSError as exc:
            logger.error("[file] Flush error on %s: %s", self.file_name, exc)
            self._fail(protocol.ERR_WRITE_ERROR, f"flush failed: {exc.strerror or exc}")
            return
        finally:
            with contextlib.suppress(OSError):
                self._fp.close()
            self._fp = None
        logger.info("[file] Completed stream: %s (%s bytes)", self.file_name, self._written)

    def verify_checksum(self, expected_sha256):
        """Compare against the relay's checksum and close the transfer out.

        Always returns the payload to send back, success or failure, and leaves
        the receiver ready for the next upload.
        """
        name = self.file_name
        failure, hasher = self._failure, self._hasher
        self._reset(remove_partial=failure is not None)

        if failure is not None:
            code, message = failure
            return protocol.error(protocol.MSG_FILE_PUT_ERROR, code, message, filename=name)
        if hasher is None:
            return protocol.error(
                protocol.MSG_FILE_PUT_ERROR, protocol.ERR_CHECKSUM_MISSING, filename=name
            )
        if expected_sha256 is None or hasher.hexdigest() != expected_sha256:
            return protocol.error(
                protocol.MSG_FILE_PUT_ERROR, protocol.ERR_CHECKSUM_MISMATCH, filename=name
            )
        return {"type": protocol.MSG_FILE_PUT_OK, "filename": name}

    def cancel(self):
        """Drop the partial file and report the cancellation."""
        name = self.file_name
        self._reset(remove_partial=True)
        return protocol.error(protocol.MSG_FILE_PUT_ERROR, protocol.ERR_CANCELED, filename=name)

    def close(self):
        """Release the stream without deleting anything (used on disconnect)."""
        if self._fp is not None:
            with contextlib.suppress(OSError):
                self._fp.close()
            self._fp = None

    # ---- internals -------------------------------------------------------

    def _exceeds_limit(self, size):
        return self._max_file_size > 0 and size > self._max_file_size

    def _size_message(self, size):
        return f"file is {size} bytes, over the {self._max_file_size} byte limit"

    def _fail(self, code, message=None):
        """Record the failure, stop writing, and build the payload to send."""
        self._failure = (code, message)
        name = self.file_name
        if self._fp is not None:
            with contextlib.suppress(OSError):
                self._fp.close()
            self._fp = None
        self._hasher = None
        return protocol.error(protocol.MSG_FILE_PUT_ERROR, code, message, filename=name)

    def _reset(self, remove_partial=False):
        """Clear all state, optionally deleting the half-written file.

        A partial file left behind looks exactly like a complete one to whoever
        goes looking on the device, which is worse than no file at all.
        """
        if self._fp is not None:
            with contextlib.suppress(OSError):
                self._fp.close()
            self._fp = None
        if remove_partial and self.path is not None and os.path.exists(self.path):
            try:
                os.remove(self.path)
            except OSError as exc:
                logger.error("[file] Could not remove partial %s: %s", self.path, exc)
        self.file_name = None
        self.path = None
        self._hasher = None
        self._written = 0
        self._failure = None


class FileSender:
    """Streams a file off the device (device -> browser)."""

    def __init__(self, allowed_roots, chunk_size, send_json, send_bytes):
        self._allowed_roots = allowed_roots
        self._chunk_size = chunk_size
        self._send_json = send_json
        self._send_bytes = send_bytes
        self._task = None

    def start(self, path):
        """Validate the request and kick off a cancellable send."""
        if not path:
            return protocol.error(protocol.MSG_FILE_PULL_ERROR, protocol.ERR_NO_PATH)
        try:
            resolved = resolve_download_source(path, self._allowed_roots)
        except PathNotAllowed as exc:
            logger.warning("[file] Refused download source: %s", exc)
            return protocol.error(protocol.MSG_FILE_PULL_ERROR, protocol.ERR_PATH_NOT_ALLOWED)
        if not os.path.exists(resolved):
            return protocol.error(protocol.MSG_FILE_PULL_ERROR, protocol.ERR_NOT_FOUND)
        if not os.path.isfile(resolved):
            return protocol.error(protocol.MSG_FILE_PULL_ERROR, protocol.ERR_NOT_A_FILE)

        self.cancel()
        self._task = asyncio.create_task(self._run(resolved))
        return None

    def cancel(self):
        # The reference is kept until the task actually finishes, so it is not
        # garbage collected mid-cancellation.
        task = self._task
        if task is not None and not task.done():
            task.cancel()

    def close(self):
        self.cancel()
        self._task = None

    async def _run(self, path):
        try:
            try:
                size = os.path.getsize(path)
            except OSError:
                size = None

            await self._send_json({
                "type": protocol.MSG_FILE_PULL_INFO,
                "filename": os.path.basename(path),
                "size": size,
            })

            hasher = hashlib.sha256()
            sent = 0
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(self._chunk_size)
                    if not chunk:
                        break
                    hasher.update(chunk)
                    # `send` applies backpressure: on a slow link it waits for
                    # the write buffer to drain instead of queueing the whole
                    # file in the device's memory.
                    await self._send_bytes(chunk)
                    sent += len(chunk)

            await self._send_json({
                "type": protocol.MSG_FILE_PULL_CHECKSUM,
                "sha256": hasher.hexdigest(),
            })
            await self._send_json({"type": protocol.MSG_FILE_PULL_END})
            logger.info("[file] Sent %s (%s bytes)", os.path.basename(path), sent)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await self._send_json(
                    protocol.error(protocol.MSG_FILE_PULL_ERROR, protocol.ERR_CANCELED)
                )
            raise
        except OSError as exc:
            logger.error("[file] Read error on %s: %s", path, exc)
            with contextlib.suppress(Exception):
                await self._send_json(
                    protocol.error(
                        protocol.MSG_FILE_PULL_ERROR,
                        protocol.ERR_READ_ERROR,
                        f"{os.path.basename(path)}: {exc.strerror or exc}",
                    )
                )
        except Exception as exc:  # never let one transfer kill the connection
            logger.exception("[file] Unexpected error sending %s", path)
            with contextlib.suppress(Exception):
                await self._send_json(
                    protocol.error(protocol.MSG_FILE_PULL_ERROR, protocol.ERR_INTERNAL, str(exc))
                )
