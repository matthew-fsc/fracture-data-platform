-- Raw layer conventions and the lineage edge table (spec sections 6.1, 6.2).
--
-- raw.<source>__<stream> tables are created on demand by the loader from
-- `raw_table_ddl()` below, so a new adapter stream cannot land in a table with
-- a different shape than every other one.

create table if not exists raw._load (
  load_id         uuid        primary key,
  firm_id         text        not null,
  source_id       text        not null,
  stream          text        not null,
  extracted_at    timestamptz not null,
  loaded_at       timestamptz not null default now(),
  artifact_uri    text        not null,
  artifact_sha256 bytea       not null,
  row_count       bigint      not null,
  cursor_start    text,
  cursor_end      text,
  fingerprint_id  uuid,
  status          text        not null default 'complete',
  constraint raw_load_status_valid check (status in ('complete','partial','failed'))
);

create index if not exists raw_load_stream on raw._load (source_id, stream, extracted_at desc);

-- Row-grain lineage. Budgeted at 2-4x mart row count; that is the point.
create table if not exists lineage.edge (
  edge_id         bigserial primary key,
  target_table    text   not null,
  target_pk       text   not null,
  load_id         uuid   not null,
  sequence        bigint not null,
  contribution    text   not null default 'source',
  constraint contribution_valid check (contribution in ('sum','source','override','derived'))
);

create index if not exists lineage_edge_target on lineage.edge (target_table, target_pk);
create index if not exists lineage_edge_load on lineage.edge (load_id, sequence);

-- Edges from a mart row to the canonical rows beneath it. Drill-through walks
-- mart -> canon -> raw, so the two hops are stored separately.
create table if not exists lineage.mart_edge (
  edge_id         bigserial primary key,
  target_table    text   not null,
  target_pk       text   not null,
  source_table    text   not null,
  source_pk       text   not null,
  contribution    text   not null default 'sum'
);

create index if not exists lineage_mart_edge_target on lineage.mart_edge (target_table, target_pk);
create index if not exists lineage_mart_edge_source on lineage.mart_edge (source_table, source_pk);
