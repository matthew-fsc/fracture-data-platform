"""Tenant standup.

Database-per-tenant (spec section 3.1). This module is the Python side of the
`tenant` Terraform module: it creates the database, the four roles, the schema
layout and the grants. Terraform owns the KMS key, the S3 prefix and the secret
paths; both call the same role and grant definitions so they cannot drift.
"""

from __future__ import annotations

import datetime as dt
from typing import Iterable

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from psycopg2.extensions import connection as PGConnection

from fracture.core import db
from fracture.core.logging import get_logger
from fracture.control.models import DbRole, Tenant
from fracture.control.registry import ControlPlane

log = get_logger("control.provisioning")

ROLES: tuple[DbRole, ...] = ("owner", "loader", "transform", "reader")

#: Grants per role (spec section 3.3). Read this table as the security control
#: it is: `loader` has no UPDATE and no DELETE anywhere, ever.
ROLE_GRANTS: dict[DbRole, tuple[str, ...]] = {
    "owner": (
        "grant all on schema raw, stg, canon, mart, pack, lineage, ai, recon to {role}",
        "grant all on all tables in schema raw, stg, canon, mart, pack, lineage, ai, recon to {role}",
        "grant all on all sequences in schema raw, stg, canon, mart, pack, lineage, ai, recon to {role}",
    ),
    "loader": (
        "grant usage on schema raw, lineage to {role}",
        "grant insert, select on all tables in schema raw to {role}",
        "grant insert, select on all tables in schema lineage to {role}",
        "grant usage, select on all sequences in schema raw, lineage to {role}",
    ),
    "transform": (
        "grant usage on schema raw to {role}",
        # CREATE on the modelled schemas: materialising a mart is DDL, and
        # spec 3.3 gives transform full DML there. It has no CREATE on `raw`,
        # which is what keeps the evidence layer owner-only and append-only.
        "grant usage, create on schema stg, canon, mart, pack, lineage, ai, recon to {role}",
        "grant select on all tables in schema raw to {role}",
        "grant select, insert, update, delete on all tables in "
        "schema stg, canon, mart, pack, lineage, ai, recon to {role}",
        "grant usage, select on all sequences in "
        "schema stg, canon, mart, pack, lineage, ai, recon to {role}",
        "grant execute on all functions in schema canon, ai to {role}",
    ),
    "reader": (
        "grant usage on schema mart, pack, lineage to {role}",
        "grant select on all tables in schema mart, pack, lineage to {role}",
    ),
}

#: Applied so that objects created later by `owner` inherit the same grants.
DEFAULT_PRIVILEGES: dict[DbRole, tuple[str, ...]] = {
    "loader": (
        "alter default privileges in schema raw grant insert, select on tables to {role}",
        "alter default privileges in schema lineage grant insert, select on tables to {role}",
        "alter default privileges in schema raw, lineage grant usage, select on sequences to {role}",
    ),
    "transform": (
        "alter default privileges in schema raw grant select on tables to {role}",
        "alter default privileges in schema stg, canon, mart, pack, lineage, ai, recon "
        "grant select, insert, update, delete on tables to {role}",
        "alter default privileges in schema stg, canon, mart, pack, lineage, ai, recon "
        "grant usage, select on sequences to {role}",
    ),
    "reader": (
        "alter default privileges in schema mart, pack, lineage grant select on tables to {role}",
    ),
}


