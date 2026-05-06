"""Tests for parser.py — all pure (no DB, no filesystem writes)."""

from pathlib import Path

from cowork_graph.parser import (
    ParseResult,
    extract_links,
    extract_mentions,
    extract_related_blocks,
    parse_decision_log,
    parse_doc,
    parse_frontmatter,
)

CORPUS = Path(__file__).parent / "fixtures" / "corpus"


class TestParseFrontmatter:
    def test_parses_tags(self):
        text = "---\ntags:\n  - status/active\n  - type/hub\n---\n# Body"
        fm, body, status, notes = parse_frontmatter(text)
        assert fm["tags"] == ["status/active", "type/hub"]
        assert status == "ok"
        assert notes is None

    def test_body_returned_without_frontmatter_block(self):
        text = "---\ntags:\n  - status/active\n---\n# Body\n\nContent here."
        _, body, _, _ = parse_frontmatter(text)
        assert "# Body" in body
        assert "tags:" not in body

    def test_broken_yaml_returns_partial(self):
        text = "---\ntags: [unclosed\n---\n# Body"
        fm, body, status, notes = parse_frontmatter(text)
        assert status == "partial"
        assert notes is not None
        assert "# Body" in body

    def test_no_frontmatter_returns_ok(self):
        text = "# Just a heading\n\nNo frontmatter."
        fm, body, status, notes = parse_frontmatter(text)
        assert status == "ok"
        assert fm == {}
        assert "# Just a heading" in body


class TestExtractRelatedBlocks:
    def test_related_hubs_extracted(self):
        body = "> **Related hubs:** [hub](../personal/hub.md)\nBody text."
        blocks = extract_related_blocks(body)
        assert len(blocks) == 1
        assert "Related hub" in blocks[0].label
        assert "../personal/hub.md" in blocks[0].targets

    def test_downstream_extracted(self):
        body = "> **Downstream:** [dep](dep.md)"
        blocks = extract_related_blocks(body)
        assert blocks[0].label == "Downstream"

    def test_multiple_targets_on_one_line(self):
        body = "> **Related hubs:** [a](a.md) · [b](b.md)"
        blocks = extract_related_blocks(body)
        assert len(blocks[0].targets) == 2

    def test_no_blocks_returns_empty(self):
        assert extract_related_blocks("Just body text with [links](x.md).") == []


class TestExtractLinks:
    def _root(self) -> Path:
        return CORPUS

    def test_resolved_link(self):
        doc_path = "autoscriptstudio/autoscript-hub.md"
        body = "[personal hub](../personal/personal-hub.md)"
        links = extract_links(body, doc_path=doc_path, cowork_root=self._root())
        assert len(links) == 1
        assert links[0].resolved == "personal/personal-hub.md"
        assert not links[0].is_broken

    def test_broken_link(self):
        doc_path = "personal/project-doc.md"
        body = "[missing](nonexistent.md)"
        links = extract_links(body, doc_path=doc_path, cowork_root=self._root())
        assert links[0].is_broken

    def test_external_link_not_broken(self):
        doc_path = "personal/project-doc.md"
        body = "[example](https://example.com)"
        links = extract_links(body, doc_path=doc_path, cowork_root=self._root())
        assert links[0].is_external
        assert not links[0].is_broken

    def test_anchor_only_link(self):
        doc_path = "personal/project-doc.md"
        body = "[section](#anchor)"
        links = extract_links(body, doc_path=doc_path, cowork_root=self._root())
        assert links[0].is_anchor
        assert not links[0].is_broken

    def test_out_of_tree_link_is_broken(self):
        doc_path = "personal/project-doc.md"
        body = "[escape](../../outside-cowork.md)"
        links = extract_links(body, doc_path=doc_path, cowork_root=self._root())
        assert links[0].is_broken

    def test_dotdot_resolved_link_normalised(self):
        # [link](../foo.md) from dir/source.md should resolve to foo.md, not dir/../foo.md
        doc_path = "autoscriptstudio/autoscript-hub.md"
        body = "[personal hub](../personal/personal-hub.md)"
        links = extract_links(body, doc_path=doc_path, cowork_root=self._root())
        assert links[0].resolved == "personal/personal-hub.md"
        assert ".." not in (links[0].resolved or "")

    def test_sub_dotdot_normalised(self):
        # sub/../sibling.md resolved against dir/source.md → dir/sibling.md
        doc_path = "autoscriptstudio/autoscript-hub.md"
        body = "[hub](sub/../autoscript-hub.md)"
        links = extract_links(body, doc_path=doc_path, cowork_root=self._root())
        assert links[0].resolved == "autoscriptstudio/autoscript-hub.md"
        assert ".." not in (links[0].resolved or "")

    def test_dotslash_normalised(self):
        # ./local.md from dir/source.md → dir/local.md (no leading ./)
        doc_path = "autoscriptstudio/autoscript-hub.md"
        body = "[hub](./autoscript-hub.md)"
        links = extract_links(body, doc_path=doc_path, cowork_root=self._root())
        assert links[0].resolved == "autoscriptstudio/autoscript-hub.md"
        assert links[0].resolved is not None and not links[0].resolved.startswith("./")


