"""
Phase 2: Build the Neo4j knowledge graph from SQLite data.

Loads cases, opinions, citations, arguments, judges, and parties
into Neo4j with signed edges for ANCO-HITS argument scoring.

Usage:
  # Full load
  python script/build_graph.py --db data/private_10b5_sample_416.db

  # Wipe and reload
  python script/build_graph.py --db data/private_10b5_sample_416.db --clear

  # Verify only (no loading)
  python script/build_graph.py --db data/private_10b5_sample_416.db --verify

  # Dry run (print stats, no Neo4j writes)
  python script/build_graph.py --db data/private_10b5_sample_416.db --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

from script.graph.connect import (
    clear_graph,
    ensure_constraints,
    get_driver,
    neo4j_session,
)
from script.graph.load_edges import (
    load_charged_under_edges,
    load_citation_edges,
    load_decided_by_edges,
    load_defendant_edges,
    load_has_opinion_edges,
    load_involves_edges,
    load_represented_by_edges,
)
from script.graph.load_nodes import (
    load_argument_nodes,
    load_case_nodes,
    load_company_nodes,
    load_firm_nodes,
    load_judge_nodes,
    load_opinion_nodes,
    load_statute_nodes,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dry-run: print SQLite stats without touching Neo4j
# ---------------------------------------------------------------------------

def print_dry_run_stats(conn: sqlite3.Connection) -> None:
    """Print data counts from SQLite for planning."""
    queries = [
        ("Cases", "SELECT COUNT(*) FROM cases"),
        ("Opinions", "SELECT COUNT(*) FROM opinions"),
        ("Citation edges", "SELECT COUNT(*) FROM citation_edges"),
        ("Parties", "SELECT COUNT(*) FROM parties"),
        ("Attorneys", "SELECT COUNT(*) FROM attorneys"),
        ("Case labels", "SELECT COUNT(*) FROM case_labels"),
        ("IRAC extractions (valid)", "SELECT COUNT(*) FROM irac_extractions WHERE is_valid = 1"),
    ]

    print(f"\n{'='*50}")
    print("  DRY RUN — SQLite Data Counts")
    print(f"{'='*50}")
    for label, sql in queries:
        count = conn.execute(sql).fetchone()[0]
        print(f"  {label:30s} {count:>6,}")

    # IRAC argument counts
    rows = conn.execute(
        "SELECT extraction FROM irac_extractions WHERE is_valid = 1"
    ).fetchall()
    total_plt_args = 0
    total_def_args = 0
    for r in rows:
        ext = json.loads(r[0])
        total_plt_args += len(ext.get("arguments_plaintiff", []))
        total_def_args += len(ext.get("arguments_defendant", []))

    print(f"\n  IRAC arguments (plaintiff):   {total_plt_args:>6,}")
    print(f"  IRAC arguments (defendant):   {total_def_args:>6,}")
    print(f"  IRAC arguments (total):       {total_plt_args + total_def_args:>6,}")

    # Outcome distribution in IRAC
    outcome_counts: dict[str, int] = {}
    for r in rows:
        ext = json.loads(r[0])
        outcome = ext.get("outcome", "UNKNOWN")
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
    print(f"\n  IRAC outcome distribution:")
    for k, v in sorted(outcome_counts.items()):
        print(f"    {k:20s} {v:>4}")

    print(f"{'='*50}\n")


# ---------------------------------------------------------------------------
# Verification queries
# ---------------------------------------------------------------------------

def run_verification(driver) -> None:
    """Run verification queries against the loaded graph."""
    with neo4j_session(driver) as session:
        print(f"\n{'='*55}")
        print("  GRAPH VERIFICATION")
        print(f"{'='*55}")

        # Node counts
        print("\n  Node counts:")
        for label in ["Case", "Opinion", "Statute", "LegalArgument", "Judge", "Company", "LawFirm"]:
            count = session.run(
                f"MATCH (n:{label}) RETURN count(n) AS cnt"
            ).single()["cnt"]
            print(f"    {label:20s} {count:>6,}")

        # Edge counts
        print("\n  Edge counts:")
        for rel_type in ["HAS_OPINION", "CITES", "CHARGED_UNDER", "INVOLVES", "DECIDED_BY", "DEFENDANT_IS", "REPRESENTED_BY"]:
            count = session.run(
                f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS cnt"
            ).single()["cnt"]
            print(f"    {rel_type:20s} {count:>6,}")

        # INVOLVES sign distribution
        print("\n  INVOLVES sign distribution:")
        result = session.run(
            "MATCH ()-[r:INVOLVES]->() "
            "RETURN r.sign AS sign, count(r) AS cnt "
            "ORDER BY sign"
        )
        for record in result:
            sign_label = {1: "+1 (won)", -1: "-1 (lost)", 0: " 0 (neutral)"}.get(
                record["sign"], str(record["sign"])
            )
            print(f"    sign={sign_label}: {record['cnt']}")

        # ANCO-HITS readiness
        print("\n  ANCO-HITS bipartite stats:")
        result = session.run(
            "MATCH (c:Case)-[r:INVOLVES]->(a:LegalArgument) "
            "RETURN count(DISTINCT c) AS cases, "
            "       count(DISTINCT a) AS arguments, "
            "       count(r) AS edges"
        ).single()
        print(f"    Cases with arguments:  {result['cases']}")
        print(f"    Unique arguments:      {result['arguments']}")
        print(f"    Signed edges:          {result['edges']}")

        # Internal vs external opinions
        print("\n  Opinion nodes:")
        for internal in [True, False]:
            label = "internal" if internal else "external"
            count = session.run(
                "MATCH (o:Opinion {internal: $internal}) RETURN count(o) AS cnt",
                internal=internal,
            ).single()["cnt"]
            print(f"    {label:20s} {count:>6,}")

        # Sample citation chain
        print("\n  Sample 3-hop citation chain:")
        result = session.run(
            "MATCH path = (o1:Opinion {internal: true})-[:CITES]->(o2:Opinion)-[:CITES]->(o3:Opinion) "
            "RETURN o1.opinion_id AS src, o2.opinion_id AS mid, o3.opinion_id AS tgt "
            "LIMIT 3"
        )
        for record in result:
            print(f"    {record['src']} → {record['mid']} → {record['tgt']}")

        # Orphan check
        orphan_count = session.run(
            "MATCH (n) WHERE NOT (n)--() RETURN count(n) AS cnt"
        ).single()["cnt"]
        print(f"\n  Orphan nodes (no edges): {orphan_count}")

        print(f"{'='*55}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 2: Build Neo4j knowledge graph from SQLite",
    )
    parser.add_argument(
        "--db", type=Path, default=Path("data/private_10b5_sample_416.db"),
        help="SQLite database path",
    )
    parser.add_argument(
        "--neo4j-uri", type=str, default=None,
        help="Neo4j URI (default: from .env or bolt://localhost:7687)",
    )
    parser.add_argument(
        "--clear", action="store_true",
        help="Delete all nodes/edges before loading",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Run verification queries only (no loading)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print SQLite stats without writing to Neo4j",
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"ERROR: Database not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(args.db))

    # Dry run — just print stats
    if args.dry_run:
        print_dry_run_stats(conn)
        conn.close()
        return

    # Connect to Neo4j
    driver = get_driver(uri=args.neo4j_uri)
    try:
        driver.verify_connectivity()
        logger.info("Connected to Neo4j")
    except Exception as e:
        print(f"ERROR: Cannot connect to Neo4j: {e}", file=sys.stderr)
        print("Start Neo4j with: docker run -d --name legal-neo4j "
              "-p 7474:7474 -p 7687:7687 "
              "-e NEO4J_AUTH=neo4j/legal_graph_2026 "
              "-v neo4j-data:/data neo4j:5-community")
        conn.close()
        sys.exit(1)

    # Verify only
    if args.verify:
        run_verification(driver)
        conn.close()
        driver.close()
        return

    # Full load
    t0 = time.time()

    with neo4j_session(driver) as session:
        # Step 0: Clear if requested
        if args.clear:
            logger.info("Clearing existing graph...")
            clear_graph(session)

        # Step 1: Schema (constraints + indexes)
        logger.info("Creating constraints and indexes...")
        ensure_constraints(session)

        # Step 2: Nodes
        logger.info("Loading nodes...")
        node_counts = {}
        node_counts["Case"] = load_case_nodes(session, conn)
        node_counts["Opinion"] = load_opinion_nodes(session, conn)
        node_counts["Statute"] = load_statute_nodes(session, conn)
        node_counts["LegalArgument"] = load_argument_nodes(session, conn)
        node_counts["Judge"] = load_judge_nodes(session, conn)
        node_counts["Company"] = load_company_nodes(session, conn)
        node_counts["LawFirm"] = load_firm_nodes(session, conn)

        # Step 3: Edges
        logger.info("Loading edges...")
        edge_counts = {}
        edge_counts["HAS_OPINION"] = load_has_opinion_edges(session, conn)
        edge_counts["CITES"] = load_citation_edges(session, conn)
        edge_counts["CHARGED_UNDER"] = load_charged_under_edges(session, conn)
        edge_counts["INVOLVES"] = load_involves_edges(session, conn)
        edge_counts["DECIDED_BY"] = load_decided_by_edges(session, conn)
        edge_counts["DEFENDANT_IS"] = load_defendant_edges(session, conn)
        edge_counts["REPRESENTED_BY"] = load_represented_by_edges(session, conn)

    elapsed = time.time() - t0

    # Summary
    print(f"\n{'='*55}")
    print(f"  KNOWLEDGE GRAPH BUILD COMPLETE ({elapsed:.1f}s)")
    print(f"{'='*55}")
    print(f"\n  Nodes:")
    for label, count in node_counts.items():
        print(f"    {label:20s} {count:>6,}")
    print(f"    {'TOTAL':20s} {sum(node_counts.values()):>6,}")

    print(f"\n  Edges:")
    for rel, count in edge_counts.items():
        print(f"    {rel:20s} {count:>6,}")
    print(f"    {'TOTAL':20s} {sum(edge_counts.values()):>6,}")

    print(f"\n  Time: {elapsed:.1f}s")
    print(f"{'='*55}\n")

    # Auto-verify
    run_verification(driver)

    conn.close()
    driver.close()


if __name__ == "__main__":
    main()
