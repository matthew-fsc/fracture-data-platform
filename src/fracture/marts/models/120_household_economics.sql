-- Per-household economics, for distributions rather than averages.
--
-- A firm's mean margin can look healthy while a third of its households lose
-- money. Averages hide that; quartiles do not. This table is the grain the
-- distribution charts read, and it is also what a "which clients are
-- unprofitable" question resolves against.
--
-- depends_on: mart.margin, mart.household_aum, mart.unbilled, mart.book_assignment_effective
drop table if exists mart.household_economics;
create table mart.household_economics as
with latest as (select max(period_end) as period_end from mart.margin),
aum as (
  select h.firm_id, h.household_id, h.as_of_date, h.total_value, h.billable_value,
         h.account_count
    from mart.household_aum h
    join latest l on l.period_end = h.as_of_date
),
book as (
  select distinct on (firm_id, household_id)
         firm_id, household_id, producer_id
    from mart.book_assignment_effective
   order by firm_id, household_id, valid_from desc
),
finding as (
  select u.firm_id, u.household_id, u.finding, u.expected_amount, u.variance_amount,
         u.schedule_name
    from mart.unbilled u
    join latest l on l.period_end = u.period_end
)
select m.firm_id,
       m.period_end,
       m.household_id,
       hh.name                                   as household_name,
       hh.segment,
       b.producer_id,
       a.total_value                             as aum,
       a.billable_value,
       a.account_count,
       m.billed_amount,
       m.collected_amount,
       f.expected_amount,
       f.finding,
       f.schedule_name,
       m.direct_service_cost,
       m.producer_cost,
       m.allocated_cost,
       m.direct_service_cost + m.producer_cost + m.allocated_cost as cost_to_serve,
       m.loaded_margin,
       m.loaded_margin_pct,
       round(m.billed_amount * 4 / nullif(a.total_value, 0) * 10000, 2) as actual_yield_bps,
       round(f.expected_amount * 4 / nullif(a.total_value, 0) * 10000, 2) as schedule_yield_bps,
       m.loaded_margin < 0                       as loss_making
  from mart.margin m
  join latest l on l.period_end = m.period_end
  left join aum a on a.firm_id = m.firm_id and a.household_id = m.household_id
  left join book b on b.firm_id = m.firm_id and b.household_id = m.household_id
  left join finding f on f.firm_id = m.firm_id and f.household_id = m.household_id
  left join (
    select distinct on (firm_id, household_id) firm_id, household_id, name, segment
      from canon.household
     where superseded_at is null
     order by firm_id, household_id, recorded_at desc
  ) hh on hh.firm_id = m.firm_id and hh.household_id = m.household_id;

create index on mart.household_economics (firm_id, loaded_margin);

-- Distribution summary, so the dashboard does not recompute quartiles per view.
drop table if exists mart.household_distribution;
create table mart.household_distribution as
select firm_id,
       period_end,
       count(*)                                                          as households,
       count(*) filter (where loss_making)                               as loss_making_households,
       round(count(*) filter (where loss_making)::numeric / nullif(count(*), 0), 6)
                                                                         as loss_making_share,
       round(percentile_cont(0.25) within group (order by loaded_margin)::numeric, 2) as margin_p25,
       round(percentile_cont(0.50) within group (order by loaded_margin)::numeric, 2) as margin_p50,
       round(percentile_cont(0.75) within group (order by loaded_margin)::numeric, 2) as margin_p75,
       round(percentile_cont(0.25) within group (order by aum)::numeric, 2)           as aum_p25,
       round(percentile_cont(0.50) within group (order by aum)::numeric, 2)           as aum_p50,
       round(percentile_cont(0.75) within group (order by aum)::numeric, 2)           as aum_p75,
       round(percentile_cont(0.50) within group (order by actual_yield_bps)::numeric, 2)
                                                                         as yield_bps_p50,
       round(percentile_cont(0.50) within group (order by cost_to_serve)::numeric, 2)
                                                                         as cost_to_serve_p50
  from mart.household_economics
 group by firm_id, period_end;
