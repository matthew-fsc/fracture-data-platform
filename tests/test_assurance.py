"""The code-side gate, and proof that it can fail.

A gate that cannot fail is theatre, so each policy gets a case that violates
it. The negative cases matter more than the positive ones: a clean run proves
the tree is clean today, and a caught violation proves the gate would notice
when it stops being.
"""

from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path

import pytest

from fracture.assurance import POLICIES, Severity, run_policies
from fracture.assurance.policies import Snapshot
from tests.conftest import requires_db

REPO = Path(__file__).resolve().parents[1]


def _snap(**files: str) -> Snapshot:
    """A synthetic tree. Python files are parsed so AST policies see them."""
    python = {k: v for k, v in files.items() if k.endswith(".py")}
    sql = {k: v for k, v in files.items() if k.endswith(".sql")}
    trees = {}
    for name, text in python.items():
        try:
            trees[name] = ast.parse(text, filename=name)
        except SyntaxError:
            pass
    return Snapshot(root=REPO, python=python, sql=sql, trees=trees)


def _check(policy_id: str, snap: Snapshot):
    policy = next(p for p in POLICIES if p.policy_id == policy_id)
    return list(policy.check(snap))


# -- the tree as it stands ---------------------------------------------------


def test_the_repository_passes_its_own_gate():
    report = run_policies(REPO)
    assert report.passed, report.summary() + "\n" + "\n".join(
        f"  {v}" for v in report.violations
    )


def test_every_policy_states_why_it_is_mechanical():
    """A policy whose rationale nobody can state gets waived the first time it
    is inconvenient."""
    for policy in POLICIES:
        assert len(policy.rationale) > 60, f"{policy.policy_id} has no real rationale"
        assert policy.severity in (Severity.BLOCKING, Severity.ADVISORY)


@pytest.fixture
def frozen_tree(tmp_path: Path) -> Path:
    """A small tree that nothing else is editing.

    Reading the live repository twice is not a test of the hashing: it is a
    test of whether anyone saved a file in between, which they will. The
    property under test is that one tree hashes the same twice.
    """
    (tmp_path / "src" / "fracture" / "core").mkdir(parents=True)
    (tmp_path / "src" / "fracture" / "core" / "thing.py").write_text(
        "VALUE = 1\n"
    )
    (tmp_path / "src" / "fracture" / "packs" / "demo" / "marts").mkdir(parents=True)
    (tmp_path / "src" / "fracture" / "packs" / "demo" / "marts" / "010_a.sql").write_text(
        "select 1 from mart.other\n"
    )
    return tmp_path


def test_the_report_is_reproducible_over_one_tree(frozen_tree):
    pinned = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    first = run_policies(frozen_tree, run_at=pinned)
    second = run_policies(frozen_tree, run_at=pinned)
    assert first.content_hash == second.content_hash
    assert first.tree_hash == second.tree_hash


def test_a_changed_tree_changes_the_tree_hash(frozen_tree):
    pinned = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    before = run_policies(frozen_tree, run_at=pinned)
    (frozen_tree / "src" / "fracture" / "core" / "thing.py").write_text("VALUE = 2\n")
    after = run_policies(frozen_tree, run_at=pinned)
    assert after.tree_hash != before.tree_hash, (
        "an edited tree hashed the same; the report cannot be tied to a revision"
    )


def test_a_shrunken_policy_set_does_not_hash_like_a_clean_run(frozen_tree):
    """Otherwise the report cannot tell "we fixed it" from "we stopped
    checking", which is the only question an auditor is really asking."""
    pinned = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    full = run_policies(frozen_tree, run_at=pinned)
    partial = run_policies(frozen_tree, policies=POLICIES[:4], run_at=pinned)
    # Not asserted to pass: the frozen tree has no AI boundary files, so P8
    # correctly reports them missing. The property under test is that the
    # policy set is part of the hash, whatever the verdict.
    assert len(partial.results) < len(full.results)
    assert full.content_hash != partial.content_hash


# -- each policy, violated ---------------------------------------------------


