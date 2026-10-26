-- Professional services: the canonical measure layer.
--
-- This is where the pack earns its keep. A wealth book's entitlement is AUM
-- times a fee schedule; a professional services firm's entitlement is time
-- recorded times a rate card. Same three-yield spine, different basis.
--
-- The difference that matters is that professional services has an inventory
-- stage wealth does not. Work is recorded, then some of it is billable, then
-- some of that is billed, then some of that is collected. Four stages, three
-- gaps, and each gap has a different owner:
--
--   recorded -> billable    write-off at entry: scope, goodwill, non-chargeable
--   billable -> billed      realisation: what the bill actually went out at
--   billed   -> collected   lockup: how long the cash sat in WIP and debtors
--
-- Firms routinely report the middle one as a single "realisation" percentage
-- and manage the third by chasing. The first is usually invisible, which is
-- why overrun is discovered at the invoice rather than in the week it happened.

set search_path = canon, public;

-- The rate card. Deliberately the same shape as the wealth pack's fee
-- schedule: a named, dated, tiered price list. A rate card is a fee schedule
-- whose dimension is grade rather than assets.
create table if not exists rate_card (
  canon_id       bigserial primary key,
  firm_id        text not null,
  rate_card_id   text not null,
  name           text not null,
  currency       text not null default 'USD',
  effective_from date not null,
  effective_to   date,
  valid_from     date not null default '-infinity',
  valid_to       date not null default 'infinity',
  recorded_at    timestamptz not null default now(),
  superseded_at  timestamptz,
  source_id      text not null
);

create table if not exists rate_card_line (
  canon_id      bigserial primary key,
  firm_id       text not null,
  rate_card_id  text not null,
  grade         text not null,
  -- Charge-out rate per hour for this grade on this card.
  charge_rate   numeric(12,2) not null,
  valid_from    date not null default '-infinity',
  valid_to      date not null default 'infinity',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz,
  source_id     text not null,
  constraint charge_rate_positive check (charge_rate > 0)
);

-- Which card an engagement is priced on, effective dated. An engagement
-- repriced mid-flight keeps both assignments, so last quarter's entitlement
-- still recomputes at last quarter's rates.
create table if not exists rate_card_assignment (
  canon_id       bigserial primary key,
  firm_id        text not null,
  engagement_id  text not null,
  rate_card_id   text not null,
  -- A client-specific discount off the card, as a fraction. Held separately
  -- from the card so the card stays comparable across clients: a discount is
  -- a commercial decision about one client, not a different price list.
  discount_pct   numeric(6,4) not null default 0,
  effective_from date not null,
  effective_to   date,
  valid_from     date not null default '-infinity',
  valid_to       date not null default 'infinity',
  recorded_at    timestamptz not null default now(),
  superseded_at  timestamptz,
  source_id      text not null,
  constraint discount_is_a_fraction check (discount_pct >= 0 and discount_pct < 1)
);

-- One line of recorded time. The grain of the whole pack.
create table if not exists time_entry (
  canon_id       bigserial primary key,
  firm_id        text not null,
  time_entry_id  text not null,
  engagement_id  text not null,
  matter_id      text,
  timekeeper_id  text not null,
  work_date      date not null,
  hours          numeric(8,2) not null,
  -- Hours the timekeeper marked as chargeable. Less than `hours` when work was
  -- written off at entry. Kept as its own column rather than a flag because
  -- partial write-off at entry is the common case and a boolean loses it.
  billable_hours numeric(8,2) not null,
  -- Why the difference, when there is one. Null when fully billable. This is
  -- the column that makes the first gap diagnosable instead of a residual.
  writeoff_reason text,                     -- scope|goodwill|training|admin|rework
  narrative      text,
  -- Set when the entry has been picked up by an invoice line.
  billed_on      date,
  valid_from     date not null default '-infinity',
  valid_to       date not null default 'infinity',
  recorded_at    timestamptz not null default now(),
  superseded_at  timestamptz,
  source_id      text not null,
  constraint hours_positive check (hours > 0),
  -- Billable hours cannot exceed recorded hours. Over-recording against a
  -- client is a refund exposure, exactly as over-billing a fee schedule is in
  -- the wealth pack, so it is refused rather than netted off.
  constraint billable_within_recorded check (
    billable_hours >= 0 and billable_hours <= hours
  ),
  -- A write-off without a reason is a number nobody can act on.
  constraint writeoff_has_a_reason check (
    billable_hours = hours or writeoff_reason is not null
  ),
  constraint writeoff_reason_known check (
    writeoff_reason is null
    or writeoff_reason in ('scope','goodwill','training','admin','rework')
  )
);

