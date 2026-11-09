"""Assessment entry point for Fracture OS."""

from __future__ import annotations

from pathlib import Path

from assessment.render_findings import FindingError, render_findings

__all__ = ["FindingError", "render_findings", "templates", "assess_command"]


def templates() -> list[str]:
    """Filenames of the deliverable templates, in name order."""
    root = Path(__file__).resolve().parent / "templates"
    return sorted(path.name for path in root.glob("*.md"))


def assess_command(sources: list[str], pack: str) -> list[str]:
    """Argv for the computed coverage assessment. `--json` is always set."""
    if pack not in {"wealth", "services"}:
        raise ValueError("pack must be wealth or services")
    if not sources or any(not str(source).strip() for source in sources):
        raise ValueError("at least one source id is required")
    return ["fracture", "assess", *sources, "--pack", pack, "--json"]
