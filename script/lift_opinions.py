"""
Phase 1.3: Batch IRAC extraction from judicial opinions.

Runs the extraction pipeline on opinions from the database,
calling Llama 3.3 70B via vLLM on Gaudi 2.

Usage:
  # Local: preview prompts (no LLM needed)
  python script/lift_opinions.py --db data/private_10b5_sample_416.db --dry-run --limit 3

  # Local: test full pipeline with mock LLM
  python script/lift_opinions.py --db data/private_10b5_sample_416.db --mock --limit 3

  # Sol: run against vLLM on Gaudi 2
  python script/lift_opinions.py --db data/private_10b5_sample_416.db --llm-url http://gaudi004:8000 --limit 5

  # Single opinion
  python script/lift_opinions.py --db data/private_10b5_sample_416.db --opinion-id 12345 --llm-url http://gaudi004:8000
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

from script.lifting.extract import extract_opinion
from script.lifting.llm_client import LLMClient
from script.lifting.store import init_irac_table

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def get_opinions_to_process(
    conn: sqlite3.Connection,
    limit: int | None = None,
    opinion_id: int | None = None,
) -> list[dict]:
    """
    Get opinions eligible for extraction.

    Selects private, labeled opinions ordered by text length (shortest first).
    Skips opinions that already have a valid extraction in irac_extractions.
    """
    conn.row_factory = sqlite3.Row

    if opinion_id is not None:
        rows = conn.execute(
            """
            SELECT o.opinion_id, o.docket_id, c.case_name, c.court_id,
                   o.plain_text, cl.procedural_stage, cl.outcome_label
            FROM opinions o
            JOIN cases c ON o.docket_id = c.docket_id
            LEFT JOIN case_labels cl ON o.opinion_id = cl.opinion_id
            WHERE o.opinion_id = ?
              AND o.plain_text IS NOT NULL AND length(o.plain_text) > 1000
            """,
            (opinion_id,),
        ).fetchall()
    else:
        query = """
            SELECT o.opinion_id, o.docket_id, c.case_name, c.court_id,
                   o.plain_text, cl.procedural_stage, cl.outcome_label
            FROM opinions o
            JOIN cases c ON o.docket_id = c.docket_id
            JOIN case_labels cl ON o.opinion_id = cl.opinion_id
            WHERE cl.contamination_type = 'PRIVATE'
              AND cl.outcome_label NOT IN ('UNLABELED', 'UNCLEAR')
              AND o.plain_text IS NOT NULL AND length(o.plain_text) > 1000
              AND o.opinion_id NOT IN (
                  SELECT opinion_id FROM irac_extractions WHERE is_valid = 1
              )
            ORDER BY length(o.plain_text) ASC
        """
        if limit:
            query += f" LIMIT {limit}"
        rows = conn.execute(query).fetchall()

    return [dict(r) for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 1.3: Extract IRAC structures from judicial opinions",
    )
    parser.add_argument(
        "--db", type=Path, default=Path("data/private_10b5_sample_416.db"),
        help="SQLite database path",
    )
    parser.add_argument(
        "--llm-url", type=str, default="http://localhost:8000",
        help="vLLM endpoint URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process only N opinions",
    )
    parser.add_argument(
        "--opinion-id", type=int, default=None,
        help="Extract a single opinion by ID",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print prompts without calling LLM",
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="Use mock LLM response (test pipeline locally)",
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"ERROR: Database not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    # Determine mode
    if args.dry_run:
        mode = "dry-run"
    elif args.mock:
        mode = "mock"
    else:
        mode = "live"

    conn = sqlite3.connect(str(args.db))
    init_irac_table(conn)

    # Get opinions
    opinions = get_opinions_to_process(conn, limit=args.limit, opinion_id=args.opinion_id)

    if not opinions:
        print("No opinions to process (all done or none match criteria).")
        conn.close()
        return

    logger.info(f"Processing {len(opinions)} opinions in {mode} mode")

    # Create LLM client (only needed for live mode)
    client = None
    if mode == "live":
        client = LLMClient(base_url=args.llm_url)
        logger.info(f"LLM endpoint: {args.llm_url}")

    # Process each opinion
    stats = {"valid": 0, "invalid": 0, "error": 0, "dry-run": 0, "invalid_subs": 0}

    for i, op in enumerate(opinions, 1):
        logger.info(
            f"[{i}/{len(opinions)}] Opinion {op['opinion_id']}: "
            f"{op['case_name'][:50]} ({len(op['plain_text']):,} chars)"
        )

        result = extract_opinion(
            conn=conn,
            opinion_id=op["opinion_id"],
            docket_id=op["docket_id"],
            case_name=op["case_name"],
            court_id=op["court_id"],
            plain_text=op["plain_text"],
            procedural_stage=op.get("procedural_stage"),
            client=client,
            mode=mode,
        )

        status = result["status"]
        stats[status] = stats.get(status, 0) + 1

        if result.get("outcome"):
            logger.info(f"  → {status}: outcome={result['outcome']}")
        elif result.get("errors"):
            logger.warning(f"  → {status}: {result['errors']}")

    # Summary
    print(f"\n{'='*55}")
    print(f"  EXTRACTION SUMMARY ({mode} mode)")
    print(f"{'='*55}")
    print(f"  Total processed:  {len(opinions)}")
    for k, v in sorted(stats.items()):
        if v > 0:
            print(f"    {k:15s} {v}")

    if mode != "dry-run":
        # Show DB stats
        row = conn.execute(
            "SELECT is_valid, COUNT(*) FROM irac_extractions GROUP BY is_valid"
        ).fetchall()
        if row:
            print(f"\n  Database totals:")
            for valid, count in row:
                label = "valid" if valid else "invalid"
                print(f"    {label:15s} {count}")

    conn.close()


if __name__ == "__main__":
    main()
