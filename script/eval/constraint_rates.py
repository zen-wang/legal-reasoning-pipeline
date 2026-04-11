"""
Phase 5 constraint violation rate analysis.

Runs all 6 constraint validators on eval cases and reports
per-constraint violation rates and severity distribution.
Fully automated — no human labels needed.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from script.rag.constraints import (
    ConstraintContext,
    load_constraint_context,
    set_query_context,
    validate_output,
)
from script.rag.schema import ConstraintSeverity

from .config import DEV_EXCLUDED_DOCKETS, EVAL_DOCKETS

logger = logging.getLogger(__name__)


def compute_constraint_rates(
    db_path: Path,
    eval_dockets: list[int] | None = None,
) -> dict[str, Any]:
    """
    Run constraint validation on all eval cases and compute rates.

    Uses IRAC extractions as the analysis input (not LLM output),
    testing constraints on the symbolic pipeline's own data.
    """
    dockets = eval_dockets or EVAL_DOCKETS
    conn = sqlite3.connect(str(db_path))
    ctx = load_constraint_context(conn)

    # Load IRAC extractions for eval cases
    per_case: list[dict[str, Any]] = []
    total_violations: dict[str, int] = {}
    severity_counts: dict[str, int] = {"error": 0, "warning": 0, "info": 0}

    for did in dockets:
        if did in DEV_EXCLUDED_DOCKETS:
            continue

        # Load extraction
        row = conn.execute(
            "SELECT extraction FROM irac_extractions "
            "WHERE docket_id = ? AND is_valid = 1 "
            "ORDER BY created_at DESC LIMIT 1",
            (did,),
        ).fetchone()

        if not row:
            continue

        ext = json.loads(row[0])
        set_query_context(ctx, did)

        # Build analysis dict for validation
        elements = ext.get("elements", {})
        element_statuses: dict[str, str] = {}
        element_scores: dict[str, float] = {}
        anco_score = ctx.case_scores.get(did, 0.0)

        for name, elem in elements.items():
            if isinstance(elem, dict):
                element_statuses[name] = elem.get("status", "NOT_ANALYZED")
                element_scores[name] = anco_score

        analysis_dict: dict[str, Any] = {
            "cited_precedents": [],  # No LLM citations in symbolic mode
            "statutes_cited": ext.get("statutes_cited", []),
            "element_scores": element_scores,
            "element_statuses": element_statuses,
        }

        violations = validate_output(analysis_dict, ctx)

        case_result = {
            "docket_id": did,
            "n_violations": len(violations),
            "violations": [
                {
                    "constraint": v.constraint,
                    "severity": v.severity.value,
                    "message": v.message,
                }
                for v in violations
            ],
        }
        per_case.append(case_result)

        for v in violations:
            total_violations[v.constraint] = total_violations.get(v.constraint, 0) + 1
            severity_counts[v.severity.value] = severity_counts.get(v.severity.value, 0) + 1

    conn.close()

    n_cases = len(per_case)
    cases_with_violations = sum(1 for c in per_case if c["n_violations"] > 0)

    return {
        "n_cases_evaluated": n_cases,
        "cases_with_violations": cases_with_violations,
        "violation_rate": cases_with_violations / n_cases if n_cases > 0 else 0,
        "per_constraint_counts": total_violations,
        "severity_distribution": severity_counts,
        "per_case": per_case,
    }


def print_constraint_rates(result: dict[str, Any]) -> None:
    """Print constraint rate summary."""
    print(f"\n{'='*55}")
    print("  CONSTRAINT VIOLATION RATES (Symbolic Pipeline)")
    print(f"{'='*55}")
    print(f"  Cases evaluated: {result['n_cases_evaluated']}")
    print(f"  Cases with violations: {result['cases_with_violations']}")
    print(f"  Violation rate: {result['violation_rate']*100:.1f}%")

    print(f"\n  Per-constraint counts:")
    for constraint, count in sorted(result["per_constraint_counts"].items()):
        print(f"    {constraint:<25} {count}")

    print(f"\n  Severity distribution:")
    for severity, count in sorted(result["severity_distribution"].items()):
        print(f"    {severity:<10} {count}")
    print(f"{'='*55}\n")
