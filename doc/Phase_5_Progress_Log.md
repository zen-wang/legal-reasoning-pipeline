# Phase 5 Progress Log — Constrained RAG (Retrieve & Lower)

**Project**: Private 10b-5 Securities Fraud Analysis Pipeline
**Lab**: CIPS Lab, Arizona State University
**Date**: April 10, 2026

---

## Overview

Implemented the **lowering step** from the neuro-symbolic pipeline: given a query case, retrieve relevant real precedents via hybrid search (semantic + graph + ANCO-HITS), validate against 6 hard constraints, and optionally generate a structured IRAC analysis via LLM. This is the core contribution from *Beyond the Black Box* (Trivedi et al.) — injecting symbolic knowledge back into neural generation with zero hallucinated citations.

**Key result**: End-to-end pipeline tested on Sol A100 node. 149 opinion embeddings generated in 22s. Hybrid retrieval returns 19 candidates ranked by weighted fusion of 3 signals. All 6 constraint validators fire correctly — cross-circuit citations flagged, anachronistic citations caught, missing elements explicitly stated. Graceful degradation to symbolic-only results when LLM is offline.

---

## Problem

Phases 1-3 produced rich symbolic data per case (IRAC extractions, knowledge graph, ANCO-HITS scores), but no way to **combine** them into a unified analysis. A user asking "analyze this 10b-5 case" needs:

1. Relevant precedents retrieved from real data (not hallucinated)
2. Element-by-element assessment grounded in actual case law
3. Cross-circuit and temporal validity checks
4. Explicit flags for contested or missing elements
5. Natural language synthesis (when LLM available) constrained by symbolic rules

Phase 5 delivers all five, with graceful degradation when the LLM is offline.

---

## Approach

### 5.1 Architecture

```
User Query (docket_id)
  → Embed query text (sentence-transformers all-mpnet-base-v2, 768d)
  → Hybrid Retrieval:
      1. Semantic search (cosine similarity on cached SBERT embeddings)
      2. Graph traversal (Cypher: citations, same statute, same judge, same court)
      3. Score-based boost (ANCO-HITS absolute scores)
  → Weighted fusion re-ranking (0.4 semantic + 0.3 graph + 0.2 ANCO + 0.1 IRAC)
  → Context budget packing (greedy, ~6368 tokens)
  → Lowering prompt (retrieved context + 6 mandatory constraints + IRAC schema)
  → LLM generation (Llama 3.3 70B via vLLM) OR symbolic-only fallback
  → Post-generation constraint validation (6 hard rules)
  → Structured IRAC output (traceable, flagged uncertainties)
```

### 5.2 Hybrid Retrieval (3 Channels)

| Channel | Signal | Source | Score Range |
|---------|--------|--------|-------------|
| Semantic | Cosine similarity on SBERT embeddings | SQLite `opinion_embeddings` table | [0, 1] |
| Graph | Citation distance, same statute/judge/court | Neo4j Cypher traversals (4 queries) | {1.0, 0.5, 0.3, 0.1} by proximity |
| ANCO-HITS | Absolute score (more extreme = more informative) | SQLite `anco_hits_scores` table | [0, 1] |

**Semantic channel**: One-time batch encoding of 149 opinions using `all-mpnet-base-v2` (768d). Embeddings cached in SQLite as BLOBs. Query-time cosine similarity via numpy — <1ms over 149 vectors, no vector index needed at this scale.

**Graph channel** (when Neo4j available): 4 Cypher traversals per query:
1. 1-hop citation neighbors (distance=1, score=1.0)
2. 2-hop citation neighbors (distance=2, score=0.5)
3. Same statute (distance=3, score=0.3)
4. Same court (distance=4, score=0.1)

**Re-ranking formula**: `final = 0.4*semantic + 0.3*graph_proximity + 0.2*abs(anco_hits) + 0.1*has_irac`

Weights are tunable constants at the top of `rank.py` — will be optimized during Phase 7 evaluation.

### 5.3 Six Hard Constraints

These implement the "practical wisdom" layer from *Hybrids* (Newson et al.) — structural requirements that no amount of prompt engineering can guarantee without post-generation validation.

