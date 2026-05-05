"""Unit tests for cowork_graph.patterns — one test class per pattern."""

from cowork_graph.patterns import (
    DECISION_ALL_FIELDS,
    DECISION_OPTIONAL_FIELDS,
    DECISION_REQUIRED_FIELDS,
    ENTITY_PREFIXES,
    RE_DECISION_FIELD,
    RE_DECISION_HEADING,
    RE_MD_LINK,
    RE_RELATED_BLOCK,
    TARGETS_SEPARATOR,
)

EM_DASH = "—"


class TestDecisionHeading:
    def test_basic_match(self):
        line = f"### 2026-05-05 {EM_DASH} Cowork-graph kickoff"
        m = RE_DECISION_HEADING.search(line)
        assert m is not None
        assert m.group("date") == "2026-05-05"
        assert m.group("title") == "Cowork-graph kickoff"

    def test_found_in_multiline_text(self):
        text = f"Some intro\n### 2026-01-15 {EM_DASH} Some decision\nBody text"
        m = RE_DECISION_HEADING.search(text)
        assert m is not None
        assert m.group("date") == "2026-01-15"

    def test_trailing_whitespace_stripped(self):
        line = f"### 2026-05-05 {EM_DASH} Title with trailing   "
        m = RE_DECISION_HEADING.search(line)
        assert m is not None
        assert m.group("title") == "Title with trailing"

    def test_hyphen_not_matched(self):
        line = "### 2026-05-05 - Wrong dash"
        assert RE_DECISION_HEADING.search(line) is None

    def test_en_dash_not_matched(self):
        line = "### 2026-05-05 – En dash"
        assert RE_DECISION_HEADING.search(line) is None

    def test_h2_not_matched(self):
        line = f"## 2026-05-05 {EM_DASH} Not H3"
        assert RE_DECISION_HEADING.search(line) is None

    def test_no_date_not_matched(self):
        line = f"### No date {EM_DASH} Some title"
        assert RE_DECISION_HEADING.search(line) is None

    def test_multiple_headings_found(self):
        text = (
            f"### 2026-01-01 {EM_DASH} First\n"
            "body\n"
            f"### 2026-02-02 {EM_DASH} Second\n"
        )
        matches = list(RE_DECISION_HEADING.finditer(text))
        assert len(matches) == 2
        assert matches[0].group("title") == "First"
        assert matches[1].group("title") == "Second"


class TestDecisionField:
    def _body(self, *fields: tuple[str, str]) -> str:
        return "\n".join(f"**{name}:** {val}" for name, val in fields)

    def test_all_required_fields_extracted(self):
        body = self._body(
            ("Decision", "We chose SQLite."),
            ("Why", "Simple and stdlib."),
            ("Alternatives considered", "Kuzu, DuckDB."),
            ("Source / context", "Kickoff conversation."),
        )
        found = {m.group("field"): m.group("value").strip() for m in RE_DECISION_FIELD.finditer(body)}
        assert set(DECISION_REQUIRED_FIELDS) <= set(found)

    def test_optional_field_extracted(self):
        body = "**Principle in play:** Law of Power #1"
        m = RE_DECISION_FIELD.search(body)
        assert m is not None
        assert m.group("field") == "Principle in play"
        assert "Law of Power" in m.group("value")

    def test_multiline_value_captured(self):
        body = "**Decision:** We chose SQLite.\nIt is simple.\n**Why:** Because stdlib."
        found = {m.group("field"): m.group("value").strip() for m in RE_DECISION_FIELD.finditer(body)}
        assert "We chose SQLite.\nIt is simple." in found["Decision"]
        assert found["Why"] == "Because stdlib."

    def test_unrecognized_field_not_matched(self):
        body = "**UnknownField:** some value"
        assert RE_DECISION_FIELD.search(body) is None

    def test_value_stops_at_next_heading(self):
        text = (
            f"**Decision:** pick A\n"
            f"**Why:** because\n"
            f"### 2026-05-06 {EM_DASH} Next decision\n"
        )
        found = {m.group("field"): m.group("value").strip() for m in RE_DECISION_FIELD.finditer(text)}
        assert "Next decision" not in found.get("Why", "")


