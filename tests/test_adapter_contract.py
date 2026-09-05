"""The shared adapter suite (spec section 4.2).

Every registered adapter runs through all five required checks. This is
parametrised over the registry rather than written per adapter on purpose: an
adapter added without fixtures fails the suite, so "not shippable without" is
enforced rather than documented.
"""

from __future__ import annotations

import datetime as dt
import importlib
import json
import logging
from pathlib import Path

import pytest

from fracture.adapters.base import CanonicalRecord, RecordBatch
from fracture.adapters.registry import all_adapters
from fracture.adapters.staticcheck import assert_no_mutating_verbs, scan_source
from fracture.core.errors import AdapterError, MutatingVerbError
from fracture.core.redaction import Redactor

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "adapters"
CASES = ("empty", "typical", "pathological")

ADAPTERS = sorted(all_adapters().items())
ADAPTER_IDS = [sid for sid, _ in ADAPTERS]


def _instance(source_id: str, cls, firm_id: str = "FIX"):
    if source_id == "generic_csv":
        from fracture.adapters.sources.generic_csv import CsvMappingConfig

        config = CsvMappingConfig.from_yaml(FIXTURE_DIR / "generic_csv" / "mapping.yml")
        return cls(firm_id=firm_id, config=config)
    return cls(firm_id=firm_id)


def _creds(source_id: str, case: str) -> dict:
    return {"export_dir": str(FIXTURE_DIR / source_id / case), "read_only": True}


# -- 1. every adapter declares a usable capability manifest ------------------


@pytest.mark.parametrize("source_id,cls", ADAPTERS, ids=ADAPTER_IDS)
def test_capability_manifest_is_machine_readable(source_id, cls):
    """The fold-in estimate is computed from this, so it cannot be decorative."""
    caps = cls.capabilities
    assert caps.source_id == source_id
    assert caps.entities, f"{source_id} declares no canonical entities"
    assert caps.delivery in {"api", "database", "file", "manual"}
    assert caps.fold_in_hours > 0, "an adapter that costs nothing to stand up is a lie"
    for coverage in caps.entities:
        assert 0.0 <= coverage.completeness <= 1.0
        assert coverage.grain, f"{source_id}/{coverage.entity} does not state its grain"


@pytest.mark.parametrize("source_id,cls", ADAPTERS, ids=ADAPTER_IDS)
def test_declared_entities_are_writable(source_id, cls):
    """A manifest promising an entity the canonical writer cannot write is a
    quote for work that will not land."""
    from fracture.canon.writer import ENTITY_KEYS

    unknown = cls.capabilities.entity_names() - set(ENTITY_KEYS)
    assert not unknown, f"{source_id} claims entities with no canonical table: {sorted(unknown)}"


# -- 2. discovery snapshot fixture, checked in -------------------------------


@pytest.mark.parametrize("source_id,cls", ADAPTERS, ids=ADAPTER_IDS)
def test_discovery_snapshot_matches_fixture(source_id, cls):
    snapshot_path = FIXTURE_DIR / source_id / "discovery.json"
    assert snapshot_path.exists(), (
        f"{source_id} has no checked-in discovery snapshot at {snapshot_path}; "
        "an adapter is not shippable without one (spec 4.2)"
    )
    adapter = _instance(source_id, cls)
    streams = adapter.discover(_creds(source_id, "typical"))
    observed = sorted(
        (
            {
                "name": s.name,
                "primary_key": list(s.primary_key),
                "incremental_on": s.incremental_on,
            }
            for s in streams
        ),
        key=lambda s: s["name"],
    )
    expected = json.loads(snapshot_path.read_text())
    assert observed == expected, (
        f"{source_id} discovery drifted from its snapshot. If this is intended, "
        "update the fixture in the same commit as the adapter change."
    )


# -- 3. three extraction fixtures, and golden canonical output ---------------


@pytest.mark.parametrize("source_id,cls", ADAPTERS, ids=ADAPTER_IDS)
@pytest.mark.parametrize("case", CASES)
def test_extraction_fixture_exists(source_id, cls, case):
    path = FIXTURE_DIR / source_id / case
    assert path.is_dir(), (
        f"{source_id} is missing the {case!r} extraction fixture. Empty, typical "
        "and pathological are all required (spec 4.2)."
    )


@pytest.mark.parametrize("source_id,cls", ADAPTERS, ids=ADAPTER_IDS)
def test_empty_extraction_yields_nothing_and_does_not_raise(source_id, cls):
    """An empty source is a normal Tuesday, not an error."""
    adapter = _instance(source_id, cls)
    creds = _creds(source_id, "empty")
    total = 0
    for stream in adapter.discover(creds):
        for batch in adapter.extract(stream, creds):
            total += len(batch.records)
            batch.load_id = _fake_load_id()
            assert adapter.map(batch) == []
    assert total == 0


@pytest.mark.parametrize("source_id,cls", ADAPTERS, ids=ADAPTER_IDS)
def test_golden_canonical_output(source_id, cls):
    """Typical and pathological fixtures map to a checked-in golden file.

    This is the test that catches a mapping change nobody meant to make. The
    golden file is regenerated with `python scripts/regen_fixtures.py`, which
    makes the diff the reviewable artefact.
    """
    for case in ("typical", "pathological"):
        golden_path = FIXTURE_DIR / source_id / f"golden_{case}.json"
        assert golden_path.exists(), (
            f"{source_id} has no golden canonical output for the {case!r} fixture"
        )
        observed = _map_all(cls, source_id, case)
        expected = json.loads(golden_path.read_text())
        assert observed == expected, (
            f"{source_id} canonical output changed for the {case!r} fixture. "
            "Regenerate with scripts/regen_fixtures.py and review the diff."
        )


