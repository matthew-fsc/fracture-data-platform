"""Manual fee-schedule entry, treated as a source.

Spec 16, first failure mode: the firm bills from a spreadsheet the office
manager maintains. Assume this. So manual entry is not a form that writes
straight into `canon` -- it is an adapter, with an artifact in object storage, a
row hash, and the same lineage treatment as an API source. When a client asks
in month nine why their tiered breakpoint is 1.00% and not 0.95%, the answer is
a file with a timestamp and a name on it.
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
from fracture.core.errors import AdapterError
from fracture.core.logging import get_logger

log = get_logger("adapters.manual_fee_schedule")

_VALID_BASIS = {"aum", "flat", "revenue", "hours"}
_VALID_METHOD = {"tiered", "blended", "flat"}


@register
class ManualFeeScheduleAdapter(BaseAdapter):
    source_id = "manual_fee_schedule"
    vertical = "shared"
    version = "1.0.0"

    streams = (
        Stream("schedules", ("scheduleId",), None, "Fee schedules as entered"),
        Stream("tiers", ("scheduleId", "tierSeq"), None, "Breakpoints"),
        Stream("assignments", ("scheduleId", "scopeType", "scopeId"), None, "Who is on which schedule"),
    )

    capabilities = Capabilities(
        source_id="manual_fee_schedule",
        vertical="shared",
        delivery="manual",
        tier=1,
        # Not zero: someone still has to read the spreadsheet, ask the questions
        # and get sign-off. Week 2 of the fold-in runbook is always this.
        fold_in_hours=16.0,
        entities=(
            EntityCoverage("fee_schedule", "schedule", 1.0, 0.0, "Manual entry, human-signed"),
            EntityCoverage("fee_tier", "breakpoint", 1.0, 0.0),
            EntityCoverage("schedule_assignment", "household or account", 1.0, 0.0),
        ),
    )

    def stream_fields(self, stream: Stream, creds: Creds) -> list[str]:
        fields: set[str] = set()
        for record in self._records(stream.name, creds)[:200]:
            fields.update(record.keys())
        return sorted(fields)

    def row_counts(self, creds: Creds) -> dict[str, int]:
        return {s.name: len(self._records(s.name, creds)) for s in self.discover(creds)}

    def verify_read_only(self, creds: Creds) -> bool:
        """Trivially true: the source is a file a human handed us."""
        return True

    def _records(self, stream: str, creds: Creds) -> list[dict[str, Any]]:
        fs = creds_fileset(creds)
        name = f"fee_{stream}.json"
        if not fs.exists(name):
            return []
        return fs.read_json_records(name, key=stream)

    def extract(self, stream: Stream, creds: Creds, cursor: Cursor = None) -> Iterator[RecordBatch]:
        records = self._records(stream.name, creds)
        records.sort(key=lambda r: tuple(str(r.get(k, "")) for k in stream.primary_key))
        extracted_at = dt.datetime.now(dt.timezone.utc)
        if not records:
            yield self.batch(stream.name, [], extracted_at)
            return
        for i in range(0, len(records), self.batch_size):
            yield self.batch(stream.name, records[i : i + self.batch_size], extracted_at)

    def map(self, batch: RecordBatch) -> list[CanonicalRecord]:
        handler = {
            "schedules": self._map_schedules,
            "tiers": self._map_tiers,
            "assignments": self._map_assignments,
        }.get(batch.stream)
        if handler is None:
            log.warning("manual_fee_schedule: no mapping for stream %s", batch.stream)
            return []
        out: list[CanonicalRecord] = []
        for index, record in enumerate(batch.records):
            out.extend(handler(record, batch.ref(index), batch.firm_id))
        return out

    def _map_schedules(self, r: dict[str, Any], ref, firm_id: str) -> list[CanonicalRecord]:
        schedule_id = as_text(r["scheduleId"], "scheduleId")
        basis = as_text(r.get("basis"), "basis").lower()
        method = as_text(r.get("calcMethod"), "calcMethod").lower()
        if basis not in _VALID_BASIS:
            raise AdapterError(f"schedule {schedule_id}: basis {basis!r} is not one of {sorted(_VALID_BASIS)}")
        if method not in _VALID_METHOD:
            raise AdapterError(f"schedule {schedule_id}: calcMethod {method!r} is not one of {sorted(_VALID_METHOD)}")
        effective = as_date(r.get("effectiveFrom"), "effectiveFrom", default=dt.date(2000, 1, 1))
        return [
            CanonicalRecord(
                entity="fee_schedule",
                natural_key=schedule_id,
                firm_id=firm_id,
                values={
                    "schedule_id": schedule_id,
                    "name": as_text(r.get("name"), "name"),
                    "basis": basis,
                    "frequency": as_text(r.get("frequency"), "frequency").lower(),
                    "calc_method": method,
                    "billing_timing": (optional_text(r.get("billingTiming")) or "arrears").lower(),
                    "valuation_rule": (optional_text(r.get("valuationRule")) or "period_end").lower(),
                    "source_kind": "manual",
                },
                refs=(ref,),
                valid_from=effective,
                valid_to=as_date(r["effectiveTo"], "effectiveTo") if r.get("effectiveTo") else None,
            )
        ]

    def _map_tiers(self, r: dict[str, Any], ref, firm_id: str) -> list[CanonicalRecord]:
        schedule_id = as_text(r["scheduleId"], "scheduleId")
        tier_seq = int(as_decimal(r["tierSeq"], "tierSeq"))
        lower = as_decimal(r.get("lowerBound", 0), "lowerBound")
        upper = as_decimal(r["upperBound"], "upperBound") if r.get("upperBound") not in (None, "") else None
        rate = as_decimal(r["annualRateBps"], "annualRateBps") if r.get("annualRateBps") is not None else None
        flat = as_decimal(r["flatAmount"], "flatAmount") if r.get("flatAmount") is not None else None
        if rate is None and flat is None:
            raise AdapterError(
                f"schedule {schedule_id} tier {tier_seq}: neither a rate nor a flat amount; "
                "a tier that charges nothing is almost always a transcription error"
            )
        return [
            CanonicalRecord(
                entity="fee_tier",
                natural_key=f"{schedule_id}|{tier_seq}",
                firm_id=firm_id,
                values={
                    "schedule_id": schedule_id,
                    "tier_seq": tier_seq,
                    "lower_bound": lower,
                    "upper_bound": upper,
                    "annual_rate_bps": rate,
                    "flat_amount": flat,
                },
                refs=(ref,),
            )
        ]

    def _map_assignments(self, r: dict[str, Any], ref, firm_id: str) -> list[CanonicalRecord]:
        schedule_id = as_text(r["scheduleId"], "scheduleId")
        scope_type = as_text(r.get("scopeType"), "scopeType").lower()
        scope_id = as_text(r["scopeId"], "scopeId")
        effective = as_date(r.get("effectiveFrom"), "effectiveFrom", default=dt.date(2000, 1, 1))
        if scope_type not in {"household", "account"}:
            raise AdapterError(f"assignment scopeType {scope_type!r} must be household or account")
        return [
            CanonicalRecord(
                entity="schedule_assignment",
                natural_key=f"{scope_type}|{scope_id}|{schedule_id}|{effective.isoformat()}",
                firm_id=firm_id,
                values={
                    "schedule_id": schedule_id,
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                },
                refs=(ref,),
                valid_from=effective,
                valid_to=as_date(r["effectiveTo"], "effectiveTo") if r.get("effectiveTo") else None,
            )
        ]
