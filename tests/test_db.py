"""Tests for db.py — all run against an in-memory SQLite database."""

import pytest
from cowork_graph import db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


class TestBootstrap:
    def test_all_tables_created(self, conn):
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        expected = {
            "schema_meta", "doc", "person", "person_alias", "project",
            "vendor", "vendor_alias", "entity", "decision", "tag",
            "edge", "broken_link", "format_drift",
        }
        assert expected <= tables

    def test_entity_seeds_present(self, conn):
        slugs = {row[0] for row in conn.execute("SELECT slug FROM entity")}
        assert slugs == {"personal", "autoscriptstudio", "nexus-legacy-holdings"}

    def test_owns_edge_seeded(self, conn):
        row = conn.execute(
            "SELECT 1 FROM edge WHERE source_id='nexus-legacy-holdings'"
            " AND target_id='autoscriptstudio' AND edge_type='OWNS'"
        ).fetchone()
        assert row is not None

    def test_schema_version_set(self, conn):
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        assert row is not None
        assert row[0] == "1"

    def test_foreign_keys_enabled(self, conn):
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1

    def test_bootstrap_is_idempotent(self, conn):
        # Calling connect again on the same file should not raise or duplicate rows
        db._bootstrap(conn)
        count = conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
        assert count == 3


class TestUpsertDoc:
    def _insert(self, conn, path="autoscriptstudio/hub.md", parse_status="ok"):
        conn.execute("BEGIN")
        db.upsert_doc(
            conn,
            path=path,
            title="Hub",
            status="active",
            doc_type="hub",
            word_count=100,
            link_count=3,
            last_modified="2026-05-05T10:00:00",
            parsed_at="2026-05-05T12:00:00",
            parse_status=parse_status,
            parse_notes=None,
        )
        conn.execute("COMMIT")

    def test_doc_inserted(self, conn):
        self._insert(conn)
        row = conn.execute("SELECT * FROM doc WHERE path='autoscriptstudio/hub.md'").fetchone()
        assert row is not None
        assert row["title"] == "Hub"
        assert row["status"] == "active"

    def test_upsert_replaces_existing(self, conn):
        self._insert(conn)
        conn.execute("BEGIN")
        db.upsert_doc(
            conn,
            path="autoscriptstudio/hub.md",
            title="Updated Hub",
            status="done",
            doc_type="hub",
            word_count=200,
            link_count=5,
            last_modified="2026-05-06T10:00:00",
            parsed_at="2026-05-06T12:00:00",
            parse_status="ok",
            parse_notes=None,
        )
        conn.execute("COMMIT")
        row = conn.execute("SELECT * FROM doc WHERE path='autoscriptstudio/hub.md'").fetchone()
        assert row["title"] == "Updated Hub"
        assert row["word_count"] == 200


class TestUpsertTag:
    def test_tag_inserted(self, conn):
        conn.execute("BEGIN")
        db.upsert_tag(conn, tag="status/active")
        conn.execute("COMMIT")
        row = conn.execute("SELECT tag FROM tag WHERE tag='status/active'").fetchone()
        assert row is not None

    def test_duplicate_tag_ignored(self, conn):
        conn.execute("BEGIN")
        db.upsert_tag(conn, tag="type/hub")
        db.upsert_tag(conn, tag="type/hub")
        conn.execute("COMMIT")
        count = conn.execute("SELECT COUNT(*) FROM tag WHERE tag='type/hub'").fetchone()[0]
        assert count == 1


class TestUpsertPerson:
    def test_person_inserted(self, conn):
        conn.execute("BEGIN")
        db.upsert_person(
            conn,
            slug="jane-doe",
            display_name="Jane Doe",
            role="contractor",
            source_doc="memory/people/jane-doe.md",
            is_ghost=False,
        )
        conn.execute("COMMIT")
        row = conn.execute("SELECT * FROM person WHERE slug='jane-doe'").fetchone()
        assert row["display_name"] == "Jane Doe"
        assert row["is_ghost"] == 0

    def test_ghost_person(self, conn):
        conn.execute("BEGIN")
        db.upsert_person(
            conn,
            slug="unknown",
            display_name="Unknown Person",
            role=None,
            source_doc=None,
            is_ghost=True,
        )
        conn.execute("COMMIT")
        row = conn.execute("SELECT is_ghost FROM person WHERE slug='unknown'").fetchone()
        assert row["is_ghost"] == 1

    def test_person_alias(self, conn):
        conn.execute("BEGIN")
        db.upsert_person(
            conn, slug="jane-doe", display_name="Jane Doe",
            role=None, source_doc=None, is_ghost=False,
        )
        db.upsert_person_alias(conn, slug="jane-doe", alias="Jane")
        db.upsert_person_alias(conn, slug="jane-doe", alias="Jane")  # duplicate
        conn.execute("COMMIT")
        count = conn.execute(
            "SELECT COUNT(*) FROM person_alias WHERE slug='jane-doe'"
        ).fetchone()[0]
        assert count == 1

    def test_alias_cascade_delete(self, conn):
        conn.execute("BEGIN")
        db.upsert_person(
            conn, slug="jane-doe", display_name="Jane Doe",
            role=None, source_doc=None, is_ghost=False,
        )
        db.upsert_person_alias(conn, slug="jane-doe", alias="Jane")
        conn.execute("COMMIT")
        conn.execute("DELETE FROM person WHERE slug='jane-doe'")
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM person_alias WHERE slug='jane-doe'"
        ).fetchone()[0]
        assert count == 0


