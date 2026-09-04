"""Tenant DDL loading and the bitemporal read predicate."""

from __future__ import annotations

from pathlib import Path

DDL_DIR = Path(__file__).parent / "ddl"


def tenant_ddl_scripts() -> list[tuple[str, str]]:
    """Ordered (name, sql) pairs for the tenant database.

    Ordering is lexical on the numeric prefix; a new script must claim a number,
    which is how a dependency on an earlier script stays visible.
    """
    paths = sorted(DDL_DIR.glob("*.sql"))
    if not paths:  # pragma: no cover - packaging error
        raise FileNotFoundError(f"no tenant DDL found under {DDL_DIR}")
    return [(p.name, p.read_text()) for p in paths]


def ddl_checksum() -> bytes:
    from fracture.core.hashing import sha256_bytes

    joined = "\n".join(sql for _, sql in tenant_ddl_scripts())
    return sha256_bytes(joined.encode("utf-8"))
