"""Terminal sessions, the session cap, and process cleanup.

These spawn real shells: the parts most worth testing here — PTY wiring, the
session dict staying in step with the process table — do not survive being
mocked out.
"""

import asyncio
import errno
import os

import pytest

from wdc_client import protocol
from wdc_client.shell import SessionLimitReached, SessionManager, ShellSession


class Collector:
    """Captures what a session would send to the relay."""

    def __init__(self):
        self.messages = []

    async def __call__(self, payload):
        self.messages.append(payload)

    def text(self):
        return "".join(m.get("data", "") for m in self.messages)


async def _wait_for(predicate, timeout=5.0):
    """Poll until `predicate` holds; shells take a moment to produce output."""
    waited = 0.0
    while waited < timeout:
        if predicate():
            return True
        await asyncio.sleep(0.05)
        waited += 0.05
    return False


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


@pytest.fixture
def collector():
    return Collector()


async def test_a_session_echoes_command_output(collector):
    session = ShellSession("s1", collector, shell="/bin/sh")
    await session.start()
    try:
        session.send_input("echo marker-42\n")
        assert await _wait_for(lambda: "marker-42" in collector.text())
        assert collector.messages[0]["type"] == protocol.MSG_OUTPUT
        assert collector.messages[0]["session_id"] == "s1"
    finally:
        session.stop()


async def test_stopping_a_session_terminates_and_reaps_the_shell(collector):
    """An unreaped child on an agent that runs for months is a zombie forever."""
    session = ShellSession("s1", collector, shell="/bin/sh")
    await session.start()
    pid = session.pid

    session.stop()
    assert await _wait_for(lambda: not _alive(pid))
    # waitpid already collected it, so a second wait finds no such child.
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


async def test_a_shell_that_ignores_sigterm_is_killed(collector, monkeypatch):
    monkeypatch.setattr("wdc_client.shell.TERM_GRACE_SECONDS", 0.3)
    session = ShellSession("s1", collector, shell="/bin/sh")
    await session.start()
    session.send_input("trap '' TERM\n")
    await asyncio.sleep(0.3)
    pid = session.pid

    session.stop()
    assert await _wait_for(lambda: not _alive(pid))


async def test_stop_is_idempotent(collector):
    session = ShellSession("s1", collector, shell="/bin/sh")
    await session.start()
    session.stop()
    session.stop()


async def test_resizing_a_stopped_session_is_harmless(collector):
    session = ShellSession("s1", collector, shell="/bin/sh")
    await session.start()
    session.stop()
    session.resize_terminal(120, 40)
    session.send_input("ignored\n")


async def test_a_shell_that_exits_leaves_the_manager(collector):
    """The session dict used to grow for the lifetime of the agent."""
    manager = SessionManager(collector, shell="/bin/sh")
    await manager.start("s1")
    manager.get("s1").send_input("exit\n")

    assert await _wait_for(lambda: len(manager) == 0)
    assert "s1" not in manager


async def test_the_session_cap_is_enforced_on_the_device(collector):
    manager = SessionManager(collector, shell="/bin/sh", max_sessions=2)
    try:
        await manager.start("s1")
        await manager.start("s2")
        with pytest.raises(SessionLimitReached):
            await manager.start("s3")
    finally:
        manager.stop_all()


async def test_reusing_a_session_id_returns_the_same_session(collector):
    manager = SessionManager(collector, shell="/bin/sh", max_sessions=1)
    try:
        first = await manager.start("s1")
        assert await manager.start("s1") is first
    finally:
        manager.stop_all()


async def test_stop_all_clears_everything(collector):
    manager = SessionManager(collector, shell="/bin/sh")
    await manager.start("s1")
    await manager.start("s2")
    pids = [manager.get("s1").pid, manager.get("s2").pid]

    manager.stop_all()
    assert len(manager) == 0
    assert await _wait_for(lambda: not any(_alive(pid) for pid in pids))


def test_the_banner_is_only_prepended_when_there_is_one(collector):
    plain = ShellSession("s", collector, shell="/bin/sh")._child_argv()
    assert plain == ["/bin/sh", "-i"]

    with_motd = ShellSession("s", collector, shell="/bin/sh", motd_command="banner")._child_argv()
    assert with_motd == ["/bin/sh", "-i", "-c", "banner; exec sh -i"]
