-- AI boundary (spec section 8).
--
-- AI drafts, extracts and summarises. It never computes a number with financial
-- consequence. That is enforced here in the database, not only in Python,
-- because a boundary that lives in one language is a boundary one import
-- statement wide.

create table if not exists ai.proposal (
  proposal_id     uuid primary key default gen_random_uuid(),
  kind            text not null,        -- field_mapping|extraction|classification|narrative
  model           text not null,
  prompt_hash     bytea not null,
  input_refs      jsonb not null,       -- load_ids and artifact_uris
  output          jsonb not null,
  materiality     numeric(20,4),        -- absolute value of the number at stake, if any
  confirmed_by    text,
  confirmed_at    timestamptz,
  rejected_reason text,
  created_at      timestamptz not null default now(),
  constraint proposal_kind_valid
    check (kind in ('field_mapping','extraction','classification','narrative','triage')),
  constraint proposal_not_both_confirmed_and_rejected
    check (not (confirmed_by is not null and rejected_reason is not null))
);

-- Per-tenant materiality threshold (spec section 8). A transcription below the
-- threshold may flow; at or above it, a human confirms.
create table if not exists ai.policy (
  kind            text primary key,
  materiality_threshold numeric(20,4) not null default 0,
  updated_at      timestamptz not null default now()
);

insert into ai.policy (kind, materiality_threshold) values
  ('field_mapping', 0),
  ('extraction', 250),
  ('classification', 0),
  ('narrative', 0),
  ('triage', 0)
on conflict (kind) do nothing;

-- Which canonical rows trace to which proposal.
create table if not exists lineage.ai_edge (
  edge_id         bigserial primary key,
  target_table    text not null,
  target_pk       text not null,
  target_column   text not null,
  proposal_id     uuid not null references ai.proposal(proposal_id),
  is_numeric      boolean not null default false,
  created_at      timestamptz not null default now()
);

create index if not exists lineage_ai_edge_target on lineage.ai_edge (target_table, target_pk);

create or replace function ai.assert_proposal_confirmed() returns trigger as $$
declare
  p record;
  threshold numeric;
begin
  select * into p from ai.proposal where proposal_id = new.proposal_id;
  if p is null then
    raise exception 'ai boundary: proposal % does not exist', new.proposal_id
      using errcode = 'check_violation';
  end if;
  if p.rejected_reason is not null then
    raise exception 'ai boundary: proposal % was rejected (%)', new.proposal_id, p.rejected_reason
      using errcode = 'check_violation';
  end if;
  if new.is_numeric then
    select materiality_threshold into threshold from ai.policy where kind = p.kind;
    threshold := coalesce(threshold, 0);
    if p.confirmed_by is null and coalesce(p.materiality, threshold) >= threshold then
      raise exception
        'ai boundary: numeric column %.% cannot be populated from unconfirmed proposal % '
        '(materiality % >= threshold %)',
        new.target_table, new.target_column, new.proposal_id,
        coalesce(p.materiality, threshold), threshold
        using errcode = 'check_violation';
    end if;
  end if;
  return new;
end;
$$ language plpgsql;

drop trigger if exists ai_edge_confirmation_gate on lineage.ai_edge;
create trigger ai_edge_confirmation_gate
  before insert or update on lineage.ai_edge
  for each row execute function ai.assert_proposal_confirmed();

-- Standing check: any numeric AI edge whose proposal is not confirmed. Should
-- always be empty; the reconciliation suite asserts that it is.
create or replace view ai.boundary_violation as
  select e.target_table, e.target_pk, e.target_column, e.proposal_id,
         p.kind, p.materiality, pol.materiality_threshold
    from lineage.ai_edge e
    join ai.proposal p using (proposal_id)
    left join ai.policy pol on pol.kind = p.kind
   where e.is_numeric
     and p.confirmed_by is null
     and coalesce(p.materiality, 0) >= coalesce(pol.materiality_threshold, 0);
