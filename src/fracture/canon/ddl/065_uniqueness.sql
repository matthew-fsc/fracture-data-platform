-- One open row per fact.
--
-- The bitemporal model allows several versions of a fact to exist, separated by
-- system time (superseded_at) or business time (valid_from). What it must never
-- allow is two *current* rows for the same natural key and the same business
-- validity start: that is a duplicate, and duplicates double-count into every
-- mart above them without changing anything that looks wrong.
--
-- The mapping layer already deduplicates within a batch. These indexes are the
-- backstop, so a future adapter cannot reintroduce the bug quietly.

create unique index if not exists canon_party_one_open
  on canon.party (firm_id, party_id, valid_from) where superseded_at is null;

create unique index if not exists canon_household_one_open
  on canon.household (firm_id, household_id, valid_from) where superseded_at is null;

create unique index if not exists canon_household_member_one_open
  on canon.household_member (firm_id, household_id, party_id, valid_from)
  where superseded_at is null;

create unique index if not exists canon_producer_one_open
  on canon.producer (firm_id, producer_id, valid_from) where superseded_at is null;

create unique index if not exists canon_book_assignment_one_open
  on canon.book_assignment (firm_id, producer_id, household_id, valid_from)
  where superseded_at is null;

create unique index if not exists canon_account_one_open
  on canon.account (firm_id, account_id, valid_from) where superseded_at is null;

create unique index if not exists canon_balance_one_open
  on canon.balance_snapshot (firm_id, account_id, as_of_date) where superseded_at is null;

create unique index if not exists canon_policy_term_one_open
  on canon.policy_term (firm_id, account_id, term_seq) where superseded_at is null;

create unique index if not exists canon_fee_schedule_one_open
  on canon.fee_schedule (firm_id, schedule_id, valid_from) where superseded_at is null;

create unique index if not exists canon_fee_tier_one_open
  on canon.fee_tier (firm_id, schedule_id, tier_seq) where superseded_at is null;

create unique index if not exists canon_schedule_assignment_one_open
  on canon.schedule_assignment (firm_id, scope_type, scope_id, schedule_id, valid_from)
  where superseded_at is null;

create unique index if not exists canon_revenue_event_one_open
  on canon.revenue_event (firm_id, revenue_event_id) where superseded_at is null;

create unique index if not exists canon_invoice_one_open
  on canon.invoice (firm_id, invoice_id) where superseded_at is null;

create unique index if not exists canon_invoice_line_one_open
  on canon.invoice_line (firm_id, invoice_id, line_no) where superseded_at is null;

create unique index if not exists canon_cash_receipt_one_open
  on canon.cash_receipt (firm_id, receipt_id) where superseded_at is null;

create unique index if not exists canon_receipt_application_one_open
  on canon.receipt_application (firm_id, receipt_id, invoice_id) where superseded_at is null;

create unique index if not exists canon_cost_line_one_open
  on canon.cost_line (firm_id, cost_id) where superseded_at is null;

create unique index if not exists canon_service_event_one_open
  on canon.service_event (firm_id, service_event_id) where superseded_at is null;
