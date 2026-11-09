"""Entry point: python -m fracture_migration list|show <source_id>|checks."""

from __future__ import annotations

import json
import sys

from fracture_migration import list_cards, load_card, reconciliation


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else ""
    try:
        if command == "list" and len(args) == 1:
            payload = list_cards()
        elif command == "show" and len(args) == 2:
            payload = load_card(args[1])
        elif command == "checks" and len(args) == 1:
            payload = reconciliation()
        else:
            sys.stderr.write(
                "usage: python -m fracture_migration list|show <source_id>|checks\n"
            )
            return 2
    except KeyError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
