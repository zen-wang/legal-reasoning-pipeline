"""
One-time batch embedding of opinion texts using sentence-transformers.

Encodes the analysis portion (header-stripped) of each opinion with
all-mpnet-base-v2, stores 768-d vectors in SQLite, and provides
numpy cosine similarity search (<1ms over 149 vectors).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

MODEL_NAME = "all-mpnet-base-v2"
MIN_TEXT_CHARS = 1000


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS opinion_embeddings (
    opinion_id  INTEGER PRIMARY KEY,
    model_name  TEXT NOT NULL,
    embedding   BLOB NOT NULL,
    text_chars  INTEGER NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);
"""


# ---------------------------------------------------------------------------
# Encode opinions
# ---------------------------------------------------------------------------


def encode_opinions(db_path: Path) -> dict[int, np.ndarray]:
    """
    Encode all opinions with text > MIN_TEXT_CHARS using SBERT.

    Uses the same preprocessing as Phase 1 (split_sections + get_analysis_text)
    to strip court header boilerplate before embedding.

    Returns dict of opinion_id -> 768-d embedding vector.
    """
    from sentence_transformers import SentenceTransformer

    from script.lifting.preprocess import get_analysis_text, split_sections

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT opinion_id, plain_text FROM opinions "
        "WHERE plain_text IS NOT NULL AND length(plain_text) > ?",
        (MIN_TEXT_CHARS,),
    ).fetchall()
    conn.close()

    logger.info(f"Encoding {len(rows)} opinions with {MODEL_NAME}...")

    # Preprocess: strip headers
    opinion_ids: list[int] = []
    texts: list[str] = []
    for oid, plain_text in rows:
        sections = split_sections(plain_text)
        analysis = get_analysis_text(sections)
        if len(analysis.strip()) > 100:
            opinion_ids.append(oid)
            texts.append(analysis)

    # Batch encode
    model = SentenceTransformer(MODEL_NAME)
    vectors = model.encode(texts, show_progress_bar=True, batch_size=32)

    embeddings: dict[int, np.ndarray] = {}
    for oid, vec in zip(opinion_ids, vectors):
        embeddings[oid] = np.array(vec, dtype=np.float32)

    logger.info(
        f"Encoded {len(embeddings)} opinions -> {vectors.shape[1]}d vectors"
    )
    return embeddings


# ---------------------------------------------------------------------------
# SQLite persistence
# ---------------------------------------------------------------------------


def write_embeddings_to_sqlite(
    conn: sqlite3.Connection,
    embeddings: dict[int, np.ndarray],
) -> None:
    """Store embeddings in opinion_embeddings table (idempotent)."""
    conn.execute(CREATE_TABLE_SQL)
    conn.execute("DELETE FROM opinion_embeddings")

    rows = [
        (oid, MODEL_NAME, vec.tobytes(), vec.shape[0])
        for oid, vec in embeddings.items()
    ]
    conn.executemany(
        "INSERT INTO opinion_embeddings "
        "(opinion_id, model_name, embedding, text_chars) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    logger.info(f"Wrote {len(rows)} embeddings to SQLite")


def load_embeddings_from_sqlite(
    conn: sqlite3.Connection,
) -> dict[int, np.ndarray]:
    """Load cached embeddings from SQLite."""
    # Check table exists
    table_exists = conn.execute(
        "SELECT count(*) FROM sqlite_master "
        "WHERE type='table' AND name='opinion_embeddings'"
    ).fetchone()[0]

    if not table_exists:
        return {}

    rows = conn.execute(
        "SELECT opinion_id, embedding FROM opinion_embeddings"
    ).fetchall()

    embeddings: dict[int, np.ndarray] = {}
    for oid, blob in rows:
        embeddings[oid] = np.frombuffer(blob, dtype=np.float32).copy()

    logger.info(f"Loaded {len(embeddings)} cached embeddings from SQLite")
    return embeddings


# ---------------------------------------------------------------------------
# Cosine similarity search
# ---------------------------------------------------------------------------


def cosine_search(
    query_vec: np.ndarray,
    embeddings: dict[int, np.ndarray],
    top_k: int = 20,
) -> list[tuple[int, float]]:
    """
    Find top-K most similar opinions by cosine similarity.

    Args:
        query_vec: (D,) query embedding
        embeddings: opinion_id -> (D,) embedding vectors
        top_k: number of results to return

    Returns:
        List of (opinion_id, cosine_similarity) sorted descending.
    """
    if not embeddings:
        return []

    ids = list(embeddings.keys())
    matrix = np.stack([embeddings[oid] for oid in ids])  # (N, D)

    # Normalize
    query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-10)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-10
    matrix_norm = matrix / norms

    # Cosine similarity
    scores = matrix_norm @ query_norm  # (N,)

    # Top-K
    top_indices = np.argsort(scores)[::-1][:top_k]
    return [(ids[i], float(scores[i])) for i in top_indices]


def encode_query(text: str) -> np.ndarray:
    """Encode a single query text using SBERT. Loads model on first call."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL_NAME)
    vec = model.encode(text)
    return np.array(vec, dtype=np.float32)
