"""
Phase 0.2 + 0.3: Outcome Labeling & Dataset Split
===================================================
Extracts outcome labels from opinion text and splits the dataset
for the Private 10b-5 securities fraud pipeline.

The pipeline plan's metadata-based criteria (date_terminated, idb_judgment,
disposition) have near-zero coverage on opinion-sourced cases. This script
labels cases from the opinion text itself using regex pattern matching.

Usage:
  python script/label_and_split.py --db data/private_10b5_sample_416.db
  python script/label_and_split.py --db data/private_10b5_sample_416.db --dry-run
"""

from __future__ import annotations

import argparse
import random
import re
import sqlite3
import sys
from pathlib import Path
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class LabelResult(NamedTuple):
    outcome: str          # DEFENDANT_WINS | PLAINTIFF_WINS | MIXED | UNCLEAR | UNLABELED
    source: str           # conclusion_regex | fulltext_regex | no_opinion
    confidence: float     # 0.0-1.0
    pattern_name: str     # which pattern matched
    matched_text: str     # snippet (truncated to 200 chars)


# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

MIXED_PATTERNS: list[tuple[str, re.Pattern[str], float]] = [
    ("mixed_granted_denied_in_part",
     re.compile(r"granted\s+in\s+part\s+(?:and\s+)?denied\s+in\s+part", re.I), 0.85),
    ("mixed_denied_granted_in_part",
     re.compile(r"denied\s+in\s+part\s+(?:and\s+)?granted\s+in\s+part", re.I), 0.85),
    ("mixed_affirm_reverse_in_part",
     re.compile(r"affirm(?:ed)?\s+in\s+part\s+(?:and\s+)?revers(?:ed|e)\s+in\s+part", re.I), 0.85),
    ("mixed_reverse_affirm_in_part",
     re.compile(r"revers(?:ed|e)\s+in\s+part\s+(?:and\s+)?affirm(?:ed)?\s+in\s+part", re.I), 0.85),
]

DEF_WINS_PATTERNS: list[tuple[str, re.Pattern[str], float]] = [
    ("def_mtd_granted",
     re.compile(r"motion\s+to\s+dismiss\s+is\s+(?:hereby\s+)?granted", re.I), 0.9),
    ("def_complaint_dismissed",
     re.compile(r"complaint\s+is\s+(?:hereby\s+)?dismissed", re.I), 0.9),
    ("def_dismissed_with_prejudice",
     re.compile(r"dismissed\s+with\s+prejudice", re.I), 0.85),
    ("def_affirm_dismissal",
     re.compile(
         r"(?:we|the\s+court)\s+affirm(?:s)?\s+(?:the\s+)?"
         r"(?:district\s+court.s?\s+)?(?:judgment\s+of\s+)?dismiss", re.I), 0.85),
    ("def_affirm_grant",
     re.compile(
         r"(?:we|the\s+court)\s+affirm(?:s)?\s+(?:the\s+)?"
         r"(?:district\s+court.s?\s+)?(?:grant|order)", re.I), 0.75),
    ("def_dismissal_affirmed",
     re.compile(r"(?:the\s+)?dismissal\s+(?:of\s+.*?\s+)?is\s+(?:hereby\s+)?affirmed", re.I), 0.85),
    ("def_sj_granted_for_def",
     re.compile(
         r"summary\s+judgment\s+(?:is\s+)?(?:hereby\s+)?granted\s+"
         r"(?:in\s+favor\s+of\s+)?(?:the\s+)?defendants?", re.I), 0.9),
    ("def_sj_granted_generic",
     re.compile(
         r"(?:defendants?'?\s+)?(?:motion\s+for\s+)?summary\s+judgment\s+"
         r"(?:is\s+)?(?:hereby\s+)?granted", re.I), 0.75),
    ("def_district_court_affirmed",
     re.compile(
         r"(?:judgment|decision|order)\s+(?:of\s+the\s+district\s+court\s+)?"
         r"is\s+(?:hereby\s+)?affirmed", re.I), 0.7),
]

