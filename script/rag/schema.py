"""
Output models for Phase 5 Constrained RAG.

Defines the structured output types produced by the RAG pipeline:
- IRACAnalysis: LLM-generated analysis with constraint validation
- SymbolicOnlyResult: Graceful degradation when LLM is unavailable
- Supporting types: CitedPrecedent, ElementAssessment, UncertaintyFlag

Also defines internal pipeline types (RetrievedPrecedent, ConstraintViolation)
used across multiple modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from script.lifting.schema import ElementStatus, IRACExtraction


# ---------------------------------------------------------------------------
# Internal: retrieved precedent (mutable — final_score set by rank.py)
# ---------------------------------------------------------------------------


@dataclass
class RetrievedPrecedent:
    """A precedent retrieved by the hybrid retrieval pipeline."""

    opinion_id: int
    docket_id: int
    case_name: str
    court_id: str
    semantic_score: float = 0.0
    graph_distance: int = -1  # -1 = not graph-connected
    graph_reason: str = ""  # e.g. "1-hop citation", "same statute"
    anco_hits_score: float = 0.0
    irac_extraction: IRACExtraction | None = None
    snippet: str = ""
    # Set by rank.py after fusion
    final_score: float = 0.0


# ---------------------------------------------------------------------------
# Internal: constraint violation
# ---------------------------------------------------------------------------


class ConstraintSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ConstraintViolation:
    """A constraint violation detected during post-generation validation."""

    constraint: str  # e.g. "citation_check", "binding_authority"
    severity: ConstraintSeverity
    message: str
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Output: cited precedent in the generated analysis
# ---------------------------------------------------------------------------


class CitedPrecedent(BaseModel):
    """A precedent cited in the generated IRAC analysis."""

    case_name: str
    docket_id: int | None = None
    court_id: str = ""
    anco_hits_score: float = 0.0
    cross_circuit: bool = False
    verified: bool = False  # True if case exists in our dataset


# ---------------------------------------------------------------------------
# Output: per-element assessment
# ---------------------------------------------------------------------------


class ElementAssessment(BaseModel):
    """Assessment of a single 10b-5 element in the generated analysis."""

    element_name: str
    status: ElementStatus
    anco_hits_score: float = 0.0
    contested: bool = False  # ANCO-HITS in [-0.1, +0.1]
    not_analyzed: bool = False
    supporting_precedents: list[str] = Field(default_factory=list)
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Output: uncertainty flag
# ---------------------------------------------------------------------------


class UncertaintyFlag(BaseModel):
    """An uncertainty flag raised during analysis."""

    flag_type: str  # e.g. "CONTESTED", "CROSS_CIRCUIT", "MISSING_ELEMENT"
    message: str
    severity: str = "warning"


# ---------------------------------------------------------------------------
# Output: full IRAC analysis (LLM-generated)
# ---------------------------------------------------------------------------


class IRACAnalysis(BaseModel):
    """
    Full IRAC analysis produced by the lowering step.

    Contains the LLM-generated analysis constrained by symbolic rules,
    plus validation results from the 6 hard constraints.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    issue: str = ""
    rule: str = ""
    application: list[ElementAssessment] = Field(default_factory=list)
    conclusion: str = ""
    cited_precedents: list[CitedPrecedent] = Field(default_factory=list)
    uncertainty_flags: list[UncertaintyFlag] = Field(default_factory=list)
    constraint_violations: list[ConstraintViolation] = Field(
        default_factory=list
    )
    llm_generated: bool = True

    # Metadata
    query_docket_id: int = 0
    query_case_name: str = ""
    retrieval_count: int = 0
    context_tokens_used: int = 0


# ---------------------------------------------------------------------------
# Output: symbolic-only result (graceful degradation)
# ---------------------------------------------------------------------------


class SymbolicOnlyResult(BaseModel):
    """
    Fallback result when LLM is unavailable.

    Contains all symbolic data (IRAC extraction, ANCO-HITS scores,
    ranked precedents) without natural language generation.
    Reinforces Beyond the Black Box thesis: pipeline is useful
    even without neural generation.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    query_docket_id: int
    query_case_name: str = ""
    irac_extraction: IRACExtraction | None = None
    anco_hits_score: float = 0.0
    ranked_precedents: list[RetrievedPrecedent] = Field(default_factory=list)
    constraint_violations: list[ConstraintViolation] = Field(
        default_factory=list
    )
    llm_generated: bool = False
