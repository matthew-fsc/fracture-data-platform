# Cutover runbook

The sequence uses the commands the platform has. Credentials stay in the
secret store. The runbook does not contain them.

## 1. Diligence tenant

Load the source read-only into a tenant created with `--motion diligence`.
The same database becomes the operating tenant later; there is no second load
at cutover.

```
fracture control init
fracture tenant register --slug <slug> --legal-name "<legal name>" \
  --motion diligence --provision
```

Confirm the adapter list with `fracture adapters` and the priced gaps with
`fracture estimate <source ids>`.

## 2. Freeze and final extract

Agree a freeze time with the source-system owners. Take the final CRM,
custodian, portfolio and ledger extracts at that time. The artifact is written
and hashed before any row is loaded. A row whose file is missing cannot be
defended later.

## 3. Map

Review `mappings/` against the extract the client actually sent. A column the
card does not name is unmapped on purpose until someone adds it to the
adapter's mapping and regenerates the fixtures (`make fixtures`). A drifted
golden file fails CI.

## 4. Reconcile

`fracture recon <slug>` must be green at the tolerances in `reconciliation.md`.

- `aum_total` matches the custodian control total.
- `billed_vs_invoices` matches the ledger.
- `collected_vs_receipts` matches cash.
- No unacknowledged schema drift.
- Invoice headers that do not equal their lines have been rejected, not averaged.

## 5. Issue and verify the pack

```
fracture pack build <slug> --period-end <date>
fracture pack verify <slug> <pack_run_id>
```

Verify rebuilds at the pinned system time and compares hashes. Drill one
revenue figure and one balance figure to the raw file. A figure that does not
open is not in the pack that goes to the client.

## 6. Promote

`fracture tenant promote` converts the diligence tenant to operating in place:
same database, same artifacts, same lineage. Take the contractual export
(`fracture tenant export <slug>`) before the window, and keep the verified
pack. That pack still reproduces after later restatements, because those open
new system-time rows.

## 7. Schema fan-out

`fracture migrate` applies tenant DDL across the registry. The run is green
only when every targeted tenant succeeds. A tenant that was unreachable is a
failed tenant, not a skipped success. Do not start the window on a partial
fan-out.

## 8. Window

During the window the source systems stay read-only to the platform. Users
move to the operating pack and the dashboards (`fracture dashboards <slug>`).
The first operating refresh runs the same reconciliation. A new breach stops
the refresh; it does not publish a corrected pack quietly.

## 9. After

Acknowledge any drift the first operating week produces, in the control
plane, with a person named on the acknowledgement. Unacknowledged drift stays
on the list from step 4.