PLT_WINS_PATTERNS: list[tuple[str, re.Pattern[str], float]] = [
    ("plt_mtd_denied",
     re.compile(r"motion\s+to\s+dismiss\s+is\s+(?:hereby\s+)?denied", re.I), 0.9),
    ("plt_sj_denied",
     re.compile(
         r"(?:defendants?'?\s+)?(?:motion\s+for\s+)?summary\s+judgment\s+"
         r"(?:is\s+)?(?:hereby\s+)?denied", re.I), 0.9),
    ("plt_reversed_and_remanded",
     re.compile(r"reversed?\s+and\s+remanded", re.I), 0.85),
    ("plt_we_reverse",
     re.compile(r"(?:we|the\s+court)\s+(?:hereby\s+)?reverse", re.I), 0.85),
    ("plt_dismissal_reversed",
     re.compile(r"(?:the\s+)?dismissal\s+.*?is\s+(?:hereby\s+)?reversed", re.I), 0.85),
    ("plt_standalone_reversed",
     re.compile(r"\bREVERSED\b", re.MULTILINE), 0.8),
    ("plt_vacated_and_remanded",
     re.compile(r"vacate[ds]?\s+and\s+remand(?:ed)?", re.I), 0.8),
]

# Standalone AFFIRMED — needs disambiguation
AFFIRMED_PATTERN = re.compile(
    r"(?:is|are)\s+(?:hereby\s+)?affirmed|(?:\bAFFIRMED\b\.?\s*$)", re.I | re.MULTILINE
)

# Contamination patterns
SEC_PATTERNS = [
    re.compile(r"^SEC\s+v\.", re.I),
    re.compile(r"^S\.E\.C\.\s+v\.", re.I),
    re.compile(r"Securities and Exchange Commission", re.I),
    re.compile(r"Securities & Exchang", re.I),
]
DOJ_PATTERNS = [
    re.compile(r"^United States\s+v\.", re.I),
]
SEC_APPEAL_PATTERNS = [
    re.compile(r"v\.\s+SEC\s*$", re.I),
    re.compile(r"v\.\s+Securities and Exchange", re.I),
]

# Procedural stage patterns
STAGE_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "MTD": [
        re.compile(r"motion\s+to\s+dismiss", re.I),
        re.compile(r"12\s*\(\s*b\s*\)\s*\(\s*6\s*\)", re.I),
        re.compile(r"failure\s+to\s+state\s+a\s+claim", re.I),
    ],
    "SJ": [
        re.compile(r"summary\s+judgment", re.I),
        re.compile(r"Rule\s+56", re.I),
    ],
    "APPEAL": [
        re.compile(r"(?:we|this\s+court)\s+(?:affirm|reverse|vacate|remand)", re.I),
        re.compile(r"on\s+appeal", re.I),
    ],
    "TRIAL": [
        re.compile(r"(?:jury|bench)\s+(?:trial|verdict)", re.I),
        re.compile(r"after\s+(?:a\s+)?trial", re.I),
        re.compile(r"the\s+jury\s+(?:found|returned|awarded)", re.I),
    ],
}


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------

def detect_contamination(case_name: str) -> str:
    """Classify case as PRIVATE, SEC_ENFORCEMENT, DOJ_CRIMINAL, or SEC_APPEAL."""
    for pat in SEC_PATTERNS:
        if pat.search(case_name):
            return "SEC_ENFORCEMENT"
    for pat in DOJ_PATTERNS:
        if pat.search(case_name):
            return "DOJ_CRIMINAL"
    for pat in SEC_APPEAL_PATTERNS:
        if pat.search(case_name):
            return "SEC_APPEAL"
    return "PRIVATE"


def _disambiguate_affirmed(full_text: str) -> tuple[str, float]:
    """When conclusion says 'AFFIRMED', check full text for what was affirmed."""
    full_lower = full_text.lower()

    has_mtd_denied = bool(re.search(
        r"(?:district\s+court|lower\s+court).*?denied.*?motion\s+to\s+dismiss", full_lower))
    has_mtd_granted = bool(re.search(
        r"(?:district\s+court|lower\s+court).*?(?:grant.*?motion\s+to\s+dismiss|dismiss)", full_lower))
    has_sj_granted = bool(re.search(
        r"(?:district\s+court|lower\s+court).*?grant.*?summary\s+judgment", full_lower))

    if has_mtd_denied:
        return ("PLAINTIFF_WINS", 0.7)
    if has_mtd_granted or has_sj_granted:
        return ("DEFENDANT_WINS", 0.7)
    return ("DEFENDANT_WINS", 0.5)