@pytest.mark.parametrize("source_id,cls", ADAPTERS, ids=ADAPTER_IDS)
def test_pathological_fixture_is_actually_pathological(source_id, cls):
    """A 'pathological' fixture identical to the typical one tests nothing."""
    typical = _map_all(cls, source_id, "typical")
    pathological = _map_all(cls, source_id, "pathological")
    assert pathological != typical, (
        f"{source_id}'s pathological fixture produces the same canonical output as "
        "the typical one; it should carry nulls, unicode, negatives or backdating"
    )


@pytest.mark.parametrize("source_id,cls", ADAPTERS, ids=ADAPTER_IDS)
def test_every_canonical_record_carries_lineage(source_id, cls):
    """A canonical record with no raw reference is a number nobody can open."""
    adapter = _instance(source_id, cls)
    creds = _creds(source_id, "typical")
    for stream in adapter.discover(creds):
        for batch in adapter.extract(stream, creds):
            if not batch.records:
                continue
            batch.load_id = _fake_load_id()
            for record in adapter.map(batch):
                assert record.refs, f"{source_id}/{stream.name} produced a record with no refs"
                for ref in record.refs:
                    assert 1 <= ref.sequence <= len(batch.records)


@pytest.mark.parametrize("source_id,cls", ADAPTERS, ids=ADAPTER_IDS)
def test_map_before_load_is_refused(source_id, cls):
    """Mapping an unloaded batch would silently produce untraceable rows."""
    batch = RecordBatch(
        stream="x", firm_id="FIX", source_id=source_id,
        records=[{"a": 1}], extracted_at=dt.datetime.now(dt.timezone.utc),
    )
    with pytest.raises(AdapterError, match="not been loaded"):
        batch.ref(0)


# -- 4. redaction: no PII leaves the mapping layer in logs -------------------


@pytest.mark.parametrize("source_id,cls", ADAPTERS, ids=ADAPTER_IDS)
def test_no_pii_in_logs_during_mapping(source_id, cls, caplog):
    adapter = _instance(source_id, cls)
    creds = _creds(source_id, "pathological")
    redactor = Redactor()
    with caplog.at_level(logging.DEBUG, logger="fracture"):
        for stream in adapter.discover(creds):
            for batch in adapter.extract(stream, creds):
                if not batch.records:
                    continue
                batch.load_id = _fake_load_id()
                adapter.map(batch)
    for record in caplog.records:
        message = record.getMessage()
        assert not redactor.contains_pii(message), (
            f"{source_id} logged something the redactor would mask: {message[:160]}"
        )


@pytest.mark.parametrize("source_id,cls", ADAPTERS, ids=ADAPTER_IDS)
def test_full_tax_identifiers_never_reach_canon(source_id, cls):
    """Only the last four ever crosses into the canonical layer."""
    records = _map_all(cls, source_id, "pathological")
    for record in records:
        for column, value in record["values"].items():
            if "tax_id" in column or "ssn" in column:
                assert value is None or len(str(value)) <= 4, (
                    f"{source_id} put {column}={value!r} into canon; keep only the last four"
                )


# -- 5. static check: no mutating verbs against the source -------------------


@pytest.mark.parametrize("source_id,cls", ADAPTERS, ids=ADAPTER_IDS)
def test_no_mutating_verbs(source_id, cls):
    module = importlib.import_module(cls.__module__)
    assert_no_mutating_verbs(module)


def test_static_check_catches_a_write():
    """The guard has to actually catch something, or it is decoration."""
    findings = scan_source(
        'def extract(self):\n'
        '    self.client.post("/accounts", json={})\n'
        '    return "update positions set qty = 0 where id = 1"\n'
    )
    kinds = {f.kind for f in findings}
    assert "mutating http verb" in kinds
    assert "mutating sql" in kinds


def test_static_check_allows_reads():
    findings = scan_source(
        'def extract(self):\n'
        '    rows = self.client.get("/accounts")\n'
        '    return "select account_id, market_value from positions where as_of = %s"\n'
    )
    assert findings == []


def test_assert_no_mutating_verbs_raises_on_a_bad_module(tmp_path):
    import types

    module_path = tmp_path / "bad_adapter.py"
    module_path.write_text('QUERY = "delete from accounts where id = 1"\n')
    module = types.ModuleType("bad_adapter")
    module.__file__ = str(module_path)
    with pytest.raises(MutatingVerbError, match="write to a source system"):
        assert_no_mutating_verbs(module)


# -- helpers -----------------------------------------------------------------


def _fake_load_id():
    import uuid

    return uuid.UUID("00000000-0000-4000-8000-000000000001")


def _map_all(cls, source_id: str, case: str) -> list[dict]:
    """Canonical output as plain JSON-able data, ordered deterministically."""
    adapter = _instance(cls=cls, source_id=source_id)
    creds = _creds(source_id, case)
    out: list[dict] = []
    for stream in sorted(adapter.discover(creds), key=lambda s: s.name):
        for batch in adapter.extract(stream, creds):
            if not batch.records:
                continue
            batch.load_id = _fake_load_id()
            for record in adapter.map(batch):
                out.append(_serialise(record))
    out.sort(key=lambda r: (r["entity"], r["natural_key"]))
    return out


def _serialise(record: CanonicalRecord) -> dict:
    def norm(value):
        if isinstance(value, (dt.date, dt.datetime)):
            return value.isoformat()
        if hasattr(value, "quantize"):
            return str(value)
        return value

    return {
        "entity": record.entity,
        "natural_key": record.natural_key,
        "firm_id": record.firm_id,
        "values": {k: norm(v) for k, v in sorted(record.values.items())},
        "valid_from": norm(record.valid_from),
        "valid_to": norm(record.valid_to),
        "refs": [[str(r.load_id), r.sequence] for r in record.refs],
    }