| # | Constraint | Severity | What It Catches |
|---|-----------|----------|-----------------|
| 1 | **Citation check** | ERROR | Hallucinated case names not in our dataset |
| 2 | **Statute grounding** | WARNING | Statutes outside the known 10b-5 vocabulary |
| 3 | **Binding authority** | WARNING | Cross-circuit citations (persuasive, not binding) |
| 4 | **Temporal validity** | ERROR | Citing a case filed after the query case |
| 5 | **Ambiguity flag** | INFO | ANCO-HITS score in [-0.1, +0.1] → genuinely contested |
| 6 | **Missing element** | WARNING | NOT_ANALYZED elements must be explicitly stated |

**Court-to-circuit mapping**: 109 federal courts mapped to 13 circuits + SCOTUS using CourtListener IDs. SCOTUS is binding everywhere; same-circuit is binding; cross-circuit is flagged.

### 5.4 Context Budget Packing

Token budget breakdown (Llama 3.3 70B, 8192 context):

| Component | Tokens |
|-----------|-------:|
| System prompt (constraints + instructions) | 400 |
| Output schema | 300 |
| Template overhead | 100 |
| Reserved for generation | 1,024 |
| **Available for retrieved context** | **6,368** |

Greedy packing strategy:
1. Query case IRAC extraction (always included)
2. Top precedents with full IRAC details (~400 tokens each)
3. Remaining precedents as citation-only summaries (~50 tokens each)

Token estimation: `chars / 2.6` (calibrated in Phase 1 lifting, proven accurate).

### 5.5 Graceful Degradation

When the LLM is unavailable (no Gaudi 2 node, server down, timeout), the pipeline returns a `SymbolicOnlyResult` containing:
- IRAC extraction from Phase 1
- ANCO-HITS case score from Phase 3
- Ranked precedents from hybrid retrieval
- Constraint violations from post-validation

This reinforces the *Beyond the Black Box* thesis: the symbolic pipeline is self-sufficient. The LLM adds natural language synthesis but is not required for the core analysis.

---

## Results

### Embedding Generation

| Metric | Value |
|--------|------:|
| Opinions embedded | 149 |
| Embedding dimensions | 768 |
| Model | all-mpnet-base-v2 |
| Encoding time (A100 GPU) | 22.3s |
| Storage | SQLite BLOB (~460 KB total) |

### Retrieval Quality (Sample: Ketan Patel v. Portfolio Diversification, docket 6135547)

| Rank | Score | Semantic | ANCO | Case |
|-----:|------:|---------:|-----:|------|
| 1 | 0.589 | 0.722 | +1.000 | Citigroup Inc. v. AHW Investment Partnership |
| 2 | 0.587 | 0.717 | -1.000 | James Boykin v. K12, Inc. |
| 3 | 0.585 | 0.712 | -1.000 | Peter Fan v. Stonemor Partners LP |
| 4 | 0.576 | 0.691 | -1.000 | Irving Firemen's Relief Fund v. Uber Technologies |
| 5 | 0.575 | 0.688 | -1.000 | IBEW Local No. 58 Annuity Fund v. EveryWare Global |

- All 10 top results have IRAC extractions
- Semantic scores 0.67-0.72 indicate strong topical similarity
- ANCO-HITS scores correctly reflect outcome polarity
- Query case: PLAINTIFF_WINS, all 6 elements SATISFIED

### Constraint Validation (Same Case)

| Constraint | Violations | Details |
|-----------|-----------|---------|
| Citation check | 0 | All retrieved cases exist in dataset |
| Statute grounding | 0 | — |
| Binding authority | 7 | CA4, CA3, CA9, CA6, CA8 cases cited in CA7 case |
| Temporal validity | 0 | — |
| Ambiguity flag | 0 | ANCO-HITS score +1.000 (not contested) |
| Missing element | 0 | All 6 elements SATISFIED |

### Constraint Validation (Cory v. Stewart, docket 8175 — Edge Case)

| Constraint | Violations | Details |
|-----------|-----------|---------|
| Binding authority | 3 | CA3, CA6, CA2 cases cited in CA5 case |
| Temporal validity | 1 | Newtyn Partners (filed 2025) cited in 2019 case |
| Missing element | 6 | All elements NOT_ANALYZED (appeal case) |

