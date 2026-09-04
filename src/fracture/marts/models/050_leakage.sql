-- Leakage: revenue the firm earned and did not keep.
--
-- Three components, kept separate because they have three different owners:
--   never_invoiced        -- the billing run missed the household
--   billed_below_schedule -- the invoice was raised at the wrong rate
--   uncollected           -- the invoice was raised correctly and never paid
--
-- Rolling them into one number makes the finding unactionable, which is how a
-- leakage report ends up in a drawer.
--
-- depends_on: mart.unbilled, mart.billed_revenue
drop table if exists mart.leakage;
create table mart.leakage as
select firm_id, period_end, 'never_invoiced' as leakage_type,
       count(*)                        as item_count,
       sum(expected_amount)            as amount,
       'expected revenue with no invoice raised' as detail
  from mart.unbilled
 where finding = 'never_invoiced'
 group by firm_id, period_end
union all
select firm_id, period_end, 'billed_below_schedule',
       count(*), sum(expected_amount - billed_amount),
       'invoiced below the assigned fee schedule'
  from mart.unbilled
 where finding = 'billed_below_schedule'
 group by firm_id, period_end
union all
select firm_id, period_end, 'uncollected',
       count(*), sum(outstanding_amount),
       'invoiced correctly and not collected in full'
  from mart.billed_revenue
 where outstanding_amount > 0.01
 group by firm_id, period_end;

create index on mart.leakage (firm_id, period_end, leakage_type);

-- Receivables ageing, as of the latest period in the data.
drop table if exists mart.receivables_ageing;
create table mart.receivables_ageing as
with asof as (select max(period_end) as as_of from mart.billed_revenue)
select b.firm_id,
       b.invoice_id,
       b.household_id,
       b.issued_on,
       b.due_on,
       b.billed_amount,
       b.collected_amount,
       b.outstanding_amount,
       (select as_of from asof) - coalesce(b.due_on, b.issued_on) as days_overdue,
       case
         when (select as_of from asof) - coalesce(b.due_on, b.issued_on) <= 0   then 'current'
         when (select as_of from asof) - coalesce(b.due_on, b.issued_on) <= 30  then '1_30'
         when (select as_of from asof) - coalesce(b.due_on, b.issued_on) <= 60  then '31_60'
         when (select as_of from asof) - coalesce(b.due_on, b.issued_on) <= 90  then '61_90'
         else 'over_90'
       end as ageing_bucket
  from mart.billed_revenue b
 where b.outstanding_amount > 0.01;

create index on mart.receivables_ageing (firm_id, ageing_bucket);
