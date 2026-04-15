# Topic 4: The 6-Element Conjunctive Rule

## The Rule (Plain English)

For an investor to win a Private 10b-5 securities fraud case, they must prove **ALL 6 elements**. If any single element fails, the defendant wins. This is why it's called "conjunctive" — it's an AND rule:

```
PlaintiffWins(case) ← MaterialMisrep AND Scienter AND Connection
                       AND Reliance AND EconomicLoss AND LossCausation
```

Think of it like a 6-lock door. The plaintiff needs all 6 keys. The defendant only needs to break 1 lock.

## The 6 Elements Explained

| # | Element | What plaintiff must prove | How often analyzed (our data) | How hard to prove |
|---|---|---|---|---|
| 1 | **Material Misrepresentation** | Defendant made false statements or misleading omissions about something important | 97/128 (76%) | Common — most cases start here |
| 2 | **Scienter** | Defendant acted with intent to deceive or recklessness | 59/128 (46%) | **Hardest** — most cases are won/lost here |
| 3 | **Connection** | The fraud was in connection with buying or selling securities | 37/128 (29%) | Rarely disputed — almost always satisfied |
| 4 | **Reliance** | Plaintiff actually relied on the false statements (or fraud-on-the-market presumption) | 47/128 (37%) | Key in class actions |
| 5 | **Economic Loss** | Plaintiff suffered actual financial loss | 42/128 (33%) | Sometimes disputed |
| 6 | **Loss Causation** | The fraud (not other market factors) caused the loss | 46/128 (36%) | Technical — requires showing price dropped because of fraud |

## Sub-Conditions (OR rules within each element)

Each element can be satisfied by one or more sub-conditions. These are disjunctive (OR):

```
MaterialMisrep ← FalseStatements OR MisleadingOmissions OR SchemeToDefraud
Scienter       ← MotiveAndOpportunity OR ConsciousMisbehavior OR RecklessDisregard
Connection     ← InConnectionWithPurchase OR InConnectionWithSale
Reliance       ← FraudOnTheMarket OR DirectReliance OR AffiliateOmission
EconomicLoss   ← ActualDamages OR DiminishedValue
LossCausation  ← CorrectiveDisclosurePriceDrop OR MaterializationOfConcealedRisk
```

14 sub-conditions total. These are what the LLM extracts from opinion text, and what `rules.py` validates against.

## Why This Rule Structure Matters for the Pipeline

| Phase | How the rule is used |
|---|---|
| **Phase 1 (Lifting)** | LLM knows exactly what to extract — 6 elements, each with status and sub-conditions |
| **Phase 3 (ANCO-HITS)** | Outcome (from 6-element rule) determines signed edge weights |
| **Phase 5 (RAG)** | Lowering prompt structures IRAC output around the 6 elements |
| **Phase 7 (Evaluation)** | Element-level accuracy measured per-element |

## The 4 Statuses

| Status | Meaning | Example |
|---|---|---|
| **SATISFIED** | Judge found this element adequately pled/proven | "Plaintiffs have adequately alleged scienter" |
| **NOT_SATISFIED** | Judge found this element failed | "No strong inference of scienter under PSLRA" |
| **CONTESTED** | Disputed but not yet resolved | "The parties dispute whether reliance can be presumed" |
| **NOT_ANALYZED** | Judge didn't reach this element | Judge dismissed on scienter, never discussed loss causation |

NOT_ANALYZED is common and important — judges often dismiss on one element (usually scienter) without analyzing the rest. Legally correct: if scienter fails, the case is over regardless.

## The evaluate_outcome() Function

```python
def evaluate_outcome(elements):
    if any element is NOT_SATISFIED → DEFENDANT_WINS
    if all elements are SATISFIED → PLAINTIFF_WINS
    otherwise (mix of SATISFIED/CONTESTED/NOT_ANALYZED) → MIXED
```

Deterministic. No ML, no probability. Pure legal logic.

## Real Example (Minneapolis Firefighters v. MEMC)

From an actual Phase 1 extraction:

| Element | Status | Sub-condition | Judge's reasoning |
|---|---|---|---|
| material_misrepresentation | NOT_SATISFIED | MisleadingOmissions | "pre-class period disclosures could not create a duty to disclose" |
| scienter | NOT_SATISFIED | RecklessDisregard | "inference of scienter is not as compelling as the more innocent inference" |
| connection | NOT_ANALYZED | — | (judge dismissed on first 2 elements) |
| reliance | NOT_ANALYZED | — | — |
| economic_loss | NOT_ANALYZED | — | — |
| loss_causation | NOT_ANALYZED | — | — |

→ `evaluate_outcome()` = **DEFENDANT_WINS** (material_misrep and scienter both NOT_SATISFIED)

The judge only needed to find 2 elements failed. The remaining 4 were never analyzed — the pipeline correctly records NOT_ANALYZED rather than guessing.

## How Sub-Conditions Are Extracted

**LLM, not regex.** The LLM reads the full opinion text and identifies which sub-conditions the judge discussed. Regex can't do this because judges express the same concept in many ways:

