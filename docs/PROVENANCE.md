# Provenance

Recorded while turning this repository into the delivery-accelerator monorepo.
Source commits below are the commits that were actually read. A repository
that returned HTTP 404 was not read, was not copied, and is not safe to archive.

## This repository

`matthew-fsc/fracture-data-platform`

| Branch | Tip | What was there |
|---|---|---|
| `main` | `d67c9a3` | README only. |
| `claude/platform-infrastructure-setup-ce3e3k` | `07ace5c0346568692c5fac4d25fb56e6209c89ce` | The consolidator platform: adapters, canonical model, marts, reconciliation, pack compiler, AI boundary, assurance gate, Terraform modules, and the test suite. |

The feature branch fast-forwarded onto `07ace5c`, so that history is intact.
Nothing on the branch was dropped. Local Postgres passwords in `docker-compose.yml`
and the CI workflow are the documented development defaults, not client credentials.
Synthetic adapter fixtures are synthetic. No client extract was in the tree.

The platform code stays where the tests import it (`src/fracture/`,
`reporting/packs/`, `infra/terraform/`). The new module directories are the
engagement surface over that code. They are not a second copy of it.

## Designated source repositories

Each was requested as `https://github.com/matthew-fsc/<name>`. Both an
unauthenticated clone and the credential that can read this repository received
HTTP 404. GitHub returns 404 for a missing repository and for a private
repository the caller cannot see. A GitHub notification in the workspace names
`fracture-accelerator-factory` and commit `c5ed5db` ("Intake Processor"), which
is enough to say that repository exists and this workspace still cannot read it.

No subtree was added. No file was copied. No source commit SHA can be recorded,
because no commit was fetched.

| Repository | Kept | Dropped | Why |
|---|---|---|---|
| `fracture-systems-accelerator` | Nothing | Everything, unread | HTTP 404. Assessment templates in `assessment/` were written here from this repo's adapter and assurance model. They are not that repository. |
| `fracture-accelerator-factory` | Nothing | Everything, unread | HTTP 404. Notification shows commit `c5ed5db` exists. Contents unknown, so not placed in any module. |
| `fracture-factory-orchestrator` | Nothing | Everything, unread | HTTP 404. Contents unknown. |
| `fracture-developer-portal` | Nothing | Everything, unread | HTTP 404. Contents unknown. |
| `fracturesystems-data-profiling` | Nothing | Everything, unread | HTTP 404. `data-profiling/` is a new profiler written in this repo. It is not that repository. |
| `fracturesystems-dbt-scaffold` | Nothing | Everything, unread | HTTP 404. `dbt-scaffold/` follows the canonical grain already in this repo at `07ace5c`. It is not that repository. |
| `fracturesystems-snowflake-eval` | Nothing | Everything, unread | HTTP 404. `snowflake/` records the Postgres decision already in `docs/spec-v1.md`. No benchmark was invented. |
| `fracture-model-training` | Nothing | Everything, unread | HTTP 404. `rd/` was not created. |
| `fracture-market-topology` | Nothing | Everything, unread | HTTP 404. `rd/` was not created. |

None of these nine are fully merged. None are safe to archive.

## Other repositories that were visible

Listed so a later pass does not mistake them for the sources above.

| Repository | Commit read | Disposition |
|---|---|---|
| `fracture-synth-data-pipeline` | `9894621381fae26d8d3c3fbab092e56747110b9a` | Not a designated source. Not copied. The tree contains `docs/CREDENTIALS.md`, which was not opened and was not imported. |
| `fracturesystems` | `cf197be363d40902b9141b7e6a2d81c2fabbe9d3` | The public site. Out of scope for this monorepo. Not copied. |
| `exitblueprint`, `exitblueprint-mvp`, `exitblueprint-knowledgebase`, `fracture-prediligence-platform` | Not read | Out of scope. Not cloned. |

## What the new modules are allowed to claim

- `migration/` field maps are a reading of `src/fracture/adapters/sources/` at `07ace5c`. The executable mapping remains the adapter.
- `reporting/` and `guardrailed-ai/` point at code that was already in this repository.
- `assessment/`, `data-profiling/` and `dbt-scaffold/` contain work authored in this change because the designated repositories could not be read. They must not be described as a merge of those repositories.
