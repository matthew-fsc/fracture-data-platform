"""The no-mutating-verbs check (spec section 4.2).

"No writes into source systems, ever, including helpful ones" (spec 1.3) is a
promise that has to survive a junior engineer adding a convenience method at
11pm. So it is checked mechanically, on the adapter's own source, as part of the
test suite that gates shipping an adapter.

The check is an AST walk plus a lexical pass over string literals, because the
dangerous statement is usually inside a SQL string, not a Python call.
"""

from __future__ import annotations

import ast
import inspect
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from fracture.core.errors import MutatingVerbError

#: SQL verbs that write. `select ... into` is included because it creates a
#: table in the source database, which is a write even when it feels like a read.
MUTATING_SQL = (
    "insert", "update", "delete", "merge", "upsert", "truncate", "drop",
    "alter", "create", "grant", "revoke", "replace into", "select into",
    "call ", "exec ", "execute immediate", "vacuum", "copy into",
)

#: HTTP verbs that write. Adapters read; anything else is a bug or a breach.
MUTATING_HTTP = ("post", "put", "patch", "delete")

#: Filesystem and client-library calls that write to somewhere other than our
#: own artifact store.
MUTATING_CALLS = (
    "write_text", "write_bytes", "unlink", "rmtree", "remove", "rename",
    "put_object", "delete_object", "upload_file", "sftp_put", "storbinary",
)

#: Callers that are allowed to look like writes because their target is our own
#: database or artifact store, not the client's system.
ALLOWED_RECEIVERS = frozenset({"self.store", "store", "conn", "cursor", "cur", "db", "loader"})

_SQL_HINT = re.compile(r"(?i)\b(from|into|table|where|values|set)\b")


@dataclass(frozen=True)
class Finding:
    kind: str
    detail: str
    lineno: int

    def __str__(self) -> str:
        return f"line {self.lineno}: {self.kind} -- {self.detail}"


class _MutationVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.findings: list[Finding] = []

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        if name:
            leaf = name.rsplit(".", 1)[-1].lower()
            receiver = name.rsplit(".", 1)[0] if "." in name else ""
            if leaf in MUTATING_HTTP and receiver.lower() not in ALLOWED_RECEIVERS:
                # requests.post(...), session.put(...), client.delete(...)
                if receiver.lower() in {"requests", "session", "client", "http", "self.session", "self.client"}:
                    self.findings.append(
                        Finding("mutating http verb", f"{name}()", node.lineno)
                    )
            if leaf in MUTATING_CALLS and receiver.lower() not in ALLOWED_RECEIVERS:
                self.findings.append(Finding("mutating call", f"{name}()", node.lineno))
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self._check_sql(node.value, node.lineno)
        self.generic_visit(node)

    def _check_sql(self, text: str, lineno: int) -> None:
        stripped = text.strip().lower()
        if not stripped or not _SQL_HINT.search(stripped):
            return
        for verb in MUTATING_SQL:
            if re.match(rf"^{re.escape(verb)}\b", stripped) or re.search(
                rf"(?:^|;|\)\s*)\s*{re.escape(verb)}\b", stripped
            ):
                self.findings.append(
                    Finding("mutating sql", f"{verb!r} in string literal", lineno)
                )
                return


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def scan_source(source: str) -> list[Finding]:
    visitor = _MutationVisitor()
    visitor.visit(ast.parse(source))
    return visitor.findings


def scan_module(module: Any) -> list[Finding]:
    path = Path(inspect.getfile(module))
    return scan_source(path.read_text())


def assert_no_mutating_verbs(module: Any) -> None:
    """Raise if the adapter module contains anything that could write to a source.

    Called by the shared adapter test suite for every registered adapter, so an
    adapter cannot ship without passing it.
    """
    findings = scan_module(module)
    if findings:
        name = getattr(module, "__name__", str(module))
        detail = "; ".join(str(f) for f in findings)
        raise MutatingVerbError(
            f"{name} contains statements that could write to a source system: {detail}"
        )


def scan_paths(paths: Iterable[Path]) -> dict[str, list[Finding]]:
    out: dict[str, list[Finding]] = {}
    for path in paths:
        findings = scan_source(path.read_text())
        if findings:
            out[str(path)] = findings
    return out
