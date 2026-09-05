"""The tenant registry.

Everything that needs to reach a tenant database goes through here. That is the
point: connection strings are assembled at runtime from the registry plus a
secret lookup, and never stored in code or in orchestrator config
(spec section 3.3).
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Any, Iterable, Sequence

from psycopg2.extensions import connection as PGConnection
from psycopg2.extras import Json

from fracture.core import db
from fracture.core.config import Settings, settings as default_settings
from fracture.core.errors import ConfigError, TenantIsolationError
from fracture.core.logging import get_logger
from fracture.core.secrets import SecretResolver, default_resolver
from fracture.control.models import (
    DbRole,
    Motion,
    PackRun,
    SourceFingerprint,
    Tenant,
    TenantFirm,
    TenantSource,
    TenantStatus,
)

log = get_logger("control.registry")

SCHEMA_SQL = Path(__file__).with_name("schema.sql")

#: Statuses a tenant may be in and still be handed a working connection.
CONNECTABLE_STATUSES: frozenset[str] = frozenset({"provisioning", "active"})


def db_name_for(slug: str) -> str:
    return f"tenant_{slug.replace('-', '_')}"


def s3_prefix_for(slug: str) -> str:
    return f"tenants/{slug}"


class ControlPlane:
    """Client for `fracture_control`. Holds no tenant credentials of its own."""

    def __init__(
        self,
        settings: Settings | None = None,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self.settings = settings or default_settings
        self.secrets = secret_resolver or default_resolver()

    # -- lifecycle ---------------------------------------------------------

    def connection(self, autocommit: bool = False):
        return db.connect(self.settings.control_dsn, autocommit=autocommit)

    def install_schema(self) -> None:
        sql = SCHEMA_SQL.read_text()
        with self.connection() as conn:
            db.run_script(conn, sql)
        log.info("control plane schema installed")

    # -- tenants -----------------------------------------------------------

    def register_tenant(
        self,
        slug: str,
        legal_name: str,
        motion: Motion,
        kms_key_arn: str | None = None,
        db_host: str | None = None,
        archive_after: dt.date | None = None,
        status: TenantStatus = "provisioning",
        tenant_id: uuid.UUID | None = None,
        promoted_from: uuid.UUID | None = None,
    ) -> Tenant:
        if motion == "diligence" and archive_after is None:
            # Enforced by the database too; raised here so the caller gets a
            # sentence rather than a constraint name.
            raise ConfigError(
                "a diligence tenant must carry an archive_after date "
                "(spec section 13: ephemeral by default, promotion is explicit)"
            )
        tenant = Tenant(
            tenant_id=tenant_id or uuid.uuid4(),
            slug=slug,
            legal_name=legal_name,
            status=status,
            motion=motion,
            kms_key_arn=kms_key_arn or f"arn:aws:kms:local:000000000000:key/{slug}",
            db_host=db_host or self.settings.pg_host,
            db_name=db_name_for(slug),
            s3_prefix=s3_prefix_for(slug),
            archive_after=archive_after,
            promoted_from=promoted_from,
        )
        with self.connection() as conn:
            db.execute(
                conn,
                """
                insert into control.tenant
                  (tenant_id, slug, legal_name, status, motion, kms_key_arn,
                   db_host, db_name, s3_prefix, archive_after, promoted_from)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (slug) do update set
                  legal_name = excluded.legal_name,
                  status     = excluded.status,
                  motion     = excluded.motion
                """,
                (
                    tenant.tenant_id,
                    tenant.slug,
                    tenant.legal_name,
                    tenant.status,
                    tenant.motion,
                    tenant.kms_key_arn,
                    tenant.db_host,
                    tenant.db_name,
                    tenant.s3_prefix,
                    tenant.archive_after,
                    tenant.promoted_from,
                ),
            )
        log.info("registered tenant %s (%s)", tenant.slug, tenant.motion)
        return self.get_tenant(slug)

    def get_tenant(self, slug: str) -> Tenant:
        with self.connection() as conn:
            row = db.query_one(
                conn, "select * from control.tenant where slug = %s", (slug,)
            )
        if row is None:
            raise ConfigError(f"no tenant registered with slug {slug!r}")
        return _tenant_from_row(row)

    def find_tenant(self, slug: str) -> Tenant | None:
        try:
            return self.get_tenant(slug)
        except ConfigError:
            return None

    def list_tenants(
        self, status: str | None = None, motion: str | None = None
    ) -> list[Tenant]:
        clauses, params = [], []
        if status:
            clauses.append("status = %s")
            params.append(status)
        if motion:
            clauses.append("motion = %s")
            params.append(motion)
        where = f"where {' and '.join(clauses)}" if clauses else ""
        with self.connection() as conn:
            rows = db.query(
                conn, f"select * from control.tenant {where} order by slug", tuple(params)
            )
        return [_tenant_from_row(r) for r in rows]

    def set_tenant_status(self, slug: str, status: TenantStatus) -> None:
        with self.connection() as conn:
            updated = db.execute(
                conn, "update control.tenant set status=%s where slug=%s", (status, slug)
            )
        if updated == 0:
            raise ConfigError(f"no tenant registered with slug {slug!r}")

    def promote_tenant(self, slug: str) -> Tenant:
        """Diligence -> operating, in place (spec section 13).

        Promotion must not be a rebuild: the same database, the same raw
        artifacts, the same lineage. Only the motion and the archive date move.
        """
        with self.connection() as conn:
            row = db.query_one(conn, "select * from control.tenant where slug=%s", (slug,))
            if row is None:
                raise ConfigError(f"no tenant registered with slug {slug!r}")
            if row["motion"] == "operating":
                return _tenant_from_row(row)
            db.execute(
                conn,
                """
                update control.tenant
                   set motion = 'operating', archive_after = null, status = 'active'
                 where slug = %s
                """,
                (slug,),
            )
        log.info("promoted tenant %s from diligence to operating", slug)
        return self.get_tenant(slug)

    def tenants_due_for_archive(self, as_of: dt.date | None = None) -> list[Tenant]:
        as_of = as_of or dt.date.today()
        with self.connection() as conn:
            rows = db.query(
                conn,
                """
                select * from control.tenant
                 where motion = 'diligence'
                   and archive_after is not null
                   and archive_after <= %s
                   and status <> 'archived'
                 order by archive_after
                """,
                (as_of,),
            )
        return [_tenant_from_row(r) for r in rows]

    # -- firms -------------------------------------------------------------

    def add_firm(
        self,
        tenant: Tenant | str,
        firm_id: str,
        legal_name: str,
        role: str,
        close_date: dt.date | None = None,
        folded_in_at: dt.datetime | None = None,
    ) -> TenantFirm:
        tenant = self._resolve(tenant)
        with self.connection() as conn:
            db.execute(
                conn,
                """
                insert into control.tenant_firm
                  (tenant_id, firm_id, legal_name, role, close_date, folded_in_at)
                values (%s,%s,%s,%s,%s,%s)
                on conflict (tenant_id, firm_id) do update set
                  legal_name = excluded.legal_name,
                  role = excluded.role,
                  close_date = excluded.close_date,
                  folded_in_at = excluded.folded_in_at
                """,
                (tenant.tenant_id, firm_id, legal_name, role, close_date, folded_in_at),
            )
        return TenantFirm(tenant.tenant_id, firm_id, legal_name, role, close_date, folded_in_at)

    def list_firms(self, tenant: Tenant | str) -> list[TenantFirm]:
        tenant = self._resolve(tenant)
        with self.connection() as conn:
            rows = db.query(
                conn,
                "select * from control.tenant_firm where tenant_id=%s order by role desc, firm_id",
                (tenant.tenant_id,),
            )
        return [
            TenantFirm(
                r["tenant_id"], r["firm_id"], r["legal_name"], r["role"],
                r["close_date"], r["folded_in_at"],
            )
            for r in rows
        ]

    # -- sources -----------------------------------------------------------

    def register_source(
        self,
        tenant: Tenant | str,
        firm_id: str,
        source_id: str,
        secret_path: str,
        status: str = "pending",
    ) -> TenantSource:
        tenant = self._resolve(tenant)
        with self.connection() as conn:
            db.execute(
                conn,
                """
                insert into control.tenant_source
                  (tenant_id, firm_id, source_id, secret_path, status)
                values (%s,%s,%s,%s,%s)
                on conflict (tenant_id, firm_id, source_id) do update set
                  secret_path = excluded.secret_path
                """,
                (tenant.tenant_id, firm_id, source_id, secret_path, status),
            )
        return TenantSource(tenant.tenant_id, firm_id, source_id, secret_path, status)

    def verify_read_only(
        self,
        tenant: Tenant | str,
        firm_id: str,
        source_id: str,
        verified_by: str,
        at: dt.datetime | None = None,
    ) -> None:
        """Record the read-only proof. Without this a source cannot go live."""
        tenant = self._resolve(tenant)
        with self.connection() as conn:
            db.execute(
                conn,
                """
                update control.tenant_source
                   set status = 'verified',
                       verified_read_only_at = coalesce(%s, now()),
                       verified_by = %s
                 where tenant_id=%s and firm_id=%s and source_id=%s
                """,
                (at, verified_by, tenant.tenant_id, firm_id, source_id),
            )

    def activate_source(self, tenant: Tenant | str, firm_id: str, source_id: str) -> None:
        tenant = self._resolve(tenant)
        with self.connection() as conn:
            db.execute(
                conn,
                """
                update control.tenant_source set status='live', last_error=null
                 where tenant_id=%s and firm_id=%s and source_id=%s
                """,
                (tenant.tenant_id, firm_id, source_id),
            )

    def fail_source(self, tenant: Tenant | str, firm_id: str, source_id: str, error: str) -> None:
        tenant = self._resolve(tenant)
        with self.connection() as conn:
            db.execute(
                conn,
                """
                update control.tenant_source set status='failed', last_error=%s
                 where tenant_id=%s and firm_id=%s and source_id=%s
                """,
                (error[:2000], tenant.tenant_id, firm_id, source_id),
            )

    def list_sources(
        self, tenant: Tenant | str, firm_id: str | None = None, status: str | None = None
    ) -> list[TenantSource]:
        tenant = self._resolve(tenant)
        clauses = ["tenant_id = %s"]
        params: list[Any] = [tenant.tenant_id]
        if firm_id:
            clauses.append("firm_id = %s")
            params.append(firm_id)
        if status:
            clauses.append("status = %s")
            params.append(status)
        with self.connection() as conn:
            rows = db.query(
                conn,
                f"select * from control.tenant_source where {' and '.join(clauses)} "
                "order by firm_id, source_id",
                tuple(params),
            )
        return [
            TenantSource(
                r["tenant_id"], r["firm_id"], r["source_id"], r["secret_path"],
                r["status"], r["verified_read_only_at"], r["verified_by"], r["last_error"],
            )
            for r in rows
        ]

    def source_credentials(
        self, tenant: Tenant | str, firm_id: str, source_id: str
    ) -> dict[str, Any]:
        """Resolve a source credential at call time. The material is returned to
        the caller and never logged, cached, or written back to the registry."""
        tenant = self._resolve(tenant)
        with self.connection() as conn:
            row = db.query_one(
                conn,
                """
                select secret_path, status from control.tenant_source
                 where tenant_id=%s and firm_id=%s and source_id=%s
                """,
                (tenant.tenant_id, firm_id, source_id),
            )
        if row is None:
            raise ConfigError(f"source {source_id!r} is not registered for firm {firm_id!r}")
        return self.secrets.resolve(row["secret_path"])

    # -- fingerprints ------------------------------------------------------

    def record_fingerprint(
        self, tenant: Tenant | str, fingerprint: SourceFingerprint
    ) -> uuid.UUID:
        tenant = self._resolve(tenant)
        with self.connection() as conn:
            row = db.query_one(
                conn,
                """
                insert into control.source_fingerprint
                  (tenant_id, firm_id, source_id, source_version, schema_hash,
                   row_counts, streams, field_names, read_only_verified)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                returning fingerprint_id
                """,
                (
                    tenant.tenant_id,
                    fingerprint.firm_id,
                    fingerprint.source_id,
                    fingerprint.source_version,
                    fingerprint.schema_hash,
                    Json(fingerprint.row_counts),
                    Json(fingerprint.streams),
                    Json(fingerprint.field_names),
                    fingerprint.read_only_verified,
                ),
            )
        return row["fingerprint_id"]

    def previous_fingerprint(
        self, tenant: Tenant | str, firm_id: str, source_id: str, before_id: uuid.UUID | None = None
    ) -> SourceFingerprint | None:
        tenant = self._resolve(tenant)
        sql = """
            select * from control.source_fingerprint
             where tenant_id=%s and firm_id=%s and source_id=%s
        """
        params: list[Any] = [tenant.tenant_id, firm_id, source_id]
        if before_id is not None:
            sql += " and fingerprint_id <> %s"
            params.append(before_id)
        sql += " order by observed_at desc limit 1"
        with self.connection() as conn:
            row = db.query_one(conn, sql, tuple(params))
        if row is None:
            return None
        return SourceFingerprint(
            source_id=row["source_id"],
            firm_id=row["firm_id"],
            source_version=row["source_version"],
            schema_hash=bytes(row["schema_hash"]),
            row_counts=row["row_counts"],
            streams=row["streams"],
            field_names=row.get("field_names") or {},
            read_only_verified=row["read_only_verified"],
            observed_at=row["observed_at"],
        )

    # -- pack runs ---------------------------------------------------------

    def open_pack_run(
        self,
        tenant: Tenant | str,
        period_start: dt.date,
        period_end: dt.date,
        system_time: dt.datetime,
        supersedes: uuid.UUID | None = None,
    ) -> PackRun:
        tenant = self._resolve(tenant)
        with self.connection() as conn:
            row = db.query_one(
                conn,
                """
                insert into control.pack_run
                  (tenant_id, period, system_time, status, supersedes)
                values (%s, daterange(%s,%s,'[]'), %s, 'building', %s)
                returning pack_run_id
                """,
                (tenant.tenant_id, period_start, period_end, system_time, supersedes),
            )
        return PackRun(
            pack_run_id=row["pack_run_id"],
            tenant_id=tenant.tenant_id,
            period_start=period_start,
            period_end=period_end,
            system_time=system_time,
            status="building",
            supersedes=supersedes,
        )

    def issue_pack_run(self, pack_run_id: uuid.UUID, content_hash: bytes) -> None:
        with self.connection() as conn:
            db.execute(
                conn,
                """
                update control.pack_run
                   set status='issued', content_hash=%s, issued_at=now()
                 where pack_run_id=%s
                """,
                (content_hash, pack_run_id),
            )
            db.execute(
                conn,
                """
                update control.pack_run set status='superseded'
                 where pack_run_id = (select supersedes from control.pack_run where pack_run_id=%s)
                """,
                (pack_run_id,),
            )

    def fail_pack_run(self, pack_run_id: uuid.UUID) -> None:
        with self.connection() as conn:
            db.execute(
                conn, "update control.pack_run set status='failed' where pack_run_id=%s",
                (pack_run_id,),
            )

    def get_pack_run(self, pack_run_id: uuid.UUID) -> PackRun | None:
        with self.connection() as conn:
            row = db.query_one(
                conn, "select * from control.pack_run where pack_run_id=%s", (pack_run_id,)
            )
        return _pack_run_from_row(row) if row else None

    def list_pack_runs(self, tenant: Tenant | str) -> list[PackRun]:
        tenant = self._resolve(tenant)
        with self.connection() as conn:
            rows = db.query(
                conn,
                "select * from control.pack_run where tenant_id=%s order by system_time desc",
                (tenant.tenant_id,),
            )
        return [_pack_run_from_row(r) for r in rows]

    # -- audit -------------------------------------------------------------

    def log_access(
        self,
        tenant: Tenant | str | None,
        actor: str,
        statement: str,
        actor_kind: str = "human",
        row_count: int | None = None,
        purpose: str | None = None,
    ) -> None:
        tenant_id = self._resolve(tenant).tenant_id if tenant is not None else None
        with self.connection() as conn:
            db.execute(
                conn,
                """
                insert into control.access_log
                  (tenant_id, actor, actor_kind, statement, row_count, purpose)
                values (%s,%s,%s,%s,%s,%s)
                """,
                (tenant_id, actor, actor_kind, statement, row_count, purpose),
            )

    def record_reconciliation(
        self,
        tenant: Tenant | str,
        firm_id: str,
        check_name: str,
        period_start: dt.date,
        period_end: dt.date,
        expected: float | None,
        actual: float | None,
        variance_pct: float | None,
        tolerance_pct: float,
        passed: bool,
        failing_records: Sequence[dict[str, Any]] = (),
    ) -> None:
        tenant = self._resolve(tenant)
        with self.connection() as conn:
            db.execute(
                conn,
                """
                insert into control.reconciliation_result
                  (tenant_id, firm_id, check_name, period, expected, actual,
                   variance_pct, tolerance_pct, passed, failing_records)
                values (%s,%s,%s, daterange(%s,%s,'[)'), %s,%s,%s,%s,%s,%s)
                """,
                (
                    tenant.tenant_id, firm_id, check_name, period_start, period_end,
                    expected, actual, variance_pct, tolerance_pct, passed,
                    Json(list(failing_records)),
                ),
            )

    def reconciliation_results(
        self, tenant: Tenant | str, only_failing: bool = False
    ) -> list[dict[str, Any]]:
        tenant = self._resolve(tenant)
        sql = "select * from control.reconciliation_result where tenant_id=%s"
        if only_failing:
            sql += " and passed = false"
        sql += " order by evaluated_at desc"
        with self.connection() as conn:
            return db.query(conn, sql, (tenant.tenant_id,))

    # -- DSN assembly ------------------------------------------------------

    def tenant_dsn(self, tenant: Tenant | str, role: DbRole = "transform") -> str:
        """Assemble a DSN for one tenant and one role, at call time.

        A suspended or archived tenant is never handed a connection string; that
        is the difference between a contract-ending mistake and an exception.
        """
        tenant = self._resolve(tenant)
        if tenant.status not in CONNECTABLE_STATUSES:
            raise TenantIsolationError(
                f"tenant {tenant.slug!r} has status {tenant.status!r}; refusing to issue a DSN"
            )
        role_user = tenant.role_name(role)
        password = self._role_password(tenant, role)
        return (
            f"host={tenant.db_host} port={self.settings.pg_port} dbname={tenant.db_name} "
            f"user={role_user} password={password}"
        )

    def _role_password(self, tenant: Tenant, role: DbRole) -> str:
        try:
            material = self.secrets.resolve(f"{tenant.s3_prefix}/db/{role}")
            return material["password"]
        except ConfigError:
            # Local/dev: deterministic per (tenant, role), never used in prod
            # because FRACTURE_ENV=prod routes the resolver at Secrets Manager.
            if self.settings.is_production():  # pragma: no cover
                raise
            return f"{tenant.slug}-{role}-local"

    def tenant_connection(self, tenant: Tenant | str, role: DbRole = "transform", autocommit: bool = False):
        tenant = self._resolve(tenant)
        return db.tenant_connection(tenant.slug, self.tenant_dsn(tenant, role), autocommit=autocommit)

    # -- helpers -----------------------------------------------------------

    def _resolve(self, tenant: Tenant | str) -> Tenant:
        return tenant if isinstance(tenant, Tenant) else self.get_tenant(tenant)


def _tenant_from_row(row: dict[str, Any]) -> Tenant:
    return Tenant(
        tenant_id=row["tenant_id"],
        slug=row["slug"],
        legal_name=row["legal_name"],
        status=row["status"],
        motion=row["motion"],
        kms_key_arn=row["kms_key_arn"],
        db_host=row["db_host"],
        db_name=row["db_name"],
        s3_prefix=row["s3_prefix"],
        created_at=row.get("created_at"),
        archive_after=row.get("archive_after"),
        promoted_from=row.get("promoted_from"),
    )


def _pack_run_from_row(row: dict[str, Any]) -> PackRun:
    # Postgres normalises a date range to half-open, so the stored upper bound is
    # the day after the period ends. PackRun.period_end is the inclusive end
    # everywhere in Python; converting here keeps that single meaning, rather
    # than leaving callers to remember which end they were handed.
    period = row["period"]
    return PackRun(
        pack_run_id=row["pack_run_id"],
        tenant_id=row["tenant_id"],
        period_start=period.lower,
        period_end=period.upper - dt.timedelta(days=1),
        system_time=row["system_time"],
        status=row["status"],
        content_hash=bytes(row["content_hash"]) if row["content_hash"] else None,
        issued_at=row["issued_at"],
        supersedes=row["supersedes"],
    )
