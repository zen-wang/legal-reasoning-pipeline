"""
Private 10b-5 Case Scraper — CourtListener API v4
==================================================
Scrapes private securities fraud (Rule 10b-5) cases from CourtListener.

Strategy:
  Pass 1: Discover ~3,400 docket_ids that have opinions (via opinion search)
  Pass 2: Scrape full data for opinion-bearing cases (priority)
  Pass 3: Discover remaining docket_ids (via docket search)
  Pass 4: Scrape metadata for non-opinion cases

Rate limit: 5,000 req/hr (single EDU token). Uses async with concurrency=5.
Checkpoint: SQLite — crash-safe, resume from where it left off.

Usage:
  python scraper_private_10b5.py                  # full scrape
  python scraper_private_10b5.py --tier golden    # 200 cases for testing
  python scraper_private_10b5.py --tier opinions  # 3,400 opinion cases only
  python scraper_private_10b5.py --tier all       # all ~10,200 cases
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, quote

import aiohttp
from tqdm import tqdm

# ── Config ──────────────────────────────────────────────────────────

BASE_URL = "https://www.courtlistener.com/api/rest/v4"

# Load token from .env file
import os
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

DB_PATH = Path(__file__).parent.parent / "data" / "private_10b5_cases.db"

# Rate limiting: 5,000 req/hr = ~1.39/sec. Sequential with safe interval.
MAX_CONCURRENCY = 1
REQUEST_INTERVAL = 1.5  # seconds between requests (~0.67 req/sec, safe margin)
MAX_RETRIES = 5
RETRY_BACKOFF = 10.0  # seconds base backoff on 429/5xx
COOLDOWN_ON_429 = 60.0  # seconds to pause after any 429 error

SEARCH_QUERY_OPINIONS = '"10b-5" -"Securities and Exchange Commission"'
SEARCH_QUERY_DOCKETS = '"10b-5" -"Securities and Exchange Commission"'
NATURE_OF_SUIT = "850"

TIER_LIMITS = {
    "golden": 200,
    "opinions": None,  # all opinion-bearing cases
    "all": None,       # everything
}

logger = logging.getLogger("scraper_10b5")


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
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cases (
            docket_id       INTEGER PRIMARY KEY,
            case_name       TEXT,
            docket_number   TEXT,
            pacer_case_id   TEXT,
            slug            TEXT,
            absolute_url    TEXT,
            court_id        TEXT,
            cause           TEXT,
            nature_of_suit  TEXT,
            jurisdiction_type TEXT,
            date_filed      TEXT,
            date_terminated TEXT,
            date_last_filing TEXT,
            assigned_to_str TEXT,
            referred_to_str TEXT,
            jury_demand     TEXT,
            -- idb_data fields
            idb_disposition      INTEGER,
            idb_judgment         INTEGER,
            idb_procedural_progress INTEGER,
            idb_nature_of_suit   INTEGER,
            idb_monetary_demand  REAL,
            idb_pro_se           INTEGER,
            idb_class_action     INTEGER,
            idb_origin           INTEGER,
            idb_jury_demand      TEXT,
            -- metadata
            has_opinions    INTEGER DEFAULT 0,
            scrape_status   TEXT DEFAULT 'pending',
            scraped_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS opinions (
            opinion_id      INTEGER PRIMARY KEY,
            docket_id       INTEGER REFERENCES cases(docket_id),
            cluster_id      INTEGER,
            plain_text      TEXT,
            type            TEXT,
            author_str      TEXT,
            per_curiam      INTEGER,
            download_url    TEXT,
            -- cluster metadata
            cluster_date_filed      TEXT,
            precedential_status     TEXT,
            citation_count          INTEGER,
            syllabus                TEXT,
            disposition             TEXT,
            posture                 TEXT,
            procedural_history      TEXT
        );

        CREATE TABLE IF NOT EXISTS citation_edges (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source_opinion_id INTEGER,
            cited_opinion_url TEXT,
            UNIQUE(source_opinion_id, cited_opinion_url)
        );

        CREATE TABLE IF NOT EXISTS parties (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            docket_id       INTEGER REFERENCES cases(docket_id),
            party_id        INTEGER,
            name            TEXT,
            party_type      TEXT,
            date_terminated TEXT,
            criminal_counts TEXT,
            extra_info      TEXT
        );

        CREATE TABLE IF NOT EXISTS attorneys (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            docket_id       INTEGER REFERENCES cases(docket_id),
            attorney_id     INTEGER,
            name            TEXT,
            contact_raw     TEXT,
            phone           TEXT,
            fax             TEXT,
            email           TEXT,
            roles           TEXT
        );

        CREATE TABLE IF NOT EXISTS docket_entries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            docket_id       INTEGER REFERENCES cases(docket_id),
            entry_id        INTEGER,
            entry_number    INTEGER,
            date_filed      TEXT,
            description     TEXT
        );

        CREATE TABLE IF NOT EXISTS scrape_progress (
            key             TEXT PRIMARY KEY,
            value           TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_opinions_docket ON opinions(docket_id);
        CREATE INDEX IF NOT EXISTS idx_parties_docket ON parties(docket_id);
        CREATE INDEX IF NOT EXISTS idx_attorneys_docket ON attorneys(docket_id);
        CREATE INDEX IF NOT EXISTS idx_entries_docket ON docket_entries(docket_id);
        CREATE INDEX IF NOT EXISTS idx_citations_source ON citation_edges(source_opinion_id);
    """)
    conn.commit()
    return conn


