"""Phase 8 semantic layer: extraction, chunking, PII, sync, search, documents, memories."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime

import pytest

pytest.importorskip("sqlite_vec")
pytest.importorskip("pymupdf")

from cowork_graph import db
from cowork_graph.semantic import chunking, extract, memory, pii, search, store, sync
from tests.semantic_support import FakeDetector, FakeModels, make_cfg

# ---------------------------------------------------------------------------
# Extraction + chunking
# ---------------------------------------------------------------------------


class TestMarkdown:
    def test_frontmatter_stripped_and_heading_breadcrumbs(self):
        raw = "---\ntags:\n  - status/active\n---\n# Atlas\nintro\n## Rollback\nOmar owns it\n"
        ex = extract.extract_markdown(raw)
        assert [s.heading for s in ex.sections] == ["Atlas", "Atlas > Rollback"]
        assert "status/active" not in ex.text
        assert ex.sections[1].text == "Omar owns it"

    def test_heading_inside_code_fence_is_not_a_heading(self):
        ex = extract.extract_markdown("# Real\n```\n# not a heading\n```\n")
        assert [s.heading for s in ex.sections] == ["Real"]
        assert "# not a heading" in ex.sections[0].text

    def test_sibling_heading_pops_stack(self):
        ex = extract.extract_markdown("# A\n## B\nb\n## C\nc\n")
        assert [s.heading for s in ex.sections] == ["A > B", "A > C"]


class TestChunking:
    def test_short_section_single_chunk(self):
        chunks = chunking.chunk_sections([extract.Section("hello world", heading="H")])
        assert len(chunks) == 1 and chunks[0].heading == "H" and chunks[0].ordinal == 0

    def test_long_text_splits_with_overlap(self):
        paras = [f"Paragraph {i} " + "word " * 40 for i in range(12)]
        chunks = chunking.chunk_sections([extract.Section("\n\n".join(paras))])
        assert len(chunks) > 3
        for c in chunks:
            assert len(c.text) <= chunking.CHUNK_SIZE + chunking.CHUNK_OVERLAP + 2
        # Each chunk after the first starts with the tail of the previous one.
        assert chunks[1].text.split("\n\n")[0] in chunks[0].text

    def test_giant_unbroken_paragraph_is_hard_wrapped(self):
        chunks = chunking.chunk_sections([extract.Section("x" * 5000)])
        assert all(len(c.text) <= chunking.CHUNK_SIZE + chunking.CHUNK_OVERLAP + 2 for c in chunks)

    def test_embed_text_carries_title_and_heading(self):
        c = store.Chunk(0, "body", heading="Rollback")
        assert chunking.embed_text(c, "atlas launch") == "atlas launch > Rollback\nbody"


class TestOtherFormats:
    def test_csv_rows(self):
        ex = extract.extract_csv("a,b\n1,2\n,\n")
        assert ex.sections[0].text == "a | b\n1 | 2"

    def test_pdf_text_layer(self, tmp_path):
        import pymupdf

        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "Invoice for consulting services rendered")
        doc.new_page()  # blank page: no text, OCR off
        p = tmp_path / "x.pdf"
        doc.save(p)
        ex = extract.extract_pdf(p, ocr=False)
        assert len(ex.sections) == 1 and ex.sections[0].page == 1
        assert "consulting" in ex.text and ex.ocr_pages == 0

    def test_image_without_ocr_is_empty(self, tmp_path):
        p = tmp_path / "x.png"
        p.write_bytes(b"\x89PNG")
        assert extract.extract_image(p, ocr=False).sections == []

    def test_looks_like_text(self):
        assert extract.looks_like_text("This is a scanned letter about the custody schedule")
        assert not extract.looks_like_text("~~ |/ 0 _ ;: i1 ,. ")


# ---------------------------------------------------------------------------
# PII (ported rules)
# ---------------------------------------------------------------------------


class TestPii:
    def test_mask_whole_token_with_tail(self):
        assert pii.mask("acct 123456789 ok", ["123456789"]) == "acct [pii …6789] ok"

    def test_substance_gate_blocks_fragments(self):
        # Fragments like "50" must never shred amounts.
        assert pii.mask("paid -14.50 today", ["50"]) == "paid -14.50 today"

    def test_boundary_anchored(self):
        assert pii.mask("X1234567890Y", ["1234567890"]) == "X1234567890Y"

    def test_longest_first(self):
        text = "card 4111111111111111"
        assert pii.mask(text, ["411111", "4111111111111111"]) == "card [pii …1111]"

    def test_maskable_labels(self):
        labels = ("financial", "identity.ssn")
        assert pii.maskable("financial.account_number", labels)
        assert not pii.maskable("person.name", labels)
        assert pii.maskable("anything", ("all",))

    def test_obvious_identifiers(self):
        assert pii.obvious_identifiers("ssn 123-45-6789")
        assert pii.obvious_identifiers("card 4111 1111 1111 1111")
        assert not pii.obvious_identifiers("order 1234 5678 9012 3456")  # fails Luhn
        assert not pii.obvious_identifiers("call me at 2026-11-07")


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def _sync(cfg, models=None, **kw):
    return sync.sync(cfg, models=models or FakeModels(), log=lambda _m: None, **kw)


def _rows(cfg):
    conn = store.connect(cfg.semantic.db_path, readonly=True)
    try:
        return {r["path"]: dict(r) for r in conn.execute("SELECT * FROM file")}
    finally:
        conn.close()


class TestSync:
    def test_add_then_noop(self, tmp_path):
        cfg = make_cfg(tmp_path, {"a.md": "# A\nalpha", "b/c.txt": "charlie", "skip.json": "{}"})
        s = _sync(cfg)
        assert (s.added, s.errors) == (2, [])
        assert set(_rows(cfg)) == {"a.md", "b/c.txt"}
        models = FakeModels()
        s2 = _sync(cfg, models)
        assert (s2.added, s2.updated, s2.touched) == (0, 0, 0) and models.embedded == []

    def test_modify_delete_and_touch(self, tmp_path):
        cfg = make_cfg(tmp_path, {"a.md": "# A\nalpha", "b.md": "# B\nbravo"})
        _sync(cfg)
        root = cfg.cowork_root
        (root / "a.md").write_text("# A\nalpha changed", encoding="utf-8")
        (root / "b.md").unlink()
        s = _sync(cfg)
        assert (s.updated, s.deleted) == (1, 1)
        # Same bytes, new mtime (a fresh checkout): hash-confirmed, not re-embedded.
        st = (root / "a.md").stat()
        os.utime(root / "a.md", ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
        models = FakeModels()
        s = _sync(cfg, models)
        assert s.touched == 1 and models.embedded == []

    def test_skip_dirs(self, tmp_path):
        cfg = make_cfg(
            tmp_path,
            {"_archive/old.md": "x", ".obsidian/n.md": "x", "_backups/b.md": "x", "k.md": "keep"},
        )
        _sync(cfg)
        assert set(_rows(cfg)) == {"k.md"}

    def test_error_recorded_and_not_retried_until_changed(self, tmp_path, monkeypatch):
        cfg = make_cfg(tmp_path, {"bad.pdf": b"%PDF-not-really", "ok.md": "fine"})
        s = _sync(cfg)
        assert [p for p, _ in s.errors] == ["bad.pdf"]
        assert _rows(cfg)["bad.pdf"]["status"] == "error"
        calls = []
        real = extract.extract

        def spy(path, kind, *, ocr):
            calls.append(path.name)
            return real(path, kind, ocr=ocr)

        monkeypatch.setattr(extract, "extract", spy)
        _sync(cfg)
        assert calls == []
        _sync(cfg, retry_errors=True)
        assert calls == ["bad.pdf"]

    def test_skipped_file_indexed_by_retry_after_cap_raised(self, tmp_path, monkeypatch):
        body = "\n\n".join(f"Paragraph {i}. " + "word " * 150 for i in range(6))
        cfg = make_cfg(tmp_path, {"long.md": body})
        monkeypatch.setattr(chunking, "MAX_FILE_CHUNKS", 1)
        _sync(cfg)
        assert _rows(cfg)["long.md"]["status"] == "skipped"
        monkeypatch.setattr(chunking, "MAX_FILE_CHUNKS", 3000)
        _sync(cfg)
        assert _rows(cfg)["long.md"]["status"] == "skipped"  # unchanged file, no retry asked
        _sync(cfg, retry_errors=True)
        row = _rows(cfg)["long.md"]
        assert row["status"] == "ok" and row["chunk_count"] > 1

    def test_empty_file_kept_as_empty(self, tmp_path):
        cfg = make_cfg(tmp_path, {"blank.md": "---\ntags: []\n---\n"})
        s = _sync(cfg)
        assert s.empty == 1 and _rows(cfg)["blank.md"]["status"] == "empty"

    def test_only_limits_scope_and_never_deletes(self, tmp_path):
        cfg = make_cfg(tmp_path, {"a.md": "alpha", "b.md": "bravo"})
        _sync(cfg)
        (cfg.cowork_root / "b.md").unlink()
        (cfg.cowork_root / "c.md").write_text("charlie", encoding="utf-8")
        s = _sync(cfg, only=["c.md"])
        assert (s.added, s.deleted) == (1, 0)
        assert set(_rows(cfg)) == {"a.md", "b.md", "c.md"}

    def test_writer_lock_is_exclusive(self, tmp_path):
        cfg = make_cfg(tmp_path, {"a.md": "alpha"})
        with store.writer_lock(cfg.semantic.db_path), pytest.raises(store.SemanticBusy):
            _sync(cfg)

    def test_embed_model_change_refused(self, tmp_path):
        cfg = make_cfg(tmp_path, {"a.md": "alpha"})
        _sync(cfg)
        conn = store.connect(cfg.semantic.db_path)
        store.set_meta(conn, "embed_model", "some/other-model")
        conn.commit()
        conn.close()
        with pytest.raises(RuntimeError, match="other-model"):
            _sync(cfg)

    def test_pii_recorded_by_detector(self, tmp_path):
        cfg = make_cfg(tmp_path, {"f.md": "Account 987654321 at the bank"})
        _sync(cfg, detector=FakeDetector({"financial.account": ["987654321"]}))
        conn = store.connect(cfg.semantic.db_path, readonly=True)
        try:
            assert conn.execute("SELECT label, value FROM pii").fetchall()[0][1] == "987654321"
            assert conn.execute("SELECT pii_scanned FROM file").fetchone()[0] == 1
        finally:
            conn.close()

    def test_unscanned_files_rescanned_once_detector_appears(self, tmp_path):
        cfg = make_cfg(tmp_path, {"f.md": "Account 987654321"})
        _sync(cfg)  # no detector
        s = _sync(cfg, detector=FakeDetector({"financial.account": ["987654321"]}))
        assert s.updated == 1 and _rows(cfg)["f.md"]["pii_scanned"] == 1


# ---------------------------------------------------------------------------
# Search + read_document
# ---------------------------------------------------------------------------

CORPUS = {
    "personal/atlas.md": "# Atlas launch\nOmar Bell owns rollback for the Atlas migration.",
    "personal/garden.md": "# Garden\nTomatoes need full sun and deep watering.",
    "autoscriptstudio/atlas-budget.md": "# Atlas budget\nThe Atlas migration budget is 40k.",
    "autoscriptstudio/legal/contract.txt": "Atlas vendor contract signed by Omar Bell.",
}


@pytest.fixture
def indexed(tmp_path):
    cfg = make_cfg(tmp_path, CORPUS)
    _sync(cfg, detector=FakeDetector({"financial.account": ["4111111111111111"]}))
    return cfg


def _q(cfg, query, **kw):
    return search.search(cfg, FakeModels(), query, use_recency=False, **kw)


class TestSearch:
    def test_best_match_first(self, indexed):
        res = _q(indexed, "who owns rollback for atlas")
        assert res.items[0].path == "personal/atlas.md"
        assert res.items[0].title == "atlas.md"  # no graph: filename fallback
        assert set(res.timings_ms) >= {"embed", "vector", "keyword", "rerank"}

    def test_scope_include_and_exclude(self, indexed):
        inc = {h.path for h in _q(indexed, "atlas", scope="autoscriptstudio").items}
        assert inc and all(p.startswith("autoscriptstudio/") for p in inc)
        exc = {h.path for h in _q(indexed, "atlas", scope="-autoscriptstudio").items}
        assert exc and not any(p.startswith("autoscriptstudio/") for p in exc)

    def test_kind_and_path_prefix(self, indexed):
        assert {h.path for h in _q(indexed, "atlas omar", kind="text").items} == {
            "autoscriptstudio/legal/contract.txt"
        }
        assert {
            h.path for h in _q(indexed, "atlas", path_prefix="autoscriptstudio/legal").items
        } == {"autoscriptstudio/legal/contract.txt"}

    def test_bad_kind_and_empty_query_fail_loud(self, indexed):
        with pytest.raises(ValueError):
            _q(indexed, "atlas", kind="video")
        with pytest.raises(ValueError):
            _q(indexed, "   ")

    def test_per_source_cap_and_limit(self, tmp_path):
        long_doc = "\n\n".join(f"Atlas note {i}: " + "atlas detail " * 30 for i in range(10))
        cfg = make_cfg(tmp_path, {"a.md": long_doc, "b.md": "atlas once"})
        _sync(cfg)
        res = _q(cfg, "atlas detail", limit=10)
        assert sum(h.path == "a.md" for h in res.items) == search.PER_SOURCE

    def test_snippets_masked(self, tmp_path):
        cfg = make_cfg(tmp_path, {"card.md": "Card on file 4111111111111111 for the Atlas vendor"})
        _sync(cfg, detector=FakeDetector({"financial.card": ["4111111111111111"]}))
        hit = _q(cfg, "atlas vendor card").items[0]
        assert "4111111111111111" not in hit.snippet and "[pii …1111]" in hit.snippet

    def test_names_not_masked(self, tmp_path):
        cfg = make_cfg(tmp_path, {"p.md": "Priya Rao approved the Atlas plan"})
        _sync(cfg, detector=FakeDetector({"person.name": ["Priya Rao"]}))
        assert "Priya Rao" in _q(cfg, "who approved atlas").items[0].snippet

    def test_graph_status_and_project_filters(self, indexed):
        conn = db.connect(indexed.db_path)
        now = datetime.now(UTC).isoformat()
        for path, status in (("personal/atlas.md", "active"), ("personal/garden.md", "done")):
            db.upsert_doc(
                conn,
                path=path,
                title=path.split("/")[-1].upper(),
                status=status,
                doc_type=None,
                word_count=1,
                link_count=0,
                last_modified=None,
                parsed_at=now,
                parse_status="ok",
                parse_notes=None,
            )
        conn.execute("INSERT INTO project (slug, hub_doc, is_ghost) VALUES ('atlas', NULL, 1)")
        conn.execute(
            "INSERT INTO edge (source_type, source_id, edge_type, target_type, target_id,"
            " edge_subtype) VALUES ('doc', 'personal/atlas.md', 'MEMBER_OF_PROJECT',"
            " 'project', 'atlas', '')"
        )
        conn.commit()
        conn.close()
        res = _q(indexed, "atlas tomatoes", status="active")
        assert [h.path for h in res.items] == ["personal/atlas.md"]
        assert res.items[0].title == "ATLAS.MD" and res.items[0].projects == ["atlas"]
        assert [h.path for h in _q(indexed, "atlas", project="atlas").items] == [
            "personal/atlas.md"
        ]

    def test_status_filter_without_graph_fails_loud(self, indexed):
        with pytest.raises(RuntimeError, match="graph"):
            _q(indexed, "atlas", status="active")

    def test_since_filter(self, indexed):
        assert _q(indexed, "atlas", since="2999-01-01").items == []
        with pytest.raises(ValueError):
            _q(indexed, "atlas", since="last tuesday")

    def test_fts_query_quotes_tokens(self):
        assert search.fts_query('RFC-2041 "AND" a') == '"rfc" OR "2041" OR "and"'
        assert search.fts_query("!!") is None


class TestReadDocument:
    def test_reads_and_pages(self, tmp_path, monkeypatch):
        monkeypatch.setattr(search, "READ_PAGE_CHARS", 50)
        cfg = make_cfg(tmp_path, {"long.md": "# L\n" + ("abcdefghij " * 20)})
        _sync(cfg)
        p1 = search.read_document(cfg, "long.md")
        assert p1["pages"] > 1 and "note" in p1
        last = search.read_document(cfg, "long.md", page=999)
        assert last["page"] == p1["pages"]

    @pytest.mark.parametrize("bad", ["../etc/passwd", "/etc/passwd", "a/../../x", ".."])
    def test_traversal_rejected(self, indexed, bad):
        with pytest.raises(ValueError):
            search.read_document(indexed, bad)

    def test_unindexed_rejected(self, indexed):
        with pytest.raises(FileNotFoundError):
            search.read_document(indexed, "personal/nope.md")

    def test_masked(self, tmp_path):
        cfg = make_cfg(tmp_path, {"c.md": "card 4111111111111111"})
        _sync(cfg, detector=FakeDetector({"financial.card": ["4111111111111111"]}))
        assert "4111111111111111" not in search.read_document(cfg, "c.md")["text"]


# ---------------------------------------------------------------------------
# Memories
# ---------------------------------------------------------------------------


class TestMemory:
    def test_save_writes_markdown_and_indexes(self, tmp_path):
        cfg = make_cfg(tmp_path, {"other.md": "unrelated atlas text"})
        models = FakeModels()
        resp = memory.save_memory(
            cfg,
            models,
            None,
            "Kalshicaster live orders stay disarmed until Joe re-arms them.",
            tags=["project/kalshicaster"],
        )
        assert resp["indexed"] and resp["path"].startswith("memory/saved/")
        body = (cfg.cowork_root / resp["path"]).read_text()
        assert "  - type/memory" in body and "  - project/kalshicaster" in body
        assert "status/reference" in body
        hits = memory.recall(cfg, models, "kalshicaster live orders")["items"]
        assert [h["path"] for h in hits] == [resp["path"]]

    def test_recall_only_searches_memories(self, tmp_path):
        cfg = make_cfg(tmp_path, {"notes.md": "kalshicaster everything"})
        _sync(cfg)
        assert memory.recall(cfg, FakeModels(), "kalshicaster")["items"] == []

    def test_guard_refuses_identifiers(self, tmp_path):
        cfg = make_cfg(tmp_path)
        with pytest.raises(memory.MemoryRefused, match="…6789"):
            memory.save_memory(cfg, FakeModels(), None, "My SSN is 123-45-6789")
        with pytest.raises(memory.MemoryRefused):
            memory.save_memory(
                cfg,
                FakeModels(),
                FakeDetector({"credential.api_key": ["sk-abcdef123456"]}),
                "the key is sk-abcdef123456",
            )
        assert not (cfg.cowork_root / "memory/saved").exists() or not list(
            (cfg.cowork_root / "memory/saved").iterdir()
        )

    def test_bad_tag_and_empty_refused(self, tmp_path):
        cfg = make_cfg(tmp_path)
        with pytest.raises(memory.MemoryRefused):
            memory.write_memory(cfg, "x", tags=["not a tag"])
        with pytest.raises(memory.MemoryRefused):
            memory.write_memory(cfg, "   ")

    def test_long_title_cut_on_word_boundary(self):
        title = memory._title_from("word " * 40)
        assert title.endswith("word…") and len(title) <= memory.TITLE_MAX + 1

    def test_slug_collision_gets_suffix(self, tmp_path):
        cfg = make_cfg(tmp_path)
        when = datetime(2026, 11, 7, tzinfo=UTC)
        a = memory.write_memory(cfg, "Same title here", saved_at=when)
        b = memory.write_memory(cfg, "Same title here", saved_at=when)
        assert a != b and b.name.endswith("-2.md")

    def test_busy_index_still_saves(self, tmp_path):
        cfg = make_cfg(tmp_path)
        with store.writer_lock(cfg.semantic.db_path):
            resp = memory.save_memory(cfg, FakeModels(), None, "note while syncing")
        assert resp["indexed"] is False and (cfg.cowork_root / resp["path"]).exists()

    def test_import_rag_memories(self, tmp_path):
        cfg = make_cfg(tmp_path)
        src = tmp_path / "memories.json"
        src.write_text(
            json.dumps(
                [
                    {
                        "chroma:document": "PixelDrops: Stack Trace Docs is the only active product.",
                        "created": "2026-08-22 22:27 UTC",
                        "kind": "user",
                    },
                    {"chroma:document": "", "created": "2026-08-22 22:28 UTC"},
                ]
            )
        )
        written = memory.import_rag_memories(cfg, src, None)
        assert written == [
            "memory/saved/2026-08-22-pixeldrops-stack-trace-docs-is-the-only-active.md"
        ]
        body = (cfg.cowork_root / written[0]).read_text()
        assert (
            "Imported from cowork-rag-chat" in body and "saved: 2026-08-22T22:27:00+00:00" in body
        )


def test_schema_version_mismatch_refused(tmp_path):
    cfg = make_cfg(tmp_path, {"a.md": "alpha"})
    _sync(cfg)
    conn = sqlite3.connect(cfg.semantic.db_path)
    conn.execute("UPDATE meta SET value='0' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="schema"):
        store.connect(cfg.semantic.db_path)
