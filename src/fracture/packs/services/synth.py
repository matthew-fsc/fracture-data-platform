"""A synthetic practice, loaded through the config-driven CSV adapter.

Deliberately no bespoke adapter. A practice management system export is a
nightly file drop, which is what `generic_csv` exists for, and onboarding a
whole new vertical with a mapping config and zero new Python is the claim the
adapter was built to support. If that claim were false, this is where it would
fail.

The estate is small and shaped to exercise the pack's edges rather than to look
impressive: a fixed-fee engagement that overruns, a time-and-materials
engagement billed below entitlement, a capped engagement that hits its cap, an
engagement billed above entitlement, write-offs under every reason code, an
invoice settled late and one not settled at all, and one pooled cost line so
the allocation check has something to reconcile.
"""

from __future__ import annotations

import csv
import datetime as dt
import random
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from fracture.core.logging import get_logger

log = get_logger("packs.services.synth")

GRADES = ("partner", "director", "manager", "consultant", "analyst")
#: Charge-out rate and fully loaded cost per hour, by grade. Cost is roughly a
#: third of charge, which is the shape a healthy practice runs at; the pack's
#: job is to show where that shape breaks.
GRADE_ECONOMICS: dict[str, tuple[int, int]] = {
    "partner": (650, 210),
    "director": (480, 160),
    "manager": (360, 118),
    "consultant": (265, 92),
    "analyst": (185, 68),
}
WRITEOFF_REASONS = ("scope", "goodwill", "training", "admin", "rework")


@dataclass(frozen=True)
class PracticeSpec:
    tenant_slug: str
    tenant_name: str = "Fracture Systems Consulting LLC"
    firm_id: str = "FSC"
    motion: str = "operating"
    clients: int = 6
    engagements: int = 10
    timekeepers: int = 8
    months: int = 3
    # Close to the present on purpose. Lockup is measured from the invoice date
    # to the pack's system time, so a demo window eight months stale reports a
    # correct but useless 270 days. The model is right; the window has to be
    # plausible for the figure to be read rather than discounted.
    period_start: dt.date = dt.date(2026, 7, 1)
    seed: int = 20261026


#: The shape used by the test suite: small enough to load in a second, wide
#: enough to hit every assertion.
TEST_PRACTICE = PracticeSpec(
    tenant_slug="svc-test", clients=4, engagements=6, timekeepers=5, months=2
)
#: The shape used by the demo.
DEMO_PRACTICE = PracticeSpec(
    tenant_slug="fracture-practice", clients=8, engagements=14, timekeepers=11, months=3
)


@dataclass
class GeneratedPractice:
    spec: PracticeSpec
    export_dir: Path
    mapping_path: Path
    counts: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        parts = ", ".join(f"{k}={v}" for k, v in sorted(self.counts.items()))
        return f"practice {self.spec.firm_id}: {parts}"


def _month_ends(start: dt.date, months: int) -> list[dt.date]:
    out = []
    year, month = start.year, start.month
    for _ in range(months):
        if month == 12:
            nxt = dt.date(year + 1, 1, 1)
        else:
            nxt = dt.date(year, month + 1, 1)
        out.append(nxt - dt.timedelta(days=1))
        year, month = nxt.year, nxt.month
    return out


def _write_csv(path: Path, rows: Sequence[dict[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fields))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row[k]) for k in fields})


