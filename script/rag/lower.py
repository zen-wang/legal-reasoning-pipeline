"""
Lowering step: inject symbolic knowledge into neural generation.

Builds a constrained prompt from retrieved context + symbolic rules,
sends it to the LLM (Llama 3.3 70B via vLLM), and validates the
output against 6 hard constraints.

Graceful degradation: returns SymbolicOnlyResult if LLM is unavailable.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from script.lifting.llm_client import LLMClient, extract_json
from script.lifting.schema import ElementStatus, IRACExtraction

from .constraints import (
    ConstraintContext,
    set_query_context,
    validate_output,
)
from .schema import (
    CitedPrecedent,
    ConstraintViolation,
    ElementAssessment,
    IRACAnalysis,
    RetrievedPrecedent,
    SymbolicOnlyResult,
    UncertaintyFlag,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt with mandatory constraint instructions
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a legal analysis assistant specializing in Private Rule 10b-5 \
securities fraud cases. You produce structured IRAC analyses grounded \
in real case law and statutory authority.

## Mandatory Constraints

1. **Citation integrity**: Only cite cases provided in the context below. \
Never fabricate case names, docket IDs, or holdings. If you need a precedent \
not in the context, say "[INSUFFICIENT PRECEDENT]".

2. **Statute grounding**: Only reference statutes that appear in the \
provided context or are part of the core 10b-5 framework (15 U.S.C. § 78j(b), \
17 C.F.R. § 240.10b-5, PSLRA).

3. **Binding authority**: If a cited case is from a different federal circuit \
than the query case, explicitly note "[CROSS-CIRCUIT]" after the citation.

4. **Temporal validity**: Do not cite a case as precedent if it was filed \
after the query case.

5. **Ambiguity**: If the ANCO-HITS score for an element is near zero \
(contested), explicitly flag "[CONTESTED]" and explain why the element \
is genuinely disputed.

6. **Missing elements**: If the judge did not analyze an element \
(NOT_ANALYZED), explicitly state this. Do not speculate on the outcome.

## Output Format

Respond with a JSON object matching this schema:
{
  "issue": "One-sentence statement of the legal issue",
  "rule": "Statement of the applicable legal rule (10b-5 elements)",
  "application": [
    {
      "element_name": "material_misrepresentation",
      "status": "SATISFIED|NOT_SATISFIED|CONTESTED|NOT_ANALYZED",
      "reasoning": "How the court analyzed this element, citing precedents",
      "supporting_precedents": ["Case Name (docket_id)"]
    }
  ],
  "conclusion": "Overall assessment based on element-by-element analysis",
  "cited_precedents": [
    {
      "case_name": "...",
      "docket_id": 12345,
      "court_id": "nysd",
      "relevance": "Why this case is relevant"
    }
  ],
  "statutes_cited": ["15 U.S.C. § 78j(b)"],
  "uncertainty_flags": [
    {"flag_type": "CONTESTED", "message": "..."}
  ]
}
"""


# ---------------------------------------------------------------------------
# Build user prompt
# ---------------------------------------------------------------------------


def _build_user_prompt(
    context_str: str,
    docket_id: int,
    case_name: str,
) -> str:
    """Build the user message with retrieved context."""
    return (
        f"Analyze the following Private 10b-5 securities fraud case using "
        f"the IRAC framework. Base your analysis ONLY on the provided context.\n\n"
        f"{context_str}\n\n"
        f"Produce a complete IRAC analysis for {case_name} (docket_id={docket_id})."
    )


# ---------------------------------------------------------------------------
# Parse LLM response into IRACAnalysis
# ---------------------------------------------------------------------------


def _parse_llm_response(
    raw: str,
    parsed: dict[str, Any] | None,
    docket_id: int,
    case_name: str,
    precedents: list[RetrievedPrecedent],
    context_tokens: int,
) -> IRACAnalysis:
    """Convert LLM JSON response into IRACAnalysis model."""
    if parsed is None:
        return IRACAnalysis(
            issue="[LLM response could not be parsed]",
            query_docket_id=docket_id,
            query_case_name=case_name,
        )

    # Parse application (element assessments)
    application: list[ElementAssessment] = []
    for elem_data in parsed.get("application", []):
        status_str = elem_data.get("status", "NOT_ANALYZED")
        try:
            status = ElementStatus(status_str)
        except ValueError:
            status = ElementStatus.NOT_ANALYZED

        application.append(ElementAssessment(
            element_name=elem_data.get("element_name", ""),
            status=status,
            reasoning=elem_data.get("reasoning", ""),
            supporting_precedents=elem_data.get("supporting_precedents", []),
            not_analyzed=(status == ElementStatus.NOT_ANALYZED),
            contested=(status == ElementStatus.CONTESTED),
        ))

    # Parse cited precedents
    cited: list[CitedPrecedent] = []
    for cp_data in parsed.get("cited_precedents", []):
        cited.append(CitedPrecedent(
            case_name=cp_data.get("case_name", ""),
            docket_id=cp_data.get("docket_id"),
            court_id=cp_data.get("court_id", ""),
        ))

    # Parse uncertainty flags
    flags: list[UncertaintyFlag] = []
    for uf_data in parsed.get("uncertainty_flags", []):
        flags.append(UncertaintyFlag(
            flag_type=uf_data.get("flag_type", ""),
            message=uf_data.get("message", ""),
        ))

    return IRACAnalysis(
        issue=parsed.get("issue", ""),
        rule=parsed.get("rule", ""),
        application=application,
        conclusion=parsed.get("conclusion", ""),
        cited_precedents=cited,
        uncertainty_flags=flags,
        llm_generated=True,
        query_docket_id=docket_id,
        query_case_name=case_name,
        retrieval_count=len(precedents),
        context_tokens_used=context_tokens,
    )


