#!/usr/bin/env python3
"""Build the synthetic demo end to end and render the pack.

    python scripts/demo.py [--scale small|demo] [--out out/pack.html]

Runs the whole platform against a generated estate: provision the tenant, load
every source through its adapter, map to canon with lineage, build the marts,
reconcile, pin a pack and render it. Nothing here is demo-only: it is the same
code path a real tenant takes, pointed at generated exports instead of a
client's systems.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("FRACTURE_PG_HOST", "127.0.0.1")
os.environ.setdefault("FRACTURE_ARTIFACT_ROOT", str(ROOT / "out" / "artifacts"))
os.environ.setdefault("FRACTURE_SECRET_ROOT", str(ROOT / "out" / "secrets"))

from fracture.adapters import estimate_fold_in  # noqa: E402
from fracture.control import ControlPlane  # noqa: E402
from fracture.core import db  # noqa: E402
from fracture.core.secrets import EnvSecretResolver  # noqa: E402
from fracture.pack import PackBuilder, assert_drillable  # noqa: E402
from fracture.pack.data import collect  # noqa: E402
from fracture.pack.render import write as write_pack  # noqa: E402
from fracture.synth import DEMO_ESTATE, TEST_ESTATE, EstateGenerator, load_estate  # noqa: E402

SCALES = {"demo": DEMO_ESTATE, "small": TEST_ESTATE}


def banner(text: str) -> None:
    print(f"\n\033[1m{text}\033[0m", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", choices=sorted(SCALES), default="demo")
    parser.add_argument("--out", default=str(ROOT / "out" / "pack.html"))
    parser.add_argument("--synth-dir", default=str(ROOT / "out" / "synth"))
    parser.add_argument("--period-end", default=None, help="YYYY-MM-DD, defaults to the estate's")
    parser.add_argument("--reset", action="store_true", help="drop and rebuild the tenant database")
    args = parser.parse_args()

    spec = SCALES[args.scale]
    started = time.perf_counter()

    control = ControlPlane(secret_resolver=EnvSecretResolver())
    control.install_schema()

    banner(f"1. Generating the synthetic estate ({args.scale})")
    estate = EstateGenerator(spec, args.synth_dir).generate()
    for firm in estate.firms:
        print(f"   {firm.firm.firm_id:5s} {firm.firm.legal_name:32s} {firm.counts}")

    if args.reset:
        existing = control.find_tenant(spec.tenant_slug)
        if existing is not None:
            print(f"   resetting tenant {existing.slug} (dropping {existing.db_name})")
            _drop_database(control, existing.db_name)
            with control.connection() as conn:
                db.execute(conn, "delete from control.tenant where slug=%s", (spec.tenant_slug,))

    banner("2. Provisioning the tenant and loading every source")
    tenant, report = load_estate(estate, control=control)
    print(f"   {report.summary()}")
    for firm in control.list_firms(tenant):
        sources = control.list_sources(tenant, firm.firm_id)
        live = sum(1 for s in sources if s.status == "live")
        print(f"   {firm.firm_id:5s} {firm.role:8s} {live}/{len(sources)} sources live, read-only verified")

    period_end = (
        dt.date.fromisoformat(args.period_end) if args.period_end else spec.period_end
    )
    period_start = dt.date(period_end.year, period_end.month - 2, 1)
    system_time = dt.datetime.now(dt.timezone.utc)

    banner("3. Building marts, reconciling, and pinning the pack")
    builder = PackBuilder(control, tenant)
    result = builder.build(period_start, period_end, system_time=system_time)
    print(f"   {result.figure_count} figures, content hash {result.content_hash_hex[:32]}")
    print(f"   reconciliation: {result.recon.summary()}")

    banner("4. Verifying the reproducibility guarantee")
    reissued = builder.build(period_start, period_end, system_time=system_time)
    identical = reissued.content_hash == result.content_hash
    print(f"   reissue at the same system time: "
          f"{'byte-identical' if identical else 'DIVERGED'} ({reissued.content_hash_hex[:16]})")
    if not identical:
        raise SystemExit("reproducibility guarantee failed")

    banner("5. Checking every figure opens to raw records")
    with control.tenant_connection(tenant, "transform") as conn:
        assert_drillable(conn, result.pack_run.pack_run_id)
        print("   every lineaged figure resolves to raw payloads and their artifacts")

        firms = [
            {"firm_id": f.firm_id, "legal_name": f.legal_name, "role": f.role}
            for f in control.list_firms(tenant)
        ]
        systems = sorted({s for f in spec.firms for s in f.sources})
        coverage = estimate_fold_in(systems).as_dict()
        data = collect(conn, tenant, result.pack_run.pack_run_id, firms, coverage)

    banner("6. Rendering the pack")
    out = write_pack(data, args.out)
    size_kb = out.stat().st_size / 1024
    print(f"   {out}  ({size_kb:.0f} KB)")

    summary = {
        "tenant": tenant.slug,
        "period_end": period_end.isoformat(),
        "system_time": system_time.isoformat(),
        "content_hash": result.content_hash_hex,
        "figures": result.figure_count,
        "raw_rows": report.rows_loaded,
        "canonical_rows": report.canon_rows,
        "recon": result.recon.summary(),
        "fold_in_coverage": coverage["weighted_coverage_pct"],
        "pack": str(out),
        "elapsed_s": round(time.perf_counter() - started, 1),
    }
    (Path(args.out).parent / "demo_summary.json").write_text(json.dumps(summary, indent=2))
    banner(f"Done in {summary['elapsed_s']}s")
    for key, value in summary.items():
        print(f"   {key:20s} {value}")
    return 0


def _drop_database(control: ControlPlane, db_name: str) -> None:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    conn = psycopg2.connect(control.settings.control_dsn)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity where datname = %s",
                (db_name,),
            )
            cur.execute(f'drop database if exists "{db_name}"')
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
