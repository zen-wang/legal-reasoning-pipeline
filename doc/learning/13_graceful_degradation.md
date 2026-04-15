# Topic 13: Graceful Degradation

## The Core Idea

The LLM (Llama 3.3 70B on Gaudi 2) is the most expensive and least available component — 8x Gaudi 2 GPUs, SLURM allocation, 5-10 min startup, ~200s per case. Graceful degradation means: **the pipeline delivers useful results even when the LLM is completely offline.** The symbolic layer is self-sufficient.

## What Works Without the LLM

| Component | Needs LLM? | Works offline? |
|---|---|---|
| Phase 1 IRAC extractions (in SQLite) | Already computed | Yes |
| Phase 3 ANCO-HITS scores (in SQLite) | Already computed | Yes |
| Phase 5 SBERT embeddings (in SQLite) | Already computed | Yes |
| Phase 5 semantic search (numpy cosine) | No | Yes |
| Phase 5 graph traversal (Neo4j Cypher) | No | Yes (needs Neo4j) |
| Phase 5 re-ranking (weighted fusion) | No | Yes |
| Phase 5 constraint validation (6 rules) | No | Yes |
| Phase 5 LLM generation | **Yes** | **No — only part that fails** |

Only one step requires the LLM at query time: natural language IRAC generation in `lower.py`. Everything else runs locally.

## The Two Output Modes

**With LLM → IRACAnalysis:**
- Natural language Issue, Rule, Application, Conclusion
- LLM synthesizes precedents into coherent analysis
- Constraint-validated citations
- `llm_generated: true`

**Without LLM → SymbolicOnlyResult:**
- IRAC extraction from Phase 1 (element statuses, facts, reasoning)
- ANCO-HITS case score from Phase 3
- Ranked precedents from hybrid retrieval
- Constraint violations from validation
- `llm_generated: false`

## What the User Sees

```
With LLM:
  "This case is likely DEFENDANT_WINS. The court found material
   misrepresentation NOT_SATISFIED because pre-class period disclosures
   did not create a duty to disclose (citing Minneapolis Firefighters
   v. MEMC [CA8, CROSS-CIRCUIT]). Scienter was also NOT_SATISFIED —
   the inference was not as compelling as the more innocent inference.
   The remaining elements were NOT_ANALYZED."

Without LLM:
  OUTCOME: DEFENDANT_WINS
  ANCO-HITS: -1.000

  Elements:
    material_misrepresentation: NOT_SATISFIED (MisleadingOmissions)
    scienter: NOT_SATISFIED (RecklessDisregard)
    connection: NOT_ANALYZED
    reliance: NOT_ANALYZED
    economic_loss: NOT_ANALYZED
    loss_causation: NOT_ANALYZED

  Top precedents:
    1. Citigroup v. AHW (score=0.589)
    2. Boykin v. K12 (score=0.587)
    3. Fan v. Stonemor (score=0.585)

  Constraints: 7 cross-circuit warnings
```

Symbolic result has all the same data — element statuses, scores, precedents, flags. Just lacks natural language prose. A lawyer can still read and interpret it.

## Why This Matters for the Thesis

Beyond the Black Box argues the symbolic rule interface should be **independent** of the neural model. Graceful degradation proves this:

1. **Symbolic layer has value on its own** — IRAC extraction + ANCO-HITS + retrieval + constraints all work without LLM
2. **LLM adds polish, not substance** — natural language is nice but core analysis (elements, outcome, precedents) comes from symbolic pipeline
3. **Deployable without expensive GPU** — demo runs on laptop with SQLite + Neo4j, showing symbolic analysis. LLM is optional upgrade.

## How It's Implemented

In `lower.py`:

```python
def lower(docket_id, case_name, query_irac, context_str,
          precedents, constraint_ctx, client=None):

    # No LLM client → symbolic only
    if client is None:
        return build_symbolic_result(...)

    # LLM call fails → symbolic only
    try:
        raw, parsed = client.chat_completion(messages, max_tokens=1024)
    except (ConnectionError, TimeoutError, RuntimeError):
        return build_symbolic_result(...)

    # LLM succeeded → full IRACAnalysis
    return parse_and_validate(raw, parsed, ...)
```

Three triggers for degradation:
1. `--symbolic-only` flag (user choice)
2. No `--llm-url` provided (no LLM configured)
3. LLM call fails (timeout, connection error, server down)

## Elevator Pitch

"The pipeline works without the LLM — by design, not a workaround. The symbolic layer (IRAC extractions, ANCO-HITS scores, hybrid retrieval, constraint validation) runs locally and provides the core analysis: which elements are satisfied, what the outcome is, which precedents are relevant, what constraints are violated. The LLM adds natural language synthesis but isn't required. This proves the Beyond the Black Box thesis — the symbolic rule interface is self-sufficient."
