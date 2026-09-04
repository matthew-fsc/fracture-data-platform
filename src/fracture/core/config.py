"""Runtime configuration.

Connection strings for tenant databases are never held here. They are assembled
at request time from the control-plane registry plus a secret lookup
(spec section 3.3). This object holds only the coordinates of the control plane
and the artifact store.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from fracture.core.errors import ConfigError


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise ConfigError(f"required environment variable {name} is not set")
    return value


@dataclass(frozen=True)
class Settings:
    """Process-wide settings, read from the environment once."""

    pg_host: str = field(default_factory=lambda: os.environ.get("FRACTURE_PG_HOST", "localhost"))
    pg_port: int = field(default_factory=lambda: int(os.environ.get("FRACTURE_PG_PORT", "5432")))
    pg_user: str = field(default_factory=lambda: os.environ.get("FRACTURE_PG_USER", "fracture"))
    pg_password: str = field(
        default_factory=lambda: os.environ.get("FRACTURE_PG_PASSWORD", "fracture")
    )
    control_db: str = field(
        default_factory=lambda: os.environ.get("FRACTURE_CONTROL_DB", "fracture_control")
    )
    artifact_root: Path = field(
        default_factory=lambda: Path(
            os.environ.get("FRACTURE_ARTIFACT_ROOT", "/tmp/fracture-artifacts")
        )
    )
    artifact_bucket: str = field(
        default_factory=lambda: os.environ.get("FRACTURE_ARTIFACT_BUCKET", "fracture-raw-local")
    )
    env: str = field(default_factory=lambda: os.environ.get("FRACTURE_ENV", "local"))

    @property
    def control_dsn(self) -> str:
        return (
            f"host={self.pg_host} port={self.pg_port} dbname={self.control_db} "
            f"user={self.pg_user} password={self.pg_password}"
        )

    def tenant_dsn(self, db_name: str, user: str | None = None, password: str | None = None) -> str:
        """Assemble a tenant DSN. Callers must go through the registry, not this
        method directly, so that the tenant's status and host are checked first."""
        return (
            f"host={self.pg_host} port={self.pg_port} dbname={db_name} "
            f"user={user or self.pg_user} password={password or self.pg_password}"
        )

    def is_production(self) -> bool:
        return self.env == "prod"


settings = Settings()
