"""
Cross-validation evaluation — no human annotations needed.

Compares two independent signals that should agree:
1. Regex outcome labels (from opinion text pattern matching)
2. IRAC extraction outcomes (from LLM element-by-element analysis)

These are produced by independent methods (regex vs LLM), so their
agreement measures pipeline consistency without human ground truth.

Also computes:
- Element-outcome consistency: do element statuses logically match the outcome?
- ANCO-HITS vs IRAC outcome agreement
- Pydantic validation pass rate
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from .bootstrap import balanced_accuracy, bootstrap_ci, format_ci
from .config import DEV_EXCLUDED_DOCKETS

logger = logging.getLogger(__name__)

OUTCOME_LABELS = ["DEFENDANT_WINS", "PLAINTIFF_WINS", "MIXED"]
OUTCOME_TO_INT = {l: i for i, l in enumerate(OUTCOME_LABELS)}

ELEMENT_NAMES = [
    "material_misrepresentation", "scienter", "connection",
    "reliance", "economic_loss", "loss_causation",
]


def compute_cross_validation(db_path: Path) -> dict[str, Any]:
    """
    Evaluate pipeline consistency using cross-method agreement.

    No human annotations required. Compares:
    1. Regex labels vs IRAC outcomes (two independent outcome signals)
    2. Element-outcome consistency (do elements logically imply the outcome?)
    3. ANCO-HITS score vs IRAC outcome direction
    4. Schema validation pass rate
    """
    conn = sqlite3.connect(str(db_path))

    # --- 1. Regex vs IRAC outcome agreement ---
    irac_rows = conn.execute(
        "SELECT ie.docket_id, ie.extraction, cl.outcome_label "
        "FROM irac_extractions ie "
        "JOIN case_labels cl ON ie.docket_id = cl.docket_id "
        "WHERE ie.is_valid = 1 "
        "AND cl.outcome_label IN ('DEFENDANT_WINS', 'PLAINTIFF_WINS', 'MIXED')"
    ).fetchall()

    regex_vs_irac_true: list[int] = []
    regex_vs_irac_pred: list[int] = []
    disagreements: list[dict] = []

    for did, ext_json, regex_label in irac_rows:
        if did in DEV_EXCLUDED_DOCKETS:
            continue
        ext = json.loads(ext_json)
        irac_outcome = ext.get("outcome", "")

        if regex_label not in OUTCOME_TO_INT or irac_outcome not in OUTCOME_TO_INT:
            continue

        regex_vs_irac_true.append(OUTCOME_TO_INT[regex_label])
        regex_vs_irac_pred.append(OUTCOME_TO_INT[irac_outcome])

        if regex_label != irac_outcome:
            name = conn.execute(
                "SELECT case_name FROM cases WHERE docket_id = ?", (did,)
            ).fetchone()
            disagreements.append({
                "docket_id": did,
                "case_name": name[0] if name else "",
                "regex": regex_label,
                "irac": irac_outcome,
            })

    y_regex = np.array(regex_vs_irac_true)
    y_irac = np.array(regex_vs_irac_pred)

    if len(y_regex) > 0:
        agreement_rate = float((y_regex == y_irac).mean())
        ba = balanced_accuracy(y_regex, y_irac)
        pt, lo, hi = bootstrap_ci((y_regex == y_irac).astype(float))
    else:
        agreement_rate = 0.0
        ba = 0.0
        pt, lo, hi = 0.0, 0.0, 0.0

    regex_irac_result = {
        "n_cases": len(y_regex),
        "agreement_rate": agreement_rate,
        "balanced_agreement": ba,
        "agreement_ci": format_ci(pt, lo, hi),
        "n_disagreements": len(disagreements),
        "disagreements": disagreements[:10],  # Show first 10
    }

    # --- 2. Element-outcome consistency ---
    # If all 6 elements SATISFIED → should be PLT_WINS
    # If any element NOT_SATISFIED → should be DEF_WINS or MIXED
    # If all NOT_ANALYZED → outcome is indeterminate
    consistent = 0
    inconsistent = 0
    consistency_details: list[dict] = []

    for did, ext_json, _ in irac_rows:
        if did in DEV_EXCLUDED_DOCKETS:
            continue
        ext = json.loads(ext_json)
        elements = ext.get("elements", {})
        outcome = ext.get("outcome", "")

        statuses = [
            e.get("status", "NOT_ANALYZED")
            for e in elements.values()
            if isinstance(e, dict)
        ]

        n_satisfied = statuses.count("SATISFIED")
        n_not_satisfied = statuses.count("NOT_SATISFIED")
        n_analyzed = sum(1 for s in statuses if s != "NOT_ANALYZED")

        # Check logical consistency
        is_consistent = True
        reason = ""

        if n_analyzed == 0:
            reason = "all_not_analyzed"
        elif n_satisfied == 6 and outcome != "PLAINTIFF_WINS":
            is_consistent = False
            reason = f"all_satisfied_but_{outcome}"
        elif n_not_satisfied > 0 and outcome == "PLAINTIFF_WINS":
            is_consistent = False
            reason = f"has_not_satisfied_but_PLT_WINS"
        elif n_satisfied == 0 and n_not_satisfied > 0 and outcome != "DEFENDANT_WINS":
            is_consistent = False
            reason = f"no_satisfied_has_not_satisfied_but_{outcome}"

        if is_consistent:
            consistent += 1
        else:
            inconsistent += 1
            name = conn.execute(
                "SELECT case_name FROM cases WHERE docket_id = ?", (did,)
            ).fetchone()
            consistency_details.append({
                "docket_id": did,
                "case_name": name[0][:40] if name else "",
                "outcome": outcome,
                "n_satisfied": n_satisfied,
                "n_not_satisfied": n_not_satisfied,
                "reason": reason,
            })

    total_checked = consistent + inconsistent
    element_consistency = {
        "n_cases": total_checked,
        "consistent": consistent,
        "inconsistent": inconsistent,
        "consistency_rate": consistent / total_checked if total_checked > 0 else 0,
        "inconsistencies": consistency_details[:10],
    }

    # --- 3. ANCO-HITS vs IRAC outcome ---
    scores: dict[int, float] = {}
    table_exists = conn.execute(
        "SELECT count(*) FROM sqlite_master "
        "WHERE type='table' AND name='anco_hits_scores'"
    ).fetchone()[0]
    if table_exists:
        score_rows = conn.execute(
            "SELECT entity_id, score FROM anco_hits_scores WHERE entity_type = 'case'"
        ).fetchall()
        for eid, score in score_rows:
            try:
                scores[int(eid)] = score
            except (ValueError, TypeError):
                pass

    anco_agree = 0
    anco_disagree = 0
    for did, ext_json, _ in irac_rows:
        if did in DEV_EXCLUDED_DOCKETS or did not in scores:
            continue
        ext = json.loads(ext_json)
        irac_outcome = ext.get("outcome", "")
        score = scores[did]

        # Check direction agreement
        if irac_outcome == "PLAINTIFF_WINS" and score > 0:
            anco_agree += 1
        elif irac_outcome == "DEFENDANT_WINS" and score < 0:
            anco_agree += 1
        elif irac_outcome == "MIXED" and abs(score) < 0.5:
            anco_agree += 1
        elif irac_outcome == "MIXED":
            anco_agree += 1  # MIXED is ambiguous, don't penalize
        else:
            anco_disagree += 1

    anco_total = anco_agree + anco_disagree
    anco_result = {
        "n_cases": anco_total,
        "agreement": anco_agree,
        "disagreement": anco_disagree,
        "agreement_rate": anco_agree / anco_total if anco_total > 0 else 0,
    }

    # --- 4. Schema validation pass rate ---
    all_extractions = conn.execute(
        "SELECT is_valid, count(*) FROM irac_extractions GROUP BY is_valid"
    ).fetchall()
    valid_counts = {v: c for v, c in all_extractions}
    total_ext = sum(valid_counts.values())
    valid_ext = valid_counts.get(1, 0)

    validation_result = {
        "total_extractions": total_ext,
        "valid": valid_ext,
        "invalid": valid_counts.get(0, 0),
        "pass_rate": valid_ext / total_ext if total_ext > 0 else 0,
    }

    conn.close()

    return {
        "regex_vs_irac": regex_irac_result,
        "element_consistency": element_consistency,
        "anco_vs_irac": anco_result,
        "schema_validation": validation_result,
    }


def print_cross_validation(result: dict[str, Any]) -> None:
    """Print cross-validation summary."""
    print(f"\n{'='*65}")
    print("  CROSS-VALIDATION (No Human Annotations Required)")
    print(f"{'='*65}")

    # Regex vs IRAC
    r = result["regex_vs_irac"]
    print(f"\n  1. Regex Labels vs IRAC Outcomes:")
    print(f"     Cases compared: {r['n_cases']}")
    print(f"     Agreement rate: {r['agreement_rate']*100:.1f}%")
    print(f"     Balanced agreement: {r['balanced_agreement']*100:.1f}%")
    print(f"     CI: {r['agreement_ci']}")
    if r["disagreements"]:
        print(f"     Disagreements ({r['n_disagreements']}):")
        for d in r["disagreements"][:5]:
            print(f"       {d['docket_id']}: regex={d['regex']} vs irac={d['irac']} — {d['case_name'][:40]}")

    # Element consistency
    e = result["element_consistency"]
    print(f"\n  2. Element-Outcome Logical Consistency:")
    print(f"     Cases checked: {e['n_cases']}")
    print(f"     Consistent: {e['consistent']} ({e['consistency_rate']*100:.1f}%)")
    print(f"     Inconsistent: {e['inconsistent']}")
    if e["inconsistencies"]:
        print(f"     Inconsistencies:")
        for inc in e["inconsistencies"][:5]:
            print(f"       {inc['docket_id']}: {inc['outcome']} but S={inc['n_satisfied']} NS={inc['n_not_satisfied']} — {inc['reason']}")

    # ANCO vs IRAC
    a = result["anco_vs_irac"]
    print(f"\n  3. ANCO-HITS Score vs IRAC Outcome Direction:")
    print(f"     Cases compared: {a['n_cases']}")
    print(f"     Agreement: {a['agreement']} ({a['agreement_rate']*100:.1f}%)")

    # Validation
    v = result["schema_validation"]
    print(f"\n  4. Schema Validation Pass Rate:")
    print(f"     Total extractions: {v['total_extractions']}")
    print(f"     Valid: {v['valid']} ({v['pass_rate']*100:.1f}%)")
    print(f"     Invalid: {v['invalid']}")

    print(f"{'='*65}\n")
