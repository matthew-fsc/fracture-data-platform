"""Entry point: python -m fracture_snowflake."""

from __future__ import annotations

import json
import sys

from fracture_snowflake import warehouse_decision


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        sys.stderr.write("usage: python -m fracture_snowflake\n")
        return 2
    json.dump(warehouse_decision(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
