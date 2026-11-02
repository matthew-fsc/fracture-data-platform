"""Dagster resources.

The important property here is negative: no resource holds more than one
tenant's credentials at a time, and the DSN is assembled per run from the
control plane rather than living in Dagster config (spec sections 3.3, 7).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from dagster import ConfigurableResource

from fracture.control.models import Tenant
from fracture.control.registry import ControlPlane
from fracture.core.secrets import EnvSecretResolver, default_resolver
from fracture.ingest.artifacts import ArtifactStore, default_store


class ControlPlaneResource(ConfigurableResource):
    """Access to `fracture_control`. Holds coordinates, never credentials."""

    secret_root: str | None = None

    def client(self) -> ControlPlane:
        resolver = (
            EnvSecretResolver(self.secret_root) if self.secret_root else default_resolver()
        )
        return ControlPlane(secret_resolver=resolver)

    def tenant(self, slug: str) -> Tenant:
        return self.client().get_tenant(slug)


class ArtifactStoreResource(ConfigurableResource):
    """Object storage for raw extraction artifacts."""

    def store(self, kms_key_arn: str | None = None) -> ArtifactStore:
        return default_store(kms_key_arn=kms_key_arn)


def utc_midnight(date: dt.date) -> dt.datetime:
    return dt.datetime.combine(date, dt.time.min, tzinfo=dt.timezone.utc)


def creds_for(control: ControlPlane, tenant: Tenant, firm_id: str, source_id: str) -> dict[str, Any]:
    return control.source_credentials(tenant, firm_id, source_id)
