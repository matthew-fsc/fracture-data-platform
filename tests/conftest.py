"""Shared fixtures.

Tests that need a database are marked `db` and skipped with a clear message when
no Postgres is reachable, rather than failing with a connection error that looks
like a code bug.
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("FRACTURE_PG_HOST", "127.0.0.1")
os.environ.setdefault("FRACTURE_ENV", "test")


def _database_available() -> bool:
    import psycopg2

    from fracture.core.config import Settings

    try:
        conn = psycopg2.connect(Settings().control_dsn, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


DB_AVAILABLE = _database_available()
requires_db = pytest.mark.skipif(
    not DB_AVAILABLE,
    reason="no Postgres reachable at FRACTURE_PG_HOST; run `make db-up` or docker compose up",
)


@pytest.fixture(scope="session")
def artifact_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("artifacts")
    os.environ["FRACTURE_ARTIFACT_ROOT"] = str(root)
    return root


@pytest.fixture(scope="session")
def secret_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("secrets")
    os.environ["FRACTURE_SECRET_ROOT"] = str(root)
    return root


@pytest.fixture(scope="session")
def control(artifact_root, secret_root):
    if not DB_AVAILABLE:
        pytest.skip("no Postgres reachable")
    from fracture.control.registry import ControlPlane
    from fracture.core.secrets import EnvSecretResolver

    cp = ControlPlane(secret_resolver=EnvSecretResolver(secret_root))
    cp.install_schema()
    return cp


@pytest.fixture
def tenant_slug() -> str:
    """A fresh slug per test, so tests never share a database."""
    return f"t{uuid.uuid4().hex[:12]}"


@pytest.fixture
def fresh_tenant(control, tenant_slug):
    """A provisioned, empty tenant, dropped afterwards."""
    from fracture.control.provisioning import Provisioner

    tenant = control.register_tenant(
        slug=tenant_slug, legal_name="Fixture Tenant LLC", motion="operating"
    )
    provisioner = Provisioner(control)
    tenant = provisioner.provision(tenant)
    yield tenant
    _drop(control, tenant)


@pytest.fixture(scope="session")
def loaded_estate(control, artifact_root, secret_root):
    """One small estate, loaded once and shared by the read-only tests.

    Session-scoped because loading is the expensive part; every test that
    mutates uses `fresh_tenant` instead.
    """
    from dataclasses import replace

    from fracture.synth.config import TEST_ESTATE
    from fracture.synth.generator import EstateGenerator
    from fracture.synth.load import load_estate

    slug = f"e{uuid.uuid4().hex[:12]}"
    spec = replace(TEST_ESTATE, tenant_slug=slug)
    root = Path(tempfile.mkdtemp(prefix="synth-"))
    estate = EstateGenerator(spec, root).generate()
    tenant, report = load_estate(estate, control=control)
    yield {"tenant": tenant, "estate": estate, "report": report, "spec": spec}
    _drop(control, tenant)


@pytest.fixture(scope="session")
def built_marts(control, loaded_estate):
    """The loaded estate with marts built at a fixed system time."""
    from fracture.marts.runner import MartRunner

    # After the load, not a fixed past date: canon rows are recorded_at load
    # time, so a system time before that reads an empty database. The mart
    # runner's non-empty assertions catch it, which is how this was found.
    system_time = dt.datetime.now(dt.timezone.utc)
    with control.tenant_connection(loaded_estate["tenant"], "transform") as conn:
        MartRunner().run(conn, system_time)
    return {**loaded_estate, "system_time": system_time}


def _drop(control, tenant) -> None:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    from fracture.control.provisioning import ROLES
    from fracture.core import db

    conn = psycopg2.connect(control.settings.control_dsn)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
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
    with control.connection() as c:
        db.execute(c, "delete from control.tenant where slug = %s", (tenant.slug,))
