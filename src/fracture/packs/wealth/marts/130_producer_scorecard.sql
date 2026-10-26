-- Producer economics and capacity.
--
-- Two questions an aggregator asks about advisors and cannot answer from a CRM:
-- what does each one actually earn the platform after loaded cost, and how much
-- book walks if they leave. Both need effective-dated assignments, which is why
-- this reads mart.book_assignment_effective rather than the current CRM view.
--
-- depends_on: mart.producer_book, mart.concentration, mart.household_economics, mart.service_sla
drop table if exists mart.producer_scorecard;
create table mart.producer_scorecard as
with latest as (select max(as_of_date) as as_of_date from mart.producer_book),
economics as (
  select firm_id, producer_id,
         count(*)                as households,
         sum(aum)                as book_value,
         sum(billed_amount)      as billed_amount,
         sum(collected_amount)   as collected_amount,
         sum(cost_to_serve)      as cost_to_serve,
         sum(loaded_margin)      as loaded_margin,
         count(*) filter (where loss_making) as loss_making_households
    from mart.household_economics
   where producer_id is not null
   group by 1, 2
),
service as (
  select s.firm_id, s.actor_producer_id as producer_id,
         count(*)                        as service_events,
         count(*) filter (where s.breached) as service_breaches
    from mart.service_sla s
   where s.actor_producer_id is not null
   group by 1, 2
)
select pb.firm_id,
       pb.as_of_date,
       pb.producer_id,
       pb.producer_name,
       pb.term_date,
       pb.term_date is not null                                       as has_departed,
       coalesce(e.households, pb.household_count)                     as households,
       coalesce(e.book_value, pb.book_value)                          as book_value,
       c.book_share,
       c.book_rank,
       coalesce(e.billed_amount, 0)                                   as billed_amount,
       coalesce(e.collected_amount, 0)                                as collected_amount,
       coalesce(e.cost_to_serve, 0)                                   as cost_to_serve,
       coalesce(e.loaded_margin, 0)                                   as loaded_margin,
       round(e.loaded_margin / nullif(e.billed_amount, 0), 6)         as loaded_margin_pct,
       coalesce(e.loss_making_households, 0)                          as loss_making_households,
       round(e.billed_amount / nullif(e.households, 0), 2)            as revenue_per_household,
       round(e.book_value / nullif(e.households, 0), 2)               as aum_per_household,
       round(e.billed_amount * 4 / nullif(e.book_value, 0) * 10000, 2) as yield_bps,
       coalesce(sv.service_events, 0)                                 as service_events,
       coalesce(sv.service_breaches, 0)                               as service_breaches,
       round(sv.service_breaches::numeric / nullif(sv.service_events, 0), 6)
                                                                      as service_breach_rate
  from mart.producer_book pb
  join latest l on l.as_of_date = pb.as_of_date
  left join economics e on e.firm_id = pb.firm_id and e.producer_id = pb.producer_id
  left join mart.concentration c on c.firm_id = pb.firm_id and c.producer_id = pb.producer_id
  left join service sv on sv.firm_id = pb.firm_id and sv.producer_id = pb.producer_id;

create index on mart.producer_scorecard (firm_id, book_share desc);