class TestExtractMentions:
    def test_full_name_matched(self):
        body = "This project is run by Jane Doe."
        mentions = extract_mentions(body, persons={"jane-doe": "Jane Doe"})
        assert len(mentions) == 1
        assert mentions[0].person_slug == "jane-doe"

    def test_first_name_only_not_matched(self):
        body = "Jane is great but the full name is not here."
        mentions = extract_mentions(body, persons={"jane-doe": "Jane Doe"})
        assert mentions == []

    def test_case_insensitive_match(self):
        body = "JANE DOE did the work."
        mentions = extract_mentions(body, persons={"jane-doe": "Jane Doe"})
        assert len(mentions) == 1

    def test_multiple_occurrences(self):
        body = "Jane Doe and Jane Doe again."
        mentions = extract_mentions(body, persons={"jane-doe": "Jane Doe"})
        assert len(mentions) == 2

    def test_no_persons_returns_empty(self):
        assert extract_mentions("Jane Doe mentioned.", persons={}) == []


class TestParseDecisionLog:
    def _text(self) -> str:
        return (CORPUS / "memory" / "decisions-log.md").read_text(encoding="utf-8")

    def test_valid_entry_parsed_ok(self):
        decisions = parse_decision_log(
            self._text(),
            log_doc="memory/decisions-log.md",
            cowork_root=CORPUS,
        )
        ok = [d for d in decisions if d.parse_status == "ok"]
        assert len(ok) == 1
        assert ok[0].date == "2026-05-05"
        assert "SQLite" in ok[0].title
        assert ok[0].decision_text is not None
        assert ok[0].why is not None

    def test_format_drift_entry_detected(self):
        decisions = parse_decision_log(
            self._text(),
            log_doc="memory/decisions-log.md",
            cowork_root=CORPUS,
        )
        drift = [d for d in decisions if d.parse_status == "format_drift"]
        assert len(drift) == 1
        assert drift[0].format_drift_notes is not None
        assert "Why" in drift[0].format_drift_notes or "Source" in drift[0].format_drift_notes

    def test_decision_id_slugified(self):
        decisions = parse_decision_log(
            self._text(),
            log_doc="memory/decisions-log.md",
            cowork_root=CORPUS,
        )
        assert decisions[0].id.startswith("2026-05-05-")

    def test_source_links_extracted(self):
        decisions = parse_decision_log(
            self._text(),
            log_doc="memory/decisions-log.md",
            cowork_root=CORPUS,
        )
        ok = next(d for d in decisions if d.parse_status == "ok")
        assert len(ok.source_links) >= 1

    def test_about_links_from_source_context_field(self):
        # Links in Source / context appear in about_links
        text = (
            "### 2026-05-05 — Test\n\n"
            "**Decision:** Some decision.\n"
            "**Why:** Some why.\n"
            "**Alternatives considered:** None.\n"
            "**Source / context:** See [hub](../autoscriptstudio/autoscript-hub.md).\n"
        )
        decisions = parse_decision_log(text, log_doc="memory/decisions-log.md", cowork_root=CORPUS)
        assert len(decisions) == 1
        resolved = [lk.resolved for lk in decisions[0].about_links]
        assert "autoscriptstudio/autoscript-hub.md" in resolved

    def test_about_links_from_all_fields(self):
        # Links in Decision, Why, and Source / context all end up in about_links
        text = (
            "### 2026-05-05 — Multi-field\n\n"
            "**Decision:** Use [hub](../autoscriptstudio/autoscript-hub.md).\n"
            "**Why:** Because [personal](../personal/personal-hub.md).\n"
            "**Alternatives considered:** None.\n"
            "**Source / context:** See [hub](../autoscriptstudio/autoscript-hub.md) again.\n"
        )
        decisions = parse_decision_log(text, log_doc="memory/decisions-log.md", cowork_root=CORPUS)
        resolved = [lk.resolved for lk in decisions[0].about_links]
        # Both unique targets present
        assert "autoscriptstudio/autoscript-hub.md" in resolved
        assert "personal/personal-hub.md" in resolved
        # No duplicates — autoscript-hub.md appears twice in text but once in about_links
        assert resolved.count("autoscriptstudio/autoscript-hub.md") == 1

    def test_about_links_no_links(self):
        text = (
            "### 2026-05-05 — No links\n\n"
            "**Decision:** Plain text, no links.\n"
            "**Why:** Also plain.\n"
            "**Alternatives considered:** None.\n"
            "**Source / context:** Nothing.\n"
        )
        decisions = parse_decision_log(text, log_doc="memory/decisions-log.md", cowork_root=CORPUS)
        assert decisions[0].about_links == []

    def test_about_links_excludes_broken(self):
        # Broken link targets not included in about_links
        text = (
            "### 2026-05-05 — Broken\n\n"
            "**Decision:** See [missing](nonexistent.md).\n"
            "**Why:** Whatever.\n"
            "**Alternatives considered:** None.\n"
            "**Source / context:** Nothing.\n"
        )
        decisions = parse_decision_log(text, log_doc="memory/decisions-log.md", cowork_root=CORPUS)
        assert decisions[0].about_links == []


