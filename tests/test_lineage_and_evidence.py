"""Lineage, the evidence trail, and rebuild-from-S3 (spec sections 6.1, 6.2).

"Every figure opens to the records behind it" is the difference between this
offer and a Power BI consultant's. These tests are that sentence, executed.
"""

from __future__ import annotations

import gzip
import json

import pytest

from fracture.core import db
from fracture.core.errors import LineageError
from fracture.ingest.artifacts import read_envelope
from fracture.ingest.lineage import (
    assert_fully_lineaged,
    drill_through,
    drill_to_raw,
    orphan_edges,
)
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

LINEAGED_ENTITIES = (
    "party", "household", "account", "balance_snapshot", "producer",
    "book_assignment", "invoice", "cash_receipt", "fee_schedule", "fee_tier",
    "revenue_event", "cost_line", "service_event",
)


def test_every_canonical_row_traces_back_to_raw(control, loaded_estate):
    """A canonical row nobody can trace is the exact failure this platform sells
    against, so the assertion is zero, not a tolerance."""
    with control.tenant_connection(loaded_estate["tenant"], "transform") as conn:
        assert_fully_lineaged(conn, LINEAGED_ENTITIES)


def test_no_orphan_lineage_edges(control, loaded_estate):
    with control.tenant_connection(loaded_estate["tenant"], "transform") as conn:
        assert orphan_edges(conn) == []


def test_assert_fully_lineaged_actually_fails(control, fresh_tenant):
    """The guard has to catch something, or it is decoration."""
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        db.execute(
            conn,
            "insert into canon.household (firm_id, household_id, name, valid_from) "
            "values ('F1', 'ORPHAN', 'Untraceable Household', '2020-01-01')",
        )
        with pytest.raises(LineageError, match="no lineage back to raw"):
            assert_fully_lineaged(conn, ["household"])


def test_drill_from_canon_reaches_the_artifact(control, loaded_estate):
    with control.tenant_connection(loaded_estate["tenant"], "transform") as conn:
        row = db.query_one(
            conn, "select canon_id from canon.balance_snapshot order by canon_id limit 1"
        )
        evidence = drill_to_raw(conn, "canon.balance_snapshot", str(row["canon_id"]))
        assert evidence, "a balance snapshot resolved to no raw records"
        first = evidence[0]
        assert first["payload"] is not None
        assert first["artifact_uri"].startswith("s3://")
        assert len(first["artifact_sha256"]) == 64
        assert len(first["record_hash"]) == 64


def test_artifact_on_disk_matches_its_recorded_hash(control, loaded_estate, artifact_root):
    """If a tenant disputes a number you produce the file you received and its
    hash. The hash has to still match the file."""
    import hashlib

    from fracture.ingest.artifacts import LocalArtifactStore

    store = LocalArtifactStore()
    with control.tenant_connection(loaded_estate["tenant"], "transform") as conn:
        loads = db.query(
            conn, "select artifact_uri, artifact_sha256, row_count from raw._load limit 20"
        )
    assert loads
    for load in loads:
        data = store.get(load["artifact_uri"])
        assert hashlib.sha256(data).digest() == bytes(load["artifact_sha256"])
        envelope = read_envelope(data)
        assert envelope["record_count"] == load["row_count"]


def test_artifact_is_self_describing(control, loaded_estate):
    """Rebuilding from object storage alone means the object must carry its own
    context: which firm, which source, which stream (spec 6.1)."""
    from fracture.ingest.artifacts import LocalArtifactStore

    store = LocalArtifactStore()
    with control.tenant_connection(loaded_estate["tenant"], "transform") as conn:
        load = db.query_one(conn, "select artifact_uri from raw._load limit 1")
    envelope = read_envelope(store.get(load["artifact_uri"]))
    for key in ("firm_id", "source_id", "stream", "extracted_at", "records", "format_version"):
        assert key in envelope, f"artifact envelope is missing {key}"


