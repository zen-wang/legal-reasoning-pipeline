# Phase 7 Progress Log — Evaluation Framework

**Project**: Private 10b-5 Securities Fraud Analysis Pipeline
**Lab**: CIPS Lab, Arizona State University
**Date**: April 10, 2026

---

## Overview

Built a rigorous evaluation framework following LegalBench methodology (Guha et al., 2023). The framework defines component-level metrics for each pipeline phase, 5 baselines for comparison, a blinded human annotation protocol, and 7 explicit anti-pattern rules. Automated metrics run immediately; human-dependent metrics activate when Emre's annotations arrive.

**Key result**: Automated evaluation on 23 eval cases shows the symbolic pipeline produces zero ERROR-level constraint violations (no hallucinated citations, no anachronistic references). ANCO-HITS AUC = 0.687 against regex labels reveals a meaningful gap between regex-derived outcomes and IRAC-based analysis — quantifying why human ground truth is needed. Framework is reproducible across local and Sol environments.

---

## Problem

Phases 0-5 produced a working end-to-end pipeline (LLM tested on Gaudi 2 with 12/12 elements correct), but:

1. The 2 LLM test cases (dockets 6135547, 87229) were development data, not evaluation data
2. The 14-case test set is too small for reliable metrics (bootstrap CIs of 15-25 pp)
3. No human ground truth exists — regex labels cannot evaluate the pipeline that uses them (circularity)
4. No baselines established — "85% accuracy" means nothing without comparison points
5. Component-level performance is unknown — end-to-end success could hide weak phases

---

## Approach

### 7.1 Seven Evaluation Rules

| Rule | What It Prevents |
|------|-----------------|
| **1. No test-set tuning** | Retrieval weights, constraint thresholds, prompts never adjusted on test cases |
| **2. No self-evaluation** | Pipeline's IRAC extractions cannot be ground truth for evaluating the pipeline |
| **3. No single-metric reporting** | Every table: primary metric + per-class breakdown + 95% bootstrap CI + N |
| **4. No data leakage** | Regex labels don't evaluate outcome prediction; dev cases excluded |
| **5. Component vs end-to-end** | Each phase reported separately; no conflation |
| **6. Zero-shot vs few-shot** | Reported separately if prompts include examples |
| **7. Constraint violations as metric** | First-class metric, not just debugging tool |

### 7.2 Component-Level Metrics (LegalBench Typology)

Each pipeline component mapped to a LegalBench task type with appropriate metrics:

| Component | LegalBench Type | Primary Metric | Ground Truth |
|-----------|----------------|----------------|-------------|
| Phase 0: Outcome labeling | Rule-conclusion | Balanced accuracy (3-class) | Emre labels |
| Phase 1: Element extraction | Rule-application | Element status accuracy + analysis quality (0-3) | Emre IRAC |
| Phase 1: Procedural stage | Issue-spotting | Balanced accuracy (4-class) | Emre labels |
| Phase 3: ANCO-HITS | N/A (graph alg) | AUC on held-out cases | Held-out labels |
| Phase 5: Retrieval | N/A (retrieval) | Precision@5, NDCG@10 | Emre relevance judgments |
| Phase 5: Constraints | Rule-recall | Violation rate per type | Dataset ground truth |
| Phase 5+6: LLM generation | Rule-application | Correctness + analysis quality (0-3) | Emre grading |

**Dual metrics for rule-application** (LegalBench Appendix E): Correctness (binary — is the element status correct?) and Analysis Quality (0-3 scale — does the reasoning connect facts to law?). These are reported separately because a model can guess correctly without sound reasoning.

### 7.3 Five Baselines

| # | Baseline | Description | Purpose |
|---|----------|-------------|---------|
| B1 | Majority class | Always predict DEF_WINS | Sanity check floor (33.3%) |
| B2 | Regex-only | `classify_outcome()` pattern matching | Does symbolic lifting add value? |
| B3 | ANCO-HITS threshold | Score thresholds for outcome prediction | Does graph scoring add value? |
| B4 | Zero-shot LLM | Raw opinion → Llama 3.3 70B, no retrieval | Does retrieval + constraints add value? |
| B5 | BM25 + LLM | Keyword retrieval instead of hybrid | Do graph + ANCO signals justify complexity? |

B1-B3 run immediately. B4-B5 require LLM access on Sol (stubs created with instructions).

### 7.4 Human Annotation Protocol

**Annotator**: Emre (lab RA, law student)

**Blinding**: Emre reads raw opinion text only — never sees pipeline output. Cases in randomized order (fixed seed). When grading LLM output quality, system identity hidden (B4 vs full pipeline interleaved).

