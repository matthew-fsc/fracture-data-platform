-- Utilisation and yield per timekeeper, and per grade.
--
-- Utilisation against the timekeeper's own target, not against a calendar
-- year: a part-time or newly joined person is not underperforming, and a
-- denominator that says they are makes the measure unusable for the
-- conversation it exists to support.
--
-- Three figures, because utilisation alone is gameable. A timekeeper can hit
-- target by recording hours nobody can bill, so recorded, billable and
-- realised are all here, and the realised rate per hour is what the firm
-- actually earns from an hour of their time.
--
-- depends_on: mart.time_recorded, mart.entitled_revenue, canon.timekeeper, canon.producer
drop table if exists mart.utilisation;
create table mart.utilisation as
with keepers as (
  select distinct on (firm_id, timekeeper_id)
         firm_id, timekeeper_id, producer_id, grade, cost_rate, target_hours,
         started_on, ended_on
    from canon.timekeeper
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, timekeeper_id, recorded_at desc, canon_id desc
),
entries as (
  select distinct on (firm_id, time_entry_id)
         firm_id, time_entry_id, engagement_id, timekeeper_id, work_date,
         hours, billable_hours, writeoff_reason
    from canon.time_entry
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, time_entry_id, recorded_at desc, canon_id desc
),
-- Realised value per billable hour, at engagement and period grain, taken
-- from what was actually billed rather than from the card. Applied back to
-- each timekeeper's hours so a person working mostly on heavily discounted
-- engagements shows the yield they actually generate.
realised_rate as (
  select e.firm_id,
         e.engagement_id,
         e.period_end,
         case when e.billable_hours > 0
              then coalesce(b.billed_amount, 0) / e.billable_hours
         end as realised_per_hour
    from mart.entitled_revenue e
    left join mart.billed_collected b
      on b.firm_id = e.firm_id and b.engagement_id = e.engagement_id
     and b.period_end = e.period_end
),
by_period as (
  select en.firm_id,
         en.timekeeper_id,
         date_trunc('month', en.work_date)::date
           + interval '1 month' - interval '1 day'      as period_end,
         sum(en.hours)                                   as recorded_hours,
         sum(en.billable_hours)                          as billable_hours,
         sum(en.hours - en.billable_hours)               as writeoff_hours,
         sum(en.billable_hours * coalesce(r.realised_per_hour, 0)) as realised_amount,
         count(distinct en.engagement_id)                as engagement_count
    from entries en
    left join realised_rate r
      on r.firm_id = en.firm_id
     and r.engagement_id = en.engagement_id
     and r.period_end = date_trunc('month', en.work_date)::date
                          + interval '1 month' - interval '1 day'
   group by 1, 2, 3
)
select p.firm_id,
       p.timekeeper_id,
       k.producer_id,
       k.grade,
       p.period_end,
       p.recorded_hours,
       p.billable_hours,
       p.writeoff_hours,
       -- The monthly share of an annual target. Expressed this way so a
       -- single month is comparable to the year, and so a firm cannot report
       -- a good month by shortening the denominator.
       round(k.target_hours / 12.0, 2)                            as target_hours,
       round(p.recorded_hours / nullif(k.target_hours / 12.0, 0), 4) as recorded_utilisation,
       round(p.billable_hours / nullif(k.target_hours / 12.0, 0), 4) as billable_utilisation,
       -- The share of recorded hours that survived to billable. A timekeeper
       -- at target on recorded hours and well below on this is the specific
       -- case utilisation alone cannot see.
       round(p.billable_hours / nullif(p.recorded_hours, 0), 4)   as entry_realisation,
       round(p.realised_amount, 2)                                as realised_amount,
       round(p.realised_amount / nullif(p.billable_hours, 0), 2)  as realised_per_hour,
       round(k.cost_rate, 2)                                      as cost_rate,
       -- Realised yield over fully loaded cost. Below 1.0 means an hour of
       -- this person's time loses money at the rates it was sold at.
       round(
         (p.realised_amount / nullif(p.billable_hours, 0)) / nullif(k.cost_rate, 0), 3
       )                                                          as yield_multiple,
       p.engagement_count
  from by_period p
  join keepers k on k.firm_id = p.firm_id and k.timekeeper_id = p.timekeeper_id;

create index on mart.utilisation (firm_id, period_end);
create index on mart.utilisation (firm_id, timekeeper_id, period_end);