class Provisioner:
    """Creates and tears down tenant databases."""

    def __init__(self, control: ControlPlane) -> None:
        self.control = control

    # -- standup -----------------------------------------------------------

    def provision(self, tenant: Tenant, install_schema: bool = True) -> Tenant:
        """Create the database, roles, schemas and grants for one tenant.

        Roles are created before the database so the database can be handed to
        `t_<slug>_owner` immediately. That matters: spec 3.3 makes `owner` the
        DDL role used by migrations, and a role that owns none of the objects it
        is meant to migrate cannot alter or drop any of them.

        Idempotent: re-running against an existing tenant repairs missing roles
        and grants rather than failing.
        """
        self._create_roles(tenant)
        self._create_database(tenant)
        self._grant_connect(tenant)
        if install_schema:
            self.install_tenant_schema(tenant)
        self._apply_grants(tenant)
        self._revoke_public(tenant)
        self.control.set_tenant_status(tenant.slug, "active")
        log.info("provisioned tenant %s (db=%s)", tenant.slug, tenant.db_name)
        return self.control.get_tenant(tenant.slug)

    def _admin_connection(self) -> PGConnection:
        conn = psycopg2.connect(self.control.settings.control_dsn)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        return conn

    def _create_database(self, tenant: Tenant) -> None:
        conn = self._admin_connection()
        try:
            with conn.cursor() as cur:
                owner = tenant.role_name("owner")
                cur.execute("select 1 from pg_database where datname = %s", (tenant.db_name,))
                if not cur.fetchone():
                    # Identifier interpolation: db_name is derived from the slug,
                    # which the control plane constrains to
                    # ^[a-z][a-z0-9-]+[a-z0-9]$.
                    cur.execute(f'create database "{tenant.db_name}" owner "{owner}"')
                else:
                    cur.execute(f'alter database "{tenant.db_name}" owner to "{owner}"')
        finally:
            conn.close()

    def _create_roles(self, tenant: Tenant) -> None:
        conn = self._admin_connection()
        try:
            with conn.cursor() as cur:
                for role in ROLES:
                    name = tenant.role_name(role)
                    password = self.control._role_password(tenant, role)
                    cur.execute("select 1 from pg_roles where rolname = %s", (name,))
                    if cur.fetchone():
                        cur.execute(f"alter role \"{name}\" with login password %s", (password,))
                    else:
                        cur.execute(f"create role \"{name}\" with login password %s", (password,))
        # CONNECT is granted in `_revoke_public`, after the database exists.
        # Tenant isolation is not a convention: every role is denied CONNECT on
        # every other tenant's database, because PUBLIC connect is revoked there.
        finally:
            conn.close()

    def _grant_connect(self, tenant: Tenant) -> None:
        """CONNECT for this tenant's four roles, and nobody else's."""
        conn = self._admin_connection()
        try:
            with conn.cursor() as cur:
                for role in ROLES:
                    cur.execute(
                        f'grant connect on database "{tenant.db_name}" '
                        f'to "{tenant.role_name(role)}"'
                    )
        finally:
            conn.close()

    def install_tenant_schema(self, tenant: Tenant) -> None:
        """Applied as `owner`, so every object it creates it also owns."""
        from fracture.canon.schema import tenant_ddl_scripts

        dsn = self.control.tenant_dsn(tenant, "owner")
        with db.connect(dsn) as conn:
            for name, sql in tenant_ddl_scripts():
                log.debug("applying %s to %s", name, tenant.slug)
                db.run_script(conn, sql)

    def _apply_grants(self, tenant: Tenant) -> None:
        # Granted by `owner`: only the object owner can grant on its objects, and
        # doing it as superuser would work today and silently stop working the
        # moment the platform runs without one.
        dsn = self.control.tenant_dsn(tenant, "owner")
        with db.connect(dsn) as conn:
            with conn.cursor() as cur:
                for role in ROLES:
                    name = tenant.role_name(role)
                    for stmt in ROLE_GRANTS[role]:
                        cur.execute(stmt.format(role=f'"{name}"'))
                    for stmt in DEFAULT_PRIVILEGES.get(role, ()):
                        cur.execute(stmt.format(role=f'"{name}"'))

    def _revoke_public(self, tenant: Tenant) -> None:
        """Remove PUBLIC's ability to connect, and remove the transform role's
        ability to write to raw. Append-only means append-only."""
        admin = self._admin_connection()
        try:
            with admin.cursor() as cur:
                cur.execute(f'revoke all on database "{tenant.db_name}" from public')
                for role in ROLES:
                    cur.execute(
                        f'grant connect on database "{tenant.db_name}" to "{tenant.role_name(role)}"'
                    )
        finally:
            admin.close()

        dsn = self.control.tenant_dsn(tenant, "owner")
        with db.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("revoke all on schema public from public")
            loader = tenant.role_name("loader")
            transform = tenant.role_name("transform")
            reader = tenant.role_name("reader")
            # raw is append-only for everyone but the owner.
            cur.execute(f'revoke update, delete, truncate on all tables in schema raw from "{loader}"')
            cur.execute(f'revoke update, delete, truncate on all tables in schema raw from "{transform}"')
            cur.execute(
                "alter default privileges in schema raw "
                f'revoke update, delete, truncate on tables from "{transform}"'
            )
            # reader never sees raw payloads or canonical PII directly.
            cur.execute(f'revoke all on schema raw, stg, canon, ai from "{reader}"')

    # -- teardown ----------------------------------------------------------

    def archive(self, tenant: Tenant, drop_database: bool = False) -> None:
        """Archive a tenant. Diligence tenants are destroyed on a no-deal
        (spec section 13); operating tenants are only ever suspended."""
        if drop_database and tenant.motion != "diligence":
            raise ValueError(
                f"refusing to drop the database of an operating tenant ({tenant.slug}); "
                "suspend it or export it with pg_dump first"
            )
        self.control.set_tenant_status(tenant.slug, "archived")
        if not drop_database:
            return
        conn = self._admin_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "select pg_terminate_backend(pid) from pg_stat_activity where datname = %s",
                    (tenant.db_name,),
                )
                cur.execute(f'drop database if exists "{tenant.db_name}"')
                for role in ROLES:
                    cur.execute(f'drop role if exists "{tenant.role_name(role)}"')
        finally:
            conn.close()
        log.info("archived and dropped tenant %s", tenant.slug)

    def export_command(self, tenant: Tenant) -> list[str]:
        """Contractual full export (spec section 3.1). One shell command."""
        return [
            "pg_dump",
            "--no-owner",
            "--format=custom",
            f"--dbname=postgresql://{tenant.db_host}/{tenant.db_name}",
            f"--file={tenant.slug}-export.dump",
        ]


