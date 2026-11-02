"""Generic CSV / SFTP adapter with a declarative column-mapping config.

Worth more than any single named integration (spec 4.3): half of tier 1 in
practice arrives as a nightly file. A new file-drop source is a YAML file, not a
Python module, which is the difference between a two-hour fold-in task and a
two-day one.

The mapping config is data, so it is versionable, diffable and reviewable by the
person who actually knows what the columns mean.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import yaml

from fracture.adapters.base import (
    BaseAdapter,
    Capabilities,
    CanonicalRecord,
    Creds,
    Cursor,
    EntityCoverage,
    RecordBatch,
    SourceFingerprintResult,
    Stream,
    schema_hash_of,
)
from fracture.adapters.fileset import creds_fileset
from fracture.adapters.parsing import (
    as_bool,
    as_date,
    as_datetime,
    as_decimal,
    as_text,
    last4,
    optional_text,
)
from fracture.adapters.registry import register
from fracture.core.errors import AdapterError
from fracture.core.logging import get_logger

log = get_logger("adapters.generic_csv")

#: Column types the config may declare. Anything else is a config error caught
#: at load time rather than a silent str passed into a numeric column.
COERCERS = {
    "text": lambda v, f: as_text(v, f),
    "optional_text": lambda v, f: optional_text(v),
    "decimal": lambda v, f: as_decimal(v, f),
    "optional_decimal": lambda v, f: as_decimal(v, f) if v not in (None, "") else None,
    "date": lambda v, f: as_date(v, f),
    "optional_date": lambda v, f: as_date(v, f) if v not in (None, "") else None,
    "timestamp": lambda v, f: as_datetime(v, f),
    "optional_timestamp": lambda v, f: as_datetime(v, f) if v not in (None, "") else None,
    "bool": lambda v, f: as_bool(v),
    "integer": lambda v, f: int(as_decimal(v, f)),
    "last4": lambda v, f: last4(v),
}


@dataclass(frozen=True)
class ColumnMapping:
    target: str
    source: str | None = None
    type: str = "text"
    constant: Any = None
    default: Any = None
    required: bool = True

    def resolve(self, row: dict[str, Any]) -> Any:
        if self.constant is not None:
            return self.constant
        if self.source is None:
            raise AdapterError(f"column {self.target}: needs either `from` or `constant`")
        if self.source not in row:
            # A missing column is drift, not an empty value. Failing here is the
            # entire reason fingerprint() exists.
            raise AdapterError(
                f"column {self.source!r} is absent from the file; "
                "the source's schema has changed"
            )
        raw = row.get(self.source)
        if raw in (None, "") and self.default is not None:
            raw = self.default
        if raw in (None, "") and not self.required:
            return None
        coercer = COERCERS.get(self.type)
        if coercer is None:
            raise AdapterError(f"unknown column type {self.type!r} for {self.target}")
        return coercer(raw, f"{self.source}")


@dataclass(frozen=True)
class EntityMapping:
    entity: str
    natural_key: str
    columns: tuple[ColumnMapping, ...]
    valid_from: str | None = None
    valid_to: str | None = None
    filter_column: str | None = None
    filter_equals: str | None = None

    def applies(self, row: dict[str, Any]) -> bool:
        if self.filter_column is None:
            return True
        return str(row.get(self.filter_column, "")).strip() == self.filter_equals


@dataclass(frozen=True)
class StreamConfig:
    name: str
    file: str
    primary_key: tuple[str, ...]
    entities: tuple[EntityMapping, ...]
    delimiter: str = ","
    incremental_on: str | None = None

    def to_stream(self) -> Stream:
        return Stream(self.name, self.primary_key, self.incremental_on, f"CSV: {self.file}")


@dataclass
class CsvMappingConfig:
    source_id: str
    label: str
    streams: tuple[StreamConfig, ...]
    vertical: str = "shared"
    fold_in_hours: float = 4.0
    coverage: tuple[EntityCoverage, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CsvMappingConfig":
        streams = []
        for s in data.get("streams", []):
            entities = []
            for e in s.get("entities", []):
                columns = tuple(
                    ColumnMapping(
                        target=target,
                        source=spec.get("from") if isinstance(spec, dict) else spec,
                        type=spec.get("type", "text") if isinstance(spec, dict) else "text",
                        constant=spec.get("constant") if isinstance(spec, dict) else None,
                        default=spec.get("default") if isinstance(spec, dict) else None,
                        required=spec.get("required", True) if isinstance(spec, dict) else True,
                    )
                    for target, spec in e.get("columns", {}).items()
                )
                if not columns:
                    raise AdapterError(f"entity mapping for {e.get('entity')!r} has no columns")
                entities.append(
                    EntityMapping(
                        entity=e["entity"],
                        natural_key=e["natural_key"],
                        columns=columns,
                        valid_from=e.get("valid_from"),
                        valid_to=e.get("valid_to"),
                        filter_column=e.get("filter_column"),
                        filter_equals=e.get("filter_equals"),
                    )
                )
            if not entities:
                raise AdapterError(f"stream {s.get('name')!r} maps to no canonical entity")
            streams.append(
                StreamConfig(
                    name=s["name"],
                    file=s["file"],
                    primary_key=tuple(s["primary_key"]),
                    entities=tuple(entities),
                    delimiter=s.get("delimiter", ","),
                    incremental_on=s.get("incremental_on"),
                )
            )
        if not streams:
            raise AdapterError("mapping config declares no streams")
        coverage = tuple(
            EntityCoverage(
                entity=c["entity"],
                grain=c.get("grain", "row"),
                completeness=float(c.get("completeness", 0.8)),
                manual_hours=float(c.get("manual_hours", 0.0)),
                notes=c.get("notes", ""),
            )
            for c in data.get("coverage", [])
        )
        if not coverage:
            # Derive a conservative manifest from what the streams actually map,
            # so an un-annotated config still prices into a fold-in estimate.
            entities = {e.entity for s in streams for e in s.entities}
            coverage = tuple(
                EntityCoverage(e, "row", 0.7, 2.0, "derived from mapping config")
                for e in sorted(entities)
            )
        return cls(
            source_id=data["source_id"],
            label=data.get("label", data["source_id"]),
            streams=tuple(streams),
            vertical=data.get("vertical", "shared"),
            fold_in_hours=float(data.get("fold_in_hours", 4.0)),
            coverage=coverage,
        )

    @classmethod
    def from_yaml(cls, path: Path | str) -> "CsvMappingConfig":
        return cls.from_dict(yaml.safe_load(Path(path).read_text()))


@register
class GenericCsvAdapter(BaseAdapter):
    """Configured at construction time by a mapping config.

    `source_id` stays 'generic_csv' for registration; the configured instance
    reports its own `configured_source_id` so raw tables stay per-source.
    """

    source_id = "generic_csv"
    vertical = "shared"
    version = "1.0.0"

    capabilities = Capabilities(
        source_id="generic_csv",
        vertical="shared",
        delivery="file",
        tier=1,
        fold_in_hours=4.0,
        entities=(
            EntityCoverage("party", "row", 0.75, 2.0, "Whatever the file carries"),
            EntityCoverage("household", "row", 0.75, 2.0),
            EntityCoverage("account", "row", 0.75, 2.0),
            EntityCoverage("balance_snapshot", "account/day", 0.85, 1.0),
            EntityCoverage("fee_schedule", "schedule", 0.70, 4.0),
            EntityCoverage("fee_tier", "tier", 0.70, 2.0),
            EntityCoverage("schedule_assignment", "scope", 0.70, 2.0),
            EntityCoverage("revenue_event", "row", 0.70, 4.0),
            EntityCoverage("invoice", "row", 0.75, 2.0),
            EntityCoverage("cost_line", "row", 0.75, 2.0),
        ),
    )

    def __init__(self, firm_id: str, config: CsvMappingConfig, batch_size: int = 5000) -> None:
        super().__init__(firm_id, batch_size)
        self.config = config
        self.streams = tuple(s.to_stream() for s in config.streams)

    @property
    def configured_source_id(self) -> str:
        return self.config.source_id

    def _stream_config(self, name: str) -> StreamConfig:
        for s in self.config.streams:
            if s.name == name:
                return s
        raise AdapterError(f"stream {name!r} is not in the mapping config")

    def stream_fields(self, stream: Stream, creds: Creds) -> list[str]:
        cfg = self._stream_config(stream.name)
        return creds_fileset(creds).csv_fieldnames(cfg.file, cfg.delimiter)

    def row_counts(self, creds: Creds) -> dict[str, int]:
        return {s.name: len(self._rows(s.name, creds)) for s in self.streams}

    def fingerprint(self, creds: Creds) -> SourceFingerprintResult:
        fs = creds_fileset(creds)
        fields = {s.name: fs.csv_fieldnames(s.file, s.delimiter) for s in self.config.streams}
        return SourceFingerprintResult(
            source_id=self.configured_source_id,
            firm_id=self.firm_id,
            source_version=self.version,
            schema_hash=schema_hash_of(fields),
            row_counts=self.row_counts(creds),
            streams=[s.name for s in self.config.streams],
            read_only_verified=True,
            field_names=fields,
        )

    def _rows(self, stream: str, creds: Creds) -> list[dict[str, Any]]:
        cfg = self._stream_config(stream)
        fs = creds_fileset(creds)
        if not fs.exists(cfg.file):
            return []
        return fs.read_csv(cfg.file, cfg.delimiter)

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

    def batch(self, stream, records, extracted_at=None, cursor_start=None, cursor_end=None):
        batch = super().batch(stream, records, extracted_at, cursor_start, cursor_end)
        batch.source_id = self.configured_source_id
        return batch

    def map(self, batch: RecordBatch) -> list[CanonicalRecord]:
        cfg = self._stream_config(batch.stream)
        out: list[CanonicalRecord] = []
        for index, row in enumerate(batch.records):
            ref = batch.ref(index)
            for mapping in cfg.entities:
                if not mapping.applies(row):
                    continue
                values = {c.target: c.resolve(row) for c in mapping.columns}
                natural_key = mapping.natural_key.format(**row)
                out.append(
                    CanonicalRecord(
                        entity=mapping.entity,
                        natural_key=natural_key,
                        firm_id=batch.firm_id,
                        values=values,
                        refs=(ref,),
                        valid_from=(
                            as_date(row[mapping.valid_from], mapping.valid_from)
                            if mapping.valid_from and row.get(mapping.valid_from) else None
                        ),
                        valid_to=(
                            as_date(row[mapping.valid_to], mapping.valid_to)
                            if mapping.valid_to and row.get(mapping.valid_to) else None
                        ),
                    )
                )
        return out
