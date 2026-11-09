# Target-state architecture

- Client:
- Date:
- Horizon:

## Target

One database per tenant, four roles, and no path from one tenant's role to
another tenant's database. Raw retention follows the client's own policy, with
seven years as the starting point. Artifacts live in object storage; the
database can be rebuilt from them and is not itself the evidence.

Canonical rows are bitemporal. Business time is when the fact was true.
System time is when the platform learned it. A restatement closes a row and
opens a new one, which is what makes last quarter's pack reproducible after
this quarter's corrections.

The monthly pack is SQL in git, compiled per tenant, and pinned to a system
time. Reissuing that pack is byte-identical. A drill from a figure walks to
the canonical row, the raw payload, and the file hash.

Intelligent automation sits beside that path. A model may propose a field
mapping, an extraction or a narrative. A numeric canonical column accepts a
proposal only after a person other than the service account confirms it.

## Roadmap

| Phase | Outcome | Entry |
|---|---|---|
| 0 | This assessment: inventory, integration map, target, findings | Fixed fee |
| 1 | First sources loaded read-only, reconciliation green, one pack issued | Platform implementation |
| 2 | Add-on fold-in, priced from the coverage estimate | M&A integration |
| 3 | Monthly pack, drift acknowledged, architect on a retainer | Retainer |

## Decisions to settle with the client

- Where the tenant database runs. The platform keeps that choice behind one
  registry column. See `docs/OPEN_DECISIONS.md`.
- Whether the tenant is the acquirer. Splitting a tenant later is a migration;
  adding a sponsor above it later is not.
- There is no read API. A client that wants programmatic access gets a
  scheduled export into their own storage. An API that filters rows is the
  leak the database-per-tenant model exists to make impossible.

## Warehouse

The operating platform is Postgres. A separate warehouse evaluation was not
available to attach to this engagement. Do not add one in the roadmap unless
the client already runs one and the assessment measured it.
