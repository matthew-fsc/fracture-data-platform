"""Executing the mart models.

Models are plain parameterised SQL, run in filename order against one tenant
with the pack's system time bound. The runner does three things a bare `psql -f`
would not:

1. Refuses to run SQL that reads `canon` without a system-time filter. A mart
   that quietly reads superseded rows produces a number that cannot be
   reproduced, and reproducibility is what the pack is sold on.
2. Runs assertions after each model. An empty mart, a negative AUM or a
   duplicate grain key fails the run rather than rendering a plausible-looking
   zero into a board pack.
3. Records timing and row counts, so a model that silently starts returning
   half its rows is visible.
"""

from __future__ import annotations

import datetime as dt
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from psycopg2.extensions import connection as PGConnection

from fracture.canon.bitemporal import assert_temporal_filter
from fracture.core import db
from fracture.core.errors import FractureError
from fracture.core.logging import get_logger

log = get_logger("marts.runner")

MODEL_DIR = Path(__file__).parent / "models"
_DEPENDS = re.compile(r"--\s*depends_on:\s*(.+)")


class MartAssertionError(FractureError):
    """A mart produced output that cannot be right."""


@dataclass(frozen=True)
class Model:
    name: str
    sql: str
    depends_on: tuple[str, ...]
    path: Path

    @property
    def outputs(self) -> tuple[str, ...]:
        return tuple(
            sorted(set(re.findall(r"create table (mart\.\w+)", self.sql, re.IGNORECASE)))
        )


@dataclass
class ModelResult:
    name: str
    duration_s: float
    row_counts: dict[str, int] = field(default_factory=dict)

    def __str__(self) -> str:
        counts = ", ".join(f"{t}={n}" for t, n in sorted(self.row_counts.items()))
        return f"{self.name} ({self.duration_s:.2f}s) {counts}"


@dataclass
class MartRunResult:
    system_time: dt.datetime
    models: list[ModelResult] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(sum(m.row_counts.values()) for m in self.models)

    def summary(self) -> str:
        return f"{len(self.models)} models, {self.total_rows} mart rows"


def load_models(directory: Path | None = None) -> list[Model]:
    directory = directory or MODEL_DIR
    models: list[Model] = []
    for path in sorted(directory.glob("*.sql")):
        sql = path.read_text()
        match = _DEPENDS.search(sql)
        depends = tuple(d.strip() for d in match.group(1).split(",")) if match else ()
        models.append(Model(path.stem, sql, depends, path))
    if not models:  # pragma: no cover - packaging error
        raise FileNotFoundError(f"no mart models under {directory}")
    return models


# -- assertions --------------------------------------------------------------
#
# Each entry is (table, description, SQL returning rows that should not exist).
# A returned row is a failure. Written as "find the problem" rather than "check
# the invariant" so the error message can name the offending rows.

ASSERTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "mart.household_aum",
        "household AUM must never be negative",
        "select firm_id, household_id, as_of_date, total_value from mart.household_aum "
        "where total_value < 0 limit 5",
    ),
    (
        "mart.household_aum",
        "one row per firm, household and date",
        "select firm_id, household_id, as_of_date, count(*) from mart.household_aum "
        "group by 1,2,3 having count(*) > 1 limit 5",
    ),
    (
        "mart.expected_revenue",
        "expected fees must be non-negative",
        "select firm_id, household_id, period_end, expected_amount from mart.expected_revenue "
        "where expected_amount < 0 limit 5",
    ),
    (
        "mart.expected_revenue",
        "a household on one schedule yields one expectation per period",
        "select firm_id, household_id, period_end, count(*) from mart.expected_revenue "
        "group by 1,2,3 having count(*) > 1 limit 5",
    ),
    (
        "mart.billed_revenue",
        "collected must not exceed billed",
        "select firm_id, invoice_id, billed_amount, collected_amount from mart.billed_revenue "
        "where collected_amount > billed_amount + 0.01 limit 5",
    ),
    (
        "mart.unbilled",
        "every row must carry a finding",
        "select firm_id, household_id, period_end from mart.unbilled "
        "where finding is null limit 5",
    ),
    (
        "mart.unbilled",
        "variance must equal expected minus billed",
        "select firm_id, household_id, period_end, variance_amount from mart.unbilled "
        "where abs(variance_amount - (expected_amount - billed_amount)) > 0.011 limit 5",
    ),
    (
        "mart.leakage",
        "leakage amounts must be non-negative",
        "select firm_id, period_end, leakage_type, amount from mart.leakage "
        "where amount < -0.01 limit 5",
    ),
    (
        "mart.producer_book",
        "book value must be non-negative",
        "select firm_id, producer_id, as_of_date from mart.producer_book "
        "where book_value < 0 limit 5",
    ),
    (
        "mart.concentration",
        "book shares must sum to 1 per firm",
        "select firm_id, sum(book_share) as total from mart.concentration "
        "group by 1 having abs(sum(book_share) - 1) > 0.001 limit 5",
    ),
    (
        "mart.cost_allocation_check",
        "every cost in a billed quarter must be allocated somewhere in mart.margin",
        "select firm_id, period_end, cost_total, allocated_total, unallocated "
        "from mart.cost_allocation_check where abs(unallocated) > 1.00 limit 5",
    ),
    (
        "mart.sla_summary",
        "breach rate must be a fraction",
        "select firm_id, period_end, event_type, breach_rate from mart.sla_summary "
        "where breach_rate < 0 or breach_rate > 1 limit 5",
    ),
    (
        "mart.firm_month",
        "billable AUM cannot exceed total AUM",
        "select firm_id, period_end from mart.firm_month "
        "where billable_aum > total_aum + 0.01 limit 5",
    ),
    (
        "mart.consolidated_month",
        "consolidated AUM must equal the sum of its firms",
        """
        select c.period_end, c.total_aum, f.total
          from mart.consolidated_month c
          join (select period_end, sum(total_aum) as total from mart.firm_month group by 1) f
            on f.period_end = c.period_end
         where abs(c.total_aum - f.total) > 0.01
         limit 5
        """,
    ),
)

#: Marts that must never be empty. An empty mart is the quietest possible
#: failure: every downstream figure renders as a zero and nothing errors.
NON_EMPTY: tuple[str, ...] = (
    "mart.household_aum",
    "mart.expected_revenue",
    "mart.billed_revenue",
    "mart.unbilled",
    "mart.producer_book",
    "mart.concentration",
    "mart.service_sla",
    "mart.firm_month",
    "mart.consolidated_month",
)


class MartRunner:
    def __init__(self, models: Sequence[Model] | None = None) -> None:
        self.models = list(models) if models is not None else load_models()

    def run(
        self,
        conn: PGConnection,
        system_time: dt.datetime,
        assert_after: bool = True,
    ) -> MartRunResult:
        result = MartRunResult(system_time=system_time)
        params = {"system_time": system_time}
        for model in self.models:
            assert_temporal_filter(model.sql)
            started = time.perf_counter()
            with conn.cursor() as cur:
                cur.execute(model.sql, params)
            duration = time.perf_counter() - started
            counts = {
                table: int(db.scalar(conn, f"select count(*) from {table}") or 0)
                for table in model.outputs
            }
            model_result = ModelResult(model.name, duration, counts)
            result.models.append(model_result)
            log.info("mart %s", model_result)
        if assert_after:
            self.assert_sane(conn)
        log.info("mart run complete: %s", result.summary())
        return result

    def assert_sane(self, conn: PGConnection) -> None:
        failures: list[str] = []
        for table in NON_EMPTY:
            count = int(db.scalar(conn, f"select count(*) from {table}") or 0)
            if count == 0:
                failures.append(f"{table} is empty")
        for table, description, sql in ASSERTIONS:
            rows = db.query(conn, sql)
            if rows:
                failures.append(f"{table}: {description} -- {len(rows)} offending row(s): {rows[:3]}")
        if failures:
            raise MartAssertionError(
                "mart assertions failed:\n  " + "\n  ".join(failures)
            )


def build_marts(
    conn: PGConnection, system_time: dt.datetime, assert_after: bool = True
) -> MartRunResult:
    return MartRunner().run(conn, system_time, assert_after=assert_after)
