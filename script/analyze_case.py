"""
Phase 5: Constrained RAG case analysis CLI.

Analyzes a Private 10b-5 case by retrieving relevant precedents,
applying symbolic constraints, and optionally generating an IRAC
analysis via LLM (lowering step from Beyond the Black Box).

Usage:
  # One-time embedding generation
  python -m script.analyze_case --db data/private_10b5_sample_416.db --embed-only

  # Dry run (show retrieval results only, no LLM)
  python -m script.analyze_case --db data/private_10b5_sample_416.db --docket-id 67890 --dry-run

  # Symbolic only (retrieval + constraints, no LLM generation)
  python -m script.analyze_case --db data/private_10b5_sample_416.db --docket-id 67890 --symbolic-only

  # Full analysis (requires LLM on Sol/Gaudi 2)
  python -m script.analyze_case --db data/private_10b5_sample_416.db --docket-id 67890 --llm-url http://gaudi:8000
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Embedding sub-command
# ---------------------------------------------------------------------------


def run_embed(db_path: Path) -> None:
    """Generate and cache SBERT embeddings for all opinions."""
    from script.rag.embeddings import encode_opinions, write_embeddings_to_sqlite

    t0 = time.time()
    embeddings = encode_opinions(db_path)

    conn = sqlite3.connect(str(db_path))
    write_embeddings_to_sqlite(conn, embeddings)
    conn.close()

    elapsed = time.time() - t0
    print(f"\nEmbedded {len(embeddings)} opinions in {elapsed:.1f}s")
    print(f"Stored in {db_path} → opinion_embeddings table")


# ---------------------------------------------------------------------------
# Analysis pipeline
# ---------------------------------------------------------------------------


def run_analysis(
    db_path: Path,
    docket_id: int,
    dry_run: bool = False,
    symbolic_only: bool = False,
    llm_url: str | None = None,
    neo4j_uri: str | None = None,
    timeout: int = 600,
    max_tokens: int = 2048,
) -> None:
    """Run the full analysis pipeline for a single case."""
    from script.lifting.llm_client import LLMClient
    from script.lifting.store import load_extraction
    from script.rag.constraints import load_constraint_context
    from script.rag.context import build_context
    from script.rag.lower import lower
    from script.rag.rank import rank_precedents
    from script.rag.retrieve import retrieve

    conn = sqlite3.connect(str(db_path))

    # Verify case exists
    row = conn.execute(
        "SELECT case_name, court_id, date_filed FROM cases WHERE docket_id = ?",
        (docket_id,),
    ).fetchone()
    if not row:
        print(f"ERROR: Case {docket_id} not found in database", file=sys.stderr)
        conn.close()
        sys.exit(1)

    case_name, court_id, date_filed = row
    print(f"\n{'='*60}")
    print(f"  CASE: {case_name}")
    print(f"  Docket ID: {docket_id} | Court: {court_id} | Filed: {date_filed}")
    print(f"{'='*60}")

    # Load IRAC extraction for query case
    opinion_row = conn.execute(
        "SELECT opinion_id FROM opinions WHERE docket_id = ? "
        "AND plain_text IS NOT NULL ORDER BY opinion_id LIMIT 1",
        (docket_id,),
    ).fetchone()
    query_irac = None
    query_text = ""
    if opinion_row:
        query_irac = load_extraction(conn, opinion_row[0])
        text_row = conn.execute(
            "SELECT plain_text FROM opinions WHERE opinion_id = ?",
            (opinion_row[0],),
        ).fetchone()
        if text_row and text_row[0]:
            from script.lifting.preprocess import get_analysis_text, split_sections
            sections = split_sections(text_row[0])
            query_text = get_analysis_text(sections)

    if query_irac:
        print(f"\n  IRAC extraction: {query_irac.outcome} ({query_irac.procedural_stage})")
    else:
        print("\n  No IRAC extraction available for this case")

    conn.close()

    # Neo4j driver (optional)
    driver = None
    if neo4j_uri != "none":
        try:
            from script.graph.connect import get_driver
            driver = get_driver(uri=neo4j_uri)
            driver.verify_connectivity()
            logger.info("Neo4j connected")
        except Exception as e:
            logger.info(f"Neo4j unavailable ({e}), using semantic-only retrieval")
            driver = None

    # Step 1: Retrieve
    t0 = time.time()
    candidates = retrieve(
        query_text=query_text,
        docket_id=docket_id,
        db_path=db_path,
        driver=driver,
        top_k=20,
    )
    retrieve_time = time.time() - t0

    # Step 2: Rank
    ranked = rank_precedents(candidates, top_k=10)

    print(f"\n  Retrieved {len(candidates)} candidates → ranked top {len(ranked)}")
    print(f"  Retrieval time: {retrieve_time:.2f}s\n")

    # Print ranked results
    print(f"  {'#':>3}  {'Score':>6}  {'Sem':>5}  {'Graph':>8}  {'ANCO':>6}  Case")
    print(f"  {'─'*3}  {'─'*6}  {'─'*5}  {'─'*8}  {'─'*6}  {'─'*40}")
    for i, p in enumerate(ranked, 1):
        graph_label = p.graph_reason[:8] if p.graph_reason else "—"
        irac_mark = "*" if p.irac_extraction else " "
        print(
            f"  {i:3d}  {p.final_score:6.3f}  {p.semantic_score:5.3f}  "
            f"{graph_label:>8}  {p.anco_hits_score:+6.3f}  "
            f"{p.case_name[:40]}{irac_mark}"
        )
    print(f"\n  (* = has IRAC extraction)\n")

    if dry_run:
        if driver:
            driver.close()
        return

    # Step 3: Build context
    context_str, included_ids = build_context(
        query_docket_id=docket_id,
        query_case_name=case_name,
        query_irac=query_irac,
        ranked_precedents=ranked,
    )
    context_tokens = int(len(context_str) / 2.6)
    print(f"  Context: {context_tokens} tokens ({len(included_ids)} precedents packed)")

    # Step 4: Load constraints
    conn = sqlite3.connect(str(db_path))
    constraint_ctx = load_constraint_context(conn)
    conn.close()

    # Step 5: Lower (LLM or symbolic)
    client = None
    if llm_url and not symbolic_only:
        client = LLMClient(base_url=llm_url, timeout=timeout)
        logger.info(f"Using LLM at {llm_url}")

    t0 = time.time()
    result = lower(
        docket_id=docket_id,
        case_name=case_name,
        query_irac=query_irac,
        context_str=context_str,
        context_tokens=context_tokens,
        precedents=ranked,
        constraint_ctx=constraint_ctx,
        client=client,
        max_tokens=max_tokens,
    )
    lower_time = time.time() - t0

    # Step 6: Print result
    _print_result(result, lower_time)

    if driver:
        driver.close()


# ---------------------------------------------------------------------------
# Result display
# ---------------------------------------------------------------------------


def _print_result(result: object, lower_time: float) -> None:
    """Print the analysis result."""
    from script.rag.schema import IRACAnalysis, SymbolicOnlyResult

    print(f"\n{'='*60}")

    if isinstance(result, SymbolicOnlyResult):
        print("  SYMBOLIC-ONLY RESULT (no LLM generation)")
        print(f"{'='*60}")
        print(f"  ANCO-HITS score: {result.anco_hits_score:+.3f}")

        if result.irac_extraction:
            irac = result.irac_extraction
            print(f"\n  Outcome: {irac.outcome}")
            print(f"  Stage: {irac.procedural_stage}")
            print(f"\n  Elements:")
            for name, elem in [
                ("material_misrepresentation", irac.elements.material_misrepresentation),
                ("scienter", irac.elements.scienter),
                ("connection", irac.elements.connection),
                ("reliance", irac.elements.reliance),
                ("economic_loss", irac.elements.economic_loss),
                ("loss_causation", irac.elements.loss_causation),
            ]:
                print(f"    {name:30s} {elem.status.value}")

        print(f"\n  Top precedents:")
        for i, p in enumerate(result.ranked_precedents[:5], 1):
            print(f"    {i}. {p.case_name[:50]} (score={p.final_score:.3f})")

    elif isinstance(result, IRACAnalysis):
        print("  LLM-GENERATED IRAC ANALYSIS")
        print(f"{'='*60}")
        print(f"\n  Issue: {result.issue}")
        print(f"\n  Rule: {result.rule}")

        if result.application:
            print(f"\n  Application:")
            for elem in result.application:
                contested = " [CONTESTED]" if elem.contested else ""
                na = " [NOT ANALYZED]" if elem.not_analyzed else ""
                print(f"    {elem.element_name}: {elem.status.value}{contested}{na}")
                if elem.reasoning:
                    print(f"      {elem.reasoning[:200]}")

        print(f"\n  Conclusion: {result.conclusion}")

        if result.cited_precedents:
            print(f"\n  Citations ({len(result.cited_precedents)}):")
            for cp in result.cited_precedents:
                flags = []
                if cp.cross_circuit:
                    flags.append("[CROSS-CIRCUIT]")
                if not cp.verified:
                    flags.append("[UNVERIFIED]")
                flag_str = " ".join(flags)
                print(f"    - {cp.case_name} {flag_str}")

    # Constraint violations (both result types)
    violations = getattr(result, "constraint_violations", [])
    if violations:
        print(f"\n  Constraint violations ({len(violations)}):")
        for v in violations:
            print(f"    [{v.severity.value.upper()}] {v.constraint}: {v.message}")
    else:
        print(f"\n  Constraint violations: 0")

    print(f"\n  Lowering time: {lower_time:.2f}s")
    print(f"  LLM generated: {getattr(result, 'llm_generated', False)}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Batch golden cases
# ---------------------------------------------------------------------------


def run_batch_golden(db_path: Path, limit: int = 50) -> None:
    """Pre-compute analysis for golden demo cases (those with IRAC extractions)."""
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        """
        SELECT DISTINCT ie.docket_id, c.case_name
        FROM irac_extractions ie
        JOIN cases c ON ie.docket_id = c.docket_id
        WHERE ie.is_valid = 1
        ORDER BY ie.docket_id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()

    print(f"Batch analysis: {len(rows)} golden cases")
    for i, (did, name) in enumerate(rows, 1):
        print(f"\n[{i}/{len(rows)}] {name} (docket_id={did})")
        try:
            run_analysis(
                db_path=db_path,
                docket_id=did,
                symbolic_only=True,
                neo4j_uri="none",
            )
        except Exception as e:
            print(f"  ERROR: {e}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 5: Constrained RAG case analysis",
    )
    parser.add_argument(
        "--db", type=Path, default=Path("data/private_10b5_sample_416.db"),
        help="SQLite database path",
    )
    parser.add_argument(
        "--docket-id", type=int, default=None,
        help="Case docket ID to analyze",
    )
    parser.add_argument(
        "--llm-url", type=str, default=None,
        help="vLLM server URL (e.g., http://gaudi:8000)",
    )
    parser.add_argument(
        "--neo4j-uri", type=str, default=None,
        help="Neo4j URI (default: from .env, 'none' to skip)",
    )
    parser.add_argument(
        "--symbolic-only", action="store_true",
        help="Skip LLM generation, return symbolic result only",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show retrieval results only, no analysis",
    )
    parser.add_argument(
        "--embed-only", action="store_true",
        help="Generate SBERT embeddings only (one-time setup)",
    )
    parser.add_argument(
        "--timeout", type=int, default=600,
        help="LLM request timeout in seconds (default: 600)",
    )
    parser.add_argument(
        "--max-tokens", type=int, default=2048,
        help="Max output tokens for LLM generation (default: 2048)",
    )
    parser.add_argument(
        "--batch-golden", action="store_true",
        help="Batch analyze golden demo cases",
    )
    parser.add_argument(
        "--limit", type=int, default=50,
        help="Limit for --batch-golden (default: 50)",
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"ERROR: Database not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    if args.embed_only:
        run_embed(args.db)
        return

    if args.batch_golden:
        run_batch_golden(args.db, limit=args.limit)
        return

    if args.docket_id is None:
        print("ERROR: --docket-id is required (or use --embed-only / --batch-golden)", file=sys.stderr)
        sys.exit(1)

    run_analysis(
        db_path=args.db,
        docket_id=args.docket_id,
        dry_run=args.dry_run,
        symbolic_only=args.symbolic_only,
        llm_url=args.llm_url,
        neo4j_uri=args.neo4j_uri,
        timeout=args.timeout,
        max_tokens=args.max_tokens,
    )


if __name__ == "__main__":
    main()
