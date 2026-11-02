"""Marts, the fee model, and reconciliation.

The unbilled and leakage claims rest entirely on `fee_schedule` being modelled
properly (spec section 5). The strongest test available is that two independent
implementations of the same rules agree: the generator computes expected fees in
Python, the mart recomputes them in SQL from the canonical schedule, and the
undefected households must match to the cent.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from fracture.core import db
from fracture.core.errors import ReconciliationBreach
from fracture.marts.runner import MartAssertionError, MartRunner
from fracture.recon import checks as recon_checks
from fracture.synth.fees import blended_annual_fee, period_fee, tiered_annual_fee
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

TIERS = [
    {"tier_seq": 1, "lower_bound": 0, "upper_bound": 1_000_000, "annual_rate_bps": 110},
    {"tier_seq": 2, "lower_bound": 1_000_000, "upper_bound": 3_000_000, "annual_rate_bps": 85},
    {"tier_seq": 3, "lower_bound": 3_000_000, "upper_bound": None, "annual_rate_bps": 60},
]


# -- the fee arithmetic itself -----------------------------------------------


@pytest.mark.parametrize(
    "aum,expected",
    [
        # Below the first breakpoint: one rate.
        ("500000", "5500.00"),
        # Exactly on a breakpoint: the higher band charges nothing yet.
        ("1000000", "11000.00"),
        # Across two bands: 1m at 110bps plus 1.5m at 85bps.
        ("2500000", "23750.00"),
        # Across all three.
        # 1m at 110bps + 2m at 85bps + 2m at 60bps
        ("5000000", "40000.00"),
        ("0", "0.00"),
    ],
)
def test_marginal_tiering(aum, expected):
    assert tiered_annual_fee(Decimal(aum), TIERS) == Decimal(expected)


def test_blended_is_not_tiered():
    """Getting these the wrong way round moves the answer by more than the
    leakage being looked for."""
    aum = Decimal("2500000")
    assert blended_annual_fee(aum, TIERS) == Decimal("21250.00")
    assert tiered_annual_fee(aum, TIERS) == Decimal("23750.00")


def test_quarterly_is_a_quarter_of_annual():
    assert period_fee(Decimal("2500000"), TIERS, "tiered", "quarterly") == Decimal("5937.50")


def test_unknown_frequency_raises_rather_than_defaulting():
    with pytest.raises(ValueError, match="unknown billing frequency"):
        period_fee(Decimal("100"), TIERS, "tiered", "fortnightly")


# -- the SQL implementation must agree with the Python one -------------------


def test_sql_expected_revenue_matches_python_for_undefected_households(control, built_marts):
    """Two implementations of the fee rules, reconciled.

    If these diverge, the unbilled figure is fiction, and nothing else in the
    system would notice.
    """
    tenant = built_marts["tenant"]
    estate = built_marts["estate"]
    unbilled_keys = set()
    below_keys = set()
    for generated in estate.firms:
        unbilled_keys |= generated.defects.unbilled_households
        below_keys |= generated.defects.below_schedule_invoices

    with control.tenant_connection(tenant, "transform") as conn:
        rows = db.query(
            conn,
            """
            select u.firm_id, u.household_id, u.period_end, u.expected_amount,
                   u.billed_amount, u.finding
              from mart.unbilled u
             where u.finding = 'as_expected'
            """,
        )
    assert rows, "no household billed as expected; the fixture has no clean cases"
    for row in rows:
        key = f"{row['household_id']}|{row['period_end'].isoformat()}"
        assert key not in unbilled_keys
        assert row["expected_amount"] == row["billed_amount"], (
            f"{key}: SQL expected {row['expected_amount']} but the firm billed "
            f"{row['billed_amount']}, and this household has no planted defect"
        )


def test_every_planted_unbilled_household_is_found(control, built_marts):
    """The generator plants the defect; the platform has to find it."""
    tenant = built_marts["tenant"]
    planted = set()
    for generated in built_marts["estate"].firms:
        planted |= generated.defects.unbilled_households

    with control.tenant_connection(tenant, "transform") as conn:
        found = {
            f"{r['household_id']}|{r['period_end'].isoformat()}"
            for r in db.query(
                conn,
                "select household_id, period_end from mart.unbilled "
                "where finding = 'never_invoiced'",
            )
        }
    assert planted, "the fixture planted no unbilled households"
    missed = planted - found
    assert not missed, f"the platform missed {len(missed)} planted unbilled household(s): {sorted(missed)[:5]}"


def test_planted_below_schedule_invoices_are_found(control, built_marts):
    tenant = built_marts["tenant"]
    planted_amount = sum(
        (g.defects.below_schedule_amount for g in built_marts["estate"].firms), Decimal(0)
    )
    with control.tenant_connection(tenant, "transform") as conn:
        found = db.scalar(
            conn,
            "select coalesce(sum(expected_amount - billed_amount), 0) from mart.unbilled "
            "where finding = 'billed_below_schedule'",
        )
    assert planted_amount > 0, "the fixture planted no below-schedule invoices"
    # Not exact: a custodian variance can move the basis, which changes the
    # expected fee. The finding must still account for at least what was planted.
    assert found >= planted_amount * Decimal("0.9"), (
        f"found {found} of a planted {planted_amount} in below-schedule leakage"
    )


def test_non_billable_accounts_are_excluded_from_expected_revenue(control, built_marts):
    """A held-away account in the basis silently inflates expected revenue, and
    therefore silently invents unbilled revenue that does not exist."""
    tenant = built_marts["tenant"]
    with control.tenant_connection(tenant, "transform") as conn:
        rows = db.query(
            conn,
            """
            select h.firm_id, h.household_id, h.as_of_date, h.total_value, h.billable_value,
                   h.non_billable_accounts
              from mart.household_aum h
             where h.non_billable_accounts > 0
             limit 20
            """,
        )
    assert rows, "the fixture has no non-billable accounts to test with"
    for row in rows:
        assert row["billable_value"] < row["total_value"], (
            f"{row['household_id']} has {row['non_billable_accounts']} non-billable "
            "accounts but its billable basis equals its total"
        )


def test_leakage_components_do_not_double_count(control, built_marts):
    """never_invoiced and billed_below_schedule are disjoint by construction; if
    they ever overlap the headline leakage number is inflated."""
    tenant = built_marts["tenant"]
    with control.tenant_connection(tenant, "transform") as conn:
        overlap = db.query(
            conn,
            """
            select firm_id, household_id, period_end, count(distinct finding)
              from mart.unbilled
             where finding in ('never_invoiced', 'billed_below_schedule')
             group by 1,2,3 having count(distinct finding) > 1
            """,
        )
    assert overlap == []


def test_consolidated_equals_the_sum_of_its_firms(control, built_marts):
    tenant = built_marts["tenant"]
    with control.tenant_connection(tenant, "transform") as conn:
        mismatched = db.query(
            conn,
            """
            select c.period_end, c.total_aum, f.total
              from mart.consolidated_month c
              join (select period_end, sum(total_aum) as total from mart.firm_month group by 1) f
                on f.period_end = c.period_end
             where abs(c.total_aum - f.total) > 0.01
            """,
        )
    assert mismatched == []


def test_departed_advisor_still_holds_their_book(control, built_marts):
    """The whole point of effective-dated assignments: the risk is what walks
    out the door, and a report that reassigns it shows no risk at all."""
    tenant = built_marts["tenant"]
    with control.tenant_connection(tenant, "transform") as conn:
        departed = db.query(
            conn, "select * from mart.concentration where has_departed"
        )
    if not departed:
        pytest.skip("this fixture has no departed advisor")
    for row in departed:
        assert row["book_value"] > 0, (
            f"{row['producer_id']} has left and their book silently went to zero"
        )


def test_open_service_events_count_as_breaches(control, built_marts):
    """Counting only closed events hides the worst backlog."""
    tenant = built_marts["tenant"]
    with control.tenant_connection(tenant, "transform") as conn:
        rows = db.query(
            conn,
            "select breached, still_open, elapsed_hours, sla_target_hours "
            "from mart.service_sla where still_open and sla_target_hours is not null",
        )
    for row in rows:
        if row["elapsed_hours"] > row["sla_target_hours"]:
            assert row["breached"], "an event open past its target was not counted as breached"


# -- the assertions themselves -----------------------------------------------


def test_mart_assertions_catch_an_empty_mart(control, built_marts):
    """An empty mart renders as zeros everywhere and raises nothing on its own."""
    tenant = built_marts["tenant"]
    with control.tenant_connection(tenant, "transform") as conn:
        db.execute(conn, "create table mart._backup_aum as select * from mart.household_aum")
        db.execute(conn, "delete from mart.household_aum")
        try:
            with pytest.raises(MartAssertionError, match="mart.household_aum is empty"):
                MartRunner().assert_sane(conn)
        finally:
            db.execute(conn, "insert into mart.household_aum select * from mart._backup_aum")
            db.execute(conn, "drop table mart._backup_aum")


def test_mart_assertions_catch_a_broken_invariant(control, built_marts):
    tenant = built_marts["tenant"]
    with control.tenant_connection(tenant, "transform") as conn:
        db.execute(
            conn,
            "update mart.billed_revenue set collected_amount = billed_amount * 2 "
            "where invoice_id = (select invoice_id from mart.billed_revenue limit 1)",
        )
        try:
            with pytest.raises(MartAssertionError, match="collected must not exceed billed"):
                MartRunner().assert_sane(conn)
        finally:
            db.execute(
                conn,
                "update mart.billed_revenue set collected_amount = "
                "least(collected_amount, billed_amount)",
            )


# -- reconciliation ----------------------------------------------------------


def test_reconciliation_passes_on_a_clean_load(control, built_marts):
    tenant = built_marts["tenant"]
    with control.tenant_connection(tenant, "transform") as conn:
        report = recon_checks.run_all(conn, persist=False)
    assert report.results, "the reconciliation suite ran no checks"
    assert report.passed, "\n".join(f.describe() for f in report.failures)


def test_reconciliation_catches_a_dropped_mart_row(control, built_marts):
    """A mart that quietly drops rows is invisible without this."""
    tenant = built_marts["tenant"]
    with control.tenant_connection(tenant, "transform") as conn:
        victim = db.query_one(
            conn, "select invoice_id, billed_amount from mart.billed_revenue "
            "order by billed_amount desc limit 1"
        )
        db.execute(
            conn, "delete from mart.billed_revenue where invoice_id = %s", (victim["invoice_id"],)
        )
        try:
            report = recon_checks.run_all(conn, persist=False)
            assert not report.passed, "deleting a billed invoice did not fail any check"
            names = {f.check_name for f in report.failures}
            assert "billed_vs_invoices" in names
            with pytest.raises(ReconciliationBreach):
                report.raise_if_failed()
        finally:
            MartRunner().run(conn, built_marts["system_time"])


def test_a_missing_counterparty_figure_is_a_failure_not_a_pass(control, built_marts):
    """"We could not check" and "we checked and it agrees" must never look alike."""
    result = recon_checks.CheckResult(
        check_name="aum_total", firm_id="F1",
        period_start=dt.date(2026, 3, 31), period_end=dt.date(2026, 4, 1),
        grain_key="", expected=None, actual=Decimal("1000"),
        tolerance_pct=Decimal("0.01"),
    )
    assert result.passed is False


def test_tolerance_is_respected_in_both_directions():
    def make(expected, actual, tolerance="0.01"):
        return recon_checks.CheckResult(
            check_name="x", firm_id="F1",
            period_start=dt.date(2026, 1, 1), period_end=dt.date(2026, 4, 1),
            grain_key="", expected=Decimal(expected), actual=Decimal(actual),
            tolerance_pct=Decimal(tolerance),
        )

    assert make("1000", "1005").passed        # +0.5%
    assert make("1000", "995").passed         # -0.5%
    assert not make("1000", "1011").passed    # +1.1%
    assert not make("1000", "989").passed     # -1.1%
