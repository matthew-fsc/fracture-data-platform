"""QuickBooks Online: what was billed, what was collected, what it cost.

Tier 1. QBO closes the loop that makes leakage measurable. Expected revenue
comes from the fee schedule; billed comes from here; collected comes from here.
Without this adapter the unbilled and leakage findings do not exist, which is
why it is tier 1 despite not being a wealth system.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Iterator

from fracture.adapters.base import (
    BaseAdapter,
    Capabilities,
    CanonicalRecord,
    Creds,
    Cursor,
    EntityCoverage,
    RecordBatch,
    Stream,
)
from fracture.adapters.fileset import creds_fileset
from fracture.adapters.parsing import as_date, as_decimal, as_text, optional_text
from fracture.adapters.registry import register
from fracture.core.logging import get_logger

log = get_logger("adapters.qbo")


@register
class QuickBooksOnlineAdapter(BaseAdapter):
    source_id = "qbo"
    vertical = "accounting"
    version = "v3"

    streams = (
        Stream("invoices", ("invoiceId",), "txnDate", "Customer invoices with lines"),
        Stream("payments", ("paymentId",), "txnDate", "Receipts and their applications"),
        Stream("expenses", ("expenseId",), "txnDate", "Vendor and payroll costs"),
        Stream("time_entries", ("entryId",), "entryDate", "Staff time for loaded margin"),
    )

    capabilities = Capabilities(
        source_id="qbo",
        vertical="accounting",
        delivery="api",
        tier=1,
        fold_in_hours=10.0,
        entities=(
            EntityCoverage("invoice", "invoice", 0.95, 1.0),
            EntityCoverage("invoice_line", "invoice line", 0.90, 2.0,
                           "Line-to-revenue-event link depends on the memo convention"),
            EntityCoverage("cash_receipt", "receipt", 0.95, 0.5),
            EntityCoverage("receipt_application", "receipt/invoice", 0.90, 1.0),
            EntityCoverage("cost_line", "cost", 0.80, 4.0, "Allocation basis is a firm decision"),
            EntityCoverage("fte_allocation", "person/period", 0.60, 8.0,
                           "Only where the firm actually tracks time"),
            EntityCoverage("revenue_event", "billed line", 0.55, 6.0,
                           "Billed, not accrued; the accrual comes from the fee schedule"),
        ),
    )

    def stream_fields(self, stream: Stream, creds: Creds) -> list[str]:
        fields: set[str] = set()
        for record in self._records(stream.name, creds)[:200]:
            fields.update(record.keys())
        return sorted(fields)

    def row_counts(self, creds: Creds) -> dict[str, int]:
        return {s.name: len(self._records(s.name, creds)) for s in self.discover(creds)}

    def _records(self, stream: str, creds: Creds) -> list[dict[str, Any]]:
        fs = creds_fileset(creds)
        name = f"qbo_{stream}.json"
        if not fs.exists(name):
            return []
        return fs.read_json_records(name, key=stream)

    def extract(self, stream: Stream, creds: Creds, cursor: Cursor = None) -> Iterator[RecordBatch]:
        records = self._records(stream.name, creds)
        if cursor and stream.incremental_on:
            records = [r for r in records if str(r.get(stream.incremental_on, "")) > cursor]
        records.sort(key=lambda r: tuple(str(r.get(k, "")) for k in stream.primary_key))
        extracted_at = dt.datetime.now(dt.timezone.utc)
        if not records:
            yield self.batch(stream.name, [], extracted_at, cursor_start=cursor)
            return
        for i in range(0, len(records), self.batch_size):
            chunk = records[i : i + self.batch_size]
            end = max((str(r.get(stream.incremental_on, "")) for r in chunk), default=None) \
                if stream.incremental_on else None
            yield self.batch(stream.name, chunk, extracted_at, cursor_start=cursor, cursor_end=end)

    def map(self, batch: RecordBatch) -> list[CanonicalRecord]:
        handler = {
            "invoices": self._map_invoices,
            "payments": self._map_payments,
            "expenses": self._map_expenses,
            "time_entries": self._map_time_entries,
        }.get(batch.stream)
        if handler is None:
            log.warning("qbo: no mapping for stream %s", batch.stream)
            return []
        out: list[CanonicalRecord] = []
        for index, record in enumerate(batch.records):
            out.extend(handler(record, batch.ref(index), batch.firm_id))
        return out

    def _map_invoices(self, r: dict[str, Any], ref, firm_id: str) -> list[CanonicalRecord]:
        invoice_id = as_text(r["invoiceId"], "invoiceId")
        issued = as_date(r["txnDate"], "txnDate")
        lines = r.get("lines") or []
        line_total = sum(
            (as_decimal(l.get("amount"), "line.amount", default=as_decimal(0)) for l in lines),
            as_decimal(0),
        )
        header_total = as_decimal(r.get("totalAmount"), "totalAmount", default=line_total)
        if lines and header_total != line_total:
            # A header that disagrees with its lines means one of the two is
            # wrong, and picking either silently is how billed revenue drifts.
            raise ValueError(
                f"qbo invoice {invoice_id}: header total {header_total} does not equal "
                f"the sum of its lines {line_total}"
            )
        records = [
            CanonicalRecord(
                entity="invoice",
                natural_key=invoice_id,
                firm_id=firm_id,
                values={
                    "invoice_id": invoice_id,
                    "household_id": optional_text(r.get("customerRef")),
                    "issued_on": issued,
                    "due_on": as_date(r["dueDate"], "dueDate") if r.get("dueDate") else None,
                    "period_start": as_date(r["periodStart"], "periodStart") if r.get("periodStart") else None,
                    "period_end": as_date(r["periodEnd"], "periodEnd") if r.get("periodEnd") else None,
                    "total_amount": header_total,
                    "currency": optional_text(r.get("currency")) or "USD",
                    "status": (optional_text(r.get("status")) or "open").lower(),
                },
                refs=(ref,),
                valid_from=issued,
            )
        ]
        for line_no, line in enumerate(lines, start=1):
            records.append(
                CanonicalRecord(
                    entity="invoice_line",
                    natural_key=f"{invoice_id}|{line_no}",
                    firm_id=firm_id,
                    values={
                        "invoice_id": invoice_id,
                        "line_no": line_no,
                        "revenue_event_id": optional_text(line.get("revenueEventId")),
                        "account_id": optional_text(line.get("accountRef")),
                        "description": optional_text(line.get("description")),
                        "amount": as_decimal(line.get("amount"), "line.amount"),
                    },
                    refs=(ref,),
                    valid_from=issued,
                )
            )
            # The billed figure is also a revenue event: origin='source'
            # distinguishes it from the accrual the fee schedule computes.
            records.append(
                CanonicalRecord(
                    entity="revenue_event",
                    natural_key=f"BILLED-{invoice_id}-{line_no}",
                    firm_id=firm_id,
                    values={
                        "revenue_event_id": f"BILLED-{invoice_id}-{line_no}",
                        "event_type": "fee_accrual",
                        "household_id": optional_text(r.get("customerRef")),
                        "account_id": optional_text(line.get("accountRef")),
                        "producer_id": None,
                        "period_start": as_date(r["periodStart"], "periodStart") if r.get("periodStart") else issued,
                        "period_end": as_date(r["periodEnd"], "periodEnd") if r.get("periodEnd") else issued,
                        "basis_amount": (
                            as_decimal(line["basisAmount"], "basisAmount")
                            if line.get("basisAmount") is not None else None
                        ),
                        "amount": as_decimal(line.get("amount"), "line.amount"),
                        "currency": optional_text(r.get("currency")) or "USD",
                        "origin": "source",
                    },
                    refs=(ref,),
                    valid_from=issued,
                )
            )
        return records

    def _map_payments(self, r: dict[str, Any], ref, firm_id: str) -> list[CanonicalRecord]:
        receipt_id = as_text(r["paymentId"], "paymentId")
        received = as_date(r["txnDate"], "txnDate")
        records = [
            CanonicalRecord(
                entity="cash_receipt",
                natural_key=receipt_id,
                firm_id=firm_id,
                values={
                    "receipt_id": receipt_id,
                    "household_id": optional_text(r.get("customerRef")),
                    "received_on": received,
                    "amount": as_decimal(r["totalAmount"], "totalAmount"),
                    "method": optional_text(r.get("paymentMethod")),
                },
                refs=(ref,),
            )
        ]
        for application in r.get("applications") or []:
            records.append(
                CanonicalRecord(
                    entity="receipt_application",
                    natural_key=f"{receipt_id}|{application['invoiceId']}",
                    firm_id=firm_id,
                    values={
                        "receipt_id": receipt_id,
                        "invoice_id": as_text(application["invoiceId"], "invoiceId"),
                        "amount_applied": as_decimal(application["amount"], "application.amount"),
                        "applied_on": received,
                    },
                    refs=(ref,),
                )
            )
        return records

    def _map_expenses(self, r: dict[str, Any], ref, firm_id: str) -> list[CanonicalRecord]:
        cost_id = as_text(r["expenseId"], "expenseId")
        period_start = as_date(r["txnDate"], "txnDate")
        return [
            CanonicalRecord(
                entity="cost_line",
                natural_key=cost_id,
                firm_id=firm_id,
                values={
                    "cost_id": cost_id,
                    "period_start": period_start,
                    "period_end": as_date(r["periodEnd"], "periodEnd") if r.get("periodEnd") else period_start,
                    "category": (optional_text(r.get("category")) or "vendor").lower(),
                    "vendor": optional_text(r.get("vendorRef")),
                    "person_id": optional_text(r.get("employeeRef")),
                    "amount": as_decimal(r["amount"], "amount"),
                    "allocation_basis": (optional_text(r.get("allocationBasis")) or "revenue").lower(),
                },
                refs=(ref,),
            )
        ]

    def _map_time_entries(self, r: dict[str, Any], ref, firm_id: str) -> list[CanonicalRecord]:
        entry_date = as_date(r["entryDate"], "entryDate")
        return [
            CanonicalRecord(
                entity="fte_allocation",
                natural_key=as_text(r["entryId"], "entryId"),
                firm_id=firm_id,
                values={
                    "person_id": as_text(r["employeeRef"], "employeeRef"),
                    "producer_id": optional_text(r.get("producerRef")),
                    "household_id": optional_text(r.get("customerRef")),
                    "period_start": entry_date,
                    "period_end": entry_date,
                    "hours": as_decimal(r["hours"], "hours"),
                    "hourly_cost": as_decimal(r["hourlyCost"], "hourlyCost"),
                },
                refs=(ref,),
            )
        ]
