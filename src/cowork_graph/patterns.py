"""Compiled regex patterns for the cowork-graph parser. All patterns live here."""

import re

# ---------------------------------------------------------------------------
# Decision-log patterns
# ---------------------------------------------------------------------------

# H3 heading: ### YYYY-MM-DD — Title  (em-dash U+2014, not hyphen or en-dash)
RE_DECISION_HEADING = re.compile(
    r"^###\s+(?P<date>\d{4}-\d{2}-\d{2})\s+—\s+(?P<title>.+?)\s*$",
    re.MULTILINE,
)

# Bold-prefix fields inside a decision entry body.
# Value runs until the next bold field, the next ### heading, or end-of-file.
RE_DECISION_FIELD = re.compile(
    r"^\*\*(?P<field>Decision|Why|Alternatives considered|Principle in play|Source / context):\*\*"
    r"\s*(?P<value>.*?)"
    r"(?=^\*\*[A-Z][^*]+:\*\*|^###\s|\Z)",
    re.MULTILINE | re.DOTALL,
)

# Canonical field sets — hardcoded, not derived from the Format section at runtime.
# The Format section is checked against these constants; divergence is a format_drift finding.
DECISION_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"Decision", "Why", "Alternatives considered", "Source / context"}
)
DECISION_OPTIONAL_FIELDS: frozenset[str] = frozenset({"Principle in play"})
DECISION_ALL_FIELDS: frozenset[str] = DECISION_REQUIRED_FIELDS | DECISION_OPTIONAL_FIELDS

# ---------------------------------------------------------------------------
# Markdown link pattern
# ---------------------------------------------------------------------------

RE_MD_LINK = re.compile(r"\[(?P<text>[^\]]+)\]\((?P<target>[^)]+)\)")

# ---------------------------------------------------------------------------
# Related-block lines (single-line blockquotes with a bold label)
# ---------------------------------------------------------------------------

RE_RELATED_BLOCK = re.compile(
    r"^>\s*\*\*(?P<label>Related hubs?|Siblings?|Downstream|Upstream):\*\*\s*(?P<targets>.+?)\s*$",
    re.MULTILINE,
)

# Multiple targets on a Related-block line are separated by this literal string
TARGETS_SEPARATOR = " · "  # ' · '

# ---------------------------------------------------------------------------
# Entity path prefixes (ordered longest-first to avoid prefix shadowing)
# ---------------------------------------------------------------------------

ENTITY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("nexus-legacy-holdings/", "nexus-legacy-holdings"),
    ("autoscriptstudio/", "autoscriptstudio"),
    ("personal/", "personal"),
)
