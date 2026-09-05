# Fracture Systems Consolidator Platform
## Technical specification v1

**Status:** draft for build. Assumptions stated in §1.2 are mine, not yours. Open decisions in §17.

---

## 1. Scope

### 1.1 What this system does

Serves four commercial motions from one codebase:

| Motion | Duration | System behaviour |
|---|---|---|
| Systems and data diligence | 2 weeks | Ephemeral tenant, read-only, deliverable is findings + fold-in cost estimate |
| Platform operating model | 6 weeks | Permanent tenant provisioned, platform firm loaded, first pack issued |
| Add-on fold-in | 3 weeks per firm | New firm dimension inside an existing tenant, no new tenant |
| Operating retainer | Ongoing | Scheduled refresh, monthly pack, drill-through, support |

The diligence motion is not a separate product. It is the same pipeline run against a throwaway tenant, and its output (which adapters exist, what reconciled, what did not) *is* the fold-in cost estimate. This is the single most important architectural constraint: it means adapter coverage must be measurable, not anecdotal.

### 1.2 Assumptions

- **Tenant = acquirer** (PE platform, family office, aggregator). Acquired firms are a dimension *inside* a tenant, not separate tenants. A tenant with a platform plus six add-ons is one database with seven values in `dim_firm`.
- Volume ceiling per tenant: ~250k parties, ~500k accounts, daily position snapshots over 5 years, ~5M revenue events. Low tens of GB. This fits one Postgres instance with headroom.
- Read-only means the credential the client issues is read-only. You verify and document it; you do not control it.
- Sources are heterogeneous, semi-documented, and about 40% will require file drops rather than APIs.
- Solo builder plus one junior engineer for the first two quarters. Operational complexity is a real cost, not a rounding error.

### 1.3 Non-goals

Not a data warehouse for the acquirer's whole enterprise. Not a migration tool. Not a system of record. Not real-time. No writes into source systems, ever, including "helpful" ones.

---

## 2. Stack

| Layer | Choice | Rejected |
|---|---|---|
| Engine | Postgres 16+, one database per tenant | Databricks, Fabric, Snowflake: cost floor per tenant exceeds retainer margin at this data volume |
| Host | Neon or RDS (see §17) | Self-managed |
| Ingestion | dlt for API and DB sources, custom Python extractors for file drops | Fivetran: per-connector MAR pricing across tenants destroys margin |
| Transform | dbt Core | SQLMesh: better column lineage and restatement semantics, but row-grain lineage is custom here anyway (§6), and dbt wins on hiring pool and documentation for a two-person team. Revisit if per-tenant environment management becomes the bottleneck |
| Orchestration | Dagster, tenant as dynamic partition | Airflow: weaker asset lineage, worse fit for per-tenant partitioning |
| Reporting | Evidence.dev, git-versioned, compiled per tenant | Power BI: per-tenant workspace overhead, no clean drill-to-row |
| IaC | Terraform | |
| Runtime | Containers on ECS Fargate or Fly | |
| Secrets | AWS Secrets Manager, per-tenant KMS key | |

Object storage (S3) holds every raw extraction artifact. This is the evidence trail, and it is separate from the database on purpose: if a tenant disputes a number, you produce the file you received and its hash.

---

## 3. Tenancy

### 3.1 Model

Database-per-tenant. Not schema-per-tenant, not RLS.

Rationale, in order of weight:
1. Your one-pager promises isolation enforced at the database rather than application code. A separate database is the literal implementation of that sentence. RLS is one missed `WHERE` clause from a cross-tenant leak and does not survive a PE security questionnaire as cleanly.
2. Export in full becomes `pg_dump`. Contractual obligation satisfied by a shell command.
3. At exit, you hand the buyer a database, not a filtered extract.
4. Noisy-neighbour and blast radius are trivially bounded.

Cost: schema migrations must run N times. Solved by making migration a Dagster asset that fans out over the tenant registry, with a required success threshold before the run is marked green.

### 3.2 Control plane

A separate database, `fracture_control`, never joined to tenant data. `postgres_fdw` and `dblink` are not installed anywhere.

