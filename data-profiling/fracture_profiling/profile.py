"""Profile CSV and JSON extracts without retaining cell values.

The report is safe to commit and safe to paste into an assessment. It records
shape: how many rows, how many nulls, how many distinct values, what the
values look like. It does not record what they are.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

_SENSITIVE = re.compile(
    r"(?i)(ssn|tax|ein|passport|password|secret|token|account.?number|routing|dob|birth)"
)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def profile_tree(root: str | Path) -> dict:
    """Profile every CSV and JSON file under `root`, in path order."""
    base = Path(root)
    files = []
    if base.is_file():
        files.append(profile_file(base))
    else:
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix.lower() in {".csv", ".json"}:
                files.append(profile_file(path))
    return {"files": files}


def profile_file(path: str | Path) -> dict:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        tables = [_profile_rows(path.name, _read_csv(path))]
    elif suffix == ".json":
        tables = _json_tables(path)
    else:
        tables = [{"name": path.name, "shape": "unsupported", "rows": 0, "columns": []}]
    return {"path": path.name, "tables": tables}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def _json_tables(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [_profile_rows(path.stem, _as_rows(payload))]
    if isinstance(payload, dict):
        tables = []
        for key, value in payload.items():
            if isinstance(value, list):
                tables.append(_profile_rows(str(key), _as_rows(value)))
        if tables:
            return tables
        if all(not isinstance(value, (list, dict)) for value in payload.values()):
            return [_profile_rows(path.stem, [payload])]
    return [{"name": path.stem, "shape": "unsupported", "rows": 0, "columns": []}]


def _as_rows(records: list) -> list[dict]:
    rows = []
    for record in records:
        if isinstance(record, dict):
            rows.append(record)
    return rows


def _profile_rows(name: str, rows: list[dict]) -> dict:
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                columns.append(str(key))
    return {
        "name": name,
        "shape": "tabular",
        "rows": len(rows),
        "columns": [_column(column, rows) for column in columns],
    }


def _column(name: str, rows: list[dict]) -> dict:
    sensitive = bool(_SENSITIVE.search(name))
    present = 0
    distinct: set[str] = set()
    kinds: set[str] = set()
    for row in rows:
        raw = row.get(name)
        if raw is None or str(raw).strip() == "":
            continue
        present += 1
        text = str(raw).strip()
        if not sensitive:
            distinct.add(text)
            kinds.add(_kind(text))
    nulls = len(rows) - present
    column = {
        "name": name,
        "sensitive": sensitive,
        "nulls": nulls,
        "present": present,
    }
    if sensitive:
        column["type"] = "redacted"
        column["distinct"] = None
    else:
        column["type"] = _collapse(kinds) if present else "empty"
        column["distinct"] = len(distinct)
    return column


def _kind(text: str) -> str:
    lowered = text.lower()
    if lowered in {"true", "false", "yes", "no"}:
        return "boolean"
    try:
        int(text)
        return "integer"
    except ValueError:
        pass
    try:
        float(text)
        return "decimal"
    except ValueError:
        pass
    if _DATE.match(text):
        return "date"
    return "text"


def _collapse(kinds: set[str]) -> str:
    if kinds <= {"integer"}:
        return "integer"
    if kinds <= {"integer", "decimal"}:
        return "decimal"
    if kinds == {"boolean"}:
        return "boolean"
    if kinds == {"date"}:
        return "date"
    return "text"
