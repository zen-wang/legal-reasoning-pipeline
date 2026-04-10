"""
Six hard constraint validators for the Constrained RAG lowering step.

Implements the "practical wisdom" layer from Hybrids (Newson et al.):
1. Citation check — every cited case must exist in our dataset
2. Statute grounding — every statute must be in known vocabulary
3. Binding authority — flag cross-circuit citations
4. Temporal validity — don't cite cases filed after the query case
5. Ambiguity flag — ANCO-HITS in [-0.1, +0.1] → [CONTESTED]
6. Missing element — NOT_ANALYZED elements → explicit statement

All validators are pure functions over a ConstraintContext loaded
once at startup from SQLite.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from script.rag.schema import ConstraintSeverity, ConstraintViolation

logger = logging.getLogger(__name__)

# Threshold for ANCO-HITS ambiguity
CONTESTED_THRESHOLD = 0.1


# ---------------------------------------------------------------------------
# Court → Circuit mapping (CourtListener IDs)
# ---------------------------------------------------------------------------

COURT_TO_CIRCUIT: dict[str, str] = {
    # Circuit courts (are their own circuit)
    "ca1": "ca1", "ca2": "ca2", "ca3": "ca3", "ca4": "ca4",
    "ca5": "ca5", "ca6": "ca6", "ca7": "ca7", "ca8": "ca8",
    "ca9": "ca9", "ca10": "ca10", "ca11": "ca11", "cadc": "cadc",
    "scotus": "scotus",
    # 1st Circuit: ME, MA, NH, PR, RI
    "mad": "ca1", "med": "ca1", "nhd": "ca1", "prd": "ca1",
    "rid": "ca1", "mab": "ca1",
    # 2nd Circuit: CT, NY, VT
    "ctd": "ca2", "nyed": "ca2", "nynd": "ca2", "nysd": "ca2",
    "nywd": "ca2", "nysb": "ca2", "vtd": "ca2",
    # 3rd Circuit: DE, NJ, PA, VI
    "ded": "ca3", "njd": "ca3", "paed": "ca3", "pamd": "ca3",
    "pawd": "ca3", "paeb": "ca3", "vid": "ca3",
    # 4th Circuit: MD, NC, SC, VA, WV
    "mdd": "ca4", "nced": "ca4", "ncmd": "ca4", "ncwd": "ca4",
    "scd": "ca4", "vaed": "ca4", "vawd": "ca4",
    "wvnd": "ca4", "wvsd": "ca4",
    # 5th Circuit: LA, MS, TX
    "laed": "ca5", "lamd": "ca5", "lawd": "ca5",
    "msnd": "ca5", "mssd": "ca5",
    "txed": "ca5", "txnd": "ca5", "txsd": "ca5", "txwd": "ca5",
    # 6th Circuit: KY, MI, OH, TN
    "kyed": "ca6", "kywd": "ca6",
    "mied": "ca6", "miwd": "ca6",
    "ohnd": "ca6", "ohsd": "ca6",
    "tned": "ca6", "tnmd": "ca6", "tnwd": "ca6",
    # 7th Circuit: IL, IN, WI
    "ilnd": "ca7", "ilsd": "ca7", "ilcd": "ca7",
    "innd": "ca7", "insd": "ca7",
    "wied": "ca7", "wiwd": "ca7",
    # 8th Circuit: AR, IA, MN, MO, NE, ND, SD
    "ared": "ca8", "arwd": "ca8",
    "iaed": "ca8", "iand": "ca8", "iasd": "ca8",
    "mnd": "ca8", "moed": "ca8", "mowd": "ca8",
    "ned": "ca8", "ndd": "ca8", "sdd": "ca8",
    # 9th Circuit: AK, AZ, CA, GU, HI, ID, MT, NV, OR, WA
    "akd": "ca9", "azd": "ca9",
    "cacd": "ca9", "cand": "ca9", "casd": "ca9", "caed": "ca9",
    "hid": "ca9", "idd": "ca9", "mtd": "ca9", "nvd": "ca9",
    "ord": "ca9", "waed": "ca9", "wawd": "ca9",
    # 10th Circuit: CO, KS, NM, OK, UT, WY
    "cod": "ca10", "ksd": "ca10", "nmd": "ca10",
    "oknd": "ca10", "oked": "ca10", "okwd": "ca10",
    "utd": "ca10", "wyd": "ca10",
    # 11th Circuit: AL, FL, GA
    "almd": "ca11", "alnd": "ca11", "alsd": "ca11",
    "flmd": "ca11", "flnd": "ca11", "flsd": "ca11",
    "gamd": "ca11", "gand": "ca11", "gasd": "ca11",
    # DC Circuit
    "dcd": "cadc",
}


# ---------------------------------------------------------------------------
# Constraint context (loaded once at startup)
# ---------------------------------------------------------------------------


@dataclass
class ConstraintContext:
    """Pre-loaded reference data for constraint validation."""

    # Known case names (lowered) -> docket_id
    known_cases: dict[str, int] = field(default_factory=dict)
    # Known docket_ids -> case_name
    known_docket_ids: dict[int, str] = field(default_factory=dict)
    # Known docket_ids -> date_filed (ISO string)
    case_dates: dict[int, str] = field(default_factory=dict)
    # Known docket_ids -> court_id
    case_courts: dict[int, str] = field(default_factory=dict)
    # Known statutes (normalized)
    known_statutes: set[str] = field(default_factory=set)
    # ANCO-HITS scores: docket_id -> score
    case_scores: dict[int, float] = field(default_factory=dict)
    # Query case info (set per-query)
    query_docket_id: int = 0
    query_court_id: str = ""
    query_circuit: str = ""
    query_date_filed: str = ""


def load_constraint_context(conn: sqlite3.Connection) -> ConstraintContext:
    """Load constraint reference data from SQLite."""
    ctx = ConstraintContext()

    # Cases
    rows = conn.execute(
        "SELECT docket_id, case_name, date_filed, court_id FROM cases"
    ).fetchall()
    for did, name, date_filed, court_id in rows:
        if name:
            ctx.known_cases[name.lower().strip()] = did
        ctx.known_docket_ids[did] = name or ""
        ctx.case_dates[did] = date_filed or ""
        ctx.case_courts[did] = court_id or ""

    # Statutes from IRAC extractions
    irac_rows = conn.execute(
        "SELECT extraction FROM irac_extractions WHERE is_valid = 1"
    ).fetchall()
    import json
    for (extraction_json,) in irac_rows:
        ext = json.loads(extraction_json)
        for statute in ext.get("statutes_cited", []):
            if statute:
                ctx.known_statutes.add(_normalize_statute(statute))

    # ANCO-HITS case scores
    score_table = conn.execute(
        "SELECT count(*) FROM sqlite_master "
        "WHERE type='table' AND name='anco_hits_scores'"
    ).fetchone()[0]
    if score_table:
        score_rows = conn.execute(
            "SELECT entity_id, score FROM anco_hits_scores "
            "WHERE entity_type = 'case'"
        ).fetchall()
        for eid, score in score_rows:
            try:
                ctx.case_scores[int(eid)] = score
            except (ValueError, TypeError):
                pass

    logger.info(
        f"Loaded constraint context: {len(ctx.known_cases)} cases, "
        f"{len(ctx.known_statutes)} statutes, "
        f"{len(ctx.case_scores)} scored cases"
    )
    return ctx


def set_query_context(
    ctx: ConstraintContext,
    docket_id: int,
) -> None:
    """Set the query case info on the constraint context."""
    ctx.query_docket_id = docket_id
    ctx.query_court_id = ctx.case_courts.get(docket_id, "")
    ctx.query_circuit = COURT_TO_CIRCUIT.get(ctx.query_court_id, "")
    ctx.query_date_filed = ctx.case_dates.get(docket_id, "")


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

_STATUTE_NORMALIZE = re.compile(r"[§\s]+")


def _normalize_statute(s: str) -> str:
    """Normalize a statute citation for matching."""
    return _STATUTE_NORMALIZE.sub(" ", s).strip().lower()


def _normalize_case_name(name: str) -> str:
    """Normalize a case name for fuzzy matching."""
    return re.sub(r"[^a-z0-9\s]", "", name.lower()).strip()


# ---------------------------------------------------------------------------
# Individual constraint validators
# ---------------------------------------------------------------------------


def check_citations(
    cited_cases: list[dict[str, Any]],
    ctx: ConstraintContext,
) -> list[ConstraintViolation]:
    """
    Constraint 1: Every cited case must exist in our dataset.

    Uses fuzzy matching: normalize names, check substring containment.
    """
    violations: list[ConstraintViolation] = []

    for case_ref in cited_cases:
        name = case_ref.get("case_name", "")
        docket_id = case_ref.get("docket_id")

        # Check by docket_id first
        if docket_id and docket_id in ctx.known_docket_ids:
            continue

        # Fuzzy match on name
        if name and _fuzzy_match_case(name, ctx):
            continue

        violations.append(ConstraintViolation(
            constraint="citation_check",
            severity=ConstraintSeverity.ERROR,
            message=f"Cited case not found in dataset: {name}",
            details={"case_name": name, "docket_id": docket_id},
        ))

    return violations


def _fuzzy_match_case(name: str, ctx: ConstraintContext) -> bool:
    """Check if a case name fuzzy-matches any known case."""
    norm = _normalize_case_name(name)
    if not norm:
        return False

    # Exact match on normalized name
    for known_name in ctx.known_cases:
        known_norm = _normalize_case_name(known_name)
        # Check if one contains the other (handles "v." vs "v" etc.)
        if norm in known_norm or known_norm in norm:
            return True

    # Check key party names (first party v. second party)
    parts = re.split(r"\s+v\.?\s+", name, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        p1, p2 = parts[0].strip().lower(), parts[1].strip().lower()
        for known_name in ctx.known_cases:
            kn = known_name.lower()
            if p1[:8] in kn and p2[:8] in kn:
                return True

    return False


def check_statutes(
    cited_statutes: list[str],
    ctx: ConstraintContext,
) -> list[ConstraintViolation]:
    """Constraint 2: Every statute must be in known vocabulary."""
    violations: list[ConstraintViolation] = []

    for statute in cited_statutes:
        norm = _normalize_statute(statute)
        if norm and norm not in ctx.known_statutes:
            # Substring match fallback
            matched = any(
                norm in known or known in norm
                for known in ctx.known_statutes
            )
            if not matched:
                violations.append(ConstraintViolation(
                    constraint="statute_grounding",
                    severity=ConstraintSeverity.WARNING,
                    message=f"Statute not in known vocabulary: {statute}",
                    details={"statute": statute},
                ))

    return violations


def check_binding_authority(
    cited_cases: list[dict[str, Any]],
    ctx: ConstraintContext,
) -> list[ConstraintViolation]:
    """
    Constraint 3: Flag cross-circuit citations.

    A case from CA9 citing a CA2 precedent should be flagged because
    CA2 decisions are not binding on CA9 courts (only persuasive).
    SCOTUS is binding everywhere.
    """
    violations: list[ConstraintViolation] = []
    if not ctx.query_circuit:
        return violations

    for case_ref in cited_cases:
        court_id = case_ref.get("court_id", "")
        cited_circuit = COURT_TO_CIRCUIT.get(court_id, "")

        if not cited_circuit:
            continue
        if cited_circuit == "scotus":
            continue  # SCOTUS is binding everywhere
        if cited_circuit == ctx.query_circuit:
            continue  # Same circuit

        violations.append(ConstraintViolation(
            constraint="binding_authority",
            severity=ConstraintSeverity.WARNING,
            message=(
                f"Cross-circuit citation: {case_ref.get('case_name', '')} "
                f"({cited_circuit}) cited in {ctx.query_circuit} case"
            ),
            details={
                "case_name": case_ref.get("case_name", ""),
                "cited_circuit": cited_circuit,
                "query_circuit": ctx.query_circuit,
            },
        ))

    return violations


def check_temporal_validity(
    cited_cases: list[dict[str, Any]],
    ctx: ConstraintContext,
) -> list[ConstraintViolation]:
    """Constraint 4: Don't cite cases filed after the query case."""
    violations: list[ConstraintViolation] = []
    if not ctx.query_date_filed:
        return violations

    for case_ref in cited_cases:
        docket_id = case_ref.get("docket_id")
        if not docket_id:
            continue

        cited_date = ctx.case_dates.get(docket_id, "")
        if not cited_date:
            continue

        if cited_date > ctx.query_date_filed:
            violations.append(ConstraintViolation(
                constraint="temporal_validity",
                severity=ConstraintSeverity.ERROR,
                message=(
                    f"Anachronistic citation: {case_ref.get('case_name', '')} "
                    f"(filed {cited_date}) cited in case filed {ctx.query_date_filed}"
                ),
                details={
                    "case_name": case_ref.get("case_name", ""),
                    "cited_date": cited_date,
                    "query_date": ctx.query_date_filed,
                },
            ))

    return violations


