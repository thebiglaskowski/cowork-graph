"""Parsing logic for cowork markdown files. Never writes to the DB."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

import frontmatter
import yaml

from cowork_graph.patterns import (
    DECISION_ALL_FIELDS,
    DECISION_REQUIRED_FIELDS,
    ENTITY_PREFIXES,
    RE_DECISION_FIELD,
    RE_DECISION_HEADING,
    RE_MD_LINK,
    RE_RELATED_BLOCK,
)


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------


@dataclass
class RelatedBlock:
    label: str       # 'Related hubs', 'Siblings', 'Downstream', 'Upstream'
    targets: list[str]  # root-relative normalized paths (posixpath.normpath applied)


@dataclass
class Link:
    text: str
    raw_target: str
    resolved: str | None   # relative path from cowork root, None if not resolvable
    is_external: bool
    is_anchor: bool
    is_broken: bool


@dataclass
class Decision:
    id: str
    date: str
    title: str
    decision_text: str | None
    why: str | None
    alternatives: str | None
    principle: str | None
    source_context: str | None
    log_doc: str
    status: str
    parse_status: str          # 'ok' | 'format_drift'
    format_drift_notes: str | None
    source_links: list[Link]   # links from the Source / context field
    about_links: list[Link]    # deduplicated resolved links from all fields (for ABOUT_DECISION edges)


@dataclass
class Mention:
    person_slug: str
    display_name: str
    context: str   # short surrounding snippet


@dataclass
class ParseResult:
    path: str                  # relative path from cowork root
    title: str | None
    status: str | None
    doc_type: str | None
    tags: list[str]
    project_slugs: list[str]
    entity: str | None
    word_count: int
    link_count: int
    last_modified: str | None
    parsed_at: str
    parse_status: str          # 'ok' | 'partial' | 'failed'
    parse_notes: str | None
    related_blocks: list[RelatedBlock]
    links: list[Link]
    decisions: list[Decision]
    mentions: list[Mention]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_doc(
    *,
    path: str,
    text: str,
    last_modified: str | None,
    parsed_at: str,
    cowork_root: Path,
    persons: dict[str, str],  # slug -> display_name
) -> ParseResult:
    """Parse a single markdown doc. Returns a ParseResult; never writes to DB."""
    fm, body, parse_status, parse_notes = parse_frontmatter(text)

    tags: list[str] = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    tags = [str(t) for t in tags]

    status = _first_tag_value(tags, "status/")
    doc_type = _first_tag_value(tags, "type/")
    project_slugs = [t[len("project/"):].split("/")[0] for t in tags if t.startswith("project/")]

    entity = _entity_from_path(path)

    title = _extract_title(body, path)
    word_count = len(body.split())
    related_blocks = extract_related_blocks(body, doc_path=path)
    # Strip related-block lines before link extraction
    clean_body = RE_RELATED_BLOCK.sub("", body)
    links = extract_links(clean_body, doc_path=path, cowork_root=cowork_root)
    link_count = sum(1 for lk in links if not lk.is_anchor and not lk.is_external)
    mentions = extract_mentions(clean_body, persons=persons)

    return ParseResult(
        path=path,
        title=title,
        status=status,
        doc_type=doc_type,
        tags=tags,
        project_slugs=project_slugs,
        entity=entity,
        word_count=word_count,
        link_count=link_count,
        last_modified=last_modified,
        parsed_at=parsed_at,
        parse_status=parse_status,
        parse_notes=parse_notes,
        related_blocks=related_blocks,
        links=links,
        decisions=[],  # populated only for decisions-log docs by caller
        mentions=mentions,
    )


def parse_frontmatter(text: str) -> tuple[dict, str, str, str | None]:
    """Return (metadata_dict, body_text, parse_status, parse_notes)."""
    try:
        post = frontmatter.loads(text)
        return dict(post.metadata), post.content, "ok", None
    except yaml.YAMLError as exc:
        # Parse failed — try to recover body by stripping the YAML block
        body = _strip_frontmatter_raw(text)
        return {}, body, "partial", str(exc)


def extract_related_blocks(body: str, *, doc_path: str) -> list[RelatedBlock]:
    blocks = []
    for m in RE_RELATED_BLOCK.finditer(body):
        label = m.group("label")
        raw_targets = m.group("targets")
        link_targets = [
            _resolve_link_target(doc_path, lm.group("target").split("#")[0])
            for lm in RE_MD_LINK.finditer(raw_targets)
        ]
        if link_targets:
            blocks.append(RelatedBlock(label=label, targets=link_targets))
    return blocks


def extract_links(body: str, *, doc_path: str, cowork_root: Path) -> list[Link]:
    links = []
    for m in RE_MD_LINK.finditer(body):
        text = m.group("text")
        raw = m.group("target").strip()
        is_external = raw.startswith(("http://", "https://", "mailto:"))
        is_anchor = raw.startswith("#")
        resolved = None
        is_broken = False

        if not is_external and not is_anchor:
            # Strip any trailing anchor from the target before resolving
            bare = raw.split("#")[0]
            try:
                resolved_rel = _resolve_link_target(doc_path, bare)
                # Normalise away any .. components
                abs_candidate = (cowork_root / resolved_rel).resolve()
                if abs_candidate.is_relative_to(cowork_root) and abs_candidate.exists():
                    resolved = abs_candidate.relative_to(cowork_root).as_posix()
                else:
                    is_broken = True
                    resolved = resolved_rel
            except Exception:
                is_broken = True

        links.append(Link(
            text=text,
            raw_target=raw,
            resolved=resolved,
            is_external=is_external,
            is_anchor=is_anchor,
            is_broken=is_broken,
        ))
    return links


def parse_decision_log(text: str, *, log_doc: str, cowork_root: Path) -> list[Decision]:
    """Parse memory/decisions-log.md into a list of Decision objects."""
    decisions = []
    heading_matches = list(RE_DECISION_HEADING.finditer(text))
    for i, heading_m in enumerate(heading_matches):
        entry_start = heading_m.end()
        entry_end = heading_matches[i + 1].start() if i + 1 < len(heading_matches) else len(text)
        entry_body = text[entry_start:entry_end]

        date = heading_m.group("date")
        title = heading_m.group("title")
        entry_id = f"{date}-{_slugify(title)}"

        fields: dict[str, str] = {}
        for fm in RE_DECISION_FIELD.finditer(entry_body):
            fields[fm.group("field")] = fm.group("value").strip()

        missing = DECISION_REQUIRED_FIELDS - set(fields)
        unknown = set(fields) - DECISION_ALL_FIELDS
        drift_parts = []
        if missing:
            drift_parts.append(f"missing required fields: {', '.join(sorted(missing))}")
        if unknown:
            drift_parts.append(f"unrecognized fields: {', '.join(sorted(unknown))}")

        parse_status = "format_drift" if drift_parts else "ok"
        drift_notes = "; ".join(drift_parts) if drift_parts else None

        source_context_text = fields.get("Source / context", "")
        source_links = extract_links(source_context_text, doc_path=log_doc, cowork_root=cowork_root)

        # Collect links from all fields combined, deduplicated by resolved path.
        all_field_text = "\n".join(v for v in fields.values() if v)
        all_links_raw = extract_links(all_field_text, doc_path=log_doc, cowork_root=cowork_root)
        seen_targets: set[str] = set()
        about_links: list[Link] = []
        for lk in all_links_raw:
            if not lk.is_broken and lk.resolved and lk.resolved not in seen_targets:
                seen_targets.add(lk.resolved)
                about_links.append(lk)

        decisions.append(Decision(
            id=entry_id,
            date=date,
            title=title,
            decision_text=fields.get("Decision"),
            why=fields.get("Why"),
            alternatives=fields.get("Alternatives considered"),
            principle=fields.get("Principle in play"),
            source_context=source_context_text or None,
            log_doc=log_doc,
            status="active",
            parse_status=parse_status,
            format_drift_notes=drift_notes,
            source_links=source_links,
            about_links=about_links,
        ))
    return decisions


def extract_mentions(body: str, *, persons: dict[str, str]) -> list[Mention]:
    """Find full-canonical-name mentions only. v1: no aliases, no fuzzy."""
    mentions = []
    seen: set[tuple[str, int]] = set()
    for slug, display_name in persons.items():
        pattern = re.compile(r"\b" + re.escape(display_name) + r"\b", re.IGNORECASE)
        for m in pattern.finditer(body):
            key = (slug, m.start())
            if key in seen:
                continue
            seen.add(key)
            start = max(0, m.start() - 40)
            end = min(len(body), m.end() + 40)
            context = body[start:end].replace("\n", " ")
            mentions.append(Mention(person_slug=slug, display_name=display_name, context=context))
    return mentions


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_link_target(source_doc_path: str, link_target: str) -> str:
    """Resolve link_target relative to source_doc_path and normalize the result.

    Called from every edge-target construction site so normalization is consistent.
    """
    return posixpath.normpath(
        posixpath.join(posixpath.dirname(source_doc_path), link_target)
    )


def _extract_title(body: str, path: str) -> str | None:
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip() or None
    # Fallback: filename without extension
    return Path(path).stem


def _first_tag_value(tags: list[str], prefix: str) -> str | None:
    for t in tags:
        if t.startswith(prefix):
            return t[len(prefix):]
    return None


def _entity_from_path(path: str) -> str | None:
    for prefix, slug in ENTITY_PREFIXES:
        if path.startswith(prefix):
            return slug
    return None


def _slugify(title: str) -> str:
    slug = title.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = slug.strip("-")
    return slug


def _strip_frontmatter_raw(text: str) -> str:
    """Best-effort body extraction when YAML parsing fails."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text
