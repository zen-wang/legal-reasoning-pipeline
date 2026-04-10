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
  python script/lift_opinions.py --db data/private_10b5_sample_416.db --llm-url http://gaudi004:8000

  # Sol: run with 4 concurrent requests
  python script/lift_opinions.py --db data/private_10b5_sample_416.db --llm-url http://gaudi004:8000 --concurrency 4
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
              AND cl.outcome_label NOT IN ('UNLABELED')
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


def _process_one(
    op: dict,
    idx: int,
    total: int,
    db_path: str,
    client: LLMClient | None,
    mode: str,
) -> tuple[str, float, str | None]:
    """Process a single opinion in its own thread with its own DB connection."""
    logger.info(
        f"[{idx}/{total}] Opinion {op['opinion_id']}: "
        f"{op['case_name'][:50]} ({len(op['plain_text']):,} chars)"
    )

    conn = sqlite3.connect(db_path)
    init_irac_table(conn)

    t0 = time.time()
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
    elapsed = time.time() - t0
    conn.close()

    status = result["status"]
    outcome = result.get("outcome")

    if outcome:
        logger.info(f"  → {status}: outcome={outcome} ({elapsed:.1f}s)")
    elif result.get("errors"):
        logger.warning(f"  → {status}: {result['errors']} ({elapsed:.1f}s)")

    return (status, elapsed, outcome)


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
        "--concurrency", type=int, default=1,
        help="Number of concurrent LLM requests (default: 1)",
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
    conn.close()

    if not opinions:
        print("No opinions to process (all done or none match criteria).")
        return

    logger.info(f"Processing {len(opinions)} opinions in {mode} mode (concurrency={args.concurrency})")

    # Create LLM client (shared across threads — stateless HTTP)
    client = None
    if mode == "live":
        client = LLMClient(base_url=args.llm_url)
        logger.info(f"LLM endpoint: {args.llm_url}")

    # Process opinions
    stats: dict[str, int] = {}
    durations: list[float] = []
    batch_start = time.time()

    if args.concurrency <= 1:
        # Sequential
        for i, op in enumerate(opinions, 1):
            status, elapsed, _ = _process_one(op, i, len(opinions), str(args.db), client, mode)
            stats[status] = stats.get(status, 0) + 1
            durations.append(elapsed)
    else:
        # Concurrent
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {
                executor.submit(_process_one, op, i, len(opinions), str(args.db), client, mode): op
                for i, op in enumerate(opinions, 1)
            }
            for future in as_completed(futures):
                try:
                    status, elapsed, _ = future.result()
                    stats[status] = stats.get(status, 0) + 1
                    durations.append(elapsed)
                except Exception as e:
                    import traceback
                    logger.error(f"Thread error: {e}\n{traceback.format_exc()}")
                    stats["error"] = stats.get("error", 0) + 1

    # Summary
    batch_elapsed = time.time() - batch_start
    print(f"\n{'='*55}")
    print(f"  EXTRACTION SUMMARY ({mode} mode, concurrency={args.concurrency})")
    print(f"{'='*55}")
    print(f"  Total processed:  {len(opinions)}")
    for k, v in sorted(stats.items()):
        if v > 0:
            print(f"    {k:15s} {v}")

    if durations:
        avg = sum(durations) / len(durations)
        print(f"\n  Timing:")
        print(f"    Wall time:      {batch_elapsed:.1f}s ({batch_elapsed/60:.1f} min)")
        print(f"    Avg per case:   {avg:.1f}s")
        print(f"    Min / Max:      {min(durations):.1f}s / {max(durations):.1f}s")
        if args.concurrency > 1:
            print(f"    Throughput:     {len(durations)/batch_elapsed*60:.1f} cases/min")

    if mode != "dry-run":
        conn = sqlite3.connect(str(args.db))
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
