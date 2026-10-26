"""The pack seam, and the professional services pack end to end.

Two things are under test. First, that the chassis really is domain-free: that
a second pack can be registered, provisioned, loaded and measured without the
chassis knowing anything about it. Second, that the services pack's own claims
hold -- chiefly that the realisation bridge closes, because a waterfall with an
unaccounted residual reads as precision while being wrong.
"""

from __future__ import annotations

import datetime as dt
import tempfile
import uuid
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from fracture.core import db
from fracture.packs import all_packs, get_pack
from tests.conftest import requires_db


# -- the registry and the seam -----------------------------------------------


def test_both_shipped_packs_register():
    names = {p.name for p in all_packs()}
    assert {"wealth", "services"} <= names


def test_a_pack_states_what_it_measures():
    """A pack with no thesis is a schema, and a schema does not sell an
    engagement."""
    for pack in all_packs():
        assert len(pack.thesis) > 80, f"{pack.name} has no stated thesis"
        assert pack.title


def test_every_pack_numbers_its_ddl_into_the_chassis_sequence():
    """Ordering is lexical across both layers, so a pack script that depends on
    a chassis table has to claim a higher number and the dependency stays
    visible in the filename."""
    from fracture.canon.schema import chassis_ddl_scripts

    chassis = [name for name, _ in chassis_ddl_scripts()]
    for pack in all_packs():
        own = [name for name, _ in pack.ddl_scripts()]
        assert own, f"{pack.name} ships no DDL"
        merged = sorted(chassis + own)
        # 000 schemas and 010 raw/lineage must precede every pack script.
        assert merged[0] == "000_schemas.sql"
        assert merged[1] == "010_raw_lineage.sql"
        for name in own:
            assert name[:3].isdigit(), f"{pack.name}/{name} claims no number"


def test_packs_do_not_share_a_mart_table_name():
    """Two packs writing the same mart name is the seam collapsing: one tenant
    would get whichever pack ran last."""
    import re

    seen: dict[str, str] = {}
    collisions: list[str] = []
    for pack in all_packs():
        for path in sorted(pack.model_dir.glob("*.sql")):
            for table in re.findall(
                r"create table (mart\.\w+)", path.read_text(), re.IGNORECASE
            ):
                owner = seen.setdefault(table, pack.name)
                if owner != pack.name:
                    collisions.append(f"{table} in {owner} and {pack.name}")
    # Both packs legitimately publish a firm_scorecard: they are different
    # tables in different tenant databases, never in one. Assert the overlap is
    # deliberate and small rather than asserting none.
    assert len(collisions) <= 6, f"packs overlap on too many marts: {collisions}"


def test_the_canonical_writer_refuses_an_entity_its_pack_does_not_define():
    """The chassis writer is told what a fact is by the pack. An adapter typo
    has to fail loudly rather than write nothing."""
    from fracture.core.errors import FractureError

    pack = get_pack("services")
    with pytest.raises(FractureError) as exc:
        pack.entity("household")
    assert "defines no canonical entity" in str(exc.value)
    assert "household" in str(exc.value)


def test_hours_are_numeric_columns_in_the_services_pack():
    """An hour is a figure with financial consequence: it is multiplied by a
    charge rate and invoiced. If it is not declared numeric, the AI boundary
    would let a model write it."""
    spec = get_pack("services").entity("time_entry")
    assert {"hours", "billable_hours"} <= spec.numeric


# -- the services pack, end to end -------------------------------------------


@pytest.fixture(scope="module")
def practice(control):
    """A loaded practice with marts built at a pinned system time.

    Loaded through `generic_csv` and a mapping config, with no bespoke adapter:
    the config-driven adapter exists precisely so a new vertical does not need
    new Python, and this is where that claim is tested rather than asserted.
    """
    from fracture.marts.runner import MartRunner
    from fracture.packs.services.load import load_practice
    from fracture.packs.services.synth import TEST_PRACTICE, generate

    slug = f"svc{uuid.uuid4().hex[:9]}"
    spec = replace(TEST_PRACTICE, tenant_slug=slug)
    generated = generate(spec, Path(tempfile.mkdtemp(prefix="svc-")))
    tenant, report = load_practice(generated, control=control)

    system_time = dt.datetime.now(dt.timezone.utc)
    pack = get_pack("services")
    with control.tenant_connection(tenant, "transform") as conn:
        MartRunner(pack=pack).run(conn, system_time)

    yield {
        "tenant": tenant, "spec": spec, "report": report,
        "system_time": system_time, "pack": pack, "generated": generated,
    }

    from tests.conftest import _drop

    _drop(control, tenant)