def test_database_is_rebuildable_from_artifacts_alone(control, fresh_tenant, loaded_estate):
    """The claim in spec 6.1, executed: wipe a raw table, rebuild it from S3, and
    check the row count and hashes come back identical."""
    from fracture.ingest.artifacts import LocalArtifactStore
    from fracture.ingest.raw import rebuild_from_artifacts

    tenant = loaded_estate["tenant"]
    store = LocalArtifactStore()
    with control.tenant_connection(tenant, "owner") as conn:
        loads = db.query(
            conn,
            "select load_id, artifact_uri, row_count from raw._load "
            "where source_id = 'orion' and stream = 'households' limit 3",
        )
        assert loads
        before = {
            str(load["load_id"]): db.query(
                conn,
                "select _sequence, encode(_record_hash,'hex') as h from raw.orion__households "
                "where _load_id = %s order by _sequence",
                (load["load_id"],),
            )
            for load in loads
        }
        db.execute(
            conn,
            "delete from raw.orion__households where _load_id = any(%s)",
            ([load["load_id"] for load in loads],),
        )
        assert db.scalar(
            conn,
            "select count(*) from raw.orion__households where _load_id = any(%s)",
            ([load["load_id"] for load in loads],),
        ) == 0

        restored = rebuild_from_artifacts(conn, store, [load["artifact_uri"] for load in loads])
        assert restored == sum(load["row_count"] for load in loads)

        for load_id, rows in before.items():
            after = db.query(
                conn,
                "select _sequence, encode(_record_hash,'hex') as h from raw.orion__households "
                "where _load_id = %s order by _sequence",
                (load_id,),
            )
            assert after == rows, "rebuilt rows do not hash identically to the originals"


def test_mart_figure_walks_all_the_way_to_the_file(control, built_marts):
    """The full drill-through path a pack link performs."""
    tenant = built_marts["tenant"]
    with control.tenant_connection(tenant, "transform") as conn:
        target = db.query_one(
            conn,
            "select target_pk from lineage.mart_edge "
            "where target_table = 'mart.household_aum' limit 1",
        )
        assert target, "no mart lineage was written"
        walk = drill_through(conn, "mart.household_aum", target["target_pk"])
        assert walk["canon"], "mart row resolved to no canonical rows"
        raw = [r for c in walk["canon"] for r in c["raw"]]
        assert raw, "canonical rows resolved to no raw records"
        assert all(r["artifact_uri"].startswith("s3://") for r in raw)


def test_record_hash_is_stable_across_key_order():
    """Two payloads that differ only in dict ordering are the same record."""
    from fracture.core.hashing import record_hash

    a = {"account": "A-1", "value": "100.50", "as_of": "2026-03-31"}
    b = {"as_of": "2026-03-31", "value": "100.50", "account": "A-1"}
    assert record_hash(a) == record_hash(b)


def test_record_hash_normalises_decimal_scale():
    """1.50 and 1.5 are the same number and must hash the same."""
    from decimal import Decimal

    from fracture.core.hashing import record_hash

    assert record_hash({"v": Decimal("1.50")}) == record_hash({"v": Decimal("1.5")})


def test_record_hash_changes_when_a_value_changes():
    from fracture.core.hashing import record_hash

    assert record_hash({"v": "1.50"}) != record_hash({"v": "1.51"})


def test_artifact_keys_are_immutable(tmp_path):
    """Two extractions claiming one key would silently overwrite evidence."""
    from fracture.ingest.artifacts import LocalArtifactStore

    store = LocalArtifactStore(root=tmp_path)
    # gzip embeds an mtime, so the platform always compresses with mtime=0;
    # fixed bytes here keep the test testing immutability rather than gzip.
    one = gzip.compress(b'{"a":1}', mtime=0)
    two = gzip.compress(b'{"a":2}', mtime=0)
    store.put("tenants/x/raw/immutable.json.gz", one)
    store.put("tenants/x/raw/immutable.json.gz", one)  # identical content: fine
    with pytest.raises(FileExistsError, match="different content"):
        store.put("tenants/x/raw/immutable.json.gz", two)
