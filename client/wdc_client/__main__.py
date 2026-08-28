"""Entry point: `python -m wdc_client`.

Under systemd or Docker the agent is stopped with SIGTERM. Without a handler
that arrives as an abrupt process death: PTYs are left behind, the WebSocket
never closes, and the relay keeps the device marked online until presence
expires. Here the signal cancels the run task, which unwinds through the same
cleanup path a dropped connection uses.
"""

import asyncio
import logging
import signal
import sys

from . import __version__
from .config import ConfigError

logger = logging.getLogger("wdc_client")


def configure_logging(level="INFO"):
    """Set up (or re-level) root logging.

    Called twice: once so a configuration error has somewhere to go, then again
    with the configured level. basicConfig is a no-op the second time, hence the
    explicit setLevel.
    """
    logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    logging.getLogger().setLevel(getattr(logging, level.upper(), logging.INFO))


def check_configuration(settings):
    """Fail fast on a configuration that cannot possibly work."""
    problems = []
    if not settings.device_token:
        problems.append(
            "DEVICE_TOKEN is not set. Ask the relay operator for this device's token: "
            f"python -m tools.mint_device_token {settings.device_id}"
        )
    if not settings.server_url:
        problems.append("SERVER_URL is not set.")
    if not settings.allowed_root_list:
        problems.append(
            "ALLOWED_ROOTS is empty, so every file transfer would be refused. "
            'Use "~" for the home directory, or "/" to allow the whole filesystem.'
        )
    return problems


async def _run(settings):
    from .connection import DeviceClient  # imported late: pulls in websockets

    client = DeviceClient(settings)
    task = asyncio.ensure_future(client.run())

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # add_signal_handler is the asyncio-safe form: signal.signal would fire
        # the callback between bytecodes, outside the loop.
        try:
            loop.add_signal_handler(sig, task.cancel)
        except NotImplementedError:
            pass  # not available on every platform

    try:
        await task
    except asyncio.CancelledError:
        logger.info("Shutting down")


def main():
    configure_logging()
    try:
        # Loaded here, not at import time, so a bad value in the environment is
        # reported as one clear line instead of a traceback from an import.
        from .config import Settings

        settings = Settings.from_env()
    except ConfigError as exc:
        logger.error("Invalid configuration: %s", exc)
        return 1

    configure_logging(settings.log_level)
    problems = check_configuration(settings)
    if problems:
        for problem in problems:
            logger.error(problem)
        return 1

    logger.info("wdc-client %s starting for device %s", __version__, settings.device_id)
    try:
        asyncio.run(_run(settings))
    except KeyboardInterrupt:
        # Reached only if the signal handler could not be installed; without
        # this the operator gets a traceback for pressing Ctrl+C.
        logger.info("Interrupted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
