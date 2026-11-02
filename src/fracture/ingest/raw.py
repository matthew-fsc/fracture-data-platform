"""The raw layer loader (spec section 6.1).

Append-only. Every extraction writes its artifact to object storage first,
records the SHA-256, then loads. The table shape is generated from one template
so a new adapter stream cannot land in a differently-shaped table.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from psycopg2.extensions import connection as PGConnection
from psycopg2.extras import Json

from fracture.core import db
from fracture.core.errors import AdapterError
from fracture.core.hashing import record_hash
from fracture.core.logging import get_logger
from fracture.ingest.artifacts import ArtifactStore, StoredArtifact, store_extraction

log = get_logger("ingest.raw")

_IDENT = re.compile(r"^[a-z][a-z0-9_]{0,40}$")

RAW_TABLE_TEMPLATE = """
create table if not exists raw.{table} (
  _load_id        uuid        not null,
  _sequence       bigint      not null,
  _firm_id        text        not null,
  _source_id      text        not null,
  _extracted_at   timestamptz not null,
  _loaded_at      timestamptz not null default now(),
  _artifact_uri   text        not null,
  _record_hash    bytea       not null,
  _payload        jsonb       not null,
  primary key (_load_id, _sequence)
);
create index if not exists {table}_firm_extracted
  on raw.{table} (_firm_id, _extracted_at desc);
create index if not exists {table}_record_hash on raw.{table} (_record_hash);
"""


def raw_table_name(source_id: str, stream: str) -> str:
    """`<source>__<stream>`, validated. Adapter ids are not user input, but they
    are interpolated into DDL, so they are checked anyway."""
    for part in (source_id, stream):
        if not _IDENT.match(part):
            raise AdapterError(
                f"{part!r} is not a valid identifier for a raw table "
                "(lowercase letters, digits and underscore, starting with a letter)"
            )
    return f"{source_id}__{stream}"


def ensure_raw_table(conn: PGConnection, source_id: str, stream: str) -> str:
    """Create the raw table. Requires the `owner` role: raw DDL is a migration,
    not something the loader does on the fly."""
    table = raw_table_name(source_id, stream)
    db.run_script(conn, RAW_TABLE_TEMPLATE.format(table=table))
    return table


def require_raw_table(conn: PGConnection, source_id: str, stream: str) -> str:
    """Assert the table exists, without creating it.

    The loader calls this rather than `ensure_raw_table` on purpose. If a stream
    were auto-created at load time, an adapter renaming a stream would quietly
    start filling a brand new empty table while the old one went stale, and
    every downstream count would look plausible.
    """
    table = raw_table_name(source_id, stream)
    if not db.table_exists(conn, "raw", table):
        raise AdapterError(
            f"raw.{table} does not exist; run the tenant stream migration "
            "(fracture.control.provisioning.ensure_streams) before loading"
        )
    return table


@dataclass(frozen=True)
class LoadResult:
    load_id: uuid.UUID
    table: str
    row_count: int
    artifact: StoredArtifact
    duplicate_hashes: int = 0

    @property
    def artifact_uri(self) -> str:
        return self.artifact.uri


class RawLoader:
    """Loads extraction batches into `raw.<source>__<stream>`."""

    def __init__(self, store: ArtifactStore, s3_prefix: str) -> None:
        self.store = store
        self.s3_prefix = s3_prefix

    def load(
        self,
        conn: PGConnection,
        firm_id: str,
        source_id: str,
        stream: str,
        records: Sequence[dict[str, Any]],
        extracted_at: dt.datetime | None = None,
        adapter_version: str | None = None,
        schema_hash: bytes | None = None,
        cursor_start: str | None = None,
        cursor_end: str | None = None,
        fingerprint_id: uuid.UUID | None = None,
    ) -> LoadResult:
        extracted_at = extracted_at or dt.datetime.now(dt.timezone.utc)
        load_id = uuid.uuid4()
        table = require_raw_table(conn, source_id, stream)

        # 1. Artifact first. If this fails, nothing is loaded and there is no
        #    row in the database without a file behind it.
        artifact = store_extraction(
            self.store,
            self.s3_prefix,
            firm_id,
            source_id,
            stream,
            extracted_at,
            str(load_id),
            records,
            adapter_version=adapter_version,
            schema_hash=schema_hash,
            cursor_start=cursor_start,
            cursor_end=cursor_end,
        )

        # 2. Hash each record canonically, then load.
        seen: set[bytes] = set()
        duplicates = 0
        rows = []
        for seq, payload in enumerate(records, start=1):
            digest = record_hash(payload)
            if digest in seen:
                duplicates += 1
            seen.add(digest)
            rows.append(
                (
                    load_id, seq, firm_id, source_id, extracted_at,
                    artifact.uri, digest, Json(payload),
                )
            )

        db.execute_values(
            conn,
            f"""
            insert into raw.{table}
              (_load_id, _sequence, _firm_id, _source_id, _extracted_at,
               _artifact_uri, _record_hash, _payload)
            values %s
            """,
            rows,
        )
        db.execute(
            conn,
            """
            insert into raw._load
              (load_id, firm_id, source_id, stream, extracted_at, artifact_uri,
               artifact_sha256, row_count, cursor_start, cursor_end, fingerprint_id, status)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'complete')
            """,
            (
                load_id, firm_id, source_id, stream, extracted_at, artifact.uri,
                artifact.sha256, len(rows), cursor_start, cursor_end, fingerprint_id,
            ),
        )
        log.info(
            "loaded %d rows into raw.%s for firm %s (load_id=%s)",
            len(rows), table, firm_id, load_id,
        )
        return LoadResult(load_id, table, len(rows), artifact, duplicates)


def rebuild_from_artifacts(
    conn: PGConnection, store: ArtifactStore, uris: Iterable[str]
) -> int:
    """Rebuild raw tables from object storage alone (spec 6.1).

    This function is the proof of that claim, and the test that calls it is the
    proof that the claim still holds.
    """
    from fracture.ingest.artifacts import read_envelope

    total = 0
    for uri in uris:
        envelope = read_envelope(store.get(uri))
        table = ensure_raw_table(conn, envelope["source_id"], envelope["stream"])
        if not envelope.get("load_id"):
            raise AdapterError(
                f"artifact {uri} predates load_id capture in the envelope; "
                "it cannot be rebuilt without guessing which load it was"
            )
        load_id = uuid.UUID(envelope["load_id"])
        extracted_at = dt.datetime.fromisoformat(envelope["extracted_at"])
        rows = [
            (
                load_id, seq, envelope["firm_id"], envelope["source_id"], extracted_at,
                uri, record_hash(payload), Json(payload),
            )
            for seq, payload in enumerate(envelope["records"], start=1)
        ]
        db.execute(conn, f"delete from raw.{table} where _load_id = %s", (load_id,))
        db.execute_values(
            conn,
            f"""
            insert into raw.{table}
              (_load_id, _sequence, _firm_id, _source_id, _extracted_at,
               _artifact_uri, _record_hash, _payload)
            values %s
            """,
            rows,
        )
        total += len(rows)
    return total


def raw_streams(conn: PGConnection) -> list[dict[str, Any]]:
    return db.query(
        conn,
        """
        select source_id, stream, count(*) as loads, sum(row_count) as rows,
               max(extracted_at) as last_extracted_at
          from raw._load group by 1,2 order by 1,2
        """,
    )
