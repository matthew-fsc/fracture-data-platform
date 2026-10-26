-- Canonical entity layer (spec section 5).
--
-- Every canonical table carries the same four timestamps (spec 6.3):
--   valid_from / valid_to     business time, when the fact was true
--   recorded_at / superseded_at  system time, when we learned it
-- A read pinned to a pack's system_time therefore reproduces exactly.

create table if not exists canon.firm (
  canon_id      bigserial primary key,
  firm_id       text not null,
  legal_name    text not null,
  role          text not null,                -- platform|addon
  close_date    date,
  valid_from    date not null default '1900-01-01',
  valid_to      date,
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz,
  constraint firm_role_valid check (role in ('platform','addon'))
);
create unique index if not exists canon_firm_key on canon.firm (firm_id, valid_from, recorded_at);

create table if not exists canon.party (
  canon_id      bigserial primary key,
  firm_id       text not null,
  party_id      text not null,                -- source-agnostic natural key
  party_type    text not null,                -- individual|organisation|trust
  display_name  text not null,
  legal_name    text,
  country       text,
  tax_id_last4  text,                         -- never the full identifier
  valid_from    date not null,
  valid_to      date,
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz,
  constraint party_type_valid check (party_type in ('individual','organisation','trust'))
);
create unique index if not exists canon_party_key on canon.party (firm_id, party_id, valid_from, recorded_at);
create index if not exists canon_party_current on canon.party (firm_id, party_id) where superseded_at is null;

create table if not exists canon.household (
  canon_id      bigserial primary key,
  firm_id       text not null,
  household_id  text not null,
  name          text not null,
  segment       text,
  onboarded_on  date,
  valid_from    date not null,
  valid_to      date,
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz
);
create unique index if not exists canon_household_key on canon.household (firm_id, household_id, valid_from, recorded_at);

create table if not exists canon.household_member (
  canon_id      bigserial primary key,
  firm_id       text not null,
  household_id  text not null,
  party_id      text not null,
  role          text not null,                -- primary|spouse|dependent|trustee|other
  valid_from    date not null,
  valid_to      date,
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz
);
create unique index if not exists canon_hh_member_key
  on canon.household_member (firm_id, household_id, party_id, valid_from, recorded_at);

create table if not exists canon.producer (
  canon_id      bigserial primary key,
  firm_id       text not null,
  producer_id   text not null,
  display_name  text not null,
  producer_type text not null,                -- advisor|agent|partner|csa
  party_id      text,                         -- may or may not resolve to a party
  hire_date     date,
  term_date     date,
  valid_from    date not null,
  valid_to      date,
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz
);
create unique index if not exists canon_producer_key on canon.producer (firm_id, producer_id, valid_from, recorded_at);

-- Spec section 16: CRM advisor, custodian rep code and payroll employee are
-- three different keys. Resolution is a persisted, human-reviewable crosswalk,
-- not fuzzy matching at query time.
create table if not exists canon.producer_crosswalk (
  canon_id      bigserial primary key,
  firm_id       text not null,
  producer_id   text not null,
  system        text not null,                -- source_id the external key belongs to
  external_key  text not null,
  confidence    numeric(5,4) not null default 1.0,
  reviewed_by   text,
  reviewed_at   timestamptz,
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz,
  constraint crosswalk_confidence_range check (confidence >= 0 and confidence <= 1)
);
create unique index if not exists canon_crosswalk_key
  on canon.producer_crosswalk (firm_id, system, external_key) where superseded_at is null;

-- Effective dating here is non-negotiable: producer transitions are the whole
-- point of the "what walks out the door" metric.
create table if not exists canon.book_assignment (
  canon_id      bigserial primary key,
  firm_id       text not null,
  producer_id   text not null,
  household_id  text not null,
  split_pct     numeric(7,4) not null,
  valid_from    date not null,
  valid_to      date,
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz,
  constraint split_pct_range check (split_pct > 0 and split_pct <= 100)
);
create unique index if not exists canon_book_assignment_key
  on canon.book_assignment (firm_id, producer_id, household_id, valid_from, recorded_at);

create table if not exists canon.account (
  canon_id      bigserial primary key,
  firm_id       text not null,
  account_id    text not null,
  account_type  text not null,                -- custodial|policy|engagement
  account_subtype text,
  household_id  text,
  party_id      text,
  custodian     text,
  opened_on     date,
  closed_on     date,
  status        text not null default 'open',
  billable      boolean not null default true,
  valid_from    date not null,
  valid_to      date,
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz,
  constraint account_type_valid check (account_type in ('custodial','policy','engagement'))
);
create unique index if not exists canon_account_key on canon.account (firm_id, account_id, valid_from, recorded_at);
create index if not exists canon_account_household on canon.account (firm_id, household_id);
