"""Lazy in-process retrieval models (FastEmbed on CPU).

Same models as cowork-rag-chat; its 2026-07-29 benchmark showed the CPU at
parity or better than CUDA for both, so no GPU is involved. Loaded on first
use, so graph-only MCP tools never pay the ~1 GB of RAM.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Sequence
from pathlib import Path

EMBED_MODEL = "nomic-ai/nomic-embed-text-v1.5"
RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
EMBED_BATCH = 32
RERANK_BATCH = 32
# Pinned so the embedder and reranker don't each grab all 24 hardware threads
# and thrash each other (measured 2026-11-07: rerank of 40 chunks 290 ms at 8
# threads vs 350-990 ms unpinned under load).
ONNX_THREADS = 8
# Nomic's documented task prefixes. A fresh index, so no legacy raw-vector
# space to stay compatible with (the RAG's 2026-09-17 benchmark showed raw
# and prefixed at parity).
DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


class Models:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self._embed = None
        self._rerank = None
        self._embed_lock = threading.Lock()
        self._rerank_lock = threading.Lock()

    def _embedder(self):
        if self._embed is None:
            from fastembed import TextEmbedding

            self._embed = TextEmbedding(
                model_name=EMBED_MODEL, cache_dir=str(self.cache_dir), threads=ONNX_THREADS
            )
        return self._embed

    def _reranker(self):
        if self._rerank is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            self._rerank = TextCrossEncoder(
                model_name=RERANK_MODEL, cache_dir=str(self.cache_dir), threads=ONNX_THREADS
            )
        return self._rerank

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        with self._embed_lock:
            vecs = self._embedder().embed([DOC_PREFIX + t for t in texts], batch_size=EMBED_BATCH)
            return [v.tolist() for v in vecs]

    def embed_query(self, text: str) -> list[float]:
        with self._embed_lock:
            return next(iter(self._embedder().embed([QUERY_PREFIX + text]))).tolist()

    def rerank(self, query: str, docs: Sequence[str]) -> list[float]:
        """Cross-encoder logits squashed to 0..1 (as the RAG did), in input order."""
        if not docs:
            return []
        with self._rerank_lock:
            scores = self._reranker().rerank(query, list(docs), batch_size=RERANK_BATCH)
            return [1.0 / (1.0 + math.exp(-float(s))) for s in scores]

    def warm(self) -> None:
        self.embed_query("warm up")
        self.rerank("warm up", ["warm up"])


_shared: Models | None = None
_shared_lock = threading.Lock()


def shared(cache_dir: Path) -> Models:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = Models(cache_dir)
        return _shared
