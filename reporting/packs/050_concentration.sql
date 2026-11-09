-- section: concentration
-- title: Key-person and book concentration
-- order: 50
--
-- Book share is computed from effective-dated assignments, so a departed
-- advisor still shows the book they held. That is the point: the risk is what
-- walks out the door, and a report that silently reassigns their households to
-- whoever inherited them shows no risk at all.
select 'producer_book_share'                        as metric,
       c.firm_id                                    as firm_id,
       c.producer_id                                as grain_key,
       coalesce(c.producer_name, c.producer_id)     as grain_label,
       c.book_share                                 as numeric_value,
       case when c.has_departed then 'departed' else 'active' end as text_value,
       'ratio'                                      as unit,
       c.book_rank::int                             as sort_order,
       'mart.producer_book|' || c.firm_id || '|' || c.producer_id as drill_query
  from mart.concentration c
 where c.book_rank <= 5
union all
select 'top_producer_share', c.firm_id, c.firm_id, c.firm_id || ' top advisor',
       max(c.book_share), null, 'ratio', 100, 'mart.concentration|' || c.firm_id
  from mart.concentration c group by c.firm_id
union all
select 'departed_producer_book', c.firm_id, c.firm_id, c.firm_id || ' book held by leavers',
       coalesce(sum(c.book_value) filter (where c.has_departed), 0), null, 'USD', 110,
       'mart.producer_book|' || c.firm_id
  from mart.concentration c group by c.firm_id;
