-- The comparability layer: one row per practice and period, every figure a
-- rate or a per-unit measure.
--
-- Same design constraint as the wealth pack. A four-partner practice and a
-- forty-partner practice cannot be compared on fees, and a view that tries
-- ranks them by headcount and teaches nothing. So: three yields per hour,
-- utilisation, lockup in days, margin as a percentage, and fee per head.
--
-- depends_on: mart.entitled_revenue, mart.billed_collected, mart.time_recorded, mart.utilisation, mart.lockup, mart.engagement_margin
drop table if exists mart.firm_scorecard;
create table mart.firm_scorecard as
with base as (
  select e.firm_id,
         e.period_end,
         sum(e.recorded_hours)                            as recorded_hours,
         sum(e.billable_hours)                            as billable_hours,
         sum(e.entitled_amount)                           as entitled_amount,
         sum(e.time_value_amount)                         as time_value_amount,
         sum(e.gross_recorded_amount)                     as recorded_value,
         sum(e.gross_card_amount)                         as billable_value,
         sum(coalesce(b.billed_amount, 0))                as billed_amount,
         sum(coalesce(b.collected_amount, 0))             as collected_amount,
         count(distinct e.engagement_id)                  as engagement_count,
         count(distinct e.client_id)                      as client_count
    from mart.entitled_revenue e
    left join mart.billed_collected b
      on b.firm_id = e.firm_id and b.engagement_id = e.engagement_id
     and b.period_end = e.period_end
   group by 1, 2
),
people as (
  select firm_id, period_end,
         count(distinct timekeeper_id)                    as timekeeper_count,
         sum(recorded_hours)                              as recorded_hours,
         sum(billable_hours)                              as billable_hours,
         sum(target_hours)                                as target_hours,
         sum(realised_amount)                             as realised_amount
    from mart.utilisation
   group by 1, 2
),
lock as (
  select firm_id, period_end,
         sum(wip_amount)                                  as wip_amount,
         sum(debtor_amount)                               as debtor_amount,
         sum(overdue_amount)                              as overdue_amount,
         round(sum(debtor_amount * coalesce(debtor_days, 0))
               / nullif(sum(debtor_amount), 0), 1)        as debtor_days,
         round(sum(wip_amount * coalesce(wip_days, 0))
               / nullif(sum(wip_amount), 0), 1)           as wip_days
    from mart.lockup
   group by 1, 2
),
margin as (
  select firm_id, period_end,
         sum(total_cost)                                  as total_cost,
         sum(margin_amount)                               as margin_amount
    from mart.engagement_margin
   group by 1, 2
)
select b.firm_id,
       b.period_end,
       b.engagement_count,
       b.client_count,
       pe.timekeeper_count,
       b.recorded_hours,
       b.billable_hours,
       b.entitled_amount,
       b.billed_amount,
       b.collected_amount,

       -- The three yields, per recorded hour. Per *recorded* hour, not per
       -- billable hour, because an hour written off at entry still consumed
       -- the firm's capacity and the comparison is about capacity.
       -- Every recorded hour at card rate. The denominator is recorded, not
       -- billable, hours throughout: an hour written off at entry still
       -- consumed the practice's capacity, and a comparison about capacity
       -- that excludes it flatters whoever writes off the most.
       round(b.recorded_value / nullif(b.recorded_hours, 0), 2)    as card_rate_per_hour,
       round(b.entitled_amount   / nullif(b.recorded_hours, 0), 2) as entitled_per_hour,
       round(b.billed_amount     / nullif(b.recorded_hours, 0), 2) as billed_per_hour,
       round(b.collected_amount  / nullif(b.recorded_hours, 0), 2) as collected_per_hour,

       -- The three gaps, as rates. Each has one owner.
       -- Write-off at entry: hours recorded that never became billable, at
       -- card rate. Measured against recorded value rather than against
       -- entitlement, because entitlement is already net of it.
       round(1 - b.billable_value / nullif(b.recorded_value, 0), 4)     as writeoff_rate,
       -- Pricing: entitlement against billable hours at full card rate. Below
       -- 1.0 is discounting; above 1.0 is a fixed fee priced over its time.
       round(b.entitled_amount / nullif(b.billable_value, 0), 4)        as pricing_rate,
       round(b.billed_amount / nullif(b.entitled_amount, 0), 4)         as realisation_rate,
       round(b.collected_amount / nullif(b.billed_amount, 0), 4)        as collection_rate,

       -- mart.utilisation already states target_hours on a monthly basis, so
       -- dividing by twelve again inflated this twelvefold. The assertion that
       -- utilisation must be a rate caught it.
       round(pe.billable_hours / nullif(pe.target_hours, 0), 4)         as utilisation,
       round(pe.billable_hours / nullif(pe.recorded_hours, 0), 4)       as entry_realisation,

       l.wip_days,
       l.debtor_days,
       round(coalesce(l.wip_days, 0) + coalesce(l.debtor_days, 0), 1)   as lockup_days,
       round(l.overdue_amount / nullif(l.debtor_amount, 0), 4)          as overdue_share,

       round(m.margin_amount / nullif(b.billed_amount, 0), 4)           as margin_pct,
       round(m.total_cost / nullif(b.recorded_hours, 0), 2)             as cost_per_hour,

       -- Fee per timekeeper. The scale-free way to say how much the practice
       -- earns from a head, comparable across firms of any size.
       round(b.billed_amount / nullif(pe.timekeeper_count, 0), 2)       as fee_per_head,
       round(b.billed_amount / nullif(b.engagement_count, 0), 2)        as fee_per_engagement
  from base b
  left join people pe on pe.firm_id = b.firm_id and pe.period_end = b.period_end
  left join lock l    on l.firm_id = b.firm_id and l.period_end = b.period_end
  left join margin m  on m.firm_id = b.firm_id and m.period_end = b.period_end;

create index on mart.firm_scorecard (firm_id, period_end);
