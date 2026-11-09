"""Schwab custodian file feed.

Tier 1, and a file drop rather than an API: a nightly positions extract and an
account master. This is the reconciliation counterparty for AUM. When Orion and
the custodian disagree, the custodian is right and the difference is the finding
that justifies the engagement.

The file also carries the master account number, which is why `last4` exists:
the full number never enters the canonical layer.
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
    schema_hash_of,
)
from fracture.adapters.base import SourceFingerprintResult
from fracture.adapters.fileset import creds_fileset
from fracture.adapters.parsing import as_date, as_decimal, as_text, optional_text
from fracture.adapters.registry import register
from fracture.core.logging import get_logger

log = get_logger("adapters.schwab")


@register
class SchwabCustodianAdapter(BaseAdapter):
    source_id = "schwab_custodian"
    vertical = "wealth"
    version = "file-1.0"

    streams = (
        Stream("accounts", ("AccountNumber",), None, "Account master file"),
        Stream("positions", ("AccountNumber", "AsOfDate"), "AsOfDate", "Nightly balances"),
        Stream("control_totals", ("AsOfDate",), "AsOfDate", "Firm-level totals for reconciliation"),
    )

    capabilities = Capabilities(
        source_id="schwab_custodian",
        vertical="wealth",
        delivery="file",
        tier=1,
        fold_in_hours=6.0,
        entities=(
            EntityCoverage("account", "account", 0.70, 3.0, "No household grouping in the file"),
            EntityCoverage("balance_snapshot", "account/day", 1.0, 0.0,
                           "Custodian of record; the reconciliation baseline"),
        ),
    )

    FILES = {
        "accounts": "schwab_accounts.csv",
        "positions": "schwab_positions.csv",
        "control_totals": "schwab_control_totals.csv",
    }

    def stream_fields(self, stream: Stream, creds: Creds) -> list[str]:
        fs = creds_fileset(creds)
        return fs.csv_fieldnames(self.FILES[stream.name])

    def row_counts(self, creds: Creds) -> dict[str, int]:
        return {s.name: len(self._rows(s.name, creds)) for s in self.discover(creds)}

    def fingerprint(self, creds: Creds) -> SourceFingerprintResult:
        """A file feed's schema is its header row.

        Hashing the header is exactly the drift alarm spec 16 asks for: a
        custodian adding a column shifts nothing, but a custodian *removing*
        one silently empties a canonical column unless this fires first.
        """
        fs = creds_fileset(creds)
        fields = {name: fs.csv_fieldnames(path) for name, path in self.FILES.items()}
        return SourceFingerprintResult(
            source_id=self.source_id,
            firm_id=self.firm_id,
            source_version=self.version,
            schema_hash=schema_hash_of(fields),
            row_counts=self.row_counts(creds),
            streams=list(self.FILES),
            read_only_verified=True,  # an SFTP drop we only ever read
            field_names=fields,
        )

    def _rows(self, stream: str, creds: Creds) -> list[dict[str, Any]]:
        fs = creds_fileset(creds)
        name = self.FILES[stream]
        if not fs.exists(name):
            return []
        return fs.read_csv(name)

    def extract(self, stream: Stream, creds: Creds, cursor: Cursor = None) -> Iterator[RecordBatch]:
        rows = self._rows(stream.name, creds)
        if cursor and stream.incremental_on:
            rows = [r for r in rows if str(r.get(stream.incremental_on, "")) > cursor]
        rows.sort(key=lambda r: tuple(str(r.get(k, "")) for k in stream.primary_key))
        extracted_at = dt.datetime.now(dt.timezone.utc)
        if not rows:
            yield self.batch(stream.name, [], extracted_at, cursor_start=cursor)
            return
        for i in range(0, len(rows), self.batch_size):
            chunk = rows[i : i + self.batch_size]
            end = max((str(r.get(stream.incremental_on, "")) for r in chunk), default=None) \
                if stream.incremental_on else None
            yield self.batch(stream.name, chunk, extracted_at, cursor_start=cursor, cursor_end=end)

    def map(self, batch: RecordBatch) -> list[CanonicalRecord]:
        if batch.stream == "accounts":
            handler = self._map_accounts
        elif batch.stream == "positions":
            handler = self._map_positions
        elif batch.stream == "control_totals":
            # Control totals are not canonical facts; they are the counterparty
            # figure the reconciliation asset compares against, loaded separately.
            return []
        else:
            log.warning("schwab_custodian: no mapping for stream %s", batch.stream)
            return []
        out: list[CanonicalRecord] = []
        for index, record in enumerate(batch.records):
            out.extend(handler(record, batch.ref(index), batch.firm_id))
        return out

    def _map_accounts(self, r: dict[str, Any], ref, firm_id: str) -> list[CanonicalRecord]:
        account_id = as_text(r["AccountNumber"], "AccountNumber")
        opened = as_date(r.get("OpenDate"), "OpenDate", default=dt.date(2000, 1, 1))
        closed = as_date(r["CloseDate"], "CloseDate") if r.get("CloseDate") else None
        return [
            CanonicalRecord(
                entity="account",
                natural_key=account_id,
                firm_id=firm_id,
                values={
                    "account_id": account_id,
                    "account_type": "custodial",
                    "account_subtype": optional_text(r.get("RegistrationType")),
                    "household_id": None,
                    "party_id": None,
                    "custodian": "schwab",
                    "opened_on": opened,
                    "closed_on": closed,
                    "status": "closed" if closed else "open",
                    # The custodian has no view of the advisory agreement, so it
                    # asserts nothing about billability. Writing True here would
                    # overwrite the portfolio system's flag and quietly pull
                    # held-away accounts into expected revenue.
                    "billable": None,
                },
                refs=(ref,),
                valid_from=opened,
                valid_to=closed,
                contribution="source",
            )
        ]

    def _map_positions(self, r: dict[str, Any], ref, firm_id: str) -> list[CanonicalRecord]:
        account_id = as_text(r["AccountNumber"], "AccountNumber")
        as_of = as_date(r["AsOfDate"], "AsOfDate")
        return [
            CanonicalRecord(
                entity="balance_snapshot",
                natural_key=f"{account_id}|{as_of.isoformat()}",
                firm_id=firm_id,
                values={
                    "account_id": account_id,
                    "as_of_date": as_of,
                    "market_value": as_decimal(r["TotalValue"], "TotalValue"),
                    "cash_value": as_decimal(r.get("CashBalance"), "CashBalance", default=as_decimal(0)),
                    "billable_value": None,
                    "currency": optional_text(r.get("Currency")) or "USD",
                },
                refs=(ref,),
                valid_from=as_of,
                valid_to=as_of + dt.timedelta(days=1),
            )
        ]

    def control_totals(self, creds: Creds) -> list[dict[str, Any]]:
        """The custodian's own reported totals, used by the recon asset."""
        return [
            {
                "as_of_date": as_date(r["AsOfDate"], "AsOfDate"),
                "total_value": as_decimal(r["TotalValue"], "TotalValue"),
                "account_count": int(r.get("AccountCount") or 0),
            }
            for r in self._rows("control_totals", creds)
        ]
