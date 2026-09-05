-- section: assurance
-- title: Reconciliation and data provenance
-- order: 60
--
-- The section that makes the rest of the pack believable: what reconciled
-- against the source systems' own reports, where two sources disagree, and how
-- many figures can be opened to the records behind them.
select 'recon_checks_passed'  as metric,
       null                   as firm_id,
       'consolidated'         as grain_key,
       'Checks passed'        as grain_label,
       count(*) filter (where r.passed) as numeric_value,
       null                   as text_value,
       'count'                as unit,
       1                      as sort_order,
       'recon.result'         as drill_query
  from recon.latest_result r
union all
select 'recon_checks_failed', null, 'consolidated', 'Checks failed',
       count(*) filter (where not r.passed), null, 'count', 2, 'recon.result'
  from recon.latest_result r
union all
select 'recon_worst_variance_pct', null, 'consolidated', 'Largest variance',
       coalesce(max(abs(r.variance_pct)), 0), null, 'ratio', 3, 'recon.result'
  from recon.latest_result r
union all
select 'open_source_variances', null, 'consolidated', 'Unresolved source disagreements',
       count(*), null, 'count', 4, 'recon.source_variance'
  from recon.source_variance v where v.resolved_by is null
union all
select 'unacknowledged_schema_drift', null, 'consolidated', 'Unreviewed schema changes',
       count(*), null, 'count', 5, 'recon.schema_drift'
  from recon.schema_drift d where d.acknowledged_by is null
union all
select 'raw_rows_held', null, 'consolidated', 'Raw records under lineage',
       coalesce(sum(l.row_count), 0), null, 'count', 6, 'raw._load'
  from raw._load l
union all
select 'lineage_edges', null, 'consolidated', 'Row-grain lineage edges',
       (select count(*) from lineage.edge) + (select count(*) from lineage.mart_edge),
       null, 'count', 7, 'lineage.edge'
union all
select 'ai_boundary_violations', null, 'consolidated', 'AI values in numeric columns without sign-off',
       (select count(*) from ai.boundary_violation), null, 'count', 8, 'ai.boundary_violation'
union all
select 'sources_read_only_verified', null, 'consolidated', 'Sources with recorded read-only proof',
       count(distinct l.source_id), null, 'count', 9, 'control.tenant_source'
  from raw._load l;
