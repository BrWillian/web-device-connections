"""The relay connection: handshake, dispatch loop and reconnection policy."""

import asyncio
import json
import logging
import os
import platform
import random
import socket
import ssl

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from . import protocol
from .shell import SessionLimitReached, SessionManager, make_json_sender
from .transfer import FileReceiver, FileSender
from . import __version__

logger = logging.getLogger(__name__)

PING_INTERVAL = 60  # keepalive
PING_TIMEOUT = 60   # tolerated silence before the connection is considered dead
CLOSE_TIMEOUT = 10


class AuthRejected(Exception):
    """The relay refused this device's token, or revoked it mid-session.

    Distinct from a network error on purpose: retrying in five seconds cannot
    fix a revoked token, it only pounds the relay that just turned the device
    away.
    """


class Backoff:
    """Exponential backoff with jitter.

    A fleet that loses the relay reconnects together. With the old fixed delay
    every device retried on the same 5 second beat, so the relay came back up
    into a synchronised stampede; the jitter spreads them out.
    """

    def __init__(self, base, maximum, jitter=0.3, rng=random.random):
        self.base = max(base, 0.1)
        self.maximum = max(maximum, self.base)
        self.jitter = jitter
        self._rng = rng
        self._attempt = 0

    def reset(self):
        self._attempt = 0

    def next_delay(self, floor=None):
        """Delay before the next attempt, growing with consecutive failures.

        `floor` raises the minimum for this one call — that is how a rejected
        handshake gets its much longer wait without disturbing the sequence.
        """
        delay = min(self.base * (2 ** self._attempt), self.maximum)
        self._attempt += 1
        if floor is not None:
            delay = max(delay, floor)
        spread = delay * self.jitter
        return max(0.1, delay - spread + self._rng() * 2 * spread)


def build_ssl_context(uri, insecure):
    """TLS context for a self-signed relay certificate, or None.

    Passing a context to a ws:// URI is an error the websockets library rejects
    outright, so a mismatched config is warned about and ignored rather than
    left to fail on every reconnect.
    """
    if not insecure:
        return None
    if not uri.startswith("wss://"):
        logger.warning(
            "WS_INSECURE_TLS is on but SERVER_URL is not wss:// — ignoring it, "
            "since there is no TLS to relax on a plaintext connection."
        )
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def device_metadata(settings):
    """What this device reports about itself at handshake time.

    Without it the dashboard knows a device is online but not what it is running,
    which is the first thing anyone asks when a fleet needs an update.
    """
    uname = platform.uname()
    return {
        "client_version": __version__,
        "hostname": socket.gethostname(),
        "system": f"{uname.system} {uname.release}",
        "machine": uname.machine,
        "python": platform.python_version(),
        "shell": settings.shell,
        "pid": os.getpid(),
    }


