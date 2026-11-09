# dbt scaffold

Service line: platform implementation. The canonical financial-services model
an engagement starts from: clients, households, accounts, producers, revenue
and receivables, with tests on the grain.

## Entry point

Fracture OS calls `fracture_dbt.project()`, which returns the project directory,
the profile name, the model names and the `dbt build` argv. It does not connect
to a warehouse.

```
PYTHONPATH=dbt-scaffold python -m fracture_dbt
```

This project was written in this repository, following the canonical tables
already at `07ace5c`. `fracturesystems-dbt-scaffold` could not be read. This
directory is not a merge of that repository, and that repository is not safe
to archive.

The model the consolidator executes is not dbt. Marts are parameterised SQL
run by `src/fracture/marts`, because every mart has to be pinned to the pack's
system time. See `docs/DEVIATIONS.md`. These models are the engagement
starting point when a client warehouse is the deliverable. They select current
rows (`superseded_at is null`). They do not subtract receipts from invoices:
the open-balance arithmetic lives in the platform marts, where it is
reconciled.

| Model | Grain | Source table |
|---|---|---|
| `client` | firm, client | services `canon.client` |
| `household` | firm, household | wealth `canon.household` |
| `account` | firm, account | wealth `canon.account` |
| `producer` | firm, producer | wealth `canon.producer` |
| `revenue` | firm, revenue event | wealth `canon.revenue_event` |
| `receivable` | firm, open invoice | wealth `canon.invoice` where status is open or partial |

`tests/` fails when a grain is duplicated. `models/canonical/schema.yml`
requires the key columns. A local dbt run needs the `transform` extra
(`dbt-core`, `dbt-postgres`) and a profile that is not committed here.
