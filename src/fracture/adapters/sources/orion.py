"""Orion Advisor Technology: portfolio accounting.

Tier 1 (spec 4.3). Orion is the system of record for accounts, positions and
the rep code that owns them, which makes it the primary source for AUM and for
`book_assignment` on the custodial side. It is not a source of truth for fee
schedules -- those are in the billing module and, in practice, in a spreadsheet.

The extraction here reads recorded API pages from an export directory. A live
deployment swaps `_pages()` for paged HTTP GETs against the same shapes; nothing
downstream changes, which is the point of the adapter contract.
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

log = get_logger("adapters.orion")


@register
class OrionAdapter(BaseAdapter):
    source_id = "orion"
    vertical = "wealth"
    version = "1.2.0"

    streams = (
        Stream("households", ("householdId",), "updatedAt", "Client household groupings"),
        Stream("accounts", ("accountId",), "updatedAt", "Custodial accounts"),
        Stream("positions", ("accountId", "asOfDate"), "asOfDate", "Daily account values"),
        Stream("representatives", ("repId",), None, "Advisors and their rep codes"),
        Stream("service_requests", ("requestId",), "openedAt", "Transfers, onboarding, trades"),
    )

    capabilities = Capabilities(
        source_id="orion",
        vertical="wealth",
        delivery="api",
        tier=1,
        fold_in_hours=12.0,
        entities=(
            EntityCoverage("household", "household", 0.85, 2.0, "No segment; derived downstream"),
            EntityCoverage("party", "person", 0.70, 4.0, "Contact detail is thin; CRM is better"),
            EntityCoverage("household_member", "party/household", 0.75, 2.0),
            EntityCoverage("producer", "advisor", 0.90, 1.0, "Rep code is the join key"),
            EntityCoverage("book_assignment", "producer/household, effective dated", 0.80, 4.0,
                           "Splits present; historical transitions need a backfill"),
            EntityCoverage("account", "account", 0.95, 1.0),
            EntityCoverage("balance_snapshot", "account/day", 1.0, 0.0,
                           "Daily values, the AUM spine"),
            EntityCoverage("service_event", "request", 0.70, 3.0,
                           "Transfers and onboarding; tickets live in the CRM"),
        ),
    )

    # -- extraction --------------------------------------------------------

    def stream_fields(self, stream: Stream, creds: Creds) -> list[str]:
        records = self._records(stream.name, creds)
        fields: set[str] = set()
        for record in records[:200]:
            fields.update(record.keys())
        return sorted(fields)

    def row_counts(self, creds: Creds) -> dict[str, int]:
        return {s.name: len(self._records(s.name, creds)) for s in self.discover(creds)}

    def _records(self, stream: str, creds: Creds) -> list[dict[str, Any]]:
        fs = creds_fileset(creds)
        name = f"orion_{stream}.json"
        if not fs.exists(name):
            return []
        return fs.read_json_records(name, key=stream)

    def extract(self, stream: Stream, creds: Creds, cursor: Cursor = None) -> Iterator[RecordBatch]:
        records = self._records(stream.name, creds)
        if cursor and stream.incremental_on:
            # Never paginate past cursor semantics: strictly greater than, so a
            # record written in the same second as the cursor is not skipped by
            # an off-by-one that only shows up at month end.
            records = [r for r in records if str(r.get(stream.incremental_on, "")) > cursor]
        records.sort(key=lambda r: tuple(str(r.get(k, "")) for k in stream.primary_key))
        extracted_at = dt.datetime.now(dt.timezone.utc)
        if not records:
            yield self.batch(stream.name, [], extracted_at, cursor_start=cursor)
            return
        for i in range(0, len(records), self.batch_size):
            chunk = records[i : i + self.batch_size]
            end = None
            if stream.incremental_on:
                end = max(str(r.get(stream.incremental_on, "")) for r in chunk)
            yield self.batch(stream.name, chunk, extracted_at, cursor_start=cursor, cursor_end=end)

    # -- mapping -----------------------------------------------------------

    def map(self, batch: RecordBatch) -> list[CanonicalRecord]:
        handler = {
            "households": self._map_households,
            "accounts": self._map_accounts,
            "positions": self._map_positions,
            "representatives": self._map_representatives,
            "service_requests": self._map_service_requests,
        }.get(batch.stream)
        if handler is None:
            log.warning("orion: no mapping for stream %s", batch.stream)
            return []
        out: list[CanonicalRecord] = []
        for index, record in enumerate(batch.records):
            out.extend(handler(record, batch.ref(index), batch.firm_id))
        return out

    def _map_households(self, r: dict[str, Any], ref, firm_id: str) -> list[CanonicalRecord]:
        household_id = as_text(r["householdId"], "householdId")
        opened = as_date(r.get("createdOn"), "createdOn", default=dt.date(2000, 1, 1))
        return [
            CanonicalRecord(
                entity="household",
                natural_key=household_id,
                firm_id=firm_id,
                values={
                    "household_id": household_id,
                    "name": as_text(r.get("name"), "name"),
                    "segment": optional_text(r.get("tier")),
                    "onboarded_on": opened,
                },
                refs=(ref,),
                valid_from=opened,
            )
        ]

    def _map_accounts(self, r: dict[str, Any], ref, firm_id: str) -> list[CanonicalRecord]:
        account_id = as_text(r["accountId"], "accountId")
        opened = as_date(r.get("openedOn"), "openedOn", default=dt.date(2000, 1, 1))
        closed = as_date(r["closedOn"], "closedOn") if r.get("closedOn") else None
        records = [
            CanonicalRecord(
                entity="account",
                natural_key=account_id,
                firm_id=firm_id,
                values={
                    "account_id": account_id,
                    "account_type": "custodial",
                    "account_subtype": optional_text(r.get("registrationType")),
                    "household_id": optional_text(r.get("householdId")),
                    "party_id": optional_text(r.get("primaryContactId")),
                    "custodian": optional_text(r.get("custodian")),
                    "opened_on": opened,
                    "closed_on": closed,
                    "status": "closed" if closed else "open",
                    # Non-billable registrations (held-away, 529 outside the
                    # agreement) must not inflate expected revenue.
                    "billable": bool(r.get("billable", True)),
                },
                refs=(ref,),
                valid_from=opened,
                valid_to=closed,
            )
        ]
        if r.get("primaryContactId"):
            party_id = as_text(r["primaryContactId"], "primaryContactId")
            records.append(
                CanonicalRecord(
                    entity="party",
                    natural_key=party_id,
                    firm_id=firm_id,
                    values={
                        "party_id": party_id,
                        "party_type": "individual",
                        "display_name": as_text(r.get("primaryContactName"), "primaryContactName"),
                        "legal_name": optional_text(r.get("primaryContactName")),
                        "country": optional_text(r.get("country")) or "US",
                        "tax_id_last4": last4(r.get("taxId")),
                    },
                    refs=(ref,),
                    valid_from=opened,
                )
            )
            if r.get("householdId"):
                records.append(
                    CanonicalRecord(
                        entity="household_member",
                        natural_key=f"{r['householdId']}|{party_id}",
                        firm_id=firm_id,
                        values={
                            "household_id": as_text(r["householdId"], "householdId"),
                            "party_id": party_id,
                            "role": "primary",
                        },
                        refs=(ref,),
                        valid_from=opened,
                    )
                )
        return records

    def _map_positions(self, r: dict[str, Any], ref, firm_id: str) -> list[CanonicalRecord]:
        account_id = as_text(r["accountId"], "accountId")
        as_of = as_date(r["asOfDate"], "asOfDate")
        market_value = as_decimal(r["marketValue"], "marketValue")
        billable_raw = r.get("billableValue")
        return [
            CanonicalRecord(
                entity="balance_snapshot",
                natural_key=f"{account_id}|{as_of.isoformat()}",
                firm_id=firm_id,
                values={
                    "account_id": account_id,
                    "as_of_date": as_of,
                    "market_value": market_value,
                    "cash_value": as_decimal(r.get("cashValue"), "cashValue", default=as_decimal(0)),
                    "billable_value": (
                        as_decimal(billable_raw, "billableValue") if billable_raw is not None else None
                    ),
                    "currency": optional_text(r.get("currency")) or "USD",
                },
                refs=(ref,),
                valid_from=as_of,
                valid_to=as_of + dt.timedelta(days=1),
            )
        ]

    def _map_representatives(self, r: dict[str, Any], ref, firm_id: str) -> list[CanonicalRecord]:
        producer_id = as_text(r["repId"], "repId")
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
                    "producer_type": "advisor",
                    "party_id": optional_text(r.get("partyId")),
                    "hire_date": hire,
                    "term_date": term,
                },
                refs=(ref,),
                valid_from=hire,
                valid_to=term,
            )
        ]
        # The rep code is the key the custodian file carries. Persisting it as a
        # crosswalk row is what stops fuzzy name matching at query time (spec 16).
        if r.get("repCode"):
            records.append(
                CanonicalRecord(
                    entity="producer_crosswalk",
                    natural_key=f"orion|{r['repCode']}",
                    firm_id=firm_id,
                    values={
                        "producer_id": producer_id,
                        "system": "orion",
                        "external_key": as_text(r["repCode"], "repCode"),
                        "confidence": 1.0,
                        "reviewed_by": "system:orion",
                    },
                    refs=(ref,),
                )
            )
        for assignment in r.get("assignments", []) or []:
            start = as_date(assignment.get("effectiveFrom"), "effectiveFrom", default=hire)
            end = as_date(assignment["effectiveTo"], "effectiveTo") if assignment.get("effectiveTo") else None
            records.append(
                CanonicalRecord(
                    entity="book_assignment",
                    natural_key=f"{producer_id}|{assignment['householdId']}|{start.isoformat()}",
                    firm_id=firm_id,
                    values={
                        "producer_id": producer_id,
                        "household_id": as_text(assignment["householdId"], "householdId"),
                        "split_pct": as_decimal(
                            assignment.get("splitPct", 100), "splitPct"
                        ),
                    },
                    refs=(ref,),
                    valid_from=start,
                    valid_to=end,
                )
            )
        return records

    def _map_service_requests(self, r: dict[str, Any], ref, firm_id: str) -> list[CanonicalRecord]:
        request_id = as_text(r["requestId"], "requestId")
        opened = as_datetime(r["openedAt"], "openedAt")
        closed = as_datetime(r["closedAt"], "closedAt") if r.get("closedAt") else None
        return [
            CanonicalRecord(
                entity="service_event",
                natural_key=request_id,
                firm_id=firm_id,
                values={
                    "service_event_id": request_id,
                    "event_type": as_text(r.get("requestType"), "requestType"),
                    "household_id": optional_text(r.get("householdId")),
                    "account_id": optional_text(r.get("accountId")),
                    "actor_producer_id": optional_text(r.get("assignedRepId")),
                    "opened_at": opened,
                    "closed_at": closed,
                    "sla_target_hours": (
                        as_decimal(r["slaHours"], "slaHours") if r.get("slaHours") is not None else None
                    ),
                },
                refs=(ref,),
            )
        ]
