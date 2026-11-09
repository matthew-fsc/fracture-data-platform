-- section: revenue_margin
-- title: Revenue and margin, fully loaded
-- order: 20
--
-- Loaded margin is revenue less directly attributed service time less an
-- allocated share of the rest. The allocation basis travels with each cost line
-- rather than being assumed here, so a firm that allocates on headcount and one
-- that allocates on revenue both consolidate correctly.
select 'consolidated_billed'    as metric, null as firm_id, 'consolidated' as grain_key,
       'All firms' as grain_label, c.billed_amount as numeric_value, null as text_value,
       'USD' as unit, 1 as sort_order, 'mart.billed_revenue' as drill_query
  from mart.consolidated_month c where c.period_end = %(period_end)s
union all
select 'consolidated_collected', null, 'consolidated', 'All firms',
       c.collected_amount, null, 'USD', 2, 'mart.billed_revenue'
  from mart.consolidated_month c where c.period_end = %(period_end)s
union all
select 'consolidated_margin', null, 'consolidated', 'All firms',
       c.loaded_margin, null, 'USD', 3, 'mart.margin'
  from mart.consolidated_month c where c.period_end = %(period_end)s
union all
select 'consolidated_margin_pct', null, 'consolidated', 'All firms',
       c.loaded_margin_pct, null, 'ratio', 4, 'mart.margin'
  from mart.consolidated_month c where c.period_end = %(period_end)s
union all
select 'firm_billed', f.firm_id, f.firm_id, f.firm_id, f.billed_amount, null, 'USD', 10,
       'mart.billed_revenue|' || f.firm_id
  from mart.firm_month f where f.period_end = %(period_end)s
union all
select 'firm_margin', f.firm_id, f.firm_id, f.firm_id, f.loaded_margin, null, 'USD', 11,
       'mart.margin|' || f.firm_id
  from mart.firm_month f where f.period_end = %(period_end)s
union all
select 'firm_margin_pct', f.firm_id, f.firm_id, f.firm_id, f.loaded_margin_pct, null, 'ratio', 12,
       'mart.margin|' || f.firm_id
  from mart.firm_month f where f.period_end = %(period_end)s
union all
-- Top clients by loaded margin: where the firm actually makes its money, which
-- is regularly not where the AUM is.
--
-- Wrapped in a derived table: an ORDER BY / LIMIT after the last branch of a
-- UNION applies to the whole union and would silently drop the section's other
-- figures.
select * from (
  select 'top_client_margin' as metric, m.firm_id, m.household_id as grain_key,
         coalesce(h.name, m.household_id) as grain_label,
         m.loaded_margin as numeric_value, null::text as text_value, 'USD' as unit,
         20 + row_number() over (order by m.loaded_margin desc)::int as sort_order,
         'mart.margin|' || m.firm_id || '|' || m.household_id as drill_query
    from mart.margin m
    left join (
      select distinct on (firm_id, household_id) firm_id, household_id, name
        from canon.household where superseded_at is null
       order by firm_id, household_id, recorded_at desc
    ) h on h.firm_id = m.firm_id and h.household_id = m.household_id
   where m.period_end = %(period_end)s
   order by m.loaded_margin desc
   limit 10
) top_clients;
