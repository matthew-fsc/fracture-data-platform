"""Packs: pinned figures, byte-identical reissue, and drill-through."""

from fracture.pack.build import (
    PackBuilder,
    PackBuildResult,
    SectionDef,
    compute_content_hash,
    figures_for,
    load_sections,
    restatement_delta,
    verify_reproducible,
)
from fracture.pack.drill import DrillResult, Evidence, assert_drillable, resolve

__all__ = [
    "PackBuilder", "PackBuildResult", "SectionDef", "load_sections",
    "compute_content_hash", "figures_for", "verify_reproducible",
    "restatement_delta", "resolve", "assert_drillable", "DrillResult", "Evidence",
]
