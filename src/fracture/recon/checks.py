"""Reconciliation checks.

These run as assets on every refresh, not as a fold-in afterthought (spec 7).
The pattern is always the same: a figure this platform computed, against a
figure the source system reports for itself, with a tolerance and a hard failure
above it.

The tolerance is per check and stated. A check with no tolerance is a check that
passes or fails on floating-point noise; a check with a generous one is a check
that passes while the number is wrong.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Sequence

from psycopg2.extensions import connection as PGConnection
from psycopg2.extras import Json

from fracture.core import db
from fracture.core.errors import ReconciliationBreach
from fracture.core.logging import get_logger

log = get_logger("recon.checks")


@dataclass
class CheckResult:
    check_name: str
    firm_id: str
    period_start: dt.date
    period_end: dt.date
    grain_key: str
    expected: Decimal | None
    actual: Decimal | None
    tolerance_pct: Decimal
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def variance(self) -> Decimal | None:
        if self.expected is None or self.actual is None:
            return None
        return self.actual - self.expected

    @property
    def variance_pct(self) -> Decimal | None:
        if self.expected in (None, 0) or self.actual is None:
            return None
        return (self.actual - self.expected) / abs(self.expected)

    @property
    def passed(self) -> bool:
        if self.expected is None or self.actual is None:
            # A missing counterparty figure is a failure, not a pass. "We could
            # not check" and "we checked and it agrees" must never look alike.
            return False
        pct = self.variance_pct
        return pct is not None and abs(pct) <= self.tolerance_pct

    def describe(self) -> str:
        pct = self.variance_pct
        pct_text = f"{pct:.4%}" if pct is not None else "n/a"
        return (
            f"{self.check_name}[{self.firm_id}{'/' + self.grain_key if self.grain_key else ''}] "
            f"{self.period_end}: expected {self.expected}, actual {self.actual}, "
            f"variance {pct_text} (tolerance {self.tolerance_pct:.2%})"
        )


@dataclass
class ReconReport:
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)
    results: list[CheckResult] = field(default_factory=list)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed]

    @property
    def passed(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        return (
            f"{len(self.results) - len(self.failures)}/{len(self.results)} checks passed"
            + (f"; {len(self.failures)} failing" if self.failures else "")
        )

    def raise_if_failed(self, max_failures: int = 0) -> None:
        if len(self.failures) > max_failures:
            detail = "\n  ".join(f.describe() for f in self.failures[:10])
            raise ReconciliationBreach(
                f"{len(self.failures)} reconciliation check(s) breached tolerance:\n  {detail}"
            )


#: Tolerances, per check. Stated here rather than passed in, so changing one is
#: a reviewable diff rather than a call-site argument nobody notices.
TOLERANCES: dict[str, Decimal] = {
    "aum_total": Decimal("0.0001"),          # custodian totals should be exact
    "billed_vs_invoices": Decimal("0.0001"),
    "collected_vs_receipts": Decimal("0.0001"),
    "consolidated_rollup": Decimal("0.000001"),
}


def check_aum_against_custodian(
    conn: PGConnection, tolerance: Decimal | None = None
) -> list[CheckResult]:
    """Our AUM by firm and month-end against the custodian's own control total.

    This is the check that makes the diligence reproducibility finding real: if
    the AUM shown to the seller does not reproduce from custodian records, that
    variance percentage is the deliverable.
    """
    tolerance = tolerance if tolerance is not None else TOLERANCES["aum_total"]
    rows = db.query(
        conn,
        """
        with ours as (
          select firm_id, as_of_date, sum(total_value) as total
            from mart.household_aum group by 1, 2
        )
        select c.firm_id, c.period_start, c.period_end, c.expected_value as expected,
               o.total as actual
          from recon.control_total c
          left join ours o on o.firm_id = c.firm_id and o.as_of_date = c.period_start
         where c.check_name = 'aum_total'
         order by c.firm_id, c.period_start
        """,
    )
    return [
        CheckResult(
            check_name="aum_total",
            firm_id=r["firm_id"],
            period_start=r["period_start"],
            period_end=r["period_end"],
            grain_key="",
            expected=r["expected"],
            actual=r["actual"],
            tolerance_pct=tolerance,
        )
        for r in rows
    ]


def check_billed_against_invoices(
    conn: PGConnection, tolerance: Decimal | None = None
) -> list[CheckResult]:
    """The billed-revenue mart against the raw invoice headers it derives from.

    A mart that quietly drops rows -- a join that became inner, a filter that
    became too narrow -- is invisible without this.
    """
    tolerance = tolerance if tolerance is not None else TOLERANCES["billed_vs_invoices"]
    rows = db.query(
        conn,
        """
        with canon_totals as (
          select firm_id,
                 coalesce(period_end, issued_on) as period_end,
                 sum(total_amount) as expected
            from (
              select distinct on (firm_id, invoice_id)
                     firm_id, invoice_id, period_end, issued_on, total_amount
                from canon.invoice
               where superseded_at is null
               order by firm_id, invoice_id, recorded_at desc
            ) i
           group by 1, 2
        ),
        mart_totals as (
          select firm_id, period_end, sum(billed_amount) as actual
            from mart.billed_revenue group by 1, 2
        )
        select c.firm_id, c.period_end, c.expected, m.actual
          from canon_totals c
          left join mart_totals m on m.firm_id = c.firm_id and m.period_end = c.period_end
         order by c.firm_id, c.period_end
        """,
    )
    return [
        CheckResult(
            check_name="billed_vs_invoices",
            firm_id=r["firm_id"],
            period_start=r["period_end"],
            period_end=r["period_end"],
            grain_key="",
            expected=r["expected"],
            actual=r["actual"],
            tolerance_pct=tolerance,
        )
        for r in rows
    ]


def check_collected_against_receipts(
    conn: PGConnection, tolerance: Decimal | None = None
) -> list[CheckResult]:
    """Collections in the mart against applied cash receipts in canon."""
    tolerance = tolerance if tolerance is not None else TOLERANCES["collected_vs_receipts"]
    rows = db.query(
        conn,
        """
        with applications as (
          select distinct on (firm_id, receipt_id, invoice_id)
                 firm_id, receipt_id, invoice_id, amount_applied
            from canon.receipt_application
           where superseded_at is null
           order by firm_id, receipt_id, invoice_id, recorded_at desc
        ),
        expected as (
          select a.firm_id, b.period_end, sum(a.amount_applied) as expected
            from applications a
            join mart.billed_revenue b
              on b.firm_id = a.firm_id and b.invoice_id = a.invoice_id
           group by 1, 2
        ),
        actual as (
          select firm_id, period_end, sum(collected_amount) as actual
            from mart.billed_revenue group by 1, 2
        )
        select e.firm_id, e.period_end, e.expected, a.actual
          from expected e
          left join actual a on a.firm_id = e.firm_id and a.period_end = e.period_end
         order by e.firm_id, e.period_end
        """,
    )
    return [
        CheckResult(
            check_name="collected_vs_receipts",
            firm_id=r["firm_id"],
            period_start=r["period_end"],
            period_end=r["period_end"],
            grain_key="",
            expected=r["expected"],
            actual=r["actual"],
            tolerance_pct=tolerance,
        )
        for r in rows
    ]


def check_consolidated_rollup(
    conn: PGConnection, tolerance: Decimal | None = None
) -> list[CheckResult]:
    """The consolidated view against the sum of its firms.

    Trivial arithmetic, and exactly the thing that breaks the first time a firm
    is added with a slightly different grain.
    """
    tolerance = tolerance if tolerance is not None else TOLERANCES["consolidated_rollup"]
    rows = db.query(
        conn,
        """
        select c.period_end,
               (select sum(total_aum) from mart.firm_month f where f.period_end = c.period_end)
                 as expected,
               c.total_aum as actual
          from mart.consolidated_month c
         order by c.period_end
        """,
    )
    return [
        CheckResult(
            check_name="consolidated_rollup",
            firm_id="__consolidated__",
            period_start=r["period_end"],
            period_end=r["period_end"],
            grain_key="",
            expected=r["expected"],
            actual=r["actual"],
            tolerance_pct=tolerance,
        )
        for r in rows
    ]


CHECKS: tuple[Callable[[PGConnection], list[CheckResult]], ...] = (
    check_aum_against_custodian,
    check_billed_against_invoices,
    check_collected_against_receipts,
    check_consolidated_rollup,
)


def run_all(
    conn: PGConnection,
    checks: Sequence[Callable[[PGConnection], list[CheckResult]]] = CHECKS,
    persist: bool = True,
    run_id: uuid.UUID | None = None,
) -> ReconReport:
    report = ReconReport(run_id=run_id or uuid.uuid4())
    for check in checks:
        report.results.extend(check(conn))
    if persist:
        persist_results(conn, report.results, report.run_id)
    log.info("reconciliation %s: %s", report.run_id, report.summary())
    return report


def persist_results(
    conn: PGConnection, results: Sequence[CheckResult], run_id: uuid.UUID
) -> None:
    if not results:
        return
    rows = [
        (
            run_id, r.firm_id, r.check_name, r.period_start, r.period_end, r.grain_key,
            r.expected, r.actual, r.variance, r.variance_pct, r.tolerance_pct,
            r.passed, Json(r.detail),
        )
        for r in results
    ]
    db.execute_values(
        conn,
        """
        insert into recon.result
          (run_id, firm_id, check_name, period_start, period_end, grain_key, expected,
           actual, variance, variance_pct, tolerance_pct, passed, detail)
        values %s
        """,
        rows,
    )


def unacknowledged_drift(conn: PGConnection) -> list[dict[str, Any]]:
    """Schema changes nobody has signed off on yet."""
    return db.query(
        conn,
        """
        select firm_id, source_id, observed_at, added_fields, removed_fields
          from recon.schema_drift
         where acknowledged_by is null
         order by observed_at desc
        """,
    )


def open_source_variances(conn: PGConnection, limit: int = 200) -> list[dict[str, Any]]:
    """Places two sources disagree and nobody has adjudicated."""
    return db.query(
        conn,
        f"""
        select firm_id, entity, canon_id, authoritative_source, deferred_source,
               detail, observed_at
          from recon.source_variance
         where resolved_by is null
         order by observed_at desc
         limit {int(limit)}
        """,
    )
