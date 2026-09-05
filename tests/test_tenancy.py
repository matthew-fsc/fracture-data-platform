"""Tenancy, isolation and the role model (spec section 3).

These tests exist because the one-pager promises isolation enforced at the
database rather than in application code. A promise like that has to be
executable.
"""

from __future__ import annotations

import datetime as dt

import psycopg2
import pytest

from fracture.core import db
from fracture.core.errors import ConfigError, TenantIsolationError
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]


def test_slug_must_be_dns_safe(control):
    with pytest.raises(Exception):
        control.register_tenant(
            slug="Not A Slug", legal_name="Bad", motion="operating"
        )


def test_diligence_tenant_must_carry_an_archive_date(control):
    """An ephemeral tenant with no destruction date quietly becomes permanent."""
    with pytest.raises(ConfigError, match="archive_after"):
        control.register_tenant(
            slug="d" + "x" * 12, legal_name="Diligence Co", motion="diligence"
        )


def test_diligence_tenant_promotes_in_place(control, tenant_slug):
    """Promotion must be a supported path, not a rebuild (spec section 13)."""
    from fracture.control.provisioning import Provisioner
    from tests.conftest import _drop

    tenant = control.register_tenant(
        slug=tenant_slug, legal_name="Target Co", motion="diligence",
        archive_after=dt.date.today() + dt.timedelta(days=30),
    )
    tenant = Provisioner(control).provision(tenant)
    original_db = tenant.db_name
    try:
        promoted = control.promote_tenant(tenant_slug)
        assert promoted.motion == "operating"
        assert promoted.archive_after is None
        assert promoted.db_name == original_db, "promotion rebuilt the database"
    finally:
        _drop(control, control.get_tenant(tenant_slug))


def test_exactly_one_platform_firm_per_tenant(control, fresh_tenant):
    """Two platform firms means an ambiguous consolidation root."""
    control.add_firm(fresh_tenant, "F1", "Platform", "platform")
    with pytest.raises(psycopg2.Error):
        control.add_firm(fresh_tenant, "F2", "Also Platform", "platform")


def test_source_cannot_go_live_without_read_only_proof(control, fresh_tenant):
    """The record you produce when a client asks how you know you could not have
    modified their systems (spec section 3.2)."""
    control.add_firm(fresh_tenant, "F1", "Platform", "platform")
    control.register_source(fresh_tenant, "F1", "orion", "secret/path")
    with control.connection() as conn:
        with pytest.raises(psycopg2.Error):
            db.execute(
                conn,
                "update control.tenant_source set status='live' "
                "where tenant_id=%s and firm_id='F1' and source_id='orion'",
                (fresh_tenant.tenant_id,),
            )


def test_read_only_proof_then_live_is_allowed(control, fresh_tenant):
    control.add_firm(fresh_tenant, "F1", "Platform", "platform")
    control.register_source(fresh_tenant, "F1", "orion", "secret/path")
    control.verify_read_only(fresh_tenant, "F1", "orion", verified_by="matthew")
    control.activate_source(fresh_tenant, "F1", "orion")
    source = control.list_sources(fresh_tenant, "F1")[0]
    assert source.status == "live"
    assert source.verified_read_only_at is not None
    assert source.verified_by == "matthew"


def test_two_tenants_cannot_be_open_at_once(control, fresh_tenant):
    """Spec section 7: no code path holds two tenants' credentials at once."""
    with control.tenant_connection(fresh_tenant, "transform"):
        with pytest.raises(TenantIsolationError, match="refusing to open"):
            with db.tenant_connection("other-tenant", control.settings.control_dsn):
                pass


def test_same_tenant_nests_fine(control, fresh_tenant):
    with control.tenant_connection(fresh_tenant, "transform"):
        with control.tenant_connection(fresh_tenant, "transform") as inner:
            assert db.scalar(inner, "select 1") == 1


def test_suspended_tenant_is_not_handed_a_dsn(control, fresh_tenant):
    control.set_tenant_status(fresh_tenant.slug, "suspended")
    try:
        with pytest.raises(TenantIsolationError, match="refusing to issue a DSN"):
            control.tenant_dsn(control.get_tenant(fresh_tenant.slug))
    finally:
        control.set_tenant_status(fresh_tenant.slug, "active")


# -- the role model ----------------------------------------------------------


