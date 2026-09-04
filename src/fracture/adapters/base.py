"""The source adapter contract (spec section 4.1).

This is the product. Everything else is plumbing that already exists, and the
fold-in cost -- therefore the margin -- is almost entirely adapter coverage.
That is why `Capabilities` is machine-readable rather than prose: the diligence
deliverable is computed from it, not written by hand.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal, Mapping, Protocol, Sequence, runtime_checkable

from fracture.core.errors import AdapterError
from fracture.core.hashing import record_hash, sha256_bytes
from fracture.ingest.lineage import RawRef

Vertical = Literal["wealth", "insurance", "accounting", "shared"]
Delivery = Literal["api", "database", "file", "manual"]

Creds = Mapping[str, Any]
Cursor = str | None


# -- capability manifest -----------------------------------------------------


@dataclass(frozen=True)
class EntityCoverage:
    """What one adapter can populate, at what grain, and how complete it is.

    `completeness` is the fraction of the canonical entity's material columns
    this source can fill. `manual_hours` is what it costs to close the rest.
    Both are estimates, but they are *stated* estimates, which is what makes a
    fold-in quote defensible and a scope change a change order (spec 16).
    """

    entity: str
    grain: str
    completeness: float
    manual_hours: float = 0.0
    notes: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.completeness <= 1.0:
            raise AdapterError(
                f"completeness for {self.entity} must be between 0 and 1, got {self.completeness}"
            )
        if self.manual_hours < 0:
            raise AdapterError(f"manual_hours for {self.entity} cannot be negative")


@dataclass(frozen=True)
class Capabilities:
    source_id: str
    vertical: Vertical
    delivery: Delivery
    entities: tuple[EntityCoverage, ...]
    #: Baseline hours to stand this adapter up against a new firm's instance.
    fold_in_hours: float = 8.0
    #: True when the adapter reads a file drop rather than an API. Spec 1.2
    #: assumes ~40% of sources arrive this way.
    tier: int = 1

    def entity_names(self) -> frozenset[str]:
        return frozenset(e.entity for e in self.entities)

    def coverage_for(self, entity: str) -> EntityCoverage | None:
        for e in self.entities:
            if e.entity == entity:
                return e
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "vertical": self.vertical,
            "delivery": self.delivery,
            "tier": self.tier,
            "fold_in_hours": self.fold_in_hours,
            "entities": [
                {
                    "entity": e.entity, "grain": e.grain, "completeness": e.completeness,
                    "manual_hours": e.manual_hours, "notes": e.notes,
                }
                for e in self.entities
            ],
        }


# -- extraction primitives ---------------------------------------------------


@dataclass(frozen=True)
class Stream:
    """One extractable collection from a source."""

    name: str
    primary_key: tuple[str, ...]
    incremental_on: str | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if not self.primary_key:
            raise AdapterError(f"stream {self.name!r} must declare a primary key")


@dataclass
class RecordBatch:
    """A batch of raw records, plus the context needed to store and trace them."""

    stream: str
    firm_id: str
    source_id: str
    records: list[dict[str, Any]]
    extracted_at: dt.datetime
    cursor_start: Cursor = None
    cursor_end: Cursor = None
    #: Assigned by the loader once the batch is in `raw`. Mapping without it
    #: would produce canonical rows that cannot be traced, so `ref()` refuses.
    load_id: uuid.UUID | None = None

    def ref(self, index: int) -> RawRef:
        if self.load_id is None:
            raise AdapterError(
                "batch has not been loaded yet; map() must run after the raw load "
                "so that lineage references resolve"
            )
        return RawRef(load_id=self.load_id, sequence=index + 1)

    def __len__(self) -> int:
        return len(self.records)


@dataclass
class CanonicalRecord:
    """One canonical row with its lineage attached.

    `refs` is not optional. A canonical record with no raw references is a
    number nobody can open, and the mapper rejects it.
    """

    entity: str
    natural_key: str
    firm_id: str
    values: dict[str, Any]
    refs: tuple[RawRef, ...]
    #: Which source produced this version. Fan-in precedence needs it; a record
    #: with no provenance cannot be arbitrated against another source.
    source_id: str = "unknown"
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    contribution: str = "source"
    ai_proposal_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        if not self.refs:
            raise AdapterError(
                f"canonical record {self.entity}/{self.natural_key} carries no lineage refs"
            )


@dataclass(frozen=True)
class SourceFingerprintResult:
    """Cheap, read-only, safe against production (spec 4.1)."""

    source_id: str
    firm_id: str
    source_version: str | None
    schema_hash: bytes
    row_counts: dict[str, int] = field(default_factory=dict)
    streams: list[str] = field(default_factory=list)
    read_only_verified: bool = False
    field_names: dict[str, list[str]] = field(default_factory=dict)


@runtime_checkable
class SourceAdapter(Protocol):
    source_id: str
    vertical: Vertical
    capabilities: Capabilities

    def fingerprint(self, creds: Creds) -> SourceFingerprintResult: ...
    def discover(self, creds: Creds) -> list[Stream]: ...
    def extract(self, stream: Stream, creds: Creds, cursor: Cursor = None) -> Iterator[RecordBatch]: ...
    def map(self, batch: RecordBatch) -> list[CanonicalRecord]: ...


class BaseAdapter:
    """Shared machinery. Adapters subclass this; the Protocol is what callers see."""

    source_id: str = ""
    vertical: Vertical = "shared"
    version: str = "0.1.0"
    capabilities: Capabilities

    #: Streams the adapter can produce. `discover` narrows this to what the
    #: specific instance actually exposes.
    streams: tuple[Stream, ...] = ()

    def __init__(self, firm_id: str, batch_size: int = 5000) -> None:
        if not self.source_id:
            raise AdapterError(f"{type(self).__name__} does not declare a source_id")
        self.firm_id = firm_id
        self.batch_size = batch_size

    # -- contract ----------------------------------------------------------

    def discover(self, creds: Creds) -> list[Stream]:
        return list(self.streams)

    def fingerprint(self, creds: Creds) -> SourceFingerprintResult:
        """Default fingerprint: hash the declared field names of each stream.

        Adapters over live systems override this to hash the *observed* schema,
        which is what makes drift detection (spec 16) real rather than a
        restatement of the code.
        """
        streams = self.discover(creds)
        fields = {s.name: sorted(self.stream_fields(s, creds)) for s in streams}
        return SourceFingerprintResult(
            source_id=self.source_id,
            firm_id=self.firm_id,
            source_version=self.version,
            schema_hash=schema_hash_of(fields),
            row_counts=self.row_counts(creds),
            streams=[s.name for s in streams],
            read_only_verified=self.verify_read_only(creds),
            field_names=fields,
        )

    def stream_fields(self, stream: Stream, creds: Creds) -> list[str]:
        raise NotImplementedError

    def row_counts(self, creds: Creds) -> dict[str, int]:
        return {}

    def verify_read_only(self, creds: Creds) -> bool:
        """Whether the issued credential is demonstrably read-only.

        The default is False, not True. Spec 1.2: you verify and document it;
        you do not control it. An adapter that cannot prove it says so.
        """
        return bool(creds.get("read_only") is True)

    def extract(self, stream: Stream, creds: Creds, cursor: Cursor = None) -> Iterator[RecordBatch]:
        raise NotImplementedError

    def map(self, batch: RecordBatch) -> list[CanonicalRecord]:
        raise NotImplementedError

    # -- helpers -----------------------------------------------------------

    def batch(
        self,
        stream: str,
        records: Sequence[dict[str, Any]],
        extracted_at: dt.datetime | None = None,
        cursor_start: Cursor = None,
        cursor_end: Cursor = None,
    ) -> RecordBatch:
        return RecordBatch(
            stream=stream,
            firm_id=self.firm_id,
            source_id=self.source_id,
            records=list(records),
            extracted_at=extracted_at or dt.datetime.now(dt.timezone.utc),
            cursor_start=cursor_start,
            cursor_end=cursor_end,
        )

    def chunked(
        self, stream: str, records: Sequence[dict[str, Any]], extracted_at: dt.datetime | None = None
    ) -> Iterator[RecordBatch]:
        for i in range(0, len(records), self.batch_size) or [0]:
            yield self.batch(stream, records[i : i + self.batch_size], extracted_at)
        if not records:
            yield self.batch(stream, [], extracted_at)


def schema_hash_of(fields: Mapping[str, Sequence[str]]) -> bytes:
    """Stable hash of a source's shape: stream name -> sorted field names."""
    normalised = {k: sorted(v) for k, v in sorted(fields.items())}
    return record_hash(normalised)


def diff_schema(
    previous: Mapping[str, Sequence[str]], current: Mapping[str, Sequence[str]]
) -> tuple[list[str], list[str]]:
    """(added, removed) as `stream.field` strings."""
    def flat(m: Mapping[str, Sequence[str]]) -> set[str]:
        return {f"{stream}.{field}" for stream, fields in m.items() for field in fields}

    prev, curr = flat(previous), flat(current)
    return sorted(curr - prev), sorted(prev - curr)