```sql
create table tenant (
  tenant_id        uuid primary key,
  slug             text unique not null,      -- dns-safe, used in db name and s3 prefix
  legal_name       text not null,
  status           text not null,             -- provisioning|active|suspended|archived
  motion           text not null,             -- diligence|operating
  kms_key_arn      text not null,
  db_host          text not null,
  created_at       timestamptz not null default now(),
  archive_after    date                        -- diligence tenants: close + 30d
);

create table tenant_firm (
  tenant_id        uuid references tenant,
  firm_id          text not null,
  legal_name       text not null,
  role             text not null,             -- platform|addon
  close_date       date,
  folded_in_at     timestamptz,
  primary key (tenant_id, firm_id)
);

create table tenant_source (
  tenant_id        uuid,
  firm_id          text,
  source_id        text not null,             -- adapter id, e.g. 'orion', 'ams360'
  secret_path      text not null,
  status           text not null,             -- pending|verified|live|failed
  verified_read_only_at timestamptz,
  verified_by      text,
  primary key (tenant_id, firm_id, source_id)
);

create table pack_run (
  pack_run_id      uuid primary key,
  tenant_id        uuid,
  period           daterange not null,
  system_time      timestamptz not null,      -- freezes bitemporal reads, §6.3
  status           text not null,
  issued_at        timestamptz,
  supersedes       uuid references pack_run
);
```

`verified_read_only_at` is not decoration. It is the record you produce when a client asks how you know you could not have modified their systems.

### 3.3 Roles

Per tenant database:

- `t_<slug>_loader` — insert only on `raw.*`, no update, no delete
- `t_<slug>_transform` — full DML on `stg/canon/mart`, select on `raw`
- `t_<slug>_reader` — select on `mart` and `lineage` only
- `t_<slug>_owner` — DDL, used by migrations only

No role can connect to another tenant's database. Connection strings are assembled at runtime from the control plane and never stored in code or in Dagster config.

---

## 4. Source adapters

This is the product. Everything else is plumbing that already exists. Your fold-in cost, and therefore your margin, is almost entirely adapter coverage.

### 4.1 Contract

```python
class SourceAdapter(Protocol):
    source_id: str
    vertical: Literal["wealth", "insurance", "accounting", "shared"]
    capabilities: Capabilities   # which canonical entities it can populate, and at what grain

    def fingerprint(self, creds: Creds) -> SourceFingerprint:
        """Version, schema hash, row counts. Run first in diligence.
        Cheap, read-only, safe against production."""

    def discover(self, creds: Creds) -> list[Stream]: ...

    def extract(self, stream: Stream, cursor: Cursor | None) -> Iterator[RecordBatch]:
        """Yields batches. Never mutates. Never paginates past `cursor` semantics."""

    def map(self, batch: RecordBatch) -> list[CanonicalRecord]:
        """Pure function. Raw payload to canonical, with lineage refs attached."""
```

`Capabilities` is machine-readable and drives the diligence deliverable directly: given the sources a target runs, you compute which canonical entities you can populate, at what completeness, and what is manual. That table is the fold-in estimate.

### 4.2 Required tests per adapter

An adapter is not shippable without:
- a discovery snapshot fixture, checked in
- three extraction fixtures covering empty, typical, and pathological (nulls, unicode, negative amounts, backdated records)
- golden canonical output for each fixture
- a redaction test proving no PII leaves the mapping layer in logs
- a static check that the module contains no mutating verbs against the source

### 4.3 Priority order

Tier 1, build first, covers the RIA aggregator ICP:
Orion, Redtail, Wealthbox, Schwab and Fidelity custodian file feeds, QuickBooks Online, and a generic CSV/SFTP adapter with a declarative column-mapping config.

Tier 2: Addepar, Black Diamond, Salesforce FSC, Pershing, Bill.com, Gusto/ADP.

Tier 3, insurance track: AMS360, Applied Epic, EZLynx, carrier commission statement parsers (these are PDF and Excel, and are the hardest thing in the whole system).

The generic CSV/SFTP adapter is worth more than any single named integration. Half of tier 1 in practice arrives as a nightly file.

