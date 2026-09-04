"""Source -> raw -> canon, for one firm and one source.

This is what a Dagster asset calls. Keeping it here rather than in the
orchestrator means the whole path is testable without a Dagster instance, and
that the orchestrator holds no logic of its own beyond scheduling and
partitioning.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any, Sequence

from psycopg2.extras import Json

from fracture.adapters.base import BaseAdapter, diff_schema
from fracture.control.models import SourceFingerprint, Tenant
from fracture.control.provisioning import ensure_streams
from fracture.control.registry import ControlPlane
from fracture.core import db
from fracture.core.errors import SchemaDriftError
from fracture.core.logging import get_logger
from fracture.canon.writer import CanonWriter, WriteStats
from fracture.ingest.artifacts import ArtifactStore
from fracture.ingest.lineage import LineageWriter
from fracture.ingest.raw import RawLoader

log = get_logger("ingest.pipeline")


@dataclass
class SourceRunResult:
    firm_id: str
    source_id: str
    fingerprint_id: uuid.UUID | None = None
    schema_drift: bool = False
    added_fields: list[str] = field(default_factory=list)
    removed_fields: list[str] = field(default_factory=list)
    loads: dict[str, int] = field(default_factory=dict)
    canon: WriteStats = field(default_factory=WriteStats)
    read_only_verified: bool = False

    @property
    def rows_loaded(self) -> int:
        return sum(self.loads.values())


class SourceRunner:
    """Runs one adapter against one firm inside one tenant."""

    def __init__(
        self,
        control: ControlPlane,
        tenant: Tenant,
        store: ArtifactStore,
        system_time: dt.datetime | None = None,
    ) -> None:
        self.control = control
        self.tenant = tenant
        self.store = store
        self.system_time = system_time
        self.loader = RawLoader(store, tenant.s3_prefix)

    def run(
        self,
        adapter: BaseAdapter,
        creds: dict[str, Any],
        streams: Sequence[str] | None = None,
        allow_drift: bool = False,
        map_to_canon: bool = True,
    ) -> SourceRunResult:
        """Fingerprint, extract, load, map -- each step on the role that owns it.

        Three connections, deliberately: `owner` creates raw tables, `loader`
        appends to them and can neither update nor delete, `transform` reads raw
        and writes canon. Running the whole pipeline as one superuser would work
        and would also mean the role separation in spec 3.3 is documentation
        rather than a control.
        """
        source_id = getattr(adapter, "configured_source_id", adapter.source_id)
        result = SourceRunResult(firm_id=adapter.firm_id, source_id=source_id)

        # 1. Fingerprint first, every run (spec 16). Cheap, read-only.
        fp = adapter.fingerprint(creds)
        result.read_only_verified = fp.read_only_verified
        previous = self.control.previous_fingerprint(self.tenant, adapter.firm_id, source_id)
        fingerprint_id = self.control.record_fingerprint(
            self.tenant,
            SourceFingerprint(
                source_id=source_id,
                firm_id=adapter.firm_id,
                source_version=fp.source_version,
                schema_hash=fp.schema_hash,
                row_counts=fp.row_counts,
                streams=fp.streams,
                field_names=fp.field_names,
                read_only_verified=fp.read_only_verified,
            ),
        )
        result.fingerprint_id = fingerprint_id

        discovered = adapter.discover(creds)
        selected = [s for s in discovered if streams is None or s.name in streams]

        # 2. Raw tables exist before anything is extracted, created by `owner`.
        ensure_streams(self.control, self.tenant, [(source_id, s.name) for s in selected])

        drift_rows: list[tuple[Any, ...]] = []
        if previous is not None and previous.schema_hash != fp.schema_hash:
            added, removed = diff_schema(previous.field_names, fp.field_names)
            result.schema_drift = True
            result.added_fields, result.removed_fields = added, removed
            drift_rows.append(
                (
                    adapter.firm_id, source_id, previous.schema_hash, fp.schema_hash,
                    Json(added), Json(removed),
                )
            )

        if drift_rows:
            with self.control.tenant_connection(self.tenant, "transform") as conn:
                db.execute_values(
                    conn,
                    """
                    insert into recon.schema_drift
                      (firm_id, source_id, previous_hash, current_hash, added_fields, removed_fields)
                    values %s
                    """,
                    drift_rows,
                )
            # A removed field silently empties a canonical column. Alert before
            # mapping, not after the board pack goes out (spec 16).
            if result.removed_fields and not allow_drift:
                raise SchemaDriftError(
                    f"{source_id}/{adapter.firm_id}: source schema changed, fields removed: "
                    f"{', '.join(result.removed_fields)}. "
                    "Review the mapping, then re-run with allow_drift=True."
                )

        # 3. Extract and load, as `loader`: insert on raw only, no update, no delete.
        batches = []
        with self.control.tenant_connection(self.tenant, "loader") as conn:
            for stream in selected:
                cursor = self._last_cursor(conn, adapter.firm_id, source_id, stream.name)
                for batch in adapter.extract(stream, creds, cursor):
                    if not batch.records:
                        continue
                    load = self.loader.load(
                        conn,
                        firm_id=batch.firm_id,
                        source_id=source_id,
                        stream=batch.stream,
                        records=batch.records,
                        extracted_at=batch.extracted_at,
                        adapter_version=adapter.version,
                        schema_hash=fp.schema_hash,
                        cursor_start=batch.cursor_start,
                        cursor_end=batch.cursor_end,
                        fingerprint_id=fingerprint_id,
                    )
                    batch.load_id = load.load_id
                    result.loads[stream.name] = result.loads.get(stream.name, 0) + load.row_count
                    batches.append(batch)

        # 4. Map to canon, as `transform`, with lineage.
        if map_to_canon and batches:
            with self.control.tenant_connection(self.tenant, "transform") as conn:
                with LineageWriter(conn) as lineage:
                    writer = CanonWriter(conn, lineage, system_time=self.system_time)
                    for batch in batches:
                        records = adapter.map(batch)
                        if not records:
                            continue
                        # Stamp provenance centrally so an adapter cannot forget
                        # to, and so fan-in precedence always has a source.
                        for record in records:
                            record.source_id = source_id
                        stats = writer.write(records)
                        result.canon.inserted += stats.inserted
                        result.canon.superseded += stats.superseded
                        result.canon.unchanged += stats.unchanged
                        result.canon.deferred += stats.deferred
                        result.canon.variances += stats.variances
                        for entity, n in stats.by_entity.items():
                            result.canon.record(entity, n)

        log.info(
            "%s/%s: %d raw rows, %s",
            adapter.firm_id, source_id, result.rows_loaded, result.canon,
        )
        return result

    def _last_cursor(self, conn, firm_id: str, source_id: str, stream: str) -> str | None:
        """The high-water mark for one firm's stream.

        Scoped by firm as well as source: a tenant holds several firms running
        the same system, and a cursor shared across them would silently skip the
        second firm's entire history on its first run.
        """
        return db.scalar(
            conn,
            """
            select cursor_end from raw._load
             where firm_id = %s and source_id = %s and stream = %s and cursor_end is not null
             order by extracted_at desc limit 1
            """,
            (firm_id, source_id, stream),
        )
