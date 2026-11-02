"""Shape of the synthetic estate.

Spec 10: build the synthetic tenant generator early, because you cannot develop
against client data and a realistic fake RIA is the fixture for every demo as
well. "Realistic" here means *deliberately broken* -- a clean fixture proves
nothing, because the product exists to find the breakage.

Every defect below is a switch with a rate attached, so a test can assert the
platform finds exactly what was planted.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DefectRates:
    """The planted breakage. Each one maps to a finding in the pack."""

    #: Households on a fee schedule that were never invoiced for a period.
    unbilled_household_rate: float = 0.06
    #: Invoices raised below what the schedule says. The classic silent leak.
    below_schedule_rate: float = 0.09
    #: How far below, as a fraction of the correct fee.
    below_schedule_discount: float = 0.22
    #: Invoices never collected, or collected short.
    uncollected_rate: float = 0.11
    partial_collection_rate: float = 0.05
    #: Accounts where the custodian and the portfolio system disagree on value.
    custodian_variance_rate: float = 0.02
    custodian_variance_magnitude: float = 0.035
    #: Advisors in the CRM whose rep code does not match the custodian's.
    producer_key_mismatch_rate: float = 0.15
    #: Service events that blow their SLA.
    sla_breach_rate: float = 0.18
    #: Accounts flagged non-billable in the portfolio system but billed anyway.
    billing_non_billable_rate: float = 0.03


@dataclass(frozen=True)
class FirmSpec:
    firm_id: str
    legal_name: str
    role: str                       # platform|addon
    households: int
    producers: int
    close_date: dt.date | None = None
    #: Concentration of the book in the top advisor. The key-person finding.
    top_producer_share: float = 0.34
    #: Fully loaded margin the firm's cost base is sized to produce. Costs are
    #: derived from revenue rather than drawn independently: an unprofitable
    #: fixture makes every margin figure in the demo look like a bug.
    target_margin: float = 0.31
    #: Which sources this firm runs. Drives the fold-in estimate directly.
    sources: tuple[str, ...] = (
        "orion", "redtail", "schwab_custodian", "qbo", "manual_fee_schedule",
    )
    defects: DefectRates = field(default_factory=DefectRates)


@dataclass(frozen=True)
class EstateSpec:
    """One tenant: an acquirer with a platform firm and its add-ons."""

    tenant_slug: str
    tenant_name: str
    motion: str = "operating"
    firms: tuple[FirmSpec, ...] = ()
    #: Month-ends generated, ending at `period_end`.
    months: int = 24
    period_end: dt.date = dt.date(2026, 6, 30)
    #: Quarterly billing in arrears, which is what most RIAs actually run.
    billing_frequency: str = "quarterly"
    seed: int = 20260101

    def month_ends(self) -> list[dt.date]:
        out: list[dt.date] = []
        year, month = self.period_end.year, self.period_end.month
        for _ in range(self.months):
            out.append(_month_end(year, month))
            month -= 1
            if month == 0:
                year, month = year - 1, 12
        return sorted(out)


def _month_end(year: int, month: int) -> dt.date:
    if month == 12:
        return dt.date(year, 12, 31)
    return dt.date(year, month + 1, 1) - dt.timedelta(days=1)


#: The demo estate: a mid-market RIA aggregator with a platform and two add-ons.
#: The platform is a well-run firm with a billing problem; the first add-on is
#: the one whose fee schedules live in a spreadsheet; the second closed recently
#: and has barely been integrated. That spread is what makes the consolidated
#: view worth looking at.
DEMO_ESTATE = EstateSpec(
    tenant_slug="meridian-partners",
    tenant_name="Meridian Wealth Partners LP",
    motion="operating",
    months=24,
    period_end=dt.date(2026, 6, 30),
    firms=(
        FirmSpec(
            firm_id="MWP",
            legal_name="Meridian Wealth Advisors",
            role="platform",
            households=1400,
            producers=11,
            top_producer_share=0.28,
            target_margin=0.34,
            defects=DefectRates(
                unbilled_household_rate=0.04,
                below_schedule_rate=0.07,
                uncollected_rate=0.08,
                sla_breach_rate=0.12,
            ),
        ),
        FirmSpec(
            firm_id="HRC",
            legal_name="Harbour Ridge Capital",
            role="addon",
            households=620,
            producers=6,
            close_date=dt.date(2025, 3, 31),
            top_producer_share=0.41,
            target_margin=0.24,
            defects=DefectRates(
                unbilled_household_rate=0.11,
                below_schedule_rate=0.14,
                below_schedule_discount=0.28,
                uncollected_rate=0.16,
                custodian_variance_rate=0.035,
                producer_key_mismatch_rate=0.25,
                sla_breach_rate=0.24,
            ),
        ),
        FirmSpec(
            firm_id="CLB",
            legal_name="Calloway Brooks Financial",
            role="addon",
            households=340,
            producers=4,
            close_date=dt.date(2026, 1, 31),
            top_producer_share=0.52,
            target_margin=0.16,
            sources=("orion", "schwab_custodian", "qbo", "manual_fee_schedule"),
            defects=DefectRates(
                unbilled_household_rate=0.17,
                below_schedule_rate=0.19,
                below_schedule_discount=0.31,
                uncollected_rate=0.21,
                partial_collection_rate=0.09,
                custodian_variance_rate=0.05,
                producer_key_mismatch_rate=0.4,
                sla_breach_rate=0.33,
                billing_non_billable_rate=0.06,
            ),
        ),
    ),
)

#: A small estate for the test suite: same shapes, seconds to build.
TEST_ESTATE = EstateSpec(
    tenant_slug="test-acquirer",
    tenant_name="Test Acquirer LLC",
    motion="operating",
    months=4,
    period_end=dt.date(2026, 3, 31),
    firms=(
        FirmSpec("TF1", "Test Platform Firm", "platform", households=24, producers=3),
        FirmSpec("TF2", "Test Addon Firm", "addon", households=12, producers=2,
                 close_date=dt.date(2025, 9, 30)),
    ),
)