def _scan_patterns(
    text: str,
    patterns: list[tuple[str, re.Pattern[str], float]],
) -> tuple[str, float, str] | None:
    """Search text for first matching pattern. Returns (pattern_name, confidence, matched_text) or None."""
    for name, pat, conf in patterns:
        m = pat.search(text)
        if m:
            snippet = text[max(0, m.start() - 30):m.end() + 30].strip()
            return (name, conf, snippet[:200])
    return None


def classify_outcome(plain_text: str) -> LabelResult:
    """Extract outcome label from opinion text using two-pass regex analysis."""
    tail = plain_text[-2000:]

    # Pass 1: Check conclusion for MIXED (highest priority)
    mixed_hit = _scan_patterns(tail, MIXED_PATTERNS)
    if mixed_hit:
        return LabelResult("MIXED", "conclusion_regex", mixed_hit[1], mixed_hit[0], mixed_hit[2])

    # Pass 1: Check conclusion for DEF_WINS and PLT_WINS
    def_hit = _scan_patterns(tail, DEF_WINS_PATTERNS)
    plt_hit = _scan_patterns(tail, PLT_WINS_PATTERNS)

    if def_hit and plt_hit:
        return LabelResult("MIXED", "conclusion_cooccurrence", 0.7, "both_in_tail", f"DEF:{def_hit[0]} PLT:{plt_hit[0]}")
    if def_hit:
        return LabelResult("DEFENDANT_WINS", "conclusion_regex", def_hit[1], def_hit[0], def_hit[2])
    if plt_hit:
        return LabelResult("PLAINTIFF_WINS", "conclusion_regex", plt_hit[1], plt_hit[0], plt_hit[2])

    # Pass 1: Check for standalone AFFIRMED (needs disambiguation)
    if AFFIRMED_PATTERN.search(tail):
        outcome, conf = _disambiguate_affirmed(plain_text)
        return LabelResult(outcome, "conclusion_regex", conf, "affirmed_disambiguated", tail[-200:].strip())

    # Pass 2: Full-text fallback
    mixed_hit = _scan_patterns(plain_text, MIXED_PATTERNS)
    if mixed_hit:
        return LabelResult("MIXED", "fulltext_regex", mixed_hit[1] * 0.8, mixed_hit[0], mixed_hit[2])

    def_hit = _scan_patterns(plain_text, DEF_WINS_PATTERNS)
    plt_hit = _scan_patterns(plain_text, PLT_WINS_PATTERNS)

    if def_hit and plt_hit:
        return LabelResult("MIXED", "fulltext_cooccurrence", 0.5, "both_in_fulltext", f"DEF:{def_hit[0]} PLT:{plt_hit[0]}")
    if def_hit:
        return LabelResult("DEFENDANT_WINS", "fulltext_regex", def_hit[1] * 0.8, def_hit[0], def_hit[2])
    if plt_hit:
        return LabelResult("PLAINTIFF_WINS", "fulltext_regex", plt_hit[1] * 0.8, plt_hit[0], plt_hit[2])

    if AFFIRMED_PATTERN.search(plain_text):
        outcome, conf = _disambiguate_affirmed(plain_text)
        return LabelResult(outcome, "fulltext_regex", conf * 0.8, "affirmed_disambiguated_fulltext", "")

    return LabelResult("UNCLEAR", "no_signal", 0.0, "", "")


def detect_stage(plain_text: str, court_id: str) -> str | None:
    """Detect procedural stage from opinion text and court identifier."""
    is_appellate = court_id.startswith("ca") or court_id == "scotus"

    found_stages: list[str] = []
    for stage, patterns in STAGE_PATTERNS.items():
        for pat in patterns:
            if pat.search(plain_text):
                found_stages.append(stage)
                break

    if is_appellate and "APPEAL" not in found_stages:
        found_stages.append("APPEAL")

    # Priority: APPEAL > TRIAL > SJ > MTD
    priority = ["APPEAL", "TRIAL", "SJ", "MTD"]
    for stage in priority:
        if stage in found_stages:
            return stage
    return None