-- Expenses and disbursements recharged to a client.
create table if not exists disbursement (
  canon_id        bigserial primary key,
  firm_id         text not null,
  disbursement_id text not null,
  engagement_id   text not null,
  incurred_on     date not null,
  amount          numeric(18,2) not null,
  rechargeable    boolean not null default true,
  category        text,
  billed_on       date,
  valid_from      date not null default '-infinity',
  valid_to        date not null default 'infinity',
  recorded_at     timestamptz not null default now(),
  superseded_at   timestamptz,
  source_id       text not null
);

-- Invoices, lines, receipts and applications. Same shape as the wealth pack
-- because the cash side of a professional services firm is not special: the
-- difference is entirely in what entitles you to invoice.
create table if not exists invoice (
  canon_id      bigserial primary key,
  firm_id       text not null,
  invoice_id    text not null,
  client_id     text not null,
  engagement_id text,
  issued_on     date not null,
  due_on        date,
  period_start  date,
  period_end    date,
  total_amount  numeric(18,2) not null,
  currency      text not null default 'USD',
  status        text not null default 'issued',  -- issued|paid|part_paid|written_off|void
  valid_from    date not null default '-infinity',
  valid_to      date not null default 'infinity',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz,
  source_id     text not null,
  constraint invoice_status_known check (
    status in ('issued','paid','part_paid','written_off','void')
  )
);

create table if not exists invoice_line (
  canon_id        bigserial primary key,
  firm_id         text not null,
  invoice_line_id text not null,
  invoice_id      text not null,
  engagement_id   text,
  matter_id       text,
  line_type       text not null default 'time',  -- time|disbursement|fixed_fee|adjustment
  -- Hours this line billed, for time lines. Null for a fixed-fee line, which
  -- is the point: a fixed-fee engagement's realisation is measured against the
  -- agreed fee, not against hours.
  hours           numeric(10,2),
  amount          numeric(18,2) not null,
  narrative       text,
  valid_from      date not null default '-infinity',
  valid_to        date not null default 'infinity',
  recorded_at     timestamptz not null default now(),
  superseded_at   timestamptz,
  source_id       text not null,
  constraint line_type_known check (
    line_type in ('time','disbursement','fixed_fee','adjustment')
  )
);

create table if not exists cash_receipt (
  canon_id      bigserial primary key,
  firm_id       text not null,
  receipt_id    text not null,
  client_id     text not null,
  received_on   date not null,
  amount        numeric(18,2) not null,
  method        text,
  currency      text not null default 'USD',
  valid_from    date not null default '-infinity',
  valid_to      date not null default 'infinity',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz,
  source_id     text not null
);

create table if not exists receipt_application (
  canon_id       bigserial primary key,
  firm_id        text not null,
  application_id text not null,
  receipt_id     text not null,
  invoice_id     text not null,
  amount         numeric(18,2) not null,
  applied_on     date not null,
  valid_from     date not null default '-infinity',
  valid_to       date not null default 'infinity',
  recorded_at    timestamptz not null default now(),
  superseded_at  timestamptz,
  source_id      text not null
);

-- Costs the practice carries that are not a timekeeper's hours: software,
-- subcontractors, premises. Allocated to engagements where attributable and
-- pooled where not, the same two-bucket treatment the wealth pack uses, which
-- exists because excluding the pool makes every engagement look profitable.
create table if not exists cost_line (
  canon_id      bigserial primary key,
  firm_id       text not null,
  cost_line_id  text not null,
  period_start  date not null,
  period_end    date not null,
  category      text not null,
  amount        numeric(18,2) not null,
  -- Set when the cost belongs to one engagement. Null means it is pooled and
  -- allocated across the book.
  engagement_id text,
  valid_from    date not null default '-infinity',
  valid_to      date not null default 'infinity',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz,
  source_id     text not null
);
