"""Migration entry point for Fracture OS.

Discovery reads the field cards and the reconciliation tolerances. Execution
is the platform CLI, returned here as an argv so the caller does not assemble
it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_CARDS = _ROOT / "mappings"


def list_cards() -> list[dict]:
    """One row per field card: source, role, adapter, entities."""
    rows = []
    for path in sorted(_CARDS.glob("*.yml")):
        card = yaml.safe_load(path.read_text())
        entities = []
        for mapping in card.get("maps", []):
            entity = mapping["entity"]
            if entity not in entities:
                entities.append(entity)
        rows.append({
            "source_id": card["source_id"],
            "role": card["role"],
            "adapter": card["adapter"],
            "file": path.name,
            "entities": entities,
        })
    return rows


def load_card(source_id: str) -> dict:
    """The full field card for one source. KeyError if it is not a known card."""
    for path in sorted(_CARDS.glob("*.yml")):
        card = yaml.safe_load(path.read_text())
        if card.get("source_id") == source_id:
            return card
    known = ", ".join(row["source_id"] for row in list_cards())
    raise KeyError(f"no migration card for {source_id!r}; known: {known}")


def reconciliation() -> list[dict]:
    """Check name and tolerance, from the platform constant."""
    from fracture.recon import TOLERANCES

    return [
        {"name": name, "tolerance": format(value, "f")}
        for name, value in TOLERANCES.items()
    ]


def recon_command(slug: str) -> list[str]:
    if not str(slug).strip():
        raise ValueError("slug is required")
    return ["fracture", "recon", slug]


def migrate_command() -> list[str]:
    """Fan tenant DDL out. Green only when every targeted tenant succeeds."""
    return ["fracture", "migrate", "--success-threshold", "1.0"]
