"""
Per-opinion IRAC extraction orchestrator.

Pipeline: load opinion → preprocess → build prompt → call LLM → validate → store.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Literal

from pydantic import ValidationError

from .llm_client import LLMClient
from .preprocess import get_analysis_text, split_sections
from .prompt import build_messages
from .rules import validate_extraction_rules
from .schema import (
    ElementAnalysis,
    Elements,
    ElementStatus,
    IRACExtraction,
)
from .store import init_irac_table, save_extraction

logger = logging.getLogger(__name__)

MODEL_CONTEXT_LIMIT = 8192  # vLLM --max-model-len
PROMPT_TEMPLATE_TOKENS = 1200  # system prompt + instructions + schema + chat template overhead
MIN_OUTPUT_TOKENS = 512  # minimum tokens reserved for JSON output
MAX_OUTPUT_TOKENS = 1024  # ideal output budget
SAFETY_MARGIN = 100  # extra buffer for chat template tokens


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English legal text."""
    return int(len(text) / 2.6)


def _compute_budget(opinion_chars: int) -> tuple[int, int]:
    """
    Dynamically compute max opinion chars and max_tokens for LLM output.

    Returns (max_opinion_chars, max_output_tokens).
    """
    opinion_tokens = int(opinion_chars / 2.6)  # Llama tokenizer: ~2.6 chars/token for legal text
    total_input = opinion_tokens + PROMPT_TEMPLATE_TOKENS + SAFETY_MARGIN

    available_for_output = MODEL_CONTEXT_LIMIT - total_input
    if available_for_output >= MAX_OUTPUT_TOKENS:
        # Fits fine — no truncation needed
        return (opinion_chars, MAX_OUTPUT_TOKENS)

    if available_for_output >= MIN_OUTPUT_TOKENS:
        # Tight but usable — reduce output budget
        return (opinion_chars, available_for_output)

    # Must truncate opinion text
    max_input_tokens = MODEL_CONTEXT_LIMIT - MAX_OUTPUT_TOKENS - PROMPT_TEMPLATE_TOKENS - SAFETY_MARGIN
    max_chars = int(max_input_tokens * 2.6)
    return (max_chars, MAX_OUTPUT_TOKENS)


def truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    """
    Truncate opinion text if too long, keeping beginning (60%) + end (40%).

    The conclusion (end) matters most for outcome determination.
    Returns (text, was_truncated).
    """
    if len(text) <= max_chars:
        return (text, False)

    keep_start = int(max_chars * 0.6)
    keep_end = max_chars - keep_start
    truncated = (
        text[:keep_start]
        + "\n\n[... MIDDLE SECTION TRUNCATED FOR LENGTH ...]\n\n"
        + text[-keep_end:]
    )
    return (truncated, True)


# ---------------------------------------------------------------------------
# Mock response
# ---------------------------------------------------------------------------

MOCK_RESPONSE: dict = {
    "elements": {
        "material_misrepresentation": {
            "status": "SATISFIED",
            "sub_conditions": ["FalseStatements", "MisleadingOmissions"],
            "key_facts": [
                "Defendant stated Q3 revenue was $2.1B when actual was $1.4B",
                "Defendant omitted material information about declining product sales",
            ],
            "judge_reasoning": "The Court finds that plaintiffs have adequately alleged "
            "that defendants made materially false and misleading statements.",
        },
        "scienter": {
            "status": "NOT_SATISFIED",
            "sub_conditions": ["MotiveAndOpportunity"],
            "key_facts": [
                "CEO sold shares during the class period",
            ],
            "judge_reasoning": "While plaintiffs allege motive and opportunity, "
            "they have not raised a strong inference of scienter as required by the PSLRA.",
        },
        "connection": {
            "status": "SATISFIED",
            "sub_conditions": ["InConnectionWithPurchase"],
            "key_facts": ["Misstatements were made in SEC filings and earnings calls"],
            "judge_reasoning": "The alleged fraud was clearly in connection with "
            "the purchase and sale of securities.",
        },
        "reliance": {
            "status": "NOT_ANALYZED",
            "sub_conditions": [],
            "key_facts": [],
            "judge_reasoning": "",
        },
        "economic_loss": {
            "status": "NOT_ANALYZED",
            "sub_conditions": [],
            "key_facts": [],
            "judge_reasoning": "",
        },
        "loss_causation": {
            "status": "NOT_ANALYZED",
            "sub_conditions": [],
            "key_facts": [],
            "judge_reasoning": "",
        },
    },
    "outcome": "DEFENDANT_WINS",
    "statutes_cited": [
        "15 U.S.C. § 78j(b)",
        "17 C.F.R. § 240.10b-5",
        "15 U.S.C. § 78u-4(b)(2) (PSLRA)",
    ],
    "precedents_cited": [
        "Tellabs, Inc. v. Makor Issues & Rights, Ltd., 551 U.S. 308 (2007)",
        "Dura Pharmaceuticals, Inc. v. Broudo, 544 U.S. 336 (2005)",
    ],
    "arguments_plaintiff": [
        "Defendant had motive and opportunity to commit fraud",
        "CEO sold significant shares during class period",
    ],
    "arguments_defendant": [
        "No strong inference of scienter under PSLRA",
        "Forward-looking statements protected by safe harbor",
    ],
}


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------