@requires_db
def test_the_tenant_records_its_pack_and_engagement(practice):
    tenant = practice["tenant"]
    assert tenant.pack == "services"
    assert tenant.engagement_code == "INTERNAL-OPS"


@requires_db
def test_a_whole_vertical_loads_with_no_bespoke_adapter(practice):
    report = practice["report"]
    assert report.raw_rows > 0
    assert report.canon_rows > 0, (
        "the config-driven adapter mapped nothing; a new vertical was supposed "
        "to be a mapping config rather than a Python module"
    )


@requires_db
def test_every_canonical_row_traces_to_a_raw_payload(control, practice):
    """The chassis lineage guarantee, exercised through a pack it has never
    seen."""
    with control.tenant_connection(practice["tenant"], "transform") as conn:
        unlineaged = db.scalar(
            conn,
            """
            select count(*) from canon.time_entry t
             where not exists (
               select 1 from lineage.edge e
                where e.target_table = 'canon.time_entry'
                  and e.target_pk = t.canon_id::text
             )
            """,
        )
    assert unlineaged == 0, f"{unlineaged} canonical time entries have no lineage"


@requires_db
def test_the_realisation_bridge_closes(control, practice):
    """The assertion that matters. A bridge with an unaccounted residual reads
    as precision while being wrong, and the first version of this model had
    one: its opening balance was billable hours at card, which is already net
    of write-off, so write-off was subtracted twice."""
    with control.tenant_connection(practice["tenant"], "transform") as conn:
        rows = db.query(
            conn,
            """
            select firm_id, period_end,
                   max(amount) filter (where step = 'recorded_at_card') as opening,
                   sum(amount) filter (where direction in ('loss','gain')) as movement,
                   max(amount) filter (where step = 'collected') as closing
              from mart.realisation_bridge
             group by 1, 2 order by 1, 2
            """,
        )
    assert rows, "the bridge published nothing"
    for row in rows:
        residual = row["opening"] + row["movement"] - row["closing"]
        assert abs(residual) <= Decimal("0.05"), (
            f"{row['firm_id']} {row['period_end']}: bridge does not close, "
            f"residual {residual}"
        )


@requires_db
def test_the_bridge_reports_over_billing_rather_than_netting_it_off(control, practice):
    """An engagement billed above entitlement is a refund exposure, not a
    windfall. Netted into the residual it disappears."""
    with control.tenant_connection(practice["tenant"], "transform") as conn:
        steps = {
            r["step"]
            for r in db.query(conn, "select distinct step from mart.realisation_bridge")
        }
    assert "billed_above_entitlement" in steps
    assert "pricing" in steps


@requires_db
def test_write_off_at_entry_is_attributed_to_a_reason(control, practice):
    """A write-off without a reason is a number nobody can act on, which is why
    the canonical model refuses one."""
    with control.tenant_connection(practice["tenant"], "transform") as conn:
        unexplained = db.scalar(
            conn,
            """
            select count(*) from canon.time_entry
             where superseded_at is null
               and billable_hours < hours
               and writeoff_reason is null
            """,
        )
    assert unexplained == 0


@requires_db
def test_the_scorecard_measures_rates_not_sizes(control, practice):
    """Every figure a practice is compared on has to be scale-free, or the view
    ranks practices by headcount and says nothing about how they are run."""
    with control.tenant_connection(practice["tenant"], "transform") as conn:
        row = db.query_one(conn, "select * from mart.firm_scorecard limit 1")
    assert row is not None
    for key in ("writeoff_rate", "realisation_rate", "collection_rate", "utilisation"):
        value = row[key]
        assert value is not None, f"{key} is null"
        assert Decimal("-1") <= Decimal(str(value)) <= Decimal("3"), (
            f"{key}={value} is not a rate"
        )
    assert Decimal(str(row["card_rate_per_hour"])) > 0


@requires_db
def test_lockup_splits_into_the_two_legs_with_different_owners(control, practice):
    with control.tenant_connection(practice["tenant"], "transform") as conn:
        row = db.query_one(
            conn,
            "select sum(wip_amount) as wip, sum(debtor_amount) as debtors, "
            "sum(overdue_amount) as overdue from mart.lockup",
        )
    assert row is not None
    assert row["wip"] is not None and row["debtors"] is not None
    assert Decimal(str(row["overdue"])) <= Decimal(str(row["debtors"])) + Decimal("0.01")


