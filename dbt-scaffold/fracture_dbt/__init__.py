"""dbt scaffold entry point for Fracture OS.

Returns where the project is and which models it contains. Running dbt is a
separate command; this function does not connect to a warehouse.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def project() -> dict:
    models = sorted(
        path.stem
        for path in (_ROOT / "models" / "canonical").glob("*.sql")
    )
    return {
        "project_dir": str(_ROOT),
        "profile": "fracture_canonical",
        "models": models,
        "command": ["dbt", "build", "--project-dir", str(_ROOT)],
    }