class TestMdLink:
    def test_basic_relative_link(self):
        m = RE_MD_LINK.search("[some text](path/to/file.md)")
        assert m is not None
        assert m.group("text") == "some text"
        assert m.group("target") == "path/to/file.md"

    def test_external_url_captured(self):
        m = RE_MD_LINK.search("[example](https://example.com)")
        assert m is not None
        assert m.group("target") == "https://example.com"

    def test_anchor_only_link(self):
        m = RE_MD_LINK.search("[section](#anchor)")
        assert m is not None
        assert m.group("target") == "#anchor"

    def test_no_link_returns_none(self):
        assert RE_MD_LINK.search("no links here") is None

    def test_multiple_links_found(self):
        matches = RE_MD_LINK.findall("[a](x.md) and [b](y.md)")
        assert len(matches) == 2

    def test_link_with_path_and_anchor(self):
        m = RE_MD_LINK.search("[doc](path/doc.md#section)")
        assert m is not None
        assert m.group("target") == "path/doc.md#section"


class TestRelatedBlock:
    def test_related_hubs(self):
        line = "> **Related hubs:** [hub1](x.md) · [hub2](y.md)"
        m = RE_RELATED_BLOCK.search(line)
        assert m is not None
        assert "Related hub" in m.group("label")

    def test_related_hub_singular(self):
        line = "> **Related hub:** [hub1](x.md)"
        m = RE_RELATED_BLOCK.search(line)
        assert m is not None

    def test_sibling(self):
        line = "> **Sibling:** [sib](s.md)"
        m = RE_RELATED_BLOCK.search(line)
        assert m is not None

    def test_siblings_plural(self):
        line = "> **Siblings:** [sib](s.md)"
        m = RE_RELATED_BLOCK.search(line)
        assert m is not None

    def test_downstream(self):
        line = "> **Downstream:** [dep](d.md)"
        m = RE_RELATED_BLOCK.search(line)
        assert m is not None
        assert m.group("label") == "Downstream"

    def test_upstream(self):
        line = "> **Upstream:** [blocker](b.md)"
        m = RE_RELATED_BLOCK.search(line)
        assert m is not None
        assert m.group("label") == "Upstream"

    def test_targets_extracted(self):
        line = "> **Downstream:** [a](a.md) · [b](b.md)"
        m = RE_RELATED_BLOCK.search(line)
        assert m is not None
        assert TARGETS_SEPARATOR in m.group("targets")

    def test_indented_blockquote_not_matched(self):
        # Column-0 requirement: leading spaces disqualify
        assert RE_RELATED_BLOCK.search("  > **Related hubs:** [hub](x.md)") is None

    def test_missing_bold_not_matched(self):
        assert RE_RELATED_BLOCK.search("> Related hubs: [hub](x.md)") is None

    def test_found_in_multiline_text(self):
        text = "intro\n> **Related hubs:** [parent](p.md)\nbody"
        m = RE_RELATED_BLOCK.search(text)
        assert m is not None


class TestFieldConstants:
    def test_required_fields_complete(self):
        assert DECISION_REQUIRED_FIELDS == {
            "Decision",
            "Why",
            "Alternatives considered",
            "Source / context",
        }

    def test_optional_fields(self):
        assert DECISION_OPTIONAL_FIELDS == {"Principle in play"}

    def test_all_fields_is_union(self):
        assert DECISION_ALL_FIELDS == DECISION_REQUIRED_FIELDS | DECISION_OPTIONAL_FIELDS

    def test_required_and_optional_disjoint(self):
        assert not DECISION_REQUIRED_FIELDS & DECISION_OPTIONAL_FIELDS


class TestEntityPrefixes:
    def test_all_three_entities_present(self):
        slugs = {slug for _, slug in ENTITY_PREFIXES}
        assert "personal" in slugs
        assert "autoscriptstudio" in slugs
        assert "nexus-legacy-holdings" in slugs

    def test_longest_prefix_first(self):
        # nexus-legacy-holdings is the longest; must come before shorter ones
        # to avoid 'nexus-legacy-holdings/' being shadowed by a shorter match
        prefixes = [prefix for prefix, _ in ENTITY_PREFIXES]
        nl_idx = next(i for i, p in enumerate(prefixes) if "nexus" in p)
        as_idx = next(i for i, p in enumerate(prefixes) if "autoscript" in p)
        pe_idx = next(i for i, p in enumerate(prefixes) if "personal" in p)
        assert nl_idx < as_idx
        assert nl_idx < pe_idx
