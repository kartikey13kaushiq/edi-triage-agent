"""Runbook chunking, embedding and retrieval (in-memory or pgvector).

Chunking follows the runbook structure: one chunk per ``##`` section, prefixed with the
document title so a chunk is self-describing when it is shown to the model on its own.
Long sections are split into overlapping windows.

The default embedder is a deterministic feature-hashing embedder (unigrams + bigrams),
which keeps tests and evals offline and reproducible. Anything that implements
``Embedder`` - e.g. a hosted dense embedding model - can be swapped in; the pgvector
schema only depends on ``dim``.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

RUNBOOK_DIR = Path(__file__).parent / "data" / "runbooks"

_TOKEN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_STOP = frozenset(
    "a an and are as at be by for from if in into is it its of on or our the their then this to "
    "was we with not no do does any every only".split()
)


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    source: str
    title: str
    section: str
    text: str


@dataclass(frozen=True)
class ScoredChunk:
    chunk: Chunk
    score: float

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk.chunk_id,
            "source": self.chunk.source,
            "section": self.chunk.section,
            "text": self.chunk.text,
            "score": round(self.score, 4),
        }


def chunk_markdown(source: str, text: str, max_chars: int = 1200, overlap: int = 200) -> list[Chunk]:
    title_match = re.search(r"^# (.+)$", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else source
    sections = re.split(r"^## ", text, flags=re.MULTILINE)[1:]
    chunks: list[Chunk] = []
    for section in sections:
        heading, _, body = section.partition("\n")
        body = body.strip()
        windows = (
            [body]
            if len(body) <= max_chars
            else [body[i : i + max_chars] for i in range(0, len(body), max_chars - overlap)]
        )
        for n, window in enumerate(windows):
            slug = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
            chunk_id = f"{Path(source).stem}#{slug}" + (f"-{n}" if len(windows) > 1 else "")
            chunks.append(
                Chunk(chunk_id, source, title, heading.strip(), f"{title} - {heading.strip()}\n{window}")
            )
    return chunks


def load_runbook_chunks(directory: Path = RUNBOOK_DIR) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(directory.glob("*.md")):
        chunks.extend(chunk_markdown(path.name, path.read_text()))
    return chunks


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


class Embedder(Protocol):
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Signed feature hashing over unigrams and bigrams, L2-normalised."""

    def __init__(self, dim: int = 512):
        self.dim = dim

    def _index(self, feature: str) -> tuple[int, float]:
        digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        return value % self.dim, 1.0 if (value >> 63) & 1 else -1.0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * self.dim
            tokens = tokenize(text)
            features = tokens + [f"{a} {b}" for a, b in zip(tokens, tokens[1:], strict=False)]
            for feature in features:
                idx, sign = self._index(feature)
                vec[idx] += sign
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vectors.append([v / norm for v in vec])
        return vectors


class Retriever(Protocol):
    def search(self, query: str, k: int = 4) -> list[ScoredChunk]: ...


class InMemoryRetriever:
    def __init__(self, chunks: Sequence[Chunk], embedder: Embedder | None = None):
        self.embedder = embedder or HashingEmbedder()
        self.chunks = list(chunks)
        self.vectors = self.embedder.embed([c.text for c in self.chunks])

    def search(self, query: str, k: int = 4) -> list[ScoredChunk]:
        (q,) = self.embedder.embed([query])
        scored = [
            ScoredChunk(chunk, sum(a * b for a, b in zip(q, vec, strict=True)))
            for chunk, vec in zip(self.chunks, self.vectors, strict=True)
        ]
        return sorted(scored, key=lambda s: s.score, reverse=True)[:k]


class PgVectorRetriever:
    """Cosine search over the ``runbook_chunks`` table (see ``sql/schema.sql``)."""

    def __init__(self, dsn: str, embedder: Embedder | None = None):
        import psycopg

        self.embedder = embedder or HashingEmbedder()
        self.conn = psycopg.connect(dsn, autocommit=True)

    @staticmethod
    def _literal(vec: Sequence[float]) -> str:
        return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"

    def index(self, chunks: Sequence[Chunk]) -> int:
        vectors = self.embedder.embed([c.text for c in chunks])
        with self.conn.cursor() as cur:
            for chunk, vec in zip(chunks, vectors, strict=True):
                cur.execute(
                    """
                    INSERT INTO runbook_chunks (chunk_id, source, section, content, embedding)
                    VALUES (%s, %s, %s, %s, %s::vector)
                    ON CONFLICT (chunk_id) DO UPDATE
                      SET content = EXCLUDED.content, embedding = EXCLUDED.embedding
                    """,
                    (chunk.chunk_id, chunk.source, chunk.section, chunk.text, self._literal(vec)),
                )
        return len(chunks)

    def search(self, query: str, k: int = 4) -> list[ScoredChunk]:
        (q,) = self.embedder.embed([query])
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT chunk_id, source, section, content, 1 - (embedding <=> %s::vector) AS score
                FROM runbook_chunks
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (self._literal(q), self._literal(q), k),
            )
            rows = cur.fetchall()
        return [
            ScoredChunk(Chunk(cid, src, src, section, content), float(score))
            for cid, src, section, content, score in rows
        ]


def default_retriever() -> Retriever:
    if os.environ.get("RETRIEVER") == "pgvector":
        return PgVectorRetriever(os.environ["DATABASE_URL"])
    return InMemoryRetriever(load_runbook_chunks())