def test_p1_catches_a_write_into_a_source_system():
    snap = _snap(**{
        "src/fracture/adapters/sources/bad.py": (
            "import requests\n"
            "def push(session, row):\n"
            "    session.post('https://client/api', json=row)\n"
        )
    })
    found = _check("P1", snap)
    assert found and "http" in found[0].detail


def test_p1_catches_mutating_sql_in_a_string_literal():
    snap = _snap(**{
        "src/fracture/adapters/sources/bad.py": (
            "SQL = 'update accounts set status = %s where account_id = %s'\n"
        )
    })
    assert _check("P1", snap)


def test_p2_catches_the_chassis_importing_a_pack():
    snap = _snap(**{
        "src/fracture/pack/build.py": (
            "from fracture.packs.wealth.dashboard import DEPARTMENTS\n"
        )
    })
    found = _check("P2", snap)
    assert found and "wealth" in found[0].detail


def test_p2_permits_the_registry_resolving_packs_by_name():
    snap = _snap(**{
        "src/fracture/packs/__init__.py": (
            "import importlib\n"
            "importlib.import_module('fracture.packs.wealth')\n"
        )
    })
    assert not _check("P2", snap)


def test_p3_catches_one_pack_importing_another():
    snap = _snap(**{
        "src/fracture/packs/services/dashboard.py": (
            "from fracture.packs.wealth.data import DashboardData\n"
        )
    })
    found = _check("P3", snap)
    assert found and "wealth" in found[0].detail


def test_p4_catches_wall_clock_time_in_a_mart():
    snap = _snap(**{
        "src/fracture/packs/wealth/marts/900_bad.sql": (
            "select now() - opened_at from canon.service_event "
            "where recorded_at <= %(system_time)s\n"
        )
    })
    found = _check("P4", snap)
    assert found and "wall-clock" in found[0].detail


def test_p4_catches_a_mart_reading_canon_unbounded():
    snap = _snap(**{
        "src/fracture/packs/wealth/marts/900_bad.sql": (
            "select firm_id, sum(market_value) from canon.balance_snapshot group by 1\n"
        )
    })
    assert _check("P4", snap)


def test_p4_permits_a_mart_that_reads_only_other_marts():
    """The predicate was applied upstream. Demanding it again is cargo cult,
    and a policy that fires on correct code gets switched off."""
    snap = _snap(**{
        "src/fracture/packs/wealth/marts/900_ok.sql": (
            "select firm_id, sum(total_value) from mart.household_aum group by 1\n"
        )
    })
    assert not _check("P4", snap)


def test_p5_catches_a_credential():
    snap = _snap(**{
        "src/fracture/core/bad.py": 'KEY = "AKIAIOSFODNN7EXAMPLE"\n'
    })
    assert _check("P5", snap)


def test_p5_catches_an_issuable_tax_identifier_even_in_a_test():
    """Where a pasted-in client extract would actually land."""
    # Assembled rather than written out: P5 is correct to flag a literal
    # issuable identifier in any file, and this file is not an exception.
    planted = "1" + "23-45-" + "6789"
    snap = _snap(**{"tests/test_bad.py": f'SSN = "{planted}"\n'})
    assert _check("P5", snap)


def test_p5_permits_an_identifier_that_cannot_be_real():
    """The SSA has never issued area 900-999, so a fixture there cannot collide
    with a real person's number. Flagging it would push fixtures toward
    plausible identifiers, which is the opposite of what we want."""
    reserved = "9" + "00-45-" + "6789"
    snap = _snap(**{"tests/test_ok.py": f'SSN = "{reserved}"\n'})
    assert not _check("P5", snap)


def test_p6_catches_an_adapter_with_no_fixtures():
    snap = _snap(**{"src/fracture/adapters/sources/ghost.py": "# nothing\n"})
    found = _check("P6", snap)
    assert found and "fixture directory" in found[0].detail


def test_p7_catches_a_file_that_does_not_parse():
    snap = _snap(**{"src/fracture/core/broken.py": "def f(:\n"})
    found = _check("P7", snap)
    assert found and "does not parse" in found[0].detail


