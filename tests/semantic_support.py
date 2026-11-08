"""Shared helpers for the semantic-layer tests: a deterministic fake model and
a throwaway corpus/config, so tests never download or load real models."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import replace
from pathlib import Path

from cowork_graph.config import Config, SemanticConfig
from cowork_graph.semantic.store import EMBED_DIM

_WORD = re.compile(r"[a-z0-9]+")


def _bag(text: str) -> list[float]:
    vec = [0.0] * EMBED_DIM
    for w in _WORD.findall(text.lower()):
        h = int(hashlib.md5(w.encode(), usedforsecurity=False).hexdigest(), 16)
        vec[h % EMBED_DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class FakeModels:
    """Bag-of-words 'embeddings' and an overlap 'reranker' in 0..1."""

    def __init__(self):
        self.embedded: list[str] = []

    def embed_documents(self, texts):
        self.embedded.extend(texts)
        return [_bag(t) for t in texts]

    def embed_query(self, text):
        return _bag(text)

    def rerank(self, query, docs):
        q = set(_WORD.findall(query.lower()))
        out = []
        for d in docs:
            words = set(_WORD.findall(d.lower()))
            out.append(len(q & words) / len(q) if q else 0.0)
        return out

    def warm(self):
        pass


class FakeDetector:
    """Stands in for the LFM2.5 detector: flags configured literal values."""

    def __init__(self, values: dict[str, list[str]] | None = None):
        self.values = values or {}

    def available(self):
        return True

    def detect_many(self, texts):
        joined = "\n".join(texts)
        return {
            label: [v for v in vals if v in joined]
            for label, vals in self.values.items()
            if any(v in joined for v in vals)
        }


def make_cfg(tmp_path: Path, files: dict[str, str | bytes] | None = None) -> Config:
    root = tmp_path / "corpus"
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in (files or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
    return Config(
        cowork_root=root,
        db_path=tmp_path / "graph.db",
        semantic=replace(
            SemanticConfig(),
            db_path=tmp_path / "semantic.db",
            model_cache=tmp_path / "models",
            pii_detect="off",
            workers=2,
        ),
    )
