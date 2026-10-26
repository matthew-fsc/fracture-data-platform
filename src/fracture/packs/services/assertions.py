"""Reconciliation assertions for the professional services pack.

Each entry is (table, what must be true, SQL that returns rows which should not
exist). A returned row fails the mart run. Written as "find the problem" rather
than "check the invariant" so a failure names the offending engagement rather
than just the rule.

The important one is that the realisation bridge closes. A waterfall with an
unaccounted residual reads as precision while being wrong, and the wealth pack
shipped exactly that defect before the equivalent assertion was added.
"""

from __future__ import annotations

ASSERTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "mart.time_recorded",
        "recorded hours must never be negative",
        """
        select firm_id, engagement_id, period_end, recorded_hours
          from mart.time_recorded where recorded_hours < 0 limit 5
        """,
    ),
    (
        "mart.time_recorded",
        "billable hours can never exceed recorded hours",
        """
        select firm_id, engagement_id, period_end, recorded_hours, billable_hours
          from mart.time_recorded where billable_hours > recorded_hours + 0.001
         limit 5
        """,
    ),
    (
        "mart.time_recorded",
        "one row per firm, engagement, period and grade",
        """
        select firm_id, engagement_id, period_end, grade, count(*)
          from mart.time_recorded group by 1,2,3,4 having count(*) > 1 limit 5
        """,
    ),
    (
        "mart.entitled_revenue",
        "entitlement must never be negative",
        """
        select firm_id, engagement_id, period_end, entitled_amount
          from mart.entitled_revenue where entitled_amount < 0 limit 5
        """,
    ),
    (
        "mart.entitled_revenue",
        "one row per firm, engagement and period",
        """
        select firm_id, engagement_id, period_end, count(*)
          from mart.entitled_revenue group by 1,2,3 having count(*) > 1 limit 5
        """,
    ),
    (
        "mart.entitled_revenue",
        "a capped engagement can never be entitled above its agreed fee",
        """
        select e.firm_id, e.engagement_id, e.period_end, e.entitled_amount, g.agreed_fee
          from mart.entitled_revenue e
          join (
            select distinct on (firm_id, engagement_id)
                   firm_id, engagement_id, agreed_fee, fee_basis
              from canon.engagement
             where superseded_at is null
             order by firm_id, engagement_id, recorded_at desc, canon_id desc
          ) g on g.firm_id = e.firm_id and g.engagement_id = e.engagement_id
         where g.fee_basis = 'capped'
           and g.agreed_fee is not null
           and e.entitled_amount > g.agreed_fee + 0.01
         limit 5
        """,
    ),
    (
        "mart.billed_collected",
        "collected can never exceed billed on the same period",
        """
        select firm_id, engagement_id, period_end, billed_amount, collected_amount
          from mart.billed_collected
         where collected_amount > billed_amount + 0.01
         limit 5
        """,
    ),
    (
        "mart.realisation_bridge",
        "the bridge must close: every step from time value must sum to collected",
        """
        with totals as (
          select firm_id, period_end,
                 sum(amount) filter (where direction in ('loss','gain'))  as movement,
                 max(amount) filter (where step = 'recorded_at_card')     as opening,
                 max(amount) filter (where step = 'collected')            as closing
            from mart.realisation_bridge group by 1, 2
        )
        select firm_id, period_end, opening, movement, closing,
               round(opening + movement - closing, 2) as residual
          from totals
         where abs(opening + movement - closing) > 0.05
         limit 5
        """,
    ),
    (
        "mart.realisation_bridge",
        "every bridge has all ten steps, or a step was silently dropped",
        """
        select firm_id, period_end, count(*) as steps
          from mart.realisation_bridge group by 1, 2 having count(*) <> 10 limit 5
        """,
    ),
    (
        "mart.utilisation",
        "utilisation must be a rate, not a count of hours",
        """
        select firm_id, timekeeper_id, period_end, billable_utilisation
          from mart.utilisation
         where billable_utilisation < 0 or billable_utilisation > 5
         limit 5
        """,
    ),
    (
        "mart.utilisation",
        "entry realisation is a fraction of recorded hours and cannot exceed one",
        """
        select firm_id, timekeeper_id, period_end, entry_realisation
          from mart.utilisation where entry_realisation > 1.0001 limit 5
        """,
    ),
    (
        "mart.lockup",
        "work in progress can never be negative",
        """
        select firm_id, engagement_id, period_end, wip_amount
          from mart.lockup where wip_amount < 0 limit 5
        """,
    ),
    (
        "mart.lockup",
        "overdue amount can never exceed the debtor balance it is drawn from",
        """
        select firm_id, engagement_id, period_end, debtor_amount, overdue_amount
          from mart.lockup where overdue_amount > debtor_amount + 0.01 limit 5
        """,
    ),
    (
        "mart.cost_allocation_check",
        "every pooled cost must land on exactly one engagement",
        """
        select firm_id, period_end, allocated_total, pooled_total, variance
          from mart.cost_allocation_check where abs(variance) > 0.05 limit 5
        """,
    ),
    (
        "mart.engagement_margin",
        "total cost must equal labour plus direct plus allocated overhead",
        """
        select firm_id, engagement_id, period_end, total_cost,
               labour_cost + direct_cost + allocated_overhead as parts
          from mart.engagement_margin
         where abs(total_cost - (labour_cost + direct_cost + allocated_overhead)) > 0.05
         limit 5
        """,
    ),
    (
        "mart.firm_scorecard",
        "the three yields must be ordered: card rate >= entitled >= billed is not "
        "required, but none may be negative",
        """
        select firm_id, period_end, card_rate_per_hour, entitled_per_hour,
               billed_per_hour, collected_per_hour
          from mart.firm_scorecard
         where card_rate_per_hour < 0 or entitled_per_hour < 0
            or billed_per_hour < 0 or collected_per_hour < 0
         limit 5
        """,
    ),
    (
        "mart.firm_scorecard",
        "one row per firm and period",
        """
        select firm_id, period_end, count(*)
          from mart.firm_scorecard group by 1,2 having count(*) > 1 limit 5
        """,
    ),
    (
        "mart.pipeline",
        "a stage weight must be between zero and one",
        """
        select firm_id, engagement_id, stage, stage_weight
          from mart.pipeline where stage_weight < 0 or stage_weight > 1 limit 5
        """,
    ),
)

#: Marts that must never be empty. An empty mart is the quietest possible
#: failure: every downstream figure renders as a zero and nothing raises.
NON_EMPTY: tuple[str, ...] = (
    "mart.time_recorded",
    "mart.entitled_revenue",
    "mart.billed_collected",
    "mart.lockup",
    "mart.realisation_bridge",
    "mart.utilisation",
    "mart.engagement_margin",
    "mart.cost_allocation_check",
    "mart.firm_scorecard",
    "mart.pipeline",
)
