"""CLI entry point. Real commands land in Phase 2."""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "cowork-graph CLI — Phase 1 scaffolding only. "
        "Real commands land in Phase 2."
    )
    print(
        "See cowork/claude-environment/cowork-graph/plan.md "
        "for the schema and roadmap."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
