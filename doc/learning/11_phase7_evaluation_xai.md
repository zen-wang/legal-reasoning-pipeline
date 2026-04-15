# Topic 11: Phase 7 — Evaluation, XAI, and Output Structure

## What Phase 7 Does

Builds a rigorous evaluation framework to answer: **does the pipeline actually work, and can we trust the results?** Three parts:
1. **Automated metrics** — component-level accuracy, constraint violation rates, baselines
2. **Evaluation design** — anti-overfitting rules, human annotation protocol
3. **XAI (Explainable AI)** — how the pipeline provides transparency

## How This Pipeline Shows XAI (Explainable AI)

Most AI systems are evaluated on accuracy alone — "it got 85% right." In legal analysis, a lawyer needs to know **WHY** and **WHERE the evidence comes from**. The pipeline provides explainability at every layer:

| Layer | What's explainable | How |
|---|---|---|
| **Element level** | Which of 6 elements determined the outcome | Each element has status + sub-conditions + key facts + judge reasoning |
| **Rule level** | Why plaintiff won or lost | `evaluate_outcome()` — deterministic: any NOT_SATISFIED → defendant wins |
| **Evidence level** | What facts support each element | `key_facts` field — specific factual findings from the opinion |
| **Citation level** | Which precedents support the analysis | Every cited case verified against dataset, cross-circuit flagged |
| **Uncertainty level** | Where the system is unsure | CONTESTED flags (ANCO-HITS near 0), NOT_ANALYZED explicit statements |
| **Score level** | How strongly an argument predicts an outcome | ANCO-HITS [-1, +1] on every argument and case |

### Contrast with a Black-Box Classifier

```
Black box: "This case is DEFENDANT_WINS (85% confidence)"
  → Why? "..."
  → Which element failed? "..."
  → What precedent supports this? "..."

Our pipeline: "This case is DEFENDANT_WINS because:
  - Material misrepresentation: NOT_SATISFIED
    Sub-condition: MisleadingOmissions
    Fact: 'pre-class period disclosures could not create duty to disclose'
    Reasoning: [judge quote]
    Supporting precedent: Minneapolis Firefighters v. MEMC (ca8)
  - Scienter: NOT_SATISFIED
    Sub-condition: RecklessDisregard
    Reasoning: 'inference of scienter not as compelling as more innocent inference'
  - Remaining 4 elements: NOT_ANALYZED (dismissed on first two)
  ⚠ Cross-circuit: Minneapolis Firefighters (ca8) cited in ca7 case
  ANCO-HITS score: -1.0 (strongly defendant-favorable)"
```

Every claim traces to: element → sub-condition → facts → verified precedent. A lawyer can follow the chain and verify each step.

**The "programmable" part**: If the explanation is wrong, edit the rule — add a sub-condition, adjust a threshold, fix the prompt. Re-run. No retraining.

## The Output Structure

### IRACAnalysis (LLM-generated)

```json
{
  "issue": "Whether defendant's statements constituted material misrepresentation",
  "rule": "Under Rule 10b-5, plaintiff must establish 6 elements...",
  "application": [
    {
      "element_name": "material_misrepresentation",
      "status": "NOT_SATISFIED",
      "reasoning": "The court found...",
      "supporting_precedents": ["Case X (docket_id)"],
      "anco_hits_score": -1.0,
      "contested": false,
      "not_analyzed": false
    }
    // ... 5 more elements
  ],
  "conclusion": "DEFENDANT_WINS — dismissed on material misrep and scienter",
  "cited_precedents": [
    {"case_name": "...", "docket_id": 123, "court_id": "nysd",
     "verified": true, "cross_circuit": false}
  ],
  "uncertainty_flags": [
    {"flag_type": "CROSS_CIRCUIT", "message": "CA8 case cited in CA7"}
  ],
  "constraint_violations": [...],
  "llm_generated": true
}
```

### SymbolicOnlyResult (no LLM)

```json
{
  "irac_extraction": { "... Phase 1 extraction ..." },
  "anco_hits_score": -1.0,
  "ranked_precedents": [ "... top 10 from hybrid retrieval ..." ],
  "constraint_violations": [ "..." ],
  "llm_generated": false
}
```

## The 7 Evaluation Rules (Anti-Overfitting)

| Rule | What it prevents |
|---|---|
| **1. No test-set tuning** | Never adjust weights, thresholds, or prompts using test cases. All tuning on train/val only. Test touched once. |
| **2. No self-evaluation** | Pipeline's own extractions can't be ground truth. Independent human annotations required. |
| **3. No single-metric reporting** | Every metric: primary + per-class breakdown + 95% bootstrap CI + sample size. |
| **4. No data leakage** | Regex labels can't evaluate outcome prediction (circular). Dev cases excluded. |
| **5. Component vs end-to-end separation** | Each phase reported separately. End-to-end on full pipeline runs only. |
| **6. Separate zero-shot and few-shot** | If prompts include examples, report both modes. |
| **7. Constraint violations as first-class metric** | Violation rates reported independently, not just downstream accuracy. |

### How Can You Trust the Evaluation Won't Fit the Pipeline?

Three structural protections:
1. **Blinded human annotation** — annotator reads raw opinions, never sees pipeline output, randomized order
2. **5 baselines** — pipeline must beat ALL of them, not just report a number in isolation
3. **Bootstrap CIs on 24 cases** — honestly acknowledge intervals are 15-25 pp wide; no overclaiming

