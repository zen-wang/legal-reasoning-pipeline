"""
Multi-signal re-ranking via weighted fusion.

Combines semantic similarity, graph proximity, ANCO-HITS score, and
IRAC availability into a single final_score per precedent.

Weights are tunable constants — adjust during Phase 7 evaluation.
"""

from __future__ import annotations

from .schema import RetrievedPrecedent

# ---------------------------------------------------------------------------
# Tunable weights (Phase 7 evaluation)
# ---------------------------------------------------------------------------

W_SEMANTIC = 0.4
W_GRAPH = 0.3
W_ANCO = 0.2
W_IRAC = 0.1

# Graph proximity scores by distance/reason
GRAPH_PROXIMITY: dict[int, float] = {
    1: 1.0,   # 1-hop citation
    2: 0.5,   # 2-hop citation
    3: 0.3,   # same statute or same judge
    4: 0.1,   # same court only
}


# ---------------------------------------------------------------------------
# Re-ranking
# ---------------------------------------------------------------------------


def rank_precedents(
    precedents: list[RetrievedPrecedent],
    top_k: int = 10,
) -> list[RetrievedPrecedent]:
    """
    Re-rank precedents using weighted fusion of all signals.

    Formula:
        final = W_SEMANTIC * semantic_score
              + W_GRAPH * graph_proximity
              + W_ANCO * abs(anco_hits_score)
              + W_IRAC * has_irac

    Deduplicates by opinion_id, returns top-K sorted by final_score.
    """
    # Dedup by opinion_id (keep highest semantic score)
    seen: dict[int, RetrievedPrecedent] = {}
    for p in precedents:
        if p.opinion_id not in seen or p.semantic_score > seen[p.opinion_id].semantic_score:
            seen[p.opinion_id] = p
    unique = list(seen.values())

    # Compute final scores
    for p in unique:
        graph_prox = GRAPH_PROXIMITY.get(p.graph_distance, 0.0)
        has_irac = 1.0 if p.irac_extraction is not None else 0.0

        p.final_score = (
            W_SEMANTIC * max(p.semantic_score, 0.0)
            + W_GRAPH * graph_prox
            + W_ANCO * abs(p.anco_hits_score)
            + W_IRAC * has_irac
        )

    # Sort descending by final_score
    unique.sort(key=lambda p: p.final_score, reverse=True)

    return unique[:top_k]
