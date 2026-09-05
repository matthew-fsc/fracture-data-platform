-- The yield bridge: schedule to collected, in basis points, by cause.
--
-- Read left to right it answers "the fee schedules say this book should earn
-- 84.6bps a year; we collected 51.5; where did 33 basis points go, and who owns
-- each piece?" That is a different question from "what was revenue", and it is
-- the one an operating partner can act on.
--
-- Emitted long rather than wide: each row is one step of the waterfall, so the
-- chart is a data structure and adding a leakage cause does not change any code.
--
-- depends_on: mart.firm_scorecard, mart.firm_month
drop table if exists mart.yield_bridge;
create table mart.yield_bridge as
with base as (
  select s.firm_id, s.period_end, s.total_aum, s.schedule_yield_bps,
         s.actual_yield_bps, s.collected_yield_bps,
         s.leak_never_invoiced, s.leak_below_schedule, s.leak_uncollected,
         s.over_billed
    from mart.firm_scorecard s
),
steps as (
  select firm_id, period_end, 1 as step_order, 'schedule' as step,
         'Schedule entitlement' as label, 'total' as step_kind,
         schedule_yield_bps as bps, null::numeric as delta_bps,
         'What the assigned fee schedules earn on this book' as detail
    from base
  union all
  select firm_id, period_end, 2, 'never_invoiced', 'Never invoiced', 'loss',
         null,
         -round(leak_never_invoiced * 4 / nullif(total_aum, 0) * 10000, 2),
         'Households on a schedule that were not billed for the period'
    from base
  union all
  select firm_id, period_end, 3, 'below_schedule', 'Billed below schedule', 'loss',
         null,
         -round(leak_below_schedule * 4 / nullif(total_aum, 0) * 10000, 2),
         'Invoiced at less than the assigned schedule'
    from base
  union all
  select firm_id, period_end, 4, 'over_billed', 'Billed above schedule', 'gain',
         null,
         round(over_billed * 4 / nullif(total_aum, 0) * 10000, 2),
         'Invoiced above the assigned schedule, or with no schedule assigned'
    from base
  union all
  select firm_id, period_end, 5, 'actual', 'Invoiced', 'total',
         actual_yield_bps, null,
         'What the firm actually billed'
    from base
  union all
  select firm_id, period_end, 6, 'uncollected', 'Not collected', 'loss',
         null,
         -round(leak_uncollected * 4 / nullif(total_aum, 0) * 10000, 2),
         'Invoiced correctly and not collected in full'
    from base
  union all
  select firm_id, period_end, 7, 'collected', 'Collected', 'total',
         collected_yield_bps, null,
         'Cash against this book'
    from base
)
select * from steps;

create index on mart.yield_bridge (firm_id, period_end, step_order);
