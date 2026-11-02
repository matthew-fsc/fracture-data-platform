"""The ingest pipeline end to end, and the failure modes in spec section 16."""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

from fracture.adapters.registry import get_adapter
from fracture.core import db
from fracture.core.errors import SchemaDriftError
from fracture.ingest.artifacts import LocalArtifactStore
from fracture.ingest.pipeline import SourceRunner
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]


def _export(tmp_path: Path) -> Path:
    """A minimal Orion export we can then mutate to simulate drift."""
    root = tmp_path / "orion-export"
    root.mkdir(parents=True, exist_ok=True)
    (root / "orion_households.json").write_text(json.dumps({"households": [
        {"householdId": "H-1", "name": "The Okafor Household", "tier": "core",
         "createdOn": "2019-04-02", "updatedAt": "2026-01-05"},
    ]}))
    (root / "orion_accounts.json").write_text(json.dumps({"accounts": [
        {"accountId": "A-1", "householdId": "H-1", "primaryContactId": "P-1",
         "primaryContactName": "Amara Okafor", "registrationType": "joint",
         "custodian": "schwab", "openedOn": "2019-04-10", "closedOn": None,
         "billable": True, "country": "US", "updatedAt": "2026-01-05"},
    ]}))
    (root / "orion_positions.json").write_text(json.dumps({"positions": [
        {"accountId": "A-1", "asOfDate": "2026-03-31", "marketValue": "1000000.00",
         "cashValue": "1000.00", "billableValue": "1000000.00", "currency": "USD"},
    ]}))
    return root


def _runner(control, tenant) -> SourceRunner:
    return SourceRunner(control, tenant, LocalArtifactStore())


def test_pipeline_loads_raw_then_canon_with_lineage(control, fresh_tenant, tmp_path):
    control.add_firm(fresh_tenant, "F1", "Fixture Firm", "platform")
    creds = {"export_dir": str(_export(tmp_path)), "read_only": True}
    adapter = get_adapter("orion")(firm_id="F1")
    result = _runner(control, fresh_tenant).run(adapter, creds)

    assert result.rows_loaded == 3
    assert result.canon.inserted > 0
    assert result.read_only_verified is True

    with control.tenant_connection(fresh_tenant, "transform") as conn:
        assert db.scalar(conn, "select count(*) from canon.household") == 1
        assert db.scalar(conn, "select count(*) from canon.balance_snapshot") == 1
        assert db.scalar(conn, "select count(*) from lineage.edge") >= 4
        load = db.query_one(conn, "select * from raw._load where stream = 'positions'")
        assert load["artifact_uri"].startswith("s3://")
        assert load["row_count"] == 1


def test_rerunning_an_unchanged_source_is_idempotent(control, fresh_tenant, tmp_path):
    """A daily refresh over unchanged data must not multiply the canonical layer."""
    control.add_firm(fresh_tenant, "F1", "Fixture Firm", "platform")
    creds = {"export_dir": str(_export(tmp_path)), "read_only": True}
    runner = _runner(control, fresh_tenant)
    runner.run(get_adapter("orion")(firm_id="F1"), creds)
    runner.run(get_adapter("orion")(firm_id="F1"), creds)

    with control.tenant_connection(fresh_tenant, "transform") as conn:
        assert db.scalar(conn, "select count(*) from canon.household") == 1
        assert db.scalar(
            conn, "select count(*) from canon.balance_snapshot where superseded_at is null"
        ) == 1


def test_two_firms_do_not_share_a_cursor(control, fresh_tenant, tmp_path):
    """A tenant holds several firms on the same system. A cursor shared across
    them silently skips the second firm's entire history on its first run."""
    control.add_firm(fresh_tenant, "F1", "Firm One", "platform")
    control.add_firm(fresh_tenant, "F2", "Firm Two", "addon")
    creds = {"export_dir": str(_export(tmp_path)), "read_only": True}
    runner = _runner(control, fresh_tenant)
    first = runner.run(get_adapter("orion")(firm_id="F1"), creds)
    second = runner.run(get_adapter("orion")(firm_id="F2"), creds)
    assert second.rows_loaded == first.rows_loaded, (
        "the second firm loaded fewer rows than the first from the same export"
    )
    with control.tenant_connection(fresh_tenant, "transform") as conn:
        firms = {
            r["firm_id"] for r in db.query(conn, "select distinct firm_id from canon.household")
        }
    assert firms == {"F1", "F2"}


def test_a_removed_source_field_halts_before_mapping(control, fresh_tenant, tmp_path):
    """Spec 16: alert on schema hash change before mapping silently drops a
    column. A dropped column does not error -- it produces nulls."""
    control.add_firm(fresh_tenant, "F1", "Fixture Firm", "platform")
    export = _export(tmp_path)
    creds = {"export_dir": str(export), "read_only": True}
    runner = _runner(control, fresh_tenant)
    runner.run(get_adapter("orion")(firm_id="F1"), creds)

    # The custodian stops sending `billableValue`.
    positions = json.loads((export / "orion_positions.json").read_text())
    for record in positions["positions"]:
        record.pop("billableValue")
    (export / "orion_positions.json").write_text(json.dumps(positions))

    with pytest.raises(SchemaDriftError, match="fields removed"):
        runner.run(get_adapter("orion")(firm_id="F1"), creds)

    with control.tenant_connection(fresh_tenant, "transform") as conn:
        drift = db.query_one(conn, "select * from recon.schema_drift order by observed_at desc")
    assert drift is not None
    assert "positions.billableValue" in drift["removed_fields"]
    assert drift["acknowledged_by"] is None


