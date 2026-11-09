-- Mart-to-canon lineage for the services pack.
--
-- Written as inserts rather than derived at query time, for the same reason
-- the wealth pack does it: a drill-through that re-derives its own joins is a
-- second implementation of the mart, and the two disagree the first time
-- either changes.
--
-- depends_on: mart.time_recorded, mart.entitled_revenue, mart.billed_collected
delete from lineage.mart_edge
 where target_table in ('mart.time_recorded', 'mart.entitled_revenue',
                        'mart.billed_collected');

insert into lineage.mart_edge (target_table, target_pk, source_table, source_pk, contribution)
select 'mart.time_recorded',
       t.firm_id || '|' || t.engagement_id || '|' || t.period_end,
       'canon.time_entry',
       e.canon_id::text,
       e.hours
  from mart.time_recorded t
  join canon.time_entry e
    on e.firm_id = t.firm_id
   and e.engagement_id = t.engagement_id
   and date_trunc('month', e.work_date)::date
         + interval '1 month' - interval '1 day' = t.period_end
 where e.recorded_at <= %(system_time)s
   and (e.superseded_at is null or e.superseded_at > %(system_time)s);

insert into lineage.mart_edge (target_table, target_pk, source_table, source_pk, contribution)
select 'mart.entitled_revenue',
       r.firm_id || '|' || r.engagement_id || '|' || r.period_end,
       'canon.time_entry',
       e.canon_id::text,
       e.billable_hours
  from mart.entitled_revenue r
  join canon.time_entry e
    on e.firm_id = r.firm_id
   and e.engagement_id = r.engagement_id
   and date_trunc('month', e.work_date)::date
         + interval '1 month' - interval '1 day' = r.period_end
 where e.recorded_at <= %(system_time)s
   and (e.superseded_at is null or e.superseded_at > %(system_time)s);

insert into lineage.mart_edge (target_table, target_pk, source_table, source_pk, contribution)
select 'mart.billed_collected',
       b.firm_id || '|' || b.engagement_id || '|' || b.period_end,
       'canon.invoice_line',
       l.canon_id::text,
       l.amount
  from mart.billed_collected b
  join canon.invoice i
    on i.firm_id = b.firm_id
   and date_trunc('month', i.issued_on)::date
         + interval '1 month' - interval '1 day' = b.period_end
   and i.recorded_at <= %(system_time)s
   and (i.superseded_at is null or i.superseded_at > %(system_time)s)
  join canon.invoice_line l
    on l.firm_id = i.firm_id
   and l.invoice_id = i.invoice_id
   and coalesce(l.engagement_id, i.engagement_id) = b.engagement_id
 where l.recorded_at <= %(system_time)s
   and (l.superseded_at is null or l.superseded_at > %(system_time)s);
