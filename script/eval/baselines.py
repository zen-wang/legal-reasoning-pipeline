"""
Five baselines for Phase 7 evaluation.

B1: Majority class (always DEF_WINS) — analytical floor
B2: Regex-only outcome labeling — tests whether symbolic lifting adds value
B3: ANCO-HITS score threshold — tests whether graph scoring adds value
B4: Zero-shot LLM (stub) — tests whether retrieval + constraints add value
B5: BM25 + LLM (stub) — tests whether graph + ANCO signals justify complexity

B4 and B5 require LLM access (Sol) and are computed separately.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import numpy as np

from .bootstrap import balanced_accuracy, bootstrap_ci, format_ci
from .config import (
    ANCO_THRESHOLD_DEF,
    ANCO_THRESHOLD_PLT,
    DEV_EXCLUDED_DOCKETS,
    EVAL_DOCKETS,
)

logger = logging.getLogger(__name__)

OUTCOME_CLASSES = ["DEFENDANT_WINS", "PLAINTIFF_WINS", "MIXED"]
OUTCOME_TO_INT = {label: i for i, label in enumerate(OUTCOME_CLASSES)}


# ---------------------------------------------------------------------------
# Load ground truth
# ---------------------------------------------------------------------------


def load_human_labels(conn: sqlite3.Connection) -> dict[int, str]:
    """Load human annotations (Emre) as ground truth. Falls back to regex labels."""
    # Try human annotations first
    table_exists = conn.execute(
        "SELECT count(*) FROM sqlite_master "
        "WHERE type='table' AND name='human_annotations'"
    ).fetchone()[0]

    if table_exists:
        rows = conn.execute(
            "SELECT docket_id, outcome FROM human_annotations "
            "ORDER BY annotation_date DESC"
        ).fetchall()
        if rows:
            # Deduplicate: keep latest per docket
            labels: dict[int, str] = {}
            for did, outcome in rows:
                if did not in labels:
                    labels[did] = outcome
            logger.info(f"Loaded {len(labels)} human annotations as ground truth")
            return labels

    # Fallback to regex labels (with warning)
    logger.warning(
        "No human annotations found — using regex labels as ground truth. "
        "This is CIRCULAR for outcome prediction (Rule 4 violation). "
        "Only valid for baselines comparison, not for claiming pipeline accuracy."
    )
    rows = conn.execute(
        "SELECT docket_id, outcome_label FROM case_labels "
        "WHERE outcome_label IN ('DEFENDANT_WINS', 'PLAINTIFF_WINS', 'MIXED')"
    ).fetchall()
    return {did: label for did, label in rows}


# ---------------------------------------------------------------------------
# B1: Majority class
# ---------------------------------------------------------------------------


def baseline_majority(
    ground_truth: dict[int, str],
    eval_dockets: list[int] | None = None,
) -> dict[str, object]:
    """B1: Always predict the majority class (DEF_WINS)."""
    dockets = eval_dockets or EVAL_DOCKETS
    labels = [ground_truth[d] for d in dockets if d in ground_truth]

    if not labels:
        return {"name": "B1_majority", "error": "No labels found"}

    # Find majority class
    from collections import Counter
    counts = Counter(labels)
    majority = counts.most_common(1)[0][0]

    y_true = np.array([OUTCOME_TO_INT.get(l, -1) for l in labels])
    y_pred = np.full_like(y_true, OUTCOME_TO_INT[majority])

    ba = balanced_accuracy(y_true, y_pred)

    return {
        "name": "B1_majority",
        "description": f"Always predict {majority}",
        "n_cases": len(labels),
        "majority_class": majority,
        "class_distribution": dict(counts),
        "balanced_accuracy": ba,
        "expected": 1.0 / len(set(labels)),
    }


# ---------------------------------------------------------------------------
# B2: Regex-only outcome
# ---------------------------------------------------------------------------


def baseline_regex(
    conn: sqlite3.Connection,
    ground_truth: dict[int, str],
    eval_dockets: list[int] | None = None,
) -> dict[str, object]:
    """B2: Use regex classify_outcome() on raw opinion text."""
    from script.label_and_split import classify_outcome

    dockets = eval_dockets or EVAL_DOCKETS

    y_true_list: list[int] = []
    y_pred_list: list[int] = []
    details: list[dict] = []

    for did in dockets:
        if did in DEV_EXCLUDED_DOCKETS or did not in ground_truth:
            continue

        # Get opinion text
        row = conn.execute(
            "SELECT plain_text FROM opinions WHERE docket_id = ? "
            "AND plain_text IS NOT NULL ORDER BY opinion_id LIMIT 1",
            (did,),
        ).fetchone()

        if not row or not row[0]:
            continue

        result = classify_outcome(row[0])
        pred = result.outcome
        true = ground_truth[did]

        if pred not in OUTCOME_TO_INT or true not in OUTCOME_TO_INT:
            continue

        y_true_list.append(OUTCOME_TO_INT[true])
        y_pred_list.append(OUTCOME_TO_INT[pred])
        details.append({
            "docket_id": did,
            "true": true,
            "pred": pred,
            "correct": true == pred,
            "method": result.source,
            "confidence": result.confidence,
        })

    y_true = np.array(y_true_list)
    y_pred = np.array(y_pred_list)

    ba = balanced_accuracy(y_true, y_pred)
    pt, lo, hi = bootstrap_ci(
        (y_true == y_pred).astype(float),
    )

    return {
        "name": "B2_regex",
        "description": "Regex classify_outcome() on opinion text",
        "n_cases": len(y_true),
        "balanced_accuracy": ba,
        "raw_accuracy": float((y_true == y_pred).mean()),
        "raw_accuracy_ci": format_ci(pt, lo, hi),
        "details": details,
    }


# ---------------------------------------------------------------------------
# B3: ANCO-HITS threshold
# ---------------------------------------------------------------------------


def baseline_anco_threshold(
    conn: sqlite3.Connection,
    ground_truth: dict[int, str],
    eval_dockets: list[int] | None = None,
) -> dict[str, object]:
    """B3: Predict outcome from ANCO-HITS case score with thresholds."""
    dockets = eval_dockets or EVAL_DOCKETS

    # Load ANCO-HITS scores
    scores: dict[int, float] = {}
    table_exists = conn.execute(
        "SELECT count(*) FROM sqlite_master "
        "WHERE type='table' AND name='anco_hits_scores'"
    ).fetchone()[0]
    if table_exists:
        rows = conn.execute(
            "SELECT entity_id, score FROM anco_hits_scores "
            "WHERE entity_type = 'case'"
        ).fetchall()
        for eid, score in rows:
            try:
                scores[int(eid)] = score
            except (ValueError, TypeError):
                pass

    y_true_list: list[int] = []
    y_pred_list: list[int] = []
    details: list[dict] = []

    for did in dockets:
        if did in DEV_EXCLUDED_DOCKETS or did not in ground_truth:
            continue
        if did not in scores:
            continue

        score = scores[did]
        if score > ANCO_THRESHOLD_PLT:
            pred = "PLAINTIFF_WINS"
        elif score < ANCO_THRESHOLD_DEF:
            pred = "DEFENDANT_WINS"
        else:
            pred = "MIXED"

        true = ground_truth[did]
        if true not in OUTCOME_TO_INT:
            continue

        y_true_list.append(OUTCOME_TO_INT[true])
        y_pred_list.append(OUTCOME_TO_INT[pred])
        details.append({
            "docket_id": did,
            "true": true,
            "pred": pred,
            "score": score,
            "correct": true == pred,
        })

    y_true = np.array(y_true_list)
    y_pred = np.array(y_pred_list)

    ba = balanced_accuracy(y_true, y_pred) if len(y_true) > 0 else 0.0

    return {
        "name": "B3_anco_threshold",
        "description": f"ANCO-HITS score thresholds (>{ANCO_THRESHOLD_PLT}=PLT, <{ANCO_THRESHOLD_DEF}=DEF, else MIXED)",
        "n_cases": len(y_true),
        "balanced_accuracy": ba,
        "raw_accuracy": float((y_true == y_pred).mean()) if len(y_true) > 0 else 0.0,
        "details": details,
    }


# ---------------------------------------------------------------------------
# B4: Zero-shot LLM (stub — requires Sol)
# ---------------------------------------------------------------------------


def baseline_zero_shot_stub() -> dict[str, object]:
    """B4: Placeholder — requires batch LLM run on Sol."""
    return {
        "name": "B4_zero_shot_llm",
        "description": "Raw opinion text → Llama 3.3 70B, no retrieval/constraints",
        "status": "STUB — requires batch run on Sol with --llm-url",
        "instructions": (
            "Run on Sol: python -m script.eval.baselines --db <db> "
            "--run-b4 --llm-url http://<host>:8000"
        ),
    }


# ---------------------------------------------------------------------------
# B5: BM25 + LLM (stub — requires rank-bm25 + Sol)
# ---------------------------------------------------------------------------


def baseline_bm25_stub() -> dict[str, object]:
    """B5: Placeholder — requires rank-bm25 library + LLM."""
    return {
        "name": "B5_bm25_llm",
        "description": "BM25 keyword retrieval + Llama 3.3 70B",
        "status": "STUB — requires rank-bm25 library + batch run on Sol",
        "instructions": (
            "pip install rank-bm25; "
            "Run on Sol: python -m script.eval.baselines --db <db> "
            "--run-b5 --llm-url http://<host>:8000"
        ),
    }


# ---------------------------------------------------------------------------
# Run all baselines
# ---------------------------------------------------------------------------


def run_all_baselines(db_path: Path) -> list[dict[str, object]]:
    """Compute all available baselines."""
    conn = sqlite3.connect(str(db_path))
    ground_truth = load_human_labels(conn)

    results = [
        baseline_majority(ground_truth),
        baseline_regex(conn, ground_truth),
        baseline_anco_threshold(conn, ground_truth),
        baseline_zero_shot_stub(),
        baseline_bm25_stub(),
    ]

    conn.close()
    return results


def print_baselines(results: list[dict[str, object]]) -> None:
    """Print baseline comparison table."""
    print(f"\n{'='*65}")
    print("  BASELINE COMPARISON")
    print(f"{'='*65}")
    print(f"  {'#':>3}  {'Baseline':<22}  {'Bal.Acc':>8}  {'Raw Acc':>8}  {'N':>4}")
    print(f"  {'─'*3}  {'─'*22}  {'─'*8}  {'─'*8}  {'─'*4}")

    for i, r in enumerate(results, 1):
        if "balanced_accuracy" in r:
            ba = f"{r['balanced_accuracy']*100:.1f}%"
            ra = f"{r.get('raw_accuracy', 0)*100:.1f}%"
            n = str(r.get("n_cases", "—"))
        else:
            ba = "STUB"
            ra = "—"
            n = "—"
        print(f"  B{i}   {r['name']:<22}  {ba:>8}  {ra:>8}  {n:>4}")

    print(f"{'='*65}\n")