def test_p8_catches_the_ai_boundary_being_removed():
    snap = _snap(**{
        "src/fracture/canon/writer.py": "class CanonWriter: pass\n",
        "src/fracture/canon/ddl/040_ai_boundary.sql": "-- emptied\n",
    })
    found = _check("P8", snap)
    assert len(found) == 3, [str(f) for f in found]


def test_p9_catches_a_limit_with_ties():
    """The defect this policy was written for. A pack section ordered by one
    non-unique measure under a limit publishes whichever row the database
    happened to return."""
    snap = _snap(**{
        "src/fracture/pack/data.py": (
            "SQL = 'select * from mart.unbilled "
            "order by abs(variance_amount) desc limit 12'\n"
        )
    })
    found = _check("P9", snap)
    assert found and "break ties" in found[0].detail


def test_p9_catches_a_distinct_on_tied_on_recorded_at():
    """recorded_at defaults to now(), which is one value per statement, so
    every row of a batch insert ties on it."""
    snap = _snap(**{
        "src/fracture/packs/wealth/marts/900_bad.sql": (
            "select distinct on (firm_id, account_id) * from canon.account "
            "where recorded_at <= %(system_time)s "
            "order by firm_id, account_id, recorded_at desc\n"
        )
    })
    found = _check("P9", snap)
    assert found and "not unique within the group" in found[0].detail


def test_p9_permits_a_total_ordering():
    snap = _snap(**{
        "src/fracture/packs/wealth/marts/900_ok.sql": (
            "select distinct on (firm_id, account_id) * from canon.account "
            "where recorded_at <= %(system_time)s "
            "order by firm_id, account_id, recorded_at desc, canon_id desc\n"
        )
    })
    assert not _check("P9", snap)


# -- the change gate ---------------------------------------------------------


@requires_db
def test_an_agent_authored_change_cannot_ship_without_a_human(control):
    from fracture.assurance.gate import GateRecordError, record_gate

    report = run_policies(REPO)
    with pytest.raises(GateRecordError) as exc:
        record_gate(control, report, authored_by="an-agent", author_kind="agent")
    assert "agent_change_needs_human_approval" in str(exc.value)


@requires_db
def test_an_agent_cannot_approve_its_own_work(control):
    from fracture.assurance.gate import GateRecordError, record_gate

    report = run_policies(REPO)
    with pytest.raises(GateRecordError) as exc:
        record_gate(
            control, report, authored_by="an-agent", author_kind="agent",
            approved_by="an-agent",
        )
    assert "approver_is_not_the_author" in str(exc.value)


@requires_db
def test_a_failing_gate_can_be_recorded_but_never_approved(control):
    """Recording a failure is expected and useful. Marking it approved is the
    thing that must be impossible, and it is a database constraint rather than
    a branch in the recorder."""
    from dataclasses import replace

    from fracture.assurance.engine import PolicyResult
    from fracture.assurance.gate import GateRecordError, approve_gate, record_gate
    from fracture.assurance.policies import Violation

    clean = run_policies(REPO)
    planted = PolicyResult(POLICIES[0], (Violation("P1", "x.py", "planted", 1),))
    broken = replace(clean, results=(planted,))
    assert not broken.passed

    record = record_gate(
        control, broken, authored_by="a-human", author_kind="human",
    )
    assert record.verdict == "fail"
    assert record.approved_by is None
    assert not record.shippable

    with pytest.raises(GateRecordError) as exc:
        approve_gate(control, record.change_gate_id, approved_by="someone-else")
    assert "failed_gate_is_not_approved" in str(exc.value)


@requires_db
def test_a_human_approved_passing_gate_is_shippable(control):
    from fracture.assurance.gate import list_gates, record_gate

    report = run_policies(REPO)
    record = record_gate(
        control, report, authored_by="an-agent", author_kind="agent",
        approved_by="a-human", summary="pack split",
    )
    assert record.shippable
    assert record.change_gate_id in {r.change_gate_id for r in list_gates(control)}
