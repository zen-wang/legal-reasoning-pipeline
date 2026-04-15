# Topic 14: Current Limitations and Next Steps

## Honest Limitations

Proactively mention these to teammates and professor — owning limitations builds credibility.

### 1. Small dataset (416 cases, 128 IRAC extractions)

Everything works end-to-end, but 128 cases is a prototype. Most metrics have wide confidence intervals. ANCO-HITS produces trivially perfect AUC because of singletons. GraphSAGE deferred entirely. Scaling to 5,855+ in progress.

### 2. No human ground truth yet

Phase 7 framework is built but no annotations exist. Current metrics use regex labels (circular for outcome prediction) or pipeline's own outputs (self-evaluation). 24-case annotation protocol designed but not executed.

### 3. Singleton arguments in ANCO-HITS (353/370)

Most arguments connect to 1 case — LLM generates unique phrasing per opinion. Scores are ±1 or 0, no intermediate values. Algorithm is correct but uninformative at this scale. SBERT clustering would fix this.

### 4. Context window limits (8192 tokens)

Llama 3.3 supports 128K natively, but Gaudi 2 memory limits practical context to 8192. ~15% of opinions need truncation. Increasing to 16384 would cover ~95%.

### 5. Sub-condition extraction risk

LLM might miss a sub-condition when judge discusses multiple with different outcomes. No second-pass verification. ~70% of cases safe (one sub-condition), ~30% at risk (multiple discussed).

### 6. Appellate opinion bias (88% of dataset)

CourtListener has better appellate coverage. Appellate opinions have implicit element analysis (harder to extract) vs district court with explicit headings (easier). Pipeline tested mostly on harder case.

### 7. LLM dependency for new cases

Phase 1 extraction requires Gaudi 2 (~200s/case, 6.1 cases/min). 5,855 cases ≈ 16 hours of Gaudi 2. Can't process new cases without HPC. Graceful degradation only works for already-extracted cases.

### 8. No semantic argument clustering

Lab papers (NARRA-SCALE, Beyond the Black Box) both use semantic grouping before ANCO-HITS. We skip this, producing singletons. SBERT clustering is ~20 lines but not done yet.

### 9. Retrieval weights unoptimized

0.4/0.3/0.2/0.1 weights are intuitive defaults, no empirical basis. Ablation study planned but not run with human relevance judgments.

### 10. Single LLM, no comparison

Llama 3.3 70B exclusively. No comparison with GPT-4, Claude, or smaller variants. Can't claim Llama is optimal without comparison.

## Next Steps (Prioritized)

### Immediate (in progress)

| Step | What | Impact |
|---|---|---|
| Scale to 5,855 cases | Re-run scraper | 3-4x more IRAC extractions, denser citation network |
| Re-run Phase 1 | Llama 3.3 on Gaudi 2, ~16 hours | More arguments, more embeddings |
| Try `--max-model-len 16384` | Reduce truncation 15% → ~5% | Better extraction quality |

### Short-term (next 2-3 weeks)

| Step | What | Impact |
|---|---|---|
| SBERT argument clustering | ~20 lines, merge similar arguments | Intermediate ANCO-HITS scores |
| Re-run Phase 3 on merged graph | ANCO-HITS on clustered arguments | Meaningful scores instead of ±1 |
| Phase 7 ablation study | Remove one retrieval channel at a time | Justify multi-channel design |
| Human annotation (24 cases) | Execute annotation protocol | Real ground truth |
| Promote court_id to Court node | ~94 nodes in Neo4j | Cleaner circuit queries |

### Medium-term (1-2 months)

| Step | What | Impact |
|---|---|---|
| Refine Phase 0.3 labeler | Use `split_sections()` for CONCLUSION detection | Better outcome labels |
| Keyword audit for sub-conditions | Post-extraction check for missed sub-conditions | Catch Pattern B errors |
| Baselines B4 + B5 | Zero-shot LLM and BM25+LLM on Sol | Complete baseline comparison |
| GraphSAGE at scale | Train on 3,400+ cases | Citation pattern learning |
| Codebook argument taxonomy | ~30-50 canonical argument types | Interpretable ANCO-HITS for paper |

### Long-term (for the paper)

| Step | What | Impact |
|---|---|---|
| Full 10,200 cases | Complete all scraping tiers | Production-scale evaluation |
| Multiple iteration loop | Exception handling 2-3 times | Demonstrate self-improving loop from paper |
| LLM comparison | GPT-4, Claude, smaller Llama | Justify model choice |
| Demo interface | FastAPI + simple frontend | Professor/committee demo |

## Elevator Pitch

"The pipeline works end-to-end at prototype scale — 128 IRAC extractions, knowledge graph, ANCO-HITS scoring, constrained RAG with 6 hard validators, and evaluation framework. Main limitations: scale (416 cases, scaling to 5,855 now), no human ground truth yet (framework built, pending), and singleton arguments (SBERT clustering planned). The architecture is sound — scaling and refinement is engineering, not research risk."