# ---------------------------------------------------------------------------
# Build SymbolicOnlyResult (graceful degradation)
# ---------------------------------------------------------------------------


def build_symbolic_result(
    docket_id: int,
    case_name: str,
    irac: IRACExtraction | None,
    anco_score: float,
    precedents: list[RetrievedPrecedent],
    ctx: ConstraintContext,
) -> SymbolicOnlyResult:
    """
    Build a result using only symbolic data (no LLM).

    This is returned when the LLM is unavailable or --symbolic-only is set.
    Still runs constraint validation on the symbolic data.
    """
    # Build analysis dict for constraint validation
    cited_cases: list[dict[str, Any]] = [
        {
            "case_name": p.case_name,
            "docket_id": p.docket_id,
            "court_id": p.court_id,
        }
        for p in precedents
    ]

    element_scores: dict[str, float] = {}
    element_statuses: dict[str, str] = {}
    if irac:
        for name, elem in [
            ("material_misrepresentation", irac.elements.material_misrepresentation),
            ("scienter", irac.elements.scienter),
            ("connection", irac.elements.connection),
            ("reliance", irac.elements.reliance),
            ("economic_loss", irac.elements.economic_loss),
            ("loss_causation", irac.elements.loss_causation),
        ]:
            element_statuses[name] = elem.status.value
            element_scores[name] = anco_score

    analysis_dict: dict[str, Any] = {
        "cited_precedents": cited_cases,
        "statutes_cited": irac.statutes_cited if irac else [],
        "element_scores": element_scores,
        "element_statuses": element_statuses,
    }

    violations = validate_output(analysis_dict, ctx)

    return SymbolicOnlyResult(
        query_docket_id=docket_id,
        query_case_name=case_name,
        irac_extraction=irac,
        anco_hits_score=anco_score,
        ranked_precedents=precedents,
        constraint_violations=violations,
    )


# ---------------------------------------------------------------------------
# Main lowering function
# ---------------------------------------------------------------------------


def lower(
    docket_id: int,
    case_name: str,
    query_irac: IRACExtraction | None,
    context_str: str,
    context_tokens: int,
    precedents: list[RetrievedPrecedent],
    constraint_ctx: ConstraintContext,
    client: LLMClient | None = None,
) -> IRACAnalysis | SymbolicOnlyResult:
    """
    Execute the lowering step: prompt LLM with constrained context.

    If client is None or LLM call fails, returns SymbolicOnlyResult.
    Otherwise returns IRACAnalysis with constraint validation applied.
    """
    set_query_context(constraint_ctx, docket_id)
    anco_score = constraint_ctx.case_scores.get(docket_id, 0.0)

    # Graceful degradation: no LLM client
    if client is None:
        logger.info("No LLM client — returning symbolic-only result")
        return build_symbolic_result(
            docket_id, case_name, query_irac, anco_score,
            precedents, constraint_ctx,
        )

    # Build prompt
    user_prompt = _build_user_prompt(context_str, docket_id, case_name)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    # Call LLM
    try:
        raw, parsed = client.chat_completion(messages, max_tokens=1024)
    except (ConnectionError, TimeoutError, RuntimeError) as e:
        logger.warning(f"LLM call failed: {e} — returning symbolic-only result")
        return build_symbolic_result(
            docket_id, case_name, query_irac, anco_score,
            precedents, constraint_ctx,
        )

    # Parse response
    analysis = _parse_llm_response(
        raw, parsed, docket_id, case_name, precedents, context_tokens,
    )

    # Run constraint validation on LLM output
    cited_cases = [
        {
            "case_name": cp.case_name,
            "docket_id": cp.docket_id,
            "court_id": cp.court_id,
        }
        for cp in analysis.cited_precedents
    ]

    element_statuses: dict[str, str] = {}
    element_scores: dict[str, float] = {}
    for elem in analysis.application:
        element_statuses[elem.element_name] = elem.status.value
        element_scores[elem.element_name] = anco_score

    statutes = []
    if parsed:
        statutes = parsed.get("statutes_cited", [])

    analysis_dict: dict[str, Any] = {
        "cited_precedents": cited_cases,
        "statutes_cited": statutes,
        "element_scores": element_scores,
        "element_statuses": element_statuses,
    }

    violations = validate_output(analysis_dict, constraint_ctx)
    analysis.constraint_violations = violations

    # Mark verified citations
    for cp in analysis.cited_precedents:
        if cp.docket_id and cp.docket_id in constraint_ctx.known_docket_ids:
            cp.verified = True

    # Flag cross-circuit
    from .constraints import COURT_TO_CIRCUIT
    query_circuit = constraint_ctx.query_circuit
    for cp in analysis.cited_precedents:
        cited_circuit = COURT_TO_CIRCUIT.get(cp.court_id, "")
        if (cited_circuit and query_circuit
                and cited_circuit != "scotus"
                and cited_circuit != query_circuit):
            cp.cross_circuit = True

    # Set ANCO-HITS scores on element assessments
    for elem in analysis.application:
        elem.anco_hits_score = anco_score
        if abs(anco_score) <= 0.1:
            elem.contested = True

    n_violations = len(violations)
    logger.info(
        f"Lowering complete: {len(analysis.application)} elements, "
        f"{len(analysis.cited_precedents)} citations, "
        f"{n_violations} constraint violations"
    )

    return analysis
