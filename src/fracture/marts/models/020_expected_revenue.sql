-- Expected revenue: the fee schedule applied to the billing basis.
--
-- This is the model the whole unbilled/leakage claim rests on (spec section 5).
-- It is a deliberate second implementation of `fracture.synth.fees`, in SQL,
-- against the canonical schedule -- so if the two ever disagree, a test fails
-- rather than a client discovering it.
--
-- Marginal tiering: each band charges its own rate on the slice of the balance
-- inside it. Blended: one rate, chosen by the band the total lands in. Getting
-- these two the wrong way round moves the answer by 20 to 40 percent at a breakpoint, which
-- is larger than the leakage being looked for.
--
-- depends_on: mart.household_aum, canon.fee_schedule, canon.fee_tier, canon.schedule_assignment
drop table if exists mart.expected_revenue;
create table mart.expected_revenue as
with schedules as (
  select distinct on (firm_id, schedule_id)
         firm_id, schedule_id, name, basis, frequency, calc_method,
         billing_timing, valuation_rule, source_kind, valid_from, valid_to
    from canon.fee_schedule
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, schedule_id, recorded_at desc
),
tiers as (
  select distinct on (firm_id, schedule_id, tier_seq)
         firm_id, schedule_id, tier_seq, lower_bound, upper_bound,
         annual_rate_bps, flat_amount
    from canon.fee_tier
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, schedule_id, tier_seq, recorded_at desc
),
assignments as (
  select distinct on (firm_id, scope_type, scope_id, schedule_id, valid_from)
         firm_id, scope_type, scope_id, schedule_id, valid_from, valid_to
    from canon.schedule_assignment
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, scope_type, scope_id, schedule_id, valid_from, recorded_at desc
),
-- Billing periods: quarter ends present in the AUM mart. Advance billing would
-- value at period start; `valuation_rule` carries that and is honoured below.
periods as (
  select distinct firm_id, as_of_date as period_end,
         (date_trunc('quarter', as_of_date))::date as period_start
    from mart.household_aum
   where extract(month from as_of_date) in (3, 6, 9, 12)
),
basis as (
  select p.firm_id, p.period_start, p.period_end,
         h.household_id,
         coalesce(h.billable_value, 0) as billable_value,
         a.schedule_id
    from periods p
    join mart.household_aum h
      on h.firm_id = p.firm_id and h.as_of_date = p.period_end
    join assignments a
      on a.firm_id = h.firm_id
     and a.scope_type = 'household'
     and a.scope_id = h.household_id
     and a.valid_from <= p.period_end
     and (a.valid_to is null or a.valid_to > p.period_end)
),
tiered as (
  select b.firm_id, b.household_id, b.period_start, b.period_end, b.schedule_id,
         b.billable_value,
         sum(
           greatest(
             least(b.billable_value, coalesce(t.upper_bound, b.billable_value)) - t.lower_bound,
             0
           ) * coalesce(t.annual_rate_bps, 0) / 10000
         ) as annual_rate_component,
         sum(coalesce(t.flat_amount, 0)) filter (where b.billable_value > t.lower_bound)
           as annual_flat_component
    from basis b
    join schedules s on s.firm_id = b.firm_id and s.schedule_id = b.schedule_id
    join tiers t on t.firm_id = b.firm_id and t.schedule_id = b.schedule_id
   where s.calc_method = 'tiered'
   group by b.firm_id, b.household_id, b.period_start, b.period_end, b.schedule_id, b.billable_value
),
blended as (
  select b.firm_id, b.household_id, b.period_start, b.period_end, b.schedule_id,
         b.billable_value,
         coalesce(b.billable_value * t.annual_rate_bps / 10000, 0) as annual_rate_component,
         coalesce(t.flat_amount, 0) as annual_flat_component
    from basis b
    join schedules s on s.firm_id = b.firm_id and s.schedule_id = b.schedule_id
    join tiers t
      on t.firm_id = b.firm_id and t.schedule_id = b.schedule_id
     and b.billable_value >= t.lower_bound
     and (t.upper_bound is null or b.billable_value < t.upper_bound)
   where s.calc_method = 'blended'
),
flat as (
  select b.firm_id, b.household_id, b.period_start, b.period_end, b.schedule_id,
         b.billable_value,
         0::numeric as annual_rate_component,
         sum(coalesce(t.flat_amount, 0)) as annual_flat_component
    from basis b
    join schedules s on s.firm_id = b.firm_id and s.schedule_id = b.schedule_id
    join tiers t on t.firm_id = b.firm_id and t.schedule_id = b.schedule_id
   where s.calc_method = 'flat'
   group by b.firm_id, b.household_id, b.period_start, b.period_end, b.schedule_id, b.billable_value
),
combined as (
  select * from tiered union all select * from blended union all select * from flat
)
select c.firm_id,
       c.household_id,
       c.schedule_id,
       s.name           as schedule_name,
       s.calc_method,
       s.frequency,
       s.source_kind,
       c.period_start,
       c.period_end,
       c.billable_value as basis_amount,
       round(
         (c.annual_rate_component + coalesce(c.annual_flat_component, 0))
         / case s.frequency
             when 'monthly'   then 12
             when 'quarterly' then 4
             when 'annual'    then 1
           end,
         2
       ) as expected_amount
  from combined c
  join schedules s on s.firm_id = c.firm_id and s.schedule_id = c.schedule_id;

create index on mart.expected_revenue (firm_id, period_end);
create index on mart.expected_revenue (firm_id, household_id, period_end);
