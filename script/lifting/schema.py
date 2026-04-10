"""
Pydantic schema for IRAC extraction output.

Defines the structured target that the LLM must produce for each opinion.
Maps 1:1 to the pipeline plan's Phase 1.3 output format and the
6-element conjunctive rule for Private 10b-5.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ElementStatus(str, Enum):
    """Status of a single 10b-5 element as determined by the judge."""

    SATISFIED = "SATISFIED"
    """Judge found this element adequately pled/proven."""

    NOT_SATISFIED = "NOT_SATISFIED"
    """Judge found this element failed."""

    CONTESTED = "CONTESTED"
    """Element is disputed but not yet resolved."""

    NOT_ANALYZED = "NOT_ANALYZED"
    """Judge didn't reach this element (e.g., dismissed on other grounds)."""


class ElementAnalysis(BaseModel):
    """Analysis of a single 10b-5 element extracted from opinion text."""

    status: ElementStatus

    sub_conditions: list[str] = Field(
        default_factory=list,
        description=(
            "Which sub-rules triggered for this element. "
            "E.g., ['FalseStatements', 'MisleadingOmissions'] for material_misrepresentation. "
            "Must be valid sub-conditions per rules.ELEMENT_RULES."
        ),
    )

    key_facts: list[str] = Field(
        default_factory=list,
        description="Supporting facts from the opinion that the judge relied on.",
    )

    judge_reasoning: str = Field(
        default="",
        description="Relevant quote or paraphrase of the judge's reasoning on this element.",
    )

    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "How clearly the judge addressed this element (0.0 = not mentioned, "
            "1.0 = dedicated section with clear conclusion). "
            "Computed post-hoc in Phase 1.3 from proxy signals — NOT filled by the LLM."
        ),
    )


class Elements(BaseModel):
    """The 6 elements of a Private 10b-5 claim. ALL must be SATISFIED for plaintiff to win."""

    material_misrepresentation: ElementAnalysis = Field(
        description="Defendant made false statements or misleading omissions.",
    )
    scienter: ElementAnalysis = Field(
        description="Defendant acted with intent to deceive or recklessness.",
    )
    connection: ElementAnalysis = Field(
        description="Fraud was in connection with purchase or sale of securities.",
    )
    reliance: ElementAnalysis = Field(
        description="Plaintiff relied on the misrepresentation (or fraud-on-the-market presumption).",
    )
    economic_loss: ElementAnalysis = Field(
        description="Plaintiff suffered actual financial loss.",
    )
    loss_causation: ElementAnalysis = Field(
        description="The fraud (not other factors) caused the loss.",
    )


class IRACExtraction(BaseModel):
    """
    Structured IRAC (Issue-Rule-Application-Conclusion) extraction from a judicial opinion.

    This is the core output of Phase 1 symbolic lifting. Each opinion produces one
    IRACExtraction that captures the judge's element-by-element analysis of the
    10b-5 claim.
    """

    case_id: int = Field(description="docket_id from the cases table.")
    opinion_id: int = Field(description="opinion_id from the opinions table.")

    procedural_stage: Literal["MTD", "SJ", "TRIAL", "APPEAL"] = Field(
        description="At which procedural stage this opinion was issued.",
    )

    elements: Elements = Field(
        description="Element-by-element analysis of the 6-element 10b-5 rule.",
    )

    outcome: Literal["PLAINTIFF_WINS", "DEFENDANT_WINS", "MIXED"] = Field(
        description="Overall outcome as stated in the opinion.",
    )

    statutes_cited: list[str] = Field(
        default_factory=list,
        description='Statutes cited in the opinion. E.g., ["15 U.S.C. § 78j(b)", "17 C.F.R. § 240.10b-5"].',
    )

    precedents_cited: list[str] = Field(
        default_factory=list,
        description='Landmark precedents cited. E.g., ["Tellabs v. Makor", "Dura v. Broudo"].',
    )

    arguments_plaintiff: list[str] = Field(
        default_factory=list,
        description="Key arguments made by the plaintiff.",
    )

    arguments_defendant: list[str] = Field(
        default_factory=list,
        description="Key arguments made by the defendant.",
    )
