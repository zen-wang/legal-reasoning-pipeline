"""
Hybrid retrieval: semantic search + graph traversal + ANCO-HITS boost.

Three retrieval channels are fused by rank.py:
1. Semantic: cosine similarity on SBERT embeddings (SQLite-cached)
2. Graph: Cypher traversals — citation neighbors, same statute, same judge, same court
3. Score: ANCO-HITS absolute score (more extreme = more informative)
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
from neo4j import Driver

from script.graph.connect import neo4j_session
from script.graph.schema import (
    CASE,
    CITES,
    DECIDED_BY,
    HAS_OPINION,
    CHARGED_UNDER,
    JUDGE,
    OPINION,
    STATUTE,
)
from script.lifting.schema import IRACExtraction
from script.lifting.store import load_extraction

from .embeddings import cosine_search, encode_query, load_embeddings_from_sqlite
from .schema import RetrievedPrecedent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Semantic retrieval channel
# ---------------------------------------------------------------------------


def _retrieve_semantic(
    query_text: str,
    embeddings: dict[int, np.ndarray],
    top_k: int = 20,
) -> dict[int, float]:
    """
    Return opinion_id -> cosine similarity score for top-K results.
    """
    if not embeddings or not query_text.strip():
        return {}

    query_vec = encode_query(query_text)
    if query_vec is None:
        return {}
    results = cosine_search(query_vec, embeddings, top_k=top_k)
    return dict(results)


# ---------------------------------------------------------------------------
# Graph retrieval channel (Cypher)
# ---------------------------------------------------------------------------


def _retrieve_graph(
    driver: Driver,
    docket_id: int,
) -> dict[int, tuple[int, str]]:
    """
    Return opinion_id -> (graph_distance, reason) from 4 Cypher traversals.

    Traversals:
    1. 1-hop citations: opinions cited by or citing this case's opinions
    2. 2-hop citations: transitive citation neighbors
    3. Same statute: cases charged under the same statutes
    4. Same court: cases in the same court
    """
    results: dict[int, tuple[int, str]] = {}

    with neo4j_session(driver) as session:
        # 1-hop citation neighbors
        one_hop = session.run(
            f"""
            MATCH (c:{CASE} {{docket_id: $did}})-[:{HAS_OPINION}]->(o:{OPINION})
                  -[:{CITES}]-(neighbor:{OPINION})
            WHERE neighbor.internal = true AND neighbor.opinion_id <> o.opinion_id
            RETURN DISTINCT neighbor.opinion_id AS oid
            """,
            did=docket_id,
        ).data()
        for row in one_hop:
            oid = row["oid"]
            if oid not in results or results[oid][0] > 1:
                results[oid] = (1, "1-hop citation")

        # 2-hop citation neighbors
        two_hop = session.run(
            f"""
            MATCH (c:{CASE} {{docket_id: $did}})-[:{HAS_OPINION}]->(o:{OPINION})
                  -[:{CITES}]-(mid:{OPINION})-[:{CITES}]-(neighbor:{OPINION})
            WHERE neighbor.internal = true
              AND neighbor.opinion_id <> o.opinion_id
              AND mid.opinion_id <> o.opinion_id
            RETURN DISTINCT neighbor.opinion_id AS oid
            """,
            did=docket_id,
        ).data()
        for row in two_hop:
            oid = row["oid"]
            if oid not in results:
                results[oid] = (2, "2-hop citation")

        # Same statute
        same_statute = session.run(
            f"""
            MATCH (c:{CASE} {{docket_id: $did}})-[:{CHARGED_UNDER}]->(s:{STATUTE})
                  <-[:{CHARGED_UNDER}]-(other:{CASE})-[:{HAS_OPINION}]->(o:{OPINION})
            WHERE other.docket_id <> $did AND o.internal = true
            RETURN DISTINCT o.opinion_id AS oid
            """,
            did=docket_id,
        ).data()
        for row in same_statute:
            oid = row["oid"]
            if oid not in results:
                results[oid] = (3, "same statute")

        # Same judge
        same_judge = session.run(
            f"""
            MATCH (c:{CASE} {{docket_id: $did}})-[:{DECIDED_BY}]->(j:{JUDGE})
                  <-[:{DECIDED_BY}]-(other:{CASE})-[:{HAS_OPINION}]->(o:{OPINION})
            WHERE other.docket_id <> $did AND o.internal = true
            RETURN DISTINCT o.opinion_id AS oid
            """,
            did=docket_id,
        ).data()
        for row in same_judge:
            oid = row["oid"]
            if oid not in results:
                results[oid] = (3, "same judge")

        # Same court
        same_court = session.run(
            f"""
            MATCH (c:{CASE} {{docket_id: $did}})
            WITH c.court_id AS court
            MATCH (other:{CASE} {{court_id: court}})-[:{HAS_OPINION}]->(o:{OPINION})
            WHERE other.docket_id <> $did AND o.internal = true
            RETURN DISTINCT o.opinion_id AS oid
            LIMIT 50
            """,
            did=docket_id,
        ).data()
        for row in same_court:
            oid = row["oid"]
            if oid not in results:
                results[oid] = (4, "same court")

    logger.info(f"Graph retrieval: {len(results)} opinions from Cypher")
    return results


# ---------------------------------------------------------------------------
# Load opinion metadata for building RetrievedPrecedent
# ---------------------------------------------------------------------------


def _load_opinion_metadata(
    conn: sqlite3.Connection,
    opinion_ids: set[int],
) -> dict[int, dict[str, Any]]:
    """Load metadata for a set of opinion IDs."""
    if not opinion_ids:
        return {}

    placeholders = ",".join("?" for _ in opinion_ids)
    rows = conn.execute(
        f"""
        SELECT o.opinion_id, o.docket_id, c.case_name, c.court_id,
               substr(o.plain_text, 1, 500) AS snippet
        FROM opinions o
        JOIN cases c ON o.docket_id = c.docket_id
        WHERE o.opinion_id IN ({placeholders})
        """,
        list(opinion_ids),
    ).fetchall()

    return {
        r[0]: {
            "opinion_id": r[0],
            "docket_id": r[1],
            "case_name": r[2] or "",
            "court_id": r[3] or "",
            "snippet": r[4] or "",
        }
        for r in rows
    }


def _load_anco_scores(
    conn: sqlite3.Connection,
) -> dict[int, float]:
    """Load ANCO-HITS case scores (docket_id -> score)."""
    table_exists = conn.execute(
        "SELECT count(*) FROM sqlite_master "
        "WHERE type='table' AND name='anco_hits_scores'"
    ).fetchone()[0]
    if not table_exists:
        return {}

    rows = conn.execute(
        "SELECT entity_id, score FROM anco_hits_scores WHERE entity_type = 'case'"
    ).fetchall()
    scores: dict[int, float] = {}
    for eid, score in rows:
        try:
            scores[int(eid)] = score
        except (ValueError, TypeError):
            pass
    return scores


# ---------------------------------------------------------------------------
# Main retrieval function
# ---------------------------------------------------------------------------


def retrieve(
    query_text: str,
    docket_id: int,
    db_path: Path,
    driver: Driver | None = None,
    top_k: int = 20,
) -> list[RetrievedPrecedent]:
    """
    Hybrid retrieval: merge semantic, graph, and ANCO-HITS signals.

    Args:
        query_text: Text to embed for semantic search (typically opinion text)
        docket_id: Query case docket ID for graph traversal
        db_path: Path to SQLite database
        driver: Neo4j driver (None = skip graph channel)
        top_k: Max results per channel

    Returns:
        List of RetrievedPrecedent with raw scores (not yet ranked).
    """
    conn = sqlite3.connect(str(db_path))

    # Channel 1: Semantic
    embeddings = load_embeddings_from_sqlite(conn)
    semantic_scores = _retrieve_semantic(query_text, embeddings, top_k=top_k)

    # Channel 2: Graph (if Neo4j available)
    graph_results: dict[int, tuple[int, str]] = {}
    if driver is not None:
        try:
            graph_results = _retrieve_graph(driver, docket_id)
        except Exception as e:
            logger.warning(f"Graph retrieval failed: {e}")

    # Collect all candidate opinion IDs
    all_opinion_ids = set(semantic_scores.keys()) | set(graph_results.keys())

    # Load metadata
    metadata = _load_opinion_metadata(conn, all_opinion_ids)
    anco_scores = _load_anco_scores(conn)

    # Build RetrievedPrecedent for each candidate
    precedents: list[RetrievedPrecedent] = []
    seen_opinions: set[int] = set()

    for oid in all_opinion_ids:
        if oid in seen_opinions:
            continue
        seen_opinions.add(oid)

        meta = metadata.get(oid)
        if not meta:
            continue

        did = meta["docket_id"]

        # Skip self
        if did == docket_id:
            continue

        # Load IRAC extraction if available
        irac = load_extraction(conn, oid)

        graph_dist, graph_reason = graph_results.get(oid, (-1, ""))

        precedents.append(RetrievedPrecedent(
            opinion_id=oid,
            docket_id=did,
            case_name=meta["case_name"],
            court_id=meta["court_id"],
            semantic_score=semantic_scores.get(oid, 0.0),
            graph_distance=graph_dist,
            graph_reason=graph_reason,
            anco_hits_score=anco_scores.get(did, 0.0),
            irac_extraction=irac,
            snippet=meta["snippet"],
        ))

    conn.close()

    logger.info(
        f"Retrieved {len(precedents)} candidates "
        f"(semantic={len(semantic_scores)}, graph={len(graph_results)})"
    )
    return precedents
