"""Incremental graph update: re-parse only files changed since a git ref."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from cowork_graph import db, parser


@dataclass
class DiffResult:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    renamed: list[tuple[str, str]] = field(default_factory=list)  # (old, new)


def is_merge_commit(cowork_root: Path) -> bool:
    """Return True if HEAD has a second parent (i.e. is a merge commit)."""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD^2"],
        cwd=cowork_root,
        capture_output=True,
    )
    return result.returncode == 0


def head_sha(cowork_root: Path) -> str | None:
    """Return the full SHA of HEAD, or None if it can't be resolved."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=cowork_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def commit_exists(sha: str, cowork_root: Path) -> bool:
    """Return True if sha still resolves to a commit object in this repo.

    A stored SHA can become unreachable (history rewrite followed by gc), in
    which case diffing against it fails and the caller must fall back to a
    full rebuild. Note that a merely *orphaned* commit is still fine: git diff
    compares trees, not ancestry, so an unreachable-but-present commit yields
    the correct file delta.
    """
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=cowork_root,
        capture_output=True,
    )
    return result.returncode == 0


def git_diff_files(since_ref: str, cowork_root: Path) -> DiffResult:
    """Return file-level diff between since_ref and HEAD, with rename detection at 80%."""
    result = subprocess.run(
        ["git", "diff", "--name-status", "-M80%", since_ref, "HEAD"],
        cwd=cowork_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return _parse_name_status(result.stdout)


def _parse_name_status(output: str) -> DiffResult:
    """Parse `git diff --name-status` output into a DiffResult."""
    diff = DiffResult()
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R"):
            if len(parts) >= 3:
                diff.renamed.append((parts[1], parts[2]))
        elif status == "A" and len(parts) >= 2:
            diff.added.append(parts[1])
        elif status == "M" and len(parts) >= 2:
            diff.modified.append(parts[1])
        elif status == "D" and len(parts) >= 2:
            diff.deleted.append(parts[1])
    return diff


def _should_skip(rel_path: str) -> bool:
    """Return True for non-markdown files and paths inside _archive/ or .obsidian/."""
    if not rel_path.endswith(".md"):
        return True
    parts = Path(rel_path).parts
    return "_archive" in parts or ".obsidian" in parts


def _recompute_project_entities(conn) -> None:
    """Recompute project→entity MEMBER_OF_ENTITY edges from current doc edges."""
    conn.execute(
        "DELETE FROM edge WHERE edge_type='MEMBER_OF_ENTITY' AND source_type='project'"
    )
    rows = conn.execute(
        "SELECT e1.target_id AS project_slug, e2.target_id AS entity_slug, COUNT(*) AS cnt"
        " FROM edge e1"
        " JOIN edge e2"
        "   ON e1.source_id = e2.source_id"
        "   AND e2.edge_type = 'MEMBER_OF_ENTITY'"
        "   AND e2.source_type = 'doc'"
        " WHERE e1.edge_type = 'MEMBER_OF_PROJECT'"
        "   AND e1.source_type = 'doc'"
        " GROUP BY project_slug, entity_slug"
    ).fetchall()

    winners: dict[str, tuple[str, int]] = {}
    for row in rows:
        proj, entity, cnt = row[0], row[1], row[2]
        if proj not in winners or cnt > winners[proj][1]:
            winners[proj] = (entity, cnt)

    for proj, (entity, _) in winners.items():
        db.upsert_edge(
            conn,
            source_type="project", source_id=proj,
            edge_type="MEMBER_OF_ENTITY",
            target_type="entity", target_id=entity,
        )


def run_incremental(
    conn,
    since_ref: str,
    cowork_root: Path,
    persons: dict[str, str],
) -> dict:
    """Re-parse files changed since since_ref and update the graph.

    Returns a summary dict: added, modified, deleted, renamed, failed.
    persons is updated in-place when person files are re-parsed.
    """
    from cowork_graph.cli import _display_name_from_fm, _write_doc_to_db

    diff = git_diff_files(since_ref, cowork_root)

    n_added = n_modified = n_deleted = n_renamed = n_failed = 0

    # Renames: rewire existing rows, then re-parse under the new path
    for old_rel, new_rel in diff.renamed:
        if _should_skip(old_rel) and _should_skip(new_rel):
            continue
        conn.execute("BEGIN")
        db.rename_doc(conn, old_rel, new_rel)
        conn.execute("COMMIT")
        n_renamed += 1
        if not _should_skip(new_rel):
            diff.modified.append(new_rel)

    # Deletions
    for rel_path in diff.deleted:
        if _should_skip(rel_path):
            continue
        conn.execute("BEGIN")
        db.delete_doc(conn, rel_path)
        conn.execute("COMMIT")
        n_deleted += 1

    # Person files in the diff must be processed first so mentions resolve correctly
    changed = diff.added + diff.modified
    for rel_path in changed:
        if not rel_path.startswith("memory/people/"):
            continue
        abs_path = cowork_root / rel_path
        if not abs_path.exists():
            continue
        text = abs_path.read_text(encoding="utf-8")
        fm, _, _, _ = parser.parse_frontmatter(text)
        slug = abs_path.stem
        display_name = _display_name_from_fm(fm, slug)
        persons[slug] = display_name
        aliases = fm.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        conn.execute("BEGIN")
        db.upsert_person(
            conn, slug=slug, display_name=display_name,
            role=fm.get("role"), source_doc=rel_path, is_ghost=False,
        )
        for alias in aliases:
            db.upsert_person_alias(conn, slug=slug, alias=str(alias))
        conn.execute("COMMIT")

    # Added and modified docs
    for rel_path in changed:
        if _should_skip(rel_path):
            continue
        abs_path = cowork_root / rel_path
        if not abs_path.exists():
            continue

        is_modified = rel_path in diff.modified
        if is_modified:
            conn.execute("BEGIN")
            db.delete_doc(conn, rel_path)
            conn.execute("COMMIT")

        text = abs_path.read_text(encoding="utf-8")
        try:
            last_mod = datetime.fromtimestamp(
                abs_path.stat().st_mtime, tz=timezone.utc
            ).isoformat()
        except OSError:
            last_mod = None

        parsed_at = datetime.now(timezone.utc).isoformat()
        is_decision_log = abs_path.name == "decisions-log.md"
        _, body_text, _, _ = parser.parse_frontmatter(text)
        result = parser.parse_doc(
            path=rel_path,
            text=text,
            last_modified=last_mod,
            parsed_at=parsed_at,
            cowork_root=cowork_root,
            persons=persons,
        )

        conn.execute("BEGIN")
        try:
            _write_doc_to_db(
                conn,
                result,
                body_text,
                rel_path,
                parsed_at,
                is_decision_log,
                text,
                cowork_root,
                votes_acc=None,
            )
            conn.execute("COMMIT")
            if is_modified:
                n_modified += 1
            else:
                n_added += 1
        except Exception as exc:
            conn.execute("ROLLBACK")
            n_failed += 1
            print(f"Warning: failed to write {rel_path}: {exc}", file=sys.stderr)

    # Recompute project→entity edges from the updated doc edges
    conn.execute("BEGIN")
    _recompute_project_entities(conn)
    conn.execute("COMMIT")

    # Flip ghost projects whose hub doc now exists
    conn.execute("BEGIN")
    db.resolve_ghost_projects(conn)
    conn.execute("COMMIT")

    return {
        "added": n_added,
        "modified": n_modified,
        "deleted": n_deleted,
        "renamed": n_renamed,
        "failed": n_failed,
    }