def check_ambiguity(
    element_scores: dict[str, float],
) -> list[ConstraintViolation]:
    """
    Constraint 5: ANCO-HITS in [-0.1, +0.1] → [CONTESTED].

    Flags elements whose associated arguments have near-zero ANCO-HITS scores,
    indicating genuinely contested legal ground.
    """
    violations: list[ConstraintViolation] = []

    for element_name, score in element_scores.items():
        if abs(score) <= CONTESTED_THRESHOLD:
            violations.append(ConstraintViolation(
                constraint="ambiguity_flag",
                severity=ConstraintSeverity.INFO,
                message=(
                    f"[CONTESTED] {element_name}: ANCO-HITS score {score:.3f} "
                    f"is near zero — genuinely contested"
                ),
                details={"element": element_name, "score": score},
            ))

    return violations


def check_missing_elements(
    element_statuses: dict[str, str],
) -> list[ConstraintViolation]:
    """
    Constraint 6: NOT_ANALYZED elements → explicit statement.

    If the judge didn't reach an element (e.g., dismissed on other grounds),
    the analysis must explicitly note this rather than guess.
    """
    violations: list[ConstraintViolation] = []

    for element_name, status in element_statuses.items():
        if status == "NOT_ANALYZED":
            violations.append(ConstraintViolation(
                constraint="missing_element",
                severity=ConstraintSeverity.WARNING,
                message=(
                    f"Element '{element_name}' was NOT_ANALYZED by the judge — "
                    f"analysis should explicitly state this"
                ),
                details={"element": element_name, "status": status},
            ))

    return violations


# ---------------------------------------------------------------------------
# Aggregate validator
# ---------------------------------------------------------------------------


def validate_output(
    analysis: dict[str, Any],
    ctx: ConstraintContext,
) -> list[ConstraintViolation]:
    """
    Run all 6 constraint validators on a generated analysis.

    Args:
        analysis: The LLM-generated analysis dict with keys:
            - cited_precedents: list of {case_name, docket_id, court_id}
            - statutes_cited: list of statute strings
            - element_scores: dict of element_name -> ANCO-HITS score
            - element_statuses: dict of element_name -> status string
        ctx: Pre-loaded constraint context

    Returns:
        List of all constraint violations found.
    """
    violations: list[ConstraintViolation] = []

    cited_cases = analysis.get("cited_precedents", [])
    violations.extend(check_citations(cited_cases, ctx))
    violations.extend(check_statutes(analysis.get("statutes_cited", []), ctx))
    violations.extend(check_binding_authority(cited_cases, ctx))
    violations.extend(check_temporal_validity(cited_cases, ctx))
    violations.extend(check_ambiguity(analysis.get("element_scores", {})))
    violations.extend(
        check_missing_elements(analysis.get("element_statuses", {}))
    )

    return violations