def is_case_scraped(conn: sqlite3.Connection, docket_id: int) -> bool:
    row = conn.execute(
        "SELECT scrape_status FROM cases WHERE docket_id = ? AND scrape_status = 'done'",
        (docket_id,),
    ).fetchone()
    return row is not None


def get_scraped_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM cases WHERE scrape_status = 'done'").fetchone()
    return row[0] if row else 0


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
                    logger.warning(f"Access denied (403): {url}")
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


# ── Discovery ───────────────────────────────────────────────────────

async def discover_opinion_dockets(
    session: aiohttp.ClientSession,
    limiter: RateLimiter,
    limit: int | None = None,
) -> list[int]:
    """Search opinions API to find docket_ids that have 10b-5 opinions."""
    logger.info("Discovering opinion-bearing dockets...")
    docket_ids: set[int] = set()

    params = {
        "type": "o",
        "q": SEARCH_QUERY_OPINIONS,
    }
    url = f"{BASE_URL}/search/?{urlencode(params)}"

    page = 0
    while url:
        data = await fetch_json(session, url, limiter)
        if not data:
            break
        for r in data.get("results", []):
            did = r.get("docket_id")
            if did:
                docket_ids.add(did)
                if limit and len(docket_ids) >= limit:
                    logger.info(f"Reached limit of {limit} opinion dockets.")
                    return sorted(docket_ids)
        url = data.get("next")
        page += 1
        if page % 10 == 0:
            logger.info(f"  ...discovered {len(docket_ids)} opinion dockets (page {page})")

    logger.info(f"Total opinion-bearing dockets: {len(docket_ids)}")
    return sorted(docket_ids)


async def discover_all_dockets(
    session: aiohttp.ClientSession,
    limiter: RateLimiter,
    exclude: set[int] | None = None,
) -> list[int]:
    """Search dockets API to find all private 10b-5 docket_ids."""
    logger.info("Discovering all dockets (NOS=850)...")
    docket_ids: set[int] = set()
    exclude = exclude or set()

    params = {
        "type": "d",
        "q": SEARCH_QUERY_DOCKETS,
        "nature_of_suit": NATURE_OF_SUIT,
    }
    url = f"{BASE_URL}/search/?{urlencode(params)}"

    page = 0
    while url:
        data = await fetch_json(session, url, limiter)
        if not data:
            break
        for r in data.get("results", []):
            did = r.get("docket_id")
            if did and did not in exclude:
                docket_ids.add(did)
        url = data.get("next")
        page += 1
        if page % 10 == 0:
            logger.info(f"  ...discovered {len(docket_ids)} new dockets (page {page})")

    logger.info(f"Additional non-opinion dockets: {len(docket_ids)}")
    return sorted(docket_ids)


# ── Per-Case Scraping ───────────────────────────────────────────────