MAPPING = """\
source_id: generic_csv
label: Practice management nightly extract
vertical: accounting
fold_in_hours: 6
streams:
  - name: clients
    file: clients.csv
    primary_key: [client_id]
    entities:
      - entity: client
        natural_key: "{client_id}"
        valid_from: onboarded_on
        columns:
          client_id: {from: client_id, type: text}
          name: {from: name, type: text}
          client_type: {from: client_type, type: text}
          industry: {from: industry, type: optional_text, required: false}
          payment_terms_days: {from: payment_terms_days, type: decimal}
          onboarded_on: {from: onboarded_on, type: date}
  - name: engagements
    file: engagements.csv
    primary_key: [engagement_id]
    entities:
      - entity: engagement
        natural_key: "{engagement_id}"
        valid_from: started_on
        columns:
          engagement_id: {from: engagement_id, type: text}
          client_id: {from: client_id, type: text}
          name: {from: name, type: text}
          fee_basis: {from: fee_basis, type: text}
          agreed_fee: {from: agreed_fee, type: optional_decimal, required: false}
          stage: {from: stage, type: text}
          responsible_producer_id: {from: responsible_producer_id, type: optional_text, required: false}
          started_on: {from: started_on, type: date}
          due_on: {from: due_on, type: optional_date, required: false}
  - name: timekeepers
    file: timekeepers.csv
    primary_key: [timekeeper_id]
    entities:
      - entity: timekeeper
        natural_key: "{timekeeper_id}"
        valid_from: started_on
        columns:
          timekeeper_id: {from: timekeeper_id, type: text}
          grade: {from: grade, type: text}
          cost_rate: {from: cost_rate, type: decimal}
          target_hours: {from: target_hours, type: decimal}
          started_on: {from: started_on, type: date}
  - name: rate_cards
    file: rate_cards.csv
    primary_key: [rate_card_id]
    entities:
      - entity: rate_card
        natural_key: "{rate_card_id}"
        valid_from: effective_from
        columns:
          rate_card_id: {from: rate_card_id, type: text}
          name: {from: name, type: text}
          effective_from: {from: effective_from, type: date}
  - name: rate_card_lines
    file: rate_card_lines.csv
    primary_key: [rate_card_id, grade]
    entities:
      - entity: rate_card_line
        natural_key: "{rate_card_id}|{grade}"
        valid_from: effective_from
        columns:
          rate_card_id: {from: rate_card_id, type: text}
          grade: {from: grade, type: text}
          charge_rate: {from: charge_rate, type: decimal}
  - name: rate_card_assignments
    file: rate_card_assignments.csv
    primary_key: [engagement_id, effective_from]
    entities:
      - entity: rate_card_assignment
        natural_key: "{engagement_id}|{effective_from}"
        valid_from: effective_from
        columns:
          engagement_id: {from: engagement_id, type: text}
          rate_card_id: {from: rate_card_id, type: text}
          discount_pct: {from: discount_pct, type: decimal}
          effective_from: {from: effective_from, type: date}
  - name: time_entries
    file: time_entries.csv
    primary_key: [time_entry_id]
    incremental_on: work_date
    entities:
      - entity: time_entry
        natural_key: "{time_entry_id}"
        valid_from: work_date
        columns:
          time_entry_id: {from: time_entry_id, type: text}
          engagement_id: {from: engagement_id, type: text}
          timekeeper_id: {from: timekeeper_id, type: text}
          work_date: {from: work_date, type: date}
          hours: {from: hours, type: decimal}
          billable_hours: {from: billable_hours, type: decimal}
          writeoff_reason: {from: writeoff_reason, type: optional_text, required: false}
          narrative: {from: narrative, type: optional_text, required: false}
  - name: invoices
    file: invoices.csv
    primary_key: [invoice_id]
    incremental_on: issued_on
    entities:
      - entity: invoice
        natural_key: "{invoice_id}"
        valid_from: issued_on
        columns:
          invoice_id: {from: invoice_id, type: text}
          client_id: {from: client_id, type: text}
          engagement_id: {from: engagement_id, type: optional_text, required: false}
          issued_on: {from: issued_on, type: date}
          due_on: {from: due_on, type: optional_date, required: false}
          total_amount: {from: total_amount, type: decimal}
          status: {from: status, type: text}
  - name: invoice_lines
    file: invoice_lines.csv
    primary_key: [invoice_line_id]
    entities:
      - entity: invoice_line
        natural_key: "{invoice_line_id}"
        columns:
          invoice_line_id: {from: invoice_line_id, type: text}
          invoice_id: {from: invoice_id, type: text}
          engagement_id: {from: engagement_id, type: optional_text, required: false}
          line_type: {from: line_type, type: text}
          hours: {from: hours, type: optional_decimal, required: false}
          amount: {from: amount, type: decimal}
  - name: receipts
    file: receipts.csv
    primary_key: [receipt_id]
    incremental_on: received_on
    entities:
      - entity: cash_receipt
        natural_key: "{receipt_id}"
        columns:
          receipt_id: {from: receipt_id, type: text}
          client_id: {from: client_id, type: text}
          received_on: {from: received_on, type: date}
          amount: {from: amount, type: decimal}
          method: {from: method, type: optional_text, required: false}
  - name: receipt_applications
    file: receipt_applications.csv
    primary_key: [application_id]
    entities:
      - entity: receipt_application
        natural_key: "{application_id}"
        columns:
          application_id: {from: application_id, type: text}
          receipt_id: {from: receipt_id, type: text}
          invoice_id: {from: invoice_id, type: text}
          amount: {from: amount, type: decimal}
          applied_on: {from: applied_on, type: date}
  - name: cost_lines
    file: cost_lines.csv
    primary_key: [cost_line_id]
    entities:
      - entity: cost_line
        natural_key: "{cost_line_id}"
        columns:
          cost_line_id: {from: cost_line_id, type: text}
          period_start: {from: period_start, type: date}
          period_end: {from: period_end, type: date}
          category: {from: category, type: text}
          amount: {from: amount, type: decimal}
          engagement_id: {from: engagement_id, type: optional_text, required: false}
coverage:
  - {entity: client, grain: client, completeness: 0.9, manual_hours: 1}
  - {entity: engagement, grain: engagement, completeness: 0.85, manual_hours: 2}
  - {entity: timekeeper, grain: person, completeness: 0.7, manual_hours: 3}
  - {entity: rate_card, grain: card, completeness: 0.8, manual_hours: 1}
  - {entity: rate_card_line, grain: card/grade, completeness: 0.95, manual_hours: 0}
  - {entity: rate_card_assignment, grain: engagement, completeness: 0.75, manual_hours: 2}
  - {entity: time_entry, grain: entry, completeness: 0.95, manual_hours: 0}
  - {entity: invoice, grain: invoice, completeness: 0.9, manual_hours: 1}
  - {entity: invoice_line, grain: line, completeness: 0.9, manual_hours: 1}
  - {entity: cash_receipt, grain: receipt, completeness: 0.95, manual_hours: 0}
  - {entity: receipt_application, grain: application, completeness: 0.9, manual_hours: 1}
  - {entity: cost_line, grain: period/category, completeness: 0.6, manual_hours: 4}
"""

