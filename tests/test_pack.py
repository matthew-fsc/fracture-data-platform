"""Packs: pinning, reproducibility, drill-through and restatement.

"Reissuing a pack with the same system_time produces byte-identical numbers"
(spec 6.3) is the single most testable commercial claim in the specification,
and the one most likely to rot silently: any `now()` in a mart, any counter that
accumulates across runs, and it is quietly false.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from fracture.core import db
from fracture.core.errors import LineageError, PackIntegrityError
from fracture.pack import PackBuilder, assert_drillable, figures_for, resolve
from fracture.pack.build import compute_content_hash, restatement_delta
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

PERIOD_START = dt.date(2026, 1, 1)
PERIOD_END = dt.date(2026, 3, 31)


@pytest.fixture(scope="module")
def pack(control, built_marts):
    system_time = dt.datetime.now(dt.timezone.utc)
    builder = PackBuilder(control, built_marts["tenant"])
    result = builder.build(PERIOD_START, PERIOD_END, system_time=system_time)
    return {"builder": builder, "result": result, "system_time": system_time, **built_marts}


def test_pack_issues_with_figures_in_every_section(control, pack):
    result = pack["result"]
    assert result.figure_count > 0
    with control.tenant_connection(pack["tenant"], "transform") as conn:
        sections = {
            r["section"] for r in figures_for(conn, result.pack_run.pack_run_id)
        }
    assert sections >= {
        "platform_model", "revenue_margin", "unbilled_leakage",
        "service_sla", "concentration", "assurance",
    }


#: The metrics each section is expected to publish. Written out rather than
#: derived, because the failure this catches is a section quietly publishing
#: fewer figures than it defines -- an `ORDER BY ... LIMIT` after the last branch
#: of a UNION applies to the whole union, and the section still renders.
EXPECTED_METRICS = {
    "platform_model": {
        "consolidated_aum", "consolidated_households", "consolidated_firms",
        "firm_aum", "firm_billable_aum", "firm_households",
    },
    "revenue_margin": {
        "consolidated_billed", "consolidated_collected", "consolidated_margin",
        "consolidated_margin_pct", "firm_billed", "firm_margin", "firm_margin_pct",
        "top_client_margin",
    },
    "unbilled_leakage": {
        "leakage_never_invoiced", "leakage_billed_below_schedule",
        "leakage_uncollected", "leakage_total", "leakage_rate", "unbilled_household",
    },
    "service_sla": {"sla_still_open", "firm_sla_breach_rate"},
    "concentration": {
        "producer_book_share", "top_producer_share", "departed_producer_book",
    },
    "assurance": {
        "recon_checks_passed", "recon_checks_failed", "recon_worst_variance_pct",
        "open_source_variances", "unacknowledged_schema_drift", "raw_rows_held",
        "lineage_edges", "ai_boundary_violations", "sources_read_only_verified",
    },
}


def test_every_section_publishes_the_metrics_it_defines(control, pack):
    with control.tenant_connection(pack["tenant"], "transform") as conn:
        rows = figures_for(conn, pack["result"].pack_run.pack_run_id)
    published: dict[str, set[str]] = {}
    for row in rows:
        published.setdefault(row["section"], set()).add(row["metric"])
    for section, expected in EXPECTED_METRICS.items():
        missing = expected - published.get(section, set())
        assert not missing, (
            f"pack section {section} published no rows for {sorted(missing)}; "
            "a truncated section still renders and the figures look undefined"
        )


def test_reissue_at_the_same_system_time_is_byte_identical(control, pack):
    """The guarantee, executed."""
    again = pack["builder"].build(
        PERIOD_START, PERIOD_END, system_time=pack["system_time"]
    )
    assert again.content_hash == pack["result"].content_hash, (
        "a pack reissued at its pinned system time produced different numbers"
    )


def test_verify_reproducible_passes_for_an_issued_pack(control, pack):
    from fracture.pack import verify_reproducible

    digest = verify_reproducible(
        control, pack["tenant"], pack["result"].pack_run.pack_run_id
    )
    assert digest == pack["result"].content_hash


def test_content_hash_ignores_row_order(control, pack):
    """Two runs returning the same figures in a different order are the same
    pack; a hash that disagreed would make the guarantee useless."""
    with control.tenant_connection(pack["tenant"], "transform") as conn:
        rows = figures_for(conn, pack["result"].pack_run.pack_run_id)
    forwards = compute_content_hash(rows)
    backwards = compute_content_hash(list(reversed(rows)))
    assert forwards == backwards


def test_content_hash_ignores_decimal_scale():
    base = [
        {"section": "s", "metric": "m", "firm_id": "F", "grain_key": "g",
         "numeric_value": Decimal("1.50"), "text_value": None, "unit": "USD"}
    ]
    other = [{**base[0], "numeric_value": Decimal("1.5000")}]
    assert compute_content_hash(base) == compute_content_hash(other)


def test_content_hash_changes_when_a_figure_changes():
    base = [
        {"section": "s", "metric": "m", "firm_id": "F", "grain_key": "g",
         "numeric_value": Decimal("1.50"), "text_value": None, "unit": "USD"}
    ]
    other = [{**base[0], "numeric_value": Decimal("1.51")}]
    assert compute_content_hash(base) != compute_content_hash(other)


def test_a_new_system_time_produces_the_restatement(control, pack):
    """Reissuing with a new system time after a restatement produces the delta,
    and the delta is itself a report (spec 6.3).

    The restated fact here is a cost line rather than a balance: restating a
    balance is equally valid and correctly breaks the custodian reconciliation,
    which would make this test assert two unrelated things at once.
    """
    tenant = pack["tenant"]
    original = pack["result"]

    with control.tenant_connection(tenant, "transform") as conn:
        target = db.query_one(
            conn,
            "select canon_id, amount from canon.cost_line "
            "where superseded_at is null and period_start between %s and %s "
            "order by amount desc limit 1",
            (PERIOD_START, PERIOD_END),
        )
        assert target, "no cost line to restate"
        restated_at = dt.datetime.now(dt.timezone.utc)
        db.execute(
            conn,
            "update canon.cost_line set superseded_at = %s where canon_id = %s",
            (restated_at, target["canon_id"]),
        )
        db.execute(
            conn,
            """
            insert into canon.cost_line
              (firm_id, cost_id, period_start, period_end, category, vendor, person_id,
               amount, allocation_basis, source_id, recorded_at)
            select firm_id, cost_id, period_start, period_end, category, vendor, person_id,
                   amount * 1.4, allocation_basis, source_id, %s
              from canon.cost_line where canon_id = %s
            """,
            (restated_at, target["canon_id"]),
        )

    try:
        restated = pack["builder"].build(
            PERIOD_START, PERIOD_END,
            system_time=restated_at + dt.timedelta(seconds=1),
            supersedes=original.pack_run.pack_run_id,
        )
        assert restated.content_hash != original.content_hash, (
            "a restated cost did not move a single figure in the pack"
        )

        with control.tenant_connection(tenant, "transform") as conn:
            delta = restatement_delta(
                conn, original.pack_run.pack_run_id, restated.pack_run.pack_run_id
            )
        assert delta, "a restated cost produced no delta between the two packs"
        assert {"consolidated_margin", "firm_margin"} & {d["metric"] for d in delta}

        # The superseding pack marks its predecessor.
        runs = {str(r.pack_run_id): r for r in control.list_pack_runs(tenant)}
        assert runs[str(original.pack_run.pack_run_id)].status == "superseded"
        assert runs[str(restated.pack_run.pack_run_id)].status == "issued"

        # And the original still reproduces at its own system time: a
        # restatement explains the new number without destroying the old one.
        from fracture.pack import verify_reproducible

        assert verify_reproducible(
            control, tenant, original.pack_run.pack_run_id
        ) == original.content_hash
    finally:
        from fracture.marts.runner import MartRunner

        with control.tenant_connection(tenant, "transform") as conn:
            db.execute(
                conn, "delete from canon.cost_line where recorded_at = %s", (restated_at,)
            )
            db.execute(
                conn,
                "update canon.cost_line set superseded_at = null where canon_id = %s",
                (target["canon_id"],),
            )
            MartRunner().run(conn, pack["system_time"])


def test_every_figure_opens_to_raw_records(control, pack):
    with control.tenant_connection(pack["tenant"], "transform") as conn:
        assert_drillable(conn, pack["result"].pack_run.pack_run_id)


def test_drill_through_returns_the_artifact_and_its_hash(control, pack):
    with control.tenant_connection(pack["tenant"], "transform") as conn:
        figure = db.query_one(
            conn,
            "select drill_query from pack.figure where pack_run_id = %s "
            "and drill_query like 'mart.unbilled|%%' limit 1",
            (pack["result"].pack_run.pack_run_id,),
        )
        assert figure
        result = resolve(conn, figure["drill_query"], limit=2, evidence_limit=5)
    assert result.evidence
    for item in result.evidence:
        assert item.artifact_uri.startswith("s3://")
        assert len(item.artifact_sha256) == 64
        assert item.payload is not None


def test_assert_drillable_actually_fails(control, pack):
    """The guard has to catch something."""
    with control.tenant_connection(pack["tenant"], "transform") as conn:
        db.execute(conn, "create table lineage._backup as select * from lineage.mart_edge")
        db.execute(conn, "delete from lineage.mart_edge")
        try:
            with pytest.raises(LineageError, match="cannot be opened to raw"):
                assert_drillable(conn, pack["result"].pack_run.pack_run_id)
        finally:
            db.execute(conn, "insert into lineage.mart_edge select * from lineage._backup")
            db.execute(conn, "drop table lineage._backup")


def test_an_empty_section_fails_the_build_rather_than_rendering_blank(control, built_marts):
    """A silently empty section is a blank page in a board pack."""
    from fracture.pack.build import SectionDef

    empty_section = SectionDef(
        section="ghost", title="Ghost section", order=99,
        sql=(
            "select 'x' as metric, null as firm_id, null as grain_key, null as grain_label, "
            "null::numeric as numeric_value, null as text_value, null as unit, "
            "1 as sort_order, null as drill_query where false"
        ),
        path=None,
    )
    builder = PackBuilder(control, built_marts["tenant"], sections=[empty_section])
    with pytest.raises(PackIntegrityError, match="produced no figures"):
        builder.build(PERIOD_START, PERIOD_END)


def test_a_section_missing_a_required_column_fails_the_build(control, built_marts):
    from fracture.pack.build import SectionDef

    bad = SectionDef(
        section="bad", title="Missing columns", order=99,
        sql="select 'x' as metric, 1 as numeric_value",
        path=None,
    )
    builder = PackBuilder(control, built_marts["tenant"], sections=[bad])
    with pytest.raises(PackIntegrityError, match="missing column"):
        builder.build(PERIOD_START, PERIOD_END)


def test_a_pack_is_not_issued_when_reconciliation_breaches(control, built_marts):
    """A pack built on numbers that did not reconcile is worse than no pack: it
    looks authoritative."""
    from fracture.core.errors import ReconciliationBreach

    tenant = built_marts["tenant"]
    with control.tenant_connection(tenant, "transform") as conn:
        db.execute(
            conn,
            "delete from mart.billed_revenue where invoice_id in "
            "(select invoice_id from mart.billed_revenue limit 3)",
        )
    try:
        with pytest.raises(ReconciliationBreach):
            PackBuilder(control, tenant).build(
                PERIOD_START, PERIOD_END, rebuild_marts=False
            )
        with control.tenant_connection(tenant, "transform") as conn:
            failed = db.query(
                conn,
                "select 1 from pack.manifest m join pack.figure f using (pack_run_id) limit 1",
            )
        runs = control.list_pack_runs(tenant)
        assert any(r.status == "failed" for r in runs), "the breached pack run was not marked failed"
    finally:
        # Rebuilt rather than restored from a copy: mart.billed_revenue has no
        # unique constraint, so an INSERT ... ON CONFLICT DO NOTHING restore
        # silently duplicates every row it did not delete, and every test after
        # this one reads doubled revenue.
        from fracture.marts.runner import MartRunner

        with control.tenant_connection(tenant, "transform") as conn:
            MartRunner().run(conn, built_marts["system_time"])


def test_rendered_pack_carries_the_hash_and_the_pinned_time(control, pack, tmp_path):
    from fracture.adapters import estimate_fold_in
    from fracture.pack.data import collect
    from fracture.pack.render import render

    tenant = pack["tenant"]
    firms = [
        {"firm_id": f.firm_id, "legal_name": f.legal_name, "role": f.role}
        for f in control.list_firms(tenant)
    ]
    with control.tenant_connection(tenant, "transform") as conn:
        data = collect(
            conn, tenant, pack["result"].pack_run.pack_run_id, firms,
            estimate_fold_in(["orion", "redtail", "qbo"]).as_dict(),
        )
    html = render(data)
    assert data.content_hash[:32] in html, "the pack does not show its own content hash"
    assert data.system_time.strftime("%Y-%m-%d") in html
    assert "<title>" in html
    # Every theme token is defined on bare :root, not only inside a media query.
    assert ":root {" in html and "--ground:" in html
    assert "prefers-color-scheme: dark" in html
