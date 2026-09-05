-- section: unbilled_leakage
-- title: Unbilled and leaked revenue
-- order: 30
--
-- Three components, deliberately not summed into one headline: they have three
-- different owners and three different fixes. "Never invoiced" is a billing-run
-- problem, "below schedule" is a repapering problem, "uncollected" is a
-- collections problem.
select 'leakage_' || l.leakage_type as metric,
       null                         as firm_id,
       'consolidated'               as grain_key,
       initcap(replace(l.leakage_type, '_', ' ')) as grain_label,
       sum(l.amount)                as numeric_value,
       null                         as text_value,
       'USD'                        as unit,
       case l.leakage_type
         when 'never_invoiced' then 1
         when 'billed_below_schedule' then 2
         else 3
       end                          as sort_order,
       'mart.leakage|' || l.leakage_type as drill_query
  from mart.leakage l
 where l.period_end = %(period_end)s
 group by l.leakage_type
union all
select 'leakage_total', null, 'consolidated', 'Total leakage',
       sum(l.amount), null, 'USD', 4, 'mart.leakage'
  from mart.leakage l where l.period_end = %(period_end)s
union all
select 'leakage_rate', null, 'consolidated', 'Leakage as share of expected',
       round(sum(l.amount) / nullif(
         (select sum(expected_amount) from mart.expected_revenue
           where period_end = %(period_end)s), 0), 6),
       null, 'ratio', 5, 'mart.expected_revenue'
  from mart.leakage l where l.period_end = %(period_end)s
union all
select 'firm_leakage_' || l.leakage_type, l.firm_id, l.firm_id || '|' || l.leakage_type,
       l.firm_id || ' ' || replace(l.leakage_type, '_', ' '),
       sum(l.amount), null, 'USD', 10, 'mart.leakage|' || l.firm_id
  from mart.leakage l where l.period_end = %(period_end)s
 group by l.firm_id, l.leakage_type
union all
-- The households behind the headline. A leakage number without names attached
-- cannot be actioned by the person who reads the pack.
--
-- Wrapped in a derived table on purpose: an ORDER BY / LIMIT written after the
-- last branch of a UNION applies to the *whole* union, which silently truncates
-- every other figure in the section. The section still renders, and the missing
-- figures look like they were never defined.
select * from (
  select 'unbilled_household' as metric, u.firm_id, u.household_id as grain_key,
         coalesce(h.name, u.household_id) as grain_label,
         u.variance_amount as numeric_value, u.finding as text_value, 'USD' as unit,
         50 + row_number() over (order by u.variance_amount desc)::int as sort_order,
         'mart.unbilled|' || u.firm_id || '|' || u.household_id as drill_query
    from mart.unbilled u
    left join (
      select distinct on (firm_id, household_id) firm_id, household_id, name
        from canon.household where superseded_at is null
       order by firm_id, household_id, recorded_at desc
    ) h on h.firm_id = u.firm_id and h.household_id = u.household_id
   where u.period_end = %(period_end)s
     and u.finding in ('never_invoiced', 'billed_below_schedule')
   order by u.variance_amount desc
   limit 15
) top_findings;