class DeviceClient:
    """WebSocket agent that runs on a remote device."""

    def __init__(self, settings):
        self.settings = settings
        self.uri = settings.uri
        self.device_id = settings.device_id
        self.ssl_context = build_ssl_context(self.uri, settings.ws_insecure_tls)
        self.backoff = Backoff(settings.reconnect_interval, settings.reconnect_max_interval)
        self.sessions = None
        self.receiver = FileReceiver(settings.allowed_root_list, settings.max_file_size)
        self.sender = None

    # ---- connection lifecycle -------------------------------------------

    async def run(self):
        logger.info("Starting device client %s (version %s)", self.device_id, __version__)
        logger.info("Connecting to relay: %s", self.uri)
        logger.info("Allowed transfer roots: %s", ", ".join(self.settings.allowed_root_list) or "(none)")

        while True:
            auth_floor = None
            try:
                async with connect(
                    self.uri,
                    ping_interval=PING_INTERVAL,
                    ping_timeout=PING_TIMEOUT,
                    close_timeout=CLOSE_TIMEOUT,
                    max_size=max(1 << 20, self.settings.chunk_size * 4),
                    ssl=self.ssl_context,
                ) as ws:
                    await self._authenticate(ws)
                    logger.info("Device %s connected successfully", self.device_id)
                    self.backoff.reset()
                    await self._serve(ws)
            except asyncio.CancelledError:
                self._cleanup()
                raise
            except AuthRejected as exc:
                # Falls through to the sleep below rather than continuing: the
                # whole point is that a refused device waits a long time.
                logger.error("Authentication rejected: %s", exc)
                auth_floor = self.settings.reconnect_auth_interval
            except ConnectionClosed as exc:
                logger.warning("Connection closed: %s", exc)
            except OSError as exc:
                logger.warning("Cannot reach the relay: %s", exc)
            except Exception as exc:
                logger.error("Connection error: %s", exc)
            finally:
                self._cleanup()

            delay = self.backoff.next_delay(floor=auth_floor)
            logger.info("Reconnecting in %.1f seconds...", delay)
            await asyncio.sleep(delay)

    async def _authenticate(self, ws):
        """Prove identity before the relay will forward anything.

        The token is HMAC(master_secret, device_id), minted server-side with
        `python -m tools.mint_device_token <device_id>`.
        """
        await ws.send(json.dumps({
            "type": "auth",
            "token": self.settings.device_token,
            "device_id": self.device_id,
            "meta": device_metadata(self.settings),
        }))

        try:
            reply = json.loads(await ws.recv())
        except (json.JSONDecodeError, TypeError, ValueError):
            raise AuthRejected("unexpected handshake reply from the relay")

        if reply.get("type") == protocol.MSG_AUTH_OK:
            logger.info("Authenticated against pod %s", reply.get("pod", "?"))
            return

        raise AuthRejected(reply.get("error", "unknown reason"))

    # ---- message dispatch ------------------------------------------------

    async def _serve(self, ws):
        send_json = make_json_sender(ws)
        self.sessions = SessionManager(
            send_json,
            shell=self.settings.shell,
            motd_command=self.settings.motd_command,
            max_sessions=self.settings.max_sessions,
        )
        self.sender = FileSender(
            self.settings.allowed_root_list,
            self.settings.chunk_size,
            send_json,
            ws.send,
        )

        while True:
            message = await ws.recv()
            if isinstance(message, (bytes, bytearray)):
                # A file chunk in flight (browser -> device).
                await self._reply(ws, self.receiver.write_chunk(bytes(message)))
                continue
            await self._handle_text(ws, message)

    async def _handle_text(self, ws, message):
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return  # non-JSON text frames are not ours

        msg_type = data.get("type")
        if msg_type == protocol.MSG_AUTH_ERROR:
            # The relay revokes devices mid-session too, not only at connect.
            raise AuthRejected(data.get("error", "revoked while connected"))

        handler = self._handlers().get(msg_type)
        if handler is None:
            return

        try:
            await handler(ws, data)
        except AuthRejected:
            raise
        except asyncio.CancelledError:
            raise
        except ConnectionClosed:
            raise
        except Exception:
            # One malformed message used to take the whole connection down with
            # it — a missing "session_id" key was enough — and every live
            # session with it. Handlers fail alone now.
            logger.exception("Error handling %s message", msg_type)

    def _handlers(self):
        return {
            protocol.MSG_START_SESSION: self._on_start_session,
            protocol.MSG_INPUT: self._on_input,
            protocol.MSG_RESIZE: self._on_resize,
            protocol.MSG_FILE_BEGIN: self._on_file_begin,
            protocol.MSG_FILE_END: self._on_file_end,
            protocol.MSG_FILE_CANCEL: self._on_file_cancel,
            protocol.MSG_FILE_CHECKSUM: self._on_file_checksum,
            protocol.MSG_FILE_PULL_BEGIN: self._on_file_pull_begin,
            protocol.MSG_FILE_PULL_CANCEL: self._on_file_pull_cancel,
        }

    async def _on_start_session(self, ws, data):
        session_id = data.get("session_id")
        if not session_id:
            logger.warning("start_session without a session_id; ignored")
            return
        try:
            await self.sessions.start(session_id)
        except SessionLimitReached as exc:
            logger.warning("Refused session %s: %s", session_id, exc)
            # Reported as terminal output because that is the only channel the
            # relay forwards to a terminal socket; the operator sees why the
            # shell never appeared instead of an empty black rectangle.
            await self._reply(ws, {
                "type": protocol.MSG_OUTPUT,
                "session_id": session_id,
                "error_code": protocol.ERR_SESSION_LIMIT,
                "data": "\r\n[device] too many terminal sessions open; close one and try again.\r\n",
            })

    async def _on_input(self, ws, data):
        session = self.sessions.get(data.get("session_id"))
        if session is not None:
            session.send_input(data.get("data", ""))

    async def _on_resize(self, ws, data):
        session = self.sessions.get(data.get("session_id"))
        if session is not None:
            session.resize_terminal(int(data.get("cols", 80)), int(data.get("rows", 24)))

    async def _on_file_begin(self, ws, data):
        await self._reply(ws, self.receiver.begin(
            data.get("filename", "received.bin"),
            data.get("target_path"),
            data.get("size"),
        ))

    async def _on_file_end(self, ws, data):
        self.receiver.end()

    async def _on_file_cancel(self, ws, data):
        await self._reply(ws, self.receiver.cancel())

    async def _on_file_checksum(self, ws, data):
        payload = self.receiver.verify_checksum(data.get("sha256"))
        if payload.get("type") == protocol.MSG_FILE_PUT_OK:
            logger.info("[file] Upload OK: %s", payload.get("filename"))
        else:
            logger.error("[file] Upload failed for %s: %s",
                         payload.get("filename"), payload.get("error_code"))
        await self._reply(ws, payload)

    async def _on_file_pull_begin(self, ws, data):
        await self._reply(ws, self.sender.start(data.get("path")))

    async def _on_file_pull_cancel(self, ws, data):
        self.sender.cancel()

    # ---- helpers ---------------------------------------------------------

    async def _reply(self, ws, payload):
        """Send a payload if there is one; a None means nothing to report."""
        if payload is None:
            return
        try:
            await ws.send(json.dumps(payload))
        except Exception as exc:
            logger.debug("Send failed: %s", exc)

    def _cleanup(self):
        """Tear down every session and transfer when the connection drops."""
        if self.sessions is not None:
            self.sessions.stop_all()
            self.sessions = None
        if self.sender is not None:
            self.sender.close()
            self.sender = None
        self.receiver.close()
