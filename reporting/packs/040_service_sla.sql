-- section: service_sla
-- title: Onboarding, transfer and service SLAs
-- order: 40
--
-- Events still open past their target count as breaches. Counting only closed
-- events would make the worst backlog invisible, because the tickets nobody has
-- touched never close.
select 'sla_' || s.event_type || '_breach_rate' as metric,
       null                                     as firm_id,
       s.event_type                             as grain_key,
       initcap(s.event_type)                    as grain_label,
       round(sum(s.breach_count)::numeric / nullif(sum(s.event_count), 0), 6) as numeric_value,
       null                                     as text_value,
       'ratio'                                  as unit,
       1                                        as sort_order,
       'mart.service_sla|' || s.event_type      as drill_query
  from mart.sla_summary s
 where s.period_end = %(period_end)s
 group by s.event_type
union all
select 'sla_' || s.event_type || '_p90_hours', null, s.event_type, initcap(s.event_type),
       max(s.p90_elapsed_hours), null, 'hours', 2, 'mart.service_sla|' || s.event_type
  from mart.sla_summary s where s.period_end = %(period_end)s group by s.event_type
union all
select 'sla_still_open', null, 'consolidated', 'Events still open',
       sum(s.still_open_count), null, 'count', 3, 'mart.service_sla'
  from mart.sla_summary s where s.period_end = %(period_end)s
union all
select 'firm_sla_breach_rate', s.firm_id, s.firm_id, s.firm_id,
       round(sum(s.breach_count)::numeric / nullif(sum(s.event_count), 0), 6),
       null, 'ratio', 10, 'mart.service_sla|' || s.firm_id
  from mart.sla_summary s where s.period_end = %(period_end)s group by s.firm_id;
