"""Reporting entry point for Fracture OS.

`list_sections` reads the pack SQL in git. Building a pack is `fracture pack
build`, which needs a tenant; this module returns that argv and does not open
a database.
"""

from __future__ import annotations


def list_sections() -> list[dict]:
    from fracture.pack.build import load_sections

    return [
        {
            "section": section.section,
            "title": section.title,
            "order": section.order,
            "file": section.path.name,
        }
        for section in load_sections()
    ]


def build_command(slug: str, period_end: str) -> list[str]:
    if not str(slug).strip() or not str(period_end).strip():
        raise ValueError("slug and period_end are required")
    return ["fracture", "pack", "build", slug, "--period-end", period_end]


def verify_command(slug: str, pack_run_id: str) -> list[str]:
    if not str(slug).strip() or not str(pack_run_id).strip():
        raise ValueError("slug and pack_run_id are required")
    return ["fracture", "pack", "verify", slug, pack_run_id]
