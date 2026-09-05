"""The asset graph (spec section 7).

    source_fingerprint_<source>
      -> raw_<source>_<stream>
           -> stg_<source>_<stream>
                -> canon_<entity>        (fan-in across sources)
                     -> mart_<metric>
                          -> pack_<section>

Two partition dimensions: `tenant` (dynamic, sourced from the control plane) and
`date`. Each run executes with exactly one tenant's DSN, assembled at run time.

The assets here are thin. All of the logic lives in `fracture.ingest.pipeline`,
`fracture.marts`, `fracture.recon` and `fracture.pack`, so the whole path is
testable without a Dagster instance and the orchestrator carries scheduling and
partitioning only.
"""

# NOTE: no `from __future__ import annotations` here. Dagster resolves the
# `context` parameter's annotation at decoration time, and stringised
# annotations make it fail with a misleading "must be annotated with
# AssetExecutionContext" error.

import datetime as dt
from typing import Any, Dict

from dagster import (
    AssetExecutionContext,
    AssetKey,
    Backoff,
    DailyPartitionsDefinition,
    DynamicPartitionsDefinition,
    FreshnessPolicy,
    MetadataValue,
    MultiPartitionKey,
    MultiPartitionsDefinition,
    Output,
    RetryPolicy,
    asset,
)

from fracture.adapters.registry import get_adapter
from fracture.core.errors import ReconciliationBreach
from fracture.core.logging import get_logger
from fracture.marts.runner import MartRunner
from fracture.orchestration.resources import ArtifactStoreResource, ControlPlaneResource
from fracture.pack.build import PackBuilder
from fracture.recon import checks as recon_checks

log = get_logger("orchestration.assets")

TENANT_PARTITIONS = DynamicPartitionsDefinition(name="tenant")
DATE_PARTITIONS = DailyPartitionsDefinition(start_date="2024-01-01")
TENANT_DATE = MultiPartitionsDefinition({"tenant": TENANT_PARTITIONS, "date": DATE_PARTITIONS})

#: The retainer SLA. A missed refresh should page before the client notices,
#: because the client noticing first is how a retainer gets cancelled
#: (spec section 7). Warn at 26 hours, fail at 30: a daily refresh that slips an
#: hour is not an incident, one that slipped a day is.
CANON_MAX_LAG = dt.timedelta(hours=30)
CANON_FRESHNESS = FreshnessPolicy.time_window(
    fail_window=CANON_MAX_LAG, warn_window=dt.timedelta(hours=26)
)

RETRY = RetryPolicy(max_retries=2, delay=30, backoff=Backoff.EXPONENTIAL)


def _partition(context: AssetExecutionContext) -> tuple[str, dt.date]:
    key = context.partition_key
    if isinstance(key, MultiPartitionKey):
        return key.keys_by_dimension["tenant"], dt.date.fromisoformat(
            key.keys_by_dimension["date"]
        )
    return str(key), dt.date.today()


@asset(
    partitions_def=TENANT_DATE,
    group_name="ingest",
    retry_policy=RETRY,
    description=(
        "Fingerprint every registered source, load raw, map to canon. "
        "Fingerprint runs first on every refresh so a schema change is caught "
        "before mapping silently drops a column."
    ),
)
def ingested_sources(
    context: AssetExecutionContext,
    control_plane: ControlPlaneResource,
    artifacts: ArtifactStoreResource,
) -> Output[Dict[str, Any]]:
    from fracture.ingest.pipeline import SourceRunner

    tenant_slug, run_date = _partition(context)
    control = control_plane.client()
    tenant = control.get_tenant(tenant_slug)
    store = artifacts.store(kms_key_arn=tenant.kms_key_arn)
    runner = SourceRunner(control, tenant, store)

    summary: dict[str, Any] = {"tenant": tenant_slug, "date": run_date.isoformat(), "sources": {}}
    drift: list[str] = []
    for firm in control.list_firms(tenant):
        for source in control.list_sources(tenant, firm.firm_id):
            if source.status not in ("verified", "live"):
                context.log.warning(
                    "skipping %s/%s: status is %s, no read-only proof on file",
                    firm.firm_id, source.source_id, source.status,
                )
                continue
            creds = control.source_credentials(tenant, firm.firm_id, source.source_id)
            adapter = get_adapter(source.source_id)(firm_id=firm.firm_id)
            result = runner.run(adapter, creds)
            summary["sources"][f"{firm.firm_id}/{source.source_id}"] = {
                "raw_rows": result.rows_loaded,
                "canonical_inserted": result.canon.inserted,
                "superseded": result.canon.superseded,
                "variances": result.canon.variances,
            }
            if result.schema_drift:
                drift.append(f"{firm.firm_id}/{source.source_id}")

    return Output(
        summary,
        metadata={
            "tenant": tenant_slug,
            "sources_run": len(summary["sources"]),
            "raw_rows": sum(s["raw_rows"] for s in summary["sources"].values()),
            "canonical_rows": sum(s["canonical_inserted"] for s in summary["sources"].values()),
            "schema_drift": MetadataValue.text(", ".join(drift) or "none"),
        },
    )


