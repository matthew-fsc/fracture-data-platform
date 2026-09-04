"""Typed views over the control-plane registry."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

TenantStatus = Literal["provisioning", "active", "suspended", "archived"]
Motion = Literal["diligence", "operating"]
FirmRole = Literal["platform", "addon"]
SourceStatus = Literal["pending", "verified", "live", "failed"]
DbRole = Literal["loader", "transform", "reader", "owner"]


@dataclass(frozen=True)
class Tenant:
    tenant_id: uuid.UUID
    slug: str
    legal_name: str
    status: TenantStatus
    motion: Motion
    kms_key_arn: str
    db_host: str
    db_name: str
    s3_prefix: str
    created_at: dt.datetime | None = None
    archive_after: dt.date | None = None
    promoted_from: uuid.UUID | None = None

    def role_name(self, role: DbRole) -> str:
        """Per-tenant role name (spec section 3.3): t_<slug>_<role>."""
        return f"t_{self.slug.replace('-', '_')}_{role}"

    @property
    def is_ephemeral(self) -> bool:
        return self.motion == "diligence"


@dataclass(frozen=True)
class TenantFirm:
    tenant_id: uuid.UUID
    firm_id: str
    legal_name: str
    role: FirmRole
    close_date: dt.date | None = None
    folded_in_at: dt.datetime | None = None


@dataclass(frozen=True)
class TenantSource:
    tenant_id: uuid.UUID
    firm_id: str
    source_id: str
    secret_path: str
    status: SourceStatus
    verified_read_only_at: dt.datetime | None = None
    verified_by: str | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class PackRun:
    pack_run_id: uuid.UUID
    tenant_id: uuid.UUID
    period_start: dt.date
    period_end: dt.date
    system_time: dt.datetime
    status: str
    content_hash: bytes | None = None
    issued_at: dt.datetime | None = None
    supersedes: uuid.UUID | None = None


@dataclass(frozen=True)
class SourceFingerprint:
    """Output of `SourceAdapter.fingerprint()`; drives drift detection."""

    source_id: str
    firm_id: str
    source_version: str | None
    schema_hash: bytes
    row_counts: dict[str, int] = field(default_factory=dict)
    streams: list[str] = field(default_factory=list)
    field_names: dict[str, list[str]] = field(default_factory=dict)
    read_only_verified: bool = False
    observed_at: dt.datetime | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "firm_id": self.firm_id,
            "source_version": self.source_version,
            "schema_hash": self.schema_hash,
            "row_counts": self.row_counts,
            "streams": self.streams,
            "field_names": self.field_names,
            "read_only_verified": self.read_only_verified,
        }
