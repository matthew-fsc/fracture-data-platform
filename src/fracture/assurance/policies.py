"""The policies the gate enforces.

Each policy is a pure function from a repository snapshot to a list of
violations. Pure because the report has to be reproducible: two runs over the
same tree must produce the same hash, or the artifact is worthless as evidence.

What belongs here is narrow. A policy earns its place only when it is
mechanical (no judgement call), cheap (runs on every commit without anyone
noticing) and load-bearing (its violation has already caused a wrong number, or
would end an engagement). Style preferences do not qualify; a linter does that
job and nobody needs a signed report about import order.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterator, Sequence


class Severity(str, Enum):
    #: Blocks the gate. Nothing ships.
    BLOCKING = "blocking"
    #: Recorded on the report and visible, but does not block. Used where the
    #: rule is right but the remediation is a scheduled piece of work.
    ADVISORY = "advisory"


@dataclass(frozen=True)
class Violation:
    policy: str
    path: str
    detail: str
    lineno: int | None = None

    def location(self) -> str:
        return f"{self.path}:{self.lineno}" if self.lineno else self.path

    def __str__(self) -> str:
        return f"{self.location()}: {self.detail}"


@dataclass(frozen=True)
class Snapshot:
    """The tree under inspection, read once.

    Reading once matters for reproducibility and for speed: eight policies that
    each walk the tree is eight walks, and a gate nobody waits for is a gate
    that gets switched off.
    """

    root: Path
    python: dict[str, str] = field(default_factory=dict)
    sql: dict[str, str] = field(default_factory=dict)
    trees: dict[str, ast.AST] = field(default_factory=dict)

    @classmethod
    def read(cls, root: Path) -> "Snapshot":
        root = root.resolve()
        python: dict[str, str] = {}
        sql: dict[str, str] = {}
        trees: dict[str, ast.AST] = {}
        for path in sorted(root.rglob("*")):
            if not path.is_file() or _ignored(path, root):
                continue
            rel = str(path.relative_to(root))
            if path.suffix == ".py":
                text = path.read_text(encoding="utf-8", errors="replace")
                python[rel] = text
                try:
                    trees[rel] = ast.parse(text, filename=rel)
                except SyntaxError:
                    # A file that will not parse is itself a violation, raised
                    # by `syntax_parses` rather than crashing the whole run.
                    pass
            elif path.suffix == ".sql":
                sql[rel] = path.read_text(encoding="utf-8", errors="replace")
        return cls(root=root, python=python, sql=sql, trees=trees)

    def modules_under(self, prefix: str) -> Iterator[tuple[str, str]]:
        for rel, text in self.python.items():
            if rel.startswith(prefix):
                yield rel, text


_IGNORED_DIRS = frozenset(
    {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
     "node_modules", ".venv", "venv", "out", "build", "dist", ".eggs"}
)


def _ignored(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    return any(part in _IGNORED_DIRS or part.endswith(".egg-info") for part in rel.parts)


@dataclass(frozen=True)
class Policy:
    policy_id: str
    title: str
    #: Why this is enforced mechanically rather than reviewed. Printed on the
    #: report, because a policy whose rationale nobody can state gets waived.
    rationale: str
    severity: Severity
    check: Callable[[Snapshot], Sequence[Violation]]


# -- P1: adapters cannot write to a source system ----------------------------


def _check_adapter_no_writes(snap: Snapshot) -> list[Violation]:
    from fracture.adapters.staticcheck import scan_source

    out: list[Violation] = []
    for rel, text in snap.modules_under("src/fracture/adapters/sources/"):
        for finding in scan_source(text):
            out.append(
                Violation("P1", rel, f"{finding.kind}: {finding.detail}", finding.lineno)
            )
    return out


# -- P2/P3: the seam between chassis and pack --------------------------------

_PACK_IMPORT = re.compile(r"fracture\.packs\.(?!__)([a-z_][a-z0-9_]*)")
#: The registry resolves packs by name through importlib and is allowed to.
_SEAM_EXEMPT = frozenset({"src/fracture/packs/__init__.py"})


def _imported_packs(tree: ast.AST) -> set[tuple[str, int]]:
    found: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            match = _PACK_IMPORT.match(node.module)
            if match:
                found.add((match.group(1), node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                match = _PACK_IMPORT.match(alias.name)
                if match:
                    found.add((match.group(1), node.lineno))
    return found


def _check_chassis_does_not_import_a_pack(snap: Snapshot) -> list[Violation]:
    out: list[Violation] = []
    for rel, tree in sorted(snap.trees.items()):
        if not rel.startswith("src/fracture/"):
            continue
        if rel.startswith("src/fracture/packs/") or rel in _SEAM_EXEMPT:
            continue
        for pack_name, lineno in sorted(_imported_packs(tree)):
            out.append(
                Violation(
                    "P2", rel,
                    f"chassis module imports pack {pack_name!r}; resolve it through "
                    "fracture.packs.get_pack() instead",
                    lineno,
                )
            )
    return out


def _check_packs_do_not_import_each_other(snap: Snapshot) -> list[Violation]:
    out: list[Violation] = []
    for rel, tree in sorted(snap.trees.items()):
        parts = Path(rel).parts
        if len(parts) < 4 or parts[:3] != ("src", "fracture", "packs"):
            continue
        own = parts[3]
        if own == "__init__.py":
            continue
        for pack_name, lineno in sorted(_imported_packs(tree)):
            if pack_name != own:
                out.append(
                    Violation(
                        "P3", rel,
                        f"pack {own!r} imports pack {pack_name!r}; two packs sharing a "
                        "table is one badly named pack and a migration you cannot "
                        "sequence",
                        lineno,
                    )
                )
    return out


# -- P4: mart SQL must bind the pack's system time ---------------------------

#: `now()` needs no trailing \b: a word boundary after ")" sits between two
#: non-word characters and never matches, which silently disabled the most
#: important half of this check. Found by a test that planted now() and saw
#: the policy pass.
_WALL_CLOCK = re.compile(
    r"(?i)(\bnow\s*\(\s*\)|\b(?:current_timestamp|current_date|localtimestamp|"
    r"statement_timestamp|clock_timestamp|transaction_timestamp)\b)"
)
_SYSTEM_TIME = re.compile(r"%\(system_time\)s")


def _check_marts_bind_system_time(snap: Snapshot) -> list[Violation]:
    """Two separate rules, because they fail differently.

    No mart may read the wall clock: that breaks reissue outright. And a mart
    that reads `canon` must constrain system time, which is the same rule the
    runner's `assert_temporal_filter` enforces at execution. A mart that reads
    only other marts needs neither: the predicate was applied upstream, and
    demanding it here would be cargo cult.
    """
    out: list[Violation] = []
    for rel, text in sorted(snap.sql.items()):
        normalised = rel.replace("\\", "/")
        if "/packs/" not in normalised or "/marts/" not in normalised:
            continue
        lowered = text.lower()
        for i, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith("--"):
                continue
            if _WALL_CLOCK.search(line):
                out.append(
                    Violation(
                        "P4", rel,
                        "wall-clock time in a mart model; a pack pinned to a system "
                        "time must not read the clock or it cannot be reissued",
                        i,
                    )
                )
        reads_canon = "canon." in lowered and "canon.v_" not in lowered
        constrained = _SYSTEM_TIME.search(text) or "superseded_at" in lowered
        if reads_canon and not constrained:
            out.append(
                Violation(
                    "P4", rel,
                    "mart model reads canon without binding %(system_time)s or "
                    "filtering superseded_at; it will double-count restated rows",
                )
            )
    return out


# -- P5: no credential or client identifier in the tree ----------------------

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("bearer token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{24,}")),
    ("Postgres URI with password", re.compile(r"(?i)postgres(?:ql)?://[^\s:@/]+:[^\s@/]{6,}@")),
    # Shaped like a US SSN *and* in a range the SSA actually issues. Area 000,
    # 666 and 900-999 have never been issued, so a value there cannot belong to
    # a real person and is the correct choice for a fixture. Flagging only the
    # plausible ones means this policy stays on in tests, which is where a
    # pasted-in client extract would actually land.
    ("issuable US tax identifier",
     re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")),
    ("hardcoded password assignment",
     re.compile(r"(?i)\b(?:password|passwd|secret|api_key|apikey|token)\s*[:=]\s*['\"][^'\"]{10,}['\"]")),
)

#: Values that match a pattern but are the documented local default, a fixture
#: or a test double. Each is an exemption with a reason, not a silenced rule.
_SECRET_ALLOW = (
    "fracture",            # the documented local Postgres password
    "changeme",
    "REDACTED",
    "xxxx",
    "<password>",
    "placeholder",
)


def _check_no_secrets(snap: Snapshot) -> list[Violation]:
    out: list[Violation] = []
    for rel, text in sorted({**snap.python, **snap.sql}.items()):
        for i, line in enumerate(text.splitlines(), start=1):
            if any(token in line for token in _SECRET_ALLOW):
                continue
            for label, pattern in _SECRET_PATTERNS:
                if pattern.search(line):
                    out.append(
                        Violation("P5", rel, f"possible {label} in source", i)
                    )
                    break
    return out


# -- P6: every adapter ships its admission evidence --------------------------

#: An adapter is not shippable without these. Declared here rather than left to
#: the test suite so the gate can report a missing one by name.
REQUIRED_ADAPTER_ARTEFACTS = (
    "discovery snapshot",
    "empty fixture",
    "typical fixture",
    "pathological fixture",
    "golden canonical output",
    "redaction test",
)


def _check_adapters_have_fixtures(snap: Snapshot) -> list[Violation]:
    """Checked against the filesystem rather than the registry, so an adapter
    that is registered but undocumented and one that is documented but
    unregistered are both visible."""
    out: list[Violation] = []
    fixtures = snap.root / "tests" / "fixtures" / "adapters"
    for rel in sorted(snap.python):
        parts = Path(rel).parts
        if parts[:4] != ("src", "fracture", "adapters", "sources"):
            continue
        name = Path(rel).stem
        if name.startswith("_"):
            continue
        directory = fixtures / name
        if not directory.is_dir():
            out.append(
                Violation(
                    "P6", rel,
                    f"adapter {name!r} has no fixture directory at "
                    f"tests/fixtures/adapters/{name}; it cannot ship",
                )
            )
            continue

        entries = {p.name for p in directory.iterdir()}
        if "discovery.json" not in entries:
            out.append(
                Violation(
                    "P6", rel,
                    f"adapter {name!r} has no checked-in discovery snapshot; without "
                    "one, nobody can tell a source schema change from an adapter bug",
                )
            )
        for shape in ("empty", "typical", "pathological"):
            if shape not in entries:
                out.append(
                    Violation(
                        "P6", rel, f"adapter {name!r} has no {shape} extraction fixture"
                    )
                )
        # Golden canonical output for the shapes that produce any. `empty` is
        # exempt by definition: its golden output is nothing.
        for shape in ("typical", "pathological"):
            if shape in entries and f"golden_{shape}.json" not in entries:
                out.append(
                    Violation(
                        "P6", rel,
                        f"adapter {name!r} has a {shape} fixture with no "
                        f"golden_{shape}.json; an extraction nobody pinned the output "
                        "of is an extraction that can change silently",
                    )
                )
    return out


# -- P7: every file parses ---------------------------------------------------


def _check_syntax_parses(snap: Snapshot) -> list[Violation]:
    return [
        Violation("P7", rel, "file does not parse as Python")
        for rel in sorted(snap.python)
        if rel not in snap.trees
    ]


# -- P8: the AI boundary is still enforced in all three places ---------------

_BOUNDARY_SITES = (
    ("src/fracture/canon/ddl/040_ai_boundary.sql", "ai.proposal"),
    ("src/fracture/canon/ddl/040_ai_boundary.sql", "create trigger"),
    ("src/fracture/canon/writer.py", "_check_ai_boundary"),
)


def _check_ai_boundary_intact(snap: Snapshot) -> list[Violation]:
    """The data-side boundary is held three ways that must agree. An agent
    editing the writer could remove one of them and leave a suite that still
    passes, because each check alone is sufficient for the happy path. So the
    gate asserts all three are present."""
    out: list[Violation] = []
    corpus = {**snap.python, **snap.sql}
    for path, needle in _BOUNDARY_SITES:
        text = corpus.get(path)
        if text is None:
            out.append(Violation("P8", path, "AI boundary enforcement site is missing"))
        elif needle.lower() not in text.lower():
            out.append(
                Violation(
                    "P8", path,
                    f"AI boundary site no longer contains {needle!r}; the boundary is "
                    "held in three places that must agree",
                )
            )
    return out



# -- P9: published orderings must be total -----------------------------------

_LIMIT_CLAUSE = re.compile(r"(?is)order\s+by\s+(?P<terms>.+?)\s+limit\s+\d+")
_DISTINCT_ON = re.compile(r"(?is)distinct\s+on\s*\((?P<keys>[^)]*)\)")
_ID_TERM = re.compile(r"(?i)\b[a-z_]*(?:_id|_key|_name|canon_id)\b")
#: Columns that are actually unique within a `distinct on` group. A primary
#: key qualifies; a timestamp does not.
_UNIQUE_TIEBREAK = re.compile(r"(?i)\b(?:canon_id|ctid)\b")


def _check_orderings_are_total(snap: Snapshot) -> list[Violation]:
    """An `order by` under a `limit` must break every tie.

    This is the defect that cost the most to find. A pack section ordered
    `by abs(variance_amount) desc limit 12` publishes whichever of two tied
    households Postgres happens to return, so the pack stops reissuing
    byte-identically on the estates where a tie straddles the cut-off. It
    reproduced on roughly one synthetic estate in four, which is exactly the
    rate at which a human stops believing the test and starts re-running it.

    The check is a heuristic: it requires an identifier-ish column somewhere in
    the ordering terms. That accepts some non-total orderings and rejects some
    total ones, and it is still worth having, because the failure it prevents
    is silent and the remedy is one clause.
    """
    out: list[Violation] = []
    corpus = {
        rel: text
        for rel, text in {**snap.python, **snap.sql}.items()
        if rel.startswith("src/fracture/pack/") or "/marts/" in rel.replace("\\", "/")
    }
    for rel, text in sorted(corpus.items()):
        for match in _LIMIT_CLAUSE.finditer(text):
            terms = match.group("terms")
            if not _ID_TERM.search(terms):
                line = text[: match.start()].count("\n") + 1
                out.append(
                    Violation(
                        "P9", rel,
                        "order by ... limit with no identifier column to break ties; "
                        "a tie across the limit changes which rows are published",
                        line,
                    )
                )
        for match in _DISTINCT_ON.finditer(text):
            tail = text[match.end() : match.end() + 800]
            after = re.search(r"(?is)\border\s+by\s+(?P<terms>[^;)]+)", tail)
            if after is None:
                line = text[: match.start()].count("\n") + 1
                out.append(
                    Violation(
                        "P9", rel,
                        "distinct on with no order by; which row survives is "
                        "whatever the plan happened to produce",
                        line,
                    )
                )
                continue
            terms = [t.strip().lower() for t in after.group("terms").split(",")]
            keys = {k.strip().lower() for k in match.group("keys").split(",")}
            # The final ordering term has to discriminate inside the group. A
            # primary key always does. An id column does, unless it is one of
            # the group keys, where it is constant and therefore useless. A
            # timestamp does not: recorded_at defaults to now(), one value per
            # statement, so every row of a batch insert shares it.
            last = terms[-1] if terms else ""
            bare = last.replace(" desc", "").replace(" asc", "").strip()
            discriminating = bool(_UNIQUE_TIEBREAK.search(last)) or (
                bool(_ID_TERM.search(last)) and bare not in keys
            )
            if not discriminating:
                line = text[: match.start()].count("\n") + 1
                out.append(
                    Violation(
                        "P9", rel,
                        f"distinct on ordered last by {terms[-1]!r}, which is not "
                        "unique within the group; append canon_id so the pick is "
                        "deterministic (recorded_at ties across a batch insert)",
                        line,
                    )
                )
    return out


POLICIES: tuple[Policy, ...] = (
    Policy(
        "P1", "Adapters cannot write to a source system",
        "No writes into source systems ever, including helpful ones. The promise has "
        "to survive a convenience method added at 11pm, so it is checked on the "
        "adapter's own AST rather than reviewed.",
        Severity.BLOCKING, _check_adapter_no_writes,
    ),
    Policy(
        "P2", "The chassis does not import a pack",
        "The chassis is the only asset that compounds across engagements. One static "
        "import from it into a domain pack collapses the two layers back together, "
        "and nothing else would notice until the next engagement.",
        Severity.BLOCKING, _check_chassis_does_not_import_a_pack,
    ),
    Policy(
        "P3", "Packs do not import each other",
        "Two packs sharing a table is one badly named pack plus a migration that "
        "cannot be sequenced per tenant.",
        Severity.BLOCKING, _check_packs_do_not_import_each_other,
    ),
    Policy(
        "P4", "Mart models bind the pack's system time",
        "A pack reissued at the same system time must be byte-identical. A mart that "
        "reads the wall clock, or reads canon without the system-time predicate, "
        "breaks that silently: the number renders, it is just not reproducible.",
        Severity.BLOCKING, _check_marts_bind_system_time,
    ),
    Policy(
        "P5", "No credential or tax identifier in the tree",
        "Source credentials are read-only and held in a secret store; a key in git is "
        "a notifiable event, not a cleanup task.",
        Severity.BLOCKING, _check_no_secrets,
    ),
    Policy(
        "P6", "Every adapter ships its admission evidence",
        "An adapter without empty, typical and pathological fixtures has been tested "
        "against the one extract the engineer happened to have.",
        Severity.BLOCKING, _check_adapters_have_fixtures,
    ),
    Policy(
        "P7", "Every Python file parses",
        "A file that does not parse is a file no other policy inspected. Reported "
        "explicitly so a syntax error cannot buy silence from the whole gate.",
        Severity.BLOCKING, _check_syntax_parses,
    ),
    Policy(
        "P8", "The data-side AI boundary is intact in all three places",
        "The boundary is deliberately redundant: a table, a database trigger and the "
        "canonical writer. Any one of them passes the happy path alone, so removing "
        "one is invisible to the test suite but fatal under adversarial conditions.",
        Severity.BLOCKING, _check_ai_boundary_intact,
    ),
    Policy(
        "P9", "Published orderings are total",
        "A pack reissued at the same system time must be byte-identical. An order by "
        "under a limit that leaves ties publishes whichever row the database happened "
        "to return, which breaks that guarantee on some data and not other data -- "
        "the worst kind of defect to chase.",
        Severity.BLOCKING, _check_orderings_are_total,
    ),
)
