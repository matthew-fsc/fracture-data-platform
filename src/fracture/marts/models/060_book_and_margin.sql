-- Book, concentration and fully loaded margin.
--
-- `book_assignment` is effective dated, so the producer credited with a
-- household in Q1 2025 is the producer who held it then, not whoever holds it
-- now. That distinction is the entire "what walks out the door" metric: without
-- it, a departed advisor's book silently reassigns itself to whoever inherited
-- it and the concentration risk disappears from the report.
--
-- depends_on: canon.book_assignment, canon.producer, mart.household_aum, mart.billed_revenue
drop table if exists mart.book_assignment_effective;
create table mart.book_assignment_effective as
select distinct on (firm_id, producer_id, household_id, valid_from)
       firm_id, producer_id, household_id, split_pct, valid_from, valid_to
  from canon.book_assignment
 where recorded_at <= %(system_time)s
   and (superseded_at is null or superseded_at > %(system_time)s)
 order by firm_id, producer_id, household_id, valid_from, recorded_at desc;

create index on mart.book_assignment_effective (firm_id, household_id, valid_from);

drop table if exists mart.producer_book;
create table mart.producer_book as
with producers as (
  select distinct on (firm_id, producer_id)
         firm_id, producer_id, display_name, producer_type, hire_date, term_date
    from canon.producer
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, producer_id, recorded_at desc
),
aum as (
  select h.firm_id, h.as_of_date, h.household_id, h.billable_value, h.total_value,
         ba.producer_id, ba.split_pct
    from mart.household_aum h
    join mart.book_assignment_effective ba
      on ba.firm_id = h.firm_id
     and ba.household_id = h.household_id
     and ba.valid_from <= h.as_of_date
     and (ba.valid_to is null or ba.valid_to > h.as_of_date)
),
revenue as (
  select b.firm_id, b.period_end, ba.producer_id,
         sum(b.billed_amount * ba.split_pct / 100)    as billed_amount,
         sum(b.collected_amount * ba.split_pct / 100) as collected_amount
    from mart.billed_revenue b
    join mart.book_assignment_effective ba
      on ba.firm_id = b.firm_id
     and ba.household_id = b.household_id
     and ba.valid_from <= b.period_end
     and (ba.valid_to is null or ba.valid_to > b.period_end)
   group by b.firm_id, b.period_end, ba.producer_id
)
select a.firm_id,
       a.as_of_date,
       a.producer_id,
       p.display_name        as producer_name,
       p.term_date,
       count(distinct a.household_id)                    as household_count,
       sum(a.total_value * a.split_pct / 100)            as book_value,
       sum(a.billable_value * a.split_pct / 100)         as billable_book_value,
       coalesce(r.billed_amount, 0)                      as billed_amount,
       coalesce(r.collected_amount, 0)                   as collected_amount
  from aum a
  left join producers p on p.firm_id = a.firm_id and p.producer_id = a.producer_id
  left join revenue r
    on r.firm_id = a.firm_id and r.period_end = a.as_of_date and r.producer_id = a.producer_id
 group by a.firm_id, a.as_of_date, a.producer_id, p.display_name, p.term_date,
          r.billed_amount, r.collected_amount;

create index on mart.producer_book (firm_id, as_of_date);

drop table if exists mart.concentration;
create table mart.concentration as
with latest as (select max(as_of_date) as as_of from mart.producer_book),
totals as (
  select firm_id, sum(book_value) as firm_book
    from mart.producer_book
   where as_of_date = (select as_of from latest)
   group by firm_id
)
select pb.firm_id,
       pb.as_of_date,
       pb.producer_id,
       pb.producer_name,
       pb.term_date is not null                      as has_departed,
       pb.household_count,
       pb.book_value,
       round(pb.book_value / nullif(t.firm_book, 0), 6) as book_share,
       rank() over (partition by pb.firm_id order by pb.book_value desc) as book_rank
  from mart.producer_book pb
  join totals t on t.firm_id = pb.firm_id
 where pb.as_of_date = (select as_of from latest);

-- Fully loaded margin: revenue less directly attributed cost less an allocated
-- share of everything else. Allocating on revenue is a choice, and it is stated
-- in `allocation_basis` on each cost line rather than assumed here.
drop table if exists mart.margin;
create table mart.margin as
with costs as (
  select distinct on (firm_id, cost_id)
         firm_id, cost_id, period_start, category, person_id, amount, allocation_basis
    from canon.cost_line
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, cost_id, recorded_at desc
),
cost_by_quarter as (
  select firm_id,
         (date_trunc('quarter', period_start) + interval '3 month - 1 day')::date as period_end,
         sum(amount) filter (where allocation_basis = 'direct')  as direct_cost,
         sum(amount) filter (where allocation_basis <> 'direct') as allocated_cost
    from costs
   group by 1, 2
),
direct_time as (
  select f.firm_id,
         (date_trunc('quarter', f.period_start) + interval '3 month - 1 day')::date as period_end,
         f.household_id,
         sum(f.hours * f.hourly_cost) as service_cost
    from (
      select distinct on (firm_id, person_id, period_start, household_id)
             firm_id, person_id, period_start, household_id, hours, hourly_cost
        from canon.fte_allocation
       where recorded_at <= %(system_time)s
         and (superseded_at is null or superseded_at > %(system_time)s)
       order by firm_id, person_id, period_start, household_id, recorded_at desc
    ) f
   where f.household_id is not null
   group by 1, 2, 3
),
revenue as (
  select firm_id, period_end, household_id,
         sum(billed_amount) as billed_amount,
         sum(collected_amount) as collected_amount
    from mart.billed_revenue
   group by 1, 2, 3
),
firm_revenue as (
  select firm_id, period_end, sum(billed_amount) as firm_billed
    from revenue group by 1, 2
)
select r.firm_id,
       r.period_end,
       r.household_id,
       r.billed_amount,
       r.collected_amount,
       coalesce(dt.service_cost, 0)                                  as direct_service_cost,
       round(
         coalesce(cq.allocated_cost, 0)
         * (r.billed_amount / nullif(fr.firm_billed, 0)), 2
       )                                                             as allocated_cost,
       round(
         r.billed_amount
         - coalesce(dt.service_cost, 0)
         - coalesce(cq.allocated_cost, 0) * (r.billed_amount / nullif(fr.firm_billed, 0)),
         2
       )                                                             as loaded_margin,
       round(
         (r.billed_amount
          - coalesce(dt.service_cost, 0)
          - coalesce(cq.allocated_cost, 0) * (r.billed_amount / nullif(fr.firm_billed, 0)))
         / nullif(r.billed_amount, 0), 6
       )                                                             as loaded_margin_pct
  from revenue r
  join firm_revenue fr on fr.firm_id = r.firm_id and fr.period_end = r.period_end
  left join cost_by_quarter cq on cq.firm_id = r.firm_id and cq.period_end = r.period_end
  left join direct_time dt
    on dt.firm_id = r.firm_id and dt.period_end = r.period_end
   and dt.household_id = r.household_id;

create index on mart.margin (firm_id, period_end);
