-- Fully loaded margin per engagement.
--
-- Two cost buckets, the same treatment the wealth pack uses and for the same
-- reason: a direct cost attributable to one engagement is charged to it, and
-- everything else is pooled and allocated. Excluding the pool is how every
-- engagement looks profitable and the firm still misses its number.
--
-- Allocated on recorded hours rather than on revenue. Revenue-weighted
-- allocation flatters the engagements that are already the most profitable,
-- which is the opposite of what the measure is for.
--
-- depends_on: mart.entitled_revenue, mart.billed_collected, mart.time_recorded, canon.cost_line
drop table if exists mart.engagement_margin;
create table mart.engagement_margin as
with costs as (
  select distinct on (firm_id, cost_line_id)
         firm_id, cost_line_id, period_start, period_end, category, amount, engagement_id
    from canon.cost_line
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, cost_line_id, recorded_at desc, canon_id desc
),
attributable as (
  select firm_id, engagement_id, period_end, sum(amount) as direct_cost
    from costs
   where engagement_id is not null
   group by 1, 2, 3
),
pooled as (
  select firm_id, period_end, sum(amount) as pooled_cost
    from costs
   where engagement_id is null
   group by 1, 2
),
hours_share as (
  select t.firm_id,
         t.engagement_id,
         t.period_end,
         sum(t.recorded_hours)                                       as recorded_hours,
         sum(t.cost_amount)                                          as labour_cost,
         sum(t.recorded_hours) / nullif(sum(sum(t.recorded_hours)) over (
           partition by t.firm_id, t.period_end
         ), 0)                                                       as hours_share
    from mart.time_recorded t
   group by 1, 2, 3
)
select h.firm_id,
       h.engagement_id,
       e.client_id,
       h.period_end,
       e.fee_basis,
       e.stage,
       h.recorded_hours,
       e.billable_hours,
       e.entitled_amount,
       coalesce(b.billed_amount, 0)                                  as billed_amount,
       coalesce(b.collected_amount, 0)                               as collected_amount,
       round(h.labour_cost, 2)                                       as labour_cost,
       round(coalesce(a.direct_cost, 0), 2)                          as direct_cost,
       round(coalesce(p.pooled_cost, 0) * coalesce(h.hours_share, 0), 2) as allocated_overhead,
       round(
         h.labour_cost + coalesce(a.direct_cost, 0)
           + coalesce(p.pooled_cost, 0) * coalesce(h.hours_share, 0), 2
       )                                                             as total_cost,
       -- Margin on what was billed, not on entitlement. An engagement that
       -- failed to bill its entitlement has a realisation problem and a margin
       -- problem, and conflating them loses the first.
       round(
         coalesce(b.billed_amount, 0)
           - (h.labour_cost + coalesce(a.direct_cost, 0)
              + coalesce(p.pooled_cost, 0) * coalesce(h.hours_share, 0)), 2
       )                                                             as margin_amount,
       round(
         (coalesce(b.billed_amount, 0)
           - (h.labour_cost + coalesce(a.direct_cost, 0)
              + coalesce(p.pooled_cost, 0) * coalesce(h.hours_share, 0)))
         / nullif(coalesce(b.billed_amount, 0), 0), 4
       )                                                             as margin_pct,
       -- Effective hourly rate actually billed. The single most comparable
       -- figure across engagements of different size and fee basis.
       round(coalesce(b.billed_amount, 0) / nullif(h.recorded_hours, 0), 2)
                                                                     as effective_rate,
       round(e.entitled_amount / nullif(e.billable_hours, 0), 2)     as entitled_rate
  from hours_share h
  join mart.entitled_revenue e
    on e.firm_id = h.firm_id and e.engagement_id = h.engagement_id
   and e.period_end = h.period_end
  left join mart.billed_collected b
    on b.firm_id = h.firm_id and b.engagement_id = h.engagement_id
   and b.period_end = h.period_end
  left join attributable a
    on a.firm_id = h.firm_id and a.engagement_id = h.engagement_id
   and a.period_end = h.period_end
  left join pooled p on p.firm_id = h.firm_id and p.period_end = h.period_end;

create index on mart.engagement_margin (firm_id, period_end);

-- The allocation has to be complete: every pooled pound lands on exactly one
-- engagement. A share that does not sum to 1 means overhead silently vanished,
-- which is the bug the wealth pack shipped with before the equivalent check
-- was added.
drop table if exists mart.cost_allocation_check;
create table mart.cost_allocation_check as
select m.firm_id,
       m.period_end,
       round(sum(m.allocated_overhead), 2)                   as allocated_total,
       round(max(coalesce(p.pooled_cost, 0)), 2)             as pooled_total,
       round(sum(m.allocated_overhead) - max(coalesce(p.pooled_cost, 0)), 2) as variance
  from mart.engagement_margin m
  left join (
    select firm_id, period_end, sum(amount) as pooled_cost
      from (
        select distinct on (firm_id, cost_line_id)
               firm_id, cost_line_id, period_end, amount, engagement_id
          from canon.cost_line
         where recorded_at <= %(system_time)s
           and (superseded_at is null or superseded_at > %(system_time)s)
         order by firm_id, cost_line_id, recorded_at desc, canon_id desc
      ) c
     where engagement_id is null
     group by 1, 2
  ) p on p.firm_id = m.firm_id and p.period_end = m.period_end
 group by 1, 2;
