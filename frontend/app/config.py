"""Frontend configuration, in one place.

Mirrors ``server/app/core/config.py`` so both processes are configured the same
way: a single pydantic-settings model, read from the environment and from
``.env``, with the defaults written down next to what they mean.

One value is shared with the relay and must match on both sides:
``relay_secret``. Everything about people — sessions, password hashes, roles —
lives only here; the relay has no idea users exist.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ---- Process ----
    host: str = "0.0.0.0"
    port: int = 3000
    log_level: str = "INFO"
    app_name: str = "Device Connections"

    # ---- The relay, at two different addresses ----
    # Reached by THIS process, server-to-server. Inside compose or Kubernetes
    # that is an internal name.
    relay_url: str = "http://localhost:8000"
    # Reached by the BROWSER. It must resolve from the user's machine, and in
    # Kubernetes it must be the ingress: only the ingress applies the device-id
    # hash that lands the upgrade on the right pod.
    relay_ws_url: str = "ws://localhost:8000"
    # Shared with the relay. Signs the grants it verifies, and authorises this
    # app's server-to-server calls to /devices.
    relay_secret: str = ""

    # ---- Sessions ----
    session_secret: str = ""
    session_ttl: int = 43200  # 12h
    grant_ttl: int = 30       # seconds; a grant only has to survive one upgrade
    # Set true wherever the app is served over HTTPS.
    cookie_secure: bool = False

    # ---- Storage ----
    database_url: str = "postgresql+asyncpg://wdc:wdc@localhost:5432/wdc"
    # The relay reads revocations from here. Empty means revocations are recorded
    # in the database but never reach the relay; see services/revocation.py.
    redis_url: str = ""

    # ---- First run ----
    # Used only to seed the very first account, when the users table is empty.
    admin_username: str = "admin"
    admin_password_hash: str = ""

    # ---- Presentation ----
    # Timestamps are stored in UTC; this is only how they are displayed. It used
    # to be whatever timezone each operator's browser happened to be set to.
    display_tz: str = "America/Sao_Paulo"

    min_password_length: int = 8

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def relay_base(self) -> str:
        return self.relay_url.rstrip("/")

    @property
    def relay_ws_base(self) -> str:
        return self.relay_ws_url.rstrip("/")


settings = Settings()