---

## 5. Canonical schema

Grain is stated because ambiguous grain is where roll-up reporting dies.

**Entity layer**
- `firm` — one row per acquired firm
- `party` — one row per person or organisation; `party_type`
- `household` — grouping; `household_member` resolves party to household with role and effective dates
- `producer` — advisor, agent, or partner; may or may not be a `party`
- `book_assignment` — producer to household, effective dated, with split percentage. Effective dating here is non-negotiable; producer transitions are the whole point of the "what walks out the door" metric
- `account` — polymorphic: custodial account, insurance policy, or accounting engagement. `account_type` plus subtype tables

**Measurement layer**
- `balance_snapshot` — one row per account per date. Source of AUM
- `policy_term` — one row per policy per term, with premium and commission rate
- `fee_schedule` / `fee_tier` / `schedule_assignment` — the billing rules as they exist, tiered, effective dated
- `revenue_event` — one row per billable economic event. Fee accrual, commission, retainer line. This is the spine
- `invoice` / `invoice_line` — what was actually billed
- `cash_receipt` / `receipt_application` — what was collected and against what
- `cost_line` — payroll, vendor, allocation basis
- `fte_allocation` — producer and staff time basis for fully loaded margin
- `service_event` — onboarding step, transfer, trade, ticket, with `opened_at`, `closed_at`, `sla_target`

**Derived, materialised in marts**
- receivables ageing, expected revenue, unbilled variance, leakage, margin by client and by producer, SLA breach rates, concentration.

The unbilled and leakage claims on the one-pager depend entirely on `fee_schedule` being modelled properly. Expected revenue is computed from schedule times basis; unbilled is expected minus billed; leakage is billed minus collected, plus billed-below-schedule. If you shortcut the fee schedule model you lose the most differentiated finding in the offer.

---

## 6. Layers, auditability, and time

### 6.1 Raw

Append-only. Never updated, never deleted outside retention.

```sql
create table raw.<source>__<stream> (
  _load_id        uuid        not null,
  _sequence       bigint      not null,
  _firm_id        text        not null,
  _source_id      text        not null,
  _extracted_at   timestamptz not null,
  _loaded_at      timestamptz not null default now(),
  _artifact_uri   text        not null,       -- s3 object this row came from
  _record_hash    bytea       not null,       -- sha256 of canonical-ordered payload
  _payload        jsonb       not null,
  primary key (_load_id, _sequence)
);
```

Every extraction writes its artifact to S3 first, records the SHA-256, then loads. If the database is rebuilt from scratch, it is rebuildable from S3 alone.

### 6.2 Lineage

Column-level lineage from dbt is not sufficient for "every figure opens to the records behind it". You need row grain, and it is an explicit table, not a metadata by-product:

```sql
create table lineage.edge (
  target_table    text   not null,
  target_pk       text   not null,
  load_id         uuid   not null,
  sequence        bigint not null,
  contribution    text            -- 'sum'|'source'|'override'
);
create index on lineage.edge (target_table, target_pk);
```

Populated by the mapping layer and by mart models. Drill-through is a single query against this table joined back to `raw._payload` and `_artifact_uri`. Budget for it being 2 to 4x the row count of your marts. It is worth it: this table is the difference between your offer and a Power BI consultant's.

### 6.3 Bitemporality

Canonical tables carry four timestamps: `valid_from` / `valid_to` (business time, when the fact was true) and `recorded_at` / `superseded_at` (system time, when you learned it).

Why it matters commercially: a board pack issued in March and a restated figure in June must both be reproducible and explainable. `pack_run.system_time` freezes system time for a pack. Reissuing a pack with the same `system_time` produces byte-identical numbers. Reissuing with a new one produces the restatement, and the delta between them is a report you can sell.

dbt snapshots cover the SCD2 half. The system-time half is a convention you enforce in your canonical models, not something dbt gives you.

### 6.4 Layer naming

`raw` → `stg` (typed, deduped, one model per source stream) → `canon` (canonical entities, source-agnostic, bitemporal) → `mart` (metric-ready, per-firm and consolidated) → `pack` (pinned to a `pack_run_id`).

