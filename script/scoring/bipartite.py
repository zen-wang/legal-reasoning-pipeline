"""
Extract the signed bipartite case-argument graph for ANCO-HITS.

Two data sources: Neo4j (primary) or SQLite (fallback).
Returns a BipartiteGraph with numpy sign matrix and index mappings.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass

import numpy as np
from neo4j import Driver

from script.graph.connect import neo4j_session
from script.graph.resolve import normalize_argument

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sign computation (duplicated from load_edges._compute_sign to avoid
# depending on a private function in Phase 2 code)
# ---------------------------------------------------------------------------

def compute_sign(outcome: str, side: str) -> int:
    """
    Compute signed edge weight: +1 when argument's side prevailed, -1 when lost.

    | Outcome         | Plaintiff arg | Defendant arg |
    |-----------------|---------------|---------------|
    | PLAINTIFF_WINS  | +1            | -1            |
    | DEFENDANT_WINS  | -1            | +1            |
    | MIXED           | 0             | 0             |
    """
    if outcome == "MIXED":
        return 0
    if outcome == "PLAINTIFF_WINS":
        return 1 if side == "plaintiff" else -1
    if outcome == "DEFENDANT_WINS":
        return -1 if side == "plaintiff" else 1
    return 0


# ---------------------------------------------------------------------------
# BipartiteGraph
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BipartiteGraph:
    """Signed bipartite adjacency for ANCO-HITS."""

    case_ids: list[int]               # length C, docket_id values
    argument_hashes: list[str]        # length A, text_hash values
    case_outcomes: np.ndarray         # shape (C,), values in {-1, 0, +1}
    sign_matrix: np.ndarray           # shape (C, A), values in {-1, 0, +1}
    case_index: dict[int, int]        # docket_id -> row index
    argument_index: dict[str, int]    # text_hash -> column index


# ---------------------------------------------------------------------------
# Load from Neo4j
# ---------------------------------------------------------------------------

def load_bipartite_from_neo4j(driver: Driver) -> BipartiteGraph:
    """
    Load signed bipartite graph from Neo4j INVOLVES edges.

    Uses edge sign + side to infer case outcome for seeding.
    """
    with neo4j_session(driver) as session:
        result = session.run(
            "MATCH (c:Case)-[r:INVOLVES]->(a:LegalArgument) "
            "RETURN c.docket_id AS docket_id, a.text_hash AS text_hash, "
            "       r.sign AS sign, r.side AS side"
        )
        edges = [dict(record) for record in result]

    if not edges:
        raise ValueError("No INVOLVES edges found in Neo4j")

    return _build_bipartite(edges)


# ---------------------------------------------------------------------------
# Load from SQLite
# ---------------------------------------------------------------------------

def load_bipartite_from_sqlite(conn: sqlite3.Connection) -> BipartiteGraph:
    """
    Load signed bipartite graph from irac_extractions in SQLite.

    Recomputes signs from extraction outcome + argument side.
    """
    rows = conn.execute(
        "SELECT docket_id, extraction FROM irac_extractions WHERE is_valid = 1"
    ).fetchall()

    if not rows:
        raise ValueError("No valid IRAC extractions found in SQLite")

    edges = []
    for docket_id, ext_json in rows:
        ext = json.loads(ext_json)
        outcome = ext.get("outcome", "MIXED")

        for arg_text in ext.get("arguments_plaintiff", []):
            if arg_text:
                _, h = normalize_argument(arg_text)
                sign = compute_sign(outcome, "plaintiff")
                edges.append({
                    "docket_id": docket_id,
                    "text_hash": h,
                    "sign": sign,
                    "side": "plaintiff",
                })

        for arg_text in ext.get("arguments_defendant", []):
            if arg_text:
                _, h = normalize_argument(arg_text)
                sign = compute_sign(outcome, "defendant")
                edges.append({
                    "docket_id": docket_id,
                    "text_hash": h,
                    "sign": sign,
                    "side": "defendant",
                })

    if not edges:
        raise ValueError("No argument edges extracted from IRAC data")

    return _build_bipartite(edges)


# ---------------------------------------------------------------------------
# Build matrix from edges
# ---------------------------------------------------------------------------

def _infer_outcome(case_edges: list[dict]) -> int:
    """Infer case outcome from edge signs + sides."""
    for e in case_edges:
        if e["sign"] == 0:
            continue
        if e["side"] == "plaintiff":
            return 1 if e["sign"] == 1 else -1
        if e["side"] == "defendant":
            return 1 if e["sign"] == -1 else -1
    return 0  # all zero-signed → MIXED


def _build_bipartite(edges: list[dict]) -> BipartiteGraph:
    """Build BipartiteGraph from a list of edge dicts."""
    # Collect unique cases and arguments
    case_set: dict[int, int] = {}
    arg_set: dict[str, int] = {}

    for e in edges:
        did = e["docket_id"]
        th = e["text_hash"]
        if did not in case_set:
            case_set[did] = len(case_set)
        if th not in arg_set:
            arg_set[th] = len(arg_set)

    C = len(case_set)
    A = len(arg_set)
    case_ids = [0] * C
    for did, idx in case_set.items():
        case_ids[idx] = did
    argument_hashes = [""] * A
    for th, idx in arg_set.items():
        argument_hashes[idx] = th

    # Build sign matrix
    sign_matrix = np.zeros((C, A), dtype=np.float64)
    for e in edges:
        i = case_set[e["docket_id"]]
        j = arg_set[e["text_hash"]]
        sign_matrix[i, j] = e["sign"]

    # Infer case outcomes for seeding
    case_edges_map: dict[int, list[dict]] = {}
    for e in edges:
        did = e["docket_id"]
        case_edges_map.setdefault(did, []).append(e)

    case_outcomes = np.zeros(C, dtype=np.float64)
    for did, idx in case_set.items():
        case_outcomes[idx] = _infer_outcome(case_edges_map[did])

    bg = BipartiteGraph(
        case_ids=case_ids,
        argument_hashes=argument_hashes,
        case_outcomes=case_outcomes,
        sign_matrix=sign_matrix,
        case_index=case_set,
        argument_index=arg_set,
    )

    logger.info(
        f"Bipartite graph: {C} cases × {A} arguments, "
        f"{int(np.count_nonzero(sign_matrix))} non-zero edges"
    )
    return bg