async def scrape_case(
    session: aiohttp.ClientSession,
    conn: sqlite3.Connection,
    docket_id: int,
    limiter: RateLimiter,
    fetch_opinions: bool = True,
) -> bool:
    """Scrape all data for a single case and save to SQLite."""
    if is_case_scraped(conn, docket_id):
        return True

    # 1. Docket details
    docket = await fetch_json(session, f"{BASE_URL}/dockets/{docket_id}/", limiter)
    if not docket:
        return False

    idb = docket.get("idb_data") or {}

    conn.execute("""
        INSERT OR REPLACE INTO cases (
            docket_id, case_name, docket_number, pacer_case_id, slug,
            absolute_url, court_id, cause, nature_of_suit, jurisdiction_type,
            date_filed, date_terminated, date_last_filing,
            assigned_to_str, referred_to_str, jury_demand,
            idb_disposition, idb_judgment, idb_procedural_progress,
            idb_nature_of_suit, idb_monetary_demand, idb_pro_se,
            idb_class_action, idb_origin, idb_jury_demand,
            has_opinions, scrape_status, scraped_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        docket_id,
        docket.get("case_name"),
        docket.get("docket_number"),
        docket.get("pacer_case_id"),
        docket.get("slug"),
        docket.get("absolute_url"),
        docket.get("court_id"),
        docket.get("cause"),
        docket.get("nature_of_suit"),
        docket.get("jurisdiction_type"),
        docket.get("date_filed"),
        docket.get("date_terminated"),
        docket.get("date_last_filing"),
        docket.get("assigned_to_str"),
        docket.get("referred_to_str"),
        docket.get("jury_demand"),
        idb.get("disposition"),
        idb.get("judgment"),
        idb.get("procedural_progress"),
        idb.get("nature_of_suit"),
        idb.get("monetary_demand"),
        idb.get("pro_se"),
        idb.get("class_action"),
        idb.get("origin"),
        idb.get("jury_demand"),
        1 if fetch_opinions else 0,
        "pending",
        None,
    ))

    # 2. Opinion clusters + opinions
    if fetch_opinions:
        clusters = await fetch_all_pages(
            session, f"{BASE_URL}/clusters/?docket={docket_id}", limiter, max_pages=5
        )
        for cluster in clusters:
            cluster_id = cluster.get("id")
            sub_opinions = cluster.get("sub_opinions", [])

            for op_url in sub_opinions:
                op = await fetch_json(session, op_url, limiter)
                if not op:
                    continue

                opinion_id = op.get("id")
                opinions_cited = op.get("opinions_cited", [])

                conn.execute("""
                    INSERT OR REPLACE INTO opinions (
                        opinion_id, docket_id, cluster_id, plain_text, type,
                        author_str, per_curiam, download_url,
                        cluster_date_filed, precedential_status, citation_count,
                        syllabus, disposition, posture, procedural_history
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    opinion_id,
                    docket_id,
                    cluster_id,
                    op.get("plain_text"),
                    op.get("type"),
                    op.get("author_str"),
                    1 if op.get("per_curiam") else 0,
                    op.get("download_url"),
                    cluster.get("date_filed"),
                    cluster.get("precedential_status"),
                    cluster.get("citation_count"),
                    cluster.get("syllabus"),
                    cluster.get("disposition"),
                    cluster.get("posture"),
                    cluster.get("procedural_history"),
                ))

                # Citation edges
                for cited_url in opinions_cited:
                    conn.execute("""
                        INSERT OR IGNORE INTO citation_edges
                        (source_opinion_id, cited_opinion_url) VALUES (?, ?)
                    """, (opinion_id, cited_url))

    # 3. Parties
    parties = await fetch_all_pages(
        session, f"{BASE_URL}/parties/?docket={docket_id}", limiter, max_pages=10
    )
    for party in parties:
        party_types = party.get("party_types", [])
        for pt in party_types:
            if pt.get("docket_id") != docket_id:
                continue
            criminal = pt.get("criminal_counts", [])
            conn.execute("""
                INSERT INTO parties (
                    docket_id, party_id, name, party_type,
                    date_terminated, criminal_counts, extra_info
                ) VALUES (?,?,?,?,?,?,?)
            """, (
                docket_id,
                party.get("id"),
                party.get("name"),
                pt.get("name"),
                pt.get("date_terminated"),
                json.dumps(criminal) if criminal else None,
                party.get("extra_info"),
            ))

    # 4. Attorneys
    attorneys = await fetch_all_pages(
        session, f"{BASE_URL}/attorneys/?docket={docket_id}", limiter, max_pages=10
    )
    for atty in attorneys:
        roles = []
        for rep in atty.get("parties_represented", []):
            if rep.get("docket_id") == docket_id:
                roles.append(rep.get("role"))
        conn.execute("""
            INSERT INTO attorneys (
                docket_id, attorney_id, name, contact_raw,
                phone, fax, email, roles
            ) VALUES (?,?,?,?,?,?,?,?)
        """, (
            docket_id,
            atty.get("id"),
            atty.get("name"),
            atty.get("contact_raw"),
            atty.get("phone"),
            atty.get("fax"),
            atty.get("email"),
            json.dumps(roles) if roles else None,
        ))

    # 5. Docket entries (first 100 — covers most cases)
    entries = await fetch_all_pages(
        session,
        f"{BASE_URL}/docket-entries/?docket={docket_id}&order_by=date_filed&page_size=20",
        limiter,
        max_pages=5,
    )
    for entry in entries:
        conn.execute("""
            INSERT INTO docket_entries (
                docket_id, entry_id, entry_number, date_filed, description
            ) VALUES (?,?,?,?,?)
        """, (
            docket_id,
            entry.get("id"),
            entry.get("entry_number"),
            entry.get("date_filed"),
            entry.get("description"),
        ))

    # Mark done
    conn.execute(
        "UPDATE cases SET scrape_status = 'done', scraped_at = datetime('now') WHERE docket_id = ?",
        (docket_id,),
    )
    conn.commit()
    return True


