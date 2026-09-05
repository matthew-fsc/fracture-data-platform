# Where this build departs from the specification

Every entry is a decision, not an oversight. Anything I could not do in this
environment is in the last section, marked as untested rather than done.

## Substitutions

**Evidence.dev replaced by a pack compiler.** Spec section 11 names Evidence.dev.
Pack definitions here are still SQL in git (`reporting/packs/*.sql`), still
per-tenant compiled, still diffable when a client asks why a section changed --
which is what the spec is actually buying. The renderer
(`fracture.pack.render`) compiles them to a self-contained HTML page instead of
running an Evidence build. Reasons: an Evidence build is a Node toolchain and a
per-tenant compile step in the delivery path, and the output still needs hosting
and signing; and a self-contained file is what a lender's PDF is printed from
and what survives being emailed. The section contract (nine columns, one row per
figure) is deliberately Evidence-shaped, so moving to it later is a renderer
swap, not a re-model.

**dbt Core is scaffolded, not the executor.** Spec section 2 commits to dbt Core
and section 17.5 leaves it open. The mart models are plain parameterised SQL run
by `fracture.marts.runner`, for one reason: every mart must bind the pack's
`system_time`, and a mart that reads `canon` without that parameter silently
double-counts restated rows. The runner refuses to execute SQL that reads `canon`
without a system-time filter (`assert_temporal_filter`), and runs assertions
after each model. dbt gives docs and lineage but does not give either of those,
and per-tenant target generation for database-per-tenant is its own project. The
models are written so `dbt run --vars '{system_time: ...}'` is a mechanical port
if per-tenant environment management stops being the reason not to.

**Postgres roles own the tenant schema, Terraform owns everything around it.**
The `tenant` Terraform module creates the database, the KMS key, the S3 prefix
and the secret paths; `fracture.control.provisioning` creates the four roles, the
schemas and the grants. Both read the same four role names, and
`test_terraform_and_python_agree_on_the_role_list` fails if they drift.

## Additions

These are not in the spec. Each closes a hole the spec's own guarantees imply.

**Source precedence (`fracture.canon.precedence`).** Spec section 7 has
`canon_<entity>` fanning in across sources but does not say who wins when two
sources disagree. "Whichever asset ran last" makes the number in the pack change
for reasons nobody can explain. Precedence is declared per entity; a
lower-authority source never overwrites, and the disagreement is written to
`recon.source_variance` as a finding. Orion and the Schwab file disagreeing about
an account's value is the flagship reconciliation finding, not a coin flip.

**Merge-on-supersede.** A partial source (the custodian file knows an account's
value and nothing about its household) must not null out columns it has no view
of. A null in an incoming record is absence of knowledge, not an assertion of
emptiness. Without this, running the custodian feed detaches every account from
its household and household AUM goes to zero.

**`source_id` on canonical tables.** Needed by precedence. The schema stays
source-agnostic; the column records which source produced this version.

**One-open-row indexes (`065_uniqueness.sql`).** The bitemporal model allows
several versions of a fact. It must never allow two *current* rows for the same
key and business-validity start, because duplicates double-count into every mart
above them without anything looking wrong.

**`field_names` on `source_fingerprint`.** Spec section 16 wants an alert when a
schema hash changes. Storing only the hash makes the alert "something changed",
which is not actionable. Storing the field map makes it "positions.billableValue
was removed".

**Firm-scoped incremental cursors.** A tenant holds several firms running the
same system. A cursor keyed only by source silently skips the second firm's
entire history on its first run. Found by a test, fixed, and now asserted.

**`run_id` on `recon.result`.** Without it, "how many checks passed" counts every
check ever evaluated, which grows on each rebuild and makes a pack's assurance
section irreproducible -- which quietly voids the byte-identical guarantee.

**`mart.cost_allocation_check`.** "Fully loaded" margin means no cost falls out.
An earlier version of the margin model computed a direct-cost total and never
used it, which flattered every margin figure and raised nothing. The check
asserts allocated cost equals booked cost per firm-quarter.

## Not built

Phase 2 and beyond in the spec's build sequence, and deliberately out of scope
for a phase 0/1 foundation:

- **PDF delivery.** The HTML pack prints, but there is no headless-Chrome step.
- **Tier 2 and tier 3 adapters.** Addepar, Black Diamond, Salesforce FSC,
  Pershing, Bill.com, Gusto/ADP; and the whole insurance track (AMS360, Applied
  Epic, EZLynx, commission-statement extraction). The insurance canonical tables
  (`policy_term`) exist; no adapter populates them. Spec section 15: do not start
  phase 3 before two paying tenants in wealth.
- **AI features themselves.** The boundary is built and enforced three ways
  (proposal table, database trigger, canonical writer); no model is called. The
  boundary is the part that needed to exist before anything calls one.
- **SOC 2 tooling.** `pgaudit` is not enabled here (it needs a server restart and
  a shared-preload change); `control.access_log` records human queries and the
  Terraform ships an object-locked audit bucket.
- **Signed-URL delivery service.** The reporting module creates the bucket and
  the policy; nothing mints URLs yet.

## Untested in this environment

- **Terraform.** No `terraform` binary was available, so the modules are
  unvalidated: not `fmt`, not `validate`, not `plan`. Treat them as reviewed
  drafts. The one thing that is tested is that their role list matches the
  Python.
- **Neon and RDS.** Everything ran against a local Postgres 16. The host choice
  (spec 17.1) is one variable and one output; nothing above it knows which.
- **S3, KMS and Secrets Manager.** The `LocalArtifactStore` mirrors the S3 key
  layout exactly and is what the tests exercise; `S3ArtifactStore` and
  `AWSSecretsManagerResolver` are written but never executed here.
