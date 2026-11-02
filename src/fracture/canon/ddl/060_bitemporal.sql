-- Bitemporal read helpers (spec section 6.3).
--
-- Two reads exist in this system and only two:
--   current  -- latest system time, open business validity
--   as-of    -- pinned to a pack_run.system_time and a business date
-- The as-of predicate is generated in Python (fracture.canon.bitemporal) so it
-- can be inlined into mart SQL; the current views below exist so ad-hoc
-- investigation cannot accidentally read superseded rows.

create or replace function canon.list_bitemporal_tables()
returns table (table_name text) as $$
  select c.relname::text
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'canon'
     and c.relkind = 'r'
     and exists (
       select 1 from pg_attribute a
        where a.attrelid = c.oid and a.attname = 'superseded_at' and not a.attisdropped
     )
   order by 1;
$$ language sql stable;

do $$
declare t text;
begin
  for t in select table_name from canon.list_bitemporal_tables() loop
    execute format(
      'create or replace view canon.v_%I_current as
         select * from canon.%I
          where superseded_at is null
            and (valid_to is null or valid_to > current_date)', t, t);
  end loop;
exception when undefined_column then
  -- Tables without valid_to (pure system-time tables) get the simpler view.
  for t in select table_name from canon.list_bitemporal_tables() loop
    execute format(
      'create or replace view canon.v_%I_current as
         select * from canon.%I where superseded_at is null', t, t);
  end loop;
end $$;

-- Supersede a canonical row: system time closes, the row stays. Nothing in
-- canon is ever deleted or updated in place.
create or replace function canon.supersede(
  p_table text, p_canon_id bigint, p_at timestamptz default now()
) returns void as $$
begin
  execute format(
    'update canon.%I set superseded_at = $1 where canon_id = $2 and superseded_at is null',
    p_table
  ) using p_at, p_canon_id;
end;
$$ language plpgsql;
