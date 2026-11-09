"""Entry point: python -m fracture_reporting."""

from __future__ import annotations

import json
import sys

from fracture_reporting import list_sections


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        sys.stderr.write("usage: python -m fracture_reporting\n")
        return 2
    json.dump(list_sections(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
