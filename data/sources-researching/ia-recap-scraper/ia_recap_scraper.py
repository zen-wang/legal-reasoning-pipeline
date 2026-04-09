#!/usr/bin/env python3
"""
IA RECAP Scraper — Scalable Pipeline for Internet Archive RECAP Collection
============================================================================
Production pipeline with SQLite backend, async worker pool,
checkpoint/resume, deduplication, and triple-format export.

Scrapes federal court case data from Internet Archive's RECAP collection
(collection:usfederalcourts) and CourtListener docket JSON files.

Architecture:
  IA Search → Case List → Metadata + Docket JSON → Field Extraction → SQLite → Export

Features:
  - SQLite checkpoint: crash-safe, resume from last case
  - Async curl workers: parallel fetching
  - URL dedup: run multiple times safely
  - Incremental mode: only scrape new cases
  - Triple export: CSV, JSON (Neo4j-ready), SQLite
  - Full 63-field unified schema (matching SEC EDGAR + IA-RECAP-only fields)
  - FJC Integrated Database codes decoded to human-readable text

Dependencies:
    pip install requests  (only for fallback; primary transport is async curl)

Commands:
    python ia_recap_scraper.py scrape --query "Securities AND Exchange AND Commission" --max 50
    python ia_recap_scraper.py scrape --nos 850 --max 100 --workers 3
    python ia_recap_scraper.py scrape --identifier gov.uscourts.nysd.524448
    python ia_recap_scraper.py scrape --all --workers 5
    python ia_recap_scraper.py export --format csv -o ia_recap_cases.csv
    python ia_recap_scraper.py export --format json -o ia_recap_cases.json
    python ia_recap_scraper.py export --format both -o ia_recap_cases
    python ia_recap_scraper.py status
"""

import asyncio
import csv
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import argparse
import time
from dataclasses import dataclass, asdict, fields as dc_fields
from pathlib import Path
from typing import Optional
from urllib.parse import quote

# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

IA_SEARCH_URL   = "https://archive.org/advancedsearch.php"
IA_METADATA_URL = "https://archive.org/metadata"
IA_DOWNLOAD_URL = "https://archive.org/download"
CL_BASE_URL     = "https://www.courtlistener.com"

DB_FILE         = "ia_recap.db"
REQUEST_DELAY   = 1.0
CURL_TIMEOUT    = 60
MAX_RETRIES     = 3
DEFAULT_WORKERS = 3
MAX_WORKERS     = 6

UA = "IA-RECAP-Scraper/1.0 (Academic Research; ASU CIPS Lab; wwang360@asu.edu)"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ia_recap")


# ═══════════════════════════════════════════════════════════════════════════════
#  FJC LOOKUP TABLES
# ═══════════════════════════════════════════════════════════════════════════════

DISPOSITION_CODES = {
    0: "Ongoing / not terminated",
    1: "Transfer to another district",
    2: "Remanded",
    3: "Dismissed - want of prosecution",
    4: "Dismissed - lack of jurisdiction",
    5: "Dismissed - settled",
    6: "Dismissed - voluntarily",
    7: "Dismissed - other",
    8: "Default judgment",
    9: "Consent judgment",
    10: "Judgment on motion before trial",
    11: "Judgment on jury verdict",
    12: "Judgment on directed verdict",
    13: "Judgment on court trial",
    14: "Judgment on appeal of magistrate decision",
    15: "Multi-district litigation transfer",
    18: "Statistical closing",
    19: "Other",
}