def test_loader_cannot_update_or_delete_raw(control, fresh_tenant):
    """Append-only is a grant, not a convention."""
    from fracture.control.provisioning import ensure_streams

    ensure_streams(control, fresh_tenant, [("orion", "accounts")])
    with control.tenant_connection(fresh_tenant, "loader") as conn:
        db.execute(
            conn,
            "insert into raw.orion__accounts "
            "(_load_id, _sequence, _firm_id, _source_id, _extracted_at, _artifact_uri, "
            " _record_hash, _payload) "
            "values (gen_random_uuid(), 1, 'F1', 'orion', now(), 's3://x', '\\x00', '{}')",
        )
    with control.tenant_connection(fresh_tenant, "loader") as conn:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            db.execute(conn, "update raw.orion__accounts set _firm_id = 'F2'")
    with control.tenant_connection(fresh_tenant, "loader") as conn:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            db.execute(conn, "delete from raw.orion__accounts")


def test_transform_cannot_delete_raw(control, fresh_tenant):
    from fracture.control.provisioning import ensure_streams

    ensure_streams(control, fresh_tenant, [("orion", "positions")])
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        assert db.scalar(conn, "select count(*) from raw.orion__positions") == 0
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            db.execute(conn, "delete from raw.orion__positions")


def test_loader_cannot_create_tables(control, fresh_tenant):
    """Raw DDL is a migration. A loader that can create tables can silently
    start writing to a differently-named one."""
    with control.tenant_connection(fresh_tenant, "loader") as conn:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            db.execute(conn, "create table raw.sneaky (x int)")


def test_reader_sees_marts_but_not_raw_or_canon(control, fresh_tenant):
    """The reader role is what a drill-through UI or a BI tool would hold."""
    with control.tenant_connection(fresh_tenant, "reader") as conn:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            db.query(conn, "select * from canon.party limit 1")
    with control.tenant_connection(fresh_tenant, "reader") as conn:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            db.query(conn, "select * from raw._load limit 1")
    with control.tenant_connection(fresh_tenant, "reader") as conn:
        assert db.query(conn, "select * from lineage.edge limit 1") == []


def test_a_tenant_role_cannot_connect_to_another_tenant(control, fresh_tenant, tenant_slug):
    """The literal implementation of database-level isolation."""
    from fracture.control.provisioning import Provisioner
    from tests.conftest import _drop

    other = control.register_tenant(
        slug=f"o{tenant_slug[1:]}", legal_name="Other Acquirer", motion="operating"
    )
    other = Provisioner(control).provision(other)
    try:
        dsn = control.tenant_dsn(fresh_tenant, "transform")
        cross = dsn.replace(f"dbname={fresh_tenant.db_name}", f"dbname={other.db_name}")
        with pytest.raises(psycopg2.OperationalError):
            psycopg2.connect(cross).close()
    finally:
        _drop(control, other)


def test_control_plane_has_no_cross_database_extensions(control):
    """postgres_fdw and dblink are not installed anywhere (spec section 3.2).

    A convenient cross-database join is how a database-per-tenant guarantee
    becomes a claim rather than a control.
    """
    with control.connection() as conn:
        installed = {
            r["extname"] for r in db.query(conn, "select extname from pg_extension")
        }
    assert "postgres_fdw" not in installed
    assert "dblink" not in installed


def test_tenant_database_has_no_cross_database_extensions(control, fresh_tenant):
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        installed = {
            r["extname"] for r in db.query(conn, "select extname from pg_extension")
        }
    assert "postgres_fdw" not in installed
    assert "dblink" not in installed


def test_export_is_one_shell_command(control, fresh_tenant):
    """Contractual obligation satisfied by a shell command (spec section 3.1)."""
    from fracture.control.provisioning import Provisioner

    command = Provisioner(control).export_command(fresh_tenant)
    assert command[0] == "pg_dump"
    assert fresh_tenant.db_name in " ".join(command)


def test_operating_tenant_database_is_not_droppable(control, fresh_tenant):
    from fracture.control.provisioning import Provisioner

    with pytest.raises(ValueError, match="refusing to drop"):
        Provisioner(control).archive(fresh_tenant, drop_database=True)


def test_terraform_and_python_agree_on_the_role_list():
    """Both create the four roles; a drift between them is a silent grant gap."""
    import re
    from pathlib import Path

    from fracture.control.provisioning import ROLES

    tf = Path("infra/terraform/modules/tenant/main.tf").read_text()
    match = re.search(r"roles\s*=\s*\[(.*?)\]", tf, re.S)
    assert match, "tenant module no longer declares local.roles"
    tf_roles = tuple(r.strip().strip('"') for r in match.group(1).split(","))
    assert tf_roles == ROLES, (
        f"Terraform declares {tf_roles} but provisioning.ROLES is {ROLES}"
    )
