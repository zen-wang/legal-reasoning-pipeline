"""
Post-hoc quote verification for IRAC extractions.

Verifies LLM-extracted judge_reasoning against the original opinion text
using fuzzy matching. Provides verified_quote with character offsets for
explainable AI traceability.

Also generates deterministic rule_explanation from element statuses
(no LLM, no hallucination).
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher

from .rules import ELEMENT_NAMES
from .schema import IRACExtraction

logger = logging.getLogger(__name__)

# Minimum query length to attempt matching (shorter = too many false positives)
MIN_QUERY_CHARS = 20
# Minimum similarity ratio to accept a match
DEFAULT_THRESHOLD = 0.65


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------


def find_best_match(
    query: str,
    text: str,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, object] | None:
    """
    Find the best fuzzy match for query within text using sliding window.

    Args:
        query: LLM's judge_reasoning string
        text: Full opinion plain_text
        threshold: Minimum SequenceMatcher ratio to accept

    Returns:
        {"text": str, "start": int, "end": int, "score": float} or None
    """
    query = query.strip()
    if len(query) < MIN_QUERY_CHARS or not text:
        return None

    query_len = len(query)
    # Window sizes: query length ±30%
    min_window = max(MIN_QUERY_CHARS, int(query_len * 0.7))
    max_window = int(query_len * 1.3)
    stride = max(1, query_len // 4)

    best_score = 0.0
    best_start = 0
    best_end = 0

    for window_size in range(min_window, max_window + 1, max(1, (max_window - min_window) // 3)):
        for start in range(0, len(text) - window_size + 1, stride):
            end = start + window_size
            candidate = text[start:end]

            score = SequenceMatcher(None, query.lower(), candidate.lower()).ratio()

            if score > best_score:
                best_score = score
                best_start = start
                best_end = end

    if best_score >= threshold:
        return {
            "text": text[best_start:best_end],
            "start": best_start,
            "end": best_end,
            "score": best_score,
        }

    return None


# ---------------------------------------------------------------------------
# Confidence scoring (post-hoc, from proxy signals)
# ---------------------------------------------------------------------------


def _compute_confidence(elem: object) -> float:
    """
    Compute confidence score for an element from proxy signals.

    Signals (each contributes to a 0.0-1.0 score):
    - Has judge_reasoning (0.3): LLM found something to extract
    - Reasoning length (0.2): longer = more detailed analysis
    - Quote verified (0.3): reasoning traces to source text
    - Has key_facts (0.1): specific evidence identified
    - Has sub_conditions (0.1): legal framework applied

    NOT_ANALYZED with empty reasoning → 0.0 (correct behavior, not penalized).
    """
    if elem.status.value == "NOT_ANALYZED" and not elem.judge_reasoning:
        return 0.0

    score = 0.0

    # Has reasoning at all
    if elem.judge_reasoning:
        score += 0.3

        # Reasoning length (>100 chars = detailed)
        reasoning_len = len(elem.judge_reasoning)
        score += min(0.2, reasoning_len / 500.0 * 0.2)

    # Quote verification
    if elem.quote_match_score > 0:
        score += 0.3 * elem.quote_match_score

    # Has key facts
    if elem.key_facts:
        score += min(0.1, len(elem.key_facts) * 0.05)

    # Has sub-conditions
    if elem.sub_conditions:
        score += 0.1

    return round(min(1.0, score), 2)


# ---------------------------------------------------------------------------
# Extraction-level verification
# ---------------------------------------------------------------------------


def verify_extraction_quotes(
    extraction: IRACExtraction,
    plain_text: str,
) -> IRACExtraction:
    """
    Verify judge_reasoning quotes against the original opinion text.

    For each element with non-empty judge_reasoning, attempts to find
    the closest matching passage in plain_text. Updates verified_quote,
    quote_char_start, quote_char_end, quote_match_score.

    Elements with empty judge_reasoning are skipped (LLM honestly
    reported "not mentioned").

    Returns a deep copy with verification fields populated.
    """
    updated = extraction.model_copy(deep=True)

    n_with_reasoning = 0
    n_verified = 0

    for name in ELEMENT_NAMES:
        elem = getattr(updated.elements, name)

        if not elem.judge_reasoning:
            continue  # LLM honestly said "not mentioned" — skip

        n_with_reasoning += 1
        match = find_best_match(elem.judge_reasoning, plain_text)

        if match:
            elem.verified_quote = match["text"]
            elem.quote_char_start = match["start"]
            elem.quote_char_end = match["end"]
            elem.quote_match_score = match["score"]
            n_verified += 1

    # Compute confidence scores post-verification
    for name in ELEMENT_NAMES:
        elem = getattr(updated.elements, name)
        elem.confidence = _compute_confidence(elem)

    n_no_reasoning = len(ELEMENT_NAMES) - n_with_reasoning
    logger.info(
        f"Quote verification: {n_verified}/{n_with_reasoning} verified "
        f"({n_no_reasoning} elements had no reasoning)"
    )

    return updated


# ---------------------------------------------------------------------------
# Deterministic rule explanation (no LLM)
# ---------------------------------------------------------------------------

_ELEMENT_DISPLAY = {
    "material_misrepresentation": "Material Misrepresentation",
    "scienter": "Scienter",
    "connection": "Connection to Securities",
    "reliance": "Reliance",
    "economic_loss": "Economic Loss",
    "loss_causation": "Loss Causation",
}

_OUTCOME_DESCRIPTION = {
    "SJ_GRANTED": "Summary judgment GRANTED (defendant prevails)",
    "SJ_DENIED": "Summary judgment DENIED (plaintiff survives to trial)",
    "SJ_PARTIAL": "Summary judgment GRANTED IN PART, DENIED IN PART",
    "DEFENDANT_WINS": "Defendant wins",
    "PLAINTIFF_WINS": "Plaintiff wins",
    "MIXED": "Mixed outcome",
}


def build_rule_explanation(extraction: IRACExtraction) -> str:
    """
    Generate a human-readable explanation of the outcome from element statuses.

    Uses verified_quote when available, falls back to judge_reasoning
    with [UNVERIFIED] tag. Elements with empty reasoning shown as
    "Not addressed by the court."

    This is deterministic — no LLM, no hallucination.
    """
    outcome_desc = _OUTCOME_DESCRIPTION.get(extraction.outcome, extraction.outcome)
    lines = [outcome_desc, ""]

    failed: list[str] = []      # NOT_SATISFIED elements
    survived: list[str] = []    # SATISFIED elements
    contested: list[str] = []   # CONTESTED elements
    not_addressed: list[str] = []  # NOT_ANALYZED or no reasoning

    for name in ELEMENT_NAMES:
        elem = getattr(extraction.elements, name)
        display = _ELEMENT_DISPLAY.get(name, name)

        if elem.status.value == "NOT_SATISFIED":
            failed.append(_format_element_line(display, elem))
        elif elem.status.value == "SATISFIED":
            survived.append(_format_element_line(display, elem))
        elif elem.status.value == "CONTESTED":
            contested.append(_format_element_line(display, elem))
        else:
            not_addressed.append(display)

    if failed:
        lines.append("Plaintiff failed to raise genuine dispute on:")
        lines.extend(f"  {line}" for line in failed)

    if survived:
        lines.append("Plaintiff survived on:")
        lines.extend(f"  {line}" for line in survived)

    if contested:
        lines.append("Contested elements:")
        lines.extend(f"  {line}" for line in contested)

    if not_addressed:
        lines.append(f"Not addressed by the court: {', '.join(not_addressed)}")

    return "\n".join(lines)


def _format_element_line(display_name: str, elem: object) -> str:
    """Format a single element line with quote citation."""
    if elem.verified_quote:
        quote = elem.verified_quote[:120]
        if len(elem.verified_quote) > 120:
            quote += "..."
        return (
            f"- {display_name}: \"{quote}\" "
            f"(chars {elem.quote_char_start}-{elem.quote_char_end}) "
            f"[VERIFIED {elem.quote_match_score:.0%}]"
        )

    if elem.judge_reasoning:
        reasoning = elem.judge_reasoning[:120]
        if len(elem.judge_reasoning) > 120:
            reasoning += "..."
        return f"- {display_name}: \"{reasoning}\" [UNVERIFIED]"

    return f"- {display_name}: (no reasoning provided)"
