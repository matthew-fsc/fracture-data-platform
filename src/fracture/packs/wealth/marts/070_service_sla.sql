-- Onboarding, transfer and service SLAs.
--
-- An event still open past its target is a breach now, not a pending item. The
-- alternative reading -- only closed events can breach -- makes the worst
-- backlog invisible, because the tickets nobody has touched never close.
--
-- depends_on: canon.service_event
drop table if exists mart.service_sla;
create table mart.service_sla as
with events as (
  select distinct on (firm_id, service_event_id)
         firm_id, service_event_id, event_type, household_id, account_id,
         actor_producer_id, opened_at, closed_at, sla_target_hours
    from canon.service_event
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, service_event_id, recorded_at desc
),
measured as (
  -- Elapsed time for a still-open event is measured to the pack's system time,
  -- never to now(). Using the wall clock would make the same pack produce a
  -- different SLA figure on every rebuild, which silently voids the
  -- byte-identical reissue guarantee in spec 6.3.
  select e.*,
         coalesce(e.closed_at, %(system_time)s) - e.opened_at as elapsed,
         extract(epoch from (coalesce(e.closed_at, %(system_time)s) - e.opened_at)) / 3600.0
           as elapsed_hours,
         e.closed_at is null as still_open
    from events e
)
select firm_id,
       service_event_id,
       event_type,
       household_id,
       actor_producer_id,
       opened_at,
       closed_at,
       still_open,
       sla_target_hours,
       round(elapsed_hours::numeric, 2) as elapsed_hours,
       case
         when sla_target_hours is null then null
         else elapsed_hours > sla_target_hours
       end as breached,
       (date_trunc('quarter', opened_at) + interval '3 month - 1 day')::date as period_end
  from measured;

create index on mart.service_sla (firm_id, period_end, event_type);

drop table if exists mart.sla_summary;
create table mart.sla_summary as
select firm_id,
       period_end,
       event_type,
       count(*)                                          as event_count,
       count(*) filter (where breached)                  as breach_count,
       count(*) filter (where still_open)                as still_open_count,
       round(
         count(*) filter (where breached)::numeric / nullif(count(*), 0), 6
       )                                                 as breach_rate,
       round(avg(elapsed_hours)::numeric, 2)             as avg_elapsed_hours,
       round(
         percentile_cont(0.9) within group (order by elapsed_hours)::numeric, 2
       )                                                 as p90_elapsed_hours
  from mart.service_sla
 group by firm_id, period_end, event_type;

create index on mart.sla_summary (firm_id, period_end);
