"""
Node loaders: read from SQLite, write to Neo4j using MERGE + UNWIND.

Each loader is idempotent — safe to re-run on the same data.
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
    resolve_internal_opinion_ids,
)
from .schema import (
    CASE,
    COMPANY,
    JUDGE,
    LAW_FIRM,
    LEGAL_ARGUMENT,
    OPINION,
    STATUTE,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


def _batched(items: list, size: int = BATCH_SIZE) -> list[list]:
    """Split a list into batches."""
    return [items[i : i + size] for i in range(0, len(items), size)]


# ---------------------------------------------------------------------------
# Case nodes
# ---------------------------------------------------------------------------

def load_case_nodes(session: Session, conn: sqlite3.Connection) -> int:
    """Load all cases as Case nodes with metadata + outcome + has_irac flag."""
    conn.row_factory = sqlite3.Row

    # Get IRAC opinion_ids for has_irac flag
    irac_dockets = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT docket_id FROM irac_extractions WHERE is_valid = 1"
        ).fetchall()
    }

    rows = conn.execute(
        """
        SELECT c.docket_id, c.case_name, c.court_id, c.cause,
               c.nature_of_suit, c.date_filed, c.date_terminated,
               c.assigned_to_str, c.idb_class_action, c.idb_pro_se,
               cl.outcome_label, cl.procedural_stage
        FROM cases c
        LEFT JOIN case_labels cl ON c.docket_id = cl.docket_id
        """
    ).fetchall()

    batch = [
        {
            "docket_id": r["docket_id"],
            "case_name": r["case_name"],
            "court_id": r["court_id"],
            "cause": r["cause"],
            "nature_of_suit": r["nature_of_suit"],
            "date_filed": r["date_filed"],
            "date_terminated": r["date_terminated"],
            "outcome_label": r["outcome_label"],
            "procedural_stage": r["procedural_stage"],
            "has_irac": r["docket_id"] in irac_dockets,
            "idb_class_action": r["idb_class_action"],
            "idb_pro_se": r["idb_pro_se"],
        }
        for r in rows
    ]

    count = 0
    for chunk in _batched(batch):
        session.run(
            f"""
            UNWIND $batch AS row
            MERGE (c:{CASE} {{docket_id: row.docket_id}})
            SET c.case_name = row.case_name,
                c.court_id = row.court_id,
                c.cause = row.cause,
                c.nature_of_suit = row.nature_of_suit,
                c.date_filed = row.date_filed,
                c.date_terminated = row.date_terminated,
                c.outcome_label = row.outcome_label,
                c.procedural_stage = row.procedural_stage,
                c.has_irac = row.has_irac,
                c.idb_class_action = row.idb_class_action,
                c.idb_pro_se = row.idb_pro_se
            """,
            batch=chunk,
        )
        count += len(chunk)

    logger.info(f"Loaded {count} {CASE} nodes ({sum(1 for b in batch if b['has_irac'])} with IRAC)")
    return count


# ---------------------------------------------------------------------------
# Opinion nodes (internal + external placeholders)
# ---------------------------------------------------------------------------

def load_opinion_nodes(session: Session, conn: sqlite3.Connection) -> int:
    """
    Load Opinion nodes: internal (from opinions table) + external placeholders
    (from citation_edges targets not in our DB).
    """
    conn.row_factory = sqlite3.Row

    # Internal opinions
    rows = conn.execute(
        """
        SELECT opinion_id, docket_id, author_str, precedential_status,
               citation_count, type, cluster_date_filed
        FROM opinions
        """
    ).fetchall()

    internal_batch = [
        {
            "opinion_id": r["opinion_id"],
            "docket_id": r["docket_id"],
            "author_str": r["author_str"],
            "precedential_status": r["precedential_status"],
            "citation_count": r["citation_count"],
            "type": r["type"],
            "cluster_date_filed": r["cluster_date_filed"],
            "internal": True,
        }
        for r in rows
    ]

    count = 0
    for chunk in _batched(internal_batch):
        session.run(
            f"""
            UNWIND $batch AS row
            MERGE (o:{OPINION} {{opinion_id: row.opinion_id}})
            SET o.docket_id = row.docket_id,
                o.author_str = row.author_str,
                o.precedential_status = row.precedential_status,
                o.citation_count = row.citation_count,
                o.type = row.type,
                o.cluster_date_filed = row.cluster_date_filed,
                o.internal = row.internal
            """,
            batch=chunk,
        )
        count += len(chunk)

    # External opinion placeholders (from citation targets)
    internal_ids = resolve_internal_opinion_ids(conn)
    cite_rows = conn.execute(
        "SELECT DISTINCT cited_opinion_url FROM citation_edges"
    ).fetchall()

    external_batch = []
    for r in cite_rows:
        oid = extract_opinion_id_from_url(r[0])
        if oid is not None and oid not in internal_ids:
            external_batch.append({"opinion_id": oid, "internal": False})

    for chunk in _batched(external_batch):
        session.run(
            f"""
            UNWIND $batch AS row
            MERGE (o:{OPINION} {{opinion_id: row.opinion_id}})
            ON CREATE SET o.internal = row.internal
            """,
            batch=chunk,
        )
        count += len(chunk)

    logger.info(
        f"Loaded {count} {OPINION} nodes "
        f"({len(internal_batch)} internal, {len(external_batch)} external)"
    )
    return count


# ---------------------------------------------------------------------------
# Statute nodes
# ---------------------------------------------------------------------------

def load_statute_nodes(session: Session, conn: sqlite3.Connection) -> int:
    """Load Statute nodes from cases.cause + IRAC statutes_cited."""
    statutes: dict[str, str] = {}  # normalized → raw

    # From cases.cause
    rows = conn.execute(
        "SELECT DISTINCT cause FROM cases WHERE cause IS NOT NULL AND cause != ''"
    ).fetchall()
    for r in rows:
        raw = r[0].strip()
        if raw:
            norm = normalize_statute(raw)
            statutes.setdefault(norm, raw)

    # From IRAC extractions
    irac_rows = conn.execute(
        "SELECT extraction FROM irac_extractions WHERE is_valid = 1"
    ).fetchall()
    for r in irac_rows:
        ext = json.loads(r[0])
        for statute in ext.get("statutes_cited", []):
            if statute:
                norm = normalize_statute(statute)
                statutes.setdefault(norm, statute)

    batch = [
        {"citation": norm, "raw_citation": raw}
        for norm, raw in statutes.items()
    ]

    count = 0
    for chunk in _batched(batch):
        session.run(
            f"""
            UNWIND $batch AS row
            MERGE (s:{STATUTE} {{citation: row.citation}})
            SET s.raw_citation = row.raw_citation
            """,
            batch=chunk,
        )
        count += len(chunk)

    logger.info(f"Loaded {count} {STATUTE} nodes")
    return count


# ---------------------------------------------------------------------------
# LegalArgument nodes
# ---------------------------------------------------------------------------

def load_argument_nodes(session: Session, conn: sqlite3.Connection) -> int:
    """
    Load LegalArgument nodes from IRAC arguments_plaintiff + arguments_defendant.

    Deduplicates via exact-match-after-normalization (SHA-256).
    """
    arguments: dict[str, dict] = {}  # hash → {text, normalized, side}

    rows = conn.execute(
        "SELECT extraction FROM irac_extractions WHERE is_valid = 1"
    ).fetchall()

    for r in rows:
        ext = json.loads(r[0])
        for arg_text in ext.get("arguments_plaintiff", []):
            if arg_text:
                norm, h = normalize_argument(arg_text)
                arguments.setdefault(h, {"text": arg_text, "normalized": norm, "side": "plaintiff"})

        for arg_text in ext.get("arguments_defendant", []):
            if arg_text:
                norm, h = normalize_argument(arg_text)
                arguments.setdefault(h, {"text": arg_text, "normalized": norm, "side": "defendant"})

    batch = [
        {
            "text_hash": h,
            "text": info["text"],
            "text_normalized": info["normalized"],
            "side": info["side"],
        }
        for h, info in arguments.items()
    ]

    count = 0
    for chunk in _batched(batch):
        session.run(
            f"""
            UNWIND $batch AS row
            MERGE (a:{LEGAL_ARGUMENT} {{text_hash: row.text_hash}})
            SET a.text = row.text,
                a.text_normalized = row.text_normalized,
                a.side = row.side
            """,
            batch=chunk,
        )
        count += len(chunk)

    logger.info(f"Loaded {count} {LEGAL_ARGUMENT} nodes (from {sum(len(json.loads(r[0]).get('arguments_plaintiff', [])) + len(json.loads(r[0]).get('arguments_defendant', [])) for r in rows)} total argument strings)")
    return count


# ---------------------------------------------------------------------------
# Judge nodes
# ---------------------------------------------------------------------------

def load_judge_nodes(session: Session, conn: sqlite3.Connection) -> int:
    """
    Load Judge nodes. Primary source: opinions.author_str.
    Fallback: cases.assigned_to_str (only if no matching author).
    """
    judges: dict[str, str] = {}  # normalized → raw

    # Primary: opinion authors
    rows = conn.execute(
        "SELECT DISTINCT author_str FROM opinions "
        "WHERE author_str IS NOT NULL AND author_str != ''"
    ).fetchall()
    for r in rows:
        raw = r[0].strip()
        if raw:
            norm = normalize_name(raw)
            judges.setdefault(norm, raw)

    # Fallback: assigned judges (only if not already in judges)
    rows = conn.execute(
        "SELECT DISTINCT assigned_to_str FROM cases "
        "WHERE assigned_to_str IS NOT NULL AND assigned_to_str != ''"
    ).fetchall()
    for r in rows:
        raw = r[0].strip()
        if raw:
            norm = normalize_name(raw)
            judges.setdefault(norm, raw)

    batch = [
        {"name_normalized": norm, "name_raw": raw}
        for norm, raw in judges.items()
    ]

    count = 0
    for chunk in _batched(batch):
        session.run(
            f"""
            UNWIND $batch AS row
            MERGE (j:{JUDGE} {{name_normalized: row.name_normalized}})
            SET j.name_raw = row.name_raw
            """,
            batch=chunk,
        )
        count += len(chunk)

    logger.info(f"Loaded {count} {JUDGE} nodes")
    return count


# ---------------------------------------------------------------------------
# Company nodes (defendants)
# ---------------------------------------------------------------------------

def load_company_nodes(session: Session, conn: sqlite3.Connection) -> int:
    """Load Company nodes from parties with defendant party_type."""
    rows = conn.execute(
        """
        SELECT DISTINCT name FROM parties
        WHERE party_type LIKE '%Defendant%'
          AND name IS NOT NULL AND name != ''
        """
    ).fetchall()

    batch = [
        {"name_normalized": normalize_name(r[0]), "name_raw": r[0].strip()}
        for r in rows
        if r[0] and r[0].strip()
    ]

    count = 0
    for chunk in _batched(batch):
        session.run(
            f"""
            UNWIND $batch AS row
            MERGE (co:{COMPANY} {{name_normalized: row.name_normalized}})
            SET co.name_raw = row.name_raw
            """,
            batch=chunk,
        )
        count += len(chunk)

    logger.info(f"Loaded {count} {COMPANY} nodes")
    return count


# ---------------------------------------------------------------------------
# LawFirm nodes
# ---------------------------------------------------------------------------

def load_firm_nodes(session: Session, conn: sqlite3.Connection) -> int:
    """Load LawFirm nodes extracted from attorney contact_raw."""
    rows = conn.execute(
        "SELECT DISTINCT contact_raw FROM attorneys "
        "WHERE contact_raw IS NOT NULL AND contact_raw != ''"
    ).fetchall()

    firms: dict[str, str] = {}  # normalized → raw
    for r in rows:
        firm = extract_firm_name(r[0])
        if firm:
            norm = normalize_name(firm)
            firms.setdefault(norm, firm)

    batch = [
        {"name_normalized": norm, "name_raw": raw}
        for norm, raw in firms.items()
    ]

    count = 0
    for chunk in _batched(batch):
        session.run(
            f"""
            UNWIND $batch AS row
            MERGE (f:{LAW_FIRM} {{name_normalized: row.name_normalized}})
            SET f.name_raw = row.name_raw
            """,
            batch=chunk,
        )
        count += len(chunk)

    logger.info(f"Loaded {count} {LAW_FIRM} nodes")
    return count
