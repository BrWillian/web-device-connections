from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Relay configuration.

    There is deliberately nothing here about users, passwords or sessions: the
    relay does not authenticate people. It verifies grants signed by the frontend
    and tokens derived for devices, and relays bytes.
    """

    # ---- Process ----
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    # Identifies this replica in the presence store. In Kubernetes, feed it from
    # the downward API (metadata.name).
    pod_name: str = "local"

    # ---- Shared state (required to run more than one replica) ----
    redis_url: str = ""
    presence_ttl: int = 30  # seconds; refreshed by a heartbeat at ttl/3

    # ---- Trust with the frontend ----
    # The frontend signs a short-lived grant for each WebSocket upgrade and the
    # relay verifies it with this same secret. It also authorises the frontend's
    # server-to-server call to /devices.
    relay_secret: str = ""
    grant_leeway: int = 5  # seconds of clock skew tolerated on a grant

    # ---- Device trust ----
    # Device tokens are HMAC(device_master_secret, device_id): nothing is stored,
    # and one device's token cannot open another's session.
    device_master_secret: str = ""
    # Accept unauthenticated devices on the legacy /device route. Enable only
    # during a fleet migration window, never in production.
    allow_legacy_devices: bool = False

    # Browsers connect to the relay only over WebSocket, which is not covered by
    # CORS. Listing origins here rejects upgrades from anywhere else; empty means
    # no Origin check.
    allowed_origins: str = ""

    # ---- Transfers ----
    max_file_size: int = 104857600  # 100MB
    chunk_size: int = 65536  # 64KB
    session_timeout: int = 3600
    max_sessions_per_device: int = 10

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def allowed_origin_list(self) -> List[str]:
        """Origins permitted to open a WebSocket. Empty list means no check.

        Kept as a plain string on the model because pydantic-settings tries to
        JSON-decode complex types read from the environment, which makes the
        familiar comma-separated form fail at import time.
        """
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def multi_replica_ready(self) -> bool:
        """True when shared state is configured, i.e. it is safe to scale out."""
        return bool(self.redis_url)


settings = Settings()
