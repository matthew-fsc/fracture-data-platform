"""Entry point: python -m assessment <engagement.yml> <findings.yml>."""

from __future__ import annotations

from assessment.render_findings import main

if __name__ == "__main__":
    raise SystemExit(main())
