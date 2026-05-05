-- cowork-graph v1 schema — frozen 2026-05-05.
-- DO NOT modify columns, types, or constraints without updating plan.md and bumping schema_version.
-- Every CREATE uses IF NOT EXISTS so bootstrap is idempotent.

-- Schema metadata (single-row config)
CREATE TABLE IF NOT EXISTS schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
-- Seed rows inserted by db.py:
--   ('schema_version', '1')
--   ('parser_version', '<cowork_graph.__version__>')
--   ('built_at',       '<iso>')
--   ('build_kind',     'full' | 'incremental' | 'merge_full')

-- ==========================================================
-- Node tables
-- ==========================================================

CREATE TABLE IF NOT EXISTS doc (
  path           TEXT PRIMARY KEY,    -- relative path from cowork root, forward-slash normalized
  title          TEXT,                 -- first H1, fallback to filename without extension
  status         TEXT,                 -- e.g. 'active', 'queued', 'done', 'blocked', 'reference'
  doc_type       TEXT,                 -- e.g. 'hub', 'reference', 'log', 'project'
  word_count     INTEGER NOT NULL DEFAULT 0,
  link_count     INTEGER NOT NULL DEFAULT 0,
  last_modified  TEXT,                 -- ISO 8601 from filesystem stat
  parsed_at      TEXT NOT NULL,        -- ISO 8601 of this parse run
  parse_status   TEXT NOT NULL,        -- 'ok' | 'partial' | 'failed'
  parse_notes    TEXT                  -- nullable; populated when parse_status != 'ok'
);

CREATE TABLE IF NOT EXISTS person (
  slug          TEXT PRIMARY KEY,      -- filename slug under memory/people/
  display_name  TEXT NOT NULL,
  role          TEXT,
  source_doc    TEXT,                  -- path to memory/people/<slug>.md, NULL for ghosts
  is_ghost      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS person_alias (
  slug   TEXT NOT NULL,
  alias  TEXT NOT NULL,
  PRIMARY KEY (slug, alias),
  FOREIGN KEY (slug) REFERENCES person(slug) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS project (
  slug      TEXT PRIMARY KEY,          -- the value after 'project/' in frontmatter tags
  hub_doc   TEXT,                      -- path to canonical hub doc, NULL for ghosts
  is_ghost  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS vendor (
  slug          TEXT PRIMARY KEY,
  display_name  TEXT NOT NULL,
  category      TEXT,
  source_doc    TEXT,                  -- where the vendor entry lives
  is_ghost      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS vendor_alias (
  slug   TEXT NOT NULL,
  alias  TEXT NOT NULL,
  PRIMARY KEY (slug, alias),
  FOREIGN KEY (slug) REFERENCES vendor(slug) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS entity (
  slug TEXT PRIMARY KEY               -- 'personal' | 'autoscriptstudio' | 'nexus-legacy-holdings'
);
-- Seeded by db.py: ('personal'), ('autoscriptstudio'), ('nexus-legacy-holdings')

CREATE TABLE IF NOT EXISTS decision (
  id                  TEXT PRIMARY KEY, -- '<date>-<slugified-title>', e.g. '2026-05-05-cowork-graph-kickoff'
  date                TEXT NOT NULL,    -- YYYY-MM-DD
  title               TEXT NOT NULL,
  decision_text       TEXT,             -- body of **Decision:** field
  why                 TEXT,             -- body of **Why:** field
  alternatives        TEXT,             -- body of **Alternatives considered:** field
  principle           TEXT,             -- body of **Principle in play:** field, nullable
  source_context      TEXT,             -- body of **Source / context:** field
  log_doc             TEXT NOT NULL,    -- path to memory/decisions-log.md (or wherever it lives)
  status              TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'superseded'
  parse_status        TEXT NOT NULL,    -- 'ok' | 'format_drift'
  format_drift_notes  TEXT              -- nullable; describes which fields/format failed validation
);

CREATE TABLE IF NOT EXISTS tag (
  tag TEXT PRIMARY KEY                 -- full tag string, e.g. 'status/active', 'type/hub'
);

-- ==========================================================
-- Unified edge table
-- ==========================================================

CREATE TABLE IF NOT EXISTS edge (
  source_type    TEXT NOT NULL,
  source_id      TEXT NOT NULL,
  edge_type      TEXT NOT NULL,        -- 'LINKS_TO' | 'TAGGED' | 'MEMBER_OF_PROJECT' | 'MEMBER_OF_ENTITY'
                                       -- 'MENTIONS' | 'BLOCKS' | 'RELATED_TO' | 'SUPERSEDES'
                                       -- 'OWNS' | 'ABOUT_DECISION'
  target_type    TEXT NOT NULL,
  target_id      TEXT NOT NULL,
  edge_subtype   TEXT NOT NULL DEFAULT '',  -- 'parent_hub' | 'sibling' | 'downstream' | 'upstream' | ''
  confidence     TEXT,
  context        TEXT,
  PRIMARY KEY (source_type, source_id, edge_type, target_type, target_id, edge_subtype)
);

-- ==========================================================
-- Health / drift tracking
-- ==========================================================

CREATE TABLE IF NOT EXISTS broken_link (
  source_doc      TEXT NOT NULL,
  link_text       TEXT NOT NULL,
  link_target     TEXT NOT NULL,       -- what the link tried to resolve to
  detected_at     TEXT NOT NULL,
  PRIMARY KEY (source_doc, link_text, link_target)
);

CREATE TABLE IF NOT EXISTS format_drift (
  artifact_kind   TEXT NOT NULL,       -- 'decision' for now
  artifact_id     TEXT NOT NULL,
  detected_at     TEXT NOT NULL,
  notes           TEXT NOT NULL,
  PRIMARY KEY (artifact_kind, artifact_id)
);

-- ==========================================================
-- Indexes
-- ==========================================================

-- Edge query patterns: outbound from a node, inbound to a node, by-type slices
CREATE INDEX IF NOT EXISTS idx_edge_source ON edge(source_type, source_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_edge_target ON edge(target_type, target_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_edge_type   ON edge(edge_type);

-- Doc filters used by list_active, list_blocked, search_docs
CREATE INDEX IF NOT EXISTS idx_doc_status   ON doc(status);
CREATE INDEX IF NOT EXISTS idx_doc_type     ON doc(doc_type);
CREATE INDEX IF NOT EXISTS idx_doc_modified ON doc(last_modified);

-- Decision filters
CREATE INDEX IF NOT EXISTS idx_decision_date   ON decision(date);
CREATE INDEX IF NOT EXISTS idx_decision_status ON decision(status);

-- Ghost lookups (partial indexes)
CREATE INDEX IF NOT EXISTS idx_person_ghost  ON person(is_ghost)  WHERE is_ghost = 1;
CREATE INDEX IF NOT EXISTS idx_project_ghost ON project(is_ghost) WHERE is_ghost = 1;
CREATE INDEX IF NOT EXISTS idx_vendor_ghost  ON vendor(is_ghost)  WHERE is_ghost = 1;

-- Alias lookups
CREATE INDEX IF NOT EXISTS idx_person_alias_alias ON person_alias(alias);
CREATE INDEX IF NOT EXISTS idx_vendor_alias_alias ON vendor_alias(alias);

-- ==========================================================
-- Full-text search (Phase 3)
-- ==========================================================

-- Contentful FTS5 table so snippet() is available.
-- path is UNINDEXED (stored, not tokenized); join to doc on path for metadata.
CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5(
  path  UNINDEXED,
  title,
  body
);
