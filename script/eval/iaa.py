"""
Inter/intra-annotator agreement (Cohen's kappa).

Computes:
1. Intra-rater: Emre vs Emre-retest (5 cases, 2 weeks apart)
2. Calibration: Emre vs Professor (5 cases)

Quality gate: kappa >= 0.70 required before using annotations as ground truth.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

from .config import MIN_KAPPA

logger = logging.getLogger(__name__)

ELEMENT_NAMES = [
    "material_misrepresentation", "scienter", "connection",
    "reliance", "economic_loss", "loss_causation",
]


def cohens_kappa(y1: np.ndarray, y2: np.ndarray) -> float:
    """Compute Cohen's kappa between two annotators."""
    n = len(y1)
    if n == 0:
        return 0.0

    # Observed agreement
    po = (y1 == y2).mean()

    # Expected agreement (by chance)
    classes = np.unique(np.concatenate([y1, y2]))
    pe = sum(
        ((y1 == c).mean()) * ((y2 == c).mean())
        for c in classes
    )

    if pe >= 1.0:
        return 1.0

    return float((po - pe) / (1 - pe))


def compute_iaa(db_path: Path) -> dict[str, Any]:
    """Compute inter/intra-annotator agreement."""
    conn = sqlite3.connect(str(db_path))

    if not conn.execute(
        "SELECT count(*) FROM sqlite_master "
        "WHERE type='table' AND name='human_annotations'"
    ).fetchone()[0]:
        conn.close()
        return {"status": "WAITING", "message": "human_annotations table not found"}

    # Load all annotations grouped by annotator
    rows = conn.execute(
        "SELECT docket_id, annotator, element_statuses, outcome "
        "FROM human_annotations ORDER BY annotation_date DESC"
    ).fetchall()
    conn.close()

    # Group by annotator (latest per docket)
    by_annotator: dict[str, dict[int, dict]] = {}
    for did, annotator, elem_json, outcome in rows:
        by_annotator.setdefault(annotator, {})
        if did not in by_annotator[annotator]:
            by_annotator[annotator][did] = {
                "elements": json.loads(elem_json),
                "outcome": outcome,
            }

    result: dict[str, Any] = {"annotators": list(by_annotator.keys())}

    # Compute pairwise kappa for each annotator pair
    pairs = []
    annotator_names = list(by_annotator.keys())
    for i, a1 in enumerate(annotator_names):
        for a2 in annotator_names[i + 1:]:
            shared = set(by_annotator[a1].keys()) & set(by_annotator[a2].keys())
            if not shared:
                continue

            # Element-level kappa
            y1_elements: list[str] = []
            y2_elements: list[str] = []
            for did in shared:
                for elem in ELEMENT_NAMES:
                    s1 = by_annotator[a1][did]["elements"].get(elem, "NOT_ANALYZED")
                    s2 = by_annotator[a2][did]["elements"].get(elem, "NOT_ANALYZED")
                    y1_elements.append(s1)
                    y2_elements.append(s2)

            # Map to ints
            all_statuses = sorted(set(y1_elements + y2_elements))
            s_to_i = {s: i for i, s in enumerate(all_statuses)}
            y1_arr = np.array([s_to_i[s] for s in y1_elements])
            y2_arr = np.array([s_to_i[s] for s in y2_elements])
            element_kappa = cohens_kappa(y1_arr, y2_arr)

            # Outcome-level kappa
            y1_out = [by_annotator[a1][did]["outcome"] for did in shared]
            y2_out = [by_annotator[a2][did]["outcome"] for did in shared]
            out_labels = sorted(set(y1_out + y2_out))
            o_to_i = {o: i for i, o in enumerate(out_labels)}
            y1_out_arr = np.array([o_to_i[o] for o in y1_out])
            y2_out_arr = np.array([o_to_i[o] for o in y2_out])
            outcome_kappa = cohens_kappa(y1_out_arr, y2_out_arr)

            pair_type = "intra-rater" if "retest" in a1 or "retest" in a2 else "inter-rater"

            pairs.append({
                "annotator_1": a1,
                "annotator_2": a2,
                "type": pair_type,
                "n_shared_cases": len(shared),
                "element_kappa": element_kappa,
                "outcome_kappa": outcome_kappa,
                "element_gate_passed": element_kappa >= MIN_KAPPA,
                "outcome_gate_passed": outcome_kappa >= MIN_KAPPA,
            })

    result["pairs"] = pairs
    result["gate_passed"] = all(
        p["element_kappa"] >= MIN_KAPPA for p in pairs
    ) if pairs else False

    return result


def print_iaa(result: dict[str, Any]) -> None:
    """Print IAA summary."""
    print(f"\n{'='*60}")
    print("  INTER/INTRA-ANNOTATOR AGREEMENT")
    print(f"{'='*60}")

    if result.get("status") == "WAITING":
        print(f"  {result['message']}")
        print(f"{'='*60}\n")
        return

    print(f"  Annotators: {result['annotators']}")

    for p in result.get("pairs", []):
        gate = "PASS" if p["element_gate_passed"] else "FAIL"
        print(f"\n  {p['annotator_1']} vs {p['annotator_2']} ({p['type']}):")
        print(f"    Shared cases: {p['n_shared_cases']}")
        print(f"    Element kappa: {p['element_kappa']:.3f} [{gate}]")
        print(f"    Outcome kappa: {p['outcome_kappa']:.3f}")

    gate_status = "PASSED" if result.get("gate_passed") else "NOT PASSED"
    print(f"\n  Quality gate (kappa >= {MIN_KAPPA}): {gate_status}")
    print(f"{'='*60}\n")
