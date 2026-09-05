"""Firm comparability: the scorecard, the yield bridge, and the KPI set.

The commercial question these answer is "which of our firms is well run", and
the trap is that the biggest firm always looks best on any absolute measure.
Everything here is either a rate, a per-unit figure or basis points on AUM, so
these tests are mostly about proving that normalisation is real and that the
decomposition adds up.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from fracture.core import db
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

BPS_TOLERANCE = Decimal("0.02")


def test_the_yield_bridge_closes_for_every_firm_and_period(control, built_marts):
    """Schedule minus what was lost, plus what was over-billed, equals collected.

    A waterfall with an unexplained residual is worse than no waterfall: it
    reads as precision. This assertion is what forced over-billing to become an
    explicit step rather than a silent gap.
    """
    with control.tenant_connection(built_marts["tenant"], "transform") as conn:
        rows = db.query(
            conn,
            """
            select s.firm_id, s.period_end, s.schedule_yield_bps, s.collected_yield_bps,
                   b.total_move
              from mart.firm_scorecard s
              join (
                select firm_id, period_end, sum(delta_bps) as total_move
                  from mart.yield_bridge where step_kind in ('loss','gain')
                 group by 1, 2
              ) b on b.firm_id = s.firm_id and b.period_end = s.period_end
            """,
        )
    assert rows, "the yield bridge produced no rows"
    for row in rows:
        closed = row["schedule_yield_bps"] + row["total_move"]
        assert abs(closed - row["collected_yield_bps"]) <= BPS_TOLERANCE, (
            f"{row['firm_id']} {row['period_end']}: bridge does not close -- "
            f"schedule {row['schedule_yield_bps']} + moves {row['total_move']} "
            f"= {closed}, but collected is {row['collected_yield_bps']}"
        )


def test_the_bridge_intermediate_total_matches_the_scorecard(control, built_marts):
    """The 'Invoiced' step must equal the scorecard's actual yield, or the two
    views of the same number disagree in front of the reader."""
    with control.tenant_connection(built_marts["tenant"], "transform") as conn:
        rows = db.query(
            conn,
            """
            select b.firm_id, b.period_end, b.bps as bridge_actual, s.actual_yield_bps
              from mart.yield_bridge b
              join mart.firm_scorecard s
                on s.firm_id = b.firm_id and s.period_end = b.period_end
             where b.step = 'actual'
            """,
        )
    assert rows
    for row in rows:
        assert row["bridge_actual"] == row["actual_yield_bps"]


def test_the_yields_are_rates_and_therefore_scale_invariant(control, built_marts):
    """Each yield must be exactly its ratio, which is what makes it comparable.

    Asserted as an identity rather than by comparing rank orders: with a handful
    of firms two orderings can coincide by chance, so a rank test passes or fails
    on the fixture rather than on the code. If the figure equals
    billed x 4 / AUM x 10000 for every row, it is a rate by construction and
    doubling a firm's size cannot move it.
    """
    with control.tenant_connection(built_marts["tenant"], "transform") as conn:
        rows = db.query(
            conn,
            """
            select firm_id, period_end, total_aum, expected_amount, billed_amount,
                   collected_amount, schedule_yield_bps, actual_yield_bps, collected_yield_bps
              from mart.firm_scorecard
            """,
        )
    assert rows
    for row in rows:
        for amount_col, bps_col in (
            ("expected_amount", "schedule_yield_bps"),
            ("billed_amount", "actual_yield_bps"),
            ("collected_amount", "collected_yield_bps"),
        ):
            expected = row[amount_col] * 4 / row["total_aum"] * 10000
            assert abs(expected - row[bps_col]) <= Decimal("0.01"), (
                f"{row['firm_id']} {row['period_end']}: {bps_col} is {row[bps_col]} but "
                f"{amount_col} implies {expected:.4f}; the metric is not a pure rate"
            )


def test_per_unit_metrics_are_ratios_too(control, built_marts):
    """The same argument for the per-household figures the profitability view
    compares firms on."""
    with control.tenant_connection(built_marts["tenant"], "transform") as conn:
        rows = db.query(
            conn,
            "select firm_id, period_end, household_count, billed_amount, cost_to_serve, "
            "revenue_per_household, cost_per_household from mart.firm_scorecard",
        )
    assert rows
    for row in rows:
        assert abs(
            row["billed_amount"] / row["household_count"] - row["revenue_per_household"]
        ) <= Decimal("0.01")
        assert abs(
            row["cost_to_serve"] / row["household_count"] - row["cost_per_household"]
        ) <= Decimal("0.01")


def test_the_peer_benchmark_is_weighted_not_averaged(control, built_marts):
    """The platform's yield is total billed over total AUM, never the mean of
    the firms' yields. With firms of different size the two differ, and the
    unweighted one is wrong."""
    with control.tenant_connection(built_marts["tenant"], "transform") as conn:
        firms = db.query(
            conn,
            """
            select firm_id, total_aum, billed_amount, actual_yield_bps
              from mart.firm_scorecard
             where period_end = (select max(period_end) from mart.firm_scorecard)
            """,
        )
        peer = db.query_one(
            conn,
            "select distinct peer_value from mart.firm_kpi where kpi = 'actual_yield_bps'",
        )
    assert firms and peer

    total_aum = sum(f["total_aum"] for f in firms)
    total_billed = sum(f["billed_amount"] for f in firms)
    weighted = total_billed * 4 / total_aum * 10000
    naive_mean = sum(f["actual_yield_bps"] for f in firms) / len(firms)

    assert abs(peer["peer_value"] - weighted) <= Decimal("0.05"), (
        "the peer benchmark is not the AUM-weighted platform yield"
    )
    # And prove the distinction is not academic on this estate.
    assert abs(weighted - naive_mean) > Decimal("0.1"), (
        "weighted and unweighted peer values are identical here, so this test "
        "cannot tell a correct implementation from a wrong one"
    )


def test_rank_one_is_the_best_firm_not_the_largest_number(control, built_marts):
    """For a lower-is-better metric, rank 1 must be the smallest value. A
    leaderboard whose first place is worst is a bug readers blame themselves
    for."""
    with control.tenant_connection(built_marts["tenant"], "transform") as conn:
        rows = db.query(
            conn,
            """
            select kpi, direction, firm_id, value, firm_rank from mart.firm_kpi
             where firm_rank is not null order by kpi, firm_rank
            """,
        )
    assert rows
    by_kpi: dict[str, list[dict]] = {}
    for row in rows:
        by_kpi.setdefault(row["kpi"], []).append(row)
    for kpi, entries in by_kpi.items():
        values = [e["value"] for e in entries if e["value"] is not None]
        if len(values) < 2:
            continue
        best = entries[0]
        direction = best["direction"]
        if direction == "higher_better":
            assert best["value"] == max(values), f"{kpi}: rank 1 is not the highest"
        elif direction == "lower_better":
            assert best["value"] == min(values), f"{kpi}: rank 1 is not the lowest"


def test_neutral_metrics_are_not_ranked(control, built_marts):
    """AUM per household is context, not performance. Ranking it would invent a
    winner where there is none."""
    with control.tenant_connection(built_marts["tenant"], "transform") as conn:
        ranked_neutral = db.query(
            conn,
            "select kpi, firm_id from mart.firm_kpi "
            "where direction = 'neutral' and firm_rank is not null limit 5",
        )
    assert ranked_neutral == []


def test_every_kpi_belongs_to_a_department_the_dashboard_renders(control, built_marts):
    from fracture.pack.dashboard import DEPARTMENTS

    known = {d.key for d in DEPARTMENTS}
    with control.tenant_connection(built_marts["tenant"], "transform") as conn:
        departments = {
            r["department"] for r in db.query(conn, "select distinct department from mart.firm_kpi")
        }
    orphans = departments - known
    assert not orphans, (
        f"KPIs assigned to departments with no dashboard view: {sorted(orphans)}"
    )


def test_leakage_components_reconcile_to_the_realisation_gap(control, built_marts):
    """Expected minus the two billing losses plus over-billing equals billed.
    This is the same identity as the bridge, in dollars rather than bps, and it
    is the one a finance reader will check by hand."""
    with control.tenant_connection(built_marts["tenant"], "transform") as conn:
        rows = db.query(
            conn,
            """
            select firm_id, period_end, expected_amount, billed_amount,
                   leak_never_invoiced, leak_below_schedule, over_billed
              from mart.firm_scorecard
            """,
        )
    assert rows
    for row in rows:
        reconstructed = (
            row["expected_amount"]
            - row["leak_never_invoiced"]
            - row["leak_below_schedule"]
            + row["over_billed"]
        )
        assert abs(reconstructed - row["billed_amount"]) <= Decimal("0.05"), (
            f"{row['firm_id']} {row['period_end']}: expected {row['expected_amount']} "
            f"less losses plus over-billing = {reconstructed}, billed {row['billed_amount']}"
        )


def test_household_quartiles_are_ordered(control, built_marts):
    with control.tenant_connection(built_marts["tenant"], "transform") as conn:
        rows = db.query(conn, "select * from mart.household_distribution")
    assert rows
    for row in rows:
        assert row["margin_p25"] <= row["margin_p50"] <= row["margin_p75"]
        assert row["aum_p25"] <= row["aum_p50"] <= row["aum_p75"]
        assert 0 <= row["loss_making_share"] <= 1


def test_producer_book_shares_sum_to_the_whole_firm(control, built_marts):
    with control.tenant_connection(built_marts["tenant"], "transform") as conn:
        rows = db.query(
            conn,
            "select firm_id, sum(book_share) as total from mart.producer_scorecard "
            "where book_share is not null group by 1",
        )
    assert rows
    for row in rows:
        assert abs(row["total"] - 1) < Decimal("0.001"), (
            f"{row['firm_id']}: advisor book shares sum to {row['total']}, not 1"
        )


def test_cost_to_serve_is_fully_allocated_per_household(control, built_marts):
    with control.tenant_connection(built_marts["tenant"], "transform") as conn:
        rows = db.query(
            conn,
            """
            select h.firm_id, sum(h.cost_to_serve) as household_cost, s.cost_to_serve as firm_cost
              from mart.household_economics h
              join mart.firm_scorecard s
                on s.firm_id = h.firm_id and s.period_end = h.period_end
             group by h.firm_id, s.cost_to_serve
            """,
        )
    assert rows
    for row in rows:
        assert abs(row["household_cost"] - row["firm_cost"]) <= Decimal("0.05")
