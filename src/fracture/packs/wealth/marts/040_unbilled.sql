-- Unbilled revenue: expected minus billed, per household per billing period.
--
-- A full outer join, not a left join. Billed-with-no-expectation is as much a
-- finding as expected-with-no-invoice -- it means the firm charged something
-- the schedule does not support, which is the harder conversation of the two.
--
-- depends_on: mart.expected_revenue, mart.billed_revenue
drop table if exists mart.unbilled;
create table mart.unbilled as
with billed as (
  select firm_id, household_id, period_end,
         sum(billed_amount) as billed_amount,
         min(period_start)  as period_start,
         array_agg(invoice_id order by invoice_id) as invoice_ids
    from mart.billed_revenue
   group by firm_id, household_id, period_end
)
select coalesce(e.firm_id, b.firm_id)             as firm_id,
       coalesce(e.household_id, b.household_id)   as household_id,
       coalesce(e.period_end, b.period_end)       as period_end,
       coalesce(e.period_start, b.period_start)   as period_start,
       e.schedule_id,
       e.schedule_name,
       e.basis_amount,
       coalesce(e.expected_amount, 0)             as expected_amount,
       coalesce(b.billed_amount, 0)               as billed_amount,
       coalesce(e.expected_amount, 0) - coalesce(b.billed_amount, 0) as variance_amount,
       case
         when e.expected_amount is null or e.expected_amount = 0 then null
         else round(
           (coalesce(e.expected_amount, 0) - coalesce(b.billed_amount, 0))
           / e.expected_amount, 6)
       end                                        as variance_pct,
       b.invoice_ids,
       case
         when b.billed_amount is null then 'never_invoiced'
         when e.expected_amount is null then 'billed_without_schedule'
         when b.billed_amount < e.expected_amount - 0.01 then 'billed_below_schedule'
         when b.billed_amount > e.expected_amount + 0.01 then 'billed_above_schedule'
         else 'as_expected'
       end                                        as finding
  from mart.expected_revenue e
  full outer join billed b
    on b.firm_id = e.firm_id
   and b.household_id = e.household_id
   and b.period_end = e.period_end;

create index on mart.unbilled (firm_id, period_end, finding);