#: The (source_id, stream) pairs the raw layer needs before a load.
STREAMS: tuple[tuple[str, str], ...] = tuple(
    ("generic_csv", name)
    for name in (
        "clients", "engagements", "timekeepers", "rate_cards", "rate_card_lines",
        "rate_card_assignments", "time_entries", "invoices", "invoice_lines",
        "receipts", "receipt_applications", "cost_lines",
    )
)

#: Fee bases cycled across engagements, in an order that guarantees at least one
#: of each shape the pack has an assertion about.
_FEE_CYCLE = ("fixed", "time_and_materials", "capped", "fixed", "retainer",
              "time_and_materials")


def generate(spec: PracticeSpec, root: Path) -> GeneratedPractice:
    rng = random.Random(spec.seed)
    export = Path(root) / spec.tenant_slug / spec.firm_id
    export.mkdir(parents=True, exist_ok=True)
    months = _month_ends(spec.period_start, spec.months)
    start = spec.period_start

    clients = [
        {
            "client_id": f"CL{i:03d}",
            "name": f"Client {i:03d} Holdings",
            "client_type": "organisation" if i % 5 else "individual",
            "industry": rng.choice(["banking", "insurance", "asset_mgmt", "legal", "tech"]),
            # Varied terms so lockup is measured against what was agreed rather
            # than a firm-wide default.
            "payment_terms_days": rng.choice([14, 30, 30, 45, 60]),
            "onboarded_on": (start - dt.timedelta(days=rng.randint(60, 900))).isoformat(),
        }
        for i in range(1, spec.clients + 1)
    ]

    timekeepers = []
    for i in range(1, spec.timekeepers + 1):
        grade = GRADES[(i - 1) % len(GRADES)]
        charge, cost = GRADE_ECONOMICS[grade]
        timekeepers.append({
            "timekeeper_id": f"TK{i:03d}",
            "grade": grade,
            "cost_rate": cost,
            # One part-timer, so utilisation against an individual target is
            # visibly different from utilisation against a calendar year.
            "target_hours": 750 if i == spec.timekeepers else 1500,
            "started_on": (start - dt.timedelta(days=rng.randint(120, 2000))).isoformat(),
        })

    rate_cards = [{
        "rate_card_id": "RC2026",
        "name": "Standard 2026",
        "effective_from": (start - dt.timedelta(days=30)).isoformat(),
    }]
    rate_card_lines = [
        {
            "rate_card_id": "RC2026",
            "grade": grade,
            "charge_rate": GRADE_ECONOMICS[grade][0],
            "effective_from": (start - dt.timedelta(days=30)).isoformat(),
        }
        for grade in GRADES
    ]

    engagements, assignments = [], []
    for i in range(1, spec.engagements + 1):
        basis = _FEE_CYCLE[(i - 1) % len(_FEE_CYCLE)]
        client = clients[(i - 1) % len(clients)]
        # Agreed fee is filled in below, once the time entries exist. A fee
        # picked from a list before the hours are known is unanchored from the
        # work, and the pack then reports every fixed-fee engagement as a
        # realisation failure or a windfall depending on which way the list
        # happened to fall.
        agreed = None
        stage = "active"
        if i % 7 == 0:
            stage = "pipeline"
        elif i % 5 == 0:
            stage = "delivered"
        eid = f"ENG{i:03d}"
        engagements.append({
            "engagement_id": eid,
            "client_id": client["client_id"],
            "name": f"{client['name']} {basis.replace('_', ' ')} {i:03d}",
            "fee_basis": basis,
            "agreed_fee": agreed,
            "stage": stage,
            "responsible_producer_id": f"TK{((i - 1) % spec.timekeepers) + 1:03d}",
            "started_on": start.isoformat(),
            # Two engagements deliberately run past their date.
            "due_on": (
                (months[-1] - dt.timedelta(days=20)).isoformat() if i % 4 == 0
                else (months[-1] + dt.timedelta(days=45)).isoformat()
            ),
        })
        assignments.append({
            "engagement_id": eid,
            "rate_card_id": "RC2026",
            # A client discount on some engagements, so a thin margin can be
            # traced to discounting rather than to delivery.
            "discount_pct": rng.choice([0.0, 0.0, 0.05, 0.10, 0.15]),
            "effective_from": start.isoformat(),
        })

    time_entries: list[dict[str, Any]] = []
    entry_no = 0
    for month_end in months:
        month_start = month_end.replace(day=1)
        for eng in engagements:
            if eng["stage"] == "pipeline":
                continue  # nothing recorded against unsold work
            for tk in rng.sample(timekeepers, k=min(3, len(timekeepers))):
                days = rng.randint(2, 5)
                for _ in range(days):
                    entry_no += 1
                    work_date = month_start + dt.timedelta(
                        days=rng.randint(0, (month_end - month_start).days)
                    )
                    hours = Decimal(rng.choice(["1.5", "3.0", "4.5", "6.0", "7.5"]))
                    # One entry in six is partially written off at entry, with a
                    # reason, which is what makes the first bridge gap readable.
                    if entry_no % 6 == 0:
                        reason = WRITEOFF_REASONS[entry_no % len(WRITEOFF_REASONS)]
                        billable = (hours * Decimal(rng.choice(["0.0", "0.5", "0.75"]))
                                    ).quantize(Decimal("0.01"))
                    else:
                        reason, billable = None, hours
                    time_entries.append({
                        "time_entry_id": f"TE{entry_no:06d}",
                        "engagement_id": eng["engagement_id"],
                        "timekeeper_id": tk["timekeeper_id"],
                        "work_date": work_date.isoformat(),
                        "hours": str(hours),
                        "billable_hours": str(billable),
                        "writeoff_reason": reason,
                        "narrative": f"{eng['engagement_id']} work",
                    })

    charge_by_tk = {t["timekeeper_id"]: GRADE_ECONOMICS[t["grade"]][0] for t in timekeepers}
    discount_by_eng = {a["engagement_id"]: Decimal(str(a["discount_pct"])) for a in assignments}

    # Card value per engagement, over the whole window. A fixed fee is agreed
    # against an estimate of this, so anchoring the synthetic fee to it is what
    # makes the resulting realisation and margin land in a band a practice
    # would recognise.
    card_value: dict[str, Decimal] = {}
    for te in time_entries:
        card_value[te["engagement_id"]] = card_value.get(
            te["engagement_id"], Decimal("0")
        ) + Decimal(te["billable_hours"]) * Decimal(charge_by_tk[te["timekeeper_id"]])

    # A spread of well-priced, under-priced and over-priced fixed work, so the
    # pricing step of the bridge is signed both ways.
    _FEE_FACTORS = (Decimal("1.10"), Decimal("0.88"), Decimal("1.02"), Decimal("0.95"))
    for i, eng in enumerate(engagements):
        if eng["fee_basis"] == "time_and_materials":
            continue
        value = card_value.get(eng["engagement_id"], Decimal("0"))
        if value <= 0:
            # Pipeline work has no hours yet; price it off the median of the
            # engagements that do, so the weighted book is not zero.
            others = [v for v in card_value.values() if v > 0]
            value = (sorted(others)[len(others) // 2] if others else Decimal("40000"))
        fee = (value * _FEE_FACTORS[i % len(_FEE_FACTORS)]).quantize(Decimal("1"))
        # Rounded to the nearest thousand, the way a fee actually gets agreed.
        eng["agreed_fee"] = int((fee / 1000).quantize(Decimal("1")) * 1000) or 1000
    invoices, lines, receipts, applications = [], [], [], []
    inv_no = rec_no = app_no = line_no = 0
    for month_end in months:
        for eng in engagements:
            entitled = Decimal("0")
            hours_billed = Decimal("0")
            for te in time_entries:
                if te["engagement_id"] != eng["engagement_id"]:
                    continue
                if dt.date.fromisoformat(te["work_date"]).month != month_end.month:
                    continue
                bh = Decimal(te["billable_hours"])
                entitled += bh * Decimal(charge_by_tk[te["timekeeper_id"]])
                hours_billed += bh
            if entitled <= 0:
                continue
            entitled *= (Decimal("1") - discount_by_eng[eng["engagement_id"]])
            # A fixed, capped or retainer engagement invoices its instalment,
            # not its hours. Pricing it off hours is what a time-and-materials
            # engagement does, and conflating the two makes every fixed fee
            # look like a realisation failure rather than what it is: a price
            # agreed in advance that the hours then come in over or under.
            if eng["fee_basis"] != "time_and_materials" and eng["agreed_fee"]:
                basis_amount = (
                    Decimal(str(eng["agreed_fee"])) / Decimal(len(months))
                )
            else:
                basis_amount = entitled
            # Three shapes: under-billed, on the number, over-billed. Kept
            # narrow so realisation lands in a band a practice would recognise
            # rather than at a figure that reads as a broken model.
            factor = {0: Decimal("0.94"), 1: Decimal("1.00"), 2: Decimal("1.03")}[
                inv_no % 3
            ]
            amount = (basis_amount * factor).quantize(Decimal("0.01"))
            inv_no += 1
            iid = f"INV{inv_no:05d}"
            terms = next(
                c["payment_terms_days"] for c in clients
                if c["client_id"] == eng["client_id"]
            )
            invoices.append({
                "invoice_id": iid,
                "client_id": eng["client_id"],
                "engagement_id": eng["engagement_id"],
                "issued_on": month_end.isoformat(),
                "due_on": (month_end + dt.timedelta(days=int(terms))).isoformat(),
                "total_amount": str(amount),
                "status": "issued",
            })
            line_no += 1
            lines.append({
                "invoice_line_id": f"IL{line_no:06d}",
                "invoice_id": iid,
                "engagement_id": eng["engagement_id"],
                "line_type": "fixed_fee" if eng["fee_basis"] != "time_and_materials" else "time",
                "hours": str(hours_billed) if eng["fee_basis"] == "time_and_materials" else None,
                "amount": str(amount),
            })
            # One invoice in four is unsettled, and one in eight settles late,
            # so debtor days has a spread rather than a single value.
            if inv_no % 4 != 0:
                rec_no += 1
                lag = 55 if inv_no % 8 == 0 else rng.randint(5, 28)
                received = month_end + dt.timedelta(days=lag)
                receipts.append({
                    "receipt_id": f"RCT{rec_no:05d}",
                    "client_id": eng["client_id"],
                    "received_on": received.isoformat(),
                    "amount": str(amount),
                    "method": rng.choice(["bacs", "wire", "card"]),
                })
                app_no += 1
                applications.append({
                    "application_id": f"APP{app_no:05d}",
                    "receipt_id": f"RCT{rec_no:05d}",
                    "invoice_id": iid,
                    "amount": str(amount),
                    "applied_on": received.isoformat(),
                })

    # Costs: one attributable line per engagement per month, plus one pooled
    # line per month so the allocation check has something to reconcile.
    cost_lines = []
    cost_no = 0
    for month_end in months:
        month_start = month_end.replace(day=1)
        for eng in engagements:
            if eng["stage"] == "pipeline":
                continue
            cost_no += 1
            cost_lines.append({
                "cost_line_id": f"CST{cost_no:05d}",
                "period_start": month_start.isoformat(),
                "period_end": month_end.isoformat(),
                "category": rng.choice(["subcontractor", "travel", "software"]),
                "amount": str(rng.randint(400, 2600)),
                "engagement_id": eng["engagement_id"],
            })
        cost_no += 1
        # Pooled overhead scales with headcount, because premises, software and
        # support do. A flat figure makes a thinly utilised practice look
        # highly profitable, which is the combination this pack exists to
        # contradict.
        per_head = rng.randint(3_400, 4_400)
        cost_lines.append({
            "cost_line_id": f"CST{cost_no:05d}",
            "period_start": month_start.isoformat(),
            "period_end": month_end.isoformat(),
            "category": "premises_and_overhead",
            "amount": str(per_head * len(timekeepers)),
            "engagement_id": None,
        })

    files = {
        "clients.csv": (clients, ["client_id", "name", "client_type", "industry",
                                  "payment_terms_days", "onboarded_on"]),
        "engagements.csv": (engagements, ["engagement_id", "client_id", "name",
                                          "fee_basis", "agreed_fee", "stage",
                                          "responsible_producer_id", "started_on",
                                          "due_on"]),
        "timekeepers.csv": (timekeepers, ["timekeeper_id", "grade", "cost_rate",
                                          "target_hours", "started_on"]),
        "rate_cards.csv": (rate_cards, ["rate_card_id", "name", "effective_from"]),
        "rate_card_lines.csv": (rate_card_lines, ["rate_card_id", "grade",
                                                  "charge_rate", "effective_from"]),
        "rate_card_assignments.csv": (assignments, ["engagement_id", "rate_card_id",
                                                    "discount_pct", "effective_from"]),
        "time_entries.csv": (time_entries, ["time_entry_id", "engagement_id",
                                            "timekeeper_id", "work_date", "hours",
                                            "billable_hours", "writeoff_reason",
                                            "narrative"]),
        "invoices.csv": (invoices, ["invoice_id", "client_id", "engagement_id",
                                    "issued_on", "due_on", "total_amount", "status"]),
        "invoice_lines.csv": (lines, ["invoice_line_id", "invoice_id", "engagement_id",
                                      "line_type", "hours", "amount"]),
        "receipts.csv": (receipts, ["receipt_id", "client_id", "received_on",
                                    "amount", "method"]),
        "receipt_applications.csv": (applications, ["application_id", "receipt_id",
                                                    "invoice_id", "amount", "applied_on"]),
        "cost_lines.csv": (cost_lines, ["cost_line_id", "period_start", "period_end",
                                        "category", "amount", "engagement_id"]),
    }
    counts = {}
    for name, (rows, fields) in files.items():
        _write_csv(export / name, rows, fields)
        counts[name.removesuffix(".csv")] = len(rows)

    mapping_path = export / "mapping.yml"
    mapping_path.write_text(MAPPING)

    out = GeneratedPractice(spec=spec, export_dir=export, mapping_path=mapping_path,
                            counts=counts)
    log.info(out.summary())
    return out
