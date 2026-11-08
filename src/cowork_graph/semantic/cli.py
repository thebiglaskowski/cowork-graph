"""`cowork-graph semantic …` and `cowork-graph memory …` subcommands."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from cowork_graph import config as cfg_mod
from cowork_graph import semantic

HELP = """\
  semantic build             Extract, embed and index the whole corpus (resumable; same
                             work as sync on an empty index).
  semantic sync [--retry-errors] [--wait]
                             Re-index only files whose size/mtime/content changed.
                             Run after every graph update by the git hooks.
  semantic status [--json]   Index counts, failures, models, DB size.
  semantic eval [--json]     Golden-question parity check on an ephemeral corpus.
  memory import <json>       One-time import of cowork-rag-chat memories into memory/saved/.
"""


def _require() -> bool:
    if not semantic.available():
        print(f"semantic layer not installed — {semantic.MISSING_DEPS_HINT}", file=sys.stderr)
        return False
    return True


def _runtime(cfg):
    from cowork_graph.semantic.models import shared
    from cowork_graph.semantic.pii import Detector

    return shared(cfg.semantic.model_cache), Detector(
        cfg.semantic.pii_detect, cfg.semantic.pii_device
    )


def run_sync(cfg, *, retry_errors: bool = False, wait: bool = False, quiet: bool = False) -> int:
    from cowork_graph.semantic import store, sync

    models, detector = _runtime(cfg)
    try:
        summary = sync.sync(
            cfg,
            models=models,
            detector=detector,
            retry_errors=retry_errors,
            wait_for_lock=wait,
            log=(lambda _m: None) if quiet else (lambda m: print(m, flush=True)),
        )
    except store.SemanticBusy as exc:
        print(f"semantic: {exc}; the next sync will pick up any changes", file=sys.stderr)
        return 0
    print(summary.line())
    for rel, err in summary.errors[:20]:
        print(f"  error: {rel}: {err}", file=sys.stderr)
    if len(summary.errors) > 20:
        print(f"  ...and {len(summary.errors) - 20} more errors", file=sys.stderr)
    return 0


def after_graph_update(cfg) -> None:
    """Called by `cowork-graph update` (the git hooks). Only once the index exists:
    the first build is a deliberate act, not something a commit should trigger."""
    if semantic.available() and cfg.semantic.db_path.exists():
        run_sync(cfg, quiet=True)


def status(cfg) -> dict:
    from cowork_graph.semantic import store

    conn = store.connect(cfg.semantic.db_path, readonly=True)
    try:
        by = [
            dict(r)
            for r in conn.execute(
                "SELECT kind, status, COUNT(*) AS files, SUM(chunk_count) AS chunks"
                " FROM file GROUP BY kind, status ORDER BY kind, status"
            )
        ]
        errors = [
            dict(r)
            for r in conn.execute(
                "SELECT path, status, error FROM file WHERE status IN ('error','skipped')"
                " ORDER BY path LIMIT 50"
            )
        ]
        totals = conn.execute(
            "SELECT COUNT(*) AS files, COALESCE(SUM(chunk_count),0) AS chunks,"
            " COALESCE(SUM(ocr_pages),0) AS ocr_pages,"
            " SUM(status='ok' AND pii_scanned=0) AS pii_unscanned FROM file"
        ).fetchone()
        pii_records = conn.execute("SELECT COUNT(*) FROM pii").fetchone()[0]
        meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    finally:
        conn.close()
    size = sum(
        p.stat().st_size
        for p in cfg.semantic.db_path.parent.glob(cfg.semantic.db_path.name + "*")
        if p.is_file()
    )
    return {
        "db_path": str(cfg.semantic.db_path),
        "db_bytes": size,
        **dict(totals),
        "pii_records": pii_records,
        "by_kind": by,
        "problems": errors,
        "meta": meta,
    }


def main(args: list[str]) -> int:
    if not args:
        print(HELP)
        return 1
    if not _require():
        return 1
    cfg = cfg_mod.load()
    cmd = args[0]
    if cmd in ("build", "sync"):
        return run_sync(cfg, retry_errors="--retry-errors" in args, wait="--wait" in args)
    if cmd == "status":
        try:
            st = status(cfg)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if "--json" in args:
            print(json.dumps(st, indent=2))
            return 0
        print(f"{st['db_path']}  ({st['db_bytes'] / 1e6:.1f} MB)")
        print(
            f"{st['files']} files, {st['chunks']} chunks, {st['ocr_pages']} OCR'd pages, "
            f"{st['pii_records']} PII records, {st['pii_unscanned'] or 0} files not PII-scanned"
        )
        for r in st["by_kind"]:
            print(
                f"  {r['kind']:<7} {r['status']:<8} {r['files']:>6} files {r['chunks'] or 0:>7} chunks"
            )
        for p in st["problems"]:
            print(f"  {p['status']}: {p['path']}: {p['error']}")
        print(
            f"  last sync: {st['meta'].get('last_sync', 'never')}, "
            f"embed model: {st['meta'].get('embed_model')}"
        )
        return 0
    if cmd == "eval":
        from cowork_graph.semantic import evaluate

        models, _ = _runtime(cfg)
        result = evaluate.run(models, cfg.semantic.model_cache)
        if "--json" in args:
            print(json.dumps(result, indent=2))
        else:
            s = result["summary"]
            print(
                f"hit@1 {s['hit_at_1']:.3f}  hit@5 {s['hit_at_5']:.3f}  mrr {s['mrr']:.3f}  "
                f"no-answer rejected {s['no_answer_rejected_at_0_5']:.3f}  "
                f"median {s['median_ms']:.0f} ms  p95 {s['p95_ms']:.0f} ms  "
                f"parity with cowork-rag-chat: {'yes' if s['parity'] else 'NO'}"
            )
        return 0 if result["summary"]["parity"] else 2
    print(f"Unknown semantic subcommand: {cmd}\n{HELP}", file=sys.stderr)
    return 1


def memory_main(args: list[str]) -> int:
    if len(args) != 2 or args[0] != "import":
        print("Usage: cowork-graph memory import <exported-memories.json>", file=sys.stderr)
        return 1
    if not _require():
        return 1
    from cowork_graph.semantic import memory

    cfg = cfg_mod.load()
    _, detector = _runtime(cfg)
    try:
        written = memory.import_rag_memories(cfg, Path(args[1]), detector)
    except memory.MemoryRefused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for rel in written:
        print(f"wrote {rel}")
    print(f"{len(written)} memories imported; run `cowork-graph semantic sync` to index them")
    return 0