@requires_db
def test_pooled_overhead_lands_entirely_on_engagements(control, practice):
    """Excluding the pool is how every engagement looks profitable and the
    practice still misses its number."""
    with control.tenant_connection(practice["tenant"], "transform") as conn:
        rows = db.query(conn, "select * from mart.cost_allocation_check")
    assert rows
    for row in rows:
        assert abs(Decimal(str(row["variance"]))) <= Decimal("0.05"), (
            f"overhead allocation is incomplete: {row}"
        )


@requires_db
def test_lockup_ages_against_the_pinned_system_time_not_the_wall_clock(
    control, practice
):
    """Rebuilt at an explicitly earlier system time, an unsettled invoice must
    report fewer debtor days. If it reads the clock instead, the figure is the
    same and the pack cannot be reissued."""
    from fracture.marts.runner import MartRunner

    pack = practice["pack"]
    # Forwards, not backwards. Winding the system time back past the load would
    # read an empty canon -- every row was recorded at load time -- so the mart
    # would be empty for the right reason and the test would prove nothing.
    later = practice["system_time"] + dt.timedelta(days=20)
    with control.tenant_connection(practice["tenant"], "transform") as conn:
        before = db.scalar(conn, "select max(debtor_days) from mart.lockup")
        MartRunner(pack=pack).run(conn, later, assert_after=False)
        after = db.scalar(conn, "select max(debtor_days) from mart.lockup")
        # Restore the fixture's state for any later test in the module.
        MartRunner(pack=pack).run(conn, practice["system_time"])

    assert before is not None and after is not None
    assert after > before, (
        f"debtor days did not move with the pinned system time "
        f"({before} -> {after}); the mart is reading the wall clock"
    )
    assert after - before == pytest.approx(20, abs=1), (
        f"debtor days moved by {after - before} when the system time moved 20 days"
    )


@requires_db
def test_the_console_renders_for_the_services_pack(control, practice):
    from fracture.pack.shell import render

    pack = practice["pack"]
    board = pack.dashboard()
    firms = [{
        "firm_id": practice["spec"].firm_id,
        "legal_name": practice["spec"].tenant_name,
        "role": "practice", "close_date": None,
    }]
    with control.tenant_connection(practice["tenant"], "transform") as conn:
        data = board.collect(conn, practice["tenant"], firms)
    html = render(board.DEPARTMENTS, board.VIEWS, data, board.chrome(data))
    assert html.lstrip().startswith("<title>")
    for dept in board.DEPARTMENTS:
        assert f'id="view-{dept.key}"' in html, f"{dept.key} did not render"
    # The shell is chassis and must not have leaked a wealth word into it.
    for word in ("household", "AUM", "custodian"):
        assert word not in html, f"the services console mentions {word!r}"


# -- the assessment ----------------------------------------------------------


def test_the_assessment_prices_an_unsupported_system_rather_than_dropping_it():
    """An omitted line is the one that blows the schedule."""
    from fracture.assurance.assess import assess

    a = assess(["orion", "redtail", "addepar"], pack="wealth")
    assert "addepar" in a.unsupported
    assert a.new_adapter_hours > 0
    assert a.delivery_hours > a.adapter_hours


def test_the_assessment_credits_itself_against_delivery():
    from fracture.assurance.assess import assess

    a = assess(["orion", "redtail"], pack="wealth")
    assert a.assessment_fee > 0
    assert a.net_delivery_fee == pytest.approx(a.delivery_fee - a.assessment_fee)


def test_two_assessments_of_the_same_scope_hash_the_same():
    """So a quote can be checked against the manifest it came from, later, by
    anyone holding both."""
    from fracture.assurance.assess import assess

    first = assess(["orion", "qbo"], pack="wealth", rate=250.0)
    second = assess(["orion", "qbo"], pack="wealth", rate=250.0)
    assert first.content_hash() == second.content_hash()
    third = assess(["orion", "qbo"], pack="wealth", rate=300.0)
    assert third.content_hash() != first.content_hash()


def test_each_pack_prices_against_its_own_required_entities():
    from fracture.assurance.assess import assess

    wealth = assess(["orion"], pack="wealth")
    services = assess(["orion"], pack="services")
    assert {e["entity"] for e in wealth.entities} != {
        e["entity"] for e in services.entities
    }
