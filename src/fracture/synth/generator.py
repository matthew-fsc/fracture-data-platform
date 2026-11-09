"""Synthetic tenant generator.

Produces, per firm, the export files each adapter reads: Orion API pages,
Redtail API pages, Schwab nightly CSVs, QuickBooks transactions, and a
hand-entered fee schedule workbook. The output is a directory tree, so the
adapters exercised in tests and demos are the same code paths that will run
against a client's systems.

Everything is seeded. The same spec produces the same estate, byte for byte,
which is what lets a test assert "the platform found the 47 unbilled households
we planted" rather than "the platform found some unbilled households".
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import random
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from fracture.core.logging import get_logger
from fracture.synth.config import DEMO_ESTATE, EstateSpec, FirmSpec
from fracture.synth.fees import period_fee

log = get_logger("synth.generator")

CENT = Decimal("0.01")

FIRST_NAMES = [
    "Amara", "Devin", "Priya", "Noel", "Rowan", "Sasha", "Emeka", "Ingrid", "Tomas",
    "Yuki", "Marcus", "Lena", "Idris", "Farah", "Owen", "Nadia", "Hugo", "Clara",
    "Rafael", "Simone", "Anders", "Mei", "Kwame", "Beatriz", "Jonas", "Tamsin",
]
LAST_NAMES = [
    "Okafor", "Lindqvist", "Ferrer", "Whitlock", "Nakamura", "Duarte", "Kowalski",
    "Abebe", "Marchetti", "Stroud", "Vasquez", "Bergman", "Halloran", "Osei",
    "Petrov", "Ellison", "Rahman", "Castellanos", "Novak", "Fairbanks", "Ibarra",
]
SEGMENTS = ["private_client", "core", "emerging", "institutional"]
REGISTRATIONS = ["individual", "joint", "ira", "roth_ira", "trust", "corporate"]
SERVICE_TYPES = ["onboarding", "transfer", "trade"]

#: Schedules a mid-market RIA actually runs: a tiered standard, a legacy flat
#: rate nobody has repapered, a household minimum, and a flat retainer.
SCHEDULE_TEMPLATES = [
    {
        "schedule_id": "SCHED-STD",
        "name": "Standard Tiered Advisory",
        "basis": "aum",
        "frequency": "quarterly",
        "calc_method": "tiered",
        "weight": 0.55,
        "tiers": [
            {"tier_seq": 1, "lower_bound": 0, "upper_bound": 1000000, "annual_rate_bps": 110},
            {"tier_seq": 2, "lower_bound": 1000000, "upper_bound": 3000000, "annual_rate_bps": 85},
            {"tier_seq": 3, "lower_bound": 3000000, "upper_bound": 10000000, "annual_rate_bps": 60},
            {"tier_seq": 4, "lower_bound": 10000000, "upper_bound": None, "annual_rate_bps": 40},
        ],
    },
    {
        "schedule_id": "SCHED-LEGACY",
        "name": "Legacy Flat 1.00%",
        "basis": "aum",
        "frequency": "quarterly",
        "calc_method": "blended",
        "weight": 0.22,
        "tiers": [
            {"tier_seq": 1, "lower_bound": 0, "upper_bound": None, "annual_rate_bps": 100},
        ],
    },
    {
        "schedule_id": "SCHED-INST",
        "name": "Institutional Tiered",
        "basis": "aum",
        "frequency": "quarterly",
        "calc_method": "tiered",
        "weight": 0.13,
        "tiers": [
            {"tier_seq": 1, "lower_bound": 0, "upper_bound": 5000000, "annual_rate_bps": 55},
            {"tier_seq": 2, "lower_bound": 5000000, "upper_bound": None, "annual_rate_bps": 35},
        ],
    },
    {
        "schedule_id": "SCHED-RETAINER",
        "name": "Flat Planning Retainer",
        "basis": "flat",
        "frequency": "quarterly",
        "calc_method": "flat",
        "weight": 0.10,
        "tiers": [
            {"tier_seq": 1, "lower_bound": 0, "upper_bound": None, "flat_amount": 3750},
        ],
    },
]


@dataclass
class PlantedDefects:
    """What the generator deliberately broke, for the tests to find."""

    unbilled_households: set[str] = field(default_factory=set)
    unbilled_amount: Decimal = Decimal(0)
    below_schedule_invoices: set[str] = field(default_factory=set)
    below_schedule_amount: Decimal = Decimal(0)
    uncollected_invoices: set[str] = field(default_factory=set)
    uncollected_amount: Decimal = Decimal(0)
    custodian_variance_accounts: set[str] = field(default_factory=set)
    producer_key_mismatches: set[str] = field(default_factory=set)
    sla_breaches: set[str] = field(default_factory=set)
    non_billable_billed: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, Any]:
        return {
            "unbilled_households": sorted(self.unbilled_households),
            "unbilled_amount": str(self.unbilled_amount),
            "below_schedule_invoices": sorted(self.below_schedule_invoices),
            "below_schedule_amount": str(self.below_schedule_amount),
            "uncollected_invoices": sorted(self.uncollected_invoices),
            "uncollected_amount": str(self.uncollected_amount),
            "custodian_variance_accounts": sorted(self.custodian_variance_accounts),
            "producer_key_mismatches": sorted(self.producer_key_mismatches),
            "sla_breaches": sorted(self.sla_breaches),
            "non_billable_billed": sorted(self.non_billable_billed),
        }


@dataclass
class GeneratedFirm:
    firm: FirmSpec
    export_dir: Path
    defects: PlantedDefects
    counts: dict[str, int] = field(default_factory=dict)


@dataclass
class GeneratedEstate:
    spec: EstateSpec
    root: Path
    firms: list[GeneratedFirm] = field(default_factory=list)

    def firm(self, firm_id: str) -> GeneratedFirm:
        for f in self.firms:
            if f.firm.firm_id == firm_id:
                return f
        raise KeyError(firm_id)

    def export_dir(self, firm_id: str) -> Path:
        return self.firm(firm_id).export_dir

    def manifest(self) -> dict[str, Any]:
        return {
            "tenant_slug": self.spec.tenant_slug,
            "tenant_name": self.spec.tenant_name,
            "motion": self.spec.motion,
            "period_end": self.spec.period_end.isoformat(),
            "months": self.spec.months,
            "firms": [
                {
                    "firm_id": f.firm.firm_id,
                    "legal_name": f.firm.legal_name,
                    "role": f.firm.role,
                    "close_date": f.firm.close_date.isoformat() if f.firm.close_date else None,
                    "sources": list(f.firm.sources),
                    "counts": f.counts,
                    "planted_defects": f.defects.as_dict(),
                }
                for f in self.firms
            ],
        }


def _money(value: Decimal | float) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _quarter_ends(month_ends: list[dt.date]) -> list[dt.date]:
    return [d for d in month_ends if d.month in (3, 6, 9, 12)]


class EstateGenerator:
    def __init__(self, spec: EstateSpec, root: Path | str) -> None:
        self.spec = spec
        self.root = Path(root)
        self.month_ends = spec.month_ends()
        self.quarter_ends = _quarter_ends(self.month_ends)

    def generate(self) -> GeneratedEstate:
        self.root.mkdir(parents=True, exist_ok=True)
        estate = GeneratedEstate(spec=self.spec, root=self.root)
        for index, firm in enumerate(self.spec.firms):
            rng = random.Random(self.spec.seed + index * 7919)
            estate.firms.append(self._generate_firm(firm, rng))
        (self.root / "estate_manifest.json").write_text(
            json.dumps(estate.manifest(), indent=2, sort_keys=True)
        )
        log.info(
            "generated estate %s: %d firms under %s",
            self.spec.tenant_slug, len(estate.firms), self.root,
        )
        return estate

    # -- one firm ----------------------------------------------------------

    def _generate_firm(self, firm: FirmSpec, rng: random.Random) -> GeneratedFirm:
        out = self.root / firm.firm_id
        out.mkdir(parents=True, exist_ok=True)
        defects = PlantedDefects()

        producers = self._producers(firm, rng)
        households, accounts = self._households_and_accounts(firm, producers, rng)
        balances = self._balances(firm, accounts, rng)
        schedules, assignments = self._schedules(firm, households, rng)
        invoices, payments = self._billing(
            firm, households, accounts, balances, schedules, assignments, defects, rng
        )
        expenses, time_entries = self._costs(firm, producers, households, invoices, rng)
        service_events = self._service_events(firm, households, accounts, producers, defects, rng)
        custodian_accounts, custodian_balances, control_totals = self._custodian_view(
            firm, accounts, balances, defects, rng
        )

        self._write_orion(out, households, accounts, balances, producers, service_events)
        if "redtail" in firm.sources:
            self._write_redtail(out, firm, households, accounts, producers, service_events, defects, rng)
        self._write_schwab(out, custodian_accounts, custodian_balances, control_totals)
        self._write_qbo(out, invoices, payments, expenses, time_entries)
        self._write_fee_schedules(out, schedules, assignments)

        counts = {
            "households": len(households), "accounts": len(accounts),
            "producers": len(producers), "balance_snapshots": len(balances),
            "invoices": len(invoices), "payments": len(payments),
            "expenses": len(expenses), "time_entries": len(time_entries),
            "service_events": len(service_events), "schedules": len(schedules),
        }
        log.info("firm %s: %s", firm.firm_id, counts)
        return GeneratedFirm(firm=firm, export_dir=out, defects=defects, counts=counts)

    # -- entities ----------------------------------------------------------

    def _producers(self, firm: FirmSpec, rng: random.Random) -> list[dict[str, Any]]:
        producers = []
        for i in range(firm.producers):
            name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
            hire = dt.date(rng.randint(2005, 2022), rng.randint(1, 12), rng.randint(1, 28))
            # One advisor leaves mid-window: the "what walks out the door" case.
            term = None
            if i == firm.producers - 1 and firm.producers > 3:
                term = self.month_ends[max(0, len(self.month_ends) - 5)]
            producers.append(
                {
                    "producer_id": f"{firm.firm_id}-ADV-{i+1:03d}",
                    "name": name,
                    "rep_code": f"{firm.firm_id[:2]}{i+1:03d}",
                    "hire_date": hire,
                    "term_date": term,
                    "role": "advisor" if i < firm.producers - 1 else "advisor",
                }
            )
        return producers

    def _households_and_accounts(
        self, firm: FirmSpec, producers: list[dict[str, Any]], rng: random.Random
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        households: list[dict[str, Any]] = []
        accounts: list[dict[str, Any]] = []
        top = producers[0]["producer_id"]
        others = [p["producer_id"] for p in producers[1:]] or [top]
        first_month = self.month_ends[0]

        for i in range(firm.households):
            hh_id = f"{firm.firm_id}-HH-{i+1:05d}"
            surname = rng.choice(LAST_NAMES)
            onboarded = dt.date(
                rng.randint(2004, first_month.year), rng.randint(1, 12), rng.randint(1, 28)
            )
            # Book concentration: the key-person finding is planted here.
            producer_id = top if rng.random() < firm.top_producer_share else rng.choice(others)
            # Lognormal-ish AUM: a few very large households, a long tail.
            base_aum = Decimal(str(round(rng.lognormvariate(13.4, 0.95), 2)))
            base_aum = max(base_aum, Decimal("25000"))
            households.append(
                {
                    "household_id": hh_id,
                    "name": f"The {surname} Household",
                    "segment": rng.choices(SEGMENTS, weights=[0.2, 0.5, 0.25, 0.05])[0],
                    "onboarded_on": onboarded,
                    "producer_id": producer_id,
                    "base_aum": base_aum,
                    "primary_party_id": f"{firm.firm_id}-P-{i+1:05d}",
                    "primary_name": f"{rng.choice(FIRST_NAMES)} {surname}",
                    "spouse_party_id": f"{firm.firm_id}-P-{i+1:05d}S" if rng.random() < 0.55 else None,
                    "spouse_name": f"{rng.choice(FIRST_NAMES)} {surname}",
                }
            )
            for j in range(rng.choices([1, 2, 3, 4], weights=[0.42, 0.34, 0.18, 0.06])[0]):
                # Non-billable registrations exist and must not inflate expected fees.
                billable = rng.random() > 0.05
                accounts.append(
                    {
                        "account_id": f"{firm.firm_id}-A-{i+1:05d}-{j+1}",
                        "household_id": hh_id,
                        "party_id": households[-1]["primary_party_id"],
                        "party_name": households[-1]["primary_name"],
                        "registration": rng.choice(REGISTRATIONS),
                        "opened_on": onboarded + dt.timedelta(days=rng.randint(0, 900)),
                        "closed_on": None,
                        "custodian": "schwab",
                        "billable": billable,
                        "hh_base_aum": base_aum,
                        "weight": Decimal(str(round(rng.uniform(0.15, 1.0), 4))),
                    }
                )
        # Normalise account weights so household AUM is the household's AUM.
        by_hh: dict[str, list[dict[str, Any]]] = {}
        for account in accounts:
            by_hh.setdefault(account["household_id"], []).append(account)
        for hh in households:
            group = by_hh[hh["household_id"]]
            total = sum((a["weight"] for a in group), Decimal(0))
            for account in group:
                account["share"] = account["weight"] / total
        return households, accounts

    def _balances(
        self, firm: FirmSpec, accounts: list[dict[str, Any]], rng: random.Random
    ) -> list[dict[str, Any]]:
        by_hh: dict[str, list[dict[str, Any]]] = {}
        for a in accounts:
            by_hh.setdefault(a["household_id"], []).append(a)

        balances: list[dict[str, Any]] = []
        # A market path shared by every account, so consolidated AUM moves like
        # a market rather than like noise.
        path: list[Decimal] = []
        level = Decimal("1.0")
        for _ in self.month_ends:
            level *= Decimal(str(round(1 + rng.gauss(0.0055, 0.028), 6)))
            path.append(level)

        for account in accounts:
            base = Decimal(str(account["share"])) * Decimal(str(account["hh_base_aum"]))
            for idx, as_of in enumerate(self.month_ends):
                if account["opened_on"] > as_of:
                    continue
                idiosyncratic = Decimal(str(round(1 + rng.gauss(0, 0.012), 6)))
                value = _money(base * path[idx] * idiosyncratic)
                if value <= 0:
                    value = Decimal("1000.00")
                balances.append(
                    {
                        "account_id": account["account_id"],
                        "household_id": account["household_id"],
                        "as_of_date": as_of,
                        "market_value": value,
                        "cash_value": _money(value * Decimal(str(round(rng.uniform(0.01, 0.06), 4)))),
                        "billable": account["billable"],
                    }
                )
        return balances

    def _schedules(
        self, firm: FirmSpec, households: list[dict[str, Any]], rng: random.Random
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        schedules = []
        for template in SCHEDULE_TEMPLATES:
            schedules.append(
                {
                    **{k: v for k, v in template.items() if k != "weight"},
                    "schedule_id": f"{firm.firm_id}-{template['schedule_id']}",
                    "effective_from": dt.date(2018, 1, 1),
                    "effective_to": None,
                }
            )
        weights = [t["weight"] for t in SCHEDULE_TEMPLATES]
        assignments = []
        for hh in households:
            chosen = rng.choices(schedules, weights=weights)[0]
            hh["schedule_id"] = chosen["schedule_id"]
            assignments.append(
                {
                    "schedule_id": chosen["schedule_id"],
                    "scope_type": "household",
                    "scope_id": hh["household_id"],
                    "effective_from": max(hh["onboarded_on"], dt.date(2018, 1, 1)),
                    "effective_to": None,
                }
            )
        return schedules, assignments

    # -- billing -----------------------------------------------------------

    def _billing(
        self,
        firm: FirmSpec,
        households: list[dict[str, Any]],
        accounts: list[dict[str, Any]],
        balances: list[dict[str, Any]],
        schedules: list[dict[str, Any]],
        assignments: list[dict[str, Any]],
        defects: PlantedDefects,
        rng: random.Random,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        schedule_by_id = {s["schedule_id"]: s for s in schedules}
        billable_by_hh_date: dict[tuple[str, dt.date], Decimal] = {}
        for b in balances:
            if not b["billable"]:
                continue
            key = (b["household_id"], b["as_of_date"])
            billable_by_hh_date[key] = billable_by_hh_date.get(key, Decimal(0)) + b["market_value"]

        # Accounts wrongly billed despite being flagged non-billable.
        non_billable = [a for a in accounts if not a["billable"]]
        wrongly_billed = {
            a["account_id"]
            for a in non_billable
            if rng.random() < firm.defects.billing_non_billable_rate
        }
        defects.non_billable_billed.update(wrongly_billed)
        if wrongly_billed:
            for b in balances:
                if b["account_id"] in wrongly_billed:
                    key = (b["household_id"], b["as_of_date"])
                    billable_by_hh_date[key] = billable_by_hh_date.get(key, Decimal(0)) + b["market_value"]

        invoices: list[dict[str, Any]] = []
        payments: list[dict[str, Any]] = []
        invoice_seq = 0
        payment_seq = 0

        for quarter_end in self.quarter_ends:
            period_start = _quarter_start(quarter_end)
            for hh in households:
                if hh["onboarded_on"] > quarter_end:
                    continue
                aum = billable_by_hh_date.get((hh["household_id"], quarter_end))
                if aum is None:
                    continue
                schedule = schedule_by_id[hh["schedule_id"]]
                correct = period_fee(
                    aum, schedule["tiers"], schedule["calc_method"], schedule["frequency"]
                )
                if correct <= 0:
                    continue

                # Defect 1: never invoiced. The first eligible household in the
                # final quarter is always planted, so the finding is guaranteed
                # to exist for the test suite and the demo.
                force_unbilled = (
                    firm.defects.unbilled_household_rate > 0
                    and not defects.unbilled_households
                    and quarter_end == self.quarter_ends[-1]
                )
                if force_unbilled or rng.random() < firm.defects.unbilled_household_rate:
                    defects.unbilled_households.add(f"{hh['household_id']}|{quarter_end.isoformat()}")
                    defects.unbilled_amount += correct
                    continue

                # Defect 2: invoiced below what the schedule says.
                amount = correct
                invoice_seq += 1
                invoice_id = f"{firm.firm_id}-INV-{invoice_seq:06d}"
                force_below = (
                    firm.defects.below_schedule_rate > 0
                    and not defects.below_schedule_invoices
                    and quarter_end == self.quarter_ends[-1]
                )
                if force_below or rng.random() < firm.defects.below_schedule_rate:
                    amount = _money(correct * Decimal(str(1 - firm.defects.below_schedule_discount)))
                    defects.below_schedule_invoices.add(invoice_id)
                    defects.below_schedule_amount += correct - amount

                issued = quarter_end + dt.timedelta(days=rng.randint(3, 18))
                invoices.append(
                    {
                        "invoice_id": invoice_id,
                        "household_id": hh["household_id"],
                        "issued_on": issued,
                        "due_on": issued + dt.timedelta(days=30),
                        "period_start": period_start,
                        "period_end": quarter_end,
                        "amount": amount,
                        "basis_amount": aum,
                        "schedule_id": schedule["schedule_id"],
                        "producer_id": hh["producer_id"],
                    }
                )

                # Defect 3: not collected, or collected short.
                roll = rng.random()
                force_uncollected = (
                    firm.defects.uncollected_rate > 0
                    and not defects.uncollected_invoices
                    and quarter_end == self.quarter_ends[-1]
                )
                if force_uncollected or roll < firm.defects.uncollected_rate:
                    defects.uncollected_invoices.add(invoice_id)
                    defects.uncollected_amount += amount
                    continue
                collected = amount
                if roll < firm.defects.uncollected_rate + firm.defects.partial_collection_rate:
                    collected = _money(amount * Decimal(str(round(rng.uniform(0.4, 0.85), 4))))
                    defects.uncollected_invoices.add(invoice_id)
                    defects.uncollected_amount += amount - collected
                payment_seq += 1
                payments.append(
                    {
                        "payment_id": f"{firm.firm_id}-PMT-{payment_seq:06d}",
                        "household_id": hh["household_id"],
                        "received_on": issued + dt.timedelta(days=rng.randint(5, 55)),
                        "amount": collected,
                        "method": rng.choice(["ach", "check", "fee_debit"]),
                        "invoice_id": invoice_id,
                    }
                )
        return invoices, payments

    def _costs(
        self,
        firm: FirmSpec,
        producers: list[dict[str, Any]],
        households: list[dict[str, Any]],
        invoices: list[dict[str, Any]],
        rng: random.Random,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Cost base sized from revenue, not drawn independently.

        A firm whose payroll is unrelated to its fee income produces a margin
        figure that is noise, and a demo where every client shows a negative
        loaded margin teaches the reader to distrust the number rather than to
        act on it.
        """
        expenses: list[dict[str, Any]] = []
        time_entries: list[dict[str, Any]] = []
        billed_by_month: dict[dt.date, Decimal] = {}
        for invoice in invoices:
            key = invoice["period_end"]
            billed_by_month[key] = billed_by_month.get(key, Decimal(0)) + invoice["amount"]
        quarterly_revenue = (
            sum(billed_by_month.values(), Decimal(0)) / max(len(billed_by_month), 1)
        )
        # Direct time entries are booked below and are part of the cost base, so
        # the expense budget carries the remainder.
        monthly_budget = (quarterly_revenue / 3) * Decimal(str(1 - firm.target_margin))
        headcount = max(len([p for p in producers if not p["term_date"]]), 1)

        seq = 0
        entry_seq = 0
        for month_end in self.month_ends:
            active = [
                p for p in producers
                if not (p["term_date"] and p["term_date"] < month_end)
            ]
            payroll_pool = monthly_budget * Decimal("0.55")
            per_head = payroll_pool / max(len(active), 1)
            for producer in active:
                seq += 1
                jitter = Decimal(str(round(rng.uniform(0.72, 1.34), 4)))
                expenses.append(
                    {
                        "expense_id": f"{firm.firm_id}-EXP-{seq:06d}",
                        "txn_date": month_end,
                        "category": "payroll",
                        "vendor": None,
                        "employee_ref": producer["producer_id"],
                        "amount": _money(max(per_head * jitter, Decimal("1500"))),
                        "allocation_basis": "direct",
                    }
                )
            for category, share in (("vendor", 0.18), ("occupancy", 0.14), ("allocation", 0.13)):
                seq += 1
                jitter = Decimal(str(round(rng.uniform(0.85, 1.18), 4)))
                expenses.append(
                    {
                        "expense_id": f"{firm.firm_id}-EXP-{seq:06d}",
                        "txn_date": month_end,
                        "category": category,
                        "vendor": f"{category.title()} Supplier {rng.randint(1, 6)}",
                        "employee_ref": None,
                        "amount": _money(
                            max(monthly_budget * Decimal(str(share)) * jitter, Decimal("500"))
                        ),
                        "allocation_basis": "revenue",
                    }
                )
            # Time entries on a sample of households: firms rarely track all of it,
            # which is why the qbo manifest prices fte_allocation at 0.6 coverage.
            for hh in rng.sample(households, k=min(len(households), max(8, len(households) // 20))):
                entry_seq += 1
                time_entries.append(
                    {
                        "entry_id": f"{firm.firm_id}-TIME-{entry_seq:06d}",
                        "entry_date": month_end,
                        "employee_ref": hh["producer_id"],
                        "producer_ref": hh["producer_id"],
                        "customer_ref": hh["household_id"],
                        "hours": Decimal(str(round(rng.uniform(0.5, 7.5), 2))),
                        "hourly_cost": Decimal(str(round(rng.uniform(65, 210), 2))),
                    }
                )
        return expenses, time_entries

    def _service_events(
        self,
        firm: FirmSpec,
        households: list[dict[str, Any]],
        accounts: list[dict[str, Any]],
        producers: list[dict[str, Any]],
        defects: PlantedDefects,
        rng: random.Random,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        seq = 0
        accounts_by_hh: dict[str, list[dict[str, Any]]] = {}
        for a in accounts:
            accounts_by_hh.setdefault(a["household_id"], []).append(a)
        n = max(24, len(households) // 3)
        for _ in range(n):
            hh = rng.choice(households)
            seq += 1
            event_type = rng.choices(SERVICE_TYPES, weights=[0.28, 0.34, 0.38])[0]
            sla_hours = {"onboarding": 240, "transfer": 120, "trade": 24}[event_type]
            opened = dt.datetime.combine(
                rng.choice(self.month_ends) - dt.timedelta(days=rng.randint(0, 27)),
                dt.time(rng.randint(8, 17), rng.randint(0, 59)),
                tzinfo=dt.timezone.utc,
            )
            breached = rng.random() < firm.defects.sla_breach_rate
            if firm.defects.sla_breach_rate > 0 and not defects.sla_breaches:
                breached = True
            duration = sla_hours * (rng.uniform(1.15, 3.4) if breached else rng.uniform(0.15, 0.92))
            event_id = f"{firm.firm_id}-SVC-{seq:06d}"
            if breached:
                defects.sla_breaches.add(event_id)
            # A few stay open, which is its own finding.
            closed = None if rng.random() < 0.05 else opened + dt.timedelta(hours=duration)
            events.append(
                {
                    "service_event_id": event_id,
                    "event_type": event_type,
                    "household_id": hh["household_id"],
                    "account_id": (
                        rng.choice(accounts_by_hh[hh["household_id"]])["account_id"]
                        if accounts_by_hh.get(hh["household_id"]) else None
                    ),
                    "producer_id": hh["producer_id"],
                    "opened_at": opened,
                    "closed_at": closed,
                    "sla_target_hours": sla_hours,
                }
            )
        return events

    def _custodian_view(
        self,
        firm: FirmSpec,
        accounts: list[dict[str, Any]],
        balances: list[dict[str, Any]],
        defects: PlantedDefects,
        rng: random.Random,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """The custodian's own version of the same accounts.

        Mostly identical. On a small share of accounts it is not, and since the
        custodian is the record of truth (see canon.precedence), that difference
        is the reconciliation finding.
        """
        varying = {
            a["account_id"] for a in accounts
            if rng.random() < firm.defects.custodian_variance_rate
        }
        # Guarantee at least one when the rate is non-zero. A test that asserts
        # "the platform found the custodian disagreement" must not fail because
        # a 2% draw came up empty on a small fixture.
        if not varying and accounts and firm.defects.custodian_variance_rate > 0:
            varying = {accounts[len(accounts) // 2]["account_id"]}
        defects.custodian_variance_accounts.update(varying)

        custodian_accounts = [
            {
                "AccountNumber": a["account_id"],
                "RegistrationType": a["registration"],
                "OpenDate": a["opened_on"],
                "CloseDate": a["closed_on"],
                "RepCode": "",
            }
            for a in accounts
        ]
        custodian_balances = []
        for b in balances:
            value = b["market_value"]
            if b["account_id"] in varying:
                drift = Decimal(str(round(
                    rng.uniform(-firm.defects.custodian_variance_magnitude,
                                firm.defects.custodian_variance_magnitude), 6)))
                value = _money(value * (Decimal(1) + drift))
            custodian_balances.append(
                {
                    "AccountNumber": b["account_id"],
                    "AsOfDate": b["as_of_date"],
                    "TotalValue": value,
                    "CashBalance": b["cash_value"],
                    "Currency": "USD",
                }
            )
        totals: dict[dt.date, Decimal] = {}
        counts: dict[dt.date, int] = {}
        for b in custodian_balances:
            totals[b["AsOfDate"]] = totals.get(b["AsOfDate"], Decimal(0)) + b["TotalValue"]
            counts[b["AsOfDate"]] = counts.get(b["AsOfDate"], 0) + 1
        control_totals = [
            {"AsOfDate": d, "TotalValue": _money(v), "AccountCount": counts[d]}
            for d, v in sorted(totals.items())
        ]
        return custodian_accounts, custodian_balances, control_totals

    # -- writers -----------------------------------------------------------

    def _write_orion(
        self, out: Path, households, accounts, balances, producers, service_events
    ) -> None:
        assignments: dict[str, list[dict[str, Any]]] = {}
        for hh in households:
            assignments.setdefault(hh["producer_id"], []).append(
                {
                    "householdId": hh["household_id"],
                    "splitPct": 100,
                    "effectiveFrom": hh["onboarded_on"].isoformat(),
                    "effectiveTo": None,
                }
            )
        _write_json(out / "orion_households.json", "households", [
            {
                "householdId": hh["household_id"],
                "name": hh["name"],
                "tier": hh["segment"],
                "createdOn": hh["onboarded_on"].isoformat(),
                "updatedAt": hh["onboarded_on"].isoformat(),
            }
            for hh in households
        ])
        _write_json(out / "orion_accounts.json", "accounts", [
            {
                "accountId": a["account_id"],
                "householdId": a["household_id"],
                "primaryContactId": a["party_id"],
                "primaryContactName": a["party_name"],
                "registrationType": a["registration"],
                "custodian": a["custodian"],
                "openedOn": a["opened_on"].isoformat(),
                "closedOn": a["closed_on"].isoformat() if a["closed_on"] else None,
                "billable": a["billable"],
                "country": "US",
                "updatedAt": a["opened_on"].isoformat(),
            }
            for a in accounts
        ])
        _write_json(out / "orion_positions.json", "positions", [
            {
                "accountId": b["account_id"],
                "asOfDate": b["as_of_date"].isoformat(),
                "marketValue": str(b["market_value"]),
                "cashValue": str(b["cash_value"]),
                "billableValue": str(b["market_value"]) if b["billable"] else "0.00",
                "currency": "USD",
            }
            for b in balances
        ])
        _write_json(out / "orion_representatives.json", "representatives", [
            {
                "repId": p["producer_id"],
                "name": p["name"],
                "repCode": p["rep_code"],
                "hireDate": p["hire_date"].isoformat(),
                "termDate": p["term_date"].isoformat() if p["term_date"] else None,
                "assignments": assignments.get(p["producer_id"], []),
            }
            for p in producers
        ])
        _write_json(out / "orion_service_requests.json", "service_requests", [
            {
                "requestId": e["service_event_id"],
                "requestType": e["event_type"],
                "householdId": e["household_id"],
                "accountId": e["account_id"],
                "assignedRepId": e["producer_id"],
                "openedAt": e["opened_at"].isoformat(),
                "closedAt": e["closed_at"].isoformat() if e["closed_at"] else None,
                "slaHours": e["sla_target_hours"],
            }
            for e in service_events if e["event_type"] in SERVICE_TYPES
        ])

    def _write_redtail(
        self, out: Path, firm, households, accounts, producers, service_events, defects, rng
    ) -> None:
        contacts = []
        for hh in households:
            contacts.append(
                {
                    "contactId": hh["primary_party_id"],
                    "firstName": hh["primary_name"].split()[0],
                    "lastName": hh["primary_name"].split()[-1],
                    "displayName": hh["primary_name"],
                    "contactType": "individual",
                    "familyId": hh["household_id"],
                    "familyRole": "head of household",
                    "clientSince": hh["onboarded_on"].isoformat(),
                    "country": "US",
                    "updatedAt": hh["onboarded_on"].isoformat(),
                }
            )
            if hh["spouse_party_id"]:
                contacts.append(
                    {
                        "contactId": hh["spouse_party_id"],
                        "firstName": hh["spouse_name"].split()[0],
                        "lastName": hh["spouse_name"].split()[-1],
                        "displayName": hh["spouse_name"],
                        "contactType": "individual",
                        "familyId": hh["household_id"],
                        "familyRole": "spouse",
                        "clientSince": hh["onboarded_on"].isoformat(),
                        "country": "US",
                        "updatedAt": hh["onboarded_on"].isoformat(),
                    }
                )
        _write_json(out / "redtail_contacts.json", "contacts", contacts)
        _write_json(out / "redtail_families.json", "families", [
            {
                "familyId": hh["household_id"],
                "familyName": hh["name"],
                "segment": hh["segment"],
                "servicingAdvisorId": hh["producer_id"],
                "splitPct": 100,
                "createdAt": hh["onboarded_on"].isoformat(),
                "updatedAt": hh["onboarded_on"].isoformat(),
            }
            for hh in households
        ])
        users = []
        for p in producers:
            # The CRM's rep code does not always match the custodian's. This is
            # the entity-resolution failure mode in spec 16, planted on purpose.
            mismatched = rng.random() < firm.defects.producer_key_mismatch_rate
            if (
                firm.defects.producer_key_mismatch_rate > 0
                and not defects.producer_key_mismatches
                and p is producers[-1]
            ):
                mismatched = True
            if mismatched:
                defects.producer_key_mismatches.add(p["producer_id"])
            users.append(
                {
                    "userId": p["producer_id"],
                    "name": p["name"],
                    "role": "advisor",
                    "repCode": (p["rep_code"] + "X") if mismatched else p["rep_code"],
                    "hireDate": p["hire_date"].isoformat(),
                    "termDate": p["term_date"].isoformat() if p["term_date"] else None,
                }
            )
        _write_json(out / "redtail_users.json", "users", users)
        _write_json(out / "redtail_activities.json", "activities", [
            {
                "activityId": e["service_event_id"].replace("SVC", "ACT"),
                "familyId": e["household_id"],
                "ownerUserId": e["producer_id"],
                "createdAt": e["opened_at"].isoformat(),
                "completedAt": e["closed_at"].isoformat() if e["closed_at"] else None,
                "slaHours": 48,
            }
            for e in service_events[::3]
        ])

    def _write_schwab(self, out: Path, accounts, balances, control_totals) -> None:
        _write_csv(
            out / "schwab_accounts.csv",
            ["AccountNumber", "RegistrationType", "OpenDate", "CloseDate", "RepCode"],
            [
                {
                    "AccountNumber": a["AccountNumber"],
                    "RegistrationType": a["RegistrationType"],
                    "OpenDate": a["OpenDate"].isoformat(),
                    "CloseDate": a["CloseDate"].isoformat() if a["CloseDate"] else "",
                    "RepCode": a["RepCode"],
                }
                for a in accounts
            ],
        )
        _write_csv(
            out / "schwab_positions.csv",
            ["AccountNumber", "AsOfDate", "TotalValue", "CashBalance", "Currency"],
            [
                {
                    "AccountNumber": b["AccountNumber"],
                    "AsOfDate": b["AsOfDate"].isoformat(),
                    "TotalValue": str(b["TotalValue"]),
                    "CashBalance": str(b["CashBalance"]),
                    "Currency": b["Currency"],
                }
                for b in balances
            ],
        )
        _write_csv(
            out / "schwab_control_totals.csv",
            ["AsOfDate", "TotalValue", "AccountCount"],
            [
                {
                    "AsOfDate": t["AsOfDate"].isoformat(),
                    "TotalValue": str(t["TotalValue"]),
                    "AccountCount": str(t["AccountCount"]),
                }
                for t in control_totals
            ],
        )

    def _write_qbo(self, out: Path, invoices, payments, expenses, time_entries) -> None:
        _write_json(out / "qbo_invoices.json", "invoices", [
            {
                "invoiceId": i["invoice_id"],
                "customerRef": i["household_id"],
                "txnDate": i["issued_on"].isoformat(),
                "dueDate": i["due_on"].isoformat(),
                "periodStart": i["period_start"].isoformat(),
                "periodEnd": i["period_end"].isoformat(),
                "totalAmount": str(i["amount"]),
                "currency": "USD",
                "status": "open",
                "lines": [
                    {
                        "description": f"Advisory fee {i['period_start']} to {i['period_end']}",
                        "amount": str(i["amount"]),
                        "basisAmount": str(i["basis_amount"]),
                        "accountRef": None,
                        "revenueEventId": None,
                    }
                ],
            }
            for i in invoices
        ])
        _write_json(out / "qbo_payments.json", "payments", [
            {
                "paymentId": p["payment_id"],
                "customerRef": p["household_id"],
                "txnDate": p["received_on"].isoformat(),
                "totalAmount": str(p["amount"]),
                "paymentMethod": p["method"],
                "applications": [{"invoiceId": p["invoice_id"], "amount": str(p["amount"])}],
            }
            for p in payments
        ])
        _write_json(out / "qbo_expenses.json", "expenses", [
            {
                "expenseId": e["expense_id"],
                "txnDate": e["txn_date"].isoformat(),
                "category": e["category"],
                "vendorRef": e["vendor"],
                "employeeRef": e["employee_ref"],
                "amount": str(e["amount"]),
                "allocationBasis": e["allocation_basis"],
            }
            for e in expenses
        ])
        _write_json(out / "qbo_time_entries.json", "time_entries", [
            {
                "entryId": t["entry_id"],
                "entryDate": t["entry_date"].isoformat(),
                "employeeRef": t["employee_ref"],
                "producerRef": t["producer_ref"],
                "customerRef": t["customer_ref"],
                "hours": str(t["hours"]),
                "hourlyCost": str(t["hourly_cost"]),
            }
            for t in time_entries
        ])

    def _write_fee_schedules(self, out: Path, schedules, assignments) -> None:
        _write_json(out / "fee_schedules.json", "schedules", [
            {
                "scheduleId": s["schedule_id"],
                "name": s["name"],
                "basis": s["basis"],
                "frequency": s["frequency"],
                "calcMethod": s["calc_method"],
                "billingTiming": "arrears",
                "valuationRule": "period_end",
                "effectiveFrom": s["effective_from"].isoformat(),
                "effectiveTo": None,
            }
            for s in schedules
        ])
        _write_json(out / "fee_tiers.json", "tiers", [
            {
                "scheduleId": s["schedule_id"],
                "tierSeq": t["tier_seq"],
                "lowerBound": str(t["lower_bound"]),
                "upperBound": str(t["upper_bound"]) if t.get("upper_bound") is not None else None,
                "annualRateBps": t.get("annual_rate_bps"),
                "flatAmount": t.get("flat_amount"),
            }
            for s in schedules for t in s["tiers"]
        ])
        _write_json(out / "fee_assignments.json", "assignments", [
            {
                "scheduleId": a["schedule_id"],
                "scopeType": a["scope_type"],
                "scopeId": a["scope_id"],
                "effectiveFrom": a["effective_from"].isoformat(),
                "effectiveTo": None,
            }
            for a in assignments
        ])


def _quarter_start(quarter_end: dt.date) -> dt.date:
    return dt.date(quarter_end.year, quarter_end.month - 2, 1)


def _write_json(path: Path, key: str, records: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps({key: records}, indent=1, sort_keys=True, default=str))


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def generate(spec: EstateSpec = DEMO_ESTATE, root: Path | str = "/tmp/fracture-synth") -> GeneratedEstate:
    return EstateGenerator(spec, root).generate()
