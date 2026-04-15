# Topic 10: Phase 5 — Constrained RAG (Retrieve & Lower)

## What Phase 5 Does

This is the **lowering step** from Beyond the Black Box. When a user queries a case, the system:
1. Retrieves relevant real precedents using 3 channels
2. Packs them into a constrained prompt
3. Generates a structured IRAC analysis via LLM
4. Validates the output against 6 hard rules
5. Returns traceable, grounded legal analysis — or falls back to symbolic-only if LLM is offline

Also Phase 6 (IRAC Output) — the lowering step IS the generation step. Combined into one package.

## The Architecture

```
User Query (docket_id)
  → Embed query text (SBERT all-mpnet-base-v2)
  → Hybrid Retrieval (3 channels):
      1. Semantic search (cosine similarity on cached embeddings)
      2. Graph traversal (4 Cypher queries on Neo4j)
      3. Score boost (ANCO-HITS absolute scores)
  → Weighted fusion re-ranking
  → Context budget packing (fit into ~6368 tokens)
  → Lowering prompt (context + 6 constraint instructions + IRAC schema)
  → LLM generation (Llama 3.3 70B via vLLM)
  → Post-generation constraint validation (6 hard rules)
  → Structured IRAC output
```

## Channel 1: Semantic Search

**What**: Encode query case's opinion with SBERT, cosine similarity against 149 cached embeddings.

**What it finds**: Cases with similar legal reasoning, fact patterns, language — even if not connected in the citation graph.

**Example**: Two cases discussing "CEO sold shares during class period as evidence of scienter" have high similarity even in different circuits with no citation link.

**Implementation**: One-time batch encoding → SQLite BLOBs. Query-time numpy cosine — <1ms for 149 vectors.

## Channel 2: Graph Traversal

**What**: 4 Cypher queries on Neo4j:

| Query | What it finds | Proximity score |
|---|---|---|
| 1-hop citations | Directly cited by or citing this case | 1.0 (strongest) |
| 2-hop citations | Transitive neighbors (A→B→C) | 0.5 |
| Same statute | Cases charged under same statutes | 0.3 |
| Same court | Cases in same court | 0.1 (weakest) |

**What it finds that semantic misses**: Two cases about different topics (insider trading vs accounting fraud) that cite the same landmark. Semantically different text, legally related through citation structure.

## Channel 3: ANCO-HITS Score Boost

**What**: `abs(anco_hits_score)` as ranking signal. More extreme = more informative precedent.

**Why absolute value**: Both strongly plaintiff-favorable (+0.9) and defendant-favorable (-0.9) are useful. Contested cases (near 0) are less informative as precedents.

## Re-Ranking (Weighted Fusion)

```
final = 0.4 × semantic_score
      + 0.3 × graph_proximity
      + 0.2 × abs(anco_hits_score)
      + 0.1 × has_irac (1.0 if case has IRAC extraction, 0 otherwise)
```

Weights are tunable constants — defaults based on intuition (semantic strongest, graph second, ANCO third, IRAC minor bonus). No lab papers use this exact weighted fusion for retrieval — NARRA-SCALE uses ANCO-HITS directly, Beyond the Black Box uses rule matching, Hybrids is theoretical.

### Three Options to Optimize Weights

**Option 1 — Grid search (Phase 7):**
With human relevance judgments from 24 annotation cases:
```
For each weight combination (step 0.1):
  Compute Precision@5 and NDCG@10
  Pick combination maximizing NDCG@10 on val set
  Report final numbers on test set
```
Standard approach. Use val cases only — never tune on test.

**Option 2 — Learn from feedback (future):**
If demo lets users mark retrieved precedents as "relevant"/"not relevant," learn optimal weights from that feedback. Not feasible now.

**Option 3 — Ablation study (simplest, most convincing):**
Remove one channel at a time, measure impact:
```
Full pipeline (all 4 signals):       NDCG = X
Remove semantic (0.0/0.3/0.2/0.1):   NDCG = X - ?
Remove graph (0.4/0.0/0.2/0.1):      NDCG = X - ?
Remove ANCO (0.4/0.3/0.0/0.1):       NDCG = X - ?
Remove IRAC (0.4/0.3/0.2/0.0):       NDCG = X - ?
```
If each removal hurts, the multi-channel design is justified. Already built in Phase 7 (`retrieval_metrics.py` has channel ablation).

**Recommendation**: Ablation first (already built), grid search second if time permits. Ablation is more convincing to reviewers — shows WHY each channel matters, not just tuned numbers.

## Context Budget Packing

```
8192 total tokens
- 400 system prompt (6 constraints)
- 300 output schema
- 100 template overhead
- 1024 reserved for generation
= 6368 tokens for retrieved context
```