def test_drift_can_be_accepted_deliberately(control, fresh_tenant, tmp_path):
    """The halt is a decision point, not a wall."""
    control.add_firm(fresh_tenant, "F1", "Fixture Firm", "platform")
    export = _export(tmp_path)
    creds = {"export_dir": str(export), "read_only": True}
    runner = _runner(control, fresh_tenant)
    runner.run(get_adapter("orion")(firm_id="F1"), creds)

    positions = json.loads((export / "orion_positions.json").read_text())
    for record in positions["positions"]:
        record.pop("billableValue")
        record["asOfDate"] = "2026-04-30"
    (export / "orion_positions.json").write_text(json.dumps(positions))

    result = runner.run(get_adapter("orion")(firm_id="F1"), creds, allow_drift=True)
    assert result.schema_drift is True
    assert result.removed_fields == ["positions.billableValue"]
    assert result.rows_loaded >= 1


def test_an_added_field_does_not_halt_the_run(control, fresh_tenant, tmp_path):
    """A source adding a column is normal. Only a removal risks a silent null."""
    control.add_firm(fresh_tenant, "F1", "Fixture Firm", "platform")
    export = _export(tmp_path)
    creds = {"export_dir": str(export), "read_only": True}
    runner = _runner(control, fresh_tenant)
    runner.run(get_adapter("orion")(firm_id="F1"), creds)

    positions = json.loads((export / "orion_positions.json").read_text())
    for record in positions["positions"]:
        record["newVendorField"] = "x"
        record["asOfDate"] = "2026-04-30"
    (export / "orion_positions.json").write_text(json.dumps(positions))

    result = runner.run(get_adapter("orion")(firm_id="F1"), creds)
    assert result.schema_drift is True
    assert result.added_fields == ["positions.newVendorField"]
    assert result.removed_fields == []


def test_a_fingerprint_is_recorded_on_every_run(control, fresh_tenant, tmp_path):
    """Fingerprint on every run, not only at fold-in (spec 16)."""
    control.add_firm(fresh_tenant, "F1", "Fixture Firm", "platform")
    creds = {"export_dir": str(_export(tmp_path)), "read_only": True}
    runner = _runner(control, fresh_tenant)
    for _ in range(3):
        runner.run(get_adapter("orion")(firm_id="F1"), creds)
    with control.connection() as conn:
        count = db.scalar(
            conn,
            "select count(*) from control.source_fingerprint "
            "where tenant_id = %s and source_id = 'orion'",
            (fresh_tenant.tenant_id,),
        )
    assert count == 3


def test_a_failed_source_is_recorded_against_the_registry(control, fresh_tenant, tmp_path):
    from fracture.core.errors import AdapterError

    control.add_firm(fresh_tenant, "F1", "Fixture Firm", "platform")
    control.register_source(fresh_tenant, "F1", "orion", "secret/orion")
    runner = _runner(control, fresh_tenant)
    creds = {"export_dir": str(tmp_path / "does-not-exist"), "read_only": True}
    with pytest.raises(AdapterError):
        runner.run(get_adapter("orion")(firm_id="F1"), creds)
    control.fail_source(fresh_tenant, "F1", "orion", "export directory missing")
    source = control.list_sources(fresh_tenant, "F1")[0]
    assert source.status == "failed"
    assert "missing" in source.last_error


def test_an_unverified_credential_reports_itself_as_unverified(control, fresh_tenant, tmp_path):
    """Read-only means the credential the client issues is read-only. You verify
    and document it; you do not control it (spec 1.2). The default is False."""
    control.add_firm(fresh_tenant, "F1", "Fixture Firm", "platform")
    creds = {"export_dir": str(_export(tmp_path))}  # no read_only flag
    result = _runner(control, fresh_tenant).run(get_adapter("orion")(firm_id="F1"), creds)
    assert result.read_only_verified is False


def test_producer_crosswalk_records_disagreeing_rep_codes(control, loaded_estate):
    """CRM advisor, custodian rep code and payroll employee are three keys. The
    crosswalk is persisted and human-reviewable, not matched at query time."""
    with control.tenant_connection(loaded_estate["tenant"], "transform") as conn:
        rows = db.query(
            conn,
            "select producer_id, system, external_key from canon.producer_crosswalk "
            "where superseded_at is null order by producer_id, system",
        )
    assert rows, "no crosswalk rows were persisted"
    by_producer: dict[str, set[str]] = {}
    for row in rows:
        by_producer.setdefault(row["producer_id"], set()).add(row["external_key"])
    disagreeing = {p: keys for p, keys in by_producer.items() if len(keys) > 1}
    assert disagreeing, (
        "the fixture plants rep-code mismatches; none reached the crosswalk, so "
        "entity resolution would silently pick one"
    )
