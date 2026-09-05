-- What was actually billed, and what was actually collected against it.
--
-- depends_on: canon.invoice, canon.invoice_line, canon.cash_receipt, canon.receipt_application
drop table if exists mart.billed_revenue;
create table mart.billed_revenue as
with invoices as (
  select distinct on (firm_id, invoice_id)
         firm_id, invoice_id, household_id, issued_on, due_on,
         period_start, period_end, total_amount, status
    from canon.invoice
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, invoice_id, recorded_at desc
),
applied as (
  select r.firm_id, r.invoice_id, sum(r.amount_applied) as collected,
         max(r.applied_on) as last_applied_on
    from (
      select distinct on (firm_id, receipt_id, invoice_id)
             firm_id, receipt_id, invoice_id, amount_applied, applied_on
        from canon.receipt_application
       where recorded_at <= %(system_time)s
         and (superseded_at is null or superseded_at > %(system_time)s)
       order by firm_id, receipt_id, invoice_id, recorded_at desc
    ) r
   group by r.firm_id, r.invoice_id
)
select i.firm_id,
       i.invoice_id,
       i.household_id,
       i.issued_on,
       i.due_on,
       coalesce(i.period_start, date_trunc('quarter', i.issued_on)::date) as period_start,
       coalesce(i.period_end, i.issued_on)                                as period_end,
       i.total_amount                                as billed_amount,
       coalesce(a.collected, 0)                      as collected_amount,
       i.total_amount - coalesce(a.collected, 0)     as outstanding_amount,
       a.last_applied_on,
       case
         when coalesce(a.collected, 0) >= i.total_amount then 'paid'
         when coalesce(a.collected, 0) > 0               then 'partial'
         else 'unpaid'
       end                                           as collection_status
  from invoices i
  left join applied a on a.firm_id = i.firm_id and a.invoice_id = i.invoice_id;

create index on mart.billed_revenue (firm_id, period_end);
create index on mart.billed_revenue (firm_id, household_id, period_end);
