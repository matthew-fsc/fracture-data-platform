"""Loading a synthetic practice through the artifact-first path.

Same path a real engagement takes: extract to the object store, hash it, load
raw from the stored artifact, map to canon with row-grain lineage. No shortcut
that writes canon directly, because a shortcut here would mean the pack's
lineage and reconciliation were never exercised and the first client would be
the test.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fracture.adapters.sources.generic_csv import CsvMappingConfig, GenericCsvAdapter
from fracture.control.provisioning import Provisioner, ensure_streams
from fracture.control.registry import ControlPlane
from fracture.core.logging import get_logger
from fracture.core.secrets import EnvSecretResolver
from fracture.ingest.pipeline import SourceRunner
from fracture.ingest.artifacts import ArtifactStore, LocalArtifactStore
from fracture.packs.services.synth import STREAMS, GeneratedPractice

log = get_logger("packs.services.load")


@dataclass
class PracticeLoadReport:
    tenant_slug: str
    results: list[Any] = field(default_factory=list)

    @property
    def raw_rows(self) -> int:
        return sum(r.rows_loaded for r in self.results)

    @property
    def canon_rows(self) -> int:
        return sum(r.canon.inserted + r.canon.superseded for r in self.results)

    def summary(self) -> str:
        return (
            f"loaded {self.tenant_slug}: {len(self.results)} stream run(s), "
            f"{self.raw_rows} raw rows, {self.canon_rows} canonical rows"
        )


def load_practice(
    practice: GeneratedPractice,
    control: ControlPlane | None = None,
    store: ArtifactStore | None = None,
    system_time: dt.datetime | None = None,
    provision: bool = True,
) -> tuple[Any, PracticeLoadReport]:
    control = control or ControlPlane(secret_resolver=EnvSecretResolver())
    spec = practice.spec

    tenant = control.find_tenant(spec.tenant_slug)
    if tenant is None:
        tenant = control.register_tenant(
            slug=spec.tenant_slug,
            legal_name=spec.tenant_name,
            motion=spec.motion,
            archive_after=(
                dt.date.today() + dt.timedelta(days=30)
                if spec.motion == "diligence" else None
            ),
            pack="services",
            engagement_code="INTERNAL-OPS",
        )
    if provision:
        tenant = Provisioner(control).provision(tenant)
    # Raw DDL is a migration run as owner, never created at load time: a stream
    # auto-created by the loader means a renamed stream silently fills a new
    # empty table while the old one goes stale.
    ensure_streams(control, tenant, STREAMS)

    control.add_firm(tenant, spec.firm_id, spec.tenant_name, "practice")

    creds = {"export_dir": str(practice.export_dir), "read_only": True}
    secret_path = f"{tenant.s3_prefix}/sources/{spec.firm_id}/generic_csv"
    if isinstance(control.secrets, EnvSecretResolver):
        control.secrets.put(secret_path, creds)
    control.register_source(tenant, spec.firm_id, "generic_csv", secret_path)

    config = CsvMappingConfig.from_yaml(practice.mapping_path)
    adapter = GenericCsvAdapter(firm_id=spec.firm_id, config=config)

    if adapter.verify_read_only(creds):
        control.verify_read_only(
            tenant, spec.firm_id, "generic_csv", verified_by="services:synth"
        )
        control.activate_source(tenant, spec.firm_id, "generic_csv")

    store = store or LocalArtifactStore()
    runner = SourceRunner(
        control, tenant, store, system_time=system_time, pack="services"
    )
    report = PracticeLoadReport(tenant_slug=tenant.slug)
    try:
        report.results.append(runner.run(adapter, creds))
    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
        control.fail_source(tenant, spec.firm_id, "generic_csv", str(exc))
        log.error("services load failed: %s", exc)
        raise

    log.info(report.summary())
    return tenant, report
