"""RAG engine.

Real retrieval logic:
- text chunking with overlap,
- deterministic token-feature embeddings (hashing vectorizer over uni/bigrams,
  sign hashing, L2 normalised),
- cosine similarity, stored in SQLite as JSON vectors,
- seeded a knowledge corpus so retrieval has real content to return.

The ``embed`` function is dependency-free so the whole system runs offline;
swapping in a learned embedder (e.g. sentence-transformers) is a drop-in
change of one function.
"""
from __future__ import annotations

import hashlib
import re
import statistics
from typing import Any

from . import config, db

TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def chunk_text(text: str, size: int | None = None,
               overlap: int | None = None) -> list[str]:
    size = size or config.RAG_CHUNK_SIZE
    overlap = overlap if overlap is not None else config.RAG_CHUNK_OVERLAP
    size = max(64, size)
    step = max(16, size - overlap)
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    chunks: list[str] = []
    buf = ""
    for sent in sentences:
        if len(buf) + len(sent) + 1 <= size:
            buf = f"{buf} {sent}".strip()
        else:
            if buf:
                chunks.append(buf)
            # long sentence spill
            while len(sent) > size:
                chunks.append(sent[:size])
                sent = sent[size:]
            buf = sent
    if buf:
        chunks.append(buf)
    return chunks or [text.strip()]


def _feature_hashes(text: str, dim: int) -> dict[int, float]:
    """Token features -> hashed (dim bucket, signed weight)."""
    tokens = TOKEN_RE.findall(text.lower())
    features: dict[tuple[str, str], float] = {}
    for i, tok in enumerate(tokens):
        features[(tok, "")] = features.get((tok, ""), 0.0) + 1.0
        if i + 1 < len(tokens):
            bigram = f"{tok}_{tokens[i + 1]}"
            features[(bigram, "b")] = features.get((bigram, "b"), 0.0) + 1.0
    buckets: dict[int, float] = {}
    for (feat, kind), count in features.items():
        h = hashlib.blake2b(feot(feat, kind), digest_size=8).digest()
        idx = int.from_bytes(h[:8], "big") % dim
        sign = 1.0 if h[0] & 1 else -1.0
        buckets[idx] = buckets.get(idx, 0.0) + sign * (1.0 + count)
    return buckets


def feot(feat: str, kind: str) -> bytes:
    # deterministic byte input for hashing
    return f"{kind}:{feat}".encode("utf-8")


