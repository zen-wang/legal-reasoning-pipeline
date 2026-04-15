# Topic 12: The Exception Handling Loop

## What This Is

Algorithm 1, Step 5 from Beyond the Black Box. The **feedback loop** that makes the system self-improving without retraining. When the pipeline makes an error, trace it to the source phase, fix the rule or constraint, and re-run — no model retraining needed.

This is the architectural claim that separates Programmable AI from traditional ML: **errors are fixed by editing code, not by gathering more training data.**

## The Loop

```
Run pipeline on cases
  → Evaluate results (Phase 7 metrics)
  → Identify failures
  → Trace each failure to its source phase:
      Citation wrong?      → Fix Phase 5 constraints
      Element wrong?       → Fix Phase 1 lifting prompt or rules.py sub-conditions
      Argument wrong?      → Fix Phase 3 ANCO-HITS input (argument dedup)
      Outcome wrong?       → Fix Phase 1 patterns OR Phase 5 retrieval weights
      Statute missing?     → Add to known vocabulary in constraints.py
  → Apply fix (edit rule, adjust threshold, modify prompt)
  → Re-run ONLY the affected phase (incremental, not full retraining)
  → Re-evaluate
```

## Concrete Examples

### Example 1 — Phase 1 sub-condition missing

```
Problem:  Pipeline says scienter = NOT_SATISFIED
Reality:  Judge found scienter through "conscious avoidance" doctrine
Root cause: "ConsciousAvoidance" not in sub-condition list

Fix: Add "ConsciousAvoidance" to ELEMENT_RULES["scienter"] in rules.py
Re-run: Phase 1 on affected cases only
Cost: 1 line of code, ~2 min to re-extract
```

### Example 2 — Phase 5 constraint too strict

```
Problem:  Twombly and Iqbal flagged as UNVERIFIED citations
Reality:  Real landmarks, just not in our 416-case dataset
Root cause: Citation check only looks at our dataset

Fix: Add whitelist of known landmark cases to constraints.py
Re-run: Phase 5 constraint validation (instant)
Cost: ~20 lines of code
```

### Example 3 — Phase 3 ANCO-HITS uninformative

```
Problem:  All scores are ±1 or 0, no intermediate values
Root cause: 353/370 arguments are singletons (exact-match dedup)

Fix: Add SBERT clustering to merge similar arguments
Re-run: Phase 3 on merged graph
Cost: ~20 lines of clustering code + re-run scoring (~seconds)
```

### Example 4 — Phase 1 prompt misses second sub-condition

```
Problem:  MaterialMisrep = NOT_SATISFIED (found FalseStatements failed)
Reality:  Judge also discussed SchemeToDefraud = SATISFIED later

Fix: Add keyword audit post-extraction, or restructure prompt
Re-run: Phase 1 on affected cases
Cost: Prompt change or ~30 lines of keyword audit
```

### Example 5 — Phase 5 retrieval weights wrong

```
Problem:  Returns semantically similar but legally irrelevant cases
Root cause: Semantic weight (0.4) too high, graph weight (0.3) too low

Fix: Adjust weights in rank.py (e.g., 0.3/0.4/0.2/0.1)
Re-run: Phase 5 retrieval + ranking only
Cost: Change 2 constants, instant re-run
```

## Why "No Retraining" Matters

**Traditional ML:**
```
Model wrong → Need more labeled data → Label 500 cases → Retrain
           → Hope it fixes the error without breaking others
           → Takes days/weeks
           → No guarantee specific error is fixed
```

**Our pipeline:**
```
Pipeline wrong → Inspect which element/constraint/rule failed
              → Edit the specific rule
              → Re-run affected phase only
              → Verify fix works on that case
              → Takes minutes
              → Specific error guaranteed fixed
```

The symbolic layer makes errors **localizable** and **fixable**.

## How Phase 7 Enables the Loop

Phase 7 is the **diagnostic engine**:

```
Evaluation report shows:
  - Element accuracy per element → which elements are weak?
  - Constraint violation rates → which constraints fire most?
  - Baseline comparison → which component adds least value?
  - Per-case details → which specific cases failed?

From the report:
  "Scienter accuracy is 72% but loss_causation is 91%"
  → Focus on scienter extraction
  → Maybe prompt needs better scienter instructions
  → Maybe sub-conditions need expanding
```

## The Iterative Improvement Pattern

From Beyond the Black Box (on energy tweets):
```
Iteration 1: Rule coverage = 45.1% (first pass rules)
Iteration 2: Rule coverage = 72.8% (added exception rules)
Iteration 3: Rule coverage = 81.6% (refined edge cases)
```

Each iteration improved WITHOUT retraining. New rules added, existing refined.

In our pipeline (projected):
```
Iteration 1: Element accuracy = 75% (initial prompt + rules)
Iteration 2: Element accuracy = 82% (keyword audit for missed sub-conditions)
Iteration 3: Element accuracy = 88% (refined prompt for appellate opinions)
```

We're at Iteration 1 with 128 extractions. Phase 7 framework powers future iterations.

## What's Implemented vs Future

| Component | Status |
|---|---|
| Phase 7 metrics (diagnostic) | Implemented — automated metrics run now |
| Failure tracing (which phase?) | Partial — per-component metrics show where failures cluster |
| Rule editing (fix the error) | Fully editable — rules.py, constraints.py, prompt.py are code |
| Incremental re-run | Implemented — can re-run any phase independently |
| Iteration tracking | Not implemented — no versioning of rules across iterations yet |

## Elevator Pitch

"The exception handling loop closes the Programmable AI architecture. Phase 7 evaluation tells us which component failed. We trace it to the specific rule, constraint, or prompt, edit it, and re-run that phase only. No retraining. The Beyond the Black Box paper showed this improves coverage from 45% to 82% in 3 iterations. We've built the infrastructure — editable rules, evaluation framework, incremental re-run — but haven't run multiple iterations yet. That's next after scaling to 5,855 cases."
