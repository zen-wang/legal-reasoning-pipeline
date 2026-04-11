"""
Phase 3 ANCO-HITS evaluation on held-out cases.

Computes AUC separating PLT_WINS vs DEF_WINS on cases that
were NOT used to build the bipartite graph (held-out evaluation).
Also reports on full set for reference (with caveat).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

from .bootstrap import bootstrap_ci, format_ci
from .config import DEV_EXCLUDED_DOCKETS

logger = logging.getLogger(__name__)


def compute_anco_evaluation(db_path: Path) -> dict[str, Any]:
    """
    Evaluate ANCO-HITS scores against outcome labels.

    Reports:
    1. AUC on all scored cases (reference only — includes training data)
    2. AUC on held-out cases (honest evaluation)
    3. Score distribution statistics
    4. Singleton ratio (fraction of arguments connected to exactly 1 case)
    """
    conn = sqlite3.connect(str(db_path))

    # Load ANCO-HITS case scores
    scores: dict[int, float] = {}
    table_exists = conn.execute(
        "SELECT count(*) FROM sqlite_master "
        "WHERE type='table' AND name='anco_hits_scores'"
    ).fetchone()[0]
    if not table_exists:
        conn.close()
        return {"error": "anco_hits_scores table not found"}

    rows = conn.execute(
        "SELECT entity_id, score FROM anco_hits_scores WHERE entity_type = 'case'"
    ).fetchall()
    for eid, score in rows:
        try:
            scores[int(eid)] = score
        except (ValueError, TypeError):
            pass

    # Load outcome labels
    labels = conn.execute(
        "SELECT docket_id, outcome_label FROM case_labels "
        "WHERE outcome_label IN ('DEFENDANT_WINS', 'PLAINTIFF_WINS')"
    ).fetchall()
    label_map = {did: label for did, label in labels}

    # Load bipartite case IDs (cases used in ANCO-HITS training)
    bipartite_cases: set[int] = set()
    irac_rows = conn.execute(
        "SELECT DISTINCT docket_id FROM irac_extractions WHERE is_valid = 1"
    ).fetchall()
    bipartite_cases = {r[0] for r in irac_rows}

    # Singleton ratio
    arg_rows = conn.execute(
        "SELECT entity_id, score FROM anco_hits_scores WHERE entity_type = 'argument'"
    ).fetchall()
    n_args = len(arg_rows)
    n_extremal = sum(1 for _, s in arg_rows if abs(s) > 0.99)

    conn.close()

    # Build evaluation arrays
    def compute_auc(case_ids: set[int], desc: str) -> dict[str, Any]:
        y_true_list: list[int] = []
        y_score_list: list[float] = []

        for did in case_ids:
            if did in DEV_EXCLUDED_DOCKETS:
                continue
            if did not in scores or did not in label_map:
                continue
            # Binary: PLT_WINS = 1, DEF_WINS = 0
            y_true_list.append(1 if label_map[did] == "PLAINTIFF_WINS" else 0)
            y_score_list.append(scores[did])

        if len(y_true_list) < 2 or len(set(y_true_list)) < 2:
            return {
                "description": desc,
                "n_cases": len(y_true_list),
                "auc": None,
                "note": "Insufficient data or single class",
            }

        from sklearn.metrics import roc_auc_score
        y_true = np.array(y_true_list)
        y_score = np.array(y_score_list)
        auc = roc_auc_score(y_true, y_score)

        # Spearman correlation
        from scipy.stats import spearmanr
        corr, pval = spearmanr(y_score, y_true)

        return {
            "description": desc,
            "n_cases": len(y_true),
            "n_plt": int(y_true.sum()),
            "n_def": int((1 - y_true).sum()),
            "auc": float(auc),
            "spearman_r": float(corr),
            "spearman_p": float(pval),
            "mean_plt_score": float(y_score[y_true == 1].mean()),
            "mean_def_score": float(y_score[y_true == 0].mean()),
        }

    # All scored cases (reference — NOT honest evaluation)
    all_scored = set(scores.keys())
    full_result = compute_auc(all_scored, "All scored cases (reference only — includes training data)")

    # Held-out cases (honest — not in bipartite graph)
    holdout = all_scored - bipartite_cases
    holdout_result = compute_auc(holdout, "Held-out cases (not in bipartite graph)")

    # Bipartite cases (training data — should be perfect)
    train_result = compute_auc(bipartite_cases, "Bipartite training cases (expected AUC=1.0)")

    return {
        "full_set": full_result,
        "held_out": holdout_result,
        "training_set": train_result,
        "n_total_scored_cases": len(scores),
        "n_bipartite_cases": len(bipartite_cases),
        "n_holdout_cases": len(holdout),
        "singleton_ratio": n_extremal / n_args if n_args > 0 else 0,
        "n_arguments": n_args,
        "n_extremal_arguments": n_extremal,
    }


def print_anco_evaluation(result: dict[str, Any]) -> None:
    """Print ANCO-HITS evaluation summary."""
    print(f"\n{'='*60}")
    print("  ANCO-HITS EVALUATION")
    print(f"{'='*60}")

    if "error" in result:
        print(f"  Error: {result['error']}")
        return

    print(f"  Total scored cases: {result['n_total_scored_cases']}")
    print(f"  Bipartite (training): {result['n_bipartite_cases']}")
    print(f"  Held-out: {result['n_holdout_cases']}")
    print(f"  Arguments: {result['n_arguments']} "
          f"(extremal: {result['n_extremal_arguments']}, "
          f"ratio: {result['singleton_ratio']:.1%})")

    for key in ["training_set", "held_out", "full_set"]:
        r = result[key]
        print(f"\n  {r['description']}:")
        print(f"    N={r['n_cases']}", end="")
        if r.get("auc") is not None:
            print(f"  AUC={r['auc']:.4f}  Spearman={r['spearman_r']:.3f} (p={r['spearman_p']:.3f})")
            print(f"    Mean PLT score={r['mean_plt_score']:+.3f}  Mean DEF score={r['mean_def_score']:+.3f}")
        else:
            print(f"  {r.get('note', 'No data')}")

    print(f"{'='*60}\n")
