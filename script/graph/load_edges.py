"""
Edge loaders: create relationships between nodes in Neo4j.

Critical: INVOLVES edges carry signed weights for ANCO-HITS (Phase 3).
All loaders use MERGE for idempotent re-runs.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from neo4j import Session

from .resolve import (
    extract_firm_name,
    extract_opinion_id_from_url,
    normalize_argument,
    normalize_name,
    normalize_statute,
)
from .schema import (
    CASE,
    CHARGED_UNDER,
    CITES,
    COMPANY,
    DECIDED_BY,
    DEFENDANT_IS,
    HAS_OPINION,
    INVOLVES,
    JUDGE,
    LAW_FIRM,
    LEGAL_ARGUMENT,
    OPINION,
    REPRESENTED_BY,
    STATUTE,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


def _batched(items: list, size: int = BATCH_SIZE) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


# ---------------------------------------------------------------------------
# HAS_OPINION: Case → Opinion
# ---------------------------------------------------------------------------

def load_has_opinion_edges(session: Session, conn: sqlite3.Connection) -> int:
    """Create Case→Opinion edges from opinions.docket_id FK."""
    rows = conn.execute(
        "SELECT opinion_id, docket_id FROM opinions"
    ).fetchall()

    batch = [{"opinion_id": r[0], "docket_id": r[1]} for r in rows]

    count = 0
    for chunk in _batched(batch):
        session.run(
            f"""
            UNWIND $batch AS row
            MATCH (c:{CASE} {{docket_id: row.docket_id}})
            MATCH (o:{OPINION} {{opinion_id: row.opinion_id}})
            MERGE (c)-[:{HAS_OPINION}]->(o)
            """,
            batch=chunk,
        )
        count += len(chunk)

    logger.info(f"Loaded {count} {HAS_OPINION} edges")
    return count


# ---------------------------------------------------------------------------
# CITES: Opinion → Opinion
# ---------------------------------------------------------------------------

def load_citation_edges(session: Session, conn: sqlite3.Connection) -> int:
    """
    Create Opinion→Opinion citation edges.

    Resolves cited_opinion_url to opinion_id. Marks each edge
    as internal (target in our DB) or external.
    """
    rows = conn.execute(
        "SELECT source_opinion_id, cited_opinion_url FROM citation_edges"
    ).fetchall()

    batch = []
    skipped = 0
    for r in rows:
        target_id = extract_opinion_id_from_url(r[1])
        if target_id is None:
            skipped += 1
            continue
        batch.append({
            "source_id": r[0],
            "target_id": target_id,
        })

    count = 0
    for chunk in _batched(batch):
        session.run(
            f"""
            UNWIND $batch AS row
            MATCH (src:{OPINION} {{opinion_id: row.source_id}})
            MATCH (tgt:{OPINION} {{opinion_id: row.target_id}})
            MERGE (src)-[:{CITES}]->(tgt)
            """,
            batch=chunk,
        )
        count += len(chunk)

    if skipped:
        logger.warning(f"Skipped {skipped} citation edges with unparseable URLs")
    logger.info(f"Loaded {count} {CITES} edges")
    return count


# ---------------------------------------------------------------------------
# CHARGED_UNDER: Case → Statute
# ---------------------------------------------------------------------------

def load_charged_under_edges(session: Session, conn: sqlite3.Connection) -> int:
    """Create Case→Statute edges from cases.cause + IRAC statutes_cited."""
    batch = []

    # From cases.cause
    rows = conn.execute(
        "SELECT docket_id, cause FROM cases "
        "WHERE cause IS NOT NULL AND cause != ''"
    ).fetchall()
    for r in rows:
        norm = normalize_statute(r[1])
        batch.append({"docket_id": r[0], "citation": norm})

    # From IRAC statutes_cited
    irac_rows = conn.execute(
        """
        SELECT docket_id, extraction FROM irac_extractions
        WHERE is_valid = 1
        """
    ).fetchall()
    for r in irac_rows:
        ext = json.loads(r[1])
        for statute in ext.get("statutes_cited", []):
            if statute:
                norm = normalize_statute(statute)
                batch.append({"docket_id": r[0], "citation": norm})

    # Deduplicate (docket_id, citation) pairs
    seen = set()
    deduped = []
    for item in batch:
        key = (item["docket_id"], item["citation"])
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    batch = deduped

    count = 0
    for chunk in _batched(batch):
        session.run(
            f"""
            UNWIND $batch AS row
            MATCH (c:{CASE} {{docket_id: row.docket_id}})
            MATCH (s:{STATUTE} {{citation: row.citation}})
            MERGE (c)-[:{CHARGED_UNDER}]->(s)
            """,
            batch=chunk,
        )
        count += len(chunk)

    logger.info(f"Loaded {count} {CHARGED_UNDER} edges")
    return count


# ---------------------------------------------------------------------------
# INVOLVES: Case → LegalArgument (SIGNED — critical for ANCO-HITS)
# ---------------------------------------------------------------------------

def _compute_sign(outcome: str, side: str) -> int:
    """
    Compute the signed edge weight for ANCO-HITS.

    Sign = +1 when argument's side prevailed, -1 when it lost.

    | Outcome         | Plaintiff arg | Defendant arg |
    |-----------------|---------------|---------------|
    | PLAINTIFF_WINS  | +1 (won)      | -1 (lost)     |
    | DEFENDANT_WINS  | -1 (lost)     | +1 (won)      |
    | MIXED           | 0 (neutral)   | 0 (neutral)   |
    """
    if outcome == "MIXED":
        return 0
    if outcome == "PLAINTIFF_WINS":
        return 1 if side == "plaintiff" else -1
    if outcome == "DEFENDANT_WINS":
        return -1 if side == "plaintiff" else 1
    return 0


def load_involves_edges(session: Session, conn: sqlite3.Connection) -> int:
    """
    Create Case→LegalArgument edges with signed weights.

    This is the CRITICAL function for Phase 3 ANCO-HITS.
    """
    rows = conn.execute(
        "SELECT docket_id, extraction FROM irac_extractions WHERE is_valid = 1"
    ).fetchall()

    batch = []
    for r in rows:
        docket_id = r[0]
        ext = json.loads(r[1])
        outcome = ext.get("outcome", "MIXED")

        for arg_text in ext.get("arguments_plaintiff", []):
            if arg_text:
                _, h = normalize_argument(arg_text)
                sign = _compute_sign(outcome, "plaintiff")
                batch.append({
                    "docket_id": docket_id,
                    "text_hash": h,
                    "sign": sign,
                    "side": "plaintiff",
                })

        for arg_text in ext.get("arguments_defendant", []):
            if arg_text:
                _, h = normalize_argument(arg_text)
                sign = _compute_sign(outcome, "defendant")
                batch.append({
                    "docket_id": docket_id,
                    "text_hash": h,
                    "sign": sign,
                    "side": "defendant",
                })

    count = 0
    for chunk in _batched(batch):
        session.run(
            f"""
            UNWIND $batch AS row
            MATCH (c:{CASE} {{docket_id: row.docket_id}})
            MATCH (a:{LEGAL_ARGUMENT} {{text_hash: row.text_hash}})
            MERGE (c)-[r:{INVOLVES}]->(a)
            SET r.sign = row.sign,
                r.side = row.side
            """,
            batch=chunk,
        )
        count += len(chunk)

    # Sign distribution
    signs = {"pos": 0, "neg": 0, "zero": 0}
    for item in batch:
        if item["sign"] > 0:
            signs["pos"] += 1
        elif item["sign"] < 0:
            signs["neg"] += 1
        else:
            signs["zero"] += 1

    logger.info(
        f"Loaded {count} {INVOLVES} edges "
        f"(+1: {signs['pos']}, -1: {signs['neg']}, 0: {signs['zero']})"
    )
    return count


# ---------------------------------------------------------------------------
# DECIDED_BY: Case → Judge
# ---------------------------------------------------------------------------

def load_decided_by_edges(session: Session, conn: sqlite3.Connection) -> int:
    """
    Create Case→Judge edges.

    Primary: opinions.author_str (linked via case's opinions).
    Fallback: cases.assigned_to_str (only if case has no opinion author).
    """
    batch = []
    cases_with_author: set[int] = set()

    # Primary: opinion authors → case via docket_id
    rows = conn.execute(
        """
        SELECT DISTINCT o.docket_id, o.author_str
        FROM opinions o
        WHERE o.author_str IS NOT NULL AND o.author_str != ''
        """
    ).fetchall()
    for r in rows:
        norm = normalize_name(r[1])
        batch.append({"docket_id": r[0], "name_normalized": norm})
        cases_with_author.add(r[0])

    # Fallback: assigned_to_str for cases without opinion author
    rows = conn.execute(
        """
        SELECT docket_id, assigned_to_str FROM cases
        WHERE assigned_to_str IS NOT NULL AND assigned_to_str != ''
        """
    ).fetchall()
    for r in rows:
        if r[0] not in cases_with_author:
            norm = normalize_name(r[1])
            batch.append({"docket_id": r[0], "name_normalized": norm})

    # Deduplicate
    seen = set()
    deduped = []
    for item in batch:
        key = (item["docket_id"], item["name_normalized"])
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    batch = deduped

    count = 0
    for chunk in _batched(batch):
        session.run(
            f"""
            UNWIND $batch AS row
            MATCH (c:{CASE} {{docket_id: row.docket_id}})
            MATCH (j:{JUDGE} {{name_normalized: row.name_normalized}})
            MERGE (c)-[:{DECIDED_BY}]->(j)
            """,
            batch=chunk,
        )
        count += len(chunk)

    logger.info(f"Loaded {count} {DECIDED_BY} edges")
    return count


# ---------------------------------------------------------------------------
# DEFENDANT_IS: Case → Company
# ---------------------------------------------------------------------------

def load_defendant_edges(session: Session, conn: sqlite3.Connection) -> int:
    """Create Case→Company edges from parties with defendant type."""
    rows = conn.execute(
        """
        SELECT docket_id, name FROM parties
        WHERE party_type LIKE '%Defendant%'
          AND name IS NOT NULL AND name != ''
        """
    ).fetchall()

    batch = [
        {"docket_id": r[0], "name_normalized": normalize_name(r[1])}
        for r in rows
        if r[1] and r[1].strip()
    ]

    count = 0
    for chunk in _batched(batch):
        session.run(
            f"""
            UNWIND $batch AS row
            MATCH (c:{CASE} {{docket_id: row.docket_id}})
            MATCH (co:{COMPANY} {{name_normalized: row.name_normalized}})
            MERGE (c)-[:{DEFENDANT_IS}]->(co)
            """,
            batch=chunk,
        )
        count += len(chunk)

    logger.info(f"Loaded {count} {DEFENDANT_IS} edges")
    return count


# ---------------------------------------------------------------------------
# REPRESENTED_BY: Case → LawFirm
# ---------------------------------------------------------------------------

def load_represented_by_edges(session: Session, conn: sqlite3.Connection) -> int:
    """Create Case→LawFirm edges from attorney firm names."""
    rows = conn.execute(
        """
        SELECT a.docket_id, a.contact_raw, p.party_type
        FROM attorneys a
        LEFT JOIN parties p ON a.docket_id = p.docket_id
        WHERE a.contact_raw IS NOT NULL AND a.contact_raw != ''
        """
    ).fetchall()

    batch = []
    for r in rows:
        firm = extract_firm_name(r[1])
        if not firm:
            continue
        norm = normalize_name(firm)
        side = "defendant" if r[2] and "Defendant" in r[2] else "plaintiff"
        batch.append({
            "docket_id": r[0],
            "name_normalized": norm,
            "side": side,
        })

    # Deduplicate
    seen = set()
    deduped = []
    for item in batch:
        key = (item["docket_id"], item["name_normalized"])
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    batch = deduped

    count = 0
    for chunk in _batched(batch):
        session.run(
            f"""
            UNWIND $batch AS row
            MATCH (c:{CASE} {{docket_id: row.docket_id}})
            MATCH (f:{LAW_FIRM} {{name_normalized: row.name_normalized}})
            MERGE (c)-[r:{REPRESENTED_BY}]->(f)
            SET r.side = row.side
            """,
            batch=chunk,
        )
        count += len(chunk)

    logger.info(f"Loaded {count} {REPRESENTED_BY} edges")
    return count
