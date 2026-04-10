"""
SQLite storage for IRAC extractions.

Stores LLM extraction results in the same database as the scraped cases,
in a new `irac_extractions` table. Each row holds the full IRACExtraction
as a JSON blob plus metadata (model used, validation status, errors).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .schema import IRACExtraction


# ---------------------------------------------------------------------------
# Table setup
# ---------------------------------------------------------------------------

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS irac_extractions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    docket_id    INTEGER NOT NULL,
    opinion_id   INTEGER NOT NULL,
    extraction   TEXT NOT NULL,
    llm_model    TEXT,
    llm_raw      TEXT,
    is_valid     INTEGER,
    errors       TEXT,
    created_at   TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (docket_id) REFERENCES cases(docket_id)
);
"""

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_irac_docket ON irac_extractions(docket_id);",
    "CREATE INDEX IF NOT EXISTS idx_irac_opinion ON irac_extractions(opinion_id);",
    "CREATE INDEX IF NOT EXISTS idx_irac_valid ON irac_extractions(is_valid);",
]


def init_irac_table(conn: sqlite3.Connection) -> None:
    """Create the irac_extractions table if it doesn't exist."""
    conn.execute(CREATE_TABLE_SQL)
    for sql in INDEX_SQL:
        conn.execute(sql)
    conn.commit()


# ---------------------------------------------------------------------------
# Save / Load
# ---------------------------------------------------------------------------

def save_extraction(
    conn: sqlite3.Connection,
    extraction: IRACExtraction,
    *,
    llm_model: str = "mock",
    llm_raw: str = "",
    is_valid: bool = True,
    errors: list[str] | None = None,
) -> int:
    """
    Save a validated IRAC extraction to the database.

    Returns the row ID of the inserted record.
    """
    cursor = conn.execute(
        """
        INSERT INTO irac_extractions
            (docket_id, opinion_id, extraction, llm_model, llm_raw, is_valid, errors)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            extraction.case_id,
            extraction.opinion_id,
            extraction.model_dump_json(),
            llm_model,
            llm_raw,
            1 if is_valid else 0,
            json.dumps(errors) if errors else None,
        ),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore[return-value]


def load_extraction(conn: sqlite3.Connection, opinion_id: int) -> IRACExtraction | None:
    """Load the most recent valid extraction for an opinion."""
    row = conn.execute(
        """
        SELECT extraction FROM irac_extractions
        WHERE opinion_id = ? AND is_valid = 1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (opinion_id,),
    ).fetchone()

    if row is None:
        return None
    return IRACExtraction.model_validate_json(row[0])


def load_all_extractions(conn: sqlite3.Connection) -> list[IRACExtraction]:
    """Load all valid extractions."""
    rows = conn.execute(
        "SELECT extraction FROM irac_extractions WHERE is_valid = 1"
    ).fetchall()
    return [IRACExtraction.model_validate_json(r[0]) for r in rows]


def get_extraction_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Return counts of extractions by validity."""
    rows = conn.execute(
        """
        SELECT is_valid, COUNT(*) FROM irac_extractions
        GROUP BY is_valid
        """
    ).fetchall()
    return {("valid" if v else "invalid"): c for v, c in rows}