**Annotation set**: 24 cases (14 test split + 10 hard negatives)

| Hard Negative Category | Count | Selection Criteria |
|----------------------|------:|-------------------|
| MIXED outcomes | 3 | Rarest class, hardest to predict |
| Underrepresented circuits | 2 | CA1, CA8 (not in test split) |
| Low-confidence labels | 2 | Regex confidence < 0.50 |
| CONTESTED elements | 3 | 3-4 contested elements per case |

**No val promotion**: Val cases may have been seen during Phase 5 development. Smaller but clean test set.

**Rubric** (5 sections per case):
- A. Issue Spotting: legal issue(s), procedural stage
- B. Rule Recall: which elements analyzed, statutes, precedents
- C. Rule Application: per-element status + facts + reasoning + confidence
- D. Conclusion: outcome + rationale
- E. Quality Flags: is it 10b-5? sufficient text? debatable elements?

**Inter-annotator agreement** (1 annotator workaround):
- Intra-rater: Emre re-annotates 5 cases after 2 weeks (blind). Target: kappa >= 0.70
- Calibration: Professor annotates 5 cases independently. Target: kappa >= 0.70
- Quality gate: annotations not used as ground truth until kappa passes

---

## Results (Automated Metrics)

### Baseline Comparison

| # | Baseline | Bal. Accuracy | Raw Accuracy | N |
|---|----------|:------------:|:-----------:|:-:|
| B1 | Majority class | 33.3% | 0.0% | 23 |
| B2 | Regex-only | 93.3%* | 95.5%* | 22 |
| B3 | ANCO-HITS threshold | 40.5% | 47.8% | 23 |
| B4 | Zero-shot LLM | STUB | — | — |
| B5 | BM25 + LLM | STUB | — | — |

*B2 is circular: regex labels ARE the ground truth. Will drop to ~55-70% against Emre's annotations.

**Interpretation**:
- B1 (33.3%) confirms balanced accuracy works correctly for 3-class
- B3 (40.5%) barely above floor — ANCO-HITS scores are extremal at current scale (75.9% of arguments are singletons with scores of exactly +1/-1). Will improve with more shared arguments at 3,400 cases.
- B4 (zero-shot LLM) is the most important missing baseline — answers "why not just throw the opinion at an LLM?"

### ANCO-HITS Evaluation

| Metric | Value |
|--------|------:|
| Total scored cases | 121 (of 416) |
| Bipartite training cases | 121 |
| Held-out cases | 0 |
| AUC (training set, vs regex labels) | 0.6872 |
| Spearman correlation | 0.341 (p=0.002) |
| Singleton ratio | 75.9% (281/370 arguments) |
| Mean PLT score | -0.222 |
| Mean DEF score | -0.754 |

**Why AUC = 0.687, not 1.0**: Phase 3 reported AUC=1.0 using IRAC extraction outcomes as ground truth (the same outcomes that seed the algorithm). This evaluation uses regex-derived `case_labels.outcome_label` as ground truth, which disagrees with IRAC outcomes for some cases. The 0.687 AUC honestly measures how well ANCO-HITS scores predict regex-labeled outcomes — a harder and more meaningful test.

**Why held-out = 0**: All 121 scored cases were used to build the bipartite graph (they all have IRAC extractions). True held-out evaluation requires cases with outcome labels but no IRAC extractions in the bipartite graph — achievable at scale.

### Constraint Violation Rates (Symbolic Pipeline)

| Severity | Count | Types |
|----------|------:|-------|
| ERROR | 0 | — |
| WARNING | 50 | missing_element (NOT_ANALYZED elements) |
| INFO | 54 | ambiguity_flag (ANCO-HITS near zero) |

**Zero ERRORs**: The symbolic pipeline produces no hallucinated citations, no anachronistic references, no statute grounding failures. All violations are expected: WARNING-level missing element flags on appeal cases where the judge didn't analyze individual elements, and INFO-level ambiguity flags on cases with ANCO-HITS score = 0 (unscored or truly contested).

### Data Coverage

| Metric | Count | % of 416 |
|--------|------:|---------:|
| Cases with opinion text | 150 | 36.1% |
| Valid IRAC extractions | 128 | 30.8% |
| ANCO-HITS scored cases | 121 | 29.1% |
| Cases with outcome labels | 109 | 26.2% |
| Eval set (test + hard negatives) | 24 | 5.8% |
| SBERT embeddings | 149 | 35.8% |

