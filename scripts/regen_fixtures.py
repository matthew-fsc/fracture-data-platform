#!/usr/bin/env python3
"""Build the checked-in adapter fixtures and their golden canonical output.

Run after any deliberate change to an adapter's mapping, and review the diff:
the golden files are the record of what the platform believes each source means,
and a change to one is a change to every number downstream.

    python scripts/regen_fixtures.py [--only orion]

The typical fixtures are small and readable. The pathological ones are
hand-authored here rather than sampled, because the point is to carry the cases
a generator would never produce: nulls where the schema says not-null, unicode
and accented names, negative and parenthesised amounts, backdated records,
thousands separators, and a closed account with a value.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FIXTURES = ROOT / "tests" / "fixtures" / "adapters"


def jdump(path: Path, key: str, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({key: records}, indent=1, sort_keys=True))


def cdump(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


# -- orion -------------------------------------------------------------------


def orion(base: Path) -> None:
    typical = base / "typical"
    jdump(typical / "orion_households.json", "households", [
        {"householdId": "H-001", "name": "The Okafor Household", "tier": "core",
         "createdOn": "2019-04-02", "updatedAt": "2026-01-05"},
        {"householdId": "H-002", "name": "The Lindqvist Household", "tier": "private_client",
         "createdOn": "2016-11-20", "updatedAt": "2026-01-05"},
    ])
    jdump(typical / "orion_accounts.json", "accounts", [
        {"accountId": "A-001", "householdId": "H-001", "primaryContactId": "P-001",
         "primaryContactName": "Amara Okafor", "registrationType": "joint",
         "custodian": "schwab", "openedOn": "2019-04-10", "closedOn": None,
         "billable": True, "country": "US", "updatedAt": "2026-01-05"},
        {"accountId": "A-002", "householdId": "H-002", "primaryContactId": "P-002",
         "primaryContactName": "Devin Lindqvist", "registrationType": "ira",
         "custodian": "schwab", "openedOn": "2016-12-01", "closedOn": None,
         "billable": True, "country": "US", "updatedAt": "2026-01-05"},
    ])
    jdump(typical / "orion_positions.json", "positions", [
        {"accountId": "A-001", "asOfDate": "2026-03-31", "marketValue": "1450000.00",
         "cashValue": "42000.00", "billableValue": "1450000.00", "currency": "USD"},
        {"accountId": "A-002", "asOfDate": "2026-03-31", "marketValue": "3820000.00",
         "cashValue": "115000.00", "billableValue": "3820000.00", "currency": "USD"},
    ])
    jdump(typical / "orion_representatives.json", "representatives", [
        {"repId": "ADV-001", "name": "Priya Marchetti", "repCode": "PM001",
         "hireDate": "2014-06-01", "termDate": None,
         "assignments": [
             {"householdId": "H-001", "splitPct": 100, "effectiveFrom": "2019-04-02", "effectiveTo": None},
             {"householdId": "H-002", "splitPct": 60, "effectiveFrom": "2016-11-20", "effectiveTo": None},
         ]},
    ])
    jdump(typical / "orion_service_requests.json", "service_requests", [
        {"requestId": "SVC-001", "requestType": "transfer", "householdId": "H-001",
         "accountId": "A-001", "assignedRepId": "ADV-001",
         "openedAt": "2026-02-10T14:05:00+00:00", "closedAt": "2026-02-12T09:30:00+00:00",
         "slaHours": 120},
    ])

    path = base / "pathological"
    jdump(path / "orion_households.json", "households", [
        # Unicode, an apostrophe, and a household created before the epoch of
        # the firm's own records.
        {"householdId": "H-Ü01", "name": "Ménage Dubois-O'Brien", "tier": None,
         "createdOn": "1994-01-03", "updatedAt": "2026-01-05"},
        {"householdId": "H-002", "name": "山田ホールディングス", "tier": "institutional",
         "createdOn": "2020-02-29", "updatedAt": "2026-01-05"},
    ])
    jdump(path / "orion_accounts.json", "accounts", [
        # A closed account, a null household, a non-billable registration, and a
        # tax identifier that must never reach canon in full.
        {"accountId": "A-Ü01", "householdId": "H-Ü01", "primaryContactId": "P-Ü01",
         "primaryContactName": "Émile Dubois-O'Brien", "registrationType": "trust",
         "custodian": "schwab", "openedOn": "1994-01-10", "closedOn": "2026-02-28",
         "billable": False, "country": "FR", "taxId": "123-45-6789",
         "updatedAt": "2026-01-05"},
        {"accountId": "A-003", "householdId": None, "primaryContactId": None,
         "primaryContactName": None, "registrationType": None, "custodian": "fidelity",
         "openedOn": "2025-12-31", "closedOn": None, "billable": True,
         "country": None, "updatedAt": "2026-01-05"},
    ])
    jdump(path / "orion_positions.json", "positions", [
        # A negative value (a margin account), a zero, and a backdated snapshot.
        {"accountId": "A-Ü01", "asOfDate": "2026-03-31", "marketValue": "-18500.25",
         "cashValue": "0", "billableValue": "0.00", "currency": "EUR"},
        {"accountId": "A-003", "asOfDate": "2019-06-30", "marketValue": "0.00",
         "cashValue": None, "billableValue": None, "currency": "USD"},
    ])
    jdump(path / "orion_representatives.json", "representatives", [
        # A departed advisor whose assignments have an end date.
        {"repId": "ADV-002", "name": "Kwame Osei", "repCode": "KO002",
         "hireDate": "2008-03-17", "termDate": "2025-11-30",
         "assignments": [
             {"householdId": "H-Ü01", "splitPct": 40,
              "effectiveFrom": "1994-01-03", "effectiveTo": "2025-11-30"},
         ]},
    ])
    jdump(path / "orion_service_requests.json", "service_requests", [
        # Still open, and past its target.
        {"requestId": "SVC-Ü02", "requestType": "onboarding", "householdId": "H-Ü01",
         "accountId": None, "assignedRepId": "ADV-002",
         "openedAt": "2025-10-01T08:00:00+00:00", "closedAt": None, "slaHours": 240},
    ])


# -- redtail -----------------------------------------------------------------


def redtail(base: Path) -> None:
    typical = base / "typical"
    jdump(typical / "redtail_contacts.json", "contacts", [
        {"contactId": "P-001", "firstName": "Amara", "lastName": "Okafor",
         "displayName": "Amara Okafor", "contactType": "individual", "familyId": "H-001",
         "familyRole": "head of household", "clientSince": "2019-04-02",
         "country": "US", "updatedAt": "2026-01-05"},
        {"contactId": "P-001S", "firstName": "Noel", "lastName": "Okafor",
         "displayName": "Noel Okafor", "contactType": "individual", "familyId": "H-001",
         "familyRole": "spouse", "clientSince": "2019-04-02",
         "country": "US", "updatedAt": "2026-01-05"},
    ])
    jdump(typical / "redtail_families.json", "families", [
        {"familyId": "H-001", "familyName": "The Okafor Household", "segment": "core",
         "servicingAdvisorId": "ADV-001", "splitPct": 100,
         "createdAt": "2019-04-02", "updatedAt": "2026-01-05"},
    ])
    jdump(typical / "redtail_users.json", "users", [
        {"userId": "ADV-001", "name": "Priya Marchetti", "role": "advisor",
         "repCode": "PM001", "hireDate": "2014-06-01", "termDate": None},
    ])
    jdump(typical / "redtail_activities.json", "activities", [
        {"activityId": "ACT-001", "familyId": "H-001", "ownerUserId": "ADV-001",
         "createdAt": "2026-03-02T10:00:00+00:00",
         "completedAt": "2026-03-02T15:30:00+00:00", "slaHours": 48},
    ])

    path = base / "pathological"
    jdump(path / "redtail_contacts.json", "contacts", [
        # An organisation, a trust, a full SSN in the source, and no family.
        {"contactId": "P-Ü01", "firstName": "Émile", "lastName": "Dubois-O'Brien",
         "displayName": "Émile Dubois-O'Brien", "contactType": "individual",
         "familyId": "H-Ü01", "familyRole": "TRUSTEE", "clientSince": "1994-01-03",
         "country": "FR", "ssn": "123-45-6789", "updatedAt": "2026-01-05"},
        {"contactId": "ORG-01", "firstName": None, "lastName": None,
         "displayName": "山田ホールディングス株式会社", "contactType": "business",
         "familyId": None, "familyRole": None, "clientSince": None,
         "country": "JP", "taxId": "98-7654321", "updatedAt": "2026-01-05"},
    ])
    jdump(path / "redtail_families.json", "families", [
        # No servicing advisor recorded: the CRM does not always have one.
        {"familyId": "H-Ü01", "familyName": "Ménage Dubois-O'Brien", "segment": None,
         "servicingAdvisorId": None, "splitPct": None,
         "createdAt": "1994-01-03", "updatedAt": "2026-01-05"},
    ])
    jdump(path / "redtail_users.json", "users", [
        # A rep code that does not match the custodian's, and a departed user.
        {"userId": "ADV-002", "name": "Kwame Osei", "role": "CSA",
         "repCode": "KO002X", "hireDate": "2008-03-17", "termDate": "2025-11-30"},
    ])
    jdump(path / "redtail_activities.json", "activities", [
        {"activityId": "ACT-Ü02", "familyId": "H-Ü01", "ownerUserId": "ADV-002",
         "createdAt": "2025-10-01T08:00:00+00:00", "completedAt": None, "slaHours": None},
    ])


# -- schwab_custodian --------------------------------------------------------


SCHWAB_ACCOUNT_FIELDS = ["AccountNumber", "RegistrationType", "OpenDate", "CloseDate", "RepCode"]
SCHWAB_POSITION_FIELDS = ["AccountNumber", "AsOfDate", "TotalValue", "CashBalance", "Currency"]
SCHWAB_CONTROL_FIELDS = ["AsOfDate", "TotalValue", "AccountCount"]


def schwab_custodian(base: Path) -> None:
    typical = base / "typical"
    cdump(typical / "schwab_accounts.csv", SCHWAB_ACCOUNT_FIELDS, [
        {"AccountNumber": "A-001", "RegistrationType": "joint", "OpenDate": "2019-04-10",
         "CloseDate": "", "RepCode": "PM001"},
        {"AccountNumber": "A-002", "RegistrationType": "ira", "OpenDate": "2016-12-01",
         "CloseDate": "", "RepCode": "PM001"},
    ])
    cdump(typical / "schwab_positions.csv", SCHWAB_POSITION_FIELDS, [
        {"AccountNumber": "A-001", "AsOfDate": "2026-03-31", "TotalValue": "1450000.00",
         "CashBalance": "42000.00", "Currency": "USD"},
        {"AccountNumber": "A-002", "AsOfDate": "2026-03-31", "TotalValue": "3820000.00",
         "CashBalance": "115000.00", "Currency": "USD"},
    ])
    cdump(typical / "schwab_control_totals.csv", SCHWAB_CONTROL_FIELDS, [
        {"AsOfDate": "2026-03-31", "TotalValue": "5270000.00", "AccountCount": "2"},
    ])

    path = base / "pathological"
    cdump(path / "schwab_accounts.csv", SCHWAB_ACCOUNT_FIELDS, [
        # A closed account and a US-format date, which custodian files still emit.
        {"AccountNumber": "A-Ü01", "RegistrationType": "trust", "OpenDate": "01/10/1994",
         "CloseDate": "02/28/2026", "RepCode": "KO002"},
        {"AccountNumber": "A-003", "RegistrationType": "", "OpenDate": "2025-12-31",
         "CloseDate": "", "RepCode": ""},
    ])
    cdump(path / "schwab_positions.csv", SCHWAB_POSITION_FIELDS, [
        # Accounting-negative in parentheses, thousands separators, a currency
        # symbol, and an empty cash balance.
        {"AccountNumber": "A-Ü01", "AsOfDate": "2026-03-31", "TotalValue": "(18,500.25)",
         "CashBalance": "", "Currency": "EUR"},
        {"AccountNumber": "A-003", "AsOfDate": "2019-06-30", "TotalValue": "$0.00",
         "CashBalance": "0", "Currency": ""},
    ])
    cdump(path / "schwab_control_totals.csv", SCHWAB_CONTROL_FIELDS, [
        {"AsOfDate": "2026-03-31", "TotalValue": "(18,500.25)", "AccountCount": "2"},
    ])


# -- qbo ---------------------------------------------------------------------


def qbo(base: Path) -> None:
    typical = base / "typical"
    jdump(typical / "qbo_invoices.json", "invoices", [
        {"invoiceId": "INV-001", "customerRef": "H-001", "txnDate": "2026-04-05",
         "dueDate": "2026-05-05", "periodStart": "2026-01-01", "periodEnd": "2026-03-31",
         "totalAmount": "3625.00", "currency": "USD", "status": "open",
         "lines": [{"description": "Advisory fee Q1 2026", "amount": "3625.00",
                    "basisAmount": "1450000.00", "accountRef": "A-001",
                    "revenueEventId": None}]},
    ])
    jdump(typical / "qbo_payments.json", "payments", [
        {"paymentId": "PMT-001", "customerRef": "H-001", "txnDate": "2026-04-28",
         "totalAmount": "3625.00", "paymentMethod": "ach",
         "applications": [{"invoiceId": "INV-001", "amount": "3625.00"}]},
    ])
    jdump(typical / "qbo_expenses.json", "expenses", [
        {"expenseId": "EXP-001", "txnDate": "2026-03-31", "category": "payroll",
         "vendorRef": None, "employeeRef": "ADV-001", "amount": "18400.00",
         "allocationBasis": "direct"},
    ])
    jdump(typical / "qbo_time_entries.json", "time_entries", [
        {"entryId": "TIME-001", "entryDate": "2026-03-31", "employeeRef": "ADV-001",
         "producerRef": "ADV-001", "customerRef": "H-001", "hours": "4.25",
         "hourlyCost": "142.00"},
    ])

    path = base / "pathological"
    jdump(path / "qbo_invoices.json", "invoices", [
        # A credit note (negative), multiple lines, and no period on the header.
        {"invoiceId": "INV-Ü02", "customerRef": "H-Ü01", "txnDate": "2026-04-05",
         "dueDate": None, "periodStart": None, "periodEnd": None,
         "totalAmount": "-1,250.50", "currency": "EUR", "status": "VOID",
         "lines": [
             {"description": "Fee crédit — überzahlung", "amount": "-2000.50",
              "basisAmount": None, "accountRef": "A-Ü01", "revenueEventId": None},
             {"description": "Reinstated portion", "amount": "750.00",
              "basisAmount": None, "accountRef": None, "revenueEventId": None},
         ]},
    ])
    jdump(path / "qbo_payments.json", "payments", [
        # A short payment applied across two invoices.
        {"paymentId": "PMT-Ü02", "customerRef": "H-Ü01", "txnDate": "2026-06-30",
         "totalAmount": "900.00", "paymentMethod": None,
         "applications": [
             {"invoiceId": "INV-Ü02", "amount": "400.00"},
             {"invoiceId": "INV-001", "amount": "500.00"},
         ]},
    ])
    jdump(path / "qbo_expenses.json", "expenses", [
        {"expenseId": "EXP-Ü02", "txnDate": "2026-03-31", "category": None,
         "vendorRef": "Fournisseur Générique", "employeeRef": None,
         "amount": "(3,200.00)", "allocationBasis": None, "periodEnd": "2026-03-31"},
    ])
    jdump(path / "qbo_time_entries.json", "time_entries", [
        {"entryId": "TIME-Ü02", "entryDate": "12/31/2025", "employeeRef": "ADV-002",
         "producerRef": None, "customerRef": None, "hours": "0.25",
         "hourlyCost": "95"},
    ])


# -- manual_fee_schedule -----------------------------------------------------


def manual_fee_schedule(base: Path) -> None:
    typical = base / "typical"
    jdump(typical / "fee_schedules.json", "schedules", [
        {"scheduleId": "SCHED-STD", "name": "Standard Tiered Advisory", "basis": "aum",
         "frequency": "quarterly", "calcMethod": "tiered", "billingTiming": "arrears",
         "valuationRule": "period_end", "effectiveFrom": "2018-01-01", "effectiveTo": None},
    ])
    jdump(typical / "fee_tiers.json", "tiers", [
        {"scheduleId": "SCHED-STD", "tierSeq": 1, "lowerBound": "0",
         "upperBound": "1000000", "annualRateBps": 110, "flatAmount": None},
        {"scheduleId": "SCHED-STD", "tierSeq": 2, "lowerBound": "1000000",
         "upperBound": None, "annualRateBps": 85, "flatAmount": None},
    ])
    jdump(typical / "fee_assignments.json", "assignments", [
        {"scheduleId": "SCHED-STD", "scopeType": "household", "scopeId": "H-001",
         "effectiveFrom": "2019-04-02", "effectiveTo": None},
    ])

    path = base / "pathological"
    jdump(path / "fee_schedules.json", "schedules", [
        # A schedule that ended, with mixed case and a flat retainer.
        {"scheduleId": "SCHED-LEGACY", "name": "Legacy 1.00% — repapering pending",
         "basis": "AUM", "frequency": "QUARTERLY", "calcMethod": "Blended",
         "billingTiming": "advance", "valuationRule": "period_start",
         "effectiveFrom": "2011-07-01", "effectiveTo": "2024-12-31"},
    ])
    jdump(path / "fee_tiers.json", "tiers", [
        # An unbounded single tier and a flat-amount-only tier.
        {"scheduleId": "SCHED-LEGACY", "tierSeq": 1, "lowerBound": "0",
         "upperBound": None, "annualRateBps": "100.000000", "flatAmount": None},
        {"scheduleId": "SCHED-LEGACY", "tierSeq": 2, "lowerBound": "25000000",
         "upperBound": None, "annualRateBps": None, "flatAmount": "12500"},
    ])
    jdump(path / "fee_assignments.json", "assignments", [
        # Account-scoped rather than household-scoped, and backdated.
        {"scheduleId": "SCHED-LEGACY", "scopeType": "ACCOUNT", "scopeId": "A-Ü01",
         "effectiveFrom": "1994-01-10", "effectiveTo": "2024-12-31"},
    ])


# -- generic_csv -------------------------------------------------------------


GENERIC_MAPPING = """
source_id: acme_ledger
label: Acme Ledger nightly extract
vertical: shared
fold_in_hours: 4
streams:
  - name: households
    file: households.csv
    primary_key: [household_id]
    incremental_on: updated_at
    entities:
      - entity: household
        natural_key: "{household_id}"
        valid_from: opened_on
        columns:
          household_id: {from: household_id, type: text}
          name: {from: household_name, type: text}
          segment: {from: segment, type: optional_text, required: false}
          onboarded_on: {from: opened_on, type: date}
  - name: balances
    file: balances.csv
    primary_key: [account_id, as_of_date]
    incremental_on: as_of_date
    entities:
      - entity: account
        natural_key: "{account_id}"
        valid_from: as_of_date
        columns:
          account_id: {from: account_id, type: text}
          account_type: {constant: custodial}
          household_id: {from: household_id, type: optional_text, required: false}
          status: {constant: open}
      - entity: balance_snapshot
        natural_key: "{account_id}|{as_of_date}"
        valid_from: as_of_date
        columns:
          account_id: {from: account_id, type: text}
          as_of_date: {from: as_of_date, type: date}
          market_value: {from: market_value, type: decimal}
          cash_value: {from: cash_value, type: optional_decimal, required: false}
          currency: {from: currency, type: optional_text, required: false}
