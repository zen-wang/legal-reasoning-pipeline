"""
Data resolution utilities for Phase 2 graph construction.

Handles: citation URL parsing, argument deduplication,
name normalization, statute normalization, firm name extraction.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3

# ---------------------------------------------------------------------------
# Citation URL → opinion_id
# ---------------------------------------------------------------------------

_CL_URL_PATTERN = re.compile(
    r"courtlistener\.com/api/rest/v\d+/opinions/(\d+)/?"
)


def extract_opinion_id_from_url(url: str) -> int | None:
    """
    Extract opinion_id from a CourtListener API URL.

    Example: "https://www.courtlistener.com/api/rest/v4/opinions/109009/" → 109009
    """
    m = _CL_URL_PATTERN.search(url)
    return int(m.group(1)) if m else None


def resolve_internal_opinion_ids(conn: sqlite3.Connection) -> set[int]:
    """Return the set of opinion_ids that exist in our database."""
    rows = conn.execute("SELECT opinion_id FROM opinions").fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Argument normalization + deduplication
# ---------------------------------------------------------------------------

def normalize_argument(text: str) -> tuple[str, str]:
    """
    Normalize argument text and compute SHA-256 hash for deduplication.

    Returns (normalized_text, hex_hash).
    """
    normalized = text.strip().lower()
    # Collapse whitespace
    normalized = re.sub(r"\s+", " ", normalized)
    # Strip trailing punctuation
    normalized = normalized.rstrip(".,;:")
    text_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return (normalized, text_hash)


# ---------------------------------------------------------------------------
# Name normalization (judges, companies)
# ---------------------------------------------------------------------------

_TITLE_SUFFIXES = re.compile(
    r",?\s*(jr\.?|sr\.?|iii?|iv|esq\.?|ph\.?d\.?|j\.?d\.?)$",
    re.IGNORECASE,
)
_TITLE_PREFIXES = re.compile(
    r"^(hon\.?|judge|justice|chief\s+judge|magistrate\s+judge)\s+",
    re.IGNORECASE,
)


def normalize_name(name: str) -> str:
    """
    Normalize a person or entity name for deduplication.

    Strips titles, suffixes, extra whitespace, lowercases.
    """
    normalized = name.strip()
    normalized = _TITLE_PREFIXES.sub("", normalized)
    normalized = _TITLE_SUFFIXES.sub("", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()
    return normalized


# ---------------------------------------------------------------------------
# Statute normalization
# ---------------------------------------------------------------------------

def normalize_statute(text: str) -> str:
    """
    Normalize a statute citation string.

    Standardizes section symbols and whitespace.
    Example: "15 U.S.C. §78j(b)" → "15 u.s.c. § 78j(b)"
    """
    normalized = text.strip()
    # Normalize section symbols
    normalized = normalized.replace("§§", "§").replace("SS", "§")
    # Ensure space after §
    normalized = re.sub(r"§\s*", "§ ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()
    return normalized


# ---------------------------------------------------------------------------
# Firm name extraction from attorney contact_raw
# ---------------------------------------------------------------------------

_FIRM_INDICATORS = re.compile(
    r"\b(llp|llc|p\.?a\.?|p\.?c\.?|l\.?l\.?p|pllc|"
    r"law\s+(office|firm|group|center)|"
    r"& associates|attorneys?\s+at\s+law)\b",
    re.IGNORECASE,
)
_SKIP_LINE = re.compile(
    r"(^\d|^phone|^fax|^tel|^direct|^email|^@|^http|^www\.|"
    r"^\(?\d{3}\)?[\s.-]?\d{3}|"
    r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z])",
    re.IGNORECASE,
)


def extract_firm_name(contact_raw: str | None) -> str | None:
    """
    Extract law firm name from attorney contact block.

    Scans lines for firm indicators (LLP, LLC, etc.).
    Returns None if no firm found.
    """
    if not contact_raw:
        return None

    for line in contact_raw.split("\n"):
        line = line.strip()
        if not line or len(line) < 5:
            continue
        if _SKIP_LINE.match(line):
            continue
        if _FIRM_INDICATORS.search(line):
            return line

    return None
