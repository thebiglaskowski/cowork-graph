"""Filesystem traversal over the cowork corpus."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

_SKIP_DIRS = frozenset({".git", ".obsidian", "_archive", "node_modules", ".venv", "__pycache__"})
_SKIP_NAMES = frozenset({".projects"})


def walk(root: Path) -> Iterator[tuple[Path, str]]:
    """Yield (absolute_path, relative_path) for every .md file under root.

    Skip dirs in _SKIP_DIRS (checked against every component of the path),
    skip the .projects symlink at its junction (don't follow), and ignore
    non-.md files.
    """
    root = root.resolve()
    yield from _walk(root, root)


def _walk(root: Path, current: Path) -> Iterator[tuple[Path, str]]:
    try:
        entries = sorted(current.iterdir())
    except PermissionError:
        return

    for entry in entries:
        # Never follow the .projects symlink
        if entry.name in _SKIP_NAMES:
            continue

        if entry.is_symlink():
            # Resolve only to check if target is within root (non-.projects symlinks
            # that happen to be directories are skipped, not followed).
            if entry.is_dir():
                continue
            # Symlinked files: check extension, yield if .md
            if entry.suffix == ".md" and _path_is_clean(entry.relative_to(root)):
                rel = entry.relative_to(root).as_posix()
                yield entry, rel
            continue

        if entry.is_dir():
            if entry.name in _SKIP_DIRS or _any_component_skipped(entry, root):
                continue
            yield from _walk(root, entry)
        elif entry.is_file() and entry.suffix == ".md":
            yield entry, entry.relative_to(root).as_posix()


def _path_is_clean(rel: Path) -> bool:
    return not any(part in _SKIP_DIRS for part in rel.parts)


def _any_component_skipped(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    return any(part in _SKIP_DIRS for part in rel.parts)
