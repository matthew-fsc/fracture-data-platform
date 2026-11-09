# Migration

Service line: CRM and custodian migration, and the fold-in of an add-on after
a roll-up.

The executable mapping is the adapter under `src/fracture/adapters/sources/`.
The YAML in `mappings/` is the card an engagement lead reviews with the
client. It was read off those adapters at `07ace5c`. If a card and an adapter
disagree, the adapter is the one that runs, and the card is wrong.

| Card | System | Adapter |
|---|---|---|
| `mappings/crm-redtail.yml` | CRM | `redtail.py` |
| `mappings/custodian-schwab.yml` | Custodian | `schwab_custodian.py` |
| `mappings/portfolio-orion.yml` | Portfolio accounting | `orion.py` |
| `mappings/ledger-qbo.yml` | Ledger | `qbo.py` |

A file-drop source that is none of these is a `generic_csv` mapping, same
shape as `tests/fixtures/adapters/generic_csv/mapping.yml`. A new mapping is a
YAML file, not a new Python module.

Reconciliation checks and their tolerances are in `reconciliation.md`. The
cutover sequence, which is the commands the platform actually has, is in
`cutover-runbook.md`.

Tax identifiers cross into canon as the last four characters only. A mapping
that copies a full identifier is a defect in the adapter, and the assurance
gate treats a full identifier in the tree as a blocking finding.
