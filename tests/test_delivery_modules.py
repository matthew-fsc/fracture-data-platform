"""Contracts for the delivery-accelerator modules.

These tests do not need Postgres. They lock the engagement surface to the
platform code it claims to describe: mapping cards name fields the adapter
actually reads, profiles do not retain cell values, findings cannot carry a
recomputed amount, and the dbt scaffold keeps its grain tests.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data-profiling"))

from fracture_profiling import profile_file, profile_tree  # noqa: E402


def _load_render():
    path = ROOT / "assessment" / "render_findings.py"
    spec = importlib.util.spec_from_file_location("render_findings", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_profile_keeps_no_cell_values(tmp_path: Path):
    extract = tmp_path / "clients.csv"
    extract.write_text(
        "client_id,display_name,ssn\n"
        "C-100,Ada Lovelace,000-00-0000\n"
        "C-200,Grace Hopper,\n",
        encoding="utf-8",
    )
    report = profile_file(extract)
    encoded = json.dumps(report)
    assert "Ada Lovelace" not in encoded
    assert "Grace Hopper" not in encoded
    assert "000-00-0000" not in encoded
    assert "C-100" not in encoded
    columns = {column["name"]: column for column in report["tables"][0]["columns"]}
    assert columns["ssn"]["sensitive"] is True
    assert columns["ssn"]["type"] == "redacted"
    assert columns["ssn"]["distinct"] is None
    assert columns["display_name"]["type"] == "text"
    assert columns["display_name"]["distinct"] == 2
    assert report["tables"][0]["rows"] == 2


def test_checked_in_fixtures_do_not_leak_into_a_profile():
    households = ROOT / "tests/fixtures/adapters/generic_csv/typical/households.csv"
    schwab = ROOT / "tests/fixtures/adapters/schwab_custodian/typical/schwab_accounts.csv"
    encoded = json.dumps(profile_tree(households.parent)) + json.dumps(profile_file(schwab))
    assert "Okafor" not in encoded
    assert "H-001" not in encoded
    assert "A-001" not in encoded
    assert "PM001" not in encoded


def test_findings_renderer_refuses_an_amount(tmp_path: Path):
    render = _load_render()
    engagement = {"client": "Example Advisory LLC", "pack": "wealth"}
    findings = [{
        "id": "F1",
        "statement": "The custodian total does not reproduce.",
        "evidence": "recon check aum_total",
        "recommendation": "Resolve the unmatched accounts before issue.",
        "amount": "10",
    }]
    with pytest.raises(render.FindingError):
        render.render_findings(engagement, findings)


def test_findings_renderer_cites_evidence_and_does_not_invent_a_figure():
    render = _load_render()
    text = render.render_findings(
        {"client": "Example Advisory LLC", "pack": "wealth", "period": "2026-Q1"},
        [{
            "id": "F1",
            "severity": "high",
            "statement": "The custodian total does not reproduce.",
            "evidence": "recon check aum_total",
            "figure_ref": "mart.unbilled|EXAMPLE|HH-1",
            "recommendation": "Resolve the unmatched accounts before issue.",
        }],
    )
    assert "Example Advisory LLC" in text
    assert "recon check aum_total" in text
    assert "mart.unbilled|EXAMPLE|HH-1" in text
    assert "10" not in text


def test_mapping_cards_name_fields_the_adapter_reads():
    for path in sorted((ROOT / "migration" / "mappings").glob("*.yml")):
        card = yaml.safe_load(path.read_text())
        adapter = (ROOT / card["adapter"]).read_text()
        assert card["maps"], path.name
        for mapping in card["maps"]:
            assert mapping["entity"]
            assert mapping["fields"]
            for field in mapping["fields"]:
                assert field["canonical"]
                source = field["source"]
                if source.startswith("("):
                    continue
                assert source in adapter, f"{path.name} cites {source}, absent from {card['adapter']}"


def test_dbt_scaffold_grains_are_tested():
    models = ROOT / "dbt-scaffold" / "models" / "canonical"
    schema = yaml.safe_load((models / "schema.yml").read_text())
    by_name = {model["name"]: model for model in schema["models"]}
    grains = {
        "client": ("firm_id", "client_id"),
        "household": ("firm_id", "household_id"),
        "account": ("firm_id", "account_id"),
        "producer": ("firm_id", "producer_id"),
        "revenue": ("firm_id", "revenue_event_id"),
        "receivable": ("firm_id", "invoice_id"),
    }
    for name, keys in grains.items():
        sql = (models / f"{name}.sql").read_text().lower()
        assert "superseded_at is null" in sql
        assert "sum(" not in sql
        tested = {
            column["name"]
            for column in by_name[name]["columns"]
            if "not_null" in column.get("tests", [])
        }
        assert set(keys) <= tested
        grain_sql = (ROOT / "dbt-scaffold" / "tests" / f"{name}_grain.sql").read_text()
        for key in keys:
            assert key in grain_sql
        assert "having count(*) > 1" in grain_sql
    receivable = (models / "receivable.sql").read_text()
    assert "'open'" in receivable and "'partial'" in receivable


def test_reconciliation_card_names_the_platform_checks():
    text = (ROOT / "migration" / "reconciliation.md").read_text()
    for check in ("aum_total", "billed_vs_invoices", "collected_vs_receipts", "consolidated_rollup"):
        assert check in text


def test_rd_was_not_created_from_unread_repositories():
    assert not (ROOT / "rd").exists()
