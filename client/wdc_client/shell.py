"""PTY terminal sessions.

Each session is a forked shell wired to a pseudo-terminal; the manager owns the
set of live sessions, caps how many may exist and makes sure every child is
reaped. The reaping matters more than it looks: an agent that runs for months
on a device turns every unwaited child into a permanent zombie entry.
"""

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import pty
import signal
import struct
import termios

from . import protocol

logger = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024
# How long a shell gets to leave on SIGTERM before it is killed outright.
TERM_GRACE_SECONDS = 3.0
REAP_POLL_SECONDS = 0.1


class SessionLimitReached(Exception):
    """Raised when a device already has as many sessions as it may hold."""


class ShellSession:
    """A PTY shell session attached to one relay connection."""

    def __init__(self, session_id, send, shell="/bin/sh", motd_command="", on_exit=None):
        self.session_id = session_id
        self._send = send
        self._shell = shell
        self._motd_command = motd_command
        self._on_exit = on_exit
        self.pid = None
        self.master_fd = None
        self._loop = None
        self._exited = False

    def _child_argv(self):
        """Command line for the forked shell.

        The banner is prepended only when the distro actually has one; the old
        unconditional `run-parts /etc/update-motd.d` made the shell start with a
        "command not found" on every image that is not Debian-shaped.
        """
        name = os.path.basename(self._shell)
        if self._motd_command:
            return [self._shell, "-i", "-c", f"{self._motd_command}; exec {name} -i"]
        return [self._shell, "-i"]

    async def start(self):
        self._loop = asyncio.get_running_loop()
        argv = self._child_argv()
        self.pid, self.master_fd = pty.fork()
        if self.pid == 0:
            # Child process: interactive shell in the user home dir. Nothing
            # here may raise back into the parent's event loop, so a failed exec
            # ends the child rather than returning into forked async state.
            try:
                os.chdir(os.path.expanduser("~"))
                os.execvp(argv[0], argv)
            finally:
                os._exit(1)

        # Non-blocking fd so the reader callback never blocks the event loop.
        flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
        fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self._loop.add_reader(self.master_fd, self._on_readable)
        logger.debug("Shell session %s started (pid=%s, shell=%s)", self.session_id, self.pid, self._shell)

    def _on_readable(self):
        try:
            data = os.read(self.master_fd, CHUNK_SIZE)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            # EOF: the shell exited on its own. Tearing the session down here is
            # what keeps the manager's dict — and the process table — from
            # growing for the lifetime of the agent.
            logger.info("Shell session %s ended", self.session_id)
            self.stop()
            return
        asyncio.ensure_future(self._send_output(data))

    async def _send_output(self, data):
        try:
            await self._send({
                "type": protocol.MSG_OUTPUT,
                "session_id": self.session_id,
                "data": data.decode(errors="ignore"),
            })
        except Exception as exc:
            logger.debug("Failed to send output for session %s: %s", self.session_id, exc)
            self.stop()

    def send_input(self, data):
        if self.master_fd is None:
            return
        try:
            os.write(self.master_fd, data.encode())
        except OSError as exc:
            logger.debug("Failed to write input to session %s: %s", self.session_id, exc)

    def resize_terminal(self, cols, rows):
        if self.master_fd is None:
            return
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
        except OSError as exc:
            logger.debug("Failed to resize session %s: %s", self.session_id, exc)

    def _remove_reader(self):
        if self._loop is not None and self.master_fd is not None:
            with contextlib.suppress(ValueError, OSError):
                self._loop.remove_reader(self.master_fd)

    def stop(self):
        """Detach the fd, signal the shell and arrange for it to be reaped.

        Safe to call more than once and from a reader callback, which is why the
        waiting half runs as a task instead of blocking here.
        """
        if self._exited:
            return
        self._exited = True

        self._remove_reader()
        if self.master_fd is not None:
            with contextlib.suppress(OSError):
                os.close(self.master_fd)
            self.master_fd = None

        pid, self.pid = self.pid, None
        if pid is not None:
            if self._loop is not None and not self._loop.is_closed():
                self._loop.create_task(self._terminate(pid))
            else:
                _terminate_blocking(pid)

        if self._on_exit is not None:
            with contextlib.suppress(Exception):
                self._on_exit(self.session_id)

    async def _terminate(self, pid):
        """SIGTERM, wait, then SIGKILL — and always waitpid.

        The old code sent SIGTERM and immediately called waitpid(WNOHANG), which
        almost never collects a process that has not scheduled yet: the child
        stayed a zombie and a shell that ignored SIGTERM stayed alive.
        """
        with contextlib.suppress(ProcessLookupError, OSError):
            os.kill(pid, signal.SIGTERM)

        deadline = TERM_GRACE_SECONDS
        while deadline > 0:
            if _reap(pid):
                return
            await asyncio.sleep(REAP_POLL_SECONDS)
            deadline -= REAP_POLL_SECONDS

        logger.warning("Shell session %s did not exit on SIGTERM; killing pid %s", self.session_id, pid)
        with contextlib.suppress(ProcessLookupError, OSError):
            os.kill(pid, signal.SIGKILL)
        for _ in range(int(TERM_GRACE_SECONDS / REAP_POLL_SECONDS)):
            if _reap(pid):
                return
            await asyncio.sleep(REAP_POLL_SECONDS)


def _reap(pid):
    """Collect `pid` if it has exited. True when it is gone for good."""
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return True  # already reaped, e.g. by a SIGCHLD handler
    except OSError:
        return True
    return done != 0


def _terminate_blocking(pid):
    """Last-resort teardown with no event loop left to schedule on."""
    with contextlib.suppress(ProcessLookupError, OSError):
        os.kill(pid, signal.SIGKILL)
    with contextlib.suppress(ChildProcessError, OSError):
        os.waitpid(pid, 0)


class SessionManager:
    """Owns the live sessions for one relay connection."""

    def __init__(self, send, shell="/bin/sh", motd_command="", max_sessions=10):
        self._send = send
        self._shell = shell
        self._motd_command = motd_command
        self._max_sessions = max_sessions
        self._sessions = {}

    def __len__(self):
        return len(self._sessions)

    def __contains__(self, session_id):
        return session_id in self._sessions

    def get(self, session_id):
        return self._sessions.get(session_id)

    async def start(self, session_id):
        """Open a session, or raise SessionLimitReached.

        The relay advertises max_sessions_per_device, but the device is the one
        that pays for a runaway count in PTYs and RAM, so the cap is enforced on
        both sides.
        """
        existing = self._sessions.get(session_id)
        if existing is not None:
            return existing
        if len(self._sessions) >= self._max_sessions:
            raise SessionLimitReached(
                f"{len(self._sessions)} sessions already open (limit {self._max_sessions})"
            )

        session = ShellSession(
            session_id,
            self._send,
            shell=self._shell,
            motd_command=self._motd_command,
            on_exit=self._forget,
        )
        self._sessions[session_id] = session
        await session.start()
        return session

    def _forget(self, session_id):
        self._sessions.pop(session_id, None)

    def stop_all(self):
        for session in list(self._sessions.values()):
            session.stop()
        self._sessions.clear()


def make_json_sender(ws):
    """Adapt a WebSocket to the `send(payload_dict)` callable sessions expect."""

    async def send(payload):
        await ws.send(json.dumps(payload))

    return send
