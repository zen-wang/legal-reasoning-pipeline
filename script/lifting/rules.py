"""
Rule-based pattern definitions for Private 10b-5.

Top-level rule (conjunctive — ALL 6 must be SATISFIED for plaintiff to win):
    PlaintiffWins <- MaterialMisrep AND Scienter AND Connection
                     AND Reliance AND EconomicLoss AND LossCausation

Each element has disjunctive sub-conditions (any one can satisfy the element).
"""

from __future__ import annotations

from typing import Literal

from .schema import ElementStatus, Elements


# ---------------------------------------------------------------------------
# Element rules: element name -> valid sub-conditions (disjunctive / OR)
# ---------------------------------------------------------------------------

ELEMENT_RULES: dict[str, list[str]] = {
    "material_misrepresentation": [
        "FalseStatements",
        "MisleadingOmissions",
        "SchemeToDefraud",
    ],
    "scienter": [
        "MotiveAndOpportunity",
        "ConsciousMisbehavior",
        "RecklessDisregard",
    ],
    "connection": [
        "InConnectionWithPurchase",
        "InConnectionWithSale",
    ],
    "reliance": [
        "FraudOnTheMarket",
        "DirectReliance",
        "AffiliateOmission",
    ],
    "economic_loss": [
        "ActualDamages",
        "DiminishedValue",
    ],
    "loss_causation": [
        "CorrectiveDisclosurePriceDrop",
        "MaterializationOfConcealedRisk",
    ],
}

ELEMENT_NAMES = list(ELEMENT_RULES.keys())


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_sub_conditions(element_name: str, sub_conditions: list[str]) -> list[str]:
    """
    Check that all sub_conditions are valid for the given element.

    Returns list of invalid sub-condition names (empty = all valid).
    Raises KeyError if element_name is unknown.
    """
    valid = set(ELEMENT_RULES[element_name])
    return [sc for sc in sub_conditions if sc not in valid]


def validate_extraction_rules(elements: Elements) -> dict[str, list[str]]:
    """
    Validate all sub-conditions across all 6 elements.

    Returns dict of element_name -> list of invalid sub-conditions.
    Empty dict means everything is valid.
    """
    errors: dict[str, list[str]] = {}
    for name in ELEMENT_NAMES:
        analysis = getattr(elements, name)
        invalid = validate_sub_conditions(name, analysis.sub_conditions)
        if invalid:
            errors[name] = invalid
    return errors


# ---------------------------------------------------------------------------
# Outcome evaluation
# ---------------------------------------------------------------------------

Outcome = Literal["PLAINTIFF_WINS", "DEFENDANT_WINS", "MIXED"]


def evaluate_outcome(elements: Elements) -> Outcome:
    """
    Apply the 10b-5 conjunctive rule to determine predicted outcome.

    Logic (matches real legal standard):
        - Any element NOT_SATISFIED → DEFENDANT_WINS (single failed element kills the claim)
        - All elements SATISFIED → PLAINTIFF_WINS
        - Otherwise (mix of SATISFIED/CONTESTED/NOT_ANALYZED) → MIXED
    """
    statuses = [getattr(elements, name).status for name in ELEMENT_NAMES]

    if any(s == ElementStatus.NOT_SATISFIED for s in statuses):
        return "DEFENDANT_WINS"
    if all(s == ElementStatus.SATISFIED for s in statuses):
        return "PLAINTIFF_WINS"
    return "MIXED"
