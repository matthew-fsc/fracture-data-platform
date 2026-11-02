# NOTE: no `from __future__ import annotations` -- see assets.py.

import os

from dagster import (
    AssetSelection,
    DefaultSensorStatus,
    Definitions,
    RunRequest,
    ScheduleDefinition,
    SensorEvaluationContext,
    SkipReason,
    define_asset_job,
    sensor,
)

from fracture.control.registry import ControlPlane
from fracture.core.secrets import EnvSecretResolver
from fracture.orchestration.assets import (
    TENANT_PARTITIONS,
    canonical_layer,
    ingested_sources,
    marts,
    monthly_pack,
    reconciliation,
    tenant_migrations,
)
from fracture.orchestration.resources import ArtifactStoreResource, ControlPlaneResource

ALL_ASSETS = [
    ingested_sources,
    canonical_layer,
    marts,
    reconciliation,
    monthly_pack,
    tenant_migrations,
]

refresh_job = define_asset_job(
    name="tenant_refresh",
    selection=AssetSelection.assets(ingested_sources, canonical_layer, marts, reconciliation),
    description="Daily refresh for one tenant and one date.",
)

pack_job = define_asset_job(
    name="monthly_pack",
    selection=AssetSelection.assets(monthly_pack),
    description="Pin and issue the monthly pack for one tenant.",
)

migration_job = define_asset_job(
    name="tenant_migrations",
    selection=AssetSelection.assets(tenant_migrations),
    description="Fan the tenant DDL out over the registry.",
)

migration_schedule = ScheduleDefinition(
    job=migration_job,
    cron_schedule="0 5 * * *",
    name="nightly_migration_fanout",
    description="Keep every tenant on the current schema; the job fails below the success threshold.",
)


@sensor(
    name="tenant_registry_sensor",
    minimum_interval_seconds=60,
    default_status=DefaultSensorStatus.RUNNING,
    description=(
        "Register a Dagster partition for every active tenant in the control "
        "plane. Tenant standup therefore does not require a code deploy."
    ),
)
def tenant_registry_sensor(context: SensorEvaluationContext):
    control = ControlPlane(
        secret_resolver=EnvSecretResolver(os.environ.get("FRACTURE_SECRET_ROOT"))
    )
    try:
        slugs = [t.slug for t in control.list_tenants(status="active")]
    except Exception as exc:  # noqa: BLE001 - a sensor must not crash the daemon
        return SkipReason(f"control plane unreachable: {exc}")

    existing = set(context.instance.get_dynamic_partitions(TENANT_PARTITIONS.name))
    added = sorted(set(slugs) - existing)
    # Removals are deliberate and manual. Dropping a partition discards its
    # materialisation history, which is the audit trail for a tenant we may
    # still owe an export to.
    if not added:
        return SkipReason(f"{len(existing)} tenant partition(s) already registered")
    context.instance.add_dynamic_partitions(TENANT_PARTITIONS.name, added)
    return SkipReason(f"registered tenant partition(s): {', '.join(added)}")


defs = Definitions(
    assets=ALL_ASSETS,
    jobs=[refresh_job, pack_job, migration_job],
    schedules=[migration_schedule],
    sensors=[tenant_registry_sensor],
    resources={
        "control_plane": ControlPlaneResource(
            secret_root=os.environ.get("FRACTURE_SECRET_ROOT")
        ),
        "artifacts": ArtifactStoreResource(),
    },
)
