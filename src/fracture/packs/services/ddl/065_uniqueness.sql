-- One open row per fact, for the professional services model.
--
-- Same backstop as the wealth pack's: the bitemporal model allows several
-- versions of a fact separated by system or business time, but never two
-- *current* rows for one natural key and one business-validity start. A
-- duplicate time entry double-counts into recorded hours, utilisation,
-- realisation and margin without anything looking wrong.

create unique index if not exists canon_svc_firm_one_open
  on canon.firm (firm_id, valid_from) where superseded_at is null;

create unique index if not exists canon_svc_client_one_open
  on canon.client (firm_id, client_id, valid_from) where superseded_at is null;

create unique index if not exists canon_svc_engagement_one_open
  on canon.engagement (firm_id, engagement_id, valid_from) where superseded_at is null;

create unique index if not exists canon_svc_matter_one_open
  on canon.matter (firm_id, matter_id, valid_from) where superseded_at is null;

create unique index if not exists canon_svc_timekeeper_one_open
  on canon.timekeeper (firm_id, timekeeper_id, valid_from) where superseded_at is null;

create unique index if not exists canon_svc_rate_card_one_open
  on canon.rate_card (firm_id, rate_card_id, valid_from) where superseded_at is null;

create unique index if not exists canon_svc_rate_card_line_one_open
  on canon.rate_card_line (firm_id, rate_card_id, grade, valid_from)
  where superseded_at is null;

-- An engagement can only be on one card at a time. Two overlapping
-- assignments make entitled revenue ambiguous, which is the figure the whole
-- pack is built on.
create unique index if not exists canon_svc_rate_assignment_one_open
  on canon.rate_card_assignment (firm_id, engagement_id, effective_from, valid_from)
  where superseded_at is null;

create unique index if not exists canon_svc_time_entry_one_open
  on canon.time_entry (firm_id, time_entry_id, valid_from) where superseded_at is null;

create unique index if not exists canon_svc_disbursement_one_open
  on canon.disbursement (firm_id, disbursement_id, valid_from)
  where superseded_at is null;

create unique index if not exists canon_svc_invoice_one_open
  on canon.invoice (firm_id, invoice_id, valid_from) where superseded_at is null;

create unique index if not exists canon_svc_invoice_line_one_open
  on canon.invoice_line (firm_id, invoice_line_id, valid_from)
  where superseded_at is null;

create unique index if not exists canon_svc_receipt_one_open
  on canon.cash_receipt (firm_id, receipt_id, valid_from) where superseded_at is null;

create unique index if not exists canon_svc_application_one_open
  on canon.receipt_application (firm_id, application_id, valid_from)
  where superseded_at is null;

create unique index if not exists canon_svc_cost_line_one_open
  on canon.cost_line (firm_id, cost_line_id, valid_from) where superseded_at is null;

-- Useful read paths for the marts: time by engagement and period, and the
-- receivable side by client.
create index if not exists canon_svc_time_by_engagement
  on canon.time_entry (firm_id, engagement_id, work_date) where superseded_at is null;

create index if not exists canon_svc_time_by_timekeeper
  on canon.time_entry (firm_id, timekeeper_id, work_date) where superseded_at is null;

create index if not exists canon_svc_invoice_by_client
  on canon.invoice (firm_id, client_id, issued_on) where superseded_at is null;
