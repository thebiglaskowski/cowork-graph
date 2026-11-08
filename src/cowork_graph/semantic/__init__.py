"""Phase 8 semantic layer (plan.md "Phase 8 — Semantic layer").

Hybrid vector + keyword search over the cowork corpus, including PDFs and
OCR'd images, plus saved memories. Lives in its own SQLite file
(semantic.db) so the frozen graph schema and the 13s graph rebuild are
untouched. Requires the optional `semantic` dependency group; nothing in the
graph CLI imports this package unless a semantic command runs.
"""

from __future__ import annotations

MISSING_DEPS_HINT = "run: uv sync --group mcp --group semantic"


def available() -> bool:
    """Whether the semantic dependency group is installed."""
    try:
        import fastembed  # noqa: F401
        import pymupdf  # noqa: F401
        import sqlite_vec  # noqa: F401
    except ImportError:
        return False
    return True
