"""Redtail CRM.

Tier 1. Redtail is the better source for people: contact detail, household
composition and the advisor relationship as the firm understands it, which is
frequently not what the custodian's rep code says. The disagreement between the
two is a finding, not a bug -- `producer_crosswalk` is where it gets resolved
once, by a human, instead of being papered over at query time (spec 16).
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
from fracture.adapters.parsing import (
    as_date,
    as_datetime,
    as_decimal,
    as_text,
    last4,
    optional_text,
)
from fracture.adapters.registry import register
from fracture.core.logging import get_logger

log = get_logger("adapters.redtail")

_ROLE_MAP = {
    "head of household": "primary",
    "primary": "primary",
    "spouse": "spouse",
    "partner": "spouse",
    "child": "dependent",
    "dependent": "dependent",
    "trustee": "trustee",
}


@register
class RedtailAdapter(BaseAdapter):
    source_id = "redtail"
    vertical = "wealth"
    version = "3.0.1"

    streams = (
        Stream("contacts", ("contactId",), "updatedAt", "People and organisations"),
        Stream("families", ("familyId",), "updatedAt", "Household groupings"),
        Stream("users", ("userId",), None, "Advisors and staff"),
        Stream("activities", ("activityId",), "createdAt", "Service tickets"),
    )

    capabilities = Capabilities(
        source_id="redtail",
        vertical="wealth",
        delivery="api",
        tier=1,
        fold_in_hours=10.0,
        entities=(
            EntityCoverage("party", "person or organisation", 0.95, 1.0),
            EntityCoverage("household", "household", 0.90, 1.0, "Carries segment/tier"),
            EntityCoverage("household_member", "party/household with role", 0.95, 0.5),
            EntityCoverage("producer", "advisor or CSA", 0.85, 2.0),
            EntityCoverage("book_assignment", "producer/household", 0.65, 6.0,
                           "Current only; no effective dating in the source"),
            EntityCoverage("service_event", "ticket", 0.85, 2.0),
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
        name = f"redtail_{stream}.json"
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
            "contacts": self._map_contacts,
            "families": self._map_families,
            "users": self._map_users,
            "activities": self._map_activities,
        }.get(batch.stream)
        if handler is None:
            log.warning("redtail: no mapping for stream %s", batch.stream)
            return []
        out: list[CanonicalRecord] = []
        for index, record in enumerate(batch.records):
            out.extend(handler(record, batch.ref(index), batch.firm_id))
        return out

    def _map_contacts(self, r: dict[str, Any], ref, firm_id: str) -> list[CanonicalRecord]:
        party_id = as_text(r["contactId"], "contactId")
        since = as_date(r.get("clientSince"), "clientSince", default=dt.date(2000, 1, 1))
        kind = (optional_text(r.get("contactType")) or "individual").lower()
        party_type = {"individual": "individual", "person": "individual",
                      "business": "organisation", "organization": "organisation",
                      "organisation": "organisation", "trust": "trust"}.get(kind, "individual")
        records = [
            CanonicalRecord(
                entity="party",
                natural_key=party_id,
                firm_id=firm_id,
                values={
                    "party_id": party_id,
                    "party_type": party_type,
                    "display_name": as_text(
                        r.get("displayName")
                        or " ".join(filter(None, [r.get("firstName"), r.get("lastName")])),
                        "displayName",
                    ),
                    "legal_name": optional_text(r.get("legalName")),
                    "country": optional_text(r.get("country")) or "US",
                    # Only the last four ever crosses into canon.
                    "tax_id_last4": last4(r.get("taxId") or r.get("ssn")),
                },
                refs=(ref,),
                valid_from=since,
            )
        ]
        if r.get("familyId"):
            role = _ROLE_MAP.get((optional_text(r.get("familyRole")) or "").lower(), "other")
            records.append(
                CanonicalRecord(
                    entity="household_member",
                    natural_key=f"{r['familyId']}|{party_id}",
                    firm_id=firm_id,
                    values={
                        "household_id": as_text(r["familyId"], "familyId"),
                        "party_id": party_id,
                        "role": role,
                    },
                    refs=(ref,),
                    valid_from=since,
                )
            )
        return records

    def _map_families(self, r: dict[str, Any], ref, firm_id: str) -> list[CanonicalRecord]:
        household_id = as_text(r["familyId"], "familyId")
        since = as_date(r.get("createdAt"), "createdAt", default=dt.date(2000, 1, 1))
        records = [
            CanonicalRecord(
                entity="household",
                natural_key=household_id,
                firm_id=firm_id,
                values={
                    "household_id": household_id,
                    "name": as_text(r.get("familyName"), "familyName"),
                    "segment": optional_text(r.get("segment")),
                    "onboarded_on": since,
                },
                refs=(ref,),
                valid_from=since,
            )
        ]
        # Redtail has no effective dating on the servicing advisor. Recording it
        # as valid from the household's creation is the honest reading; the
        # capability manifest prices the historical backfill at 6 hours.
        if r.get("servicingAdvisorId"):
            records.append(
                CanonicalRecord(
                    entity="book_assignment",
                    natural_key=f"{r['servicingAdvisorId']}|{household_id}|{since.isoformat()}",
                    firm_id=firm_id,
                    values={
                        "producer_id": as_text(r["servicingAdvisorId"], "servicingAdvisorId"),
                        "household_id": household_id,
                        "split_pct": as_decimal(r.get("splitPct", 100), "splitPct"),
                    },
                    refs=(ref,),
                    valid_from=since,
                )
            )
        return records

    def _map_users(self, r: dict[str, Any], ref, firm_id: str) -> list[CanonicalRecord]:
        producer_id = as_text(r["userId"], "userId")
        hire = as_date(r.get("hireDate"), "hireDate", default=dt.date(2000, 1, 1))
        term = as_date(r["termDate"], "termDate") if r.get("termDate") else None
        records = [
            CanonicalRecord(
                entity="producer",
                natural_key=producer_id,
                firm_id=firm_id,
                values={
                    "producer_id": producer_id,
                    "display_name": as_text(r.get("name"), "name"),
                    "producer_type": (optional_text(r.get("role")) or "advisor").lower(),
                    "party_id": optional_text(r.get("contactId")),
                    "hire_date": hire,
                    "term_date": term,
                },
                refs=(ref,),
                valid_from=hire,
                valid_to=term,
            )
        ]
        if r.get("repCode"):
            records.append(
                CanonicalRecord(
                    entity="producer_crosswalk",
                    natural_key=f"redtail|{r['repCode']}",
                    firm_id=firm_id,
                    values={
                        "producer_id": producer_id,
                        "system": "redtail",
                        "external_key": as_text(r["repCode"], "repCode"),
                        "confidence": 1.0,
                        "reviewed_by": "system:redtail",
                    },
                    refs=(ref,),
                )
            )
        return records

    def _map_activities(self, r: dict[str, Any], ref, firm_id: str) -> list[CanonicalRecord]:
        activity_id = as_text(r["activityId"], "activityId")
        opened = as_datetime(r["createdAt"], "createdAt")
        closed = as_datetime(r["completedAt"], "completedAt") if r.get("completedAt") else None
        return [
            CanonicalRecord(
                entity="service_event",
                natural_key=activity_id,
                firm_id=firm_id,
                values={
                    "service_event_id": activity_id,
                    "event_type": "ticket",
                    "household_id": optional_text(r.get("familyId")),
                    "account_id": None,
                    "actor_producer_id": optional_text(r.get("ownerUserId")),
                    "opened_at": opened,
                    "closed_at": closed,
                    "sla_target_hours": (
                        as_decimal(r["slaHours"], "slaHours") if r.get("slaHours") is not None else None
                    ),
                },
                refs=(ref,),
            )
        ]
