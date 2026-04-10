"""
Phase 3: ANCO-HITS argument scoring on signed bipartite case-argument graph.

Scores every legal argument and case on [-1, +1] using the ANCO-HITS
algorithm from the NARRA-SCALE paper (Gokalp et al., ICTAI).

Usage:
  # Full run (Neo4j + SQLite + plots)
  python -m script.score_arguments --db data/private_10b5_sample_416.db

  # SQLite only (no Neo4j needed)
  python -m script.score_arguments --db data/private_10b5_sample_416.db --source sqlite

  # Dry run (bipartite stats only)
  python -m script.score_arguments --db data/private_10b5_sample_416.db --dry-run

  # Skip plots (headless)
  python -m script.score_arguments --db data/private_10b5_sample_416.db --no-plot
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

from script.scoring.anco_hits import anco_hits
from script.scoring.bipartite import BipartiteGraph, load_bipartite_from_neo4j, load_bipartite_from_sqlite
from script.scoring.validate import (
    compute_auc,
    plot_argument_distribution,
    plot_case_scores,
    plot_convergence,
    print_score_summary,
)
from script.scoring.write_scores import write_scores_to_neo4j, write_scores_to_sqlite

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Load bipartite graph (auto: try Neo4j, fall back to SQLite)
# ---------------------------------------------------------------------------

def load_bipartite(
    source: str,
    db_path: Path,
    neo4j_uri: str | None,
) -> tuple[BipartiteGraph, str]:
    """
    Load bipartite graph from specified source.

    Returns (bipartite, actual_source_used).
    """
    if source in ("neo4j", "auto"):
        try:
            from script.graph.connect import get_driver
            driver = get_driver(uri=neo4j_uri)
            driver.verify_connectivity()
            bg = load_bipartite_from_neo4j(driver)
            driver.close()
            return (bg, "neo4j")
        except Exception as e:
            if source == "neo4j":
                raise
            logger.info(f"Neo4j unavailable ({e}), falling back to SQLite")

    conn = sqlite3.connect(str(db_path))
    bg = load_bipartite_from_sqlite(conn)
    conn.close()
    return (bg, "sqlite")


# ---------------------------------------------------------------------------
# Load argument texts for display
# ---------------------------------------------------------------------------

def load_argument_texts(db_path: Path) -> dict[str, str]:
    """Load argument hash → text mapping from IRAC extractions."""
    from script.graph.resolve import normalize_argument

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT extraction FROM irac_extractions WHERE is_valid = 1"
    ).fetchall()
    conn.close()

    texts: dict[str, str] = {}
    for r in rows:
        ext = json.loads(r[0])
        for arg in ext.get("arguments_plaintiff", []):
            if arg:
                _, h = normalize_argument(arg)
                texts.setdefault(h, arg)
        for arg in ext.get("arguments_defendant", []):
            if arg:
                _, h = normalize_argument(arg)
                texts.setdefault(h, arg)
    return texts


# ---------------------------------------------------------------------------
# Dry-run stats
# ---------------------------------------------------------------------------

def print_bipartite_stats(bg: BipartiteGraph) -> None:
    """Print bipartite graph statistics."""
    outcomes = bg.case_outcomes
    signs = bg.sign_matrix

    print(f"\n{'='*55}")
    print("  BIPARTITE GRAPH STATISTICS")
    print(f"{'='*55}")
    print(f"  Cases:       {len(bg.case_ids)}")
    print(f"  Arguments:   {len(bg.argument_hashes)}")
    print(f"  Non-zero edges: {int(np.count_nonzero(signs))}")
    print(f"  Total edges:    {int((signs != 0).sum())}")

    print(f"\n  Case outcomes:")
    for val, name in [(1, "PLAINTIFF_WINS"), (-1, "DEFENDANT_WINS"), (0, "MIXED")]:
        print(f"    {name:18s} {int((outcomes == val).sum()):4d}")

    print(f"\n  Edge sign distribution:")
    for val, name in [(1, "+1 (won)"), (-1, "-1 (lost)"), (0, " 0 (neutral)")]:
        # Count non-zero entries for +1/-1, and explicit zeros in non-zero positions
        if val == 0:
            # Count edges that are explicitly 0 (from MIXED cases)
            count = int(((signs == 0) & (np.abs(bg.sign_matrix) == 0)).sum())
            # Actually for 0 edges, we need to count entries that are 0 but represent real edges
            # The sign_matrix uses 0 for both "no edge" and "MIXED edge"
            # We can't distinguish here, so just report from the non-zero count
            total_edges = int(np.count_nonzero(signs))
            count = int(signs.size) - total_edges  # This is wrong for sparse
            # Better: just count +1 and -1 from non-zero
            continue
        else:
            count = int((signs == val).sum())
            print(f"    sign={name}: {count}")

    # Degree distribution
    case_degrees = np.count_nonzero(signs, axis=1)
    arg_degrees = np.count_nonzero(signs, axis=0)
    print(f"\n  Case degree: mean={case_degrees.mean():.1f}, max={case_degrees.max()}")
    print(f"  Argument degree: mean={arg_degrees.mean():.1f}, max={arg_degrees.max()}")
    print(f"  Singleton arguments (degree=1): {int((arg_degrees == 1).sum())}")
    print(f"  Shared arguments (degree>1):    {int((arg_degrees > 1).sum())}")
    print(f"{'='*55}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 3: ANCO-HITS argument scoring",
    )
    parser.add_argument(
        "--db", type=Path, default=Path("data/private_10b5_sample_416.db"),
        help="SQLite database path",
    )
    parser.add_argument(
        "--neo4j-uri", type=str, default=None,
        help="Neo4j URI (default: from .env)",
    )
    parser.add_argument(
        "--source", choices=["neo4j", "sqlite", "auto"], default="auto",
        help="Data source (default: auto — try neo4j, fall back to sqlite)",
    )
    parser.add_argument(
        "--max-iterations", type=int, default=200,
        help="Max ANCO-HITS iterations (default: 200)",
    )
    parser.add_argument(
        "--epsilon", type=float, default=1e-6,
        help="Convergence threshold (default: 1e-6)",
    )
    parser.add_argument(
        "--no-write", action="store_true",
        help="Skip writing scores to databases",
    )
    parser.add_argument(
        "--no-plot", action="store_true",
        help="Skip generating matplotlib plots",
    )
    parser.add_argument(
        "--plot-dir", type=Path, default=Path("data"),
        help="Directory for plot files (default: data/)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print bipartite stats only, no scoring",
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"ERROR: Database not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    # Step 1: Load bipartite graph
    logger.info("Loading bipartite graph...")
    bg, source_used = load_bipartite(args.source, args.db, args.neo4j_uri)
    logger.info(f"Loaded from {source_used}: {len(bg.case_ids)} cases, {len(bg.argument_hashes)} arguments")

    # Print bipartite stats (always)
    print_bipartite_stats(bg)

    if args.dry_run:
        return

    # Step 2: Run ANCO-HITS
    logger.info(f"Running ANCO-HITS (max_iter={args.max_iterations}, eps={args.epsilon})...")
    t0 = time.time()
    argument_scores, case_scores, history = anco_hits(
        sign_matrix=bg.sign_matrix,
        case_seeds=bg.case_outcomes,
        max_iterations=args.max_iterations,
        epsilon=args.epsilon,
    )
    elapsed = time.time() - t0
    logger.info(f"ANCO-HITS converged in {len(history)} iterations ({elapsed:.3f}s)")

    # Step 3: Load argument texts for display
    argument_texts = load_argument_texts(args.db)

    # Step 4: Print summary (always)
    print_score_summary(
        argument_scores=argument_scores,
        case_scores=case_scores,
        bipartite=bg,
        convergence_history=history,
        argument_texts=argument_texts,
    )

    # Step 5: Plots
    if not args.no_plot:
        args.plot_dir.mkdir(parents=True, exist_ok=True)

        plot_case_scores(
            case_scores, bg.case_outcomes,
            args.plot_dir / "anco_hits_case_scores.png",
        )
        plot_argument_distribution(
            argument_scores,
            args.plot_dir / "anco_hits_argument_distribution.png",
        )
        plot_convergence(
            history,
            args.plot_dir / "anco_hits_convergence.png",
        )

    # Step 6: Write scores
    if not args.no_write:
        # SQLite (always)
        conn = sqlite3.connect(str(args.db))
        write_scores_to_sqlite(conn, bg, argument_scores, case_scores)
        conn.close()

        # Neo4j (if available)
        if source_used == "neo4j" or args.source == "neo4j":
            try:
                from script.graph.connect import get_driver
                driver = get_driver(uri=args.neo4j_uri)
                write_scores_to_neo4j(driver, bg, argument_scores, case_scores)
                driver.close()
            except Exception as e:
                logger.warning(f"Could not write to Neo4j: {e}")


if __name__ == "__main__":
    main()
