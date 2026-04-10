"""
Context budget manager for the lowering prompt.

Greedy packing: query case IRAC (always included) → top precedents with
full IRAC details (~400 tok each) → remaining as citation-only (~50 tok each).

Token budget: 8192 total - 400 system - 300 schema - 100 template - 1024 output
            = ~6368 tokens for retrieved context.

Uses chars / 2.6 token estimate (proven in Phase 1 lifting).
"""

from __future__ import annotations

from script.lifting.schema import IRACExtraction

from .schema import RetrievedPrecedent

# ---------------------------------------------------------------------------
# Budget constants
# ---------------------------------------------------------------------------

TOTAL_TOKENS = 8192
SYSTEM_TOKENS = 400
SCHEMA_TOKENS = 300
TEMPLATE_TOKENS = 100
OUTPUT_TOKENS = 1024
CONTEXT_BUDGET = TOTAL_TOKENS - SYSTEM_TOKENS - SCHEMA_TOKENS - TEMPLATE_TOKENS - OUTPUT_TOKENS
# = 6368

CHARS_PER_TOKEN = 2.6  # Calibrated in Phase 1


def _estimate_tokens(text: str) -> int:
    """Estimate token count from character count."""
    return int(len(text) / CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------


def _format_query_case(
    docket_id: int,
    case_name: str,
    irac: IRACExtraction | None,
) -> str:
    """Format the query case section for the prompt."""
    lines = [
        f"## Query Case: {case_name} (docket_id={docket_id})",
    ]
    if irac:
        lines.append(f"Procedural stage: {irac.procedural_stage}")
        lines.append(f"Outcome: {irac.outcome}")
        lines.append("Elements:")
        for name, elem in [
            ("material_misrepresentation", irac.elements.material_misrepresentation),
            ("scienter", irac.elements.scienter),
            ("connection", irac.elements.connection),
            ("reliance", irac.elements.reliance),
            ("economic_loss", irac.elements.economic_loss),
            ("loss_causation", irac.elements.loss_causation),
        ]:
            lines.append(f"  - {name}: {elem.status.value}")
            if elem.key_facts:
                for fact in elem.key_facts[:2]:
                    lines.append(f"    Fact: {fact[:200]}")
            if elem.judge_reasoning:
                lines.append(f"    Reasoning: {elem.judge_reasoning[:300]}")
        if irac.statutes_cited:
            lines.append(f"Statutes: {', '.join(irac.statutes_cited[:5])}")
        if irac.precedents_cited:
            lines.append(f"Precedents cited: {', '.join(irac.precedents_cited[:5])}")
    else:
        lines.append("(No IRAC extraction available for this case)")

    return "\n".join(lines)


def _format_precedent_full(p: RetrievedPrecedent) -> str:
    """Format a precedent with full IRAC details (~400 tokens)."""
    lines = [
        f"### {p.case_name} (docket_id={p.docket_id}, court={p.court_id})",
        f"  Retrieval: semantic={p.semantic_score:.3f}, "
        f"graph={p.graph_reason or 'none'}, "
        f"anco_hits={p.anco_hits_score:.3f}, "
        f"final={p.final_score:.3f}",
    ]
    if p.irac_extraction:
        irac = p.irac_extraction
        lines.append(f"  Outcome: {irac.outcome}, Stage: {irac.procedural_stage}")
        for name, elem in [
            ("material_misrepresentation", irac.elements.material_misrepresentation),
            ("scienter", irac.elements.scienter),
            ("connection", irac.elements.connection),
            ("reliance", irac.elements.reliance),
            ("economic_loss", irac.elements.economic_loss),
            ("loss_causation", irac.elements.loss_causation),
        ]:
            status = elem.status.value
            reasoning = elem.judge_reasoning[:150] if elem.judge_reasoning else ""
            lines.append(f"  {name}: {status}" + (f" — {reasoning}" if reasoning else ""))
    return "\n".join(lines)


def _format_precedent_brief(p: RetrievedPrecedent) -> str:
    """Format a precedent as citation-only (~50 tokens)."""
    outcome = p.irac_extraction.outcome if p.irac_extraction else "unknown"
    return (
        f"- {p.case_name} (docket={p.docket_id}, court={p.court_id}, "
        f"outcome={outcome}, anco={p.anco_hits_score:.2f}, "
        f"score={p.final_score:.3f})"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_context(
    query_docket_id: int,
    query_case_name: str,
    query_irac: IRACExtraction | None,
    ranked_precedents: list[RetrievedPrecedent],
    max_tokens: int = CONTEXT_BUDGET,
) -> tuple[str, list[int]]:
    """
    Pack retrieved context into the token budget.

    Strategy (greedy):
    1. Always include query case IRAC
    2. Add top precedents with full IRAC details (~400 tok each)
    3. Fill remaining budget with citation-only summaries (~50 tok each)

    Args:
        query_docket_id: The case being analyzed
        query_case_name: Case name for display
        query_irac: IRAC extraction for the query case (may be None)
        ranked_precedents: Pre-ranked list from rank.py
        max_tokens: Token budget for context

    Returns:
        (context_string, list_of_included_opinion_ids)
    """
    # Start with query case (always included)
    query_section = _format_query_case(query_docket_id, query_case_name, query_irac)
    tokens_used = _estimate_tokens(query_section)
    sections = [query_section]
    included_ids: list[int] = []

    if ranked_precedents:
        sections.append("\n## Retrieved Precedents\n")
        tokens_used += _estimate_tokens("\n## Retrieved Precedents\n")

    # Phase 1: Full IRAC details for top precedents
    for p in ranked_precedents:
        full = _format_precedent_full(p)
        full_tokens = _estimate_tokens(full)

        if tokens_used + full_tokens > max_tokens:
            break

        sections.append(full)
        tokens_used += full_tokens
        included_ids.append(p.opinion_id)

    # Phase 2: Brief citations for remaining precedents
    remaining = [p for p in ranked_precedents if p.opinion_id not in set(included_ids)]
    if remaining:
        brief_header = "\n### Additional precedents (citation only):\n"
        tokens_used += _estimate_tokens(brief_header)
        sections.append(brief_header)

        for p in remaining:
            brief = _format_precedent_brief(p)
            brief_tokens = _estimate_tokens(brief)

            if tokens_used + brief_tokens > max_tokens:
                break

            sections.append(brief)
            tokens_used += brief_tokens
            included_ids.append(p.opinion_id)

    context_str = "\n".join(sections)
    return context_str, included_ids
