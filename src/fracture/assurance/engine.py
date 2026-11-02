"""Running the policies and producing the evidence.

The report is content-hashed over its canonically-ordered findings, the same
construction the board pack uses. That is deliberate: the claim being sold is
"continuous automated audit", and an audit whose output cannot be reproduced
is a log line, not evidence. Two runs over the same tree produce the same hash,
so a report can be attached to a release, a change gate or a security
questionnaire and checked later.
"""

from __future__ import annotations

import datetime as dt
import json
import platform
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from fracture.assurance.policies import POLICIES, Policy, Severity, Snapshot, Violation
from fracture.core.errors import FractureError
from fracture.core.hashing import canonical_json, sha256_bytes
from fracture.core.logging import get_logger

log = get_logger("assurance")


class GateFailed(FractureError):
    """A blocking policy was violated. Nothing ships."""


@dataclass(frozen=True)
class PolicyResult:
    policy: Policy
    violations: tuple[Violation, ...]

    @property
    def passed(self) -> bool:
        return not self.violations

    @property
    def blocking(self) -> bool:
        return bool(self.violations) and self.policy.severity is Severity.BLOCKING

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy.policy_id,
            "title": self.policy.title,
            "severity": self.policy.severity.value,
            "passed": self.passed,
            "violations": [
                {"path": v.path, "lineno": v.lineno, "detail": v.detail}
                for v in self.violations
            ],
        }


@dataclass(frozen=True)
class AssuranceReport:
    """One gate run, hashed.

    `tree_hash` covers the inspected source; `content_hash` covers the findings.
    Both are needed: an unchanged tree with a changed report means the policy
    set moved, which is the interesting case and the one a single hash hides.
    """

    run_at: dt.datetime
    root: str
    revision: str | None
    results: tuple[PolicyResult, ...]
    tree_hash: bytes
    content_hash: bytes
    files_inspected: int

    @property
    def violations(self) -> tuple[Violation, ...]:
        return tuple(v for r in self.results for v in r.violations)

    @property
    def blocking(self) -> tuple[PolicyResult, ...]:
        return tuple(r for r in self.results if r.blocking)

    @property
    def passed(self) -> bool:
        return not self.blocking

    def summary(self) -> str:
        passed = sum(1 for r in self.results if r.passed)
        verdict = "PASS" if self.passed else "FAIL"
        return (
            f"assurance {verdict}: {passed}/{len(self.results)} policies clean, "
            f"{len(self.violations)} violation(s) over {self.files_inspected} files, "
            f"hash={self.content_hash.hex()[:16]}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_at": self.run_at.isoformat(),
            "root": self.root,
            "revision": self.revision,
            "files_inspected": self.files_inspected,
            "tree_hash": self.tree_hash.hex(),
            "content_hash": self.content_hash.hex(),
            "passed": self.passed,
            "policies": [r.as_dict() for r in self.results],
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True)

    def raise_if_blocked(self) -> "AssuranceReport":
        if self.passed:
            return self
        lines = [
            f"  {r.policy.policy_id} {r.policy.title}"
            + "".join(f"\n    {v}" for v in r.violations)
            for r in self.blocking
        ]
        raise GateFailed(
            "assurance gate failed; blocking policies violated:\n" + "\n".join(lines)
        )


#: Alias kept so callers can say what they mean at the call site.
AssuranceRun = AssuranceReport


def _revision(root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - no git
        return None
    return out.stdout.strip() or None


def _tree_hash(snap: Snapshot) -> bytes:
    """Hash of the inspected source, path-ordered.

    Only the files the policies actually read, so an unrelated asset changing
    does not invalidate a report and make the hash useless for comparison.
    """
    parts = [
        f"{path}\n{sha256_bytes(text.encode('utf-8')).hex()}"
        for path, text in sorted({**snap.python, **snap.sql}.items())
    ]
    return sha256_bytes("\n".join(parts).encode("utf-8"))


def _content_hash(results: Sequence[PolicyResult]) -> bytes:
    """Hash over the findings, canonically ordered.

    Policy id, then path, then line, then detail: a stable order that does not
    depend on filesystem iteration or on the order policies happen to run in.
    """
    findings = sorted(
        (
            {
                "policy": r.policy.policy_id,
                "severity": r.policy.severity.value,
                "path": v.path,
                "lineno": v.lineno,
                "detail": v.detail,
            }
            for r in results
            for v in r.violations
        ),
        key=lambda f: (f["policy"], f["path"], f["lineno"] or 0, f["detail"]),
    )
    # The policy set is hashed alongside the findings. A clean run against a
    # shrunken policy set must not hash the same as a clean run against the
    # full one, or the report cannot tell "we fixed it" from "we stopped
    # checking".
    declared = sorted(
        (
            {"policy": r.policy.policy_id, "severity": r.policy.severity.value}
            for r in results
        ),
        key=lambda d: d["policy"],
    )
    payload = {"policies": declared, "findings": findings}
    return sha256_bytes(canonical_json(payload))


def run_policies(
    root: Path | str | None = None,
    policies: Sequence[Policy] | None = None,
    run_at: dt.datetime | None = None,
) -> AssuranceReport:
    """Run the gate over a repository tree.

    `run_at` is a parameter rather than a call to the clock so a report can be
    rebuilt for an earlier revision and compared.
    """
    root = Path(root) if root else Path(__file__).resolve().parents[3]
    snap = Snapshot.read(root)
    selected = tuple(policies if policies is not None else POLICIES)

    results: list[PolicyResult] = []
    for policy in selected:
        found = tuple(policy.check(snap))
        results.append(PolicyResult(policy, found))
        if found:
            log.warning(
                "assurance %s %s: %d violation(s)",
                policy.policy_id, policy.severity.value, len(found),
            )

    report = AssuranceReport(
        run_at=run_at or dt.datetime.now(dt.timezone.utc),
        root=str(root),
        revision=_revision(root),
        results=tuple(results),
        tree_hash=_tree_hash(snap),
        content_hash=_content_hash(results),
        files_inspected=len(snap.python) + len(snap.sql),
    )
    log.info(report.summary())
    return report
