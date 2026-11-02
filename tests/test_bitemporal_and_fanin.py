"""System time, business time, and fan-in across sources (spec sections 6.3, 7).

The commercial claim being tested: a board pack issued in March and a restated
figure in June are both reproducible and explainable. That only holds if a
restatement closes the old row rather than overwriting it, and if two sources
disagreeing produces a finding rather than whichever ran last.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from fracture.adapters.base import CanonicalRecord
from fracture.canon.bitemporal import AsOf, assert_temporal_filter
from fracture.canon.writer import CanonWriter
from fracture.core import db
from fracture.core.errors import FractureError
from fracture.core.timeutil import freeze_system_time, utcnow
from fracture.ingest.lineage import LineageWriter, RawRef
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

T1 = dt.datetime(2026, 3, 31, 12, 0, tzinfo=dt.timezone.utc)
T2 = dt.datetime(2026, 6, 30, 12, 0, tzinfo=dt.timezone.utc)


def _seed_load(control, tenant, source_id="orion", stream="positions"):
    """A raw load row so lineage references resolve."""
    import uuid

    from fracture.control.provisioning import ensure_streams

    ensure_streams(control, tenant, [(source_id, stream)])
    load_id = uuid.uuid4()
    with control.tenant_connection(tenant, "owner") as conn:
        db.execute(
            conn,
            "insert into raw._load (load_id, firm_id, source_id, stream, extracted_at, "
            "artifact_uri, artifact_sha256, row_count) "
            "values (%s,'F1',%s,%s, now(), 's3://fixture', '\\x00', 1)",
            (load_id, source_id, stream),
        )
    return RawRef(load_id=load_id, sequence=1)


def _balance(ref, value: str, source_id: str = "orion") -> CanonicalRecord:
    return CanonicalRecord(
        entity="balance_snapshot",
        natural_key="A-1|2026-03-31",
        firm_id="F1",
        values={
            "account_id": "A-1",
            "as_of_date": dt.date(2026, 3, 31),
            "market_value": Decimal(value),
            "cash_value": Decimal("0"),
        },
        refs=(ref,),
        source_id=source_id,
        valid_from=dt.date(2026, 3, 31),
        valid_to=dt.date(2026, 4, 1),
    )


def test_restatement_closes_the_old_row_and_both_are_readable(control, fresh_tenant):
    """The core bitemporal claim: March reproduces at T1, June reads the
    restated figure at T2, and neither destroys the other."""
    ref = _seed_load(control, fresh_tenant)

    with control.tenant_connection(fresh_tenant, "transform") as conn:
        with LineageWriter(conn) as lineage:
            CanonWriter(conn, lineage, system_time=T1).write([_balance(ref, "1000000")])
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        with LineageWriter(conn) as lineage:
            CanonWriter(conn, lineage, system_time=T2).write([_balance(ref, "1150000")])

    with control.tenant_connection(fresh_tenant, "transform") as conn:
        rows = db.query(
            conn,
            "select market_value, recorded_at, superseded_at from canon.balance_snapshot "
            "order by recorded_at",
        )
        assert len(rows) == 2, "a restatement overwrote the original instead of closing it"
        assert rows[0]["superseded_at"] == T2
        assert rows[1]["superseded_at"] is None

        as_of_march = AsOf(system_time=T1)
        march = db.query(
            conn,
            "select market_value from canon.balance_snapshot "
            f"where {as_of_march.predicate(include_business_time=False)}",
            as_of_march.params(),
        )
        assert [r["market_value"] for r in march] == [Decimal("1000000.0000")]

        as_of_june = AsOf(system_time=T2)
        june = db.query(
            conn,
            "select market_value from canon.balance_snapshot "
            f"where {as_of_june.predicate(include_business_time=False)}",
            as_of_june.params(),
        )
        assert [r["market_value"] for r in june] == [Decimal("1150000.0000")]


def test_unchanged_facts_do_not_create_a_new_version(control, fresh_tenant):
    """Re-running an unchanged source must not churn system time, or every
    refresh produces a restatement of nothing."""
    ref = _seed_load(control, fresh_tenant)
    for system_time in (T1, T2):
        with control.tenant_connection(fresh_tenant, "transform") as conn:
            with LineageWriter(conn) as lineage:
                CanonWriter(conn, lineage, system_time=system_time).write(
                    [_balance(ref, "1000000")]
                )
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        assert db.scalar(conn, "select count(*) from canon.balance_snapshot") == 1
        # Lineage still records the second confirmation.
        assert db.scalar(
            conn,
            "select count(*) from lineage.edge where target_table = 'canon.balance_snapshot'",
        ) == 2


def test_lower_authority_source_does_not_overwrite(control, fresh_tenant):
    """The custodian is the record of AUM; the portfolio system is not."""
    ref = _seed_load(control, fresh_tenant)
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        with LineageWriter(conn) as lineage:
            CanonWriter(conn, lineage, system_time=T1).write(
                [_balance(ref, "1000000", source_id="schwab_custodian")]
            )
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        with LineageWriter(conn) as lineage:
            stats = CanonWriter(conn, lineage, system_time=T2).write(
                [_balance(ref, "1200000", source_id="orion")]
            )
    assert stats.deferred == 1
    assert stats.variances == 1
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        current = db.query_one(
            conn,
            "select market_value, source_id from canon.balance_snapshot "
            "where superseded_at is null",
        )
        assert current["source_id"] == "schwab_custodian"
        assert current["market_value"] == Decimal("1000000.0000")
        variance = db.query_one(conn, "select * from recon.source_variance")
        assert variance["authoritative_source"] == "schwab_custodian"
        assert variance["deferred_source"] == "orion"
        assert variance["detail"][0]["column"] == "market_value"


def test_higher_authority_source_supersedes_and_still_raises_a_variance(control, fresh_tenant):
    """Recording the disagreement only when the loser arrives second would make
    the finding depend on asset execution order."""
    ref = _seed_load(control, fresh_tenant)
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        with LineageWriter(conn) as lineage:
            CanonWriter(conn, lineage, system_time=T1).write(
                [_balance(ref, "1200000", source_id="orion")]
            )
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        with LineageWriter(conn) as lineage:
            stats = CanonWriter(conn, lineage, system_time=T2).write(
                [_balance(ref, "1000000", source_id="schwab_custodian")]
            )
    assert stats.superseded == 1
    assert stats.variances == 1
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        current = db.query_one(
            conn,
            "select market_value, source_id from canon.balance_snapshot "
            "where superseded_at is null",
        )
        assert current["source_id"] == "schwab_custodian"


def test_a_partial_source_does_not_null_out_a_richer_row(control, fresh_tenant):
    """The Schwab file knows an account's value and nothing about its household.
    Letting its nulls through would detach every account from its household."""
    ref = _seed_load(control, fresh_tenant, source_id="orion", stream="accounts")
    rich = CanonicalRecord(
        entity="account", natural_key="A-1", firm_id="F1",
        values={
            "account_id": "A-1", "account_type": "custodial", "household_id": "H-1",
            "party_id": "P-1", "custodian": "schwab", "status": "open", "billable": False,
        },
        refs=(ref,), source_id="orion", valid_from=dt.date(2020, 1, 1),
    )
    partial = CanonicalRecord(
        entity="account", natural_key="A-1", firm_id="F1",
        values={
            "account_id": "A-1", "account_type": "custodial", "household_id": None,
            "party_id": None, "custodian": "schwab", "status": "closed",
            "closed_on": dt.date(2026, 5, 1), "billable": None,
        },
        refs=(ref,), source_id="schwab_custodian", valid_from=dt.date(2020, 1, 1),
    )
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        with LineageWriter(conn) as lineage:
            CanonWriter(conn, lineage, system_time=T1).write([rich])
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        with LineageWriter(conn) as lineage:
            CanonWriter(conn, lineage, system_time=T2).write([partial])

    with control.tenant_connection(fresh_tenant, "transform") as conn:
        current = db.query_one(
            conn, "select * from canon.account where superseded_at is null"
        )
    assert current["household_id"] == "H-1", "the custodian file wiped the household link"
    assert current["party_id"] == "P-1"
    assert current["billable"] is False, "the custodian overwrote a flag it cannot know"
    assert current["status"] == "closed", "the custodian's own knowledge did not apply"
    assert current["closed_on"] == dt.date(2026, 5, 1)


def test_duplicate_keys_within_one_batch_become_one_row(control, fresh_tenant):
    """Orion reports a household once per account. That is one fact seen twice."""
    ref = _seed_load(control, fresh_tenant, stream="households")
    records = [
        CanonicalRecord(
            entity="household", natural_key="H-1", firm_id="F1",
            values={"household_id": "H-1", "name": "The Okafor Household"},
            refs=(ref,), source_id="orion", valid_from=dt.date(2020, 1, 1),
        )
        for _ in range(4)
    ]
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        with LineageWriter(conn) as lineage:
            CanonWriter(conn, lineage, system_time=T1).write(records)
        assert db.scalar(conn, "select count(*) from canon.household") == 1
        # All four references still resolve to the single row.
        assert db.scalar(
            conn, "select count(*) from lineage.edge where target_table='canon.household'"
        ) == 4


def test_the_database_refuses_two_open_rows_for_one_fact(control, fresh_tenant):
    """The backstop under the mapping layer's deduplication."""
    import psycopg2

    with control.tenant_connection(fresh_tenant, "transform") as conn:
        db.execute(
            conn,
            "insert into canon.household (firm_id, household_id, name, valid_from) "
            "values ('F1','H-1','First','2020-01-01')",
        )
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        with pytest.raises(psycopg2.errors.UniqueViolation):
            db.execute(
                conn,
                "insert into canon.household (firm_id, household_id, name, valid_from) "
                "values ('F1','H-1','Duplicate','2020-01-01')",
            )


