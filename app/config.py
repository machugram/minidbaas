"""Runtime configuration, sourced from environment (prefix ``MDBAAS_``).

Kept as a single frozen ``Settings`` object resolved once at import time so the
rest of the code depends on values, not on ``os.environ`` scattered everywhere.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MDBAAS_", env_file=".env", extra="ignore")

    # Metadata store — the control plane's own Postgres. SQLite is used only by
    # the test-suite (locking clauses degrade gracefully there; see services/ports.py).
    database_url: str = "postgresql+psycopg://mdbaas:mdbaas@localhost:5432/mdbaas"

    # Credential encryption KEK (Fernet, urlsafe-base64). Required in production;
    # the tests inject a generated one via the env. Envelope encryption wraps a
    # per-secret DEK with this key (design-review §1.3).
    credential_encryption_key: str = ""
    # During KEK rotation: old key for decrypt-only; new encrypts use credential_encryption_key.
    credential_encryption_key_previous: str = ""

    # Provisioner. base_url=None means "use the ambient DOCKER_HOST / default socket".
    # In compose, point at docker-socket-proxy instead of the raw socket (DR §1.1).
    docker_base_url: str | None = None
    # Attach each instance to an isolated bridge network per team (DR §1.2).
    per_team_networks: bool = True
    pg_image_default: str = "postgres:16"
    # Only images on this allow-list may be provisioned or patched to.
    allowed_pg_images: list[str] = Field(
        default_factory=lambda: ["postgres:15", "postgres:16", "postgres:16.4", "postgres:17"]
    )

    # Connectivity (ADR-007): one localhost-bound host port per instance.
    instance_bind_host: str = "127.0.0.1"
    port_range_start: int = 15000
    port_range_end: int = 16000

    # Backups share a single named volume mounted into every managed instance at
    # /backups, so dumps never round-trip through the control-plane process.
    backups_volume: str = "mdbaas-backups"
    backup_retention_days: int = 7
    default_rpo_hours: int = 24

    # Two-phase expiry (DR-7): stop at expiry, destroy only after the grace window.
    expiry_grace_hours: int = 24

    # Scheduler cadence (seconds unless noted).
    worker_interval_seconds: int = 2
    reconcile_interval_seconds: int = 60
    reaper_interval_seconds: int = 300
    backup_cron_hour: int = 2

    # Job retry policy (DR-3).
    job_max_attempts: int = 5
    job_claim_timeout_seconds: int = 300

    api_prefix: str = "/v1"

    # API hardening
    cors_origins: list[str] = Field(default_factory=list)
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60
    max_request_bytes: int = 1_048_576

    # Disabled by the test-suite so no BackgroundScheduler (and no Docker) spins up.
    enable_scheduler: bool = True
    # Auto-create a default team + admin on first start (local demo convenience).
    enable_bootstrap: bool = True

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
