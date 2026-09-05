# Fracture Systems Consolidator Platform

One codebase serving four commercial motions: systems and data diligence,
platform operating model, add-on fold-in, and the operating retainer. The
diligence motion is not a separate product — it is this pipeline run against a
throwaway tenant, and its output *is* the fold-in cost estimate.

Built to `docs/spec-v1.md`. Every departure from that specification is recorded
in [`docs/DEVIATIONS.md`](docs/DEVIATIONS.md), including the parts that are
scaffolded but untested in this environment.

## See it work

Postgres 16 on `localhost:5432`, then:

    make demo

That generates a three-firm synthetic estate with deliberately broken billing,
provisions a tenant, loads every source through its adapter, maps to a bitemporal
canonical model with row-grain lineage, builds the marts, reconciles against each
source system's own reported totals, pins a pack, verifies it reissues
byte-identically, checks every figure opens to raw records, and renders the pack
to `out/pack.html`.

    make test          # 252 tests
    make demo-small    # the same path in about 4 seconds

It renders two things. `out/pack.html` is the board pack: pinned to a system
time, byte-identical on reissue, the artefact a client or a lender receives.
`out/dashboards.html` is the operating console: six departmental views of
current state, with every measure normalised so firms of different size compare
directly. Definitions are in [`docs/KPIS.md`](docs/KPIS.md).

## Shape

    src/fracture/
      core/           config, DSN assembly, hashing, redaction, bitemporal time
      control/        tenant registry, provisioning, migration fan-out
      adapters/       the SourceAdapter contract, six adapters, fold-in estimator
      ingest/         artifact store, append-only raw layer, row-grain lineage
      canon/          canonical DDL, bitemporal writer, source precedence
      marts/          SQL models and the runner that asserts they make sense
      recon/          checks that run every refresh, with stated tolerances
      ai/             the AI boundary and its three enforcement points
      pack/           pinning, reproducibility, drill-through, pack and dashboards
      synth/          the synthetic estate generator
      orchestration/  Dagster assets, tenant as a dynamic partition
    reporting/packs/  pack definitions: SQL in git, diffable
    infra/terraform/  network, control-plane, tenant, compute, reporting
    tests/            252 tests, every adapter through the same five gates

## The four claims, and where each is tested

The specification makes four promises that are easy to state and easy to let rot.
Each has a test that fails loudly when it stops being true.

**Isolation is enforced at the database, not in application code.**
Database per tenant, four roles per database, `PUBLIC` connect revoked. A
tenant's role cannot connect to another tenant's database;
`postgres_fdw` and `dblink` are installed nowhere. The `loader` role has no
`UPDATE` and no `DELETE` on `raw`, and cannot create tables there — raw DDL is a
migration. → `tests/test_tenancy.py`

**Every figure opens to the records behind it.**
Row-grain lineage is an explicit table, written by the mapping layer and by the
mart models, not a metadata by-product. A pack figure walks to canonical rows, to
raw payloads, to the S3 object and its SHA-256. A canonical row with no lineage
is rejected, and the database is rebuildable from object storage alone.
→ `tests/test_lineage_and_evidence.py`

**A pack reissued at the same system time is byte-identical.**
System time is pinned for the run; the content hash is SHA-256 over the
canonically-ordered figures. Reissuing at a new system time produces the
restatement, and the delta between the two is itself a report — while the earlier
pack still reproduces. This is the claim most likely to rot silently: one `now()`
in a mart, one counter that accumulates across runs, and it is quietly false.
Both of those happened during the build and are now regression tests.
→ `tests/test_pack.py`

**AI never computes a number with financial consequence.**
Enforced three ways that all have to agree: nothing enters except as a row in
`ai.proposal`; a database trigger refuses a numeric column populated from an
unconfirmed proposal; and the canonical writer refuses the same thing earlier
with a better error. A service account cannot confirm its own proposal.
→ `tests/test_ai_boundary.py`

## Adapters

An adapter is not shippable without a checked-in discovery snapshot, three
extraction fixtures (empty, typical, pathological), golden canonical output for
each, a redaction test, and a static check proving the module contains nothing
that could write to a source system. That suite is parametrised over the
registry, so an adapter added without fixtures fails it.

| Source | Tier | Delivery | Populates |
|---|---|---|---|
| `orion` | 1 | api | households, accounts, balances, producers, book, service events |
| `redtail` | 1 | api | parties, households, producers, book, tickets |
| `schwab_custodian` | 1 | file | accounts, balances, control totals |
| `qbo` | 1 | api | invoices, receipts, costs, time |
| `manual_fee_schedule` | 1 | manual | fee schedules, tiers, assignments |
| `generic_csv` | 1 | file | configurable, by a YAML mapping |

`Capabilities` is machine-readable and drives the diligence deliverable directly:

    fracture estimate orion redtail qbo addepar

returns which canonical entities you can populate, at what completeness, what is
manual, and what an unsupported system costs — priced, not omitted.

## Comparing firms

The platform firm bills 4.8x what the smallest add-on bills, so no measure in
the dashboards is an absolute amount. Everything is a rate, a per-unit figure or
basis points on AUM. Three yields carry most of the weight:

| Yield | Definition | Tells you |
|---|---|---|
| Schedule | expected fees over AUM | how the book is priced |
| Realised | invoiced over AUM | what was billed |
| Collected | cash over AUM | what was kept |

The gaps between them separate billing execution from credit control, which
have different owners and different fixes. On the demo estate the smallest firm
is the *most* expensively priced book on the platform and still lands last on
realised yield, because it invoices 78% of what it is owed. A single-yield
dashboard reads that as "cheap" and sends someone to reprice it, which is the
wrong action. Full definitions and the six departmental views are in
[`docs/KPIS.md`](docs/KPIS.md).

## Operating it

    fracture control init
    fracture tenant register --slug acme --legal-name "Acme Partners LP" \
      --motion operating --provision
    fracture migrate                          # fan out, fails below threshold
    fracture recon acme
    fracture pack build acme --period-end 2026-06-30
    fracture pack verify acme <pack_run_id>   # rebuild and compare hashes
    fracture dashboards acme                  # the six departmental views
    fracture drill acme 'mart.unbilled|MWP|MWP-HH-00042'
    fracture tenant export acme               # the contractual full export

Diligence tenants use the same path with `--motion diligence --archive-after`,
and `fracture tenant promote` converts one to operating in place — the same
database, the same raw artifacts, the same lineage.

## Requirements

Python 3.11+, PostgreSQL 16+. `pip install -e ".[dev,orchestration]"`.
