"""Reconciliation assertions for the wealth pack.

Each entry is (table, what must be true, SQL that returns rows which should not
exist). A returned row fails the mart run. They are written as "find the
problem" rather than "check the invariant" so the failure can name the
offending rows rather than just the rule.

These live in the pack, not the chassis, because every one of them is a claim
about a wealth-management book: that AUM is non-negative, that a household sits
in one firm, that the yield bridge closes. A professional-services engagement
asserts different things and must not inherit these.
"""

from __future__ import annotations

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
        "mart.firm_scorecard",
        "schedule less the billing gap equals actual",
        "select firm_id, period_end, schedule_yield_bps, billing_gap_bps, actual_yield_bps "
        "from mart.firm_scorecard "
        "where abs(schedule_yield_bps - billing_gap_bps - actual_yield_bps) > 0.02 limit 5",
    ),
    (
        "mart.firm_scorecard",
        "the yield bridge must close: actual less the collection gap equals collected",
        "select firm_id, period_end, actual_yield_bps, collection_gap_bps, collected_yield_bps "
        "from mart.firm_scorecard "
        "where abs(actual_yield_bps - collection_gap_bps - collected_yield_bps) > 0.02 limit 5",
    ),
    (
        "mart.firm_scorecard",
        "collection rate cannot exceed 1",
        "select firm_id, period_end, collection_rate from mart.firm_scorecard "
        "where collection_rate > 1.0001 limit 5",
    ),
    (
        "mart.firm_scorecard",
        "leakage components must sum to the leakage total",
        "select firm_id, period_end, leak_total from mart.firm_scorecard "
        "where abs(leak_total - (leak_never_invoiced + leak_below_schedule + leak_uncollected)) "
        "> 0.02 limit 5",
    ),
    (
        "mart.yield_bridge",
        "the bridge steps must account for the whole move from schedule to collected",
        """
        select b.firm_id, b.period_end, s.schedule_yield_bps, s.collected_yield_bps, b.total_loss
          from (
            select firm_id, period_end, sum(delta_bps) as total_loss
              from mart.yield_bridge where step_kind in ('loss', 'gain')
             group by 1, 2
          ) b
          join mart.firm_scorecard s
            on s.firm_id = b.firm_id and s.period_end = b.period_end
         where abs((s.schedule_yield_bps + b.total_loss) - s.collected_yield_bps) > 0.03
         limit 5
        """,
    ),
    (
        "mart.firm_kpi",
        "every KPI must declare a direction the reader can act on",
        "select distinct kpi, direction from mart.firm_kpi "
        "where direction not in ('higher_better','lower_better','neutral') limit 5",
    ),
    (
        "mart.firm_kpi",
        "rank 1 must be the best value for the metric's direction, not merely the largest",
        """
        select k.kpi, k.firm_id, k.value, k.direction
          from mart.firm_kpi k
         where k.firm_rank = 1
           and k.value is distinct from (
             select case k.direction
                      when 'higher_better' then max(k2.value)
                      when 'lower_better'  then min(k2.value)
                    end
               from mart.firm_kpi k2 where k2.kpi = k.kpi
           )
         limit 5
        """,
    ),
    (
        "mart.household_economics",
        "cost components must sum to cost to serve",
        "select firm_id, household_id from mart.household_economics "
        "where abs(cost_to_serve - (direct_service_cost + producer_cost + allocated_cost)) "
        "> 0.02 limit 5",
    ),
    (
        "mart.producer_scorecard",
        "a producer's book share must be a fraction",
        "select firm_id, producer_id, book_share from mart.producer_scorecard "
        "where book_share < 0 or book_share > 1.0001 limit 5",
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
    "mart.firm_scorecard",
    "mart.yield_bridge",
    "mart.firm_kpi",
    "mart.household_economics",
    "mart.household_distribution",
    "mart.producer_scorecard",
)
