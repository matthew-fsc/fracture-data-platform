-- Control plane: fracture_control
--
-- This database is never joined to tenant data. postgres_fdw and dblink are
-- deliberately not installed; `control_plane_isolation` in the test suite
-- asserts that.

create extension if not exists "pgcrypto";

create schema if not exists control;
set search_path = control, public;

-- --------------------------------------------------------------------------
-- Registry (spec section 3.2)
-- --------------------------------------------------------------------------

create table if not exists tenant (
  tenant_id        uuid primary key default gen_random_uuid(),
  slug             text unique not null,      -- dns-safe, used in db name and s3 prefix
  legal_name       text not null,
  status           text not null,             -- provisioning|active|suspended|archived
  motion           text not null,             -- diligence|operating
  kms_key_arn      text not null,
  db_host          text not null,
  db_name          text not null,
  s3_prefix        text not null,
  created_at       timestamptz not null default now(),
  archive_after    date,                      -- diligence tenants: close + 30d
  promoted_from    uuid references tenant(tenant_id),
  constraint tenant_slug_dns_safe check (slug ~ '^[a-z][a-z0-9-]{1,38}[a-z0-9]$'),
  constraint tenant_status_valid check (status in ('provisioning','active','suspended','archived')),
  constraint tenant_motion_valid check (motion in ('diligence','operating')),
  -- A diligence tenant without a destruction date is how an ephemeral tenant
  -- quietly becomes permanent and un-budgeted.
  constraint diligence_has_archive_date check (motion <> 'diligence' or archive_after is not null)
);

create table if not exists tenant_firm (
  tenant_id        uuid not null references tenant(tenant_id) on delete cascade,
  firm_id          text not null,
  legal_name       text not null,
  role             text not null,             -- platform|addon
  close_date       date,
  folded_in_at     timestamptz,
  primary key (tenant_id, firm_id),
  constraint firm_role_valid check (role in ('platform','addon'))
);

-- Exactly one platform firm per tenant. A tenant with two platforms has an
-- ambiguous consolidation root and every roll-up below it is wrong.
create unique index if not exists tenant_one_platform_firm
  on tenant_firm (tenant_id) where role = 'platform';

create table if not exists tenant_source (
  tenant_id        uuid not null references tenant(tenant_id) on delete cascade,
  firm_id          text not null,
  source_id        text not null,             -- adapter id, e.g. 'orion', 'ams360'
  secret_path      text not null,
  status           text not null,             -- pending|verified|live|failed
  verified_read_only_at timestamptz,
  verified_by      text,
  last_error       text,
  primary key (tenant_id, firm_id, source_id),
  foreign key (tenant_id, firm_id) references tenant_firm(tenant_id, firm_id) on delete cascade,
  constraint source_status_valid check (status in ('pending','verified','live','failed')),
  -- The record you produce when a client asks how you know you could not have
  -- modified their systems. A live source without it is not defensible.
  constraint live_requires_read_only_proof check (
    status <> 'live' or (verified_read_only_at is not null and verified_by is not null)
  )
);

create table if not exists pack_run (
  pack_run_id      uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null references tenant(tenant_id) on delete cascade,
  period           daterange not null,
  system_time      timestamptz not null,      -- freezes bitemporal reads, spec 6.3
  status           text not null,             -- building|issued|superseded|failed
  content_hash     bytea,                     -- sha256 of the rendered pack
  issued_at        timestamptz,
  supersedes       uuid references pack_run(pack_run_id),
  constraint pack_status_valid check (status in ('building','issued','superseded','failed')),
  constraint issued_pack_has_hash check (status <> 'issued' or content_hash is not null)
);

create index if not exists pack_run_tenant_period on pack_run (tenant_id, lower(period));

-- --------------------------------------------------------------------------
-- Operational tables (beyond spec 3.2; noted in docs/DEVIATIONS.md)
-- --------------------------------------------------------------------------

-- Schema migrations fan out over the tenant registry (spec 3.1). Recording the
-- outcome per tenant is what makes the success threshold enforceable.
create table if not exists tenant_migration (
  tenant_id        uuid not null references tenant(tenant_id) on delete cascade,
  version          text not null,
  applied_at       timestamptz not null default now(),
  checksum         bytea not null,
  succeeded        boolean not null,
  error            text,
  primary key (tenant_id, version)
);

-- fingerprint() output per run. Drift detection (spec 16) compares the newest
-- two rows for a source and refuses to map when the schema hash moved.
create table if not exists source_fingerprint (
  fingerprint_id   uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null references tenant(tenant_id) on delete cascade,
  firm_id          text not null,
  source_id        text not null,
  observed_at      timestamptz not null default now(),
  source_version   text,
  schema_hash      bytea not null,
  row_counts       jsonb not null default '{}'::jsonb,
  streams          jsonb not null default '[]'::jsonb,
  -- Stream -> field names. Without this the drift diff degrades to "something
  -- changed", which is not actionable and not what spec 16 asks for.
  field_names      jsonb not null default '{}'::jsonb,
  read_only_verified boolean not null default false
);

create index if not exists source_fingerprint_lookup
  on source_fingerprint (tenant_id, firm_id, source_id, observed_at desc);

-- Every human query against tenant data (spec section 9), disclosable on request.
create table if not exists access_log (
  access_id        bigserial primary key,
  at               timestamptz not null default now(),
  tenant_id        uuid,
  actor            text not null,
  actor_kind       text not null,             -- human|service
  statement        text not null,
  row_count        integer,
  purpose          text,
  constraint actor_kind_valid check (actor_kind in ('human','service'))
);

create index if not exists access_log_tenant_at on access_log (tenant_id, at desc);

-- Reconciliation outcomes are retained centrally so a retainer SLA can be
-- reported on without opening every tenant database.
create table if not exists reconciliation_result (
  result_id        uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null references tenant(tenant_id) on delete cascade,
  firm_id          text not null,
  check_name       text not null,
  period           daterange not null,
  expected         numeric(20,4),
  actual           numeric(20,4),
  variance_pct     numeric(10,6),
  tolerance_pct    numeric(10,6) not null,
  passed           boolean not null,
  failing_records  jsonb not null default '[]'::jsonb,
  evaluated_at     timestamptz not null default now()
);

create index if not exists recon_tenant_check on reconciliation_result (tenant_id, check_name, evaluated_at desc);

-- --------------------------------------------------------------------------
-- Forward migrations. `create table if not exists` does not add columns to a
-- table that already exists, so anything added after first release needs an
-- explicit, idempotent alter here.
-- --------------------------------------------------------------------------

alter table control.source_fingerprint
  add column if not exists field_names jsonb not null default '{}'::jsonb;