coverage:
  - {entity: household, grain: household, completeness: 0.8, manual_hours: 1}
  - {entity: account, grain: account, completeness: 0.6, manual_hours: 3}
  - {entity: balance_snapshot, grain: account/day, completeness: 0.95, manual_hours: 0}
"""

GENERIC_HH_FIELDS = ["household_id", "household_name", "segment", "opened_on", "updated_at"]
GENERIC_BAL_FIELDS = ["account_id", "household_id", "as_of_date", "market_value", "cash_value", "currency"]


def generic_csv(base: Path) -> None:
    (base / "mapping.yml").write_text(GENERIC_MAPPING.lstrip())

    typical = base / "typical"
    cdump(typical / "households.csv", GENERIC_HH_FIELDS, [
        {"household_id": "H-001", "household_name": "The Okafor Household",
         "segment": "core", "opened_on": "2019-04-02", "updated_at": "2026-01-05"},
    ])
    cdump(typical / "balances.csv", GENERIC_BAL_FIELDS, [
        {"account_id": "A-001", "household_id": "H-001", "as_of_date": "2026-03-31",
         "market_value": "1450000.00", "cash_value": "42000.00", "currency": "USD"},
    ])

    path = base / "pathological"
    cdump(path / "households.csv", GENERIC_HH_FIELDS, [
        # Blank optional column, unicode, and a US date format.
        {"household_id": "H-Ü01", "household_name": "Ménage Dubois-O'Brien",
         "segment": "", "opened_on": "01/03/1994", "updated_at": "2026-01-05"},
    ])
    cdump(path / "balances.csv", GENERIC_BAL_FIELDS, [
        # Parenthesised negative, missing household, empty currency.
        {"account_id": "A-Ü01", "household_id": "", "as_of_date": "2026-03-31",
         "market_value": "(18,500.25)", "cash_value": "", "currency": ""},
    ])


BUILDERS = {
    "orion": orion,
    "redtail": redtail,
    "schwab_custodian": schwab_custodian,
    "qbo": qbo,
    "manual_fee_schedule": manual_fee_schedule,
    "generic_csv": generic_csv,
}


def build_goldens(only: str | None = None) -> None:
    """Import the test helpers so goldens are produced by the same code the
    suite compares against."""
    sys.path.insert(0, str(ROOT / "tests"))
    from test_adapter_contract import _instance, _map_all  # noqa: E402

    from fracture.adapters.registry import all_adapters

    for source_id, cls in sorted(all_adapters().items()):
        if only and source_id != only:
            continue
        base = FIXTURES / source_id
        adapter = _instance(source_id, cls)
        streams = adapter.discover({"export_dir": str(base / "typical"), "read_only": True})
        snapshot = sorted(
            (
                {
                    "name": s.name,
                    "primary_key": list(s.primary_key),
                    "incremental_on": s.incremental_on,
                }
                for s in streams
            ),
            key=lambda s: s["name"],
        )
        (base / "discovery.json").write_text(json.dumps(snapshot, indent=1))
        for case in ("typical", "pathological"):
            records = _map_all(cls, source_id, case)
            (base / f"golden_{case}.json").write_text(json.dumps(records, indent=1))
        print(f"  {source_id}: {len(snapshot)} streams, goldens written")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    if args.clean and FIXTURES.exists():
        shutil.rmtree(FIXTURES)

    for source_id, builder in BUILDERS.items():
        if args.only and source_id != args.only:
            continue
        base = FIXTURES / source_id
        base.mkdir(parents=True, exist_ok=True)
        # Every adapter needs an empty fixture: an empty source is a normal
        # Tuesday, and the adapter must return nothing rather than raise.
        empty = base / "empty"
        empty.mkdir(exist_ok=True)
        (empty / ".gitkeep").write_text("")
        builder(base)
        print(f"built fixtures for {source_id}")

    print("regenerating goldens")
    build_goldens(args.only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
