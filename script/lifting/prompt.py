"""
Prompt template for 10b-5 IRAC extraction.

Builds system + user prompts for Llama 3.3 70B in OpenAI chat format.
The JSON schema sent to the LLM excludes fields filled post-hoc by the pipeline
(case_id, opinion_id, confidence, procedural_stage).
"""

from __future__ import annotations

from .rules import ELEMENT_RULES

# ---------------------------------------------------------------------------
# The JSON schema shown to the LLM (stripped of pipeline-injected fields)
# ---------------------------------------------------------------------------

_LLM_OUTPUT_SCHEMA = """{
  "elements": {
    "material_misrepresentation": {
      "status": "SATISFIED | NOT_SATISFIED | CONTESTED | NOT_ANALYZED",
      "sub_conditions": ["FalseStatements"],
      "key_facts": ["specific facts the judge relied on"],
      "judge_reasoning": "relevant quote or close paraphrase"
    },
    "scienter": { "status": "...", "sub_conditions": [], "key_facts": [], "judge_reasoning": "" },
    "connection": { "status": "...", "sub_conditions": [], "key_facts": [], "judge_reasoning": "" },
    "reliance": { "status": "...", "sub_conditions": [], "key_facts": [], "judge_reasoning": "" },
    "economic_loss": { "status": "...", "sub_conditions": [], "key_facts": [], "judge_reasoning": "" },
    "loss_causation": { "status": "...", "sub_conditions": [], "key_facts": [], "judge_reasoning": "" }
  },
  "outcome": "PLAINTIFF_WINS | DEFENDANT_WINS | MIXED",
  "statutes_cited": ["15 U.S.C. § 78j(b)"],
  "precedents_cited": ["Tellabs v. Makor"],
  "arguments_plaintiff": ["key argument"],
  "arguments_defendant": ["key argument"]
}"""


SYSTEM_PROMPT = (
    "You are a legal analysis assistant specializing in Private Securities Fraud "
    "(Rule 10b-5) litigation. Given a judicial opinion, you extract a structured "
    "IRAC (Issue-Rule-Application-Conclusion) analysis following the exact JSON "
    "schema provided. You respond with ONLY valid JSON — no explanation, no markdown "
    "fences, no text before or after the JSON."
)


def _build_sub_conditions_block() -> str:
    """Format the valid sub-conditions list for the prompt."""
    lines: list[str] = []
    for element, subs in ELEMENT_RULES.items():
        lines.append(f"  {element}: {', '.join(subs)}")
    return "\n".join(lines)


_SUB_CONDITIONS_BLOCK = _build_sub_conditions_block()


def build_user_prompt(
    opinion_text: str,
    case_name: str = "",
    court_id: str = "",
    docket_id: int = 0,
) -> str:
    """Build the user prompt for a single opinion extraction."""
    return f"""Below is a judicial opinion in a Private Securities Fraud (Rule 10b-5) case.

CASE: {case_name} (docket_id: {docket_id})
COURT: {court_id}

Analyze this opinion and extract the following as JSON:

1. For each of the 6 elements of a 10b-5 claim, determine:
   - status: one of SATISFIED, NOT_SATISFIED, CONTESTED, NOT_ANALYZED
   - sub_conditions: which specific sub-rules apply (ONLY from the valid list below)
   - key_facts: the specific facts the judge relied on
   - judge_reasoning: a relevant quote or close paraphrase from the opinion

2. The overall outcome: PLAINTIFF_WINS, DEFENDANT_WINS, or MIXED
3. Statutes and precedents cited in the opinion
4. Key arguments from each side

VALID SUB-CONDITIONS PER ELEMENT:
{_SUB_CONDITIONS_BLOCK}

IMPORTANT RULES:
- If the judge did not discuss an element at all, set status to NOT_ANALYZED with empty sub_conditions, key_facts, and judge_reasoning
- Only use sub_conditions from the valid list above — do not invent new ones
- For judge_reasoning, quote or closely paraphrase the ACTUAL opinion text
- For key_facts, list specific factual findings, not legal conclusions
- status must be exactly one of: SATISFIED, NOT_SATISFIED, CONTESTED, NOT_ANALYZED

OPINION TEXT:
{opinion_text}

Respond with ONLY valid JSON matching this exact schema:
{_LLM_OUTPUT_SCHEMA}"""


def build_messages(
    opinion_text: str,
    case_name: str = "",
    court_id: str = "",
    docket_id: int = 0,
) -> list[dict[str, str]]:
    """Build OpenAI chat messages for the extraction request."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(
            opinion_text=opinion_text,
            case_name=case_name,
            court_id=court_id,
            docket_id=docket_id,
        )},
    ]


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English legal text."""
    return len(text) // 4