def standup_tenant(
    control: ControlPlane,
    slug: str,
    legal_name: str,
    motion: str,
    firms: Iterable[tuple[str, str, str]] = (),
    archive_after: dt.date | None = None,
) -> Tenant:
    """Register plus provision, in one call. `firms` is (firm_id, name, role)."""
    if motion == "diligence" and archive_after is None:
        archive_after = dt.date.today() + dt.timedelta(days=30)
    tenant = control.register_tenant(
        slug=slug, legal_name=legal_name, motion=motion, archive_after=archive_after
    )
    tenant = Provisioner(control).provision(tenant)
    for firm_id, firm_name, role in firms:
        control.add_firm(tenant, firm_id, firm_name, role)
    return tenant


def ensure_streams(
    control: ControlPlane,
    tenant: Tenant,
    streams: Iterable[tuple[str, str]],
) -> list[str]:
    """Create the raw tables for (source_id, stream) pairs, as `owner`.

    Raw DDL is a migration step, not something the loader does at run time
    (see `fracture.ingest.raw.require_raw_table` for why).
    """
    from fracture.ingest.raw import ensure_raw_table

    created: list[str] = []
    dsn = control.tenant_dsn(tenant, "owner")
    with db.tenant_connection(tenant.slug, dsn) as conn:
        for source_id, stream in streams:
            created.append(ensure_raw_table(conn, source_id, stream))
        # Newly created tables need the standing grants; ALTER DEFAULT
        # PRIVILEGES only covers objects created by the role that set them.
        with conn.cursor() as cur:
            loader = tenant.role_name("loader")
            transform = tenant.role_name("transform")
            cur.execute(f'grant insert, select on all tables in schema raw to "{loader}"')
            cur.execute(f'grant select on all tables in schema raw to "{transform}"')
    return created
