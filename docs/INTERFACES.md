# Interfaces for Fracture OS

Fracture OS is the firm operating system. It lives in
`fracture-factory-orchestrator` and is not part of this monorepo. It is not a
merge candidate, and it is not safe to archive. OS calls the modules below. It
does not import past these entry points, and it does not copy them.

Discovery calls return data and do not need a tenant database. Execution calls
are the `fracture` command, which needs Postgres and a tenant.

| Module | Discovery | Execution |
|---|---|---|
| `data-profiling` | `fracture_profiling.profile_tree(path)` | `python -m fracture_profiling <path>` |
| `migration` | `fracture_migration.list_cards()`, `load_card(source_id)`, `reconciliation()` | `fracture recon <slug>`, `fracture migrate` |
| `reporting` | `fracture_reporting.list_sections()` | `fracture pack build <slug> --period-end YYYY-MM-DD` |
| `assessment` | `assessment.render_findings(engagement, findings)`, `assessment.templates()` | `fracture assess <systems> --pack wealth\|services --json` |
| `dbt-scaffold` | `fracture_dbt.project()` | `dbt build --project-dir dbt-scaffold` |
| `snowflake` | `fracture_snowflake.warehouse_decision()` | none — the decision is Postgres |
| `guardrailed-ai` | `fracture_guardrail.describe()` | `fracture_guardrail.record_proposal(...)` then `confirm` or `reject` |

Put the module directory on `PYTHONPATH` (and `src` as well, for the three
that call into the platform: migration, reporting, guardrailed-ai). From the
repo root:

```
PYTHONPATH=data-profiling python -m fracture_profiling extracts/
PYTHONPATH=migration:src python -m fracture_migration list
PYTHONPATH=reporting:src python -m fracture_reporting
PYTHONPATH=. python -m assessment engagement.yml findings.yml
PYTHONPATH=dbt-scaffold python -m fracture_dbt
PYTHONPATH=snowflake python -m fracture_snowflake
PYTHONPATH=guardrailed-ai:src python -m fracture_guardrail
```

Each command prints JSON to stdout. A usage error exits 2 and writes to stderr.
