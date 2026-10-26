-- Canonical measurement layer (spec section 5).
--
-- revenue_event is the spine. fee_schedule / fee_tier / schedule_assignment are
-- the differentiator: expected revenue = schedule x basis, unbilled = expected
-- minus billed, leakage = billed minus collected plus billed-below-schedule.
-- Shortcut the fee schedule model and the most differentiated finding is gone.

create table if not exists canon.balance_snapshot (
  canon_id      bigserial primary key,
  firm_id       text not null,
  account_id    text not null,
  as_of_date    date not null,
  market_value  numeric(20,4) not null,
  cash_value    numeric(20,4) not null default 0,
  billable_value numeric(20,4),               -- null means "same as market_value"
  currency      text not null default 'USD',
  valid_from    date not null,
  valid_to      date,
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz
);
create unique index if not exists canon_balance_key
  on canon.balance_snapshot (firm_id, account_id, as_of_date, recorded_at);
create index if not exists canon_balance_asof on canon.balance_snapshot (firm_id, as_of_date);

create table if not exists canon.policy_term (
  canon_id      bigserial primary key,
  firm_id       text not null,
  account_id    text not null,                -- the policy, as an account
  term_seq      integer not null,
  term_start    date not null,
  term_end      date not null,
  premium       numeric(20,4) not null,
  commission_rate numeric(9,6) not null,
  carrier       text,
  line_of_business text,
  valid_from    date not null,
  valid_to      date,
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz
);
create unique index if not exists canon_policy_term_key
  on canon.policy_term (firm_id, account_id, term_seq, recorded_at);

create table if not exists canon.fee_schedule (
  canon_id      bigserial primary key,
  firm_id       text not null,
  schedule_id   text not null,
  name          text not null,
  basis         text not null,                -- aum|flat|revenue|hours
  frequency     text not null,                -- monthly|quarterly|annual
  calc_method   text not null,                -- tiered|blended|flat
  billing_timing text not null default 'arrears', -- arrears|advance
  valuation_rule text not null default 'period_end', -- period_end|period_avg|period_start
  source_kind   text not null default 'system',     -- system|manual
  valid_from    date not null,
  valid_to      date,
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz,
  constraint fee_basis_valid check (basis in ('aum','flat','revenue','hours')),
  constraint fee_calc_valid check (calc_method in ('tiered','blended','flat')),
  constraint fee_timing_valid check (billing_timing in ('arrears','advance')),
  -- Spec section 16: schedules often live in the office manager's spreadsheet.
  -- Manual entry is a first-class path with the same lineage treatment.
  constraint fee_source_kind_valid check (source_kind in ('system','manual'))
);
create unique index if not exists canon_fee_schedule_key
  on canon.fee_schedule (firm_id, schedule_id, valid_from, recorded_at);

create table if not exists canon.fee_tier (
  canon_id      bigserial primary key,
  firm_id       text not null,
  schedule_id   text not null,
  tier_seq      integer not null,
  lower_bound   numeric(20,4) not null,
  upper_bound   numeric(20,4),                -- null = unbounded top tier
  annual_rate_bps numeric(12,6),
  flat_amount   numeric(20,4),
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz,
  constraint tier_bounds_ordered check (upper_bound is null or upper_bound > lower_bound),
  constraint tier_has_a_rate check (annual_rate_bps is not null or flat_amount is not null)
);
create unique index if not exists canon_fee_tier_key
  on canon.fee_tier (firm_id, schedule_id, tier_seq, recorded_at);

create table if not exists canon.schedule_assignment (
  canon_id      bigserial primary key,
  firm_id       text not null,
  schedule_id   text not null,
  scope_type    text not null,                -- household|account
  scope_id      text not null,
  valid_from    date not null,
  valid_to      date,
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz,
  constraint scope_type_valid check (scope_type in ('household','account'))
);
create unique index if not exists canon_schedule_assignment_key
  on canon.schedule_assignment (firm_id, scope_type, scope_id, schedule_id, valid_from, recorded_at);

