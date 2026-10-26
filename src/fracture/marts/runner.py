"""Executing the mart models.

Models are plain parameterised SQL, run in filename order against one tenant
with the pack's system time bound. The runner does three things a bare `psql -f`
would not:

1. Refuses to run SQL that reads `canon` without a system-time filter. A mart
   that quietly reads superseded rows produces a number that cannot be
   reproduced, and reproducibility is what the pack is sold on.
2. Runs assertions after each model. An empty mart, a negative AUM or a
   duplicate grain key fails the run rather than rendering a plausible-looking
   zero into a board pack.
3. Records timing and row counts, so a model that silently starts returning
   half its rows is visible.
"""

from __future__ import annotations

import datetime as dt
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from psycopg2.extensions import connection as PGConnection

from fracture.canon.bitemporal import assert_temporal_filter
from fracture.core import db
from fracture.core.errors import FractureError
from fracture.core.logging import get_logger
from fracture.packs import Pack, get_pack

log = get_logger("marts.runner")

_DEPENDS = re.compile(r"--\s*depends_on:\s*(.+)")


class MartAssertionError(FractureError):
    """A mart produced output that cannot be right."""


@dataclass(frozen=True)
class Model:
    name: str
    sql: str
    depends_on: tuple[str, ...]
    path: Path

    @property
    def outputs(self) -> tuple[str, ...]:
        return tuple(
            sorted(set(re.findall(r"create table (mart\.\w+)", self.sql, re.IGNORECASE)))
        )


@dataclass
class ModelResult:
    name: str
    duration_s: float
    row_counts: dict[str, int] = field(default_factory=dict)

    def __str__(self) -> str:
        counts = ", ".join(f"{t}={n}" for t, n in sorted(self.row_counts.items()))
        return f"{self.name} ({self.duration_s:.2f}s) {counts}"


@dataclass
class MartRunResult:
    system_time: dt.datetime
    models: list[ModelResult] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(sum(m.row_counts.values()) for m in self.models)

    def summary(self) -> str:
        return f"{len(self.models)} models, {self.total_rows} mart rows"


def load_models(directory: Path | None = None) -> list[Model]:
    directory = directory or get_pack().model_dir
    models: list[Model] = []
    for path in sorted(directory.glob("*.sql")):
        sql = path.read_text()
        match = _DEPENDS.search(sql)
        depends = tuple(d.strip() for d in match.group(1).split(",")) if match else ()
        models.append(Model(path.stem, sql, depends, path))
    if not models:  # pragma: no cover - packaging error
        raise FileNotFoundError(f"no mart models under {directory}")
    return models


class MartRunner:
    """Runs one pack's models against one tenant.

    The pack is explicit because the assertions travel with it. Running the
    wealth assertions over a professional-services tenant would fail on tables
    that correctly do not exist, and the failure would read like a bug in the
    chassis rather than a mis-specified run.
    """

    def __init__(
        self,
        models: Sequence[Model] | None = None,
        pack: Pack | str | None = None,
    ) -> None:
        self.pack = get_pack(pack) if not isinstance(pack, Pack) else pack
        self.models = (
            list(models) if models is not None else load_models(self.pack.model_dir)
        )

    def run(
        self,
        conn: PGConnection,
        system_time: dt.datetime,
        assert_after: bool = True,
    ) -> MartRunResult:
        result = MartRunResult(system_time=system_time)
        params = {"system_time": system_time}
        for model in self.models:
            assert_temporal_filter(model.sql)
            started = time.perf_counter()
            with conn.cursor() as cur:
                cur.execute(model.sql, params)
            duration = time.perf_counter() - started
            counts = {
                table: int(db.scalar(conn, f"select count(*) from {table}") or 0)
                for table in model.outputs
            }
            model_result = ModelResult(model.name, duration, counts)
            result.models.append(model_result)
            log.info("mart %s", model_result)
        if assert_after:
            self.assert_sane(conn)
        log.info("mart run complete: %s", result.summary())
        return result

    def assert_sane(self, conn: PGConnection) -> None:
        failures: list[str] = []
        for table in self.pack.non_empty:
            count = int(db.scalar(conn, f"select count(*) from {table}") or 0)
            if count == 0:
                failures.append(f"{table} is empty")
        for table, description, sql in self.pack.assertions:
            rows = db.query(conn, sql)
            if rows:
                failures.append(f"{table}: {description} -- {len(rows)} offending row(s): {rows[:3]}")
        if failures:
            raise MartAssertionError(
                "mart assertions failed:\n  " + "\n  ".join(failures)
            )


def build_marts(
    conn: PGConnection,
    system_time: dt.datetime,
    assert_after: bool = True,
    pack: Pack | str | None = None,
) -> MartRunResult:
    return MartRunner(pack=pack).run(conn, system_time, assert_after=assert_after)
