"""Tenant DDL loading and the bitemporal read predicate.

The tenant schema is assembled from two sources: the chassis scripts in this
package (schemas, raw and lineage, the AI boundary, pack pinning, bitemporal
helpers, uniqueness, reconciliation) and the domain scripts owned by whichever
pack the tenant was provisioned against. Both are numbered in one sequence, so
a pack script that depends on a chassis table has to claim a higher number and
that dependency stays visible in the filename.
"""

from __future__ import annotations

from pathlib import Path

from fracture.packs import Pack, get_pack

DDL_DIR = Path(__file__).parent / "ddl"


def chassis_ddl_scripts() -> list[tuple[str, str]]:
    """The domain-free scripts every tenant gets, whatever its pack."""
    paths = sorted(DDL_DIR.glob("*.sql"))
    if not paths:  # pragma: no cover - packaging error
        raise FileNotFoundError(f"no chassis DDL found under {DDL_DIR}")
    return [(p.name, p.read_text()) for p in paths]


def tenant_ddl_scripts(pack: Pack | str | None = None) -> list[tuple[str, str]]:
    """Ordered (name, sql) pairs for one tenant database.

    Ordering is lexical on the numeric prefix across both layers, so
    `020_canon_entity.sql` from a pack runs after `010_raw_lineage.sql` from the
    chassis and before `060_bitemporal.sql`.
    """
    resolved = pack if isinstance(pack, Pack) else get_pack(pack)
    scripts = chassis_ddl_scripts() + resolved.ddl_scripts()
    return sorted(scripts, key=lambda pair: pair[0])


def ddl_checksum(pack: Pack | str | None = None) -> bytes:
    """Checksum of the full tenant schema for one pack.

    Recorded against every migration run so a tenant whose schema drifted from
    the shipped DDL is visible in the control plane rather than at the next
    failed mart build.
    """
    from fracture.core.hashing import sha256_bytes

    joined = "\n".join(sql for _, sql in tenant_ddl_scripts(pack))
    return sha256_bytes(joined.encode("utf-8"))
