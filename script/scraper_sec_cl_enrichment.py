"""
SEC EDGAR → CourtListener Enrichment Scraper
=============================================
Samples 100 cases from the SEC EDGAR litigation releases CSV, matches them
to CourtListener using a 5-stage search cascade, and scrapes ALL available
data from 22 CourtListener API endpoints.

Usage:
  python -m script.scraper_sec_cl_enrichment full   --csv <csv> --db <db>
  python -m script.scraper_sec_cl_enrichment sample --csv <csv> --db <db>
  python -m script.scraper_sec_cl_enrichment match  --db <db>
  python -m script.scraper_sec_cl_enrichment scrape --db <db>
  python -m script.scraper_sec_cl_enrichment status --db <db>
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import random
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, quote

import aiohttp
from tqdm import tqdm

# ── Config ──────────────────────────────────────────────────────────

BASE_URL = "https://www.courtlistener.com/api/rest/v4"

_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())

COURTLISTENER_TOKEN = os.environ.get("COURTLISTENER_TOKEN", "")
if not COURTLISTENER_TOKEN:
    print("ERROR: COURTLISTENER_TOKEN not found. Set it in .env or environment.")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Token {COURTLISTENER_TOKEN}",
    "Accept": "application/json",
}

DEFAULT_CSV = Path(__file__).parent.parent / "sec-litigation-scraper" / "sec_litigation_releases_with_CL.csv"
DEFAULT_DB = Path(__file__).parent.parent / "data" / "sec_cl_enriched_100.db"

REQUEST_INTERVAL = 1.5
MAX_RETRIES = 5
RETRY_BACKOFF = 10.0
COOLDOWN_ON_429 = 60.0
SAMPLE_SIZE = 100
SAMPLE_SEED = 42

logger = logging.getLogger("scraper_sec_cl")


# ── Court Mapping ───────────────────────────────────────────────────

# SEC EDGAR court strings → CourtListener court IDs
SEC_COURT_TO_CL: dict[str, str] = {
    # Full names
    "district of maine": "med",
    "district of massachusetts": "mad",
    "district of new hampshire": "nhd",
    "district of rhode island": "rid",
    "district of connecticut": "ctd",
    "district of vermont": "vtd",
    "district of delaware": "ded",
    "district of new jersey": "njd",
    "district of maryland": "mdd",
    "district of columbia": "dcd",
    "district of colorado": "cod",
    "district of kansas": "ksd",
    "district of new mexico": "nmd",
    "district of utah": "utd",
    "district of wyoming": "wyd",
    "district of alaska": "akd",
    "district of arizona": "azd",
    "district of hawaii": "hid",
    "district of idaho": "idd",
    "district of montana": "mtd",
    "district of nevada": "nvd",
    "district of oregon": "ord",
    "district of minnesota": "mnd",
    "district of nebraska": "ned",
    "district of north dakota": "ndd",
    "district of south dakota": "sdd",
    # Directional districts
    "northern district of california": "cand",
    "central district of california": "cacd",
    "southern district of california": "casd",
    "eastern district of california": "caed",
    "northern district of illinois": "ilnd",
    "southern district of illinois": "ilsd",
    "central district of illinois": "ilcd",
    "northern district of indiana": "innd",
    "southern district of indiana": "insd",
    "eastern district of wisconsin": "wied",
    "western district of wisconsin": "wiwd",
    "eastern district of arkansas": "ared",
    "western district of arkansas": "arwd",
    "northern district of iowa": "iand",
    "southern district of iowa": "iasd",
    "eastern district of missouri": "moed",
    "western district of missouri": "mowd",
    "eastern district of kentucky": "kyed",
    "western district of kentucky": "kywd",
    "eastern district of michigan": "mied",
    "western district of michigan": "miwd",
    "northern district of ohio": "ohnd",
    "southern district of ohio": "ohsd",
    "eastern district of tennessee": "tned",
    "middle district of tennessee": "tnmd",
    "western district of tennessee": "tnwd",
    "eastern district of new york": "nyed",
    "northern district of new york": "nynd",
    "southern district of new york": "nysd",
    "western district of new york": "nywd",
    "eastern district of pennsylvania": "paed",
    "middle district of pennsylvania": "pamd",
    "western district of pennsylvania": "pawd",
    "eastern district of north carolina": "nced",
    "middle district of north carolina": "ncmd",
    "western district of north carolina": "ncwd",
    "district of south carolina": "scd",
    "eastern district of virginia": "vaed",
    "western district of virginia": "vawd",
    "northern district of west virginia": "wvnd",
    "southern district of west virginia": "wvsd",
    "eastern district of louisiana": "laed",
    "middle district of louisiana": "lamd",
    "western district of louisiana": "lawd",
    "northern district of mississippi": "msnd",
    "southern district of mississippi": "mssd",
    "eastern district of texas": "txed",
    "northern district of texas": "txnd",
    "southern district of texas": "txsd",
    "western district of texas": "txwd",
    "northern district of oklahoma": "oknd",
    "eastern district of oklahoma": "oked",
    "western district of oklahoma": "okwd",
    "northern district of alabama": "alnd",
    "middle district of alabama": "almd",
    "southern district of alabama": "alsd",
    "northern district of florida": "flnd",
    "middle district of florida": "flmd",
    "southern district of florida": "flsd",
    "northern district of georgia": "gand",
    "middle district of georgia": "gamd",
    "southern district of georgia": "gasd",
    # Abbreviations
    "s.d.n.y.": "nysd", "sdny": "nysd",
    "e.d.n.y.": "nyed", "edny": "nyed",
    "n.d. ill.": "ilnd", "n.d.ill.": "ilnd",
    "s.d. ohio": "ohsd",
    "d. mass.": "mad", "d.mass.": "mad",
    "d. conn.": "ctd",
    "d. colo.": "cod", "d.colo.": "cod",
    "d. md.": "mdd", "d.md.": "mdd",
    "d.d.c.": "dcd", "d. d.c.": "dcd",
    "c.d. cal.": "cacd", "c.d.cal.": "cacd",
    "n.d. cal.": "cand",
    "s.d. cal.": "casd",
    "e.d. cal.": "caed",
    "n.d. ga.": "gand",
    "m.d. fla.": "flmd", "m.d.fla.": "flmd",
    "s.d. fla.": "flsd",
    "n.d. fla.": "flnd",
    "e.d. pa.": "paed",
    "m.d. pa.": "pamd",
    "w.d. pa.": "pawd",
    "d. utah": "utd",
    "d. nev.": "nvd",
    "d. ariz.": "azd",
    "d. n.j.": "njd",
    "w.d. wash.": "wawd",
    "e.d. wash.": "waed",
    "d. ore.": "ord",
    "d. minn.": "mnd",
    "n.d. tex.": "txnd",
    "s.d. tex.": "txsd",
    "e.d. tex.": "txed",
    "w.d. tex.": "txwd",
    "e.d.n.c.": "nced",
    "w.d. okla.": "okwd",
}


def normalize_court(raw: str) -> str | None:
    """Map a SEC EDGAR court string to a CourtListener court_id."""
    if not raw:
        return None
    cleaned = raw.strip().lower()
    # Remove trailing junk from CSV extraction artifacts
    cleaned = re.sub(r"\s+(seeking|against|civil|no\.|civ\.).*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned in SEC_COURT_TO_CL:
        return SEC_COURT_TO_CL[cleaned]
    # Partial match: try substring
    for pattern, cl_id in SEC_COURT_TO_CL.items():
        if pattern in cleaned:
            return cl_id
    return None


# ── HTML Utilities ──────────────────────────────────────────────────

def html_to_plain_text(html: str) -> str:
    """Convert HTML opinion text to clean plain text."""
    if not html:
        return ""
    text = re.sub(r"<(?:p|div|br|blockquote|h[1-6]|li|tr)[^>]*>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()


# ── Rate Limiter ────────────────────────────────────────────────────

class RateLimiter:
    """Token bucket rate limiter for async requests."""

    def __init__(self, interval: float = REQUEST_INTERVAL) -> None:
        self._interval = interval
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self.total_requests = 0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()
            self.total_requests += 1


# ── Database ────────────────────────────────────────────────────────

def init_db(db_path: Path) -> sqlite3.Connection:
    """Create SQLite database with all tables."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    conn.executescript("""
        -- Tier A: SEC EDGAR source data
        CREATE TABLE IF NOT EXISTS sec_cases (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            case_title            TEXT DEFAULT '',
            citation              TEXT DEFAULT '',
            court                 TEXT DEFAULT '',
            date                  TEXT DEFAULT '',
            petitioner            TEXT DEFAULT '',
            respondent            TEXT DEFAULT '',
            defendant_roles       TEXT DEFAULT '',
            defendant_employer    TEXT DEFAULT '',
            employer_crd_cik      TEXT DEFAULT '',
            co_defendants         TEXT DEFAULT '',
            relief_defendants     TEXT DEFAULT '',
            sec_attorneys         TEXT DEFAULT '',
            sec_regional_office   TEXT DEFAULT '',
            judges                TEXT DEFAULT '',
            judgment_type         TEXT DEFAULT '',
            summary               TEXT DEFAULT '',
            outcome               TEXT DEFAULT '',
            legal_topic           TEXT DEFAULT '',
            charges_and_sections  TEXT DEFAULT '',
            company_domain        TEXT DEFAULT '',
            total_fine_amount     TEXT DEFAULT '',
            total_victim_losses   TEXT DEFAULT '',
            scheme_duration       TEXT DEFAULT '',
            scheme_method         TEXT DEFAULT '',
            victim_count          TEXT DEFAULT '',
            admission_status      TEXT DEFAULT '',
            parallel_actions      TEXT DEFAULT '',
            related_releases      TEXT DEFAULT '',
            case_status           TEXT DEFAULT '',
            scheme_start_date     TEXT DEFAULT '',
            scheme_end_date       TEXT DEFAULT '',
            complaint_filed_date  TEXT DEFAULT '',
            judgment_date         TEXT DEFAULT '',
            regulatory_registrations TEXT DEFAULT '',
            defendant_sentence    TEXT DEFAULT '',
            final_judgment_details TEXT DEFAULT '',
            source_url            TEXT DEFAULT '',
            pdf_insights          TEXT DEFAULT '',
            associated_documents  TEXT DEFAULT '',
            sample_stratum        TEXT DEFAULT '',
            csv_row_index         INTEGER,
            UNIQUE(source_url)
        );

        -- Tier B: Match tracking
        CREATE TABLE IF NOT EXISTS cl_match_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            sec_case_id     INTEGER REFERENCES sec_cases(id),
            match_status    TEXT DEFAULT 'pending',
            match_method    TEXT DEFAULT '',
            search_queries_tried TEXT DEFAULT '[]',
            cl_docket_id    INTEGER,
            cl_cluster_ids  TEXT DEFAULT '',
            confidence      TEXT DEFAULT '',
            match_notes     TEXT DEFAULT '',
            attempts        INTEGER DEFAULT 0,
            last_attempt_at TEXT,
            matched_at      TEXT,
            UNIQUE(sec_case_id)
        );

        -- Tier C: CourtListener enrichment
        CREATE TABLE IF NOT EXISTS cl_dockets (
            docket_id           INTEGER PRIMARY KEY,
            sec_case_id         INTEGER REFERENCES sec_cases(id),
            case_name           TEXT,
            docket_number       TEXT,
            pacer_case_id       TEXT,
            slug                TEXT,
            absolute_url        TEXT,
            court_id            TEXT,
            cause               TEXT,
            nature_of_suit      TEXT,
            jurisdiction_type   TEXT,
            date_filed          TEXT,
            date_terminated     TEXT,
            date_last_filing    TEXT,
            assigned_to         TEXT,
            assigned_to_str     TEXT,
            referred_to         TEXT,
            referred_to_str     TEXT,
            jury_demand         TEXT,
            filepath_ia         TEXT,
            idb_disposition       INTEGER,
            idb_judgment          INTEGER,
            idb_procedural_progress INTEGER,
            idb_nature_of_suit    INTEGER,
            idb_monetary_demand   REAL,
            idb_pro_se            INTEGER,
            idb_class_action      INTEGER,
            idb_origin            INTEGER,
            idb_jury_demand       TEXT,
            scrape_status       TEXT DEFAULT 'pending',
            scraped_at          TEXT
        );

        CREATE TABLE IF NOT EXISTS cl_opinions (
            opinion_id              INTEGER PRIMARY KEY,
            docket_id               INTEGER REFERENCES cl_dockets(docket_id),
            cluster_id              INTEGER,
            plain_text              TEXT,
            html_with_citations     TEXT,
            type                    TEXT,
            author_str              TEXT,
            per_curiam              INTEGER,
            download_url            TEXT,
            cluster_case_name       TEXT,
            cluster_date_filed      TEXT,
            precedential_status     TEXT,
            citation_count          INTEGER,
            syllabus                TEXT,
            disposition             TEXT,
            posture                 TEXT,
            procedural_history      TEXT
        );

        CREATE TABLE IF NOT EXISTS cl_citation_edges (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            source_opinion_id   INTEGER,
            cited_opinion_url   TEXT,
            direction           TEXT DEFAULT 'outgoing',
            UNIQUE(source_opinion_id, cited_opinion_url, direction)
        );

        CREATE TABLE IF NOT EXISTS cl_parties (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            docket_id       INTEGER REFERENCES cl_dockets(docket_id),
            party_id        INTEGER,
            name            TEXT,
            party_type      TEXT,
            date_terminated TEXT,
            criminal_counts TEXT,
            extra_info      TEXT
        );

        CREATE TABLE IF NOT EXISTS cl_attorneys (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            docket_id       INTEGER REFERENCES cl_dockets(docket_id),
            attorney_id     INTEGER,
            name            TEXT,
            contact_raw     TEXT,
            phone           TEXT,
            fax             TEXT,
            email           TEXT,
            roles           TEXT
        );

        CREATE TABLE IF NOT EXISTS cl_docket_entries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            docket_id       INTEGER REFERENCES cl_dockets(docket_id),
            entry_id        INTEGER,
            entry_number    INTEGER,
            date_filed      TEXT,
            description     TEXT
        );

        CREATE TABLE IF NOT EXISTS cl_judges (
            person_id       INTEGER PRIMARY KEY,
            name_full       TEXT,
            name_first      TEXT,
            name_last       TEXT,
            date_dob        TEXT,
            date_dod        TEXT,
            gender          TEXT,
            political_affiliation TEXT,
            races           TEXT,
            aba_rating      TEXT,
            school          TEXT,
            positions_json  TEXT,
            fetched_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS cl_judge_financials (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id       INTEGER REFERENCES cl_judges(person_id),
            year            INTEGER,
            filepath        TEXT,
            thumbnail       TEXT,
            has_been_extracted INTEGER,
            fetched_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS cl_courts (
            court_id        TEXT PRIMARY KEY,
            full_name       TEXT,
            short_name      TEXT,
            jurisdiction    TEXT,
            date_start      TEXT,
            date_end        TEXT,
            citation_string TEXT,
            url             TEXT,
            fetched_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS cl_originating_court (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            docket_id       INTEGER REFERENCES cl_dockets(docket_id),
            originating_court_id TEXT,
            originating_court_name TEXT,
            assigned_to     TEXT,
            date_filed      TEXT,
            docket_number   TEXT,
            ordering_judge  TEXT
        );

        CREATE TABLE IF NOT EXISTS cl_citation_lookups (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            sec_case_id     INTEGER REFERENCES sec_cases(id),
            input_text      TEXT,
            matched_opinion_ids TEXT,
            lookup_at       TEXT
        );

        CREATE TABLE IF NOT EXISTS cl_audio (
            id              INTEGER PRIMARY KEY,
            docket_id       INTEGER REFERENCES cl_dockets(docket_id),
            date_argued     TEXT,
            judges          TEXT,
            duration        INTEGER,
            download_url    TEXT,
            local_path      TEXT,
            source          TEXT
        );

        CREATE TABLE IF NOT EXISTS cl_recap_documents (
            id              INTEGER PRIMARY KEY,
            docket_entry_id INTEGER,
            docket_id       INTEGER,
            pacer_doc_id    TEXT,
            document_type   TEXT,
            description     TEXT,
            is_available    INTEGER,
            filepath_local  TEXT,
            filepath_ia     TEXT,
            ocr_status      TEXT,
            page_count      INTEGER
        );

        CREATE TABLE IF NOT EXISTS cl_bankruptcy (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            docket_id       INTEGER REFERENCES cl_dockets(docket_id),
            chapter         TEXT,
            trustee_str     TEXT,
            date_converted  TEXT,
            date_confirmed  TEXT,
            date_discharged TEXT,
            date_dismissed  TEXT
        );

        -- Indexes
        CREATE INDEX IF NOT EXISTS idx_sec_cases_url ON sec_cases(source_url);
        CREATE INDEX IF NOT EXISTS idx_match_status ON cl_match_log(match_status);
        CREATE INDEX IF NOT EXISTS idx_match_docket ON cl_match_log(cl_docket_id);
        CREATE INDEX IF NOT EXISTS idx_opinions_docket ON cl_opinions(docket_id);
        CREATE INDEX IF NOT EXISTS idx_parties_docket ON cl_parties(docket_id);
        CREATE INDEX IF NOT EXISTS idx_attorneys_docket ON cl_attorneys(docket_id);
        CREATE INDEX IF NOT EXISTS idx_entries_docket ON cl_docket_entries(docket_id);
        CREATE INDEX IF NOT EXISTS idx_citations_source ON cl_citation_edges(source_opinion_id);
        CREATE INDEX IF NOT EXISTS idx_dockets_sec_case ON cl_dockets(sec_case_id);
    """)
    conn.commit()
    return conn


# ── API Client ──────────────────────────────────────────────────────

async def fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    limiter: RateLimiter,
    retries: int = MAX_RETRIES,
) -> dict[str, Any] | None:
    """Fetch JSON from CourtListener with rate limiting and retry."""
    for attempt in range(retries):
        await limiter.acquire()
        try:
            async with session.get(url, headers=HEADERS) as resp:
                if resp.status == 200:
                    return await resp.json()
                if resp.status == 429:
                    logger.warning(f"Rate limited (429). Cooling down {COOLDOWN_ON_429:.0f}s...")
                    await asyncio.sleep(COOLDOWN_ON_429)
                    continue
                if resp.status == 403:
                    logger.debug(f"Access denied (403): {url}")
                    return None
                if resp.status >= 500:
                    wait = RETRY_BACKOFF * (2 ** attempt)
                    logger.warning(f"Server error ({resp.status}). Retry in {wait:.0f}s...")
                    await asyncio.sleep(wait)
                    continue
                logger.warning(f"HTTP {resp.status}: {url}")
                return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            wait = RETRY_BACKOFF * (2 ** attempt)
            logger.warning(f"Request error: {e}. Retry in {wait:.0f}s...")
            await asyncio.sleep(wait)
    logger.error(f"Failed after {retries} retries: {url}")
    return None


async def post_json(
    session: aiohttp.ClientSession,
    url: str,
    limiter: RateLimiter,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    """POST JSON to CourtListener (for citation-lookup)."""
    await limiter.acquire()
    try:
        async with session.post(url, headers=HEADERS, json=data) as resp:
            if resp.status == 200:
                return await resp.json()
            logger.debug(f"POST {resp.status}: {url}")
            return None
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning(f"POST error: {e}")
        return None


async def fetch_all_pages(
    session: aiohttp.ClientSession,
    url: str,
    limiter: RateLimiter,
    max_pages: int = 50,
) -> list[dict[str, Any]]:
    """Fetch all pages from a paginated CL endpoint."""
    results: list[dict[str, Any]] = []
    current_url: str | None = url
    page = 0
    while current_url and page < max_pages:
        data = await fetch_json(session, current_url, limiter)
        if not data:
            break
        results.extend(data.get("results", []))
        current_url = data.get("next")
        page += 1
    return results


# ── Name Normalization ──────────────────────────────────────────────

def extract_search_name(sec_row: dict) -> str:
    """Extract a clean respondent last name for CourtListener search."""
    respondent = sec_row.get("respondent", "")
    if not respondent:
        # Fall back to case_title
        title = sec_row.get("case_title", "")
        match = re.search(r"v\.\s*(.+)", title, re.I)
        respondent = match.group(1).strip() if match else ""

    # Take first defendant only
    respondent = re.split(r",\s*| and ", respondent, maxsplit=1)[0].strip()
    # Remove corporate suffixes
    respondent = re.sub(
        r"\b(Inc\.?|Corp\.?|LLC|Ltd\.?|L\.?P\.?|Co\.?|Group|et al\.?)\b",
        "", respondent, flags=re.I
    ).strip()
    # Remove newlines and extra whitespace
    respondent = re.sub(r"\s+", " ", respondent).strip()
    # Get last name for search (last word)
    parts = respondent.split()
    return parts[-1] if parts else ""


def parse_sec_date(date_str: str) -> str | None:
    """Parse SEC EDGAR date string ('September 22, 1995') to ISO format."""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str.strip(), "%B %d, %Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        try:
            dt = datetime.strptime(date_str.strip(), "%b %d, %Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None


def extract_person_id(url: str) -> int | None:
    """Extract person ID from a /people/123/ URL."""
    if not url or not isinstance(url, str):
        return None
    m = re.search(r"/people/(\d+)/", url)
    return int(m.group(1)) if m else None


# ── Sampling ────────────────────────────────────────────────────────

def _parse_year(date_str: str) -> int | None:
    """Extract year from SEC date string."""
    m = re.search(r"\d{4}", date_str)
    return int(m.group()) if m else None


def _time_stratum(year: int) -> str:
    if year <= 1999: return "T1_1995-1999"
    if year <= 2004: return "T2_2000-2004"
    if year <= 2009: return "T3_2005-2009"
    if year <= 2014: return "T4_2010-2014"
    if year <= 2019: return "T5_2015-2019"
    return "T6_2020-2026"


def _judgment_stratum(jtype: str) -> str:
    jl = jtype.lower()
    if "summary judgment" in jl:
        return "J5_summary"
    if "default judgment" in jl or "default" in jl:
        return "J4_default"
    if "final judgment" in jl:
        return "J3_final"
    if "consent" in jl:
        return "J2_consent"
    return "J1_complaint"


def sample_cases(csv_path: Path, seed: int = SAMPLE_SEED) -> list[dict]:
    """Stratified sample of 100 cases from SEC EDGAR CSV."""
    rng = random.Random(seed)
    rows: list[dict] = []

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            year = _parse_year(row.get("date", ""))
            if not year:
                continue
            ts = _time_stratum(year)
            js = _judgment_stratum(row.get("judgment_type", ""))
            row["_stratum"] = f"{ts}__{js}"
            row["_csv_row"] = i
            rows.append(row)

    # Group by stratum
    strata: dict[str, list[dict]] = {}
    for r in rows:
        strata.setdefault(r["_stratum"], []).append(r)

    # Proportional allocation with floor of 1
    total = len(rows)
    targets: dict[str, int] = {}
    for s, items in strata.items():
        targets[s] = max(1, round(SAMPLE_SIZE * len(items) / total))

    # Adjust to sum to SAMPLE_SIZE
    while sum(targets.values()) > SAMPLE_SIZE:
        largest = max(targets, key=targets.get)
        targets[largest] -= 1
    while sum(targets.values()) < SAMPLE_SIZE:
        largest = max(strata, key=lambda s: len(strata[s]))
        targets[largest] += 1

    # Sample
    sampled: list[dict] = []
    for s, target in targets.items():
        pool = strata.get(s, [])
        n = min(target, len(pool))
        sampled.extend(rng.sample(pool, n))

    # Fill if short
    remaining = [r for r in rows if r not in sampled]
    if len(sampled) < SAMPLE_SIZE:
        extra = rng.sample(remaining, SAMPLE_SIZE - len(sampled))
        sampled.extend(extra)

    logger.info(f"Sampled {len(sampled)} cases across {len(strata)} strata")
    return sampled[:SAMPLE_SIZE]


# ── Match Scoring ───────────────────────────────────────────────────

def score_match(sec_row: dict, cl_result: dict) -> tuple[str, float]:
    """Score how well a CL search result matches an SEC EDGAR case."""
    score = 0.0
    respondent = extract_search_name(sec_row).lower()
    cl_name = (cl_result.get("caseName") or cl_result.get("case_name") or "").lower()

    # Name overlap
    if respondent and respondent in cl_name:
        score += 0.4
    elif respondent:
        resp_tokens = set(respondent.split())
        cl_tokens = set(cl_name.split())
        overlap = resp_tokens & cl_tokens
        if resp_tokens:
            score += 0.4 * (len(overlap) / len(resp_tokens))

    # Court match
    sec_court = normalize_court(sec_row.get("court", ""))
    cl_court = cl_result.get("court_id") or cl_result.get("court", "")
    if isinstance(cl_court, str) and "/courts/" in cl_court:
        cl_court = cl_court.rstrip("/").split("/")[-1]
    if sec_court and cl_court and sec_court == cl_court:
        score += 0.3

    # Date proximity
    sec_date = parse_sec_date(sec_row.get("date", ""))
    cl_date = cl_result.get("dateFiled") or cl_result.get("date_filed")
    if sec_date and cl_date:
        try:
            sd = datetime.strptime(sec_date, "%Y-%m-%d")
            cd = datetime.strptime(cl_date[:10], "%Y-%m-%d")
            days = abs((sd - cd).days)
            if days <= 60:
                score += 0.3
            elif days <= 180:
                score += 0.2
            elif days <= 365:
                score += 0.1
        except ValueError:
            pass

    if score >= 0.7:
        return ("high", score)
    if score >= 0.4:
        return ("medium", score)
    return ("low", score)


# ── Case Matching ───────────────────────────────────────────────────

async def search_cl_for_case(
    session: aiohttp.ClientSession,
    limiter: RateLimiter,
    sec_row: dict,
) -> dict | None:
    """5-stage cascade to find an SEC EDGAR case on CourtListener.

    Returns a dict with docket_id, match_method, confidence, queries_tried
    or None if not found.
    """
    name = extract_search_name(sec_row)
    court = normalize_court(sec_row.get("court", ""))
    queries_tried: list[str] = []

    if not name:
        return None

    best_match: dict | None = None
    best_score = 0.0

    # Stage 1: Docket search by respondent + SEC
    params: dict[str, str] = {
        "type": "d",
        "q": f'"Securities and Exchange" "{name}"',
    }
    if court:
        params["court"] = court
    url = f"{BASE_URL}/search/?{urlencode(params)}"
    queries_tried.append(url)
    data = await fetch_json(session, url, limiter)
    if data:
        for r in data.get("results", [])[:5]:
            conf, sc = score_match(sec_row, r)
            if sc > best_score and conf in ("high", "medium"):
                best_match = {
                    "docket_id": r.get("docket_id"),
                    "match_method": "stage1_docket_respondent",
                    "confidence": conf,
                    "score": sc,
                }
                best_score = sc

    if best_match and best_score >= 0.7:
        best_match["queries_tried"] = queries_tried
        return best_match

    # Stage 2: Opinion search by respondent + SEC
    params2: dict[str, str] = {
        "type": "o",
        "q": f'"Securities and Exchange" "{name}"',
    }
    if court:
        params2["court"] = court
    url2 = f"{BASE_URL}/search/?{urlencode(params2)}"
    queries_tried.append(url2)
    data2 = await fetch_json(session, url2, limiter)
    if data2:
        for r in data2.get("results", [])[:5]:
            conf, sc = score_match(sec_row, r)
            did = r.get("docket_id")
            if did and sc > best_score and conf in ("high", "medium"):
                best_match = {
                    "docket_id": did,
                    "match_method": "stage2_opinion_respondent",
                    "confidence": conf,
                    "score": sc,
                    "cluster_id": r.get("cluster_id"),
                }
                best_score = sc

    if best_match and best_score >= 0.7:
        best_match["queries_tried"] = queries_tried
        return best_match

    # Stage 3: Docket search by case title keywords
    title = sec_row.get("case_title", "")
    title_clean = re.sub(
        r"(SECURITIES AND EXCHANGE COMMISSION|SEC)\s*v\.?\s*",
        "", title, flags=re.I
    ).strip()
    if title_clean and len(title_clean) > 3:
        # Take first 60 chars to avoid query-too-long
        title_clean = title_clean[:60]
        params3: dict[str, str] = {"type": "d", "q": f'"{title_clean}"'}
        url3 = f"{BASE_URL}/search/?{urlencode(params3)}"
        queries_tried.append(url3)
        data3 = await fetch_json(session, url3, limiter)
        if data3:
            for r in data3.get("results", [])[:5]:
                conf, sc = score_match(sec_row, r)
                if sc > best_score and conf in ("high", "medium"):
                    best_match = {
                        "docket_id": r.get("docket_id"),
                        "match_method": "stage3_title_search",
                        "confidence": conf,
                        "score": sc,
                    }
                    best_score = sc

    if best_match and best_score >= 0.6:
        best_match["queries_tried"] = queries_tried
        return best_match

    # Stage 4: Docket filter by court + date range
    sec_date = parse_sec_date(sec_row.get("date", ""))
    if court and sec_date:
        try:
            sd = datetime.strptime(sec_date, "%Y-%m-%d")
            params4 = {
                "court": court,
                "date_filed__gte": (sd - timedelta(days=180)).strftime("%Y-%m-%d"),
                "date_filed__lte": (sd + timedelta(days=30)).strftime("%Y-%m-%d"),
                "case_name__contains": "Securities",
                "fields": "id,case_name,docket_number,date_filed,court_id",
            }
            url4 = f"{BASE_URL}/dockets/?{urlencode(params4)}"
            queries_tried.append(url4)
            data4 = await fetch_json(session, url4, limiter)
            if data4:
                for r in data4.get("results", [])[:10]:
                    conf, sc = score_match(sec_row, r)
                    did = r.get("id")
                    if did and sc > best_score and conf in ("high", "medium"):
                        best_match = {
                            "docket_id": did,
                            "match_method": "stage4_court_date_filter",
                            "confidence": conf,
                            "score": sc,
                        }
                        best_score = sc
        except ValueError:
            pass

    if best_match and best_score >= 0.5:
        best_match["queries_tried"] = queries_tried
        return best_match

    # Stage 5: Broad name search
    params5: dict[str, str] = {"type": "d", "q": f'"{name}" SEC'}
    url5 = f"{BASE_URL}/search/?{urlencode(params5)}"
    queries_tried.append(url5)
    data5 = await fetch_json(session, url5, limiter)
    if data5:
        for r in data5.get("results", [])[:5]:
            conf, sc = score_match(sec_row, r)
            if sc > best_score and conf in ("high", "medium"):
                best_match = {
                    "docket_id": r.get("docket_id"),
                    "match_method": "stage5_broad_name",
                    "confidence": conf,
                    "score": sc,
                }
                best_score = sc

    if best_match:
        best_match["queries_tried"] = queries_tried
        return best_match

    return None


# ── Per-Case Scraping (ALL 22 endpoints) ────────────────────────────

async def scrape_matched_case(
    session: aiohttp.ClientSession,
    conn: sqlite3.Connection,
    docket_id: int,
    sec_case_id: int,
    limiter: RateLimiter,
) -> bool:
    """Scrape all available CourtListener data for a matched case."""
    # Check already done
    row = conn.execute(
        "SELECT scrape_status FROM cl_dockets WHERE docket_id = ? AND scrape_status = 'done'",
        (docket_id,),
    ).fetchone()
    if row:
        return True

    # Clean partial data
    for table in ("cl_docket_entries", "cl_attorneys", "cl_parties",
                  "cl_originating_court", "cl_audio", "cl_bankruptcy"):
        conn.execute(f"DELETE FROM {table} WHERE docket_id = ?", (docket_id,))
    conn.execute(
        "DELETE FROM cl_citation_edges WHERE source_opinion_id IN "
        "(SELECT opinion_id FROM cl_opinions WHERE docket_id = ?)",
        (docket_id,),
    )
    conn.execute("DELETE FROM cl_opinions WHERE docket_id = ?", (docket_id,))
    conn.execute("DELETE FROM cl_recap_documents WHERE docket_id = ?", (docket_id,))
    conn.execute("DELETE FROM cl_dockets WHERE docket_id = ?", (docket_id,))

    # ── Step 1: Docket details (/dockets/) ──
    docket = await fetch_json(session, f"{BASE_URL}/dockets/{docket_id}/", limiter)
    if not docket:
        return False

    idb = docket.get("idb_data") or {}
    assigned_to_url = docket.get("assigned_to") or ""
    court_id = docket.get("court_id") or ""
    if isinstance(docket.get("court"), str) and "/courts/" in docket["court"]:
        court_id = docket["court"].rstrip("/").split("/")[-1]

    conn.execute("""
        INSERT OR REPLACE INTO cl_dockets (
            docket_id, sec_case_id, case_name, docket_number, pacer_case_id,
            slug, absolute_url, court_id, cause, nature_of_suit,
            jurisdiction_type, date_filed, date_terminated, date_last_filing,
            assigned_to, assigned_to_str, referred_to, referred_to_str,
            jury_demand, filepath_ia,
            idb_disposition, idb_judgment, idb_procedural_progress,
            idb_nature_of_suit, idb_monetary_demand, idb_pro_se,
            idb_class_action, idb_origin, idb_jury_demand,
            scrape_status
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        docket_id, sec_case_id,
        docket.get("case_name"), docket.get("docket_number"),
        docket.get("pacer_case_id"), docket.get("slug"),
        docket.get("absolute_url"), court_id,
        docket.get("cause"), docket.get("nature_of_suit"),
        docket.get("jurisdiction_type"),
        docket.get("date_filed"), docket.get("date_terminated"),
        docket.get("date_last_filing"),
        assigned_to_url, docket.get("assigned_to_str"),
        docket.get("referred_to"), docket.get("referred_to_str"),
        docket.get("jury_demand"), docket.get("filepath_ia"),
        idb.get("disposition"), idb.get("judgment"),
        idb.get("procedural_progress"), idb.get("nature_of_suit"),
        idb.get("monetary_demand"), idb.get("pro_se"),
        idb.get("class_action"), idb.get("origin"),
        idb.get("jury_demand"), "pending",
    ))

    # ── Step 2+3: Clusters + Opinions (/clusters/, /opinions/) ──
    clusters = await fetch_all_pages(
        session, f"{BASE_URL}/clusters/?docket={docket_id}", limiter, max_pages=5
    )
    opinion_ids: list[int] = []
    for cluster in clusters:
        cluster_id = cluster.get("id")
        for op_url in cluster.get("sub_opinions", []):
            op = await fetch_json(session, op_url, limiter)
            if not op:
                continue
            opinion_id = op.get("id")
            if opinion_id:
                opinion_ids.append(opinion_id)

            plain_text = op.get("plain_text") or ""
            html_citations = op.get("html_with_citations") or ""
            if not plain_text.strip() and html_citations:
                plain_text = html_to_plain_text(html_citations)

            conn.execute("""
                INSERT OR REPLACE INTO cl_opinions (
                    opinion_id, docket_id, cluster_id, plain_text,
                    html_with_citations, type, author_str, per_curiam,
                    download_url, cluster_case_name, cluster_date_filed,
                    precedential_status, citation_count, syllabus,
                    disposition, posture, procedural_history
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                opinion_id, docket_id, cluster_id, plain_text,
                html_citations, op.get("type"), op.get("author_str"),
                1 if op.get("per_curiam") else 0, op.get("download_url"),
                cluster.get("case_name"), cluster.get("date_filed"),
                cluster.get("precedential_status"), cluster.get("citation_count"),
                cluster.get("syllabus"), cluster.get("disposition"),
                cluster.get("posture"), cluster.get("procedural_history"),
            ))

            # Citation edges from opinions_cited field
            for cited_url in op.get("opinions_cited", []):
                conn.execute("""
                    INSERT OR IGNORE INTO cl_citation_edges
                    (source_opinion_id, cited_opinion_url, direction) VALUES (?, ?, 'outgoing')
                """, (opinion_id, cited_url))

    # ── Step 4+5: Opinions-cited endpoint (dedicated) ──
    for oid in opinion_ids:
        # Outgoing
        out_data = await fetch_all_pages(
            session, f"{BASE_URL}/opinions-cited/?citing_opinion={oid}", limiter, max_pages=5
        )
        for edge in out_data:
            cited = edge.get("cited_opinion", "")
            if cited:
                conn.execute("""
                    INSERT OR IGNORE INTO cl_citation_edges
                    (source_opinion_id, cited_opinion_url, direction) VALUES (?, ?, 'outgoing')
                """, (oid, cited))

        # Incoming
        in_data = await fetch_all_pages(
            session, f"{BASE_URL}/opinions-cited/?cited_opinion={oid}", limiter, max_pages=5
        )
        for edge in in_data:
            citing = edge.get("citing_opinion", "")
            if citing:
                conn.execute("""
                    INSERT OR IGNORE INTO cl_citation_edges
                    (source_opinion_id, cited_opinion_url, direction) VALUES (?, ?, 'incoming')
                """, (oid, citing))

    # ── Step 6: Citation lookup (POST /citation-lookup/) ──
    sec_row = conn.execute("SELECT summary FROM sec_cases WHERE id = ?", (sec_case_id,)).fetchone()
    summary_text = (sec_row["summary"] if sec_row else "")[:500]
    if summary_text:
        lookup = await post_json(session, f"{BASE_URL}/citation-lookup/", limiter, {"text": summary_text})
        if lookup:
            matched_ids = json.dumps([r.get("id") for r in lookup.get("results", []) if r.get("id")])
            conn.execute("""
                INSERT INTO cl_citation_lookups (sec_case_id, input_text, matched_opinion_ids, lookup_at)
                VALUES (?, ?, ?, datetime('now'))
            """, (sec_case_id, summary_text[:200], matched_ids))

    # ── Step 7: Judge bio (/people/) ──
    person_id = extract_person_id(assigned_to_url)
    if person_id:
        existing = conn.execute("SELECT 1 FROM cl_judges WHERE person_id = ?", (person_id,)).fetchone()
        if not existing:
            person = await fetch_json(session, f"{BASE_URL}/people/{person_id}/", limiter)
            if person:
                conn.execute("""
                    INSERT OR IGNORE INTO cl_judges (
                        person_id, name_full, name_first, name_last,
                        date_dob, date_dod, gender, political_affiliation,
                        races, aba_rating, school, positions_json, fetched_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                """, (
                    person_id, person.get("name_full"), person.get("name_first"),
                    person.get("name_last"), person.get("date_dob"),
                    person.get("date_dod"), person.get("gender"),
                    person.get("political_affiliation"),
                    json.dumps(person.get("races", [])),
                    json.dumps(person.get("aba_ratings", [])),
                    json.dumps(person.get("schools", [])),
                    json.dumps(person.get("positions", [])),
                ))

    # ── Step 8: Financial disclosures (/financial-disclosures/) ──
    if person_id:
        fin_data = await fetch_all_pages(
            session, f"{BASE_URL}/financial-disclosures/?person={person_id}", limiter, max_pages=3
        )
        for fd in fin_data:
            conn.execute("""
                INSERT OR IGNORE INTO cl_judge_financials (
                    person_id, year, filepath, thumbnail, has_been_extracted, fetched_at
                ) VALUES (?,?,?,?,?,datetime('now'))
            """, (
                person_id, fd.get("year"), fd.get("filepath"),
                fd.get("thumbnail"), 1 if fd.get("has_been_extracted") else 0,
            ))

    # ── Step 9: Court metadata (/courts/) ──
    if court_id:
        existing = conn.execute("SELECT 1 FROM cl_courts WHERE court_id = ?", (court_id,)).fetchone()
        if not existing:
            court_data = await fetch_json(session, f"{BASE_URL}/courts/{court_id}/", limiter)
            if court_data:
                conn.execute("""
                    INSERT OR IGNORE INTO cl_courts (
                        court_id, full_name, short_name, jurisdiction,
                        date_start, date_end, citation_string, url, fetched_at
                    ) VALUES (?,?,?,?,?,?,?,?,datetime('now'))
                """, (
                    court_id, court_data.get("full_name"), court_data.get("short_name"),
                    court_data.get("jurisdiction"), court_data.get("date_start"),
                    court_data.get("date_end"), court_data.get("citation_string"),
                    court_data.get("url"),
                ))

    # ── Step 10: Originating court info (/originating-court-information/) ──
    orig = await fetch_json(
        session, f"{BASE_URL}/originating-court-information/?docket={docket_id}", limiter
    )
    if orig and orig.get("results"):
        for o in orig["results"]:
            conn.execute("""
                INSERT INTO cl_originating_court (
                    docket_id, originating_court_id, originating_court_name,
                    assigned_to, date_filed, docket_number, ordering_judge
                ) VALUES (?,?,?,?,?,?,?)
            """, (
                docket_id,
                o.get("originating_court_id"),
                o.get("originating_court") if isinstance(o.get("originating_court"), str) else None,
                o.get("assigned_to"),
                o.get("date_filed"),
                o.get("docket_number"),
                o.get("ordering_judge"),
            ))

    # ── Step 11: FJC integrated database (/fjc-integrated-database/) ──
    # Already captured via idb_data in docket response (Step 1)

    # ── Step 14: Audio (/audio/) ──
    audio_data = await fetch_all_pages(
        session, f"{BASE_URL}/audio/?docket={docket_id}", limiter, max_pages=3
    )
    for a in audio_data:
        conn.execute("""
            INSERT OR IGNORE INTO cl_audio (
                id, docket_id, date_argued, judges, duration,
                download_url, local_path, source
            ) VALUES (?,?,?,?,?,?,?,?)
        """, (
            a.get("id"), docket_id, a.get("date_argued"),
            a.get("judges"), a.get("duration"),
            a.get("download_url"), a.get("local_path"),
            a.get("source"),
        ))

    # ── Step 14b: Bankruptcy info (/bankruptcy-information/) ──
    bk_data = await fetch_json(
        session, f"{BASE_URL}/bankruptcy-information/?docket={docket_id}", limiter
    )
    if bk_data and bk_data.get("results"):
        for bk in bk_data["results"]:
            conn.execute("""
                INSERT INTO cl_bankruptcy (
                    docket_id, chapter, trustee_str, date_converted,
                    date_confirmed, date_discharged, date_dismissed
                ) VALUES (?,?,?,?,?,?,?)
            """, (
                docket_id, bk.get("chapter"), bk.get("trustee_str"),
                bk.get("date_converted"), bk.get("date_confirmed"),
                bk.get("date_discharged"), bk.get("date_dismissed"),
            ))

    # ── Steps 15-17: GATED endpoints (403 OK) ──

    # Parties
    parties = await fetch_all_pages(
        session, f"{BASE_URL}/parties/?docket={docket_id}&filter_nested_results=True",
        limiter, max_pages=10
    )
    for party in parties:
        for pt in party.get("party_types", []):
            conn.execute("""
                INSERT INTO cl_parties (
                    docket_id, party_id, name, party_type,
                    date_terminated, criminal_counts, extra_info
                ) VALUES (?,?,?,?,?,?,?)
            """, (
                docket_id, party.get("id"), party.get("name"),
                pt.get("name"), pt.get("date_terminated"),
                json.dumps(pt.get("criminal_counts")) if pt.get("criminal_counts") else None,
                party.get("extra_info"),
            ))

    # Attorneys
    attorneys = await fetch_all_pages(
        session, f"{BASE_URL}/attorneys/?docket={docket_id}&filter_nested_results=True",
        limiter, max_pages=10
    )
    for atty in attorneys:
        roles = [rep.get("role") for rep in atty.get("parties_represented", [])]
        conn.execute("""
            INSERT INTO cl_attorneys (
                docket_id, attorney_id, name, contact_raw,
                phone, fax, email, roles
            ) VALUES (?,?,?,?,?,?,?,?)
        """, (
            docket_id, atty.get("id"), atty.get("name"),
            atty.get("contact_raw"), atty.get("phone"),
            atty.get("fax"), atty.get("email"),
            json.dumps(roles) if roles else None,
        ))

    # Docket entries
    entries = await fetch_all_pages(
        session,
        f"{BASE_URL}/docket-entries/?docket={docket_id}&order_by=date_filed&page_size=20",
        limiter, max_pages=5,
    )
    for entry in entries:
        conn.execute("""
            INSERT INTO cl_docket_entries (
                docket_id, entry_id, entry_number, date_filed, description
            ) VALUES (?,?,?,?,?)
        """, (
            docket_id, entry.get("id"), entry.get("entry_number"),
            entry.get("date_filed"), entry.get("description"),
        ))

    # ── Step 18: RECAP documents (/recap-documents/) ──
    recap_docs = await fetch_all_pages(
        session, f"{BASE_URL}/recap-documents/?docket_entry__docket={docket_id}",
        limiter, max_pages=5,
    )
    for doc in recap_docs:
        conn.execute("""
            INSERT OR IGNORE INTO cl_recap_documents (
                id, docket_entry_id, docket_id, pacer_doc_id,
                document_type, description, is_available,
                filepath_local, filepath_ia, ocr_status, page_count
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            doc.get("id"), doc.get("docket_entry"), docket_id,
            doc.get("pacer_doc_id"), doc.get("document_type"),
            doc.get("description"), 1 if doc.get("is_available") else 0,
            doc.get("filepath_local"), doc.get("filepath_ia"),
            doc.get("ocr_status"), doc.get("page_count"),
        ))

    # ── Mark done ──
    conn.execute(
        "UPDATE cl_dockets SET scrape_status = 'done', scraped_at = datetime('now') WHERE docket_id = ?",
        (docket_id,),
    )
    conn.commit()
    return True


# ── Orchestration ───────────────────────────────────────────────────

async def run_sample(csv_path: Path, db_path: Path, seed: int) -> None:
    """Phase 1: Sample 100 cases and import into database."""
    conn = init_db(db_path)
    sampled = sample_cases(csv_path, seed)

    csv_fields = [
        "case_title", "citation", "court", "date", "petitioner", "respondent",
        "defendant_roles", "defendant_employer", "employer_crd_cik",
        "co_defendants", "relief_defendants", "sec_attorneys",
        "sec_regional_office", "judges", "judgment_type", "summary",
        "outcome", "legal_topic", "charges_and_sections", "company_domain",
        "total_fine_amount", "total_victim_losses", "scheme_duration",
        "scheme_method", "victim_count", "admission_status",
        "parallel_actions", "related_releases", "case_status",
        "scheme_start_date", "scheme_end_date", "complaint_filed_date",
        "judgment_date", "regulatory_registrations", "defendant_sentence",
        "final_judgment_details", "source_url", "pdf_insights",
        "associated_documents",
    ]

    inserted = 0
    for row in sampled:
        vals = [row.get(f, "") for f in csv_fields]
        vals.append(row.get("_stratum", ""))
        vals.append(row.get("_csv_row", 0))
        placeholders = ",".join(["?"] * (len(csv_fields) + 2))
        cols = ",".join(csv_fields + ["sample_stratum", "csv_row_index"])
        try:
            conn.execute(f"INSERT OR IGNORE INTO sec_cases ({cols}) VALUES ({placeholders})", vals)
            inserted += 1
        except sqlite3.IntegrityError:
            pass

    # Create match log entries
    conn.execute("""
        INSERT OR IGNORE INTO cl_match_log (sec_case_id, match_status)
        SELECT id, 'pending' FROM sec_cases
        WHERE id NOT IN (SELECT sec_case_id FROM cl_match_log)
    """)
    conn.commit()
    conn.close()
    logger.info(f"Imported {inserted} cases into sec_cases table")


async def run_match(db_path: Path) -> None:
    """Phase 2: Match SEC EDGAR cases to CourtListener."""
    conn = init_db(db_path)
    limiter = RateLimiter()

    pending = conn.execute("""
        SELECT s.id, s.case_title, s.respondent, s.court, s.date, s.citation
        FROM sec_cases s
        JOIN cl_match_log m ON s.id = m.sec_case_id
        WHERE m.match_status = 'pending'
    """).fetchall()

    logger.info(f"Matching {len(pending)} cases against CourtListener...")

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        for row in tqdm(pending, desc="Matching"):
            sec_row = dict(row)
            sec_case_id = sec_row["id"]

            result = await search_cl_for_case(session, limiter, sec_row)

            if result:
                has_opinion = "opinion" in result.get("match_method", "")
                has_docket = result.get("docket_id") is not None
                if has_opinion and has_docket:
                    status = "found_both"
                elif has_opinion:
                    status = "found_opinion"
                else:
                    status = "found_docket"

                conn.execute("""
                    UPDATE cl_match_log SET
                        match_status = ?,
                        match_method = ?,
                        cl_docket_id = ?,
                        cl_cluster_ids = ?,
                        confidence = ?,
                        search_queries_tried = ?,
                        match_notes = ?,
                        attempts = attempts + 1,
                        last_attempt_at = datetime('now'),
                        matched_at = datetime('now')
                    WHERE sec_case_id = ?
                """, (
                    status,
                    result.get("match_method", ""),
                    result.get("docket_id"),
                    str(result.get("cluster_id", "")),
                    result.get("confidence", ""),
                    json.dumps(result.get("queries_tried", [])),
                    f"score={result.get('score', 0):.2f}",
                    sec_case_id,
                ))
            else:
                conn.execute("""
                    UPDATE cl_match_log SET
                        match_status = 'not_found',
                        attempts = attempts + 1,
                        last_attempt_at = datetime('now')
                    WHERE sec_case_id = ?
                """, (sec_case_id,))

            conn.commit()

    conn.close()
    logger.info(f"Matching complete. Total API requests: {limiter.total_requests}")


async def run_scrape(db_path: Path) -> None:
    """Phase 3: Scrape all CL data for matched cases."""
    conn = init_db(db_path)
    limiter = RateLimiter()

    matched = conn.execute("""
        SELECT m.sec_case_id, m.cl_docket_id
        FROM cl_match_log m
        WHERE m.cl_docket_id IS NOT NULL
          AND m.cl_docket_id NOT IN (
              SELECT docket_id FROM cl_dockets WHERE scrape_status = 'done'
          )
    """).fetchall()

    logger.info(f"Scraping {len(matched)} matched cases...")

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
        for row in tqdm(matched, desc="Scraping"):
            sec_case_id = row["sec_case_id"]
            docket_id = row["cl_docket_id"]
            try:
                ok = await scrape_matched_case(session, conn, docket_id, sec_case_id, limiter)
                if ok:
                    logger.debug(f"Scraped docket {docket_id}")
                else:
                    logger.warning(f"Failed to scrape docket {docket_id}")
            except Exception as e:
                logger.error(f"Error scraping docket {docket_id}: {e}")
                conn.rollback()

    conn.close()
    logger.info(f"Scraping complete. Total API requests: {limiter.total_requests}")


async def run_status(db_path: Path) -> None:
    """Print match and scrape statistics."""
    conn = init_db(db_path)

    total = conn.execute("SELECT COUNT(*) FROM sec_cases").fetchone()[0]
    print(f"\n{'='*60}")
    print(f"SEC EDGAR → CourtListener Enrichment Status")
    print(f"{'='*60}")
    print(f"\nSEC EDGAR cases sampled: {total}")

    print(f"\n--- Match Status ---")
    for row in conn.execute("""
        SELECT match_status, COUNT(*) as cnt FROM cl_match_log GROUP BY match_status ORDER BY cnt DESC
    """):
        print(f"  {row['match_status']:<20} {row['cnt']:>4}")

    print(f"\n--- Match Methods ---")
    for row in conn.execute("""
        SELECT match_method, COUNT(*) as cnt FROM cl_match_log
        WHERE match_method != '' GROUP BY match_method ORDER BY cnt DESC
    """):
        print(f"  {row['match_method']:<35} {row['cnt']:>4}")

    print(f"\n--- Scrape Status ---")
    scraped = conn.execute("SELECT COUNT(*) FROM cl_dockets WHERE scrape_status = 'done'").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM cl_dockets WHERE scrape_status = 'pending'").fetchone()[0]
    print(f"  done:    {scraped}")
    print(f"  pending: {pending}")

    print(f"\n--- Data Collected ---")
    tables = [
        ("cl_opinions", "Opinions"),
        ("cl_citation_edges", "Citation edges"),
        ("cl_parties", "Parties"),
        ("cl_attorneys", "Attorneys"),
        ("cl_docket_entries", "Docket entries"),
        ("cl_judges", "Judges"),
        ("cl_judge_financials", "Judge financials"),
        ("cl_courts", "Courts"),
        ("cl_originating_court", "Originating court"),
        ("cl_citation_lookups", "Citation lookups"),
        ("cl_audio", "Audio recordings"),
        ("cl_recap_documents", "RECAP documents"),
        ("cl_bankruptcy", "Bankruptcy info"),
    ]
    for table, label in tables:
        cnt = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if cnt > 0:
            print(f"  {label:<25} {cnt:>6}")

    opinions_with_text = conn.execute(
        "SELECT COUNT(*) FROM cl_opinions WHERE length(html_with_citations) > 0"
    ).fetchone()[0]
    print(f"\n  Opinions with full text: {opinions_with_text}")

    conn.close()


async def run_full(csv_path: Path, db_path: Path, seed: int) -> None:
    """Run all phases: sample → match → scrape."""
    logger.info("Phase 1: Sampling...")
    await run_sample(csv_path, db_path, seed)
    logger.info("Phase 2: Matching...")
    await run_match(db_path)
    logger.info("Phase 3: Scraping...")
    await run_scrape(db_path)
    logger.info("Phase 4: Status report...")
    await run_status(db_path)


# ── CLI ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="SEC EDGAR → CourtListener enrichment scraper")
    parser.add_argument("--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    p_full = sub.add_parser("full", help="Run all phases: sample → match → scrape")
    p_full.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    p_full.add_argument("--db", type=Path, default=DEFAULT_DB)
    p_full.add_argument("--seed", type=int, default=SAMPLE_SEED)

    p_sample = sub.add_parser("sample", help="Sample 100 cases from CSV")
    p_sample.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    p_sample.add_argument("--db", type=Path, default=DEFAULT_DB)
    p_sample.add_argument("--seed", type=int, default=SAMPLE_SEED)

    p_match = sub.add_parser("match", help="Match sampled cases to CourtListener")
    p_match.add_argument("--db", type=Path, default=DEFAULT_DB)

    p_scrape = sub.add_parser("scrape", help="Scrape CL data for matched cases")
    p_scrape.add_argument("--db", type=Path, default=DEFAULT_DB)

    p_status = sub.add_parser("status", help="Print status report")
    p_status.add_argument("--db", type=Path, default=DEFAULT_DB)

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command == "full":
        asyncio.run(run_full(args.csv, args.db, args.seed))
    elif args.command == "sample":
        asyncio.run(run_sample(args.csv, args.db, args.seed))
    elif args.command == "match":
        asyncio.run(run_match(args.db))
    elif args.command == "scrape":
        asyncio.run(run_scrape(args.db))
    elif args.command == "status":
        asyncio.run(run_status(args.db))


if __name__ == "__main__":
    main()