**Greedy packing**:
1. Query case IRAC (always, ~400 tokens)
2. Top precedents with full IRAC details (~400 tokens each)
3. Remaining as brief citation-only (~50 tokens each)

Token estimation: chars / 2.6 (calibrated in Phase 1).

## The 6 Hard Constraints

Post-generation validators — run AFTER LLM output. Prompt also instructs these rules, but prompt is "soft" (LLM can ignore). Validators are "hard" (catch what prompt missed).

| # | Constraint | Severity | What it catches | How it checks |
|---|---|---|---|---|
| 1 | **Citation check** | ERROR | Hallucinated case names | Must exist in 416-case dataset. Fuzzy name matching. |
| 2 | **Statute grounding** | WARNING | Unknown statutes | Must be in known vocabulary (104 statutes from IRAC). |
| 3 | **Binding authority** | WARNING | Cross-circuit citations | COURT_TO_CIRCUIT mapping (109 courts → 13 circuits). SCOTUS binding everywhere. |
| 4 | **Temporal validity** | ERROR | Anachronistic citations | Can't cite case filed AFTER query case. |
| 5 | **Ambiguity flag** | INFO | Contested elements | ANCO-HITS in [-0.1, +0.1] → [CONTESTED]. |
| 6 | **Missing element** | WARNING | NOT_ANALYZED elements | Must explicitly state, not speculate. |

**Why post-generation validators, not just prompt instructions?**

The prompt says "never fabricate citations." But Llama 3.3 cited Twombly, Iqbal, and Frank v. Dana Corp. — real landmarks but not in our dataset. The citation check caught all 3. No prompt alone guarantees this.

**How the 6 constraints map to the papers:**
- Constraints 1-4 are **structural guarantees** from Hybrids (Newson et al.) — "practical wisdom" that must be built into the architecture, not left to the model
- Constraints 5-6 use **symbolic pipeline data** (ANCO-HITS scores, Phase 1 element statuses) — the symbolic layer informing the neural output
- Together they implement the Beyond the Black Box lowering principle: symbolic knowledge constraining neural generation

## The Lowering Prompt

**System prompt**: 6 constraint rules as explicit instructions + IRAC JSON schema.

**User prompt**: "Analyze this Private 10b-5 case using IRAC. Base your analysis ONLY on the provided context." + packed context.

LLM: Llama 3.3 70B, temperature 0.0 (deterministic). Same `extract_json()` from Phase 1 parses the response.

## Graceful Degradation

If LLM unavailable → return **SymbolicOnlyResult**:
- IRAC extraction from Phase 1
- ANCO-HITS case score from Phase 3
- Ranked precedents from hybrid retrieval
- Constraint violations from validation
- `llm_generated: False`

**Why this matters**: The symbolic pipeline is self-sufficient. Directly supports Beyond the Black Box thesis — the symbolic rule interface works without the neural model. LLM adds natural language synthesis but isn't required.

## End-to-End Test Results (Gaudi 2)

| Metric | Result |
|---|---|
| Element status accuracy | 12/12 matched Phase 1 ground truth |
| Unverified citations | 3 (Twombly, Iqbal, Frank v. Dana Corp — real but not in dataset) |
| Cross-circuit flags | 7 correctly flagged |
| Anachronistic citations caught | 1 (Newtyn Partners 2025 cited in 2019 case) |
| Latency | 196s per case (improving with Gaudi warmup) |

## Code Files

| File | Role |
|---|---|
| `script/rag/__init__.py` | Package init |
| `script/rag/schema.py` | Output models — IRACAnalysis, SymbolicOnlyResult, RetrievedPrecedent, ConstraintViolation |
| `script/rag/embeddings.py` | SBERT batch encoding + SQLite cache + cosine_search() |
| `script/rag/retrieve.py` | Hybrid retrieval — semantic + graph (4 Cypher queries) + ANCO-HITS |
| `script/rag/rank.py` | Weighted fusion re-ranking (tunable constants) |
| `script/rag/constraints.py` | 6 hard validators + COURT_TO_CIRCUIT mapping (109 courts) |
| `script/rag/context.py` | Token budget packing (~6368 tokens, greedy) |
| `script/rag/lower.py` | Lowering prompt + LLM call + graceful degradation |
| `script/analyze_case.py` | CLI — --docket-id, --symbolic-only, --dry-run, --embed-only, --batch-golden |

## Elevator Pitch

"Phase 5 is the lowering step — retrieve relevant precedents using three channels (semantic similarity, citation graph, ANCO-HITS scores), pack them into a constrained prompt, and generate a structured IRAC analysis. Six hard constraints validate the output — catching hallucinated citations, cross-circuit issues, and anachronistic references that the LLM alone can't prevent. If the LLM is offline, the system gracefully degrades to symbolic-only results, proving the symbolic pipeline is self-sufficient."
