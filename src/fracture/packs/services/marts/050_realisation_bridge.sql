-- The realisation bridge: every recorded hour at card rate, through to banked
-- cash, decomposed by cause.
--
-- The wealth pack's yield bridge has three stages and two gaps. A professional
-- services firm has five and four, because work sits in inventory before
-- anyone invoices it and because the price it is billed at is a separate
-- decision from the hours that were recorded:
--
--   recorded hours at card rate
--   less write-off at entry     scope, goodwill, training, admin, rework
--   = billable at card rate
--   less pricing                client discount, or a fixed fee below time value
--   = entitled                  what the engagement may bill
--   less realisation gap        what the bill actually went out at
--   = billed
--   less uncollected            what has not arrived
--   = collected
--
-- Four gaps, four owners. Write-off at entry belongs to whoever approved the
-- work and happens in the timesheet, which is why most practices never see it.
-- Pricing belongs to whoever agreed the fee. Realisation belongs to the
-- engagement partner. Collection belongs to finance. A single "realisation"
-- percentage collapses the middle two and sends the wrong person to fix it.
--
-- Long format, one row per step, so a new step does not require changing the
-- renderer. Signed: a negative step is a loss and a positive one a gain. The
-- pricing step is signed both ways on purpose -- a fixed fee priced above its
-- time value is a gain, and that is the profitable fixed-fee engagement.
--
-- The bridge must close. The assertion that checks it is in the pack's
-- assertions, and an earlier version of this model failed it: the opening
-- balance was billable hours at card, which is already net of write-off, so
-- the write-off step subtracted the same money twice. The structure below
-- closes by construction -- every subtotal is the previous one plus the step
-- between -- rather than by arithmetic that happens to agree.
--
-- depends_on: mart.time_recorded, mart.entitled_revenue, mart.billed_collected
drop table if exists mart.realisation_bridge;
create table mart.realisation_bridge as
with per_engagement as (
  select e.firm_id,
         e.period_end,
         e.gross_recorded_amount                          as recorded_value,
         e.gross_card_amount                              as billable_value,
         e.entitled_amount                                as entitled,
         coalesce(b.billed_amount, 0)                     as billed,
         coalesce(b.collected_amount, 0)                  as collected,
         e.recorded_hours,
         e.billable_hours,
         coalesce(b.billed_hours, 0)                      as billed_hours,
         -- Billed above entitlement, per engagement, before anything is
         -- summed. Netting it off at firm level would bury it inside the
         -- realisation gap, and an engagement billed above entitlement is a
         -- refund exposure rather than a windfall.
         greatest(coalesce(b.billed_amount, 0) - e.entitled_amount, 0) as over_billed
    from mart.entitled_revenue e
    left join mart.billed_collected b
      on b.firm_id = e.firm_id and b.engagement_id = e.engagement_id
     and b.period_end = e.period_end
),
base as (
  select firm_id,
         period_end,
         sum(recorded_value)   as recorded_value,
         sum(billable_value)   as billable_value,
         sum(entitled)         as entitled,
         sum(billed)           as billed,
         sum(collected)        as collected,
         sum(over_billed)      as over_billed,
         sum(recorded_hours)   as recorded_hours,
         sum(billable_hours)   as billable_hours,
         sum(billed_hours)     as billed_hours
    from per_engagement
   group by 1, 2
),
steps as (
  select firm_id, period_end, 1 as step_order, 'recorded_at_card' as step,
         'total' as direction, round(recorded_value, 2) as amount,
         recorded_hours as hours
    from base
  union all
  select firm_id, period_end, 2, 'writeoff_at_entry', 'loss',
         round(-(recorded_value - billable_value), 2),
         recorded_hours - billable_hours
    from base
  union all
  select firm_id, period_end, 3, 'billable_at_card', 'subtotal',
         round(billable_value, 2), billable_hours
    from base
  union all
  -- Discount against the card, or a fixed fee that differs from the time it
  -- consumed. Signed: above time value is a gain.
  select firm_id, period_end, 4, 'pricing',
         case when entitled >= billable_value then 'gain' else 'loss' end,
         round(entitled - billable_value, 2), null
    from base
  union all
  select firm_id, period_end, 5, 'entitled', 'subtotal',
         round(entitled, 2), billable_hours
    from base
  union all
  -- Net of the over-billing reported separately below, so the two steps plus
  -- the subtotal reconcile exactly rather than approximately.
  select firm_id, period_end, 6, 'realisation_gap', 'loss',
         round(-(entitled - billed + over_billed), 2), null
    from base
  union all
  select firm_id, period_end, 7, 'billed_above_entitlement', 'gain',
         round(over_billed, 2), null
    from base
  union all
  select firm_id, period_end, 8, 'billed', 'subtotal',
         round(billed, 2), billed_hours
    from base
  union all
  select firm_id, period_end, 9, 'uncollected', 'loss',
         round(-(billed - collected), 2), null
    from base
  union all
  select firm_id, period_end, 10, 'collected', 'total',
         round(collected, 2), null
    from base
)
select firm_id, period_end, step_order, step, direction, amount, hours
  from steps
 order by firm_id, period_end, step_order;

create index on mart.realisation_bridge (firm_id, period_end, step_order);
