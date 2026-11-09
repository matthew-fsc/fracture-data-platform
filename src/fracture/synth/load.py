"""Stand up a tenant from a generated estate.

This is the local `docker compose plus a synthetic tenant generator`
environment from spec 10, and the same path a real fold-in takes: register the
firm, register its sources, verify read-only, fingerprint, load raw, map to
canon. Nothing here is demo-only scaffolding.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fracture.adapters.registry import get_adapter
from fracture.control.provisioning import Provisioner
from fracture.control.registry import ControlPlane
from fracture.core.logging import get_logger
from fracture.core.secrets import EnvSecretResolver
from fracture.ingest.artifacts import ArtifactStore, LocalArtifactStore
from fracture.ingest.pipeline import SourceRunResult, SourceRunner
from fracture.synth.generator import GeneratedEstate

log = get_logger("synth.load")


@dataclass
class LoadReport:
    tenant_slug: str
    results: list[SourceRunResult] = field(default_factory=list)
    errors: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def rows_loaded(self) -> int:
        return sum(r.rows_loaded for r in self.results)

    @property
    def canon_rows(self) -> int:
        return sum(r.canon.inserted for r in self.results)

    def summary(self) -> str:
        return (
            f"{self.tenant_slug}: {len(self.results)} source runs, "
            f"{self.rows_loaded} raw rows, {self.canon_rows} canonical rows"
            + (f", {len(self.errors)} errors" if self.errors else "")
        )


def load_estate(
    estate: GeneratedEstate,
    control: ControlPlane | None = None,
    store: ArtifactStore | None = None,
    system_time: dt.datetime | None = None,
    provision: bool = True,
) -> tuple[Any, LoadReport]:
    control = control or ControlPlane(secret_resolver=EnvSecretResolver())
    spec = estate.spec

    tenant = control.find_tenant(spec.tenant_slug)
    if tenant is None:
        tenant = control.register_tenant(
            slug=spec.tenant_slug,
            legal_name=spec.tenant_name,
            motion=spec.motion,
            archive_after=(
                dt.date.today() + dt.timedelta(days=30) if spec.motion == "diligence" else None
            ),
        )
    if provision:
        tenant = Provisioner(control).provision(tenant)

    store = store or LocalArtifactStore()
    runner = SourceRunner(control, tenant, store, system_time=system_time)
    report = LoadReport(tenant_slug=tenant.slug)

    for generated in estate.firms:
        firm = generated.firm
        control.add_firm(
            tenant, firm.firm_id, firm.legal_name, firm.role,
            close_date=firm.close_date,
            folded_in_at=dt.datetime.now(dt.timezone.utc) if firm.role == "addon" else None,
        )
        creds = {"export_dir": str(generated.export_dir), "read_only": True}
        for source_id in firm.sources:
            secret_path = f"{tenant.s3_prefix}/sources/{firm.firm_id}/{source_id}"
            if isinstance(control.secrets, EnvSecretResolver):
                control.secrets.put(secret_path, creds)
            control.register_source(tenant, firm.firm_id, source_id, secret_path)

            adapter_cls = get_adapter(source_id)
            adapter = adapter_cls(firm_id=firm.firm_id)
            # The read-only proof is recorded before the source can go live; the
            # database constraint refuses 'live' without it.
            if adapter.verify_read_only(creds):
                control.verify_read_only(
                    tenant, firm.firm_id, source_id, verified_by="synth:generator"
                )
                control.activate_source(tenant, firm.firm_id, source_id)

            try:
                result = runner.run(adapter, creds)
                report.results.append(result)
            except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
                control.fail_source(tenant, firm.firm_id, source_id, str(exc))
                report.errors.append((firm.firm_id, source_id, str(exc)))
                log.error("source run failed %s/%s: %s", firm.firm_id, source_id, exc)
                raise

        # The custodian's own control totals, loaded for the reconciliation asset.
        if "schwab_custodian" in firm.sources:
            _load_control_totals(control, tenant, firm.firm_id, generated.export_dir)

    log.info(report.summary())
    return tenant, report


def _load_control_totals(control: ControlPlane, tenant, firm_id: str, export_dir: Path) -> None:
    from fracture.adapters.sources.schwab_custodian import SchwabCustodianAdapter
    from fracture.core import db

    adapter = SchwabCustodianAdapter(firm_id=firm_id)
    totals = adapter.control_totals({"export_dir": str(export_dir), "read_only": True})
    if not totals:
        return
    rows = [
        (
            firm_id, "schwab_custodian", "aum_total", t["as_of_date"],
            t["as_of_date"] + dt.timedelta(days=1), "", t["total_value"],
        )
        for t in totals
    ]
    with control.tenant_connection(tenant, "transform") as conn:
        db.execute(conn, "delete from recon.control_total where firm_id=%s and check_name='aum_total'", (firm_id,))
        db.execute_values(
            conn,
            """
            insert into recon.control_total
              (firm_id, source_id, check_name, period_start, period_end, grain_key, expected_value)
            values %s
            """,
            rows,
        )