# ── Main Orchestration ──────────────────────────────────────────────

async def run_scrape(tier: str = "all", db_path: Path = DB_PATH) -> None:
    limiter = RateLimiter()
    conn = init_db(db_path)
    already_done = get_scraped_count(conn)
    logger.info(f"Database: {DB_PATH}")
    logger.info(f"Already scraped: {already_done} cases")

    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:

        # ── Pass 1: Discover opinion-bearing dockets ──
        limit = TIER_LIMITS.get(tier)
        opinion_docket_ids = await discover_opinion_dockets(session, limiter, limit=limit)

        # Filter out already-scraped
        opinion_todo = [d for d in opinion_docket_ids if not is_case_scraped(conn, d)]
        logger.info(f"Opinion cases to scrape: {len(opinion_todo)} (skipping {len(opinion_docket_ids) - len(opinion_todo)} done)")

        # ── Pass 2: Scrape opinion cases ──
        sem = asyncio.Semaphore(MAX_CONCURRENCY)
        failed: list[int] = []

        async def scrape_with_sem(docket_id: int, fetch_op: bool) -> None:
            async with sem:
                ok = await scrape_case(session, conn, docket_id, limiter, fetch_opinions=fetch_op)
                if not ok:
                    failed.append(docket_id)
                pbar.update(1)

        logger.info(f"\n{'='*60}")
        logger.info(f"PASS 1: Scraping {len(opinion_todo)} opinion-bearing cases")
        logger.info(f"{'='*60}")

        with tqdm(total=len(opinion_todo), desc="Opinion cases", unit="case") as pbar:
            # Process in batches to avoid overwhelming the event loop
            batch_size = MAX_CONCURRENCY * 2
            for i in range(0, len(opinion_todo), batch_size):
                batch = opinion_todo[i:i + batch_size]
                tasks = [scrape_with_sem(d, True) for d in batch]
                await asyncio.gather(*tasks)

        logger.info(f"Opinion cases done. Failed: {len(failed)}")

        # ── Pass 3 & 4: Remaining dockets (metadata only) ──
        if tier == "all":
            opinion_set = set(opinion_docket_ids)
            remaining_ids = await discover_all_dockets(session, limiter, exclude=opinion_set)
            remaining_todo = [d for d in remaining_ids if not is_case_scraped(conn, d)]
            logger.info(f"\n{'='*60}")
            logger.info(f"PASS 2: Scraping {len(remaining_todo)} metadata-only cases")
            logger.info(f"{'='*60}")

            with tqdm(total=len(remaining_todo), desc="Metadata cases", unit="case") as pbar:
                for i in range(0, len(remaining_todo), batch_size):
                    batch = remaining_todo[i:i + batch_size]
                    tasks = [scrape_with_sem(d, False) for d in batch]
                    await asyncio.gather(*tasks)

            logger.info(f"Metadata cases done. Failed: {len(failed)}")

    # ── Summary ──
    total = get_scraped_count(conn)
    opinion_count = conn.execute("SELECT COUNT(*) FROM opinions").fetchone()[0]
    party_count = conn.execute("SELECT COUNT(*) FROM parties").fetchone()[0]
    attorney_count = conn.execute("SELECT COUNT(*) FROM attorneys").fetchone()[0]
    entry_count = conn.execute("SELECT COUNT(*) FROM docket_entries").fetchone()[0]
    citation_count = conn.execute("SELECT COUNT(*) FROM citation_edges").fetchone()[0]

    conn.close()

    logger.info(f"\n{'='*60}")
    logger.info(f"SCRAPE COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"  Cases:          {total}")
    logger.info(f"  Opinions:       {opinion_count}")
    logger.info(f"  Parties:        {party_count}")
    logger.info(f"  Attorneys:      {attorney_count}")
    logger.info(f"  Docket entries: {entry_count}")
    logger.info(f"  Citation edges: {citation_count}")
    logger.info(f"  Failed:         {len(failed)}")
    logger.info(f"  API requests:   {limiter.total_requests}")
    logger.info(f"  Database:       {db_path} ({db_path.stat().st_size / 1024 / 1024:.1f} MB)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Private 10b-5 cases from CourtListener")
    parser.add_argument(
        "--tier",
        choices=["golden", "opinions", "all"],
        default="golden",
        help="Scraping tier: golden (50 cases), opinions (~3,400), all (~10,200)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help=f"SQLite database path (default: {DB_PATH})",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.db != DB_PATH:
        # Override module-level DB_PATH via the run function
        pass
    db_path = args.db

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info(f"Tier: {args.tier}")
    logger.info(f"Database: {db_path}")

    asyncio.run(run_scrape(tier=args.tier, db_path=db_path))


if __name__ == "__main__":
    main()