This case demonstrates correct constraint behavior on edge cases: an appeal where the judge didn't analyze individual 10b-5 elements.

---

## LLM End-to-End Results (Gaudi 2)

Tested the full lowering pipeline with Llama 3.3 70B via vLLM on Intel Gaudi 2 (8x HL-225, tensor parallelism 8). The LLM receives retrieved context with 6 mandatory constraint instructions and produces structured IRAC JSON.

### Test 1: Ketan Patel v. Portfolio Diversification (docket 6135547) — PLAINTIFF_WINS

| Metric | Value |
|--------|------:|
| LLM generation time | 261s |
| Generation speed | ~6.4 tok/s |
| Elements parsed | 6/6 |
| Citations returned | 5 |
| Constraint violations | 4 |

**LLM Output:**

```
Issue: Whether Ketan Patel's claim of securities fraud against Portfolio
       Diversification Group satisfies the elements of Private Rule 10b-5

Application:
  material_misrepresentation: SATISFIED — Wagha's investment in options despite
    promising conservative investment constituted material misrepresentation
  scienter: SATISFIED — jury concluded Wagha broke promise, demonstrating scienter
  connection: SATISFIED — securities laws forbid fraud in all aspects of transactions
  reliance: SATISFIED — Patel showed reliance on the Dealers' representations
  economic_loss: SATISFIED — difference between options and promised instruments
  loss_causation: SATISFIED — fraud caused the measurable loss

Conclusion: Satisfies all elements of Private Rule 10b-5
```

**All 6 element statuses match the Phase 1 ground truth exactly.**

**Constraint results:**
- 4 citation ERRORs: Dura v. Broudo, SEC v. Zandford, U.S. v. Naftalin, Holtz v. JPMorgan, Brown v. E.F. Hutton — real landmark cases cited by the LLM from training data, but not in our 416-case dataset. Constraint correctly flags them as unverifiable.
- 0 binding authority / temporal / missing element violations

### Test 2: Ashland v. Oppenheimer (docket 87229) — DEFENDANT_WINS, Mixed Elements

| Metric | Value |
|--------|------:|
| LLM generation time | 196s |
| Generation speed | ~10.4 tok/s (improving with warmup) |
| Elements parsed | 6/6 |
| Citations returned | 3 |
| Constraint violations | 4 |

**Element-by-element accuracy:**

| Element | Ground Truth | LLM Output | Match |
|---------|:-----------:|:----------:|:-----:|
| material_misrepresentation | NOT_SATISFIED | NOT_SATISFIED | Yes |
| scienter | NOT_SATISFIED | NOT_SATISFIED | Yes |
| connection | NOT_ANALYZED | NOT_ANALYZED | Yes |
| reliance | NOT_SATISFIED | NOT_SATISFIED | Yes |
| economic_loss | SATISFIED | SATISFIED | Yes |
| loss_causation | NOT_SATISFIED | NOT_SATISFIED | Yes |

**6/6 elements match ground truth.** The LLM correctly identified the single SATISFIED element (economic_loss) among 4 NOT_SATISFIED and 1 NOT_ANALYZED.

**Constraint results:**
- 3 citation ERRORs: Bell Atl. Corp. v. Twombly, Ashcroft v. Iqbal, Frank v. Dana Corp. — landmark SCOTUS cases used by the original judge but not in our 416-case dataset
- 1 missing element WARNING: `connection` NOT_ANALYZED — correctly flagged

**Key observation**: The LLM cited the same landmark cases the original judge used (Twombly, Iqbal, Frank v. Dana Corp.), which are real and legally correct. They appear as ERRORs only because our dataset doesn't include these foundational cases. At scale (3,400 cases), these landmark precedents would be in the dataset and the citation check would pass.

### Gaudi 2 Performance Notes

| Request | Generation Time | Speed | Note |
|--------:|---------------:|------:|------|
| 1st | >300s (timeout) | ~1.7 tok/s | torch.compile warmup |
| 2nd | 294s (truncated) | ~3.5 tok/s | Still compiling |
| 3rd | 261s | ~6.4 tok/s | Improving |
| 4th | 196s | ~10.4 tok/s | Approaching normal |

