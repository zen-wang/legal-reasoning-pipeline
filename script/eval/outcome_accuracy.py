"""
Outcome prediction accuracy evaluation.

Compares pipeline outcome predictions against human ground truth.
Reports balanced accuracy with bootstrap CIs and per-class F1.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

from .bootstrap import balanced_accuracy, bootstrap_ci, format_ci
from .config import DEV_EXCLUDED_DOCKETS

logger = logging.getLogger(__name__)

OUTCOME_LABELS = ["DEFENDANT_WINS", "PLAINTIFF_WINS", "MIXED"]
OUTCOME_TO_INT = {l: i for i, l in enumerate(OUTCOME_LABELS)}


def compute_outcome_accuracy(db_path: Path) -> dict[str, Any]:
    """
    Compare pipeline outcome predictions against human annotations.

    Reports:
    1. Pipeline IRAC extraction outcome vs human
    2. Regex label vs human (B2 baseline context)
    3. Per-class precision, recall, F1
    """
    conn = sqlite3.connect(str(db_path))

    # Check human annotations
    if not conn.execute(
        "SELECT count(*) FROM sqlite_master "
        "WHERE type='table' AND name='human_annotations'"
    ).fetchone()[0]:
        conn.close()
        return {
            "status": "WAITING",
            "message": "human_annotations table not found — Emre annotations pending",
        }

    # Load human annotations
    human_rows = conn.execute(
        "SELECT docket_id, outcome FROM human_annotations "
        "ORDER BY annotation_date DESC"
    ).fetchall()

    if not human_rows:
        conn.close()
        return {"status": "WAITING", "message": "No human annotations found"}

    human: dict[int, str] = {}
    for did, outcome in human_rows:
        if did not in human:
            human[did] = outcome

    # Load pipeline predictions (IRAC extraction outcomes)
    pipeline: dict[int, str] = {}
    for did in human:
        if did in DEV_EXCLUDED_DOCKETS:
            continue
        row = conn.execute(
            "SELECT extraction FROM irac_extractions "
            "WHERE docket_id = ? AND is_valid = 1 "
            "ORDER BY created_at DESC LIMIT 1",
            (did,),
        ).fetchone()
        if row:
            ext = json.loads(row[0])
            pipeline[did] = ext.get("outcome", "")

    # Load regex labels for comparison
    regex: dict[int, str] = {}
    regex_rows = conn.execute(
        "SELECT docket_id, outcome_label FROM case_labels"
    ).fetchall()
    for did, label in regex_rows:
        regex[did] = label

    conn.close()

    # Compare pipeline vs human
    def compare(
        predictions: dict[int, str],
        ground_truth: dict[int, str],
        name: str,
    ) -> dict[str, Any]:
        y_true_list: list[int] = []
        y_pred_list: list[int] = []
        details: list[dict] = []

        for did in predictions:
            if did in DEV_EXCLUDED_DOCKETS or did not in ground_truth:
                continue
            true = ground_truth[did]
            pred = predictions[did]
            if true not in OUTCOME_TO_INT or pred not in OUTCOME_TO_INT:
                continue

            y_true_list.append(OUTCOME_TO_INT[true])
            y_pred_list.append(OUTCOME_TO_INT[pred])
            details.append({
                "docket_id": did,
                "true": true,
                "pred": pred,
                "correct": true == pred,
            })

        y_true = np.array(y_true_list)
        y_pred = np.array(y_pred_list)

        if len(y_true) == 0:
            return {"name": name, "n": 0}

        acc = float((y_true == y_pred).mean())
        ba = balanced_accuracy(y_true, y_pred)
        pt, lo, hi = bootstrap_ci((y_true == y_pred).astype(float))

        # Per-class metrics
        per_class: dict[str, dict[str, float]] = {}
        for label, idx in OUTCOME_TO_INT.items():
            tp = int(((y_true == idx) & (y_pred == idx)).sum())
            fp = int(((y_true != idx) & (y_pred == idx)).sum())
            fn = int(((y_true == idx) & (y_pred != idx)).sum())
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
            per_class[label] = {
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "support": int((y_true == idx).sum()),
            }

        return {
            "name": name,
            "n": len(y_true),
            "accuracy": acc,
            "balanced_accuracy": ba,
            "accuracy_ci": format_ci(pt, lo, hi),
            "per_class": per_class,
            "details": details,
        }

    pipeline_result = compare(pipeline, human, "Pipeline IRAC extraction")
    regex_result = compare(
        {d: regex[d] for d in human if d in regex and d not in DEV_EXCLUDED_DOCKETS},
        human,
        "Regex labels (B2 baseline)",
    )

    return {
        "status": "OK",
        "pipeline": pipeline_result,
        "regex_baseline": regex_result,
    }


def print_outcome_accuracy(result: dict[str, Any]) -> None:
    """Print outcome accuracy summary."""
    print(f"\n{'='*65}")
    print("  OUTCOME PREDICTION ACCURACY (vs Human Annotations)")
    print(f"{'='*65}")

    if result.get("status") == "WAITING":
        print(f"  {result['message']}")
        print(f"{'='*65}\n")
        return

    for key in ["pipeline", "regex_baseline"]:
        r = result[key]
        print(f"\n  {r['name']}:")
        if r.get("n", 0) == 0:
            print("    No data")
            continue

        print(f"    N={r['n']}  Accuracy={r['accuracy']*100:.1f}%  "
              f"Balanced={r['balanced_accuracy']*100:.1f}%")
        print(f"    CI: {r['accuracy_ci']}")

        if "per_class" in r:
            print(f"\n    {'Class':<20} {'Prec':>6} {'Rec':>6} {'F1':>6} {'Support':>8}")
            print(f"    {'─'*20} {'─'*6} {'─'*6} {'─'*6} {'─'*8}")
            for label in OUTCOME_LABELS:
                pc = r["per_class"].get(label, {})
                print(
                    f"    {label:<20} "
                    f"{pc.get('precision', 0)*100:5.1f}% "
                    f"{pc.get('recall', 0)*100:5.1f}% "
                    f"{pc.get('f1', 0)*100:5.1f}% "
                    f"{pc.get('support', 0):8d}"
                )

    print(f"{'='*65}\n")