def assign_splits(
    rows: list[dict[str, object]],
    seed: int = 42,
) -> list[dict[str, object]]:
    """Assign train/val/test splits, stratified by outcome_label."""
    rng = random.Random(seed)

    groups: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        label = str(row["outcome_label"])
        groups.setdefault(label, []).append(row)

    for label, group in groups.items():
        rng.shuffle(group)
        n = len(group)
        if n < 3:
            for row in group:
                row["split"] = "train"
            print(f"  WARNING: {label} has only {n} cases, all assigned to train")
            continue

        n_test = max(1, round(n * 0.15))
        n_val = max(1, round(n * 0.15))
        n_train = n - n_val - n_test

        for row in group[:n_train]:
            row["split"] = "train"
        for row in group[n_train:n_train + n_val]:
            row["split"] = "val"
        for row in group[n_train + n_val:]:
            row["split"] = "test"

    return rows


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS case_labels (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    docket_id        INTEGER NOT NULL,
    opinion_id       INTEGER,
    outcome_label    TEXT NOT NULL,
    procedural_stage TEXT,
    contamination_type TEXT NOT NULL DEFAULT 'PRIVATE',
    label_source     TEXT NOT NULL,
    label_confidence REAL NOT NULL DEFAULT 0.0,
    matched_pattern  TEXT,
    matched_text     TEXT,
    split            TEXT,
    FOREIGN KEY (docket_id) REFERENCES cases(docket_id)
);
"""

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_case_labels_docket ON case_labels(docket_id);",
    "CREATE INDEX IF NOT EXISTS idx_case_labels_split ON case_labels(split);",
    "CREATE INDEX IF NOT EXISTS idx_case_labels_outcome ON case_labels(outcome_label);",
]


def process_database(db_path: Path, dry_run: bool = False) -> None:
    """Main orchestrator: label all cases and write case_labels table."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Load cases
    cases = cursor.execute("SELECT docket_id, case_name, court_id FROM cases").fetchall()
    print(f"Loaded {len(cases)} cases from {db_path.name}")

    # Load opinions with text (only 010combined have actual content)
    opinions = cursor.execute("""
        SELECT opinion_id, docket_id, plain_text
        FROM opinions
        WHERE plain_text IS NOT NULL AND length(plain_text) > 1000
          AND type = '010combined'
    """).fetchall()
    print(f"Found {len(opinions)} opinions with text")

    # Index opinions by docket_id
    opinions_by_docket: dict[int, list[sqlite3.Row]] = {}
    for op in opinions:
        opinions_by_docket.setdefault(op["docket_id"], []).append(op)

    # Process each case
    all_rows: list[dict[str, object]] = []

    for case in cases:
        docket_id = case["docket_id"]
        case_name = case["case_name"] or ""
        court_id = case["court_id"] or ""

        contamination = detect_contamination(case_name)
        case_opinions = opinions_by_docket.get(docket_id, [])

        if not case_opinions:
            all_rows.append({
                "docket_id": docket_id,
                "opinion_id": None,
                "outcome_label": "UNLABELED",
                "procedural_stage": None,
                "contamination_type": contamination,
                "label_source": "no_opinion",
                "label_confidence": 0.0,
                "matched_pattern": None,
                "matched_text": None,
                "split": None,
            })
            continue

        for op in case_opinions:
            result = classify_outcome(op["plain_text"])
            stage = detect_stage(op["plain_text"], court_id)

            all_rows.append({
                "docket_id": docket_id,
                "opinion_id": op["opinion_id"],
                "outcome_label": result.outcome,
                "procedural_stage": stage,
                "contamination_type": contamination,
                "label_source": result.source,
                "label_confidence": result.confidence,
                "matched_pattern": result.pattern_name,
                "matched_text": result.matched_text,
                "split": None,
            })

    # Assign splits (only private + labeled + confident)
    splittable = [
        r for r in all_rows
        if r["contamination_type"] == "PRIVATE"
        and r["outcome_label"] in ("DEFENDANT_WINS", "PLAINTIFF_WINS", "MIXED")
        and float(r["label_confidence"]) >= 0.5  # type: ignore[arg-type]
    ]
    print(f"\nAssigning splits for {len(splittable)} qualifying cases...")
    assign_splits(splittable)

    # Print summary
    print_summary(all_rows)

    if dry_run:
        print("\n[DRY RUN] No changes written to database.")
        conn.close()
        return

    # Write to database
    cursor.execute("DROP TABLE IF EXISTS case_labels")
    cursor.execute(TABLE_SQL)
    for idx_sql in INDEX_SQL:
        cursor.execute(idx_sql)

    cursor.executemany("""
        INSERT INTO case_labels
            (docket_id, opinion_id, outcome_label, procedural_stage,
             contamination_type, label_source, label_confidence,
             matched_pattern, matched_text, split)
        VALUES
            (:docket_id, :opinion_id, :outcome_label, :procedural_stage,
             :contamination_type, :label_source, :label_confidence,
             :matched_pattern, :matched_text, :split)
    """, all_rows)

    conn.commit()
    print(f"\nWrote {len(all_rows)} rows to case_labels table in {db_path.name}")
    conn.close()


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(rows: list[dict[str, object]]) -> None:
    """Print labeling and split summary."""
    total = len(rows)

    # Contamination breakdown
    contam: dict[str, int] = {}
    for r in rows:
        key = str(r["contamination_type"])
        contam[key] = contam.get(key, 0) + 1

    print("\n" + "=" * 55)
    print("  PHASE 0.2 + 0.3: LABEL AND SPLIT SUMMARY")
    print("=" * 55)

    print(f"\nTotal rows:              {total}")
    print("  Contamination breakdown:")
    for key in ["PRIVATE", "SEC_ENFORCEMENT", "DOJ_CRIMINAL", "SEC_APPEAL"]:
        if key in contam:
            print(f"    {key:20s} {contam[key]:>4d}")

    # Outcome breakdown (private only)
    private_rows = [r for r in rows if r["contamination_type"] == "PRIVATE"]
    outcome_counts: dict[str, int] = {}
    for r in private_rows:
        key = str(r["outcome_label"])
        outcome_counts[key] = outcome_counts.get(key, 0) + 1

    print(f"\nPrivate cases outcome distribution:")
    for key in ["DEFENDANT_WINS", "PLAINTIFF_WINS", "MIXED", "UNCLEAR", "UNLABELED"]:
        if key in outcome_counts:
            print(f"    {key:20s} {outcome_counts[key]:>4d}")

    # Procedural stage breakdown (private with text)
    labeled = [r for r in private_rows if r["outcome_label"] not in ("UNLABELED", "UNCLEAR")]
    stage_counts: dict[str, int] = {}
    for r in labeled:
        key = str(r["procedural_stage"] or "UNKNOWN")
        stage_counts[key] = stage_counts.get(key, 0) + 1

    print(f"\nProcedural stage (private labeled cases):")
    for key in ["APPEAL", "MTD", "SJ", "TRIAL", "UNKNOWN"]:
        if key in stage_counts:
            print(f"    {key:20s} {stage_counts[key]:>4d}")

    # Label source breakdown
    source_counts: dict[str, int] = {}
    for r in labeled:
        key = str(r["label_source"])
        source_counts[key] = source_counts.get(key, 0) + 1

    print(f"\nLabel source (private labeled cases):")
    for key, count in sorted(source_counts.items(), key=lambda x: -x[1]):
        print(f"    {key:30s} {count:>4d}")

    # Split breakdown
    split_rows = [r for r in rows if r["split"] is not None]
    split_outcome: dict[str, dict[str, int]] = {}
    for r in split_rows:
        s = str(r["split"])
        o = str(r["outcome_label"])
        split_outcome.setdefault(s, {}).setdefault(o, 0)
        split_outcome[s][o] += 1

    print(f"\nDataset split ({len(split_rows)} cases):")
    for s in ["train", "val", "test"]:
        if s in split_outcome:
            parts = [f"{o}={c}" for o, c in sorted(split_outcome[s].items())]
            total_s = sum(split_outcome[s].values())
            print(f"    {s:8s} {total_s:>4d}  ({', '.join(parts)})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 0.2+0.3: Label outcomes and split dataset"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/private_10b5_sample_416.db"),
        help="Path to SQLite database (default: data/private_10b5_sample_416.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print summary without writing to database",
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"ERROR: Database not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    process_database(args.db, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