Gaudi 2 uses `torch.compile` (not eager mode), which compiles optimized HPU kernels for each new sequence shape. First few requests are slow; speed improves as compilation caches build. Production deployment would pre-warm with dummy requests.

### What the LLM End-to-End Test Validates

1. **Neuro-symbolic loop complete**: Text → IRAC extraction (Phase 1) → Knowledge graph (Phase 2) → ANCO-HITS scores (Phase 3) → Hybrid retrieval → Constrained LLM generation → Validated output
2. **Element accuracy**: 12/12 elements correct across 2 test cases (100%)
3. **Constraint system works on LLM output**: Citation check catches unverifiable references, missing element flag catches NOT_ANALYZED elements
4. **Graceful degradation proven**: Pipeline returns `SymbolicOnlyResult` on timeout, `IRACAnalysis` when LLM succeeds
5. **LLM cites real law**: All citations are real cases/statutes — the constraint errors are dataset coverage gaps, not hallucinations

---

## Neo4j Graph Channel Verification (Local)

Activated the graph retrieval channel by running with Neo4j on Docker locally. The graph channel was coded but untested — all prior tests used `--neo4j-uri none` (semantic-only).

### Graph State

Neo4j verified with `build_graph.py --verify`:

| Metric | Value |
|--------|------:|
| Total nodes | 9,005 |
| Total edges | 15,149 |
| Internal opinions | 486 |
| External opinions (placeholders) | 7,215 |
| CITES edges | 13,530 |
| INVOLVES edges (signed) | 389 |

### Test 1: Ketan Patel (docket 6135547) — Graph-Only Retrieval

Without `sentence-transformers` installed locally, the pipeline gracefully degrades to graph + ANCO-HITS channels only.

| Metric | Semantic-Only (Sol) | Graph-Only (Local) |
|--------|-------------------:|------------------:|
| Candidates retrieved | 19 | 105 |
| Retrieval time | 18.3s (SBERT load) | 0.8s |
| Graph column | `—` | `2-hop ci` |
| Top score | 0.589 | 0.450 |

The graph channel returned **105 opinion candidates** via Cypher — all from 2-hop citation traversals. This case has only 1 direct citation, so no 1-hop results appeared. The 2-hop network is rich because the cited opinion itself cites many others.

### Test 2: Novak v. Kasaks (docket 109152) — High-Citation Case

Selected because it has 33 direct internal citations (most in the dataset).

| Metric | Value |
|--------|------:|
| Candidates | 218 |
| Retrieval time | 0.06s |
| Graph column | `1-hop ci` |
| Top score | 0.600 |

**`1-hop ci` confirmed** — direct citation neighbors ranked highest (score 0.600 = 0.3 × 1.0 graph + 0.2 × 1.0 ANCO + 0.1 IRAC). Cross-circuit constraints correctly flagged CADC, CA3, CA10, CA5 cases cited in a CA2 case.

### Graph Channel Score Contribution

With graph active, the ranking formula works as designed:

| Proximity | Weight | Score Contribution |
|-----------|-------:|---------:|
| 1-hop citation | 0.3 × 1.0 | 0.300 |
| 2-hop citation | 0.3 × 0.5 | 0.150 |
| Same statute | 0.3 × 0.3 | 0.090 |
| Same judge | 0.3 × 0.3 | 0.090 |
| Same court | 0.3 × 0.1 | 0.030 |

On Sol with both channels active (semantic + graph), cases that are both textually similar AND citation-connected will rank highest — the best of both signals.

### Graceful Degradation Fix

Added graceful handling when `sentence-transformers` is not installed: `encode_query()` returns `None`, semantic channel skips silently, graph + ANCO-HITS channels still work. This enables local development and testing without the 3-5GB torch/sentence-transformers install.

---

## Bug Fixes During LLM Integration

