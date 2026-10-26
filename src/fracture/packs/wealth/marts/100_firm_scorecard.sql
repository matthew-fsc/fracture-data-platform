-- Firm scorecard: every metric normalised so firms of different size compare.
--
-- The problem this solves: the platform firm bills 4.8x what the smallest
-- add-on bills. That fact says nothing about whether either is well run.
-- Everything below is either a rate, a per-unit figure, or basis points on
-- AUM, so a $400m firm and a $1.7bn firm sit on the same axis.
--
-- Three yields, all annualised basis points on average AUM, are the spine:
--   schedule_yield_bps   what the fee schedules say the book should earn
--   actual_yield_bps     what was invoiced
--   collected_yield_bps  what arrived
-- The gaps between them are the two things a firm can get wrong, separated:
-- billing execution (schedule -> actual) and collections (actual -> collected).
-- A firm can be expensive and still land last on actual yield; without the
-- schedule baseline that reads as "cheap" and gets the wrong fix.
--
-- Annualisation: billing here is quarterly, so quarterly amounts are multiplied
-- by 4 before dividing by AUM. Stated because an un-annualised bps figure looks
-- like a plausible fee and is wrong by 4x.
--
-- depends_on: mart.firm_month, mart.unbilled, mart.margin, mart.producer_book, mart.concentration, mart.sla_summary, mart.service_sla, mart.household_aum
drop table if exists mart.firm_scorecard;
create table mart.firm_scorecard as
with billed_periods as (
  -- Quarter ends that actually carry billing. Month ends in between have AUM
  -- but no invoices, and averaging them into a yield understates it.
  select firm_id, period_end, total_aum, billable_aum, household_count,
         expected_amount, billed_amount, collected_amount, outstanding_amount,
         never_invoiced, below_schedule, uncollected, total_leakage,
         loaded_margin, loaded_margin_pct
    from mart.firm_month
   where billed_amount is not null and expected_amount is not null
),
history as (
  select firm_id, period_end,
         count(*) over (partition by firm_id order by period_end) as quarters_of_history
    from billed_periods
),
costs as (
  select firm_id, period_end,
         sum(direct_service_cost + producer_cost + allocated_cost) as cost_to_serve,
         sum(producer_cost + direct_service_cost)                  as direct_cost,
         sum(allocated_cost)                                       as overhead_cost,
         count(*)                                                  as costed_households
    from mart.margin
   group by 1, 2
),
accounts as (
  select firm_id, as_of_date, sum(account_count) as account_count,
         sum(non_billable_accounts) as non_billable_accounts
    from mart.household_aum group by 1, 2
),
producers as (
  select firm_id, as_of_date,
         count(*) filter (where term_date is null)  as active_producers,
         count(*)                                   as producers_with_book,
         sum(household_count)                       as assigned_households
    from mart.producer_book group by 1, 2
),
concentration as (
  select firm_id,
         max(book_share)                                          as top_producer_share,
         sum(book_share) filter (where book_rank <= 3)            as top3_producer_share,
         coalesce(sum(book_value) filter (where has_departed), 0) as departed_book_value,
         sum(book_value)                                          as ranked_book_value
    from mart.concentration group by 1
),
service as (
  select firm_id, period_end,
         sum(event_count)      as sla_events,
         sum(breach_count)     as sla_breaches,
         sum(still_open_count) as sla_open
    from mart.sla_summary group by 1, 2
),
cycle as (
  select firm_id, period_end,
         percentile_cont(0.5) within group (order by elapsed_hours) as cycle_p50_hours,
         percentile_cont(0.9) within group (order by elapsed_hours) as cycle_p90_hours
    from mart.service_sla group by 1, 2
),
-- Household revenue concentration: a firm whose top ten clients are a third of
-- revenue is a different risk from one where they are a twentieth, at identical
-- margin.
-- Billed above the schedule, or billed with no schedule assigned at all. Small,
-- but it is the residual that makes the yield bridge close, and a waterfall
-- with an unexplained gap is not a waterfall. It is also a finding in its own
-- right: over-billing a client is a refund liability, not a windfall.
over_billing as (
  select firm_id, period_end,
         coalesce(sum(billed_amount - coalesce(expected_amount, 0))
                  filter (where finding in ('billed_above_schedule',
                                            'billed_without_schedule')), 0) as over_billed
    from mart.unbilled
   group by 1, 2
),
client_concentration as (
  select firm_id, period_end,
         sum(billed_amount) filter (where revenue_rank <= 10)
           / nullif(sum(billed_amount), 0) as top10_client_revenue_share
    from (
      select firm_id, period_end, household_id, billed_amount,
             rank() over (partition by firm_id, period_end order by billed_amount desc)
               as revenue_rank
        from mart.margin
    ) ranked
   group by 1, 2
)
select b.firm_id,
       b.period_end,
       h.quarters_of_history,
       h.quarters_of_history < 4                          as short_history,

       -- scale
       b.total_aum,
       b.billable_aum,
       b.household_count,
       a.account_count,
       a.non_billable_accounts,
       coalesce(p.active_producers, 0)                    as active_producers,

       -- per-unit scale, which is what makes firms comparable at all
       round(b.total_aum / nullif(b.household_count, 0), 2)          as aum_per_household,
       round(b.household_count::numeric / nullif(p.active_producers, 0), 2)
                                                                     as households_per_producer,
       round(b.total_aum / nullif(p.active_producers, 0), 2)         as aum_per_producer,

       -- revenue
       b.expected_amount,
       b.billed_amount,
       b.collected_amount,
       b.outstanding_amount,

       -- the three yields, annualised basis points on AUM
       round(b.expected_amount  * 4 / nullif(b.total_aum, 0) * 10000, 2) as schedule_yield_bps,
       round(b.billed_amount    * 4 / nullif(b.total_aum, 0) * 10000, 2) as actual_yield_bps,
       round(b.collected_amount * 4 / nullif(b.total_aum, 0) * 10000, 2) as collected_yield_bps,

       -- and the two gaps between them, separated by cause
       round((b.expected_amount - b.billed_amount) * 4 / nullif(b.total_aum, 0) * 10000, 2)
                                                                     as billing_gap_bps,
       round((b.billed_amount - b.collected_amount) * 4 / nullif(b.total_aum, 0) * 10000, 2)
                                                                     as collection_gap_bps,

       -- execution rates
       round(b.billed_amount / nullif(b.expected_amount, 0), 6)      as realization_rate,
       round(b.collected_amount / nullif(b.billed_amount, 0), 6)     as collection_rate,
       round(b.total_leakage / nullif(b.expected_amount, 0), 6)      as leakage_rate,

       -- leakage by cause: three different owners, three different fixes
       coalesce(b.never_invoiced, 0)                                 as leak_never_invoiced,
       coalesce(b.below_schedule, 0)                                 as leak_below_schedule,
       coalesce(b.uncollected, 0)                                    as leak_uncollected,
       coalesce(b.total_leakage, 0)                                  as leak_total,
       coalesce(ob.over_billed, 0)                                   as over_billed,
       round(coalesce(ob.over_billed, 0) * 4 / nullif(b.total_aum, 0) * 10000, 2)
                                                                     as over_billed_bps,

       -- profitability, fully loaded
       b.loaded_margin,
       b.loaded_margin_pct,
       coalesce(c.cost_to_serve, 0)                                  as cost_to_serve,
       coalesce(c.direct_cost, 0)                                    as direct_cost,
       coalesce(c.overhead_cost, 0)                                  as overhead_cost,
       round(coalesce(c.cost_to_serve, 0) / nullif(b.household_count, 0), 2)
                                                                     as cost_per_household,
       round(b.billed_amount / nullif(b.household_count, 0), 2)      as revenue_per_household,
       round(b.loaded_margin / nullif(b.household_count, 0), 2)      as margin_per_household,
       round(b.billed_amount / nullif(p.active_producers, 0), 2)     as revenue_per_producer,
       round(b.loaded_margin / nullif(p.active_producers, 0), 2)     as margin_per_producer,
       round(coalesce(c.cost_to_serve, 0) / nullif(b.billed_amount, 0), 6)
                                                                     as cost_income_ratio,

       -- operations
       coalesce(s.sla_events, 0)                                     as sla_events,
       coalesce(s.sla_breaches, 0)                                   as sla_breaches,
       coalesce(s.sla_open, 0)                                       as sla_open,
       round(s.sla_breaches::numeric / nullif(s.sla_events, 0), 6)   as sla_breach_rate,
       round(1 - s.sla_breaches::numeric / nullif(s.sla_events, 0), 6) as sla_attainment,
       round(cy.cycle_p50_hours::numeric, 1)                         as cycle_p50_hours,
       round(cy.cycle_p90_hours::numeric, 1)                         as cycle_p90_hours,

       -- concentration risk
       con.top_producer_share,
       con.top3_producer_share,
       con.departed_book_value,
       round(con.departed_book_value / nullif(b.total_aum, 0), 6)    as departed_book_share,
       round(cc.top10_client_revenue_share, 6)                       as top10_client_revenue_share

  from billed_periods b
  join history h on h.firm_id = b.firm_id and h.period_end = b.period_end
  left join costs c on c.firm_id = b.firm_id and c.period_end = b.period_end
  left join accounts a on a.firm_id = b.firm_id and a.as_of_date = b.period_end
  left join producers p on p.firm_id = b.firm_id and p.as_of_date = b.period_end
  left join service s on s.firm_id = b.firm_id and s.period_end = b.period_end
  left join cycle cy on cy.firm_id = b.firm_id and cy.period_end = b.period_end
  left join over_billing ob on ob.firm_id = b.firm_id and ob.period_end = b.period_end
  left join client_concentration cc on cc.firm_id = b.firm_id and cc.period_end = b.period_end
  left join concentration con on con.firm_id = b.firm_id;

create index on mart.firm_scorecard (firm_id, period_end);
create index on mart.firm_scorecard (period_end);