```
"FalseStatements" might appear as:
- "defendants made materially false statements"
- "the complaint adequately alleges misrepresentations"
- "plaintiffs have pled that the quarterly reports were false"
- "the court finds the statements were not truthful"

No fixed regex pattern covers all of these. The LLM understands the semantics.
```

The prompt lists valid sub-conditions and tells the LLM "only use sub_conditions from this list." The LLM then reports which ones the judge discussed, not all possible ones.

**The prompt does NOT force per-sub-condition checking.** It doesn't say "check FalseStatements, then MisleadingOmissions, then SchemeToDefraud." The LLM identifies whichever sub-conditions the judge mentioned. Since sub-conditions are OR (disjunctive), only ONE needs to be satisfied for the element to pass.

## Elevator Pitch

"The entire pipeline is built on one legal rule: to win a Private 10b-5 case, the plaintiff must prove all 6 elements. We encode this as a conjunctive rule with 14 sub-conditions. The LLM extracts which elements are satisfied or not from each opinion, and our rule engine computes the predicted outcome. If any element fails, defendant wins. This rule is editable — if we need to add a new sub-condition or change the logic, we edit `rules.py` and re-run, no retraining needed."

---

## Open Discussion: Problem of Opinion Extraction in Sub-Conditions

### The Risk

The LLM might miss a sub-condition when the judge discusses multiple sub-conditions with different outcomes within the same element.

**Example scenario:**

```
MaterialMisrep ← FalseStatements OR MisleadingOmissions OR SchemeToDefraud

Judge writes:
  "Plaintiffs have not identified any false statement of fact.
   However, they have adequately alleged a scheme to defraud
   through defendants' pattern of insider trading."

Correct extraction:
  status: SATISFIED
  sub_conditions: ["SchemeToDefraud"]

Risk: LLM reads "have not identified any false statement" →
  extracts NOT_SATISFIED and stops reading →
  misses SchemeToDefraud = SATISFIED
```

### How Judges Actually Write Opinions

**Pattern A (~70% of cases):** Judge analyzes one sub-condition, reaches a conclusion, stops. Nothing to miss.

> "The Court finds plaintiffs have failed to allege material misrepresentation. The complaint does not identify any false statement of fact. Motion to dismiss GRANTED."

**Pattern B (~30% of cases):** Judge analyzes multiple sub-conditions, some fail, some succeed. This is where the LLM might miss later sub-conditions.

> "While plaintiffs have not identified any false statement of fact, they have adequately alleged a scheme to defraud through defendants' pattern of insider trading."

### Current Safeguards

1. **Prompt requires quoting judge's reasoning** — forces the LLM to read deeper, not just first sentence. The `judge_reasoning` field acts as evidence of reading depth.
2. **Prompt requires listing key_facts** — thin key_facts = signal that extraction might be incomplete.
3. **Preprocessor keeps full analysis section** — LLM sees entire judicial discussion.
4. **Sub-condition validation** (rules.py) — catches hallucinated sub-conditions (e.g., "Misrepresentation" instead of "FalseStatements"). 4 extractions were correctly rejected this way.

### Known Gap

There is **no second-pass verification** that asks: "You said MaterialMisrep is NOT_SATISFIED, but did the judge discuss MisleadingOmissions or SchemeToDefraud anywhere else in the opinion?"

### Potential Fixes (not yet implemented)

**Low effort — keyword audit (post-hoc):**
After Phase 1 extraction, scan opinion text for sub-condition keywords:
```
If LLM says MaterialMisrep = NOT_SATISFIED, but opinion contains:
  "scheme to defraud" → FLAG for human review
  "omissions" + "material" → FLAG for human review
```
Safety net, not replacement for LLM.

**Higher effort — two-pass extraction:**
1. First pass: extract as normal
2. Second pass: "You extracted MaterialMisrep as NOT_SATISFIED based on FalseStatements. The opinion also contains these phrases: [keyword matches]. Did the judge discuss any other sub-conditions?"

Doubles LLM cost but catches Pattern B misses.

### How This Connects to the Exception Handling Loop

This is exactly what Algorithm 1 Step 5 (Exception Handling) is designed for. In Phase 7 evaluation, if we discover that the pipeline systematically misses SchemeToDefraud when FalseStatements fails, we can:
1. Add the keyword audit as a post-extraction check
2. Or restructure the prompt to force per-sub-condition scanning
3. Re-run Phase 1 — no model retraining needed

This is the "programmable" advantage: the fix is a code change, not a retraining cycle.

### For Teammates

"The LLM extracts whichever sub-conditions the judge discussed. In most cases judges only analyze one sub-condition and stop, so this works. In cases where the judge discusses multiple sub-conditions with mixed results, there's a risk the LLM stops at the first one. We mitigate this through prompt design (requiring quotes and facts) but don't have a second-pass check yet. This is a known limitation — and exactly the kind of thing the exception handling loop is designed to catch and fix iteratively."
