-- Lockup: how long cash sits between doing the work and banking it.
--
-- The metric a professional services firm actually manages. It has two legs
-- and they are owned by different people, which is why a single debtor-days
-- figure never gets fixed:
--
--   WIP days      work done, not yet invoiced          -- the engagement partner
--   debtor days   invoiced, not yet collected          -- finance
--
-- Reported as one number, each team can point at the other. Split, the
-- conversation has an owner.
--
-- depends_on: mart.time_recorded, mart.billed_collected, canon.invoice, canon.client
drop table if exists mart.lockup;
create table mart.lockup as
with clients as (
  select distinct on (firm_id, client_id)
         firm_id, client_id, name, payment_terms_days
    from canon.client
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
   order by firm_id, client_id, recorded_at desc, canon_id desc
),
invoices as (
  select distinct on (firm_id, invoice_id)
         firm_id, invoice_id, client_id, engagement_id, issued_on, due_on,
         total_amount, status
    from canon.invoice
   where recorded_at <= %(system_time)s
     and (superseded_at is null or superseded_at > %(system_time)s)
     and status not in ('void','written_off')
   order by firm_id, invoice_id, recorded_at desc, canon_id desc
),
applications as (
  select firm_id, invoice_id,
         sum(amount)      as applied_amount,
         max(applied_on)  as settled_on
    from (
      select distinct on (firm_id, application_id)
             firm_id, application_id, invoice_id, amount, applied_on
        from canon.receipt_application
       where recorded_at <= %(system_time)s
         and (superseded_at is null or superseded_at > %(system_time)s)
       order by firm_id, application_id, recorded_at desc, canon_id desc
    ) a
   group by 1, 2
),
-- Debtor days per invoice, measured to settlement where settled and to the
-- period end where not. An unsettled invoice is not excluded: excluding it is
-- how a firm reports improving debtor days while its oldest debt ages.
debtor as (
  select i.firm_id,
         i.engagement_id,
         i.client_id,
         date_trunc('month', i.issued_on)::date
           + interval '1 month' - interval '1 day'  as period_end,
         i.invoice_id,
         i.total_amount,
         coalesce(ap.applied_amount, 0)             as applied_amount,
         case
           when coalesce(ap.applied_amount, 0) >= i.total_amount
             then (ap.settled_on - i.issued_on)
           -- The pack's pinned system time, not the wall clock. An unsettled
           -- invoice ages against the date the figures were cut for, so a
           -- reissue of last quarter's pack reports last quarter's debtor
           -- days rather than today's. The assurance gate refuses
           -- current_date here, and found this line.
           else (%(system_time)s::date - i.issued_on)
         end                                        as days_outstanding,
         coalesce(ap.applied_amount, 0) >= i.total_amount as settled,
         c.payment_terms_days
    from invoices i
    join clients c on c.firm_id = i.firm_id and c.client_id = i.client_id
    left join applications ap
      on ap.firm_id = i.firm_id and ap.invoice_id = i.invoice_id
),
-- WIP: billable hours recorded in the period that no invoice has picked up.
-- Valued at entitlement, because WIP valued at cost understates what is
-- actually at risk.
wip as (
  select e.firm_id,
         e.engagement_id,
         e.period_end,
         e.entitled_amount,
         coalesce(b.billed_amount, 0) as billed_amount,
         greatest(e.entitled_amount - coalesce(b.billed_amount, 0), 0) as wip_amount
    from mart.entitled_revenue e
    left join mart.billed_collected b
      on b.firm_id = e.firm_id and b.engagement_id = e.engagement_id
     and b.period_end = e.period_end
)
select w.firm_id,
       w.engagement_id,
       w.period_end,
       w.entitled_amount,
       w.billed_amount,
       w.wip_amount,
       coalesce(sum(d.total_amount) filter (where not d.settled), 0) as debtor_amount,
       -- Weighted, not a mean of invoice ages: a firm with one large old
       -- invoice and twenty small fresh ones has a lockup problem that an
       -- unweighted average hides.
       round(
         sum(d.total_amount * d.days_outstanding)
           / nullif(sum(d.total_amount), 0), 1)                     as debtor_days,
       -- WIP days expressed on the same basis: the value sitting unbilled
       -- divided by the daily entitlement rate for the period.
       round(
         w.wip_amount / nullif(w.entitled_amount / 30.0, 0), 1)     as wip_days,
       count(d.invoice_id)                                          as invoice_count,
       count(d.invoice_id) filter (
         where not d.settled and d.days_outstanding > d.payment_terms_days
       )                                                            as overdue_invoices,
       coalesce(sum(d.total_amount) filter (
         where not d.settled and d.days_outstanding > d.payment_terms_days
       ), 0)                                                        as overdue_amount
  from wip w
  left join debtor d
    on d.firm_id = w.firm_id and d.engagement_id = w.engagement_id
   and d.period_end = w.period_end
 group by 1, 2, 3, 4, 5, 6;

create index on mart.lockup (firm_id, period_end);
