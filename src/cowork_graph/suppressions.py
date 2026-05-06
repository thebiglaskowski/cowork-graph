"""Suppression filtering for the audit module.

Reads a markdown file with YAML frontmatter that lists (rule, key) pairs
representing intentional findings that should not appear in audit output.
Key derivation maps each rule's existing finding dict shape to a stable
string identifier.
"""

from __future__ import annotations

import logging
from pathlib import Path

import frontmatter
import yaml

_log = logging.getLogger(__name__)

_EMPTY_TEMPLATE = """\
---
suppressions: []
---

# Audit Suppressions

Hand-curated allowlist of audit findings that are intentional. Each entry
suppresses one finding by `rule` + `key`. Add a `reason` and the date you added it.

To suppress a finding: identify its rule name and key fields from the audit
report, write an entry above. Re-run the audit to confirm it disappears from
the count.
"""


def _make_key(rule: str, finding: dict) -> str:
    """Derive the suppression key for a finding based on its rule name."""
    if rule == "broken_links":
        return f'{finding["source_doc"]}::{finding["link_target"]}'
    elif rule == "ghost_projects":
        return finding["slug"]
    elif rule == "ghost_people":
        return finding["slug"]
    elif rule == "one_way_edges":
        return f'{finding["doc_a"]}::{finding["doc_b"]}'
    elif rule == "stale_active_docs":
        return finding["path"]
    elif rule == "inconsistent_hub_state":
        return f'{finding["hub"]}::{finding["member"]}'
    elif rule == "orphan_docs":
        return finding["path"]
    elif rule == "decision_drift":
        return finding["id"]
    elif rule == "tag_drift":
        return finding["tag"]
    elif rule == "decisions_format_drift":
        return finding["id"]
    else:
        raise ValueError(f"Unknown rule: {rule}")


def load_suppressions(suppressions_path: Path) -> set[tuple[str, str]]:
    """Load (rule, key) tuples from a markdown file with YAML frontmatter.

    Auto-creates an empty template if the file is missing. Returns an empty
    set on malformed YAML rather than raising.
    """
    if not suppressions_path.exists():
        suppressions_path.parent.mkdir(parents=True, exist_ok=True)
        suppressions_path.write_text(_EMPTY_TEMPLATE, encoding="utf-8")
        return set()

    text = suppressions_path.read_text(encoding="utf-8")
    try:
        post = frontmatter.loads(text)
        data = dict(post.metadata)
    except yaml.YAMLError as exc:
        _log.warning("Failed to parse suppressions frontmatter: %s", exc)
        return set()

    entries = data.get("suppressions") or []
    if not isinstance(entries, list):
        return set()

    result: set[tuple[str, str]] = set()
    for entry in entries:
        if isinstance(entry, dict) and "rule" in entry and "key" in entry:
            result.add((entry["rule"], str(entry["key"])))
    return result


def is_suppressed(rule: str, finding: dict, suppressions: set[tuple[str, str]]) -> bool:
    """Return True if this finding matches any entry in the suppressions set."""
    return (rule, _make_key(rule, finding)) in suppressions


def filter_findings(
    rule: str,
    findings: list[dict],
    suppressions: set[tuple[str, str]],
) -> tuple[list[dict], int]:
    """Split findings into (unsuppressed_list, suppressed_count)."""
    unsuppressed = []
    suppressed_count = 0
    for finding in findings:
        if (rule, _make_key(rule, finding)) in suppressions:
            suppressed_count += 1
        else:
            unsuppressed.append(finding)
    return unsuppressed, suppressed_count