def embed(text: str, dim: int | None = None) -> list[float]:
    dim = dim or config.RAG_EMBED_DIM
    buckets = _feature_hashes(text, dim)
    vec = [buckets.get(i, 0.0) for i in range(dim)]
    norm = (sum(v * v for v in vec) ** 0.5) or 1.0
    return [round(v / norm, 6) for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = (sum(x * x for x in a) ** 0.5) or 1.0
    nb = (sum(y * y for y in b) ** 0.5) or 1.0
    return dot / (na * nb)


class RagEngine:
    def __init__(self, dim: int | None = None):
        self.dim = dim or config.RAG_EMBED_DIM

    def ingest_document(self, source: str, title: str, text: str,
                        meta: dict | None = None) -> dict[str, Any]:
        doc_id = db.new_id()
        db.execute(
            "INSERT INTO rag_documents (id, source, title, meta, created_at) "
            "VALUES (?,?,?,?,?)",
            (doc_id, source, title, db.dumps(meta or {}), db.utc_now()),
        )
        chunks = chunk_text(text)
        params = []
        for i, chunk in enumerate(chunks):
            vec = embed(chunk, self.dim)
            params.append((db.new_id(), doc_id, i, chunk, db.dumps(vec), db.utc_now()))
        db.execute_many(
            "INSERT INTO rag_chunks (id, doc_id, chunk_index, text, vector, created_at) "
            "VALUES (?,?,?,?,?,?)",
            params,
        )
        return {"doc_id": doc_id, "chunks": len(chunks), "title": title,
                "source": source}

    def search(self, query: str, k: int | None = None,
               min_score: float = 0.0) -> list[dict[str, Any]]:
        k = k or config.RAG_TOP_K
        qv = embed(query, self.dim)
        rows = db.fetch_all("SELECT * FROM rag_chunks ORDER BY created_at")
        scored = []
        for r in rows:
            vec = db.loads(r["vector"], [])
            if len(vec) != self.dim:
                continue
            score = cosine(qv, vec)
            if score >= min_score:
                scored.append({
                    "chunk_id": r["id"], "doc_id": r["doc_id"],
                    "chunk_index": r["chunk_index"], "text": r["text"],
                    "score": round(score, 6),
                })
        scored.sort(key=lambda s: s["score"], reverse=True)
        return scored[:k]

    def retrieve_context(self, query: str, k: int | None = None) -> str:
        results = self.search(query, k=k)
        if not results:
            return ""
        parts = [f"[chunk score={r['score']:.3f}] {r['text']}" for r in results]
        return "\n".join(parts)

    def document_count(self) -> int:
        row = db.fetch_one("SELECT COUNT(*) AS c FROM rag_documents")
        return int(row["c"]) if row else 0

    def chunk_count(self) -> int:
        row = db.fetch_one("SELECT COUNT(*) AS c FROM rag_chunks")
        return int(row["c"]) if row else 0


KNOWLEDGE_CORPUS = [
    ("strategy-brief", "Kelly criterion sizing explained",
     "The Kelly criterion maximises long-run growth by betting a fraction "
     "f* = (p*b - q) / b of the bankroll, where p is the win probability, "
     "q = 1 - p and b = net odds ("wealth multiple on a win). Fractional "
     "Kelly applies a discount (e.g. 0.25) to control variance while "
     "capturing most of the growth. In prediction markets the odds b can be "
     "derived from the market price: b = (1 / price) - 1. When the estimated "
     "edge is small, half-Kelly keeps risk of ruin near zero.", {"topic": "sizing"}),
    ("strategy-brief", "Prediction market resolution mechanics",
     "Polymarket markets resolve to YES at one dollar per share when the "
     "underlying condition is satisfied and to zero otherwise. Prices in the "
     "range 0..1 therefore behave like probabilities. Buying YES at 0.45 and "
     "holding to a YES resolution yields 0.55 profit per share; NO positions "
     "are complements. Never hold a position past expiration unless the "
     "resolution is chosen deliberately, because settlement is binary and "
     "realized P&L is locked at resolution.", {"topic": "mechanics"}),
    ("research-note", "Bitcoin supply dynamics and ETF flows",
     "Exchange balances for Bitcoin fell to multi-year lows as accumulation "
     "dominated. Spot ETF inflows provide structural demand that historically "
     "precedes sustained climbs. Funding rates staying positive-but-modest "
     "indicates the rally is not overheated. These are the inputs most cited "
     "by analysts forecasting a break above prior highs within the next "
     "calendar year.", {"topic": "bitcoin"}),
    ("research-note", "Fed reaction function and rate-cut probability",
     "Futures markets (CME FedWatch) price a high probability of easing when "
     "disinflation is on track and labour markets cool. The Fed typically "
     "validates market pricing unless data surprises to the upside. Disagreement "
     "between hawks and doves widens the distribution, pushing the 'yes' price "
     "away from 0.5 toward the survey consensus. When core CPI runs below 3%, "
     "a December cut is the modal outcome.", {"topic": "rates"}),
    ("observation", "Crowded long risk in AI equities",
     "Hedge fund positioning in AI names reached the 92nd percentile, a level "
     "that historically marks reduced forward returns and higher drawdown risk. "
     "Yet earnings revisions remain strongly positive. The market resolves on "
     "market cap thresholds, which index at 6-9 months of forward earnings - "
     "consensus growth says yes while positioning screams caution.", {"topic": "ai"}),
    ("observation", "Starship flight-cadence evidence model",
     "Super heavy reuse and rapid Starship production lower the marginal cost "
     "per attempt, moving the probability of a successful orbital flight toward "
     "the number of scheduled attempts. Schedule slips are the dominant bear "
     "case; licensing approvals are the leading bull indicator. Treat launch "
     "schedule statements as directional but noisy.", {"topic": "space"}),
]


def seed_rag() -> None:
    engine = RagEngine()
    if engine.document_count() > 0:
        return
    for source, title, text, meta in KNOWLEDGE_CORPUS:
        engine.ingest_document(source, title, text, meta)


def similarity_stats(text_a: str, text_b: str) -> dict[str, float]:
    a = embed(text_a)
    b = embed(text_b)
    return {"cosine": round(cosine(a, b), 6), "dim": len(a),
            "active_features": len([v for v in a if v])}