def extract_opinion(
    conn: sqlite3.Connection,
    opinion_id: int,
    docket_id: int,
    case_name: str,
    court_id: str,
    plain_text: str,
    procedural_stage: str | None,
    client: LLMClient | None = None,
    mode: Literal["live", "mock", "dry-run"] = "live",
) -> dict[str, object]:
    """
    Extract IRAC structure from a single opinion.

    Returns a dict with keys: opinion_id, status, outcome, errors.
    """
    result: dict[str, object] = {
        "opinion_id": opinion_id,
        "status": "unknown",
        "outcome": None,
        "errors": [],
    }

    # Step 1: Preprocess
    sections = split_sections(plain_text)
    analysis_text = get_analysis_text(sections)

    # Step 2: Dynamic budget — truncate if needed, compute max_tokens
    max_opinion_chars, output_tokens = _compute_budget(len(analysis_text))
    analysis_text, was_truncated = truncate_text(analysis_text, max_opinion_chars)
    if was_truncated:
        logger.warning(
            f"Opinion {opinion_id}: truncated from {len(plain_text):,} to {len(analysis_text):,} chars"
        )

    # Step 3: Build prompt
    messages = build_messages(
        opinion_text=analysis_text,
        case_name=case_name,
        court_id=court_id,
        docket_id=docket_id,
    )

    prompt_tokens = _estimate_tokens(messages[0]["content"] + messages[1]["content"])
    logger.info(
        f"Opinion {opinion_id}: ~{prompt_tokens:,} input tokens, "
        f"max_output={output_tokens}, {len(analysis_text):,} chars"
    )

    # Step 4: Get LLM response (or mock/dry-run)
    if mode == "dry-run":
        print(f"\n{'='*70}")
        print(f"DRY RUN — Opinion {opinion_id}: {case_name[:60]}")
        print(f"Court: {court_id} | Prompt tokens: ~{prompt_tokens:,}")
        print(f"{'='*70}")
        print(f"[SYSTEM] {messages[0]['content'][:200]}...")
        print(f"[USER] {messages[1]['content'][:500]}...")
        print(f"  ... ({len(messages[1]['content']):,} chars total)")
        result["status"] = "dry-run"
        return result

    if mode == "mock":
        raw_text = json.dumps(MOCK_RESPONSE)
        parsed = MOCK_RESPONSE
    else:
        if client is None:
            result["status"] = "error"
            result["errors"] = ["No LLM client provided"]
            return result
        try:
            raw_text, parsed = client.chat_completion(messages, max_tokens=output_tokens)
        except (ConnectionError, TimeoutError, RuntimeError) as e:
            logger.error(f"Opinion {opinion_id}: LLM error — {e}")
            result["status"] = "error"
            result["errors"] = [str(e)]
            return result

    if parsed is None:
        logger.error(f"Opinion {opinion_id}: failed to parse JSON from LLM response")
        save_extraction(
            conn,
            _make_placeholder_extraction(docket_id, opinion_id, procedural_stage),
            llm_model="llama-3.3-70b" if mode == "live" else "mock",
            llm_raw=raw_text,
            is_valid=False,
            errors=["JSON parse failure"],
        )
        result["status"] = "invalid"
        result["errors"] = ["JSON parse failure"]
        return result

    # Step 5: Inject pipeline fields
    parsed["case_id"] = docket_id
    parsed["opinion_id"] = opinion_id
    parsed["procedural_stage"] = procedural_stage or "APPEAL"

    # Remove confidence if LLM included it (it's computed post-hoc)
    for elem_data in parsed.get("elements", {}).values():
        if isinstance(elem_data, dict):
            elem_data.pop("confidence", None)

    # Step 6: Pydantic validation
    try:
        extraction = IRACExtraction.model_validate(parsed)
    except ValidationError as e:
        logger.error(f"Opinion {opinion_id}: Pydantic validation failed — {e.error_count()} errors")
        save_extraction(
            conn,
            _make_placeholder_extraction(docket_id, opinion_id, procedural_stage),
            llm_model="llama-3.3-70b" if mode == "live" else "mock",
            llm_raw=raw_text,
            is_valid=False,
            errors=[str(err) for err in e.errors()],
        )
        result["status"] = "invalid"
        result["errors"] = [str(err) for err in e.errors()]
        return result

    # Step 7: Validate sub-conditions
    rule_errors = validate_extraction_rules(extraction.elements)
    if rule_errors:
        logger.warning(f"Opinion {opinion_id}: invalid sub-conditions — {rule_errors}")

    # Step 8: Store
    save_extraction(
        conn,
        extraction,
        llm_model="llama-3.3-70b" if mode == "live" else "mock",
        llm_raw=raw_text,
        is_valid=not bool(rule_errors),
        errors=[f"invalid sub-conditions: {rule_errors}"] if rule_errors else None,
    )

    result["status"] = "valid" if not rule_errors else "invalid_subs"
    result["outcome"] = extraction.outcome
    return result


def _make_placeholder_extraction(
    docket_id: int,
    opinion_id: int,
    procedural_stage: str | None,
) -> IRACExtraction:
    """Create a placeholder extraction for storing failed attempts."""
    empty_element = ElementAnalysis(status=ElementStatus.NOT_ANALYZED)
    return IRACExtraction(
        case_id=docket_id,
        opinion_id=opinion_id,
        procedural_stage=procedural_stage or "APPEAL",
        elements=Elements(
            material_misrepresentation=empty_element,
            scienter=empty_element,
            connection=empty_element,
            reliance=empty_element,
            economic_loss=empty_element,
            loss_causation=empty_element,
        ),
        outcome="MIXED",
    )
