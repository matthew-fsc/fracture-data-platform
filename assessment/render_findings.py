"""Render an assessment findings report.

The renderer copies what the engagement lead wrote and the evidence pointer
they attached. It does not calculate. A finding has no amount field: a figure
belongs in the pack, and the finding cites that figure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REQUIRED = ("id", "statement", "evidence", "recommendation")
OPTIONAL = ("owner", "severity", "figure_ref")
ALLOWED = set(REQUIRED) | set(OPTIONAL)


class FindingError(ValueError):
    """A finding the report is not allowed to publish."""


def render_findings(engagement: dict, findings: list[dict]) -> str:
    if not isinstance(engagement, dict):
        raise FindingError("engagement must be a mapping")
    name = str(engagement.get("client") or "").strip()
    if not name:
        raise FindingError("engagement.client is required")
    if not isinstance(findings, list) or not findings:
        raise FindingError("at least one finding is required")

    blocks = [
        f"# Findings — {name}",
        "",
        f"- Pack: {engagement.get('pack', 'unspecified')}",
        f"- Period: {engagement.get('period', 'unspecified')}",
        "",
        "Each finding cites evidence. This report does not recompute a figure.",
        "",
    ]
    seen: set[str] = set()
    for index, finding in enumerate(findings, start=1):
        blocks.append(_one(index, finding))
        seen.add(str(finding["id"]))
    if len(seen) != len(findings):
        raise FindingError("finding ids must be unique")
    return "\n".join(blocks).rstrip() + "\n"


def _one(index: int, finding: dict) -> str:
    if not isinstance(finding, dict):
        raise FindingError(f"finding {index} is not a mapping")
    extra = set(finding) - ALLOWED
    if extra:
        raise FindingError(
            f"finding {finding.get('id', index)} has fields the report will not "
            f"carry: {', '.join(sorted(extra))}. Cite a figure; do not restate it."
        )
    missing = [key for key in REQUIRED if not str(finding.get(key) or "").strip()]
    if missing:
        raise FindingError(
            f"finding {finding.get('id', index)} is missing {', '.join(missing)}"
        )
    lines = [
        f"## {finding['id']}. {finding['statement']}",
        "",
        f"- Severity: {finding.get('severity', 'unspecified')}",
        f"- Owner: {finding.get('owner', 'unspecified')}",
        f"- Evidence: {finding['evidence']}",
    ]
    if finding.get("figure_ref"):
        lines.append(f"- Figure: {finding['figure_ref']}")
    lines.append(f"- Recommendation: {finding['recommendation']}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render assessment findings")
    parser.add_argument("engagement", type=Path)
    parser.add_argument("findings", type=Path)
    args = parser.parse_args(argv)
    engagement = yaml.safe_load(args.engagement.read_text())
    findings = yaml.safe_load(args.findings.read_text())
    sys.stdout.write(render_findings(engagement, findings))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FindingError as exc:
        sys.stderr.write(f"{exc}\n")
        raise SystemExit(2) from exc
