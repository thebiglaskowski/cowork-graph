"""Eval parity with cowork-rag-chat (plan.md Phase 8, migration step 3).

Runs the RAG's 64-question hybrid suite (tests/fixtures/hybrid-eval/suite.json,
copied here as eval_suite.json) against an ephemeral corpus in a temp dir —
the live index plays no part. Documents land at <scope>/<source> so the RAG's
metadata scopes map onto this layer's path-prefix scopes.

RAG baseline (2026-09-17, raw Nomic + local MiniLM): first-source 55/56,
source-in-top-5 56/56, all 8 no-answer cases below 0.5.
"""

from __future__ import annotations

import json
import statistics
import tempfile
import time
from dataclasses import replace
from importlib.resources import files
from pathlib import Path

from cowork_graph.config import Config, SemanticConfig
from cowork_graph.semantic import search, sync
from cowork_graph.semantic.models import Models

BASELINE = {"hit_at_1": 55 / 56, "hit_at_5": 1.0, "no_answer_rejected_at_0_5": 1.0}


def load_suite() -> dict:
    return json.loads((files("cowork_graph.semantic") / "eval_suite.json").read_text("utf-8"))


def run(models: Models, model_cache: Path, suite: dict | None = None) -> dict:
    suite = suite or load_suite()
    with tempfile.TemporaryDirectory(prefix="cowork-graph-eval-") as tmp:
        root = Path(tmp) / "corpus"
        for doc in suite["documents"]:
            p = root / doc["scope"] / doc["source"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(doc["text"], encoding="utf-8")
        cfg = Config(
            cowork_root=root,
            db_path=Path(tmp) / "no-graph.db",
            semantic=replace(
                SemanticConfig(),
                db_path=Path(tmp) / "semantic.db",
                model_cache=model_cache,
                pii_detect="off",
            ),
        )
        sync.sync(cfg, models=models, log=lambda _m: None)
        models.warm()
        rows = []
        for case in suite["cases"]:
            t0 = time.monotonic()
            res = search.search(
                cfg,
                models,
                case["query"],
                scope=case.get("scope") or None,
                limit=5,
                per_source=1,
                use_recency=False,
            )
            ms = (time.monotonic() - t0) * 1000
            sources = [Path(h.path).name for h in res.items]
            scores = [h.score for h in res.items]
            expected = set(case["expected"])
            ranks = [sources.index(s) + 1 for s in expected if s in sources]
            evidence = case.get("evidence") or ""
            rows.append(
                {
                    "id": case["id"],
                    "category": case["category"],
                    "expected": case["expected"],
                    "sources": sources,
                    "scores": scores,
                    "hit1": bool(sources and sources[0] in expected),
                    "hit5": bool(ranks),
                    "rr": 1 / min(ranks) if ranks else 0.0,
                    "evidence_found": not evidence
                    or any(evidence.lower() in h.snippet.lower() for h in res.items),
                    "ms": round(ms, 1),
                }
            )
    pos = [r for r in rows if r["expected"]]
    neg = [r for r in rows if not r["expected"]]
    lat = sorted(r["ms"] for r in rows)
    summary = {
        "queries": len(rows),
        "hit_at_1": sum(r["hit1"] for r in pos) / len(pos),
        "hit_at_5": sum(r["hit5"] for r in pos) / len(pos),
        "mrr": sum(r["rr"] for r in pos) / len(pos),
        "evidence_found": sum(r["evidence_found"] for r in pos) / len(pos),
        "no_answer_rejected_at_0_5": sum(max(r["scores"], default=0) < 0.5 for r in neg) / len(neg),
        "positive_accepted_at_0_5": sum(max(r["scores"], default=0) >= 0.5 for r in pos) / len(pos),
        "median_ms": statistics.median(lat),
        "p95_ms": lat[int(0.95 * (len(lat) - 1))],
    }
    summary["parity"] = all(summary[k] >= v for k, v in BASELINE.items())
    return {"summary": summary, "cases": rows}
