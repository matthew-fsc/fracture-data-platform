# Reconciliation checks

These run on every refresh, from `src/fracture/recon/checks.py`. Tolerances
are constants in that module so a change to one is a diff, not a call-site
argument. `fracture recon <slug>` is the command. A breach raises; it does
not publish the pack.

| Check | Compares | Tolerance |
|---|---|---|
| `aum_total` | Canonical balances against the custodian control total, by firm and month-end | 0.0001 |
| `billed_vs_invoices` | Billed revenue against invoice totals | 0.0001 |
| `collected_vs_receipts` | Collected cash against receipts | 0.0001 |
| `consolidated_rollup` | A consolidated figure against the sum of its parts | 0.000001 |

`aum_total` is the diligence check. If the assets shown to a seller do not
reproduce from the custodian's own total, the variance is the finding.

Source drift that nobody has acknowledged stays visible (`unacknowledged_drift`).
Open disagreements between two sources that both wrote the same entity stay
visible (`open_source_variances`). A cutover does not proceed while either
list is unexplained.

Changing a tolerance to make a rehearsal pass is a failed rehearsal.