| Bug | Cause | Fix | Commit |
|-----|-------|-----|--------|
| LLM response truncated at 1024 tokens | IRAC JSON needs ~1500 tokens; `max_tokens=1024` too low | Added `--max-tokens` CLI flag (default 2048) | `99d90bf` |
| `docket_id: "NOT_PROVIDED"` crash | LLM returned string instead of int for docket_id | `try/except` int conversion, fallback to `None` | `9611fe6` |
| `court_id: null` crash | LLM returned `null` for court_id field | Use `or ""` instead of `.get("court_id", "")` | `705aa4d` |
| No raw response on JSON parse failure | `extract_json()` failed silently | Log first 2000 chars of raw response as WARNING | `a37c81d` |
| 300s timeout too short for Gaudi warmup | First requests trigger torch.compile on HPU | Added `--timeout` CLI flag (default 600s) | `3725975` |
| `sentence-transformers` ImportError locally | Module not installed on local machine | `encode_query()` returns `None`, semantic channel skips | `b809421` |

These fixes make the pipeline robust to LLM output variability and environment differences between local/Sol machines.

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Custom retrieval (not LlamaIndex) | We already have Neo4j + SQLite + vLLM client. No abstraction layer needed. Direct control over retrieval signals. |
| Numpy cosine search (no vector index) | 149 vectors × 768d = <1ms search. Neo4j vector index adds complexity without benefit at this scale. Revisit at 3,400+ opinions. |
| sentence-transformers `all-mpnet-base-v2` | Best general-purpose sentence embedding model. 768d, 384 max tokens. Sufficient for opinion text (header-stripped). |
| Reuse `lifting/llm_client.py` | Same vLLM endpoint, same `extract_json()` fallbacks. Different prompt, same interface. |
| 6 constraints as post-validators (not prompt-only) | Prompt instructions reduce but don't eliminate violations. Post-generation validation catches what the LLM misses. Structural guarantee. |
| Graceful degradation | LLM is optional by design. Symbolic pipeline provides value independently. Aligns with Beyond the Black Box architecture. |
| Tunable fusion weights | `0.4/0.3/0.2/0.1` are initial defaults. Phase 7 evaluation will optimize via grid search on labeled data. |
| `PYTHONNOUSERSITE=1` on Sol | Prevents `~/.local` site-packages from polluting the conda environment. Required for clean torch/transformers imports. |

---

## Sol HPC Setup

### Request A100 Interactive Node (embeddings + analysis client)

```bash
salloc -c 8 -N 1 -t 0-00:30 -p general -q class -A class_cse57388551fall2025 --mem=64G --gres=gpu:a100:1
conda activate legal
```

### Request A100 Node for vLLM Server (4x A100-80GB)

```bash
salloc -c 32 -N 1 -t 0-02:00 -p general -q class -A class_cse57388551fall2025 --mem=128G --gres=gpu:a100:4
conda activate legal
PYTHONNOUSERSITE=1 bash script/run_vllm_a100.sh
```

### Start vLLM on Gaudi 2 (batch job)

```bash
sbatch script/run_vllm_legal.sh
squeue -u $USER                         # find node hostname
tail -f logs/vllm_legal_<jobid>.log     # wait for "Uvicorn running"
```

### Environment Setup

```bash
conda activate legal
pip install -r requirements.txt --force-reinstall  # one-time, includes sentence-transformers
```

### Run Commands

```bash
# One-time: generate SBERT embeddings (22s on A100)
PYTHONNOUSERSITE=1 python -m script.analyze_case --db data/private_10b5_sample_416.db --embed-only

# Dry run: retrieval results only (no LLM)
PYTHONNOUSERSITE=1 python -m script.analyze_case --db data/private_10b5_sample_416.db --docket-id 6135547 --dry-run --neo4j-uri none

# Symbolic analysis: retrieval + constraints (no LLM)
PYTHONNOUSERSITE=1 python -m script.analyze_case --db data/private_10b5_sample_416.db --docket-id 6135547 --symbolic-only --neo4j-uri none

# Full analysis with LLM on Gaudi 2
PYTHONNOUSERSITE=1 python -m script.analyze_case --db data/private_10b5_sample_416.db --docket-id 6135547 --llm-url http://<gaudi-hostname>:8000 --timeout 900 --max-tokens 2048

# Full analysis with LLM on A100 (same node, use localhost)
PYTHONNOUSERSITE=1 python -m script.analyze_case --db data/private_10b5_sample_416.db --docket-id 6135547 --llm-url http://localhost:8000
```

