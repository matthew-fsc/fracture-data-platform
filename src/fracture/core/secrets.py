"""Secret resolution.

Credentials are issued by the client, stored per tenant, and never live in
source control or Dagster config (spec section 9). Code asks for a *path*; the
resolver turns the path into material. In prod that resolver is AWS Secrets
Manager with the tenant's KMS key; locally it is a directory of files or the
environment.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

from fracture.core.errors import ConfigError
from fracture.core.logging import get_logger

log = get_logger("core.secrets")


class SecretResolver(Protocol):
    def resolve(self, secret_path: str) -> dict[str, Any]:
        """Return the secret material at `secret_path`. Never log the result."""
        ...


class EnvSecretResolver:
    """Local/dev resolver.

    Looks for `FRACTURE_SECRET__<slugified path>` first, then a JSON file under
    the secret root. Values are returned, never logged; the caller gets a dict.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root or os.environ.get("FRACTURE_SECRET_ROOT", "/tmp/fracture-secrets"))

    @staticmethod
    def _env_key(secret_path: str) -> str:
        safe = "".join(c if c.isalnum() else "_" for c in secret_path).upper()
        return f"FRACTURE_SECRET__{safe}"

    def resolve(self, secret_path: str) -> dict[str, Any]:
        raw = os.environ.get(self._env_key(secret_path))
        if raw is not None:
            return json.loads(raw)
        candidate = self.root / f"{secret_path.replace('/', '__')}.json"
        if candidate.exists():
            return json.loads(candidate.read_text())
        raise ConfigError(f"no secret material found for path {secret_path!r}")

    def put(self, secret_path: str, material: dict[str, Any]) -> None:
        """Local convenience for tests and the synthetic tenant generator."""
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{secret_path.replace('/', '__')}.json"
        target.write_text(json.dumps(material))
        target.chmod(0o600)


class AWSSecretsManagerResolver:
    """Production resolver. Imported lazily so boto3 is not a hard dependency."""

    def __init__(self, region: str | None = None) -> None:
        self._region = region or os.environ.get("AWS_REGION", "us-east-1")
        self._client = None

    def _get_client(self):  # pragma: no cover - requires AWS
        if self._client is None:
            import boto3

            self._client = boto3.client("secretsmanager", region_name=self._region)
        return self._client

    def resolve(self, secret_path: str) -> dict[str, Any]:  # pragma: no cover - requires AWS
        response = self._get_client().get_secret_value(SecretId=secret_path)
        return json.loads(response["SecretString"])


def default_resolver() -> SecretResolver:
    if os.environ.get("FRACTURE_ENV") == "prod":  # pragma: no cover
        return AWSSecretsManagerResolver()
    return EnvSecretResolver()
