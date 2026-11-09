-- One model of the platform: the consolidated view across every firm.
--
-- This is the roll-up the one-pager promises, and the reason grain is stated
-- explicitly everywhere below it. Summing a household-grain mart and an
-- account-grain mart into one figure is how a consolidated report ends up
-- double-counting a third of its AUM.
--
-- depends_on: mart.household_aum, mart.billed_revenue, mart.unbilled, mart.leakage, mart.margin
drop table if exists mart.firm_month;
create table mart.firm_month as
with aum as (
  select firm_id, as_of_date, sum(total_value) as total_aum,
         sum(billable_value) as billable_aum,
         count(distinct household_id) as household_count
    from mart.household_aum group by 1, 2
),
billed as (
  select firm_id, period_end, sum(billed_amount) as billed_amount,
         sum(collected_amount) as collected_amount,
         sum(outstanding_amount) as outstanding_amount
    from mart.billed_revenue group by 1, 2
),
expected as (
  select firm_id, period_end, sum(expected_amount) as expected_amount
    from mart.expected_revenue group by 1, 2
),
leak as (
  select firm_id, period_end,
         sum(amount) filter (where leakage_type = 'never_invoiced')        as never_invoiced,
         sum(amount) filter (where leakage_type = 'billed_below_schedule') as below_schedule,
         sum(amount) filter (where leakage_type = 'uncollected')           as uncollected,
         sum(amount)                                                       as total_leakage
    from mart.leakage group by 1, 2
),
marg as (
  select firm_id, period_end, sum(loaded_margin) as loaded_margin
    from mart.margin group by 1, 2
)
select a.firm_id,
       a.as_of_date                                   as period_end,
       a.total_aum,
       a.billable_aum,
       a.household_count,
       e.expected_amount,
       b.billed_amount,
       b.collected_amount,
       b.outstanding_amount,
       l.never_invoiced,
       l.below_schedule,
       l.uncollected,
       l.total_leakage,
       m.loaded_margin,
       round(m.loaded_margin / nullif(b.billed_amount, 0), 6) as loaded_margin_pct,
       round(l.total_leakage / nullif(e.expected_amount, 0), 6) as leakage_rate
  from aum a
  left join billed   b on b.firm_id = a.firm_id and b.period_end = a.as_of_date
  left join expected e on e.firm_id = a.firm_id and e.period_end = a.as_of_date
  left join leak     l on l.firm_id = a.firm_id and l.period_end = a.as_of_date
  left join marg     m on m.firm_id = a.firm_id and m.period_end = a.as_of_date;

create index on mart.firm_month (firm_id, period_end);

drop table if exists mart.consolidated_month;
create table mart.consolidated_month as
select period_end,
       sum(total_aum)          as total_aum,
       sum(billable_aum)       as billable_aum,
       sum(household_count)    as household_count,
       count(distinct firm_id) as firm_count,
       sum(expected_amount)    as expected_amount,
       sum(billed_amount)      as billed_amount,
       sum(collected_amount)   as collected_amount,
       sum(outstanding_amount) as outstanding_amount,
       sum(total_leakage)      as total_leakage,
       sum(loaded_margin)      as loaded_margin,
       round(sum(loaded_margin) / nullif(sum(billed_amount), 0), 6) as loaded_margin_pct
  from mart.firm_month
 group by period_end;

create index on mart.consolidated_month (period_end);