class TestUpsertProject:
    def test_ghost_project(self, conn):
        conn.execute("BEGIN")
        db.upsert_project(conn, slug="skunkworks", hub_doc=None, is_ghost=True)
        conn.execute("COMMIT")
        row = conn.execute("SELECT * FROM project WHERE slug='skunkworks'").fetchone()
        assert row["is_ghost"] == 1
        assert row["hub_doc"] is None

    def test_real_project(self, conn):
        conn.execute("BEGIN")
        db.upsert_project(
            conn, slug="cowork-graph",
            hub_doc="claude-environment/cowork-graph/plan.md",
            is_ghost=False,
        )
        conn.execute("COMMIT")
        row = conn.execute("SELECT * FROM project WHERE slug='cowork-graph'").fetchone()
        assert row["is_ghost"] == 0


class TestUpsertEdge:
    def test_edge_inserted(self, conn):
        conn.execute("BEGIN")
        db.upsert_edge(
            conn,
            source_type="doc",
            source_id="autoscriptstudio/hub.md",
            edge_type="LINKS_TO",
            target_type="doc",
            target_id="personal/notes.md",
        )
        conn.execute("COMMIT")
        row = conn.execute(
            "SELECT * FROM edge WHERE source_id='autoscriptstudio/hub.md'"
        ).fetchone()
        assert row is not None
        assert row["edge_type"] == "LINKS_TO"

    def test_edge_with_subtype(self, conn):
        conn.execute("BEGIN")
        db.upsert_edge(
            conn,
            source_type="doc",
            source_id="autoscriptstudio/hub.md",
            edge_type="RELATED_TO",
            target_type="doc",
            target_id="personal/parent-hub.md",
            edge_subtype="parent_hub",
        )
        conn.execute("COMMIT")
        row = conn.execute(
            "SELECT * FROM edge WHERE edge_type='RELATED_TO'"
        ).fetchone()
        assert row["edge_subtype"] == "parent_hub"

    def test_edge_idempotent(self, conn):
        for _ in range(3):
            conn.execute("BEGIN")
            db.upsert_edge(
                conn,
                source_type="doc",
                source_id="a.md",
                edge_type="LINKS_TO",
                target_type="doc",
                target_id="b.md",
            )
            conn.execute("COMMIT")
        count = conn.execute("SELECT COUNT(*) FROM edge").fetchone()[0]
        # 1 inserted by us + 1 OWNS edge from bootstrap
        assert count == 2


class TestUpsertBrokenLink:
    def test_broken_link_inserted(self, conn):
        conn.execute("BEGIN")
        db.upsert_broken_link(
            conn,
            source_doc="personal/notes.md",
            link_text="dead link",
            link_target="nonexistent.md",
            detected_at="2026-05-05T12:00:00",
        )
        conn.execute("COMMIT")
        row = conn.execute("SELECT * FROM broken_link").fetchone()
        assert row["link_target"] == "nonexistent.md"


class TestUpsertFormatDrift:
    def test_format_drift_inserted(self, conn):
        conn.execute("BEGIN")
        db.upsert_format_drift(
            conn,
            artifact_kind="decision",
            artifact_id="2026-05-05-some-decision",
            detected_at="2026-05-05T12:00:00",
            notes="Missing required field: Why",
        )
        conn.execute("COMMIT")
        row = conn.execute("SELECT * FROM format_drift").fetchone()
        assert row["notes"] == "Missing required field: Why"


class TestUpdateMeta:
    def test_meta_updated(self, conn):
        db.update_meta(conn, built_at="2026-05-05T12:00:00", build_kind="full")
        conn.commit()
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='built_at'"
        ).fetchone()
        assert row["value"] == "2026-05-05T12:00:00"
