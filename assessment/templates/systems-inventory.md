# Systems inventory

- Client:
- Pack: wealth | services
- Date:
- Prepared by:

List every system that holds clients, accounts, producers, revenue or
receivables, including the spreadsheet somebody maintains because the system
of record does not. A system with no adapter is still a row.

| System | Role | Delivery | Adapter, if one exists | Extract date | Owner |
|---|---|---|---|---|---|
| | crm / custodian / portfolio / ledger / manual | api / file / manual | `redtail`, `schwab_custodian`, `orion`, `qbo`, `manual_fee_schedule`, `generic_csv`, or none | | |

Adapters already in the platform, and what they populate:

| Adapter | Populates |
|---|---|
| `redtail` | parties, households, producers, book assignment, service events |
| `orion` | households, accounts, balances, producers, book, service events |
| `schwab_custodian` | accounts, balances, and the custodian's own control total |
| `qbo` | invoices, receipts, costs, time |
| `manual_fee_schedule` | fee schedules, tiers, assignments |
| `generic_csv` | whatever its mapping file names |

## Canonical coverage

Run `fracture assess <source ids> --pack wealth` (or `--pack services`) and
paste the result. Then fill the entities the command does not know about.
Wealth entities the estimator expects: party, household, household_member,
producer, book_assignment, account, balance_snapshot, fee_schedule, fee_tier,
schedule_assignment, revenue_event, invoice, invoice_line, cash_receipt,
receipt_application, cost_line, fte_allocation, service_event.

| Entity | Best source | Status (covered / partial / manual) | What is missing |
|---|---|---|---|
| | | | |

An unsupported system is priced, not dropped. The estimate is the diligence
deliverable.
