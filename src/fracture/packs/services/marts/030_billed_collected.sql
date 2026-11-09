-- Invoiced and collected, by engagement and period.
--
-- The cash side is not special. What makes it useful is sitting next to
-- entitlement, so the two gaps can be separated: billed against entitled is
-- realisation, and collected against billed is lockup. They have different
-- owners -- the engagement partner and the finance team -- and reporting them
-- as one number sends the wrong person to fix it.
--
-- depends_on: canon.invoice, canon.invoice_line, canon.cash_receipt, canon.receipt_application
drop table if exists mart.billed_collected;
create table mart.billed_collected as
with invoices as (
  select distinct on (firm_id, invoice_id)
         firm_id, invoice_id, client_id, engagement_id, issued_on, due_on,
         total_amount, status
    from canon.invoice
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
     and status <> 'void'
   order by firm_id, invoice_id, recorded_at desc, canon_id desc
),
lines as (
  select distinct on (firm_id, invoice_line_id)
         firm_id, invoice_line_id, invoice_id, engagement_id, line_type, hours, amount
    from canon.invoice_line
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, invoice_line_id, recorded_at desc, canon_id desc
),
applications as (
  select distinct on (firm_id, application_id)
         firm_id, application_id, receipt_id, invoice_id, amount, applied_on
    from canon.receipt_application
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, application_id, recorded_at desc, canon_id desc
),
-- A line carries its own engagement where it has one; otherwise it inherits
-- the invoice's. An invoice spanning two engagements with unattributed lines
-- would otherwise land entirely on the first.
line_engagement as (
  select l.firm_id,
         coalesce(l.engagement_id, i.engagement_id) as engagement_id,
         i.invoice_id,
         i.client_id,
         date_trunc('month', i.issued_on)::date
           + interval '1 month' - interval '1 day'   as period_end,
         l.line_type,
         l.hours,
         l.amount
    from lines l
    join invoices i on i.firm_id = l.firm_id and i.invoice_id = l.invoice_id
),
billed as (
  select firm_id, engagement_id, client_id, period_end,
         sum(amount)                                            as billed_amount,
         sum(amount) filter (where line_type = 'time')           as billed_time_amount,
         sum(amount) filter (where line_type = 'fixed_fee')      as billed_fixed_amount,
         sum(amount) filter (where line_type = 'disbursement')   as billed_disbursements,
         -- Adjustments are credits and write-ons applied at billing. Carried
         -- separately because an adjustment is a decision somebody made, not a
         -- consequence of the hours.
         sum(amount) filter (where line_type = 'adjustment')     as billed_adjustments,
         sum(hours)                                              as billed_hours,
         count(distinct invoice_id)                              as invoice_count
    from line_engagement
   group by 1, 2, 3, 4
),
-- Cash is attributed to the period the invoice was issued in, not the period
-- it arrived in. That is what makes collected comparable to billed on one row;
-- the delay itself is the lockup measure below.
collected as (
  select le.firm_id,
         le.engagement_id,
         le.period_end,
         sum(a.amount)                                           as collected_amount,
         min(a.applied_on)                                       as first_receipt_on,
         max(a.applied_on)                                       as last_receipt_on
    from applications a
    join (select distinct firm_id, invoice_id, engagement_id, period_end
            from line_engagement) le
      on le.firm_id = a.firm_id and le.invoice_id = a.invoice_id
   group by 1, 2, 3
)
select b.firm_id,
       b.engagement_id,
       b.client_id,
       b.period_end,
       b.billed_amount,
       coalesce(b.billed_time_amount, 0)      as billed_time_amount,
       coalesce(b.billed_fixed_amount, 0)     as billed_fixed_amount,
       coalesce(b.billed_disbursements, 0)    as billed_disbursements,
       coalesce(b.billed_adjustments, 0)      as billed_adjustments,
       coalesce(b.billed_hours, 0)            as billed_hours,
       b.invoice_count,
       coalesce(c.collected_amount, 0)        as collected_amount,
       b.billed_amount - coalesce(c.collected_amount, 0) as outstanding_amount,
       c.first_receipt_on,
       c.last_receipt_on
  from billed b
  left join collected c
    on c.firm_id = b.firm_id and c.engagement_id = b.engagement_id
   and c.period_end = b.period_end;

create index on mart.billed_collected (firm_id, period_end);
create index on mart.billed_collected (firm_id, engagement_id, period_end);
