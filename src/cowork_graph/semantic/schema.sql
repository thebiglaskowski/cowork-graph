-- cowork-graph semantic layer schema (plan.md "Phase 8"). Derived state:
-- deletable and rebuildable from the corpus at any time. Bump
-- semantic_schema_version (store.py) on any change; a mismatch forces a rebuild.

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- One row per discovered corpus file, including ones that produced no text,
-- so unchanged files are never re-extracted.
CREATE TABLE IF NOT EXISTS file (
  path         TEXT PRIMARY KEY,     -- cowork-root relative, posix
  kind         TEXT NOT NULL,        -- md | pdf | image | office | text
  size         INTEGER NOT NULL,
  mtime_ns     INTEGER NOT NULL,
  modified     INTEGER NOT NULL,     -- epoch s: last git commit touching the file, else mtime
  md5          TEXT NOT NULL,
  status       TEXT NOT NULL,        -- ok | empty | error | skipped
  error        TEXT,                 -- populated when status is error/skipped
  chunk_count  INTEGER NOT NULL DEFAULT 0,
  ocr_pages    INTEGER NOT NULL DEFAULT 0,
  pii_scanned  INTEGER NOT NULL DEFAULT 0,
  indexed_at   TEXT NOT NULL
);

-- Full extracted text, so read_document can serve OCR'd files without
-- re-running OCR.
CREATE TABLE IF NOT EXISTS file_text (
  path  TEXT PRIMARY KEY REFERENCES file(path) ON DELETE CASCADE,
  text  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunk (
  id       INTEGER PRIMARY KEY,
  path     TEXT NOT NULL REFERENCES file(path) ON DELETE CASCADE,
  ordinal  INTEGER NOT NULL,
  page     INTEGER,                  -- 1-based PDF page; NULL otherwise
  heading  TEXT,                     -- "H1 > H2 > H3" breadcrumb, sheet name, etc.
  text     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunk_path ON chunk(path, ordinal);

-- PII recorded at ingest; masked at serve time (only labels under
-- pii_mask_labels, only values passing the substance gate).
CREATE TABLE IF NOT EXISTS pii (
  path   TEXT NOT NULL REFERENCES file(path) ON DELETE CASCADE,
  label  TEXT NOT NULL,
  value  TEXT NOT NULL,
  PRIMARY KEY (path, label, value)
);

-- External-content FTS over chunk text; kept in step by store.py.
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
  heading, text, content='chunk', content_rowid='id', tokenize='porter unicode61'
);