The remaining 266 cases have no opinion text — nothing to extract until more opinions are scraped from CourtListener.

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| LegalBench task typology | Maps each component to a recognized evaluation category (rule-conclusion, rule-application, issue-spotting). Gives reviewers a clear framework. |
| 24 cases (not 30) | No val promotion — val cases may be contaminated from Phase 5 development. Smaller but clean. |
| 10 hard negatives | 3 MIXED + 2 underrepresented circuits + 2 low-confidence + 3 CONTESTED. Deliberately challenging cases. |
| Dual metrics for rule-application | Correctness and analysis quality measured separately. A model that guesses right for wrong reasons is caught. |
| Regex labels with circularity warning | B2 baseline honestly flagged as circular. Framework correctly refuses to claim pipeline accuracy against its own labels. |
| Bootstrap CIs on all metrics | Honest about uncertainty at N=24. Report acknowledges "statistical significance cannot be established at this sample size." |
| Intra-rater as IAA proxy | With 1 annotator, re-annotation after 2 weeks is the best available consistency measure. Professor calibration on 5 cases adds a second signal. |

---

## Technical Stack

| Component | Technology |
|-----------|-----------|
| Bootstrap CIs | NumPy percentile bootstrap (10,000 resamples) |
| Balanced accuracy | Manual mean-per-class-recall (no sklearn dependency) |
| AUC | scikit-learn roc_auc_score |
| Spearman correlation | scipy.stats.spearmanr |
| Cohen's kappa | Custom implementation (script/eval/iaa.py) |
| Report generation | Markdown via script/eval/report.py |

## Repository Structure (Phase 7)

```
script/
├── run_evaluation.py            # Phase 7 CLI: --baselines-only, --human-metrics, --report
└── eval/
    ├── __init__.py
    ├── config.py                # 24 eval cases, dev exclusions, seeds, thresholds
    ├── bootstrap.py             # Bootstrap CI + balanced accuracy utilities
    ├── baselines.py             # B1-B5 (B4-B5 stubs pending Sol)
    ├── constraint_rates.py      # Per-constraint violation rates
    ├── anco_holdout.py          # ANCO-HITS AUC on held-out subset
    ├── retrieval_metrics.py     # P@K, NDCG, MRR, channel ablation
    ├── element_accuracy.py      # Per-element accuracy vs human labels
    ├── outcome_accuracy.py      # Outcome prediction vs human labels
    ├── iaa.py                   # Cohen's kappa (intra-rater + calibration)
    └── report.py                # Markdown report generator
doc/
├── Annotation_Rubric.md         # Printable rubric for Emre (5 sections)
└── Phase_7_Eval_Report.md       # Auto-generated evaluation report
data/
└── annotation_cases.json        # 24 case docket IDs with selection rationale
```

---

## CLI Usage

```bash
# Run all automated metrics
python -m script.run_evaluation --db data/private_10b5_sample_416.db

# Baselines only
python -m script.run_evaluation --db data/private_10b5_sample_416.db --baselines-only

# Include human-dependent metrics (after Emre annotations)
python -m script.run_evaluation --db data/private_10b5_sample_416.db --human-metrics

# Generate full Markdown report
python -m script.run_evaluation --db data/private_10b5_sample_416.db --report doc/Phase_7_Eval_Report.md
```

---

## Paper Foundations

| Paper | What We Used |
|-------|-------------|
| **LegalBench** (Guha et al., 2023) | Task typology (issue-spotting, rule-recall, rule-application, rule-conclusion), dual metrics for rule-application, evaluation anti-patterns, ground truth construction methodology |
| **Beyond the Black Box** (Trivedi et al.) | Neuro-symbolic evaluation: symbolic components measured independently from neural generation. Graceful degradation as evaluation dimension. |
| **ANCO-HITS** (Gokalp et al., ICTAI) | AUC and Spearman correlation for argument scoring evaluation. Singleton ratio as a diagnostic for graph sparsity. |

---

## Next Steps

- **Emre annotation**: Give Emre `doc/Annotation_Rubric.md` + 24 cases. Target: 2-3 cases/day, ~12 hours total. Unlocks element accuracy, outcome accuracy, and honest baseline comparison.
- **B4 zero-shot baseline**: Batch run on Sol — critical for answering "does the pipeline add value over raw LLM?"
- **B5 BM25 baseline**: Install `rank-bm25`, implement BM25 retrieval, compare against hybrid
- **Retrieval metrics on Sol**: Run P@K, NDCG with sentence-transformers
- **Scale to 3,400 cases**: More IRAC extractions → more shared arguments → intermediate ANCO-HITS scores → higher AUC → held-out evaluation possible
- **Phase 7 progress log update**: After Emre's annotations arrive, re-run evaluation and update results
