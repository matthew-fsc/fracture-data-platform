# How the pieces fit

## The path a number takes

    source system
      └─ adapter.fingerprint()      schema hash recorded; a removed field halts here
           └─ adapter.extract()     batches, never past cursor semantics
                └─ artifact to S3   written first, SHA-256 recorded, then loaded
                     └─ raw.<source>__<stream>       append-only, one row per record
                          └─ adapter.map()           pure; raw payload to canonical
                               └─ canon.<entity>     bitemporal, precedence-arbitrated
                                    └─ mart.<metric> pinned to a system time
                                         └─ pack.figure   pinned to a pack_run_id
                                              └─ the rendered pack

And back down again, which is the part that matters:

    pack.figure.drill_query
      └─ lineage.mart_edge   → canonical rows
           └─ lineage.edge   → raw rows
                └─ raw._payload + _artifact_uri + sha-256 → the file we were sent

## Why the layers are separate

**Artifact before database.** The evidence trail lives in object storage, not in
Postgres, because if a tenant disputes a number the answer is the file you
received and its hash — and a database you rebuilt is not evidence. The order is
not negotiable: artifact written, hash recorded, then loaded. A row in `raw` whose
artifact is missing is a row you cannot defend.

**Raw is append-only, and that is a grant.** The `loader` role has `INSERT` and
`SELECT` on `raw` and nothing else, in no schema. It cannot create tables there
either: raw DDL is a migration run by `owner`, because an adapter that renames a
stream would otherwise start filling a brand new empty table while the old one
went stale, and every downstream count would look plausible.

**Canon is bitemporal, and nothing is updated in place.** Business time
(`valid_from`/`valid_to`) is when the fact was true; system time
(`recorded_at`/`superseded_at`) is when we learned it. A restatement closes the
old row and opens a new one. That is what makes a March pack and a June
restatement both reproducible, and the delta between them a report you can sell.

**Marts pin a system time.** Every mart model binds `%(system_time)s`, and the
runner refuses to execute SQL that reads `canon` without it. A mart that reads
superseded rows double-counts a restatement and nothing errors.

**Packs pin everything.** `pack_run.system_time` freezes the read;
`pack_run.content_hash` is SHA-256 over the canonically-ordered figures. Reissue
at the same instant and the hash matches; reissue at a new one and the delta is
the restatement report.

## Fan-in, and who wins

Two sources will report the same fact differently. Orion and the Schwab file will
not agree on every account's value; the CRM and the custodian will not agree on
which advisor owns a household. Without a rule, the canonical value depends on
asset execution order, and the number in the pack changes for reasons nobody can
explain.

`fracture.canon.precedence` declares, per entity, which source is the record. The
custodian is the record of AUM. The CRM is the record for people. The billing
system is the record of what was billed. The hand-entered fee schedule is the
record of what *should* have been billed. A lower-authority source never
overwrites; the disagreement is written to `recon.source_variance` and becomes a
finding — which is what the engagement is sold on.

A higher-authority source that wins still raises the variance. Recording it only
when the loser happens to arrive second would make the finding depend on
execution order, which is the behaviour being designed out.

Superseding merges rather than replaces. The custodian file knows an account's
value and nothing about its household; a null from it is absence of knowledge,
not an assertion of emptiness. Letting those nulls through would detach every
account from its household the moment the custodian feed ran.

## Tenancy

Database per tenant. Four roles per database:

| Role | Has |
|---|---|
| `t_<slug>_owner` | DDL, and ownership of every object. Migrations only. |
| `t_<slug>_loader` | `INSERT`, `SELECT` on `raw` and `lineage`. No update, no delete, no create. |
| `t_<slug>_transform` | `SELECT` on `raw`; full DML plus `CREATE` on `stg`/`canon`/`mart`/`pack`/`lineage`/`ai`/`recon`. |
| `t_<slug>_reader` | `SELECT` on `mart`, `pack`, `lineage`. Nothing else. |

`PUBLIC` connect is revoked, so a role from one tenant cannot reach another
tenant's database even knowing its name. Connection strings are assembled at call
time from the registry plus a secret lookup, never stored in code or orchestrator
config. `fracture.core.db.tenant_connection` raises if a second, different tenant
is opened while one is held.

The cost of this model is that migrations run N times. `fracture.control.migrations`
fans out over the registry, attempts every tenant even after one fails — a
partial fan-out that stops at the first error leaves the estate in an unknown
state, which is worse than a known-bad one — and raises unless a stated success
threshold is met.

## The synthetic estate

`fracture.synth` generates a three-firm acquirer with deliberately broken
billing: households never invoiced, invoices raised below the schedule, invoices
never collected, a custodian who disagrees with the portfolio system, rep codes
that do not match across systems, SLA breaches, and non-billable accounts that
got billed anyway. Every defect is a rate with a guaranteed minimum, and the
planted set is recorded in the estate manifest — so a test can assert the
platform found exactly what was planted, rather than that it found *something*.

The generator computes expected fees in Python; the mart recomputes them in SQL
from the canonical schedule. Two independent implementations of the same rules,
reconciled by a test. If they ever diverge, the unbilled figure is fiction, and
nothing else in the system would notice.