-- One row per billable economic event: fee accrual, commission, retainer line.
create table if not exists canon.revenue_event (
  canon_id      bigserial primary key,
  firm_id       text not null,
  revenue_event_id text not null,
  event_type    text not null,                -- fee_accrual|commission|retainer|other
  household_id  text,
  account_id    text,
  producer_id   text,
  period_start  date not null,
  period_end    date not null,
  basis_amount  numeric(20,4),
  amount        numeric(20,4) not null,
  currency      text not null default 'USD',
  origin        text not null default 'source', -- source|computed
  valid_from    date not null,
  valid_to      date,
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz,
  constraint revenue_event_type_valid
    check (event_type in ('fee_accrual','commission','retainer','other')),
  constraint revenue_period_ordered check (period_end >= period_start)
);
create unique index if not exists canon_revenue_event_key
  on canon.revenue_event (firm_id, revenue_event_id, recorded_at);
create index if not exists canon_revenue_period on canon.revenue_event (firm_id, period_start);

create table if not exists canon.invoice (
  canon_id      bigserial primary key,
  firm_id       text not null,
  invoice_id    text not null,
  household_id  text,
  issued_on     date not null,
  due_on        date,
  period_start  date,
  period_end    date,
  total_amount  numeric(20,4) not null,
  currency      text not null default 'USD',
  status        text not null default 'open', -- open|paid|void|partial
  valid_from    date not null,
  valid_to      date,
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz
);
create unique index if not exists canon_invoice_key on canon.invoice (firm_id, invoice_id, recorded_at);

create table if not exists canon.invoice_line (
  canon_id      bigserial primary key,
  firm_id       text not null,
  invoice_id    text not null,
  line_no       integer not null,
  revenue_event_id text,
  account_id    text,
  description   text,
  amount        numeric(20,4) not null,
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz
);
create unique index if not exists canon_invoice_line_key
  on canon.invoice_line (firm_id, invoice_id, line_no, recorded_at);

create table if not exists canon.cash_receipt (
  canon_id      bigserial primary key,
  firm_id       text not null,
  receipt_id    text not null,
  household_id  text,
  received_on   date not null,
  amount        numeric(20,4) not null,
  method        text,
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz
);
create unique index if not exists canon_cash_receipt_key
  on canon.cash_receipt (firm_id, receipt_id, recorded_at);

create table if not exists canon.receipt_application (
  canon_id      bigserial primary key,
  firm_id       text not null,
  receipt_id    text not null,
  invoice_id    text not null,
  amount_applied numeric(20,4) not null,
  applied_on    date not null,
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz
);
create unique index if not exists canon_receipt_application_key
  on canon.receipt_application (firm_id, receipt_id, invoice_id, recorded_at);

create table if not exists canon.cost_line (
  canon_id      bigserial primary key,
  firm_id       text not null,
  cost_id       text not null,
  period_start  date not null,
  period_end    date not null,
  category      text not null,                -- payroll|vendor|occupancy|allocation
  vendor        text,
  person_id     text,
  amount        numeric(20,4) not null,
  allocation_basis text not null default 'revenue', -- revenue|headcount|direct|aum
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz
);
create unique index if not exists canon_cost_line_key
  on canon.cost_line (firm_id, cost_id, recorded_at);

create table if not exists canon.fte_allocation (
  canon_id      bigserial primary key,
  firm_id       text not null,
  person_id     text not null,
  producer_id   text,
  household_id  text,
  period_start  date not null,
  period_end    date not null,
  hours         numeric(12,4) not null,
  hourly_cost   numeric(14,4) not null,
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz
);
create index if not exists canon_fte_period on canon.fte_allocation (firm_id, period_start);

create table if not exists canon.service_event (
  canon_id      bigserial primary key,
  firm_id       text not null,
  service_event_id text not null,
  event_type    text not null,                -- onboarding|transfer|trade|ticket
  household_id  text,
  account_id    text,
  actor_producer_id text,
  opened_at     timestamptz not null,
  closed_at     timestamptz,
  sla_target_hours numeric(10,2),
  source_id     text not null default 'unknown',
  recorded_at   timestamptz not null default now(),
  superseded_at timestamptz
);
create unique index if not exists canon_service_event_key
  on canon.service_event (firm_id, service_event_id, recorded_at);
create index if not exists canon_service_event_opened on canon.service_event (firm_id, opened_at);
