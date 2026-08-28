"""Client configuration.

Same idea as the relay's `app/core/config.py` — one typed object, validated at
startup so a misconfigured device fails immediately with a readable message
instead of on the first reconnect, hours later, in a journal nobody is reading.

It is hand-rolled rather than pydantic-settings on purpose: this agent is
installed on the devices themselves, where dependencies are kept minimal (see
CONTRIBUTING.md) and where a wheel-less pydantic-core means compiling Rust on an
armv7 board. Everything here is standard library plus python-dotenv.
"""

import os
import shutil
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

# Shells to try, best first, when SHELL_COMMAND is unset and $SHELL points at
# nothing usable. Plenty of device images (Alpine, buildroot) ship no bash.
SHELL_CANDIDATES = ("/bin/bash", "/bin/ash", "/bin/sh")

TRUTHY = ("1", "true", "yes", "on")
FALSY = ("0", "false", "no", "off", "")


class ConfigError(Exception):
    """A value in the environment cannot be used."""


def _str(name, default):
    return os.getenv(name, default).strip()


def _bool(name, default=False):
    """Read a boolean env var.

    The string "false" is truthy in Python, so the value has to be compared and
    not merely tested for presence — a mistake that silently turns every flag on.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip().lower()
    if raw in TRUTHY:
        return True
    if raw in FALSY:
        return False
    raise ConfigError(f"{name}={raw!r} is not a boolean (use true/false)")


def _number(name, default, cast, minimum=None):
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = cast(raw.strip())
    except (TypeError, ValueError):
        raise ConfigError(f"{name}={raw!r} is not a valid {cast.__name__}")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name}={value} is below the minimum of {minimum}")
    return value


@dataclass
class Settings:
    """Everything the device agent reads from the environment."""

    # ---- Identity ----
    device_id: str = "unknown-device"
    # HMAC(device_master_secret, device_id), minted on the relay with
    # `python -m tools.mint_device_token <device_id>`.
    device_token: str = ""

    # ---- Relay ----
    # Base URL of the device endpoint; the device id is appended so the ingress
    # can hash on it and pin this device to one replica.
    server_url: str = "ws://127.0.0.1:8000/device"
    # Trust a self-signed relay certificate. Only meaningful for wss://.
    ws_insecure_tls: bool = False

    # ---- Reconnection ----
    # Base delay. The wait doubles per consecutive failure up to
    # reconnect_max_interval, with jitter, so a fleet coming back after an
    # outage does not converge on one retry instant.
    reconnect_interval: float = 5.0
    reconnect_max_interval: float = 60.0
    # Backoff after a refused handshake. A revoked or mistyped token does not
    # improve by retrying; the old fixed delay just hammered the relay that had
    # already said no.
    reconnect_auth_interval: float = 300.0

    # ---- Terminal ----
    # Empty means: honour $SHELL, else the first existing SHELL_CANDIDATES entry.
    shell_command: str = ""
    # Print the login banner where the distro has one. Skipped automatically on
    # images with no run-parts or /etc/update-motd.d, whatever this says.
    shell_motd: bool = True
    # Mirrors the relay's max_sessions_per_device: the device is the one paying
    # for a runaway session count in PTYs and RAM, so it enforces its own cap.
    max_sessions: int = 10

    # ---- Transfers ----
    chunk_size: int = 65536  # 64KB
    max_file_size: int = 104857600  # 100MB, the ceiling the relay advertises
    # Directories the relay may read from and write to, comma separated.
    # Anything outside is refused, so a compromised relay cannot drop a file in
    # /etc/cron.d or read the device's private keys. Set to "/" to opt out.
    allowed_roots: str = "~"

    # ---- Process ----
    log_level: str = "INFO"

    _roots_cache: List[str] = field(default_factory=list, repr=False, compare=False)

    # ---- Derived values -------------------------------------------------

    @property
    def uri(self) -> str:
        """Full device endpoint, id included."""
        return f"{self.server_url.rstrip('/')}/{self.device_id}"

    @property
    def allowed_root_list(self) -> List[str]:
        """Allowed roots as absolute, symlink-resolved paths.

        Resolved once so the check in `paths` compares like with like: a root
        written as `~` and a destination reached through a symlinked home have
        to normalise to the same string, or every transfer is refused.
        """
        if not self._roots_cache:
            for raw in self.allowed_roots.split(","):
                raw = raw.strip()
                if raw:
                    self._roots_cache.append(os.path.realpath(os.path.expanduser(raw)))
        return list(self._roots_cache)

    @property
    def shell(self) -> str:
        """Path to the shell to spawn for terminal sessions."""
        if self.shell_command:
            return self.shell_command
        env_shell = os.environ.get("SHELL", "").strip()
        if env_shell and os.path.exists(env_shell):
            return env_shell
        for candidate in SHELL_CANDIDATES:
            if os.path.exists(candidate):
                return candidate
        return "/bin/sh"

    @property
    def motd_command(self) -> str:
        """Banner command to run before the interactive shell, or "".

        Empty whenever the pieces are missing, which is every image that is not
        Debian-shaped.
        """
        if not self.shell_motd:
            return ""
        if not os.path.isdir("/etc/update-motd.d"):
            return ""
        if shutil.which("run-parts") is None:
            return ""
        return "run-parts /etc/update-motd.d"

    # ---- Loading ---------------------------------------------------------

    @classmethod
    def from_env(cls, use_dotenv: bool = True) -> "Settings":
        """Build settings from the environment, raising ConfigError on bad input.

        The .env file is read here rather than at import, and skippable, so a
        stray file on the developer's machine cannot decide what the tests see.
        """
        if use_dotenv:
            load_dotenv()
        return cls(
            device_id=_str("DEVICE_ID", cls.device_id),
            device_token=_str("DEVICE_TOKEN", cls.device_token),
            server_url=_str("SERVER_URL", cls.server_url),
            ws_insecure_tls=_bool("WS_INSECURE_TLS", cls.ws_insecure_tls),
            reconnect_interval=_number(
                "RECONNECT_INTERVAL", cls.reconnect_interval, float, minimum=0.1),
            reconnect_max_interval=_number(
                "RECONNECT_MAX_INTERVAL", cls.reconnect_max_interval, float, minimum=0.1),
            reconnect_auth_interval=_number(
                "RECONNECT_AUTH_INTERVAL", cls.reconnect_auth_interval, float, minimum=0.1),
            shell_command=_str("SHELL_COMMAND", cls.shell_command),
            shell_motd=_bool("SHELL_MOTD", cls.shell_motd),
            max_sessions=_number("MAX_SESSIONS", cls.max_sessions, int, minimum=1),
            chunk_size=_number("CHUNK_SIZE", cls.chunk_size, int, minimum=1024),
            max_file_size=_number("MAX_FILE_SIZE", cls.max_file_size, int, minimum=0),
            allowed_roots=_str("ALLOWED_ROOTS", cls.allowed_roots),
            log_level=_str("LOG_LEVEL", cls.log_level),
        )