JUDGMENT_CODES = {
    0: "No judgment",
    1: "Plaintiff",
    2: "Defendant",
    3: "Both",
    4: "Unknown",
}


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA MODEL — 63 fields (unified schema from IA_RECAP_vs_SEC_EDGAR_data_field.md)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class RECAPCase:
    # --- Fields shared with SEC EDGAR (13) ---
    case_title: str = ""
    court: str = ""
    date: str = ""
    judges: str = ""
    judgment_type: str = ""
    outcome: str = ""
    legal_topic: str = ""
    charges_and_sections: str = ""
    case_status: str = ""
    complaint_filed_date: str = ""
    judgment_date: str = ""
    source_url: str = ""
    associated_documents: str = ""

    # --- SEC-only fields (16) — left empty, filled if PDF extraction added later ---
    citation: str = ""
    defendant_employer: str = ""
    employer_crd_cik: str = ""
    summary: str = ""
    company_domain: str = ""
    total_victim_losses: str = ""
    scheme_duration: str = ""
    scheme_method: str = ""
    victim_count: str = ""
    admission_status: str = ""
    parallel_actions: str = ""
    related_releases: str = ""
    scheme_start_date: str = ""
    scheme_end_date: str = ""
    regulatory_registrations: str = ""
    pdf_insights: str = ""

    # --- IA-RECAP-partial fields (10) ---
    petitioner: str = ""
    respondent: str = ""
    defendant_roles: str = ""
    co_defendants: str = ""
    relief_defendants: str = ""
    sec_attorneys: str = ""
    sec_regional_office: str = ""
    total_fine_amount: str = ""
    defendant_sentence: str = ""
    final_judgment_details: str = ""

    # --- IA-RECAP-only fields (24) ---
    docket_number: str = ""
    pacer_case_id: str = ""
    jurisdiction_type: str = ""
    jury_demand: str = ""
    disposition_code: str = ""
    judgment_code: str = ""
    procedural_progress: str = ""
    case_origin: str = ""
    monetary_demand: str = ""
    class_action_flag: str = ""
    diversity_of_residence: str = ""
    pro_se: str = ""
    arbitration_at_filing: str = ""
    arbitration_at_termination: str = ""
    county_of_residence: str = ""
    date_last_filing: str = ""
    date_terminated: str = ""
    attorney_phone: str = ""
    attorney_email: str = ""
    doc_page_count: str = ""
    doc_file_size: str = ""
    doc_is_sealed: str = ""
    doc_ocr_status: str = ""
    docket_entry_text: str = ""


FIELD_NAMES = [f.name for f in dc_fields(RECAPCase)]


# ═══════════════════════════════════════════════════════════════════════════════
#  SQLITE DATABASE LAYER
# ═══════════════════════════════════════════════════════════════════════════════

