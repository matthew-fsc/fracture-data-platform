"""Entry point: python -m fracture_profiling <extract>."""

from __future__ import annotations

import json
import sys

from fracture_profiling import profile_tree


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        sys.stderr.write("usage: python -m fracture_profiling <extract>\n")
        return 2
    json.dump(profile_tree(args[0]), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
