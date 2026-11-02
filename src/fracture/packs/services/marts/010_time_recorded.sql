-- Recorded and billable time by engagement and period.
--
-- The base of the whole pack. Everything above this is a rate applied to these
-- hours, so the as-of discipline matters here more than anywhere: a restated
-- timesheet that leaks into a closed period moves every figure above it.
--
-- depends_on: canon.time_entry, canon.engagement, canon.timekeeper
drop table if exists mart.time_recorded;
create table mart.time_recorded as
with entries as (
  select distinct on (firm_id, time_entry_id)
         firm_id, time_entry_id, engagement_id, matter_id, timekeeper_id,
         work_date, hours, billable_hours, writeoff_reason, billed_on
    from canon.time_entry
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, time_entry_id, recorded_at desc, canon_id desc
),
keepers as (
  select distinct on (firm_id, timekeeper_id)
         firm_id, timekeeper_id, grade, cost_rate, target_hours
    from canon.timekeeper
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, timekeeper_id, recorded_at desc, canon_id desc
)
select e.firm_id,
       e.engagement_id,
       date_trunc('month', e.work_date)::date
         + interval '1 month' - interval '1 day'                   as period_end,
       k.grade,
       sum(e.hours)                                                as recorded_hours,
       sum(e.billable_hours)                                       as billable_hours,
       sum(e.hours - e.billable_hours)                             as writeoff_hours,
       -- Fully loaded cost of the time, billable or not. A written-off hour
       -- still cost the firm money, which is the point of carrying it here
       -- rather than dropping non-billable rows.
       sum(e.hours * k.cost_rate)                                  as cost_amount,
       sum(e.billable_hours * k.cost_rate)                         as billable_cost_amount,
       count(*)                                                    as entry_count,
       count(distinct e.timekeeper_id)                             as timekeeper_count
  from entries e
  join keepers k on k.firm_id = e.firm_id and k.timekeeper_id = e.timekeeper_id
 group by 1, 2, 3, 4;

create index on mart.time_recorded (firm_id, engagement_id, period_end);
create index on mart.time_recorded (firm_id, period_end, grade);