## The 5 Baselines

Each isolates one component's contribution:

| # | Baseline | What it tests | Expected |
|---|---|---|---|
| B1 | Majority class (always DEF_WINS) | Sanity floor | 33.3% |
| B2 | Regex-only (`classify_outcome()`) | Does symbolic lifting add value over pattern matching? | ~55-65% |
| B3 | ANCO-HITS threshold | Does graph scoring add value? | ~55-65% |
| B4 | Zero-shot LLM (no retrieval) | Do retrieval + constraints add value over raw LLM? | ~60-75% |
| B5 | BM25 + LLM | Do graph + ANCO signals justify complexity over text search? | ~65-75% |

**The story baselines tell:**
```
B1 (33%) → B2 (55%) : regex adds 22 pp over random
B2 (55%) → B3 (60%) : ANCO-HITS adds 5 pp over regex
B3 (60%) → B5 (70%) : LLM adds 10 pp over rules alone
B5 (70%) → Full (80%): graph + ANCO add 10 pp over BM25
```
Each step justifies the added complexity.

## Component-Level Metrics

| Component | LegalBench Type | Primary Metric | Ground Truth |
|---|---|---|---|
| Phase 0: Outcome labeling | Rule-conclusion | Balanced accuracy (3-class) | Human labels |
| Phase 1: Element extraction | Rule-application | Element status accuracy + quality (0-3) | Human IRAC |
| Phase 3: ANCO-HITS | Graph algorithm | AUC on held-out cases | Held-out labels |
| Phase 5: Retrieval | Retrieval | Precision@5, NDCG@10 | Human relevance judgments |
| Phase 5: Constraints | Rule-recall | Violation rate per type | Dataset ground truth |
| Phase 5+6: LLM generation | Rule-application | Correctness + quality (0-3) | Human grading |
| End-to-end | Rule-conclusion | Balanced accuracy (3-class) | Held-out labels |
| Graceful degradation | System property | Symbolic-only vs LLM accuracy | Same test set |

**LegalBench typing**: Each component mapped to a LegalBench task category. Standard framework for the professor.

**Dual metrics for rule-application** (LegalBench Appendix E):
- **Correctness** (binary): element status errors, hallucinated facts, logic errors
- **Analysis quality** (0-3): 0 = no analysis, 1 = restates facts, 2 = partial connection, 3 = full inference chain

## Human Annotation Protocol

**24 cases**: 14 test split + 10 hard negatives (3 MIXED, 2 underrepresented circuits, 2 low-confidence, 3 contested elements).

**Blinding**: Raw opinion text only, never sees pipeline output, randomized order, no feedback until complete.

**Rubric** (per case):
- A: Issue spotting (legal issues, procedural stage)
- B: Rule recall (elements analyzed, statutes, precedents)
- C: Rule application per element (status, facts, reasoning, confidence)
- D: Conclusion (outcome, rationale)
- E: Quality flags (is this Private 10b-5? sufficient text?)

**Inter-annotator agreement**: Intra-rater reliability — re-annotate 5 cases after 2 weeks (blind). Cohen's kappa ≥ 0.70 as quality gate. Professor calibrates on 5 cases.

## Current Results (Automated, No Human Labels Yet)

| Metric | Value | Note |
|---|---|---|
| B1 Majority baseline | 33.3% | Analytical floor |
| Constraint violation rate | 87% | All INFO/WARNING, zero ERRORs |
| ANCO-HITS AUC (training) | 0.687 | Against regex labels |
| ANCO-HITS AUC (held-out) | Insufficient data | Need more held-out cases |
| Retrieval P@5, NDCG | Pending | Needs sentence-transformers on Sol |

**Honest limitation**: 24 cases → CIs of 15-25 pp. Statistical significance between baselines cannot be established. Framework scales to 3,400+ cases.

## Code Files

| File | Role |
|---|---|
| `script/eval/__init__.py` | Package init |
| `script/eval/config.py` | 24 test cases, dev exclusions, seeds, thresholds |
| `script/eval/bootstrap.py` | 95% CI computation, balanced_accuracy() |
| `script/eval/baselines.py` | B1-B3 implemented, B4-B5 stubs (need Sol) |
| `script/eval/constraint_rates.py` | Per-constraint violation rates |
| `script/eval/anco_holdout.py` | ANCO-HITS AUC on held-out subset |
| `script/eval/retrieval_metrics.py` | P@K, NDCG, MRR, channel ablation |
| `script/eval/element_accuracy.py` | Element-level accuracy vs human labels |
| `script/eval/outcome_accuracy.py` | Outcome prediction accuracy |
| `script/eval/iaa.py` | Cohen's kappa, quality gate |
| `script/eval/report.py` | Markdown report generator |
| `script/run_evaluation.py` | CLI — --baselines-only, --human-metrics, --report |
| `doc/Annotation_Rubric.md` | Printable rubric for annotator |
| `data/annotation_cases.json` | 24 cases with selection rationale |

## Elevator Pitch

"Phase 7 evaluates every component separately with its own metrics, plus 5 baselines isolating each design choice. The evaluation is honest: no test-set tuning, no self-evaluation, blinded annotation, bootstrap CIs. The pipeline provides XAI through traceable element-level analysis — every claim maps to element → sub-condition → fact → verified precedent. If anything is wrong, edit the rule and re-run, no retraining."
