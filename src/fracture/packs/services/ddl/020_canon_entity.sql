-- Professional services: the canonical entity layer.
--
-- The chassis already owns firm, party, producer, book assignment and the
-- bitemporal and lineage machinery. What a professional services firm adds is
-- the thing work is done against -- a client, an engagement and a matter --
-- and the rate card that says what an hour of a given grade is worth.
--
-- Numbered 020-039 so it sorts between the chassis schemas and the chassis
-- bitemporal scripts, exactly as the wealth pack's does.

set search_path = canon, public;

-- The firm doing the work, and the firms it works for. Same shape as the
-- wealth pack's `firm` because the chassis joins to it by firm_id.
create table if not exists firm (
  canon_id      bigserial primary key,
  firm_id       text not null,
  legal_name    text not null,
  role          text not null,              -- practice|client
  practice_area text,
  opened_on     date,
  closed_on     date,
  valid_from    date not null default '-infinity',
  valid_to      date,
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz,
  source_id     text not null,
  constraint firm_role_known check (role in ('practice','client'))
);

-- A client of the practice. Separate from `firm` because a client may be an
-- individual, and because the billing relationship carries terms the firm
-- entity does not.
create table if not exists client (
  canon_id        bigserial primary key,
  firm_id         text not null,
  client_id       text not null,
  name            text not null,
  client_type     text not null default 'organisation',  -- organisation|individual
  industry        text,
  -- Agreed payment terms in days. Lockup is measured against this, not against
  -- a firm-wide default, because the firm-wide default is the number everyone
  -- quotes and nobody has agreed to.
  payment_terms_days int not null default 30,
  onboarded_on    date,
  valid_from      date not null default '-infinity',
  valid_to        date,
  recorded_at     timestamptz not null default now(),
  superseded_at   timestamptz,
  source_id       text not null,
  constraint client_type_known check (client_type in ('organisation','individual')),
  constraint payment_terms_sane check (payment_terms_days between 0 and 365)
);

-- An engagement: the unit a fee is agreed for and a margin is measured on.
create table if not exists engagement (
  canon_id        bigserial primary key,
  firm_id         text not null,
  engagement_id   text not null,
  client_id       text not null,
  name            text not null,
  -- How the engagement is priced. This is the single most important dimension
  -- in the pack: a fixed-fee engagement and a time-and-materials engagement
  -- fail in opposite directions, and averaging them hides both.
  fee_basis       text not null,            -- fixed|time_and_materials|capped|retainer
  -- Agreed fee for fixed, capped and retainer work. Null for pure T&M.
  agreed_fee      numeric(18,2),
  currency        text not null default 'USD',
  stage           text not null,            -- pipeline|active|delivered|closed|lost
  responsible_producer_id text,
  started_on      date,
  due_on          date,
  closed_on       date,
  valid_from      date not null default '-infinity',
  valid_to        date,
  recorded_at     timestamptz not null default now(),
  superseded_at   timestamptz,
  source_id       text not null,
  constraint fee_basis_known check (
    fee_basis in ('fixed','time_and_materials','capped','retainer')
  ),
  constraint stage_known check (
    stage in ('pipeline','active','delivered','closed','lost')
  ),
  -- A fixed or capped engagement without an agreed fee cannot have its margin
  -- computed, and a zero would read as free work rather than as missing data.
  constraint priced_work_has_a_fee check (
    fee_basis = 'time_and_materials' or agreed_fee is not null
  )
);

-- A matter or workstream inside an engagement. Time is recorded against this,
-- so it is the grain at which scope creep becomes visible.
create table if not exists matter (
  canon_id      bigserial primary key,
  firm_id       text not null,
  matter_id     text not null,
  engagement_id text not null,
  name          text not null,
  -- Hours the matter was scoped at. The gap between this and hours recorded is
  -- the overrun, and it is only knowable if the scope was written down.
  scoped_hours  numeric(12,2),
  status        text not null default 'open',   -- open|closed
  opened_on     date,
  closed_on     date,
  valid_from    date not null default '-infinity',
  valid_to      date,
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz,
  source_id     text not null,
  constraint matter_status_known check (status in ('open','closed'))
);

-- Someone who records time. `producer` in the chassis carries the person;
-- this carries what the practice costs for them and what it charges.
create table if not exists timekeeper (
  canon_id        bigserial primary key,
  firm_id         text not null,
  timekeeper_id   text not null,
  producer_id     text,
  grade           text not null,            -- partner|director|manager|consultant|analyst
  -- Hours a year this person is expected to be chargeable for. Utilisation is
  -- measured against this rather than against a calendar year, because a
  -- part-time or newly joined timekeeper is not underperforming.
  target_hours    numeric(10,2) not null default 1500,
  -- Fully loaded cost per hour: salary, employment taxes, benefits and the
  -- firm's overhead allocation. Not salary alone, which is the number that
  -- makes every engagement look profitable.
  cost_rate       numeric(12,2) not null,
  currency        text not null default 'USD',
  started_on      date,
  ended_on        date,
  valid_from      date not null default '-infinity',
  valid_to        date,
  recorded_at     timestamptz not null default now(),
  superseded_at   timestamptz,
  source_id       text not null,
  constraint grade_known check (
    grade in ('partner','director','manager','consultant','analyst')
  ),
  constraint cost_rate_positive check (cost_rate > 0),
  constraint target_hours_sane check (target_hours >= 0)
);
