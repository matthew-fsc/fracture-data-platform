-- Mart-to-canon lineage, so every figure in the pack opens to the records
-- behind it (spec section 6.2).
--
-- Written as inserts rather than derived at query time: a drill-through that
-- re-derives its own joins is a second implementation of the mart, and the two
-- disagree the first time either changes.
--
-- depends_on: mart.household_aum, mart.unbilled, mart.billed_revenue
delete from lineage.mart_edge
 where target_table in ('mart.household_aum', 'mart.billed_revenue', 'mart.unbilled');

insert into lineage.mart_edge (target_table, target_pk, source_table, source_pk, contribution)
select 'mart.household_aum',
       h.firm_id || '|' || h.household_id || '|' || h.as_of_date,
       'canon.balance_snapshot',
       b.canon_id::text,
       'sum'
  from mart.household_aum h
  join canon.balance_snapshot b
    on b.firm_id = h.firm_id
   and b.as_of_date = h.as_of_date
   and b.account_id = any(h.account_ids)
 where b.recorded_at <= %(system_time)s
   and (b.superseded_at is null or b.superseded_at > %(system_time)s);

insert into lineage.mart_edge (target_table, target_pk, source_table, source_pk, contribution)
select 'mart.billed_revenue',
       b.firm_id || '|' || b.invoice_id,
       'canon.invoice',
       i.canon_id::text,
       'source'
  from mart.billed_revenue b
  join canon.invoice i on i.firm_id = b.firm_id and i.invoice_id = b.invoice_id
 where i.recorded_at <= %(system_time)s
   and (i.superseded_at is null or i.superseded_at > %(system_time)s);

insert into lineage.mart_edge (target_table, target_pk, source_table, source_pk, contribution)
select 'mart.unbilled',
       u.firm_id || '|' || u.household_id || '|' || u.period_end,
       'canon.fee_tier',
       t.canon_id::text,
       'derived'
  from mart.unbilled u
  join canon.fee_tier t on t.firm_id = u.firm_id and t.schedule_id = u.schedule_id
 where u.schedule_id is not null
   and t.recorded_at <= %(system_time)s
   and (t.superseded_at is null or t.superseded_at > %(system_time)s);
