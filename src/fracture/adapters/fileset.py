"""Reading a source's exported files.

About 40% of sources arrive as file drops rather than APIs (spec 1.2), and the
API sources are exercised in development against recorded responses. Both cases
are a directory of files, so both go through here. Nothing in this module
writes: it opens files read-only and returns parsed records.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterator

from fracture.core.errors import AdapterError


class FileSet:
    """A read-only view over a directory of source exports."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        if not self.root.exists():
            raise AdapterError(f"source export directory does not exist: {self.root}")

    def path(self, name: str) -> Path:
        candidate = (self.root / name).resolve()
        root = self.root.resolve()
        if not str(candidate).startswith(str(root)):
            raise AdapterError(f"{name!r} escapes the export directory")
        return candidate

    def exists(self, name: str) -> bool:
        return self.path(name).exists()

    def read_json(self, name: str) -> Any:
        p = self.path(name)
        if not p.exists():
            raise AdapterError(f"expected export file is missing: {p}")
        with p.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def read_json_records(self, name: str, key: str | None = None) -> list[dict[str, Any]]:
        data = self.read_json(name)
        if isinstance(data, dict):
            if key is None:
                raise AdapterError(f"{name} is an object; a record key must be given")
            data = data.get(key, [])
        if not isinstance(data, list):
            raise AdapterError(f"{name} did not contain a list of records")
        return data

    def read_csv(self, name: str, delimiter: str = ",") -> list[dict[str, Any]]:
        p = self.path(name)
        if not p.exists():
            raise AdapterError(f"expected export file is missing: {p}")
        with p.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh, delimiter=delimiter)
            if reader.fieldnames is None:
                return []
            rows = []
            for lineno, row in enumerate(reader, start=2):
                # A short row means the file is malformed. Padding it with None
                # is how a shifted column becomes a wrong number.
                if None in row or any(k is None for k in row):
                    raise AdapterError(
                        f"{name} line {lineno}: row has more fields than the header"
                    )
                rows.append({k: v for k, v in row.items()})
            return rows

    def iter_csv(self, name: str, delimiter: str = ",") -> Iterator[dict[str, Any]]:
        yield from self.read_csv(name, delimiter)

    def csv_fieldnames(self, name: str, delimiter: str = ",") -> list[str]:
        p = self.path(name)
        if not p.exists():
            return []
        with p.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh, delimiter=delimiter)
            return next(reader, [])

    def glob(self, pattern: str) -> list[Path]:
        return sorted(self.root.glob(pattern))


def creds_fileset(creds: Any, key: str = "export_dir") -> FileSet:
    root = creds.get(key)
    if not root:
        raise AdapterError(f"credentials do not carry {key!r}")
    return FileSet(root)
