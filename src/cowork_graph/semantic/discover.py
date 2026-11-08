"""Which corpus files the semantic layer indexes (plan.md Phase 8, decision 4)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Same skips as the graph walker, plus backup/scratch trees that duplicate
# real docs and would crowd search results with stale copies.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".obsidian",
        "_archive",
        "node_modules",
        ".venv",
        "__pycache__",
        "_backups",
        "_to_delete",
        ".claude",
        ".projects",
    }
)

KIND_BY_EXT = {
    ".md": "md",
    ".txt": "text",
    ".csv": "text",
    ".pdf": "pdf",
    ".docx": "office",
    ".xlsx": "office",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".webp": "image",
    ".tif": "image",
    ".tiff": "image",
    ".bmp": "image",
}

# Anything bigger is almost certainly an export or dump, not writing.
MAX_FILE_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True)
class FoundFile:
    rel: str
    abs_path: Path
    kind: str
    size: int
    mtime_ns: int


def kind_for(name: str) -> str | None:
    return KIND_BY_EXT.get(os.path.splitext(name)[1].lower())


def discover(root: Path) -> dict[str, FoundFile]:
    """Stat-scan the corpus. ~3k files takes well under a second."""
    root = root.resolve()
    found: dict[str, FoundFile] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            kind = kind_for(name)
            if kind is None or name.startswith("."):
                continue
            p = Path(dirpath) / name
            if p.is_symlink():
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size == 0 or st.st_size > MAX_FILE_BYTES:
                continue
            rel = p.relative_to(root).as_posix()
            found[rel] = FoundFile(rel, p, kind, st.st_size, st.st_mtime_ns)
    return found