class Database:
    """SQLite backend with WAL mode for concurrent access."""

    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._create_tables()

    def _create_tables(self):
        cols = ", ".join(f'"{f}" TEXT DEFAULT ""' for f in FIELD_NAMES)
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                {cols},
                scraped_at TEXT DEFAULT (datetime('now')),
                UNIQUE(source_url)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS scrape_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT DEFAULT '',
                timestamp TEXT DEFAULT (datetime('now'))
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cases_url ON cases(source_url)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cases_date ON cases(date)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cases_court ON cases(court)"
        )
        self.conn.commit()

    def url_exists(self, url: str) -> bool:
        r = self.conn.execute(
            "SELECT 1 FROM cases WHERE source_url = ?", (url,)
        ).fetchone()
        return r is not None

    def insert_case(self, case: RECAPCase):
        d = asdict(case)
        cols = ", ".join(f'"{k}"' for k in d.keys())
        placeholders = ", ".join("?" for _ in d)
        try:
            self.conn.execute(
                f"INSERT OR REPLACE INTO cases ({cols}) VALUES ({placeholders})",
                list(d.values()),
            )
            self.conn.commit()
        except sqlite3.Error as e:
            log.error(f"DB insert error: {e}")

    def log_scrape(self, url: str, status: str, message: str = ""):
        try:
            self.conn.execute(
                "INSERT INTO scrape_log (url, status, message) VALUES (?, ?, ?)",
                (url, status, message),
            )
            self.conn.commit()
        except sqlite3.Error:
            pass

    def case_count(self) -> int:
        r = self.conn.execute("SELECT COUNT(*) FROM cases").fetchone()
        return r[0] if r else 0

    def latest_date(self) -> str:
        r = self.conn.execute(
            "SELECT date FROM cases ORDER BY scraped_at DESC LIMIT 1"
        ).fetchone()
        return r[0] if r else ""

    def export_csv(self, path: str):
        col_str = ", ".join(f'"{f}"' for f in FIELD_NAMES)
        rows = self.conn.execute(
            f"SELECT {col_str} FROM cases ORDER BY scraped_at DESC"
        ).fetchall()
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, quoting=csv.QUOTE_ALL)
            w.writerow(FIELD_NAMES)
            w.writerows(rows)
        return len(rows)

    def export_json(self, path: str):
        col_str = ", ".join(f'"{f}"' for f in FIELD_NAMES)
        rows = self.conn.execute(
            f"SELECT {col_str} FROM cases ORDER BY scraped_at DESC"
        ).fetchall()
        # Fields that should be arrays in JSON export
        array_fields = {
            "co_defendants", "sec_attorneys", "defendant_roles",
            "charges_and_sections", "legal_topic", "judges",
            "associated_documents", "attorney_phone", "attorney_email",
        }
        cases = []
        for row in rows:
            case = {}
            for i, field in enumerate(FIELD_NAMES):
                val = row[i] or ""
                if field in array_fields:
                    case[field] = [v.strip() for v in val.split(";") if v.strip()] if val else []
                else:
                    case[field] = val
            cases.append(case)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cases, f, indent=2, ensure_ascii=False)
        return len(cases)

    def status(self) -> dict:
        total = self.case_count()
        latest = self.latest_date()
        errors = self.conn.execute(
            "SELECT COUNT(*) FROM scrape_log WHERE status = 'error'"
        ).fetchone()[0]
        success = self.conn.execute(
            "SELECT COUNT(*) FROM scrape_log WHERE status = 'success'"
        ).fetchone()[0]
        topics = self.conn.execute(
            "SELECT legal_topic, COUNT(*) as cnt FROM cases WHERE legal_topic != '' "
            "GROUP BY legal_topic ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
        courts = self.conn.execute(
            "SELECT court, COUNT(*) as cnt FROM cases WHERE court != '' "
            "GROUP BY court ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
        dispositions = self.conn.execute(
            "SELECT disposition_code, COUNT(*) as cnt FROM cases WHERE disposition_code != '' "
            "GROUP BY disposition_code ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
        return {
            "total_cases": total,
            "latest_date": latest,
            "scrape_success": success,
            "scrape_errors": errors,
            "top_topics": topics,
            "top_courts": courts,
            "top_dispositions": dispositions,
            "db_size_mb": round(os.path.getsize(self.db_path) / 1024 / 1024, 2)
                if os.path.exists(self.db_path) else 0,
        }

    def close(self):
        self.conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  ASYNC CURL HTTP LAYER
# ═══════════════════════════════════════════════════════════════════════════════

async def curl_fetch(url: str, retries: int = MAX_RETRIES,
                     semaphore: asyncio.Semaphore = None) -> Optional[str]:
    """Async curl fetch with retries."""
    sem = semaphore or asyncio.Semaphore(1)

    for attempt in range(retries):
        async with sem:
            cmd = [
                "curl", "-s", "-S", "-L",
                "--max-time", str(CURL_TIMEOUT),
                "--compressed",
                "-H", f"User-Agent: {UA}",
                "-H", "Accept: application/json,*/*",
                "-w", "\n%{http_code}",
                url,
            ]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=CURL_TIMEOUT + 15
                )
                out = stdout.decode("utf-8", errors="replace")
                parts = out.rsplit("\n", 1)
                body, status = (parts[0], parts[1].strip()) if len(parts) == 2 else (out, "000")

                if status == "200":
                    return body
                log.warning(f"  HTTP {status} (attempt {attempt+1}): {url[:80]}")

            except asyncio.TimeoutError:
                log.warning(f"  Timeout (attempt {attempt+1}): {url[:80]}")
                if proc.returncode is None:
                    proc.kill()
            except Exception as e:
                log.warning(f"  Error (attempt {attempt+1}): {e}")

        if attempt < retries - 1:
            await asyncio.sleep(3 * (attempt + 1))

    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  IA SEARCH — find case identifiers
# ═══════════════════════════════════════════════════════════════════════════════

async def ia_search(query: str, max_results: int = 50,
                    sem: asyncio.Semaphore = None) -> list:
    """Search IA for RECAP case identifiers."""
    raw_q = f"collection:usfederalcourts AND {query}"
    # IA search expects spaces as + and allows ():
    encoded_q = quote(raw_q, safe="():+")
    encoded_q = encoded_q.replace(" ", "+")
    params = (
        f"q={encoded_q}"
        f"&fl[]=identifier&fl[]=title&fl[]=date"
        f"&sort[]=addeddate+desc"
        f"&rows={max_results}&output=json"
    )
    url = f"{IA_SEARCH_URL}?{params}"
    body = await curl_fetch(url, semaphore=sem)
    if not body:
        return []
    try:
        data = json.loads(body)
        return data.get("response", {}).get("docs", [])
    except json.JSONDecodeError:
        log.error("  Failed to parse IA search response")
        return []


async def ia_search_by_nos(nos_code: int, max_results: int = 100,
                           sem: asyncio.Semaphore = None) -> list:
    """Search IA for RECAP cases by Nature of Suit code via title heuristics."""
    nos_queries = {
        850: "title:(Securities OR SEC OR stock OR fraud OR insider)",
        470: "title:(RICO OR racketeering)",
        830: "title:(patent OR infringement)",
        890: "title:(trademark OR copyright)",
        360: "title:(civil rights OR discrimination)",
    }
    query = nos_queries.get(nos_code, f"title:({nos_code})")
    return await ia_search(query, max_results, sem)


# ═══════════════════════════════════════════════════════════════════════════════
#  DOCKET JSON FETCHER — get structured case data
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_docket_json(identifier: str,
                            sem: asyncio.Semaphore = None) -> Optional[dict]:
    """Fetch docket.json from IA for a given case identifier."""
    # First, get the file list to find the docket.json filename
    meta_url = f"{IA_METADATA_URL}/{identifier}/files"
    body = await curl_fetch(meta_url, semaphore=sem)
    if not body:
        return None

    try:
        files = json.loads(body).get("result", [])
    except json.JSONDecodeError:
        return None

    # Find docket.json file
    docket_file = None
    for f in files:
        name = f.get("name", "")
        if name.endswith(".docket.json"):
            docket_file = name
            break

    if not docket_file:
        return None

    # Download docket.json
    docket_url = f"{IA_DOWNLOAD_URL}/{identifier}/{docket_file}"
    await asyncio.sleep(REQUEST_DELAY)
    body = await curl_fetch(docket_url, semaphore=sem)
    if not body:
        return None

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        log.error(f"  Failed to parse docket.json for {identifier}")
        return None


async def fetch_ia_metadata(identifier: str,
                            sem: asyncio.Semaphore = None) -> Optional[dict]:
    """Fetch IA item metadata."""
    url = f"{IA_METADATA_URL}/{identifier}"
    body = await curl_fetch(url, semaphore=sem)
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  FIELD EXTRACTOR — docket.json → RECAPCase
# ═══════════════════════════════════════════════════════════════════════════════

def _s(val) -> str:
    """Safely convert any value to string."""
    if val is None:
        return ""
    return str(val)


def extract_case(identifier: str, docket: dict, ia_meta: Optional[dict]) -> RECAPCase:
    """Extract all 63 fields from docket.json + IA metadata into RECAPCase."""
    case = RECAPCase()

    # --- Shared fields ---
    case.case_title = _s(docket.get("case_name", ""))
    case.court = _s(docket.get("court", ""))
    case.date = _s(docket.get("date_filed", ""))
    case.complaint_filed_date = _s(docket.get("date_filed", ""))
    case.judgment_date = _s(docket.get("date_terminated", ""))

    # Judges
    judges = []
    assigned = docket.get("assigned_to_str", "")
    referred = docket.get("referred_to_str", "")
    if assigned:
        judges.append(assigned)
    if referred:
        judges.append(referred)
    case.judges = "; ".join(judges)

    # Legal topic from nature_of_suit + cause
    nos = _s(docket.get("nature_of_suit", ""))
    cause = _s(docket.get("cause", ""))
    topics = [t for t in [nos, cause] if t]
    case.legal_topic = "; ".join(topics)

    # Charges from cause + idb_data.section
    idb = docket.get("idb_data") or {}
    sections = []
    if cause:
        sections.append(cause)
    idb_section = _s(idb.get("section", ""))
    idb_subsection = _s(idb.get("subsection", ""))
    if idb_section:
        sec_str = idb_section
        if idb_subsection:
            sec_str += f"({idb_subsection})"
        sections.append(f"Section {sec_str}")
    case.charges_and_sections = "; ".join(sections)

    # Case status
    date_term = docket.get("date_terminated")
    case.case_status = "Closed" if date_term else "Open"

    # Source URL
    abs_url = docket.get("absolute_url", "")
    if abs_url:
        case.source_url = f"{CL_BASE_URL}{abs_url}"
    else:
        case.source_url = f"https://archive.org/details/{identifier}"

    # --- IDB data (FJC Integrated Database) ---
    if idb:
        # Disposition
        disp_code = idb.get("disposition")
        if disp_code is not None:
            disp_text = DISPOSITION_CODES.get(disp_code, f"Code {disp_code}")
            case.disposition_code = f"{disp_code} - {disp_text}"
            case.outcome = disp_text

        # Judgment
        judg_code = idb.get("judgment")
        if judg_code is not None:
            judg_text = JUDGMENT_CODES.get(judg_code, f"Code {judg_code}")
            case.judgment_code = f"{judg_code} - {judg_text}"
            case.judgment_type = judg_text

        # Nature of judgment
        noj = idb.get("nature_of_judgement")
        if noj and case.judgment_type:
            case.judgment_type += f" (nature: {noj})"

        # Other IDB fields
        case.procedural_progress = _s(idb.get("procedural_progress", ""))
        case.case_origin = _s(idb.get("origin", ""))
        case.monetary_demand = _s(idb.get("monetary_demand", ""))
        case.class_action_flag = _s(idb.get("class_action", ""))
        case.diversity_of_residence = _s(idb.get("diversity_of_residence", ""))
        case.pro_se = _s(idb.get("pro_se", ""))
        case.arbitration_at_filing = _s(idb.get("arbitration_at_filing", ""))
        case.arbitration_at_termination = _s(idb.get("arbitration_at_termination", ""))
        case.county_of_residence = _s(idb.get("county_of_residence", ""))
        case.total_fine_amount = _s(idb.get("amount_received", ""))

        # Final judgment details
        details = []
        if disp_code is not None:
            details.append(f"Disposition: {DISPOSITION_CODES.get(disp_code, disp_code)}")
        if judg_code is not None:
            details.append(f"Judgment for: {JUDGMENT_CODES.get(judg_code, judg_code)}")
        pp = idb.get("procedural_progress")
        if pp:
            details.append(f"Procedural progress: {pp}")
        case.final_judgment_details = "; ".join(details)

        # Defendant sentence (criminal cases)
        offense = _s(idb.get("nature_of_offense", ""))
        if offense:
            case.defendant_sentence = offense

    # --- IA-RECAP-only fields from docket top-level ---
    case.docket_number = _s(docket.get("docket_number", ""))
    case.pacer_case_id = _s(docket.get("pacer_case_id", ""))
    case.jurisdiction_type = _s(docket.get("jurisdiction_type", ""))
    case.jury_demand = _s(docket.get("jury_demand", ""))
    case.date_last_filing = _s(docket.get("date_last_filing", ""))
    case.date_terminated = _s(docket.get("date_terminated", ""))

    # --- Parties extraction ---
    parties = docket.get("parties") or []
    plaintiffs = []
    defendants = []
    all_attorneys = []
    all_phones = []
    all_emails = []

    for party in parties:
        name = party.get("name", "")
        party_types = party.get("party_types") or []

        for pt in party_types:
            role = pt.get("name", "")
            if "Plaintiff" in role:
                plaintiffs.append(name)
            elif "Defendant" in role:
                defendants.append(name)
            elif "Relief" in role:
                if case.relief_defendants:
                    case.relief_defendants += f"; {name}"
                else:
                    case.relief_defendants = name

        # Extract attorneys
        for atty in party.get("attorneys", []):
            atty_name = atty.get("name", "")
            if atty_name:
                all_attorneys.append(atty_name)
            phone = atty.get("phone", "")
            if phone:
                all_phones.append(phone)
            email = atty.get("email", "")
            if email:
                all_emails.append(email)

            # Detect SEC attorneys by contact_raw or email
            contact = atty.get("contact_raw", "")
            if "sec.gov" in (email or "").lower() or "Securities and Exchange" in contact:
                if case.sec_attorneys:
                    case.sec_attorneys += f"; {atty_name}"
                else:
                    case.sec_attorneys = atty_name
                # Parse regional office from contact
                office_match = re.search(
                    r"Securities and Exchange Commission\s*\((\w+)\)", contact
                )
                if office_match and not case.sec_regional_office:
                    case.sec_regional_office = office_match.group(1)

    if plaintiffs:
        case.petitioner = plaintiffs[0]
    if defendants:
        case.respondent = defendants[0]
        case.defendant_roles = "; ".join(
            f"{d} (Defendant)" for d in defendants
        )
        if len(defendants) > 1:
            case.co_defendants = "; ".join(defendants[1:])

    case.attorney_phone = "; ".join(dict.fromkeys(all_phones))  # dedup, preserve order
    case.attorney_email = "; ".join(dict.fromkeys(all_emails))

    # --- Docket entries ---
    entries = docket.get("docket_entries") or []
    entry_texts = []
    doc_urls = []
    page_counts = []
    file_sizes = []
    sealed_flags = []
    ocr_statuses = []

    for entry in entries:
        desc = entry.get("description", "")
        if desc:
            entry_num = entry.get("entry_number", "")
            date_filed = entry.get("date_filed", "")
            prefix = f"[{entry_num}|{date_filed}]" if entry_num else f"[{date_filed}]"
            entry_texts.append(f"{prefix} {desc[:500]}")

        for doc in entry.get("recap_documents") or []:
            # Document URLs
            ia_url = doc.get("filepath_ia", "")
            if ia_url:
                doc_urls.append(ia_url)

            # Per-document metadata
            pc = doc.get("page_count")
            if pc is not None:
                page_counts.append(str(pc))
            fs = doc.get("file_size")
            if fs is not None:
                file_sizes.append(str(fs))
            sealed = doc.get("is_sealed")
            if sealed is not None:
                sealed_flags.append(str(sealed))
            ocr = doc.get("ocr_status")
            if ocr is not None:
                ocr_statuses.append(str(ocr))

    case.docket_entry_text = " ||| ".join(entry_texts[:200])  # cap at 200 entries
    case.associated_documents = "; ".join(doc_urls[:100])  # cap at 100 docs
    case.doc_page_count = "; ".join(page_counts[:100])
    case.doc_file_size = "; ".join(file_sizes[:100])
    case.doc_is_sealed = "; ".join(sealed_flags[:100])
    case.doc_ocr_status = "; ".join(ocr_statuses[:100])

    # --- Fallback to IA metadata if docket is sparse ---
    if ia_meta:
        meta = ia_meta.get("metadata", {})
        if not case.case_title:
            case.case_title = _s(meta.get("title", ""))
        if not case.court:
            case.court = _s(meta.get("court", ""))
        if not case.source_url:
            case.source_url = _s(meta.get("source_url", ""))

    return case


def extract_case_from_ia_only(identifier: str, ia_meta: dict) -> RECAPCase:
    """Extract minimal fields when no docket.json exists."""
    case = RECAPCase()
    meta = ia_meta.get("metadata", {})
    case.case_title = _s(meta.get("title", ""))
    case.court = _s(meta.get("court", ""))
    case.source_url = _s(meta.get("source_url", ""))
    if not case.source_url:
        case.source_url = f"https://archive.org/details/{identifier}"
    case.pacer_case_id = identifier.split(".")[-1] if "." in identifier else ""

    # Count files
    files = ia_meta.get("files", [])
    pdf_urls = []
    for f in files:
        name = f.get("name", "")
        if name.endswith(".pdf") and f.get("source") == "original":
            pdf_urls.append(f"{IA_DOWNLOAD_URL}/{identifier}/{name}")
    case.associated_documents = "; ".join(pdf_urls[:100])

    return case


# ═══════════════════════════════════════════════════════════════════════════════
#  PIPELINE — process a single case
# ═══════════════════════════════════════════════════════════════════════════════

async def process_case(identifier: str, title: str, db: Database,
                       sem: asyncio.Semaphore, worker_id: int):
    """Process a single IA RECAP case: fetch metadata + docket → extract → SQLite."""
    # Build a unique source_url for dedup check
    check_url = f"https://archive.org/details/{identifier}"
    if db.url_exists(check_url):
        # Also check CourtListener-style URLs
        log.info(f"  [W{worker_id}] Skip (exists): {identifier}")
        return

    log.info(f"  [W{worker_id}] >>> {identifier} — {title[:60]}")

    # Fetch IA metadata
    await asyncio.sleep(REQUEST_DELAY)
    ia_meta = await fetch_ia_metadata(identifier, sem)

    # Fetch docket.json
    await asyncio.sleep(REQUEST_DELAY)
    docket = await fetch_docket_json(identifier, sem)

    if docket:
        case = extract_case(identifier, docket, ia_meta)
        log.info(f"  [W{worker_id}]   Docket found: {len(docket.get('docket_entries', []))} entries, "
                 f"{len(docket.get('parties', []))} parties")
    elif ia_meta:
        case = extract_case_from_ia_only(identifier, ia_meta)
        log.info(f"  [W{worker_id}]   No docket.json — IA metadata only")
    else:
        log.error(f"  [W{worker_id}]   Failed to fetch any data for {identifier}")
        db.log_scrape(check_url, "error", "No data fetched")
        return

    # Ensure source_url is set for dedup
    if not case.source_url:
        case.source_url = check_url

    # Write to SQLite
    db.insert_case(case)
    db.log_scrape(case.source_url, "success")
    log.info(f"  [W{worker_id}]   Saved: {case.case_title[:60]} ({db.case_count()} total)")


# ═══════════════════════════════════════════════════════════════════════════════
#  PIPELINE ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

async def run_pipeline(args):
    """Main async pipeline."""
    db = Database(args.db)
    workers = min(args.workers, MAX_WORKERS)
    sem = asyncio.Semaphore(workers)

    log.info("=" * 64)
    log.info("  IA RECAP Scraper — Scalable Pipeline")
    log.info("=" * 64)
    log.info(f"  Database:  {args.db}")
    log.info(f"  Workers:   {workers}")
    log.info(f"  Existing:  {db.case_count()} cases in DB")

    # Verify curl
    try:
        r = subprocess.run(["curl", "--version"], capture_output=True, text=True, timeout=5)
        log.info(f"  curl:      {r.stdout.split(chr(10))[0]}")
    except Exception:
        sys.exit("ERROR: curl not found")

    all_cases = []

    if args.identifier:
        # Single case mode
        all_cases.append({"identifier": args.identifier, "title": args.identifier})

    elif args.nos:
        # Search by Nature of Suit code
        log.info(f"\n  Searching IA for NOS code {args.nos}...")
        results = await ia_search_by_nos(args.nos, args.max, sem)
        for doc in results:
            all_cases.append({
                "identifier": doc.get("identifier", ""),
                "title": doc.get("title", ""),
            })
        log.info(f"  Found {len(all_cases)} cases")

    elif args.query:
        # Custom query mode
        log.info(f"\n  Searching IA: {args.query}")
        results = await ia_search(args.query, args.max, sem)
        for doc in results:
            all_cases.append({
                "identifier": doc.get("identifier", ""),
                "title": doc.get("title", ""),
            })
        log.info(f"  Found {len(all_cases)} cases")

    elif args.all:
        # Broad securities search with multiple queries
        queries = [
            "title:(Securities AND Exchange AND Commission)",
            "title:(SEC AND fraud)",
            "title:(securities AND litigation)",
            "title:(insider AND trading)",
            "title:(stock AND fraud)",
            "title:(Ponzi)",
            "title:(investment AND fraud)",
        ]
        seen = set()
        for q in queries:
            log.info(f"\n  Searching: {q}")
            results = await ia_search(q, args.max, sem)
            for doc in results:
                ident = doc.get("identifier", "")
                if ident and ident not in seen:
                    seen.add(ident)
                    all_cases.append({
                        "identifier": ident,
                        "title": doc.get("title", ""),
                    })
            log.info(f"  Total unique so far: {len(all_cases)}")
            await asyncio.sleep(REQUEST_DELAY)

    else:
        log.error("  No search criteria specified. Use --query, --nos, --identifier, or --all")
        db.close()
        return

    # Filter out empty identifiers
    all_cases = [c for c in all_cases if c.get("identifier")]

    # Deduplicate
    seen = set()
    unique = []
    for c in all_cases:
        if c["identifier"] not in seen:
            seen.add(c["identifier"])
            unique.append(c)

    already = sum(
        1 for c in unique
        if db.url_exists(f"https://archive.org/details/{c['identifier']}")
    )

    log.info(f"\n{'─' * 64}")
    log.info(f"  Total cases to process: {len(unique)}")
    log.info(f"  Already in DB (will skip): {already}")
    log.info(f"{'─' * 64}")

    # Process with worker pool in batches
    tasks = [
        process_case(c["identifier"], c.get("title", ""), db, sem, (i % workers) + 1)
        for i, c in enumerate(unique)
    ]

    batch_size = workers * 2
    for i in range(0, len(tasks), batch_size):
        batch = tasks[i:i + batch_size]
        await asyncio.gather(*batch, return_exceptions=True)
        log.info(f"  --- Batch {i // batch_size + 1} complete ({db.case_count()} in DB) ---")

    log.info(f"\n{'=' * 64}")
    log.info(f"  PIPELINE COMPLETE")
    log.info(f"  Total cases in DB: {db.case_count()}")
    if os.path.exists(args.db):
        log.info(f"  Database: {args.db} ({os.path.getsize(args.db) / 1024 / 1024:.1f}MB)")
    log.info(f"{'=' * 64}")

    db.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="IA RECAP Scraper — Federal Court Case Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python ia_recap_scraper.py scrape --query "Securities AND Exchange" --max 50
  python ia_recap_scraper.py scrape --nos 850 --max 100
  python ia_recap_scraper.py scrape --identifier gov.uscourts.nysd.524448
  python ia_recap_scraper.py scrape --all --max 200
  python ia_recap_scraper.py export --format both -o ia_recap_cases
  python ia_recap_scraper.py status
        """,
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # ── scrape ──
    sc = sub.add_parser("scrape", help="Scrape IA RECAP cases")
    sc.add_argument("--query", default="", help="IA search query (e.g. 'title:(SEC AND fraud)')")
    sc.add_argument("--nos", type=int, default=0, help="Nature of Suit code (e.g. 850 for securities)")
    sc.add_argument("--identifier", default="", help="Scrape a single case by IA identifier")
    sc.add_argument("--all", action="store_true", help="Broad securities fraud search with multiple queries")
    sc.add_argument("--max", type=int, default=50, help="Max results per search query (default: 50)")
    sc.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                    help=f"Concurrent workers (default: {DEFAULT_WORKERS}, max: {MAX_WORKERS})")
    sc.add_argument("--db", default=DB_FILE, help=f"SQLite database (default: {DB_FILE})")

    # ── export ──
    ex = sub.add_parser("export", help="Export database to CSV/JSON")
    ex.add_argument("--format", choices=["csv", "json", "both"], default="csv", help="Export format")
    ex.add_argument("-o", "--output", default="ia_recap_cases", help="Output filename (without extension)")
    ex.add_argument("--db", default=DB_FILE, help=f"SQLite database (default: {DB_FILE})")

    # ── status ──
    st = sub.add_parser("status", help="Show database statistics")
    st.add_argument("--db", default=DB_FILE, help=f"SQLite database (default: {DB_FILE})")

    args = parser.parse_args()

    if args.command == "scrape":
        asyncio.run(run_pipeline(args))

    elif args.command == "export":
        if not os.path.exists(args.db):
            sys.exit(f"Database not found: {args.db}")
        db = Database(args.db)
        if args.format in ("csv", "both"):
            path = f"{args.output}.csv"
            n = db.export_csv(path)
            log.info(f"  Exported {n} cases -> {path}")
        if args.format in ("json", "both"):
            path = f"{args.output}.json"
            n = db.export_json(path)
            log.info(f"  Exported {n} cases -> {path}")
        db.close()

    elif args.command == "status":
        if not os.path.exists(args.db):
            sys.exit(f"Database not found: {args.db}")
        db = Database(args.db)
        s = db.status()
        print(f"\n{'=' * 50}")
        print(f"  IA RECAP Database Status")
        print(f"{'=' * 50}")
        print(f"  Total cases:     {s['total_cases']}")
        print(f"  Latest date:     {s['latest_date']}")
        print(f"  DB size:         {s['db_size_mb']}MB")
        print(f"  Scrape success:  {s['scrape_success']}")
        print(f"  Scrape errors:   {s['scrape_errors']}")
        if s["top_topics"]:
            print(f"\n  Top legal topics:")
            for topic, cnt in s["top_topics"]:
                print(f"    {cnt:4d} | {topic}")
        if s["top_courts"]:
            print(f"\n  Top courts:")
            for court, cnt in s["top_courts"]:
                print(f"    {cnt:4d} | {court}")
        if s["top_dispositions"]:
            print(f"\n  Top dispositions:")
            for disp, cnt in s["top_dispositions"]:
                print(f"    {cnt:4d} | {disp}")
        print(f"{'=' * 50}\n")
        db.close()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
