"""Migration fan-out (spec section 3.1).

Database-per-tenant costs you this: migrations run N times. A fan-out that
reports success while three tenants are on an old schema is exactly the silent
failure this platform exists to remove, so the threshold is the mechanism.
"""

from __future__ import annotations

import pytest

from fracture.control.migrations import Migrator, MigrationThresholdNotMet
from fracture.control.provisioning import Provisioner
from fracture.core import db
from fracture.core.errors import FractureError
from tests.conftest import _drop, requires_db

pytestmark = [pytest.mark.db, requires_db]

GOOD = "create table if not exists mart.migration_probe (x int);"
BAD = "create table mart.migration_probe (this is not valid sql);"


@pytest.fixture
def two_tenants(control, tenant_slug):
    a = Provisioner(control).provision(
        control.register_tenant(slug=f"a{tenant_slug[1:]}", legal_name="A", motion="operating")
    )
    b = Provisioner(control).provision(
        control.register_tenant(slug=f"b{tenant_slug[1:]}", legal_name="B", motion="operating")
    )
    yield [a, b]
    _drop(control, a)
    _drop(control, b)


def test_rebuild_targets_every_active_tenant(control, two_tenants):
    """The fan-out's scope is the registry, not a caller-supplied list."""
    targets = {t.slug for t in Migrator(control).target_tenants()}
    assert {t.slug for t in two_tenants} <= targets


def test_fan_out_applies_to_every_tenant(control, two_tenants):
    run = Migrator(control).fan_out("probe-1", GOOD, tenants=two_tenants)
    assert run.success_rate == 1.0
    for tenant in two_tenants:
        with control.tenant_connection(tenant, "transform") as conn:
            assert db.table_exists(conn, "mart", "migration_probe")


def test_reapplying_the_same_version_is_a_no_op(control, two_tenants):
    migrator = Migrator(control)
    migrator.fan_out("probe-2", GOOD, tenants=two_tenants)
    again = migrator.fan_out("probe-2", GOOD, tenants=two_tenants)
    assert all(r.skipped for r in again.results)


def test_editing_an_applied_version_is_refused(control, two_tenants):
    """A version applied with different SQL is a fork, not a re-run."""
    migrator = Migrator(control)
    migrator.fan_out("probe-3", GOOD, tenants=two_tenants)
    with pytest.raises(FractureError, match="different checksum"):
        migrator.fan_out(
            "probe-3", GOOD + "\ncreate table if not exists mart.probe2 (y int);",
            tenants=two_tenants,
        )


def test_a_partial_fan_out_still_attempts_every_tenant(control, two_tenants):
    """Stopping at the first failure leaves the estate in an unknown state,
    which is worse than a known-bad one."""
    migrator = Migrator(control)
    # Break the first tenant by pre-creating a conflicting object.
    with control.tenant_connection(two_tenants[0], "transform") as conn:
        db.execute(conn, "create table mart.conflict_probe (x text)")
    sql = "create table mart.conflict_probe (x int);"
    with pytest.raises(MigrationThresholdNotMet):
        migrator.fan_out("probe-4", sql, tenants=two_tenants, success_threshold=1.0)
    with control.tenant_connection(two_tenants[1], "transform") as conn:
        assert db.table_exists(conn, "mart", "conflict_probe"), (
            "the fan-out stopped at the first failure instead of continuing"
        )


def test_the_threshold_is_what_fails_the_run(control, two_tenants):
    migrator = Migrator(control)
    with control.tenant_connection(two_tenants[0], "transform") as conn:
        db.execute(conn, "create table mart.threshold_probe (x text)")
    sql = "create table mart.threshold_probe (x int);"
    # Half the estate migrating is a pass at 0.5 and a failure at 1.0.
    run = migrator.fan_out("probe-5", sql, tenants=two_tenants, success_threshold=0.5)
    assert run.success_rate == 0.5
    assert len(run.failed) == 1
    with pytest.raises(MigrationThresholdNotMet, match="below required threshold"):
        migrator.fan_out("probe-6", sql, tenants=two_tenants, success_threshold=1.0)


def test_failures_are_recorded_per_tenant(control, two_tenants):
    migrator = Migrator(control)
    with control.tenant_connection(two_tenants[0], "transform") as conn:
        db.execute(conn, "create table mart.recorded_probe (x text)")
    with pytest.raises(MigrationThresholdNotMet):
        migrator.fan_out(
            "probe-7", "create table mart.recorded_probe (x int);", tenants=two_tenants
        )
    with control.connection() as conn:
        rows = db.query(
            conn,
            "select succeeded, error from control.tenant_migration where version = 'probe-7' "
            "order by succeeded",
        )
    assert len(rows) == 2
    assert rows[0]["succeeded"] is False
    assert rows[0]["error"]
    assert rows[1]["succeeded"] is True


def test_an_unreachable_tenant_fails_alone_rather_than_aborting_the_estate(control, two_tenants):
    """A half-provisioned tenant, or one whose credentials no longer work, must
    not block every migration the estate ever runs again."""
    from fracture.control.models import Tenant

    ghost = Tenant(
        tenant_id=two_tenants[0].tenant_id,
        slug="ghost-tenant",
        legal_name="Ghost",
        status="provisioning",
        motion="operating",
        kms_key_arn="arn:local:ghost",
        db_host=control.settings.pg_host,
        db_name="tenant_does_not_exist",
        s3_prefix="tenants/ghost-tenant",
    )
    run = Migrator(control).fan_out(
        "probe-ghost", GOOD, tenants=[ghost] + two_tenants, success_threshold=0.6
    )
    assert len(run.failed) == 1
    assert run.failed[0].slug == "ghost-tenant"
    for tenant in two_tenants:
        with control.tenant_connection(tenant, "transform") as conn:
            assert db.table_exists(conn, "mart", "migration_probe"), (
                "an unreachable tenant stopped the healthy ones from migrating"
            )


def test_rebuilding_the_schema_everywhere_is_idempotent(control, two_tenants):
    """Every DDL script is create-if-not-exists or create-or-replace, so a
    re-apply repairs drift rather than destroying it."""
    from fracture.canon.schema import ddl_checksum, tenant_ddl_scripts

    migrator = Migrator(control)
    ddl = "\n".join(sql for _, sql in tenant_ddl_scripts())
    version = f"ddl-{ddl_checksum().hex()[:12]}"
    # Scoped to this test's own tenants. `rebuild_schema_everywhere` fans out
    # over the whole registry by design, and asserting a clean sweep of every
    # tenant in a shared development database would make this a test about the
    # environment rather than about idempotency.
    first = migrator.fan_out(version, ddl, tenants=two_tenants)
    assert first.success_rate == 1.0
    # Dropped as `owner`: transform has DML on lineage but does not own the
    # tables, which is the grant model working rather than a test convenience.
    for tenant in two_tenants:
        with control.tenant_connection(tenant, "owner") as conn:
            db.execute(conn, "drop table if exists lineage.mart_edge cascade")
    second = migrator.fan_out(version + "-repair", ddl, tenants=two_tenants)
    assert second.success_rate == 1.0
    for tenant in two_tenants:
        with control.tenant_connection(tenant, "transform") as conn:
            assert db.table_exists(conn, "lineage", "mart_edge")