# -- the read guards ---------------------------------------------------------


def test_sql_reading_canon_without_a_time_filter_is_refused():
    with pytest.raises(FractureError, match="system-time filter"):
        assert_temporal_filter("select market_value from canon.balance_snapshot")


def test_sql_with_the_as_of_predicate_is_allowed():
    assert_temporal_filter(
        "select market_value from canon.balance_snapshot "
        "where recorded_at <= %(system_time)s"
    )


def test_sql_reading_a_current_view_is_allowed():
    assert_temporal_filter("select * from canon.v_household_current")


def test_every_mart_model_constrains_system_time():
    """A mart that reads superseded rows double-counts a restatement."""
    from fracture.marts.runner import load_models

    for model in load_models():
        assert_temporal_filter(model.sql)


def test_nested_freeze_at_a_different_instant_is_an_error():
    """Two frozen instants in one call stack means one of them is producing
    numbers nobody can reproduce."""
    with freeze_system_time(T1):
        assert utcnow() == T1
        with freeze_system_time(T1):
            assert utcnow() == T1
        with pytest.raises(RuntimeError, match="already frozen"):
            with freeze_system_time(T2):
                pass


def test_freeze_requires_an_aware_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        with freeze_system_time(dt.datetime(2026, 1, 1)):
            pass
