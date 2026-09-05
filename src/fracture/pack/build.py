"""Building a pack.

A pack run pins system time. Everything read while it is open is read as of that
instant, so:

  reissue with the same system_time  -> byte-identical numbers
  reissue with a new system_time     -> the restatement

and the delta between the two is itself a report (spec 6.3). `content_hash` is
what makes the first claim checkable rather than aspirational: it is the SHA-256
of the canonically-ordered figures, stored on the pack run, and a reissue that
does not reproduce it fails.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from psycopg2.extensions import connection as PGConnection

from fracture.control.models import PackRun, Tenant
from fracture.control.registry import ControlPlane
from fracture.core import db
from fracture.core.errors import PackIntegrityError
from fracture.core.hashing import record_hash
from fracture.core.logging import get_logger
from fracture.core.timeutil import freeze_system_time
from fracture.marts.runner import MartRunner
from fracture.recon import checks as recon_checks

log = get_logger("pack.build")

PACK_DIR = Path(__file__).resolve().parents[3] / "reporting" / "packs"
_META = re.compile(r"--\s*(section|title|order):\s*(.+)")

#: Columns every pack query must return, in this order.
FIGURE_COLUMNS = (
    "metric", "firm_id", "grain_key", "grain_label",
    "numeric_value", "text_value", "unit", "sort_order", "drill_query",
)


@dataclass(frozen=True)
class SectionDef:
    section: str
    title: str
    order: int
    sql: str
    path: Path


def load_sections(directory: Path | None = None) -> list[SectionDef]:
    """Pack definitions are SQL in git, so a section change is a reviewable diff
    and a client asking "why did this section change" gets an answer."""
    directory = Path(directory) if directory else PACK_DIR
    sections: list[SectionDef] = []
    for path in sorted(directory.glob("*.sql")):
        sql = path.read_text()
        meta = {k: v.strip() for k, v in _META.findall(sql)}
        if "section" not in meta:
            raise PackIntegrityError(f"{path.name} does not declare `-- section:`")
        sections.append(
            SectionDef(
                section=meta["section"],
                title=meta.get("title", meta["section"]),
                order=int(meta.get("order", "999")),
                sql=sql,
                path=path,
            )
        )
    if not sections:  # pragma: no cover - packaging error
        raise FileNotFoundError(f"no pack sections under {directory}")
    return sorted(sections, key=lambda s: s.order)


@dataclass
class PackBuildResult:
    pack_run: PackRun
    tenant_slug: str
    content_hash: bytes
    figure_count: int
    sections: list[str] = field(default_factory=list)
    recon: recon_checks.ReconReport | None = None

    @property
    def content_hash_hex(self) -> str:
        return self.content_hash.hex()


class PackBuilder:
    def __init__(
        self,
        control: ControlPlane,
        tenant: Tenant,
        sections: Sequence[SectionDef] | None = None,
    ) -> None:
        self.control = control
        self.tenant = tenant
        self.sections = list(sections) if sections is not None else load_sections()

    def build(
        self,
        period_start: dt.date,
        period_end: dt.date,
        system_time: dt.datetime | None = None,
        supersedes: uuid.UUID | None = None,
        rebuild_marts: bool = True,
        require_recon: bool = True,
        max_recon_failures: int = 0,
    ) -> PackBuildResult:
        system_time = system_time or dt.datetime.now(dt.timezone.utc)
        pack_run = self.control.open_pack_run(
            self.tenant, period_start, period_end, system_time, supersedes=supersedes
        )
        log.info(
            "pack %s: tenant=%s period=%s..%s system_time=%s",
            pack_run.pack_run_id, self.tenant.slug, period_start, period_end,
            system_time.isoformat(),
        )
        try:
            with freeze_system_time(system_time):
                with self.control.tenant_connection(self.tenant, "transform") as conn:
                    if rebuild_marts:
                        MartRunner().run(conn, system_time)
                    report = recon_checks.run_all(conn)
                    if require_recon:
                        # A pack built on numbers that did not reconcile is worse
                        # than no pack: it looks authoritative.
                        report.raise_if_failed(max_failures=max_recon_failures)
                    figures = self._collect_figures(conn, pack_run, period_end)
                    self._write_figures(conn, pack_run, figures)
                    content_hash = compute_content_hash(figures)
                    self._write_manifest(conn, pack_run, content_hash, len(figures))
        except Exception:
            self.control.fail_pack_run(pack_run.pack_run_id)
            raise

        self.control.issue_pack_run(pack_run.pack_run_id, content_hash)
        result = PackBuildResult(
            pack_run=pack_run,
            tenant_slug=self.tenant.slug,
            content_hash=content_hash,
            figure_count=len(figures),
            sections=[s.section for s in self.sections],
            recon=report,
        )
        log.info(
            "pack %s issued: %d figures, hash=%s",
            pack_run.pack_run_id, result.figure_count, result.content_hash_hex[:16],
        )
        return result

    # -- internals ---------------------------------------------------------

    def _collect_figures(
        self, conn: PGConnection, pack_run: PackRun, period_end: dt.date
    ) -> list[dict[str, Any]]:
        figures: list[dict[str, Any]] = []
        params = {
            "period_end": period_end,
            "period_start": pack_run.period_start,
            "system_time": pack_run.system_time,
        }
        for section in self.sections:
            rows = db.query(conn, section.sql, params)
            for row in rows:
                missing = [c for c in FIGURE_COLUMNS if c not in row]
                if missing:
                    raise PackIntegrityError(
                        f"pack section {section.section} is missing column(s) "
                        f"{', '.join(missing)}; every section must return {FIGURE_COLUMNS}"
                    )
                figures.append({"section": section.section, **{c: row[c] for c in FIGURE_COLUMNS}})
            if not rows:
                # A silently empty section renders as a blank page in a board
                # pack. Fail the build instead.
                raise PackIntegrityError(
                    f"pack section {section.section} produced no figures for period "
                    f"ending {period_end}; the marts behind it are empty or the "
                    "period is wrong"
                )
        return figures

    def _write_figures(
        self, conn: PGConnection, pack_run: PackRun, figures: Sequence[dict[str, Any]]
    ) -> None:
        db.execute(
            conn, "delete from pack.figure where pack_run_id = %s", (pack_run.pack_run_id,)
        )
        db.execute_values(
            conn,
            """
            insert into pack.figure
              (pack_run_id, section, metric, firm_id, grain_key, grain_label,
               numeric_value, text_value, unit, sort_order, drill_query)
            values %s
            """,
            [
                (
                    pack_run.pack_run_id, f["section"], f["metric"], f["firm_id"],
                    f["grain_key"], f["grain_label"], f["numeric_value"], f["text_value"],
                    f["unit"], f["sort_order"], f["drill_query"],
                )
                for f in figures
            ],
        )
        db.execute_values(
            conn,
            "insert into pack.section (pack_run_id, section, title, sort_order) values %s "
            "on conflict (pack_run_id, section) do update set title = excluded.title",
            [
                (pack_run.pack_run_id, s.section, s.title, s.order)
                for s in self.sections
            ],
        )

    def _write_manifest(
        self, conn: PGConnection, pack_run: PackRun, content_hash: bytes, figure_count: int
    ) -> None:
        db.execute(
            conn,
            """
            insert into pack.manifest
              (pack_run_id, tenant_slug, period_start, period_end, system_time,
               figure_count, content_hash)
            values (%s,%s,%s,%s,%s,%s,%s)
            on conflict (pack_run_id) do update set
              figure_count = excluded.figure_count,
              content_hash = excluded.content_hash,
              built_at = now()
            """,
            (
                pack_run.pack_run_id, self.tenant.slug, pack_run.period_start,
                pack_run.period_end, pack_run.system_time, figure_count, content_hash,
            ),
        )


def compute_content_hash(figures: Sequence[dict[str, Any]]) -> bytes:
    """SHA-256 over the figures, ordered and normalised.

    Ordering is explicit rather than relying on the query's own: two runs that
    return the same rows in a different order are the same pack, and a hash that
    disagreed with that would make the reproducibility guarantee useless.

    Decimals are normalised to a fixed string form so 1.50 and 1.5000 hash the
    same, and the pack run id is excluded so two runs at the same system time
    are comparable.
    """
    normalised = [
        {
            "section": f["section"],
            "metric": f["metric"],
            "firm_id": f["firm_id"],
            "grain_key": f["grain_key"],
            "numeric_value": _normalise_number(f["numeric_value"]),
            "text_value": f["text_value"],
            "unit": f["unit"],
        }
        for f in figures
    ]
    normalised.sort(
        key=lambda f: (
            f["section"], f["metric"], f["firm_id"] or "", f["grain_key"] or ""
        )
    )
    return record_hash(normalised)


def _normalise_number(value: Any) -> str | None:
    if value is None:
        return None
    return format(Decimal(str(value)).normalize(), "f")


def figures_for(conn: PGConnection, pack_run_id: uuid.UUID) -> list[dict[str, Any]]:
    return db.query(
        conn,
        """
        select f.*, s.title as section_title, s.sort_order as section_order
          from pack.figure f
          left join pack.section s
                 on s.pack_run_id = f.pack_run_id and s.section = f.section
         where f.pack_run_id = %s
         order by s.sort_order, f.sort_order, f.metric
        """,
        (pack_run_id,),
    )


def verify_reproducible(
    control: ControlPlane, tenant: Tenant, pack_run_id: uuid.UUID
) -> bytes:
    """Rebuild a pack at its recorded system time and check the hash.

    This is the guarantee, executed. Running it on a schedule is how you find
    out that a mart change altered a number in an already-issued pack -- before
    the client does.
    """
    original = control.get_pack_run(pack_run_id)
    if original is None:
        raise PackIntegrityError(f"pack run {pack_run_id} does not exist")
    if original.content_hash is None:
        raise PackIntegrityError(f"pack run {pack_run_id} was never issued")
    rebuilt = PackBuilder(control, tenant).build(
        period_start=original.period_start,
        period_end=original.period_end,
        system_time=original.system_time,
    )
    if rebuilt.content_hash != original.content_hash:
        raise PackIntegrityError(
            f"pack {pack_run_id} did not reproduce at system_time "
            f"{original.system_time.isoformat()}: "
            f"{original.content_hash.hex()[:16]} != {rebuilt.content_hash.hex()[:16]}"
        )
    return rebuilt.content_hash


def restatement_delta(
    conn: PGConnection, earlier_pack_run_id: uuid.UUID, later_pack_run_id: uuid.UUID
) -> list[dict[str, Any]]:
    """What changed between two packs. The report you can sell (spec 6.3)."""
    return db.query(
        conn,
        """
        select coalesce(a.section, b.section)   as section,
               coalesce(a.metric, b.metric)     as metric,
               coalesce(a.firm_id, b.firm_id)   as firm_id,
               coalesce(a.grain_key, b.grain_key) as grain_key,
               coalesce(a.grain_label, b.grain_label) as grain_label,
               a.numeric_value                  as earlier_value,
               b.numeric_value                  as later_value,
               b.numeric_value - a.numeric_value as delta,
               case when a.numeric_value is null or a.numeric_value = 0 then null
                    else round((b.numeric_value - a.numeric_value) / abs(a.numeric_value), 6)
               end                              as delta_pct
          from (select * from pack.figure where pack_run_id = %s) a
          full outer join (select * from pack.figure where pack_run_id = %s) b
            on b.section = a.section and b.metric = a.metric
           and coalesce(b.firm_id,'') = coalesce(a.firm_id,'')
           and coalesce(b.grain_key,'') = coalesce(a.grain_key,'')
         where a.numeric_value is distinct from b.numeric_value
         order by 1, 2, 3
        """,
        (earlier_pack_run_id, later_pack_run_id),
    )
