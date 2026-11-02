# Domain packs

A pack is one domain's canonical model, its mart models, its reconciliation
assertions and its departmental views. The chassis — tenancy, provisioning, the
artifact store, the bitemporal writer, row-grain lineage, reconciliation, the
pack compiler and the assurance engine — knows nothing about any of it.

The split exists for one commercial reason. The margin on a fixed-fee
engagement is whatever was not rebuilt, and the only asset that accrues across
engagements is the chassis. A per-client fork is cheap in week one and stops
the chassis accruing from week two.

## What belongs where

| Layer | Holds | Lives in | Changes per engagement |
| --- | --- | --- | --- |
| Chassis | Tenancy, provisioning, artifact store, bitemporal writer, lineage, source-precedence arbitration, reconciliation runner, pack compiler, assurance engine, chart marks, console shell, orchestration | `core/` `control/` `ingest/` `adapters/base.py` `adapters/registry.py` `adapters/staticcheck.py` `canon/writer.py` `canon/precedence.py` `canon/ddl/{000,010,040,050,060,070}` `recon/` `pack/` `assurance/` `orchestration/` | Never |
| Pack | Canonical entity and measure DDL, uniqueness backstop, mart models, assertions, entity registry, departmental views, synthetic estate | `packs/<name>/` | Only when the domain model is wrong |
| Engagement | Tenant registration, credentials, adapter configuration, the client's data | Control-plane rows and the tenant database | Every time |

Adapters are the deliberate exception: they live in `adapters/sources/` across
all packs and declare which domain they serve through the `vertical` field on
their capability manifest, because a general ledger adapter is worth writing
once and roughly a third serve more than one domain.

## Enforcement

Three of the nine assurance policies exist to keep this seam from rotting back
into one layer:

- **P2** — no chassis module may import a pack. Resolution goes through
  `fracture.packs.get_pack()`, which imports by name through `importlib`.
- **P3** — no pack may import another pack. Two packs sharing a table is one
  badly named pack plus a migration that cannot be sequenced per tenant.
- **P4** — a mart that reads `canon` must bind the pack's system time.

A fourth guard is in the tests rather than the gate:
`test_every_pack_entity_has_a_canonical_table` fails when a pack declares an
entity with no table behind it, which would otherwise surface at the first load
of a source that emits it.

## What a pack declares

`Pack` in `packs/__init__.py`. Beyond paths and titles, four things are
load-bearing:

**`entities`** — one `Entity` per canonical entity: the columns that make two
rows the same fact, whether the fact has business-time validity, which of its
columns carry figures with financial consequence, and which sources outrank
which when two disagree. The numeric set is not documentation: the AI boundary
refuses an unconfirmed proposal in a numeric column, so a column left off the
list is a column a model could write. Hours are numeric in the services pack
for exactly that reason — an hour is multiplied by a charge rate and invoiced.

**`required_entities`** — what an engagement must populate for the marts to
work, which is narrower than what the model can hold. The assessment prices
against this list: an entity on it that no adapter covers is quoted as manual
work rather than omitted, and the omitted line is the one that blows the
schedule.

**`firm_roles`** — the role vocabulary and how many of each a tenant may hold.
Seeded into `control.pack_firm_role` and enforced by a trigger on
`tenant_firm`, so the rule holds against a second code path. The wealth pack
allows exactly one `platform` firm; the services pack exactly one `practice`.

**`assertions`** and **`non_empty`** — claims about the domain, checked after
every mart run. They travel with the pack because running the wealth assertions
over a services tenant would fail on tables that correctly do not exist, and
the failure would read like a chassis bug.

## The packs shipped

### `wealth` — Wealth management

Households, accounts, balances, fee schedules, invoices, receipts, producers
and the service events around them. 21 entities, 14 marts, 23 assertions, six
views.

Measures the estate on **three yields**: what the fee schedules entitle the
book to, what was invoiced, and what arrived. The first is the one nobody else
can compute, because it is recomputed from the canonical schedule rather than
read back out of the billing system, where entitlement and invoiced are the
same number by construction.

### `services` — Professional services

Clients, engagements, matters, timekeepers, rate cards, time entries,
invoices, receipts and costs. 15 entities, 10 marts, 18 assertions, six views.

Measures a practice on **five stages**, because professional services carries
inventory that wealth does not and prices work separately from recording it:

```
recorded at card rate      every recorded hour, priced
  less write-off at entry  scope, goodwill, training, admin, rework
= billable at card rate
  less pricing             client discount, or a fixed fee below time value
= entitled                 what the engagement may bill
  less realisation gap     what the bill actually went out at
= billed
  less uncollected         what has not arrived
= collected
```

Four gaps, four owners. Write-off at entry belongs to whoever approved the work
and happens in the timesheet, which is why most practices never see it. Pricing
belongs to whoever agreed the fee. Realisation belongs to the engagement
partner. Collection belongs to finance. A single realisation percentage
collapses the middle two and sends the wrong person to fix it.

Lockup is split the same way, into work done and not yet invoiced against
invoiced and not yet collected, because reported as one number each team can
point at the other and it never improves.

The pack loads through the config-driven CSV adapter with a mapping config and
no bespoke module, which is what that adapter exists for. A whole vertical
onboarded without new Python is a claim, so it is tested rather than asserted:
`tests/test_packs_and_services.py`.

## Adding a third pack

1. `packs/<name>/ddl/` — entity and measure DDL numbered 020–039, plus a
   uniqueness backstop at 065. Follow the chassis bitemporal convention
   exactly: `valid_from date not null default '-infinity'` and `valid_to date`
   nullable, where null means the period is still open. The canonical writer
   treats a null `valid_to` as meaningful; declaring it `not null` contradicts
   the chassis and the first load fails.
2. `packs/<name>/entities.py` — the entity registry.
3. `packs/<name>/marts/` — models numbered in run order, each binding
   `%(system_time)s` where it reads `canon`.
4. `packs/<name>/assertions.py` — what must be true, written as "find the
   problem" so a failure names the offending row rather than the rule.
5. `packs/<name>/dashboard.py` — `DEPARTMENTS`, `VIEWS`, `chrome()` and
   `collect()`. Import chart marks from `pack/charts.py`; never reimplement one.
6. `packs/<name>/__init__.py` — the manifest, and `register()`.
7. Add the name to `_load()` in `packs/__init__.py`.

Then `fracture packs` should list it and `fracture assure` should still pass.

**Do not build a pack speculatively.** The seam is cheap to move while one
implementation sits behind it and expensive after three. A pack is worth
building when an engagement in that domain is sold, not when one is imagined.
