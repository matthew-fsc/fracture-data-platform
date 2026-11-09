-- section: platform_model
-- title: One model of the platform
-- order: 10
--
-- The consolidated view and each firm beneath it, at the pack period end.
-- Every figure below carries a drill_query that resolves through
-- lineage.mart_edge to canonical rows and on to the raw payloads and their S3
-- artifacts.
select 'consolidated_aum'                          as metric,
       null                                        as firm_id,
       'consolidated'                              as grain_key,
       'All firms'                                 as grain_label,
       c.total_aum                                 as numeric_value,
       null                                        as text_value,
       'USD'                                       as unit,
       1                                           as sort_order,
       'mart.consolidated_month'                   as drill_query
  from mart.consolidated_month c
 where c.period_end = %(period_end)s
union all
select 'consolidated_households', null, 'consolidated', 'All firms',
       c.household_count, null, 'count', 2, 'mart.household_aum'
  from mart.consolidated_month c where c.period_end = %(period_end)s
union all
select 'consolidated_firms', null, 'consolidated', 'All firms',
       c.firm_count, null, 'count', 3, 'control.tenant_firm'
  from mart.consolidated_month c where c.period_end = %(period_end)s
union all
select 'firm_aum', f.firm_id, f.firm_id, f.firm_id, f.total_aum, null, 'USD', 10,
       'mart.household_aum|' || f.firm_id
  from mart.firm_month f where f.period_end = %(period_end)s
union all
select 'firm_billable_aum', f.firm_id, f.firm_id, f.firm_id, f.billable_aum, null, 'USD', 11,
       'mart.household_aum|' || f.firm_id
  from mart.firm_month f where f.period_end = %(period_end)s
union all
select 'firm_households', f.firm_id, f.firm_id, f.firm_id, f.household_count, null, 'count', 12,
       'mart.household_aum|' || f.firm_id
  from mart.firm_month f where f.period_end = %(period_end)s;