@asset(
    partitions_def=TENANT_DATE,
    deps=[AssetKey("ingested_sources")],
    group_name="canon",
    freshness_policy=CANON_FRESHNESS,
    description="Canonical layer freshness gate. Fails when the canon layer is stale or empty.",
)
def canonical_layer(
    context: AssetExecutionContext, control_plane: ControlPlaneResource
) -> Output[Dict[str, Any]]:
    from fracture.core import db

    tenant_slug, _ = _partition(context)
    control = control_plane.client()
    tenant = control.get_tenant(tenant_slug)
    with control.tenant_connection(tenant, "transform") as conn:
        counts = {
            row["table_name"]: int(row["n"])
            for row in db.query(
                conn,
                """
                select c.relname as table_name, coalesce(s.n_live_tup, 0) as n
                  from pg_class c
                  join pg_namespace ns on ns.oid = c.relnamespace
                  left join pg_stat_user_tables s on s.relid = c.oid
                 where ns.nspname = 'canon' and c.relkind = 'r'
                 order by 1
                """,
            )
        }
        # An empty canonical layer is the quietest possible failure: every mart
        # renders zeros and nothing raises.
        material = ("account", "balance_snapshot", "invoice")
        empty = [t for t in material if counts.get(t, 0) == 0]
        if empty:
            raise ValueError(
                f"canonical tables are empty after ingest: {', '.join(empty)}"
            )
    return Output(counts, metadata={"tables": len(counts), "rows": sum(counts.values())})


@asset(
    partitions_def=TENANT_DATE,
    deps=[AssetKey("canonical_layer")],
    group_name="marts",
    description="Build every mart, pinned to the run's system time, with post-run assertions.",
)
def marts(
    context: AssetExecutionContext, control_plane: ControlPlaneResource
) -> Output[Dict[str, Any]]:
    tenant_slug, run_date = _partition(context)
    control = control_plane.client()
    tenant = control.get_tenant(tenant_slug)
    system_time = dt.datetime.now(dt.timezone.utc)
    with control.tenant_connection(tenant, "transform") as conn:
        result = MartRunner().run(conn, system_time)
    return Output(
        {"models": [m.name for m in result.models], "rows": result.total_rows},
        metadata={
            "models": len(result.models),
            "rows": result.total_rows,
            "system_time": system_time.isoformat(),
        },
    )


@asset(
    partitions_def=TENANT_DATE,
    deps=[AssetKey("marts")],
    group_name="assurance",
    description=(
        "Reconcile against each source system's own reported totals, with a "
        "stated tolerance and a hard failure above it. Runs every refresh."
    ),
)
def reconciliation(
    context: AssetExecutionContext, control_plane: ControlPlaneResource
) -> Output[Dict[str, Any]]:
    from fracture.ai import assert_no_violations

    tenant_slug, _ = _partition(context)
    control = control_plane.client()
    tenant = control.get_tenant(tenant_slug)
    with control.tenant_connection(tenant, "transform") as conn:
        report = recon_checks.run_all(conn)
        assert_no_violations(conn)
        for result in report.results:
            control.record_reconciliation(
                tenant, result.firm_id, result.check_name, result.period_start,
                result.period_end, result.expected, result.actual, result.variance_pct,
                result.tolerance_pct, result.passed,
            )
    metadata = {
        "checks": len(report.results),
        "failed": len(report.failures),
        "detail": MetadataValue.text(
            "\n".join(f.describe() for f in report.failures[:20]) or "all within tolerance"
        ),
    }
    if not report.passed:
        raise ReconciliationBreach(
            f"{len(report.failures)} check(s) breached tolerance: "
            + "; ".join(f.describe() for f in report.failures[:5])
        )
    return Output({"passed": True, "checks": len(report.results)}, metadata=metadata)


@asset(
    partitions_def=TENANT_DATE,
    deps=[AssetKey("reconciliation")],
    group_name="pack",
    description="Pin and issue the monthly pack. Frozen system time, hashed content.",
)
def monthly_pack(
    context: AssetExecutionContext, control_plane: ControlPlaneResource
) -> Output[Dict[str, Any]]:
    tenant_slug, run_date = _partition(context)
    control = control_plane.client()
    tenant = control.get_tenant(tenant_slug)

    period_end = run_date.replace(day=1) - dt.timedelta(days=1)
    period_start = dt.date(period_end.year, period_end.month, 1)
    previous = [p for p in control.list_pack_runs(tenant) if p.status == "issued"]

    result = PackBuilder(control, tenant).build(
        period_start=period_start,
        period_end=period_end,
        supersedes=previous[0].pack_run_id if previous else None,
    )
    return Output(
        {
            "pack_run_id": str(result.pack_run.pack_run_id),
            "content_hash": result.content_hash_hex,
            "figures": result.figure_count,
        },
        metadata={
            "pack_run_id": str(result.pack_run.pack_run_id),
            "content_hash": MetadataValue.text(result.content_hash_hex),
            "figures": result.figure_count,
            "system_time": result.pack_run.system_time.isoformat(),
        },
    )


@asset(
    group_name="platform",
    description=(
        "Apply the tenant DDL to every tenant in the registry. Fans out over the "
        "registry and requires a success threshold before the run is green."
    ),
)
def tenant_migrations(
    context: AssetExecutionContext, control_plane: ControlPlaneResource
) -> Output[Dict[str, Any]]:
    from fracture.control.migrations import Migrator

    control = control_plane.client()
    run = Migrator(control).rebuild_schema_everywhere(success_threshold=1.0)
    return Output(
        {"version": run.version, "success_rate": run.success_rate},
        metadata={
            "version": run.version,
            "tenants": len(run.attempted),
            "failed": MetadataValue.text(
                ", ".join(r.slug for r in run.failed) or "none"
            ),
        },
    )
