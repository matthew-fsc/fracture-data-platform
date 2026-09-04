"""Schema migration across N tenant databases.

Database-per-tenant costs you this: migrations run N times. Spec section 3.1
makes it a Dagster asset that fans out over the registry with a required
success threshold before the run is marked green. That threshold is the whole
mechanism -- a fan-out that reports success while three tenants are on an old
schema is exactly the silent failure this platform exists to remove.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from fracture.core import db
from fracture.core.errors import FractureError
from fracture.core.hashing import sha256_bytes
from fracture.core.logging import get_logger
from fracture.control.models import Tenant
from fracture.control.registry import ControlPlane

log = get_logger("control.migrations")

#: Fraction of tenants that must migrate cleanly for the run to be green.
DEFAULT_SUCCESS_THRESHOLD = 1.0


class MigrationThresholdNotMet(FractureError):
    """Fewer tenants migrated than the threshold requires."""


@dataclass
class TenantMigrationResult:
    slug: str
    version: str
    succeeded: bool
    error: str | None = None
    skipped: bool = False


@dataclass
class MigrationRun:
    version: str
    results: list[TenantMigrationResult] = field(default_factory=list)

    @property
    def attempted(self) -> list[TenantMigrationResult]:
        return [r for r in self.results if not r.skipped]

    @property
    def succeeded(self) -> list[TenantMigrationResult]:
        return [r for r in self.attempted if r.succeeded]

    @property
    def failed(self) -> list[TenantMigrationResult]:
        return [r for r in self.attempted if not r.succeeded]

    @property
    def success_rate(self) -> float:
        if not self.attempted:
            return 1.0
        return len(self.succeeded) / len(self.attempted)

    def summary(self) -> str:
        return (
            f"migration {self.version}: {len(self.succeeded)}/{len(self.attempted)} tenants "
            f"({self.success_rate:.0%})"
            + (f"; failed: {', '.join(r.slug for r in self.failed)}" if self.failed else "")
        )


class Migrator:
    """Applies a versioned SQL migration to every tenant in the registry."""

    def __init__(self, control: ControlPlane) -> None:
        self.control = control

    def target_tenants(self, statuses: Sequence[str] = ("active", "provisioning")) -> list[Tenant]:
        return [t for t in self.control.list_tenants() if t.status in statuses]

    def already_applied(self, tenant: Tenant, version: str, checksum: bytes) -> bool:
        with self.control.connection() as conn:
            row = db.query_one(
                conn,
                """
                select checksum, succeeded from control.tenant_migration
                 where tenant_id=%s and version=%s
                """,
                (tenant.tenant_id, version),
            )
        if row is None or not row["succeeded"]:
            return False
        if bytes(row["checksum"]) != checksum:
            # A version applied with different SQL is a fork, not a re-run.
            raise FractureError(
                f"migration {version} was applied to {tenant.slug} with a different checksum; "
                "cut a new version rather than editing an applied one"
            )
        return True

    def _record(self, tenant: Tenant, version: str, checksum: bytes, ok: bool, error: str | None) -> None:
        with self.control.connection() as conn:
            db.execute(
                conn,
                """
                insert into control.tenant_migration
                  (tenant_id, version, checksum, succeeded, error)
                values (%s,%s,%s,%s,%s)
                on conflict (tenant_id, version) do update set
                  applied_at = now(), checksum = excluded.checksum,
                  succeeded = excluded.succeeded, error = excluded.error
                """,
                (tenant.tenant_id, version, checksum, ok, error),
            )

    def apply_to_tenant(self, tenant: Tenant, version: str, sql: str) -> TenantMigrationResult:
        checksum = sha256_bytes(sql.encode("utf-8"))
        if self.already_applied(tenant, version, checksum):
            return TenantMigrationResult(tenant.slug, version, True, skipped=True)
        dsn = self.control.settings.tenant_dsn(tenant.db_name)
        try:
            with db.connect(dsn) as conn:
                db.run_script(conn, sql)
        except Exception as exc:
            self._record(tenant, version, checksum, False, str(exc)[:2000])
            log.error("migration %s failed on %s: %s", version, tenant.slug, exc)
            return TenantMigrationResult(tenant.slug, version, False, str(exc))
        self._record(tenant, version, checksum, True, None)
        return TenantMigrationResult(tenant.slug, version, True)

    def fan_out(
        self,
        version: str,
        sql: str,
        tenants: Iterable[Tenant] | None = None,
        success_threshold: float = DEFAULT_SUCCESS_THRESHOLD,
        on_result: Callable[[TenantMigrationResult], None] | None = None,
    ) -> MigrationRun:
        """Apply `sql` to every tenant. Raises unless the threshold is met.

        Every tenant is attempted even after one fails: a partial fan-out that
        stops at the first error leaves the estate in an unknown state, which is
        worse than a known-bad one.
        """
        run = MigrationRun(version=version)
        for tenant in tenants if tenants is not None else self.target_tenants():
            result = self.apply_to_tenant(tenant, version, sql)
            run.results.append(result)
            if on_result:
                on_result(result)
        log.info(run.summary())
        if run.success_rate < success_threshold:
            raise MigrationThresholdNotMet(
                f"{run.summary()} -- below required threshold {success_threshold:.0%}"
            )
        return run

    def rebuild_schema_everywhere(self, success_threshold: float = DEFAULT_SUCCESS_THRESHOLD) -> MigrationRun:
        """Re-apply the full tenant DDL. Every script is `create ... if not exists`
        or `create or replace`, so this repairs drift rather than destroying it."""
        from fracture.canon.schema import ddl_checksum, tenant_ddl_scripts

        sql = "\n".join(s for _, s in tenant_ddl_scripts())
        version = f"ddl-{ddl_checksum().hex()[:12]}"
        return self.fan_out(version, sql, success_threshold=success_threshold)
