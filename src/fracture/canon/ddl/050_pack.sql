-- Pack layer: figures pinned to a pack_run_id (spec sections 6.4, 11).
--
-- A pack row is immutable once written. Reissuing at the same system_time must
-- produce byte-identical numbers; reissuing at a new one produces the
-- restatement, and the delta between the two is itself a report.

create table if not exists pack.figure (
  figure_id       bigserial primary key,
  pack_run_id     uuid not null,
  section         text not null,
  metric          text not null,
  firm_id         text,
  grain_key       text,                        -- household_id, producer_id, month, ...
  grain_label     text,
  numeric_value   numeric(20,4),
  text_value      text,
  unit            text,
  sort_order      integer not null default 0,
  drill_query     text,                        -- resolves to lineage for this figure
  created_at      timestamptz not null default now()
);

create index if not exists pack_figure_run on pack.figure (pack_run_id, section, sort_order);
create unique index if not exists pack_figure_identity
  on pack.figure (pack_run_id, section, metric, coalesce(firm_id,''), coalesce(grain_key,''));

create table if not exists pack.section (
  pack_run_id     uuid not null,
  section         text not null,
  title           text not null,
  narrative       text,
  narrative_proposal_id uuid references ai.proposal(proposal_id),
  sort_order      integer not null default 0,
  primary key (pack_run_id, section)
);

create table if not exists pack.manifest (
  pack_run_id     uuid primary key,
  tenant_slug     text not null,
  period_start    date not null,
  period_end      date not null,
  system_time     timestamptz not null,
  figure_count    integer not null,
  content_hash    bytea not null,
  built_at        timestamptz not null default now()
);
