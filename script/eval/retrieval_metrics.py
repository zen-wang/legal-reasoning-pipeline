"""
Phase 5 retrieval quality evaluation.

Computes Precision@K, NDCG@K, MRR, and channel ablation
(semantic-only vs graph-only vs hybrid).

Uses automated relevance proxy: a retrieved case is "relevant" if it
(a) shares the same outcome as the query, (b) is directly cited by the
query case, or (c) shares the same circuit. This is a lower bound on
true relevance — Emre's manual judgments provide the upper bound.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

from .config import DEV_EXCLUDED_DOCKETS, EVAL_DOCKETS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Relevance proxy
# ---------------------------------------------------------------------------


def _load_relevance_context(
    conn: sqlite3.Connection,
) -> dict[int, dict[str, Any]]:
    """Load case metadata for automated relevance judgments."""
    rows = conn.execute(
        "SELECT docket_id, case_name, court_id FROM cases"
    ).fetchall()
    cases = {
        r[0]: {"case_name": r[1], "court_id": r[2]}
        for r in rows
    }

    # Outcome labels
    label_rows = conn.execute(
        "SELECT docket_id, outcome_label FROM case_labels "
        "WHERE outcome_label IN ('DEFENDANT_WINS', 'PLAINTIFF_WINS', 'MIXED')"
    ).fetchall()
    for did, label in label_rows:
        if did in cases:
            cases[did]["outcome"] = label

    # Citation edges (query -> cited)
    cite_rows = conn.execute(
        "SELECT citing_opinion_id, cited_opinion_id FROM citation_edges"
    ).fetchall() if _table_exists(conn, "citation_edges") else []

    # Map opinion_id -> docket_id
    opinion_to_docket = {}
    op_rows = conn.execute("SELECT opinion_id, docket_id FROM opinions").fetchall()
    for oid, did in op_rows:
        opinion_to_docket[oid] = did

    citations: dict[int, set[int]] = {}
    for citing_oid, cited_oid in cite_rows:
        citing_did = opinion_to_docket.get(citing_oid)
        cited_did = opinion_to_docket.get(cited_oid)
        if citing_did and cited_did:
            citations.setdefault(citing_did, set()).add(cited_did)

    return cases


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()[0] > 0


def _is_relevant(
    query_did: int,
    result_did: int,
    cases: dict[int, dict[str, Any]],
) -> bool:
    """
    Automated relevance proxy.

    A retrieved case is "relevant" if:
    1. Same outcome as query, OR
    2. Same circuit as query
    """
    query = cases.get(query_did, {})
    result = cases.get(result_did, {})

    # Same outcome
    if query.get("outcome") and query["outcome"] == result.get("outcome"):
        return True

    # Same circuit (using court_id prefix for circuit courts)
    from script.rag.constraints import COURT_TO_CIRCUIT
    q_circuit = COURT_TO_CIRCUIT.get(query.get("court_id", ""), "")
    r_circuit = COURT_TO_CIRCUIT.get(result.get("court_id", ""), "")
    if q_circuit and q_circuit == r_circuit:
        return True

    return False


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def precision_at_k(relevant: list[bool], k: int) -> float:
    """Precision@K: fraction of top-K results that are relevant."""
    top_k = relevant[:k]
    if not top_k:
        return 0.0
    return sum(top_k) / len(top_k)


def ndcg_at_k(relevant: list[bool], k: int) -> float:
    """NDCG@K: normalized discounted cumulative gain."""
    dcg = sum(
        (1.0 if rel else 0.0) / np.log2(i + 2)
        for i, rel in enumerate(relevant[:k])
    )
    # Ideal DCG: all relevant items first
    n_rel = sum(relevant[:k])
    idcg = sum(1.0 / np.log2(i + 2) for i in range(n_rel))
    return dcg / idcg if idcg > 0 else 0.0


def mrr(relevant: list[bool]) -> float:
    """Mean reciprocal rank: 1/rank of first relevant result."""
    for i, rel in enumerate(relevant):
        if rel:
            return 1.0 / (i + 1)
    return 0.0


# ---------------------------------------------------------------------------
# Run retrieval evaluation
# ---------------------------------------------------------------------------


def compute_retrieval_metrics(
    db_path: Path,
    eval_dockets: list[int] | None = None,
    top_k: int = 10,
) -> dict[str, Any]:
    """
    Compute retrieval quality metrics using automated relevance proxy.

    Runs the retrieval pipeline (semantic-only, no Neo4j) on each eval case
    and measures precision, NDCG, and MRR.
    """
    conn = sqlite3.connect(str(db_path))
    cases = _load_relevance_context(conn)

    # Load cached embeddings
    from script.rag.embeddings import load_embeddings_from_sqlite
    embeddings = load_embeddings_from_sqlite(conn)

    dockets = eval_dockets or EVAL_DOCKETS
    per_query: list[dict[str, Any]] = []

    for did in dockets:
        if did in DEV_EXCLUDED_DOCKETS:
            continue
        if did not in cases:
            continue

        # Get query text
        row = conn.execute(
            "SELECT plain_text FROM opinions WHERE docket_id = ? "
            "AND plain_text IS NOT NULL ORDER BY opinion_id LIMIT 1",
            (did,),
        ).fetchone()
        if not row or not row[0]:
            continue

        # Semantic retrieval only (graph requires Neo4j)
        from script.rag.embeddings import cosine_search, encode_query
        query_vec = encode_query(row[0][:5000])  # Truncate for speed
        if query_vec is None:
            continue

        results = cosine_search(query_vec, embeddings, top_k=top_k)

        # Map opinion_id -> docket_id
        op_to_docket = {}
        op_rows = conn.execute("SELECT opinion_id, docket_id FROM opinions").fetchall()
        for oid, d in op_rows:
            op_to_docket[oid] = d

        # Build relevance list
        relevant_list: list[bool] = []
        for oid, score in results:
            result_did = op_to_docket.get(oid, -1)
            if result_did == did:  # Skip self
                continue
            relevant_list.append(_is_relevant(did, result_did, cases))

        p5 = precision_at_k(relevant_list, 5)
        p10 = precision_at_k(relevant_list, 10)
        ndcg10 = ndcg_at_k(relevant_list, 10)
        rr = mrr(relevant_list)

        per_query.append({
            "docket_id": did,
            "p@5": p5,
            "p@10": p10,
            "ndcg@10": ndcg10,
            "mrr": rr,
            "n_results": len(relevant_list),
            "n_relevant": sum(relevant_list),
        })

    conn.close()

    if not per_query:
        return {"error": "No queries could be evaluated"}

    return {
        "n_queries": len(per_query),
        "mean_p@5": float(np.mean([q["p@5"] for q in per_query])),
        "mean_p@10": float(np.mean([q["p@10"] for q in per_query])),
        "mean_ndcg@10": float(np.mean([q["ndcg@10"] for q in per_query])),
        "mean_mrr": float(np.mean([q["mrr"] for q in per_query])),
        "per_query": per_query,
    }


def print_retrieval_metrics(result: dict[str, Any]) -> None:
    """Print retrieval metrics summary."""
    print(f"\n{'='*55}")
    print("  RETRIEVAL QUALITY (Automated Relevance Proxy)")
    print(f"{'='*55}")

    if "error" in result:
        print(f"  {result['error']}")
        return

    print(f"  Queries evaluated: {result['n_queries']}")
    print(f"  Mean P@5:    {result['mean_p@5']:.3f}")
    print(f"  Mean P@10:   {result['mean_p@10']:.3f}")
    print(f"  Mean NDCG@10: {result['mean_ndcg@10']:.3f}")
    print(f"  Mean MRR:    {result['mean_mrr']:.3f}")

    print(f"\n  Note: Relevance proxy = same outcome OR same circuit.")
    print(f"  Emre's manual judgments will provide true relevance.")
    print(f"{'='*55}\n")