---

## Technical Stack

| Component | Technology |
|-----------|-----------|
| Embeddings | sentence-transformers `all-mpnet-base-v2` (768d) |
| Embedding storage | SQLite `opinion_embeddings` table (BLOB) |
| Semantic search | Numpy cosine similarity |
| Graph retrieval | Neo4j Cypher (4 traversal queries) |
| Scoring | ANCO-HITS from Phase 3 |
| Re-ranking | Weighted fusion (4 signals) |
| Context packing | Greedy token budgeting (chars/2.6) |
| LLM | Llama 3.3 70B via vLLM (optional) |
| Constraint validation | 6 pure-function validators |
| GPU (embeddings) | NVIDIA A100-SXM4-80GB (Sol HPC) |
| GPU (LLM server) | Intel Gaudi 2 HL-225 ×8 or NVIDIA A100-80GB ×4 |

## Repository Structure (Phase 5)

```
script/
├── analyze_case.py              # Phase 5 CLI: --embed-only, --dry-run, --symbolic-only
├── run_vllm_legal.sh            # vLLM on Gaudi 2 (8x HL-225, TP=8)
├── run_vllm_a100.sh             # vLLM on A100 (4x 80GB, TP=4)
└── rag/
    ├── __init__.py
    ├── schema.py                # Output models: IRACAnalysis, SymbolicOnlyResult, etc.
    ├── embeddings.py            # SBERT batch encoding + SQLite cache + cosine search
    ├── retrieve.py              # Hybrid retrieval: semantic + graph + ANCO-HITS
    ├── rank.py                  # Weighted fusion re-ranking (tunable constants)
    ├── constraints.py           # 6 hard validators + COURT_TO_CIRCUIT (109 courts)
    ├── context.py               # Token budget packing (~6368 tokens)
    └── lower.py                 # Lowering prompt + LLM + graceful degradation
```

---

## Paper Foundations

| Paper | What We Used |
|-------|-------------|
| **Beyond the Black Box** (Trivedi et al.) | Lifting → Symbolic Rule Interface → **Lowering** → Exception Handling. Phase 5 implements the lowering step. |
| **RAG** (Lewis et al., NeurIPS 2020) | Retrieve-then-generate pattern. Our retriever is hybrid (graph + semantic), not pure vector search. |
| **GraphRAG** (Edge et al., 2024) | Graph structure improves retrieval over pure vector search. Our IRAC extractions serve as structured summaries analogous to community summaries. |
| **Hybrids** (Newson et al.) | Human oversight as structural requirement. The 6 hard constraints are the "practical wisdom" layer that no prompt engineering can replace. |
| **ANCO-HITS** (Gokalp et al., ICTAI) | Argument/case scores on [-1, +1] provide the scoring signal for retrieval re-ranking and ambiguity detection. |

---

## Completion Status

| Milestone | Status |
|-----------|--------|
| Embedding generation (149 opinions) | Done — 22s on A100 |
| Semantic retrieval (cosine search) | Done — tested on Sol |
| Neo4j graph channel (5 Cypher queries) | Done — tested locally, 105-218 candidates |
| Constraint validation (6 validators) | Done — all firing correctly |
| LLM end-to-end on Gaudi 2 | Done — 12/12 elements correct, 2 test cases |
| Graceful degradation | Done — SymbolicOnlyResult on timeout/missing LLM |
| A100 vLLM script | Created (`run_vllm_a100.sh`) — untested pending node availability |

## Next Steps

- **Phase 7 evaluation**: Rigorous evaluation framework with 5 baselines, 24 human-annotated cases, LegalBench methodology
- **A100 vLLM testing**: Test `run_vllm_a100.sh` for faster generation without Gaudi warmup overhead
- **Semantic argument deduplication**: Cluster similar arguments to improve ANCO-HITS intermediate scores
- **Scale to 3,400 cases**: Re-run embeddings and ANCO-HITS after full scrape completes; add Neo4j vector index at that scale
- **Expand citation database**: Add landmark SCOTUS cases (Twombly, Iqbal, Tellabs, Dura, Basic v. Levinson) to reduce false-positive citation violations