class TestParseDoc:
    def _parse(self, rel_path: str, persons: dict | None = None) -> ParseResult:
        full = CORPUS / rel_path
        text = full.read_text(encoding="utf-8")
        return parse_doc(
            path=rel_path,
            text=text,
            last_modified=None,
            parsed_at="2026-05-05T12:00:00",
            cowork_root=CORPUS,
            persons=persons or {},
        )

    def test_entity_from_autoscriptstudio_path(self):
        r = self._parse("autoscriptstudio/autoscript-hub.md")
        assert r.entity == "autoscriptstudio"

    def test_entity_from_personal_path(self):
        r = self._parse("personal/project-doc.md")
        assert r.entity == "personal"

    def test_no_entity_for_memory_path(self):
        r = self._parse("memory/people/jane-doe.md")
        assert r.entity is None

    def test_status_extracted_from_tags(self):
        r = self._parse("autoscriptstudio/autoscript-hub.md")
        assert r.status == "active"

    def test_doc_type_extracted(self):
        r = self._parse("autoscriptstudio/autoscript-hub.md")
        assert r.doc_type == "hub"

    def test_project_slugs_extracted(self):
        r = self._parse("personal/project-doc.md")
        assert "skunkworks" in r.project_slugs

    def test_related_blocks_extracted(self):
        r = self._parse("autoscriptstudio/autoscript-hub.md")
        assert len(r.related_blocks) >= 1

    def test_related_block_targets_not_in_links(self):
        # Links extracted from clean body (after related-block strip)
        # shouldn't double-count related-block targets as plain LINKS_TO
        r = self._parse("autoscriptstudio/autoscript-hub.md")
        link_targets = {lk.resolved for lk in r.links}
        # The inline body link to personal-hub.md should still appear
        assert any("personal-hub" in (t or "") for t in link_targets)

    def test_broken_link_flagged(self):
        r = self._parse("broken-links-doc.md")
        broken = [lk for lk in r.links if lk.is_broken]
        assert len(broken) >= 1
        assert any("nonexistent" in lk.raw_target for lk in broken)

    def test_full_name_mention_found(self):
        r = self._parse(
            "autoscriptstudio/autoscript-hub.md",
            persons={"jane-doe": "Jane Doe"},
        )
        assert any(m.person_slug == "jane-doe" for m in r.mentions)

    def test_word_count_positive(self):
        r = self._parse("autoscriptstudio/autoscript-hub.md")
        assert r.word_count > 0
