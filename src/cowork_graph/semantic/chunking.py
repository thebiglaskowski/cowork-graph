"""Paragraph-aware chunking — cowork-rag-chat's 600/80 numbers, carried over."""

from __future__ import annotations

import re

from cowork_graph.semantic.extract import Section
from cowork_graph.semantic.store import Chunk

CHUNK_SIZE = 600
CHUNK_OVERLAP = 80
# Files producing more chunks than this are skipped, not truncated: a
# half-indexed file is worse than an honest "skipped" (cowork-rag-chat
# MAX_FILE_CHUNKS).
MAX_FILE_CHUNKS = 2000

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _pieces(text: str) -> list[str]:
    """Paragraphs, with oversized ones broken on sentences, then hard-wrapped."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= CHUNK_SIZE:
            out.append(para)
            continue
        cur = ""
        for sent in _SENTENCE.split(para):
            while len(sent) > CHUNK_SIZE:
                if cur:
                    out.append(cur)
                    cur = ""
                out.append(sent[:CHUNK_SIZE])
                sent = sent[CHUNK_SIZE - CHUNK_OVERLAP :]
            if cur and len(cur) + len(sent) + 1 > CHUNK_SIZE:
                out.append(cur)
                cur = sent
            else:
                cur = f"{cur} {sent}".strip()
        if cur:
            out.append(cur)
    return out


def _tail(text: str) -> str:
    if len(text) <= CHUNK_OVERLAP:
        return text
    snippet = text[-CHUNK_OVERLAP:]
    # Start the overlap on a word boundary.
    space = snippet.find(" ")
    return snippet[space + 1 :] if 0 <= space < len(snippet) - 1 else snippet


def chunk_sections(sections: list[Section]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in sections:
        cur = ""
        for piece in _pieces(section.text):
            if not cur:
                cur = piece
            elif len(cur) + len(piece) + 2 <= CHUNK_SIZE:
                cur = f"{cur}\n\n{piece}"
            else:
                chunks.append(Chunk(len(chunks), cur, section.heading, section.page))
                cur = f"{_tail(cur)}\n\n{piece}".strip()
        if cur:
            chunks.append(Chunk(len(chunks), cur, section.heading, section.page))
    return chunks


def embed_text(chunk: Chunk, title: str | None) -> str:
    """What gets embedded: the chunk plus where it sits, so a paragraph under
    '## Rollback' in 'atlas-launch.md' carries that context into the vector."""
    context = " > ".join(x for x in (title, chunk.heading) if x)
    return f"{context}\n{chunk.text}" if context else chunk.text
