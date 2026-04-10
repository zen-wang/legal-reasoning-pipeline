"""
Persist ANCO-HITS scores to Neo4j and SQLite.

Writes anco_hits_score property on LegalArgument and Case nodes in Neo4j,
and creates anco_hits_scores table in SQLite.
"""

from __future__ import annotations

import logging
import sqlite3

import numpy as np
from neo4j import Driver

from script.graph.connect import neo4j_session
from script.graph.schema import CASE, LEGAL_ARGUMENT

from .bipartite import BipartiteGraph

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


def _batched(items: list, size: int = BATCH_SIZE) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


# ---------------------------------------------------------------------------
# Neo4j
# ---------------------------------------------------------------------------

def write_scores_to_neo4j(
    driver: Driver,
    bipartite: BipartiteGraph,
    argument_scores: np.ndarray,
    case_scores: np.ndarray,
) -> None:
    """Write anco_hits_score property to LegalArgument and Case nodes."""
    with neo4j_session(driver) as session:
        # Argument scores
        arg_batch = [
            {"text_hash": bipartite.argument_hashes[j], "score": float(argument_scores[j])}
            for j in range(len(argument_scores))
        ]
        for chunk in _batched(arg_batch):
            session.run(
                f"""
                UNWIND $batch AS row
                MATCH (a:{LEGAL_ARGUMENT} {{text_hash: row.text_hash}})
                SET a.anco_hits_score = row.score
                """,
                batch=chunk,
            )

        # Case scores
        case_batch = [
            {"docket_id": bipartite.case_ids[i], "score": float(case_scores[i])}
            for i in range(len(case_scores))
        ]
        for chunk in _batched(case_batch):
            session.run(
                f"""
                UNWIND $batch AS row
                MATCH (c:{CASE} {{docket_id: row.docket_id}})
                SET c.anco_hits_score = row.score
                """,
                batch=chunk,
            )

    logger.info(
        f"Wrote scores to Neo4j: {len(arg_batch)} arguments, {len(case_batch)} cases"
    )


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS anco_hits_scores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    score       REAL NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);
"""

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_ahs_type ON anco_hits_scores(entity_type);",
    "CREATE INDEX IF NOT EXISTS idx_ahs_entity ON anco_hits_scores(entity_id);",
]


def write_scores_to_sqlite(
    conn: sqlite3.Connection,
    bipartite: BipartiteGraph,
    argument_scores: np.ndarray,
    case_scores: np.ndarray,
) -> None:
    """
    Write scores to anco_hits_scores table in SQLite.

    Clears existing scores before writing (idempotent re-run).
    """
    conn.execute(CREATE_TABLE_SQL)
    for sql in INDEX_SQL:
        conn.execute(sql)

    # Clear previous scores
    conn.execute("DELETE FROM anco_hits_scores")

    # Write argument scores
    arg_rows = [
        ("argument", bipartite.argument_hashes[j], float(argument_scores[j]))
        for j in range(len(argument_scores))
    ]
    conn.executemany(
        "INSERT INTO anco_hits_scores (entity_type, entity_id, score) VALUES (?, ?, ?)",
        arg_rows,
    )

    # Write case scores
    case_rows = [
        ("case", str(bipartite.case_ids[i]), float(case_scores[i]))
        for i in range(len(case_scores))
    ]
    conn.executemany(
        "INSERT INTO anco_hits_scores (entity_type, entity_id, score) VALUES (?, ?, ?)",
        case_rows,
    )

    conn.commit()
    logger.info(
        f"Wrote scores to SQLite: {len(arg_rows)} arguments, {len(case_rows)} cases"
    )
