-- Reconciliation (spec section 7): checks run as assets, every refresh.
--
-- control_total holds the figure the source system reports for itself. Without
-- it there is nothing to reconcile against and "the numbers look right" is the
-- only available assurance.

create table if not exists recon.control_total (
  control_id      bigserial primary key,
  firm_id         text not null,
  source_id       text not null,
  check_name      text not null,
  period_start    date not null,
  period_end      date not null,
  grain_key       text not null default '',
  expected_value  numeric(20,4) not null,
  artifact_uri    text,
  load_id         uuid,
  recorded_at     timestamptz not null default now()
);

create unique index if not exists recon_control_total_key
  on recon.control_total (firm_id, source_id, check_name, period_start, grain_key, recorded_at);

create table if not exists recon.result (
  result_id       bigserial primary key,
  firm_id         text not null,
  check_name      text not null,
  period_start    date not null,
  period_end      date not null,
  grain_key       text not null default '',
  expected        numeric(20,4),
  actual          numeric(20,4),
  variance        numeric(20,4),
  variance_pct    numeric(12,6),
  tolerance_pct   numeric(12,6) not null,
  passed          boolean not null,
  detail          jsonb not null default '{}'::jsonb,
  evaluated_at    timestamptz not null default now()
);

create index if not exists recon_result_check on recon.result (check_name, evaluated_at desc);

-- Schema drift observations (spec section 16). Recorded even when the run is
-- allowed to proceed, so "the column disappeared in April" is answerable.
create table if not exists recon.schema_drift (
  drift_id        bigserial primary key,
  firm_id         text not null,
  source_id       text not null,
  observed_at     timestamptz not null default now(),
  previous_hash   bytea,
  current_hash    bytea not null,
  added_fields    jsonb not null default '[]'::jsonb,
  removed_fields  jsonb not null default '[]'::jsonb,
  acknowledged_by text,
  acknowledged_at timestamptz
);

-- Two sources disagreeing about the same canonical fact. The lower-authority
-- source does not overwrite (see fracture.canon.precedence); the disagreement
-- lands here and becomes a finding rather than a coin flip.
create table if not exists recon.source_variance (
  variance_id     bigserial primary key,
  firm_id         text not null,
  entity          text not null,
  canon_id        text not null,
  authoritative_source text not null,
  deferred_source text not null,
  detail          jsonb not null default '[]'::jsonb,
  observed_at     timestamptz not null default now(),
  resolved_by     text,
  resolved_at     timestamptz
);

create index if not exists recon_source_variance_entity
  on recon.source_variance (entity, observed_at desc);

-- Each invocation of the check suite is one run. Without this, "how many checks
-- passed" counts every check ever evaluated, which grows on each rebuild and
-- makes a pack's assurance section irreproducible.
alter table recon.result add column if not exists run_id uuid;

create index if not exists recon_result_run on recon.result (run_id);

create or replace view recon.latest_result as
  select r.*
    from recon.result r
   where r.run_id = (
     select run_id from recon.result
      where run_id is not null
      order by evaluated_at desc limit 1
   );
