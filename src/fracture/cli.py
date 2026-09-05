"""Command line entry point.

Covers the operations a two-person team actually runs by hand: standing a tenant
up, running a fold-in, issuing a pack, checking a figure, and producing the
diligence deliverable.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from fracture.core.logging import configure, get_logger

log = get_logger("cli")


def _control():
    from fracture.control.registry import ControlPlane
    from fracture.core.secrets import default_resolver

    return ControlPlane(secret_resolver=default_resolver())


# -- commands ----------------------------------------------------------------


def cmd_control_init(args: argparse.Namespace) -> int:
    _control().install_schema()
    print("control plane schema installed")
    return 0


def cmd_tenant_register(args: argparse.Namespace) -> int:
    from fracture.control.provisioning import Provisioner

    control = _control()
    archive_after = dt.date.fromisoformat(args.archive_after) if args.archive_after else None
    tenant = control.register_tenant(
        slug=args.slug,
        legal_name=args.legal_name,
        motion=args.motion,
        kms_key_arn=args.kms_key_arn,
        db_host=args.db_host,
        archive_after=archive_after,
    )
    if args.provision:
        tenant = Provisioner(control).provision(tenant)
    print(json.dumps(
        {
            "tenant_id": str(tenant.tenant_id), "slug": tenant.slug,
            "db_name": tenant.db_name, "status": tenant.status, "motion": tenant.motion,
        },
        indent=2,
    ))
    return 0


def cmd_tenant_list(args: argparse.Namespace) -> int:
    for tenant in _control().list_tenants(status=args.status, motion=args.motion):
        archive = f" archive_after={tenant.archive_after}" if tenant.archive_after else ""
        print(f"{tenant.slug:28s} {tenant.motion:10s} {tenant.status:12s} {tenant.db_name}{archive}")
    return 0


def cmd_tenant_export(args: argparse.Namespace) -> int:
    from fracture.control.provisioning import Provisioner

    control = _control()
    tenant = control.get_tenant(args.slug)
    print(" ".join(Provisioner(control).export_command(tenant)))
    return 0


def cmd_tenant_promote(args: argparse.Namespace) -> int:
    """Diligence to operating, in place. Not a rebuild (spec section 13)."""
    tenant = _control().promote_tenant(args.slug)
    print(f"{tenant.slug} is now {tenant.motion} (status {tenant.status})")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    from fracture.control.migrations import Migrator

    run = Migrator(_control()).rebuild_schema_everywhere(
        success_threshold=args.success_threshold
    )
    print(run.summary())
    return 0 if not run.failed else 1


def cmd_pack_build(args: argparse.Namespace) -> int:
    from fracture.pack import PackBuilder

    control = _control()
    tenant = control.get_tenant(args.slug)
    period_end = dt.date.fromisoformat(args.period_end)
    period_start = (
        dt.date.fromisoformat(args.period_start)
        if args.period_start
        else dt.date(period_end.year, period_end.month - 2, 1)
    )
    result = PackBuilder(control, tenant).build(period_start, period_end)
    print(json.dumps(
        {
            "pack_run_id": str(result.pack_run.pack_run_id),
            "content_hash": result.content_hash_hex,
            "figures": result.figure_count,
            "reconciliation": result.recon.summary() if result.recon else None,
        },
        indent=2,
    ))
    return 0


def cmd_pack_verify(args: argparse.Namespace) -> int:
    import uuid

    from fracture.pack import verify_reproducible

    control = _control()
    tenant = control.get_tenant(args.slug)
    digest = verify_reproducible(control, tenant, uuid.UUID(args.pack_run_id))
    print(f"reproduced: {digest.hex()}")
    return 0


def cmd_pack_render(args: argparse.Namespace) -> int:
    import uuid

    from fracture.adapters import estimate_fold_in
    from fracture.pack.data import collect
    from fracture.pack.render import write

    control = _control()
    tenant = control.get_tenant(args.slug)
    pack_run_id = uuid.UUID(args.pack_run_id) if args.pack_run_id else None
    if pack_run_id is None:
        issued = [p for p in control.list_pack_runs(tenant) if p.status == "issued"]
        if not issued:
            print(f"no issued pack for {tenant.slug}", file=sys.stderr)
            return 1
        pack_run_id = issued[0].pack_run_id

    firms = [
        {"firm_id": f.firm_id, "legal_name": f.legal_name, "role": f.role}
        for f in control.list_firms(tenant)
    ]
    source_ids = sorted({s.source_id for s in control.list_sources(tenant)})
    with control.tenant_connection(tenant, "transform") as conn:
        data = collect(conn, tenant, pack_run_id, firms, estimate_fold_in(source_ids).as_dict())
    out = write(data, args.out)
    print(out)
    return 0


def cmd_dashboards(args: argparse.Namespace) -> int:
    """Render the six departmental operating views."""
    from fracture.pack.dashboard import write
    from fracture.pack.dashboard_data import collect

    control = _control()
    tenant = control.get_tenant(args.slug)
    firms = [
        {
            "firm_id": f.firm_id, "legal_name": f.legal_name, "role": f.role,
            "close_date": f.close_date.isoformat() if f.close_date else None,
        }
        for f in control.list_firms(tenant)
    ]
    with control.tenant_connection(tenant, "transform") as conn:
        data = collect(conn, tenant, firms)
    print(write(data, args.out))
    return 0


def cmd_drill(args: argparse.Namespace) -> int:
    from fracture.pack.drill import resolve

    control = _control()
    tenant = control.get_tenant(args.slug)
    with control.tenant_connection(tenant, "reader" if args.reader else "transform") as conn:
        result = resolve(conn, args.figure, limit=args.limit)
        control.log_access(
            tenant, args.actor, f"drill:{args.figure}", actor_kind="human",
            row_count=len(result.evidence), purpose=args.purpose,
        )
    print(json.dumps(result.as_dict(), indent=2, default=str))
    return 0


def cmd_recon(args: argparse.Namespace) -> int:
    from fracture.recon import run_all

    control = _control()
    tenant = control.get_tenant(args.slug)
    with control.tenant_connection(tenant, "transform") as conn:
        report = run_all(conn)
    print(report.summary())
    for failure in report.failures:
        print(f"  FAIL {failure.describe()}")
    return 0 if report.passed else 1


def cmd_adapters(args: argparse.Namespace) -> int:
    from fracture.adapters import all_adapters

    for source_id, cls in sorted(all_adapters().items()):
        caps = cls.capabilities
        entities = ", ".join(sorted(caps.entity_names()))
        print(
            f"{source_id:22s} tier {caps.tier}  {caps.vertical:11s} {caps.delivery:8s} "
            f"{caps.fold_in_hours:5.1f}h  {entities}"
        )
    return 0


def cmd_estimate(args: argparse.Namespace) -> int:
    """The diligence deliverable: fold-in cost computed from adapter coverage."""
    from fracture.adapters import estimate_fold_in
    from fracture.adapters.registry import (
        INSURANCE_REQUIRED_ENTITIES,
        WEALTH_REQUIRED_ENTITIES,
    )

    required = (
        INSURANCE_REQUIRED_ENTITIES if args.vertical == "insurance"
        else WEALTH_REQUIRED_ENTITIES
    )
    estimate = estimate_fold_in(args.systems, required_entities=required)
    print(json.dumps(estimate.as_dict(), indent=2))
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    import subprocess

    script = Path(__file__).resolve().parents[2] / "scripts" / "demo.py"
    return subprocess.call([sys.executable, str(script), *args.rest])


# -- parser ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fracture", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    control = sub.add_parser("control", help="control plane operations")
    control_sub = control.add_subparsers(dest="control_command", required=True)
    control_sub.add_parser("init", help="install the control plane schema").set_defaults(
        func=cmd_control_init
    )

    tenant = sub.add_parser("tenant", help="tenant lifecycle")
    tenant_sub = tenant.add_subparsers(dest="tenant_command", required=True)

    reg = tenant_sub.add_parser("register", help="register and optionally provision a tenant")
    reg.add_argument("--slug", required=True)
    reg.add_argument("--legal-name", required=True)
    reg.add_argument("--motion", choices=("diligence", "operating"), required=True)
    reg.add_argument("--kms-key-arn")
    reg.add_argument("--db-host")
    reg.add_argument("--archive-after", help="YYYY-MM-DD; required for diligence tenants")
    reg.add_argument("--provision", action="store_true")
    reg.set_defaults(func=cmd_tenant_register)

    lst = tenant_sub.add_parser("list")
    lst.add_argument("--status")
    lst.add_argument("--motion")
    lst.set_defaults(func=cmd_tenant_list)

    exp = tenant_sub.add_parser("export", help="print the contractual full-export command")
    exp.add_argument("slug")
    exp.set_defaults(func=cmd_tenant_export)

    promote = tenant_sub.add_parser("promote", help="diligence -> operating, in place")
    promote.add_argument("slug")
    promote.set_defaults(func=cmd_tenant_promote)

    migrate = sub.add_parser("migrate", help="fan the tenant DDL out over the registry")
    migrate.add_argument("--success-threshold", type=float, default=1.0)
    migrate.set_defaults(func=cmd_migrate)

    pack = sub.add_parser("pack", help="pack operations")
    pack_sub = pack.add_subparsers(dest="pack_command", required=True)

    build = pack_sub.add_parser("build")
    build.add_argument("slug")
    build.add_argument("--period-end", required=True)
    build.add_argument("--period-start")
    build.set_defaults(func=cmd_pack_build)

    verify = pack_sub.add_parser("verify", help="rebuild at the pinned system time and compare hashes")
    verify.add_argument("slug")
    verify.add_argument("pack_run_id")
    verify.set_defaults(func=cmd_pack_verify)

    render = pack_sub.add_parser("render")
    render.add_argument("slug")
    render.add_argument("--pack-run-id")
    render.add_argument("--out", default="out/pack.html")
    render.set_defaults(func=cmd_pack_render)

    dash = sub.add_parser("dashboards", help="render the departmental operating views")
    dash.add_argument("slug")
    dash.add_argument("--out", default="out/dashboards.html")
    dash.set_defaults(func=cmd_dashboards)

    drill = sub.add_parser("drill", help="open a figure to the records behind it")
    drill.add_argument("slug")
    drill.add_argument("figure", help="e.g. mart.unbilled|MWP|MWP-HH-00042")
    drill.add_argument("--limit", type=int, default=10)
    drill.add_argument("--actor", default="cli")
    drill.add_argument("--purpose", default=None)
    drill.add_argument("--reader", action="store_true", help="use the read-only role")
    drill.set_defaults(func=cmd_drill)

    recon = sub.add_parser("recon", help="run the reconciliation suite")
    recon.add_argument("slug")
    recon.set_defaults(func=cmd_recon)

    sub.add_parser("adapters", help="list registered adapters and their coverage").set_defaults(
        func=cmd_adapters
    )

    estimate = sub.add_parser("estimate", help="fold-in cost from adapter coverage")
    estimate.add_argument("systems", nargs="+")
    estimate.add_argument("--vertical", choices=("wealth", "insurance"), default="wealth")
    estimate.set_defaults(func=cmd_estimate)

    demo = sub.add_parser("demo", help="run the synthetic demo end to end")
    demo.add_argument("rest", nargs=argparse.REMAINDER)
    demo.set_defaults(func=cmd_demo)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure("DEBUG" if args.verbose else None)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        log.error("%s: %s", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