---

## 7. Orchestration

Dagster. Two partition dimensions: `tenant` (dynamic, sourced from the control plane) and `date`.

Asset graph per tenant:

```
source_fingerprint_<source>
  └─ raw_<source>_<stream>
       └─ stg_<source>_<stream>
            └─ canon_<entity>            (fan-in across sources)
                 └─ mart_<metric>
                      └─ pack_<section>
```

Each run executes in an isolated process with exactly one tenant's DSN injected via a scoped role assumption. There is no code path in the orchestrator that holds two tenants' credentials simultaneously.

Freshness policies on `canon_*` drive the retainer SLA. A missed refresh should page you before the client notices, because the client noticing first is how a $2,500/month retainer gets cancelled.

Reconciliation checks run as assets, not as an afterthought: total revenue by firm by month from `canon` versus the source system's own report, with a tolerance and a hard failure above it. This runs every refresh, not just at fold-in.

---

## 8. AI boundary

The one-pager says AI drafts, extracts and summarises and never computes a number with financial consequence. Enforce it structurally.

**Permitted:** field mapping proposals during fold-in; unstructured extraction (commission statement PDFs, engagement letters); anomaly triage narrative; pack commentary drafting; classification suggestions.

**Forbidden:** producing any value that lands in a numeric canonical or mart column without a human confirmation row.

```sql
create table ai.proposal (
  proposal_id     uuid primary key,
  kind            text not null,
  model           text not null,
  prompt_hash     bytea not null,
  input_refs      jsonb not null,      -- load_ids and artifact_uris
  output          jsonb not null,
  confirmed_by    text,
  confirmed_at    timestamptz,
  rejected_reason text
);
```

Canonical models reject any row whose lineage traces to an unconfirmed proposal. Extraction from a commission PDF is permitted because the extracted figure is a *transcription* with an artifact behind it, but it still requires confirmation above a materiality threshold you set per tenant.

---

## 9. Security

Assume a PE operations partner sends a security questionnaire on the first deal. Build for that, not for a theoretical audit.

- Per-tenant KMS key, envelope encryption at rest, TLS everywhere
- `pgaudit` on, audit log shipped out of the tenant database to append-only storage
- Credentials issued by the client, stored per tenant, rotated on a documented cadence, never in source control or Dagster config
- Access log of every human query against tenant data, queryable and disclosable
- Read-only verification recorded per source with who verified and when
- SOC 2 Type I is the realistic first milestone. Start the compliance tooling before the first paid engagement, not after the first questionnaire

---

## 10. Infrastructure as code

Terraform modules:

| Module | Contents |
|---|---|
| `network` | VPC, subnets, endpoints |
| `control-plane` | control database, registry, Dagster deployment |
| `tenant` | database, four roles, KMS key, S3 prefix, secret paths, Dagster partition registration |
| `compute` | Fargate services and task definitions |
| `reporting` | Evidence build pipeline, pack storage, signed-URL delivery |

Tenant standup is `terraform apply -var tenant_slug=x`. Target: under 30 minutes from signed SOW to a tenant that can accept its first credential. Diligence tenants use the same module with `motion=diligence` and a populated `archive_after`.

Environments: local (docker compose plus a synthetic tenant generator), staging, prod. Build the synthetic tenant generator early. You cannot develop against client data, and a realistic fake RIA with 3,000 households, tiered fee schedules, and deliberately broken billing is the fixture you will use for every demo as well.

---

## 11. Reporting and the monthly pack

Evidence.dev, one project, per-tenant compilation. Markdown plus SQL in git means the pack definition is versioned and diffable, which matters when a client asks why a section changed.

Every figure in the pack renders with a drill-through link resolving to a lineage view: the canonical rows, then the raw payloads, then the S3 artifact. Delivery as both hosted (retainer clients) and PDF (board and lender distribution).

Pack sections map to the one-pager promises: one model of the platform; revenue and margin by client and producer fully loaded; unbilled and leaked revenue; onboarding, transfer and service SLAs.

