"""
Phase 1 element-level evaluation against human annotations.

Computes per-element status accuracy (pipeline vs Emre),
element-level balanced accuracy, and confusion matrices.
Requires human_annotations table populated by Emre.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from script.lifting.schema import ElementStatus

from .bootstrap import balanced_accuracy, bootstrap_ci, format_ci
from .config import DEV_EXCLUDED_DOCKETS

logger = logging.getLogger(__name__)

ELEMENT_NAMES = [
    "material_misrepresentation",
    "scienter",
    "connection",
    "reliance",
    "economic_loss",
    "loss_causation",
]

STATUS_LABELS = ["SATISFIED", "NOT_SATISFIED", "CONTESTED", "NOT_ANALYZED"]
STATUS_TO_INT = {s: i for i, s in enumerate(STATUS_LABELS)}


def compute_element_accuracy(db_path: Path) -> dict[str, Any]:
    """
    Compare pipeline IRAC element statuses against human annotations.

    Returns per-element accuracy, overall accuracy, and confusion data.
    """
    conn = sqlite3.connect(str(db_path))

    # Check human annotations exist
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
        "SELECT docket_id, element_statuses, outcome FROM human_annotations "
        "WHERE annotator = 'emre' ORDER BY annotation_date DESC"
    ).fetchall()

    if not human_rows:
        conn.close()
        return {
            "status": "WAITING",
            "message": "No annotations by Emre found",
        }

    # Deduplicate (latest per docket)
    human: dict[int, dict[str, str]] = {}
    human_outcomes: dict[int, str] = {}
    for did, elem_json, outcome in human_rows:
        if did not in human:
            human[did] = json.loads(elem_json)
            human_outcomes[did] = outcome

    # Load pipeline IRAC extractions
    pipeline: dict[int, dict[str, str]] = {}
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
            elements = ext.get("elements", {})
            pipeline[did] = {
                name: elem.get("status", "NOT_ANALYZED")
                for name, elem in elements.items()
                if isinstance(elem, dict)
            }

    conn.close()

    # Compare element by element
    per_element: dict[str, dict[str, Any]] = {}
    all_true: list[int] = []
    all_pred: list[int] = []

    for elem_name in ELEMENT_NAMES:
        y_true_list: list[int] = []
        y_pred_list: list[int] = []

        for did in pipeline:
            if did not in human:
                continue
            human_status = human[did].get(elem_name, "NOT_ANALYZED")
            pipe_status = pipeline[did].get(elem_name, "NOT_ANALYZED")

            if human_status not in STATUS_TO_INT or pipe_status not in STATUS_TO_INT:
                continue

            y_true_list.append(STATUS_TO_INT[human_status])
            y_pred_list.append(STATUS_TO_INT[pipe_status])

        y_true = np.array(y_true_list)
        y_pred = np.array(y_pred_list)

        if len(y_true) == 0:
            per_element[elem_name] = {"n": 0, "accuracy": 0.0}
            continue

        acc = float((y_true == y_pred).mean())
        ba = balanced_accuracy(y_true, y_pred)
        pt, lo, hi = bootstrap_ci((y_true == y_pred).astype(float))

        # Confusion counts
        confusion: dict[str, dict[str, int]] = {}
        for t, p in zip(y_true, y_pred):
            t_label = STATUS_LABELS[t]
            p_label = STATUS_LABELS[p]
            confusion.setdefault(t_label, {}).setdefault(p_label, 0)
            confusion[t_label][p_label] += 1

        per_element[elem_name] = {
            "n": len(y_true),
            "accuracy": acc,
            "balanced_accuracy": ba,
            "accuracy_ci": format_ci(pt, lo, hi),
            "confusion": confusion,
        }

        all_true.extend(y_true_list)
        all_pred.extend(y_pred_list)

    # Overall
    all_true_arr = np.array(all_true)
    all_pred_arr = np.array(all_pred)
    overall_acc = float((all_true_arr == all_pred_arr).mean()) if len(all_true) > 0 else 0.0
    overall_ba = balanced_accuracy(all_true_arr, all_pred_arr) if len(all_true) > 0 else 0.0

    return {
        "status": "OK",
        "n_cases_compared": len(pipeline),
        "n_human_annotations": len(human),
        "per_element": per_element,
        "overall_accuracy": overall_acc,
        "overall_balanced_accuracy": overall_ba,
        "total_comparisons": len(all_true),
    }


def print_element_accuracy(result: dict[str, Any]) -> None:
    """Print element accuracy summary."""
    print(f"\n{'='*65}")
    print("  ELEMENT-LEVEL ACCURACY (Pipeline vs Human Annotations)")
    print(f"{'='*65}")

    if result.get("status") == "WAITING":
        print(f"  {result['message']}")
        print(f"{'='*65}\n")
        return

    print(f"  Cases compared: {result['n_cases_compared']}")
    print(f"  Total comparisons: {result['total_comparisons']}")
    print(f"  Overall accuracy: {result['overall_accuracy']*100:.1f}%")
    print(f"  Overall balanced accuracy: {result['overall_balanced_accuracy']*100:.1f}%")

    print(f"\n  {'Element':<30} {'Acc':>6} {'Bal.Acc':>8} {'N':>4}  CI")
    print(f"  {'─'*30} {'─'*6} {'─'*8} {'─'*4}  {'─'*25}")
    for name in ELEMENT_NAMES:
        e = result["per_element"].get(name, {})
        if e.get("n", 0) == 0:
            print(f"  {name:<30} {'—':>6} {'—':>8} {'0':>4}")
        else:
            print(
                f"  {name:<30} {e['accuracy']*100:5.1f}% "
                f"{e['balanced_accuracy']*100:7.1f}% "
                f"{e['n']:4d}  {e.get('accuracy_ci', '')}"
            )

    print(f"{'='*65}\n")
