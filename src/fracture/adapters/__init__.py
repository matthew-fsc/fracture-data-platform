"""Source adapters: the contract, the registry, and the fold-in estimator."""

from fracture.adapters.base import (
    BaseAdapter,
    Capabilities,
    CanonicalRecord,
    Creds,
    Cursor,
    EntityCoverage,
    RecordBatch,
    SourceAdapter,
    SourceFingerprintResult,
    Stream,
    diff_schema,
    schema_hash_of,
)
from fracture.adapters.registry import (
    FoldInEstimate,
    all_adapters,
    capability_matrix,
    estimate_fold_in,
    get_adapter,
    register,
)
from fracture.adapters.staticcheck import assert_no_mutating_verbs, scan_module

__all__ = [
    "BaseAdapter", "SourceAdapter", "Capabilities", "EntityCoverage", "Stream",
    "RecordBatch", "CanonicalRecord", "SourceFingerprintResult", "Creds", "Cursor",
    "schema_hash_of", "diff_schema",
    "register", "get_adapter", "all_adapters", "capability_matrix",
    "estimate_fold_in", "FoldInEstimate",
    "assert_no_mutating_verbs", "scan_module",
]