---

## 12. Fold-in runbook (3 weeks)

**Week 1** — source inventory, fingerprint every system, issue credential requests, verify read-only, select or scope adapters, load raw for all covered sources.

**Week 2** — map to canonical, run reconciliation against the firm's own reports, drive variance below tolerance, resolve fee schedules (this is always the slow part).

**Week 3** — variance review with the client, sign-off on definitions, merge the firm into consolidated marts, first pack including the new firm.

If week 2 reconciliation cannot be driven below tolerance, that is a scoped finding, not a schedule extension. Say so in the SOW.

---

## 13. Diligence mode (2 weeks)

Ephemeral tenant, `motion=diligence`. Deliverables generated from the system rather than written by hand:
- systems inventory, from `fingerprint()` output across discovered sources
- reproducibility finding: does the revenue, AUM or book shown reproduce from custodian, carrier or billing records, expressed as a variance percentage with the failing records enumerated
- key-person and process dependency, from `book_assignment` concentration and `service_event` actor concentration
- fold-in cost, computed from adapter coverage against the target's system list

Tenant is destroyed on a no-deal or promoted to `motion=operating` on close. Promotion must be a supported path, not a rebuild, or you lose the credit-at-close economics.

---

## 14. Cost per tenant

Rough monthly, at target volumes: database $100 to $300, S3 and KMS under $10, shared orchestrator and reporting compute amortised at $50 to $100. Call it $400 all-in against a $2,500 retainer before your time.

For contrast, a Fabric F2 capacity is roughly $260 per month per capacity before storage, and F2 is undersized for this workload; sharing one capacity across tenants reintroduces the isolation problem you priced the architecture to avoid.

---

## 15. Build sequence

**Phase 0 (4 to 6 weeks)** — control plane, tenant Terraform module, raw layer, lineage edge table, Dagster skeleton, one adapter (Orion or the generic CSV adapter) end to end, synthetic tenant generator.

**Phase 1 (6 to 8 weeks)** — wealth canonical schema, fee schedule model, margin and unbilled marts, reconciliation assets, tier 1 adapters.

**Phase 2 (4 weeks)** — pack in Evidence, drill-through UI, PDF delivery, pack run pinning.

**Phase 3** — insurance vertical: AMS360, Applied Epic, commission statement extraction.

**Phase 4** — SOC 2 Type I, tier 2 adapters.

Phases 0 and 1 are the six-week platform engagement made repeatable. Do not start phase 3 before you have two paying tenants in the wealth vertical.

---

## 16. Failure modes to design against

- **Fee schedules are undocumented.** The firm bills from a spreadsheet the office manager maintains. Assume this. Build schedule ingestion as a first-class manual-entry path with the same lineage treatment as an API source.
- **The client changes the source system mid-retainer.** `fingerprint()` on every run, and alert on schema hash change before mapping silently drops a column.
- **Producer identity does not resolve across systems.** CRM advisor, custodian rep code, and payroll employee are three different keys. Entity resolution with a persisted, human-reviewable crosswalk table, not fuzzy matching at query time.
- **Fold-in scope creep.** Adapter capability manifests are the contractual boundary. Anything outside a manifest is a change order.

---

## 17. Open decisions

1. **Neon versus RDS.** Neon's branching makes per-tenant dev and point-in-time investigation genuinely cheaper, and database-per-tenant is its intended pattern. RDS is the easier answer on a PE security questionnaire. I lean Neon for phase 0 and 1, with the abstraction kept thin enough to move.
2. **Whether the tenant boundary is the acquirer or the fund/platform.** If a single sponsor runs three unrelated platforms, one database each is probably right, but it changes the consolidated-reporting story.
3. **Read API.** Do you expose tenant data programmatically, or only through packs and drill-through? Affects whether isolation has to hold at an application layer too.
4. **Raw retention.** Indefinite is the right answer for the audit story and the wrong answer for a data breach. Propose seven years with per-tenant override.
5. **dbt versus SQLMesh.** Committed to dbt above. Revisit if per-tenant environment management or restatement handling becomes the primary source of operational pain.
