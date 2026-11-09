"""Fracture OS calls these entry points and nothing past them."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for directory in (
    ROOT,
    ROOT / "data-profiling",
    ROOT / "migration",
    ROOT / "reporting",
    ROOT / "dbt-scaffold",
    ROOT / "snowflake",
    ROOT / "guardrailed-ai",
):
    sys.path.insert(0, str(directory))

from assessment import assess_command, render_findings, templates  # noqa: E402
from fracture_dbt import project  # noqa: E402
from fracture_dbt.__main__ import main as dbt_main  # noqa: E402
from fracture_guardrail import describe  # noqa: E402
from fracture_migration import list_cards, load_card, recon_command, reconciliation  # noqa: E402
from fracture_migration.__main__ import main as migration_main  # noqa: E402
from fracture_profiling.__main__ import main as profile_main  # noqa: E402
from fracture_reporting import build_command, list_sections  # noqa: E402
from fracture_snowflake import warehouse_decision  # noqa: E402


def test_profile_cli_emits_shape_only_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    extract = tmp_path / "one.csv"
    extract.write_text("client_id,name\nC-1,Ada Lovelace\n", encoding="utf-8")
    assert profile_main([str(extract)]) == 0
    encoded = capsys.readouterr().out
    payload = json.loads(encoded)
    assert payload["files"][0]["tables"][0]["rows"] == 1
    assert "Ada Lovelace" not in encoded
    assert "C-1" not in encoded


def test_migration_cards_and_recon_command():
    sources = {row["source_id"] for row in list_cards()}
    assert sources == {"redtail", "schwab_custodian", "orion", "qbo"}
    card = load_card("redtail")
    assert card["role"] == "crm"
    assert any(mapping["entity"] == "party" for mapping in card["maps"])
    with pytest.raises(KeyError):
        load_card("missing")
    names = {row["name"] for row in reconciliation()}
    assert names == {
        "aum_total", "billed_vs_invoices", "collected_vs_receipts", "consolidated_rollup",
    }
    assert recon_command("acme") == ["fracture", "recon", "acme"]


def test_migration_cli_list(capsys: pytest.CaptureFixture[str]):
    assert migration_main(["list"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {row["source_id"] for row in payload} == {
        "redtail", "schwab_custodian", "orion", "qbo",
    }
    assert migration_main(["show", "missing"]) == 2


def test_reporting_sections_and_build_command():
    sections = list_sections()
    assert [row["file"] for row in sections] == [
        "010_platform_model.sql",
        "020_revenue_margin.sql",
        "030_unbilled_leakage.sql",
        "040_service_sla.sql",
        "050_concentration.sql",
        "060_assurance.sql",
    ]
    assert build_command("acme", "2026-06-30") == [
        "fracture", "pack", "build", "acme", "--period-end", "2026-06-30",
    ]


def test_assessment_templates_and_assess_command():
    assert templates() == [
        "findings.md",
        "integration-map.md",
        "systems-inventory.md",
        "target-state-architecture.md",
    ]
    text = render_findings(
        {"client": "Example Advisory LLC", "pack": "wealth"},
        [{
            "id": "F1",
            "statement": "The custodian total does not reproduce.",
            "evidence": "recon check aum_total",
            "recommendation": "Resolve the unmatched accounts before issue.",
        }],
    )
    assert "Example Advisory LLC" in text
    assert assess_command(["redtail", "schwab_custodian"], "wealth") == [
        "fracture", "assess", "redtail", "schwab_custodian", "--pack", "wealth", "--json",
    ]
    with pytest.raises(ValueError):
        assess_command(["redtail"], "other")


def test_dbt_project_lists_the_six_models():
    described = project()
    assert described["models"] == [
        "account", "client", "household", "producer", "receivable", "revenue",
    ]
    assert described["command"][0:2] == ["dbt", "build"]
    assert Path(described["project_dir"]).name == "dbt-scaffold"


def test_dbt_cli(capsys: pytest.CaptureFixture[str]):
    assert dbt_main([]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "client" in payload["models"]


def test_warehouse_decision_is_postgres():
    decision = warehouse_decision()
    assert decision["engine"] == "postgres"
    assert decision["tenancy"] == "database_per_tenant"


def test_guardrail_contract_names_the_three_calls():
    contract = describe()
    assert contract["record"].endswith("record_proposal")
    assert contract["confirm"].endswith("confirm")
    assert contract["reject"].endswith("reject")
    assert contract["numeric_capable_kinds"] == ["extraction"]
    assert "compute a figure" in contract["rule"]
