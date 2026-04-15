# Topic 8: Phase 3 — ANCO-HITS Argument Scoring

## What Phase 3 Does

Takes the signed bipartite graph from Phase 2 (Cases ↔ LegalArguments with +1/-1 edges) and runs the ANCO-HITS algorithm to score every argument and case on [-1, +1]. Near +1 = predicts plaintiff wins, near -1 = predicts defendant wins, near 0 = genuinely contested.

## Why Score Arguments?

Phase 2 tells us "argument X appeared in case Y, plaintiff lost." But not how **strong** that argument is across ALL cases. Is "no motive and opportunity" a slam-dunk defense (10 defendant wins) or weak (5 wins, 4 losses)?

ANCO-HITS answers this by propagating outcome information through the bipartite graph.

## The Algorithm (from Gokalp et al. "Partisan Scale")

Originally designed to score US Senators and Bills on a partisan scale using voting records. Adapted: Senators → Cases, Bills → LegalArguments, Votes → INVOLVES edges with signs.

**The math** (Equation 1):

```
For each case i:
  x_i = Σ_j (sign[i,j] × arg_score[j]) / Σ_j |sign[i,j] × arg_score[j]|

For each argument j:
  y_j = Σ_i (sign[i,j] × case_score[i]) / Σ_i |sign[i,j] × case_score[i]|
```

**Per-entity normalization**: Each score divided by its own sum of absolute contributions. A case connected to 10 arguments and one connected to 2 are both on [-1, +1].

### Step by Step

```
1. Initialize:
   case_scores = outcome seeds (+1 PLT_WINS, -1 DEF_WINS, 0 MIXED)
   argument_scores = all ones (paper default)

2. Repeat:
   a. Update argument scores from case scores (authority step)
   b. Update case scores from argument scores (hub step)
   c. Converge when max(|delta|) < 1e-6

3. Output: argument_scores (370,) and case_scores (121,) in [-1, +1]
```

Seeding from outcomes departs from the paper (which initializes all to 1). Fixes sign orientation and accelerates convergence.

## Worked Example

3 cases, 4 arguments:

```
Case A: PLAINTIFF_WINS (seed = +1)
  - Arg1 "CEO sold shares" (plaintiff arg, sign = +1)
  - Arg2 "corrective disclosure" (plaintiff arg, sign = +1)
  - Arg3 "no scienter" (defendant arg, sign = -1, defendant lost)

Case B: DEFENDANT_WINS (seed = -1)
  - Arg1 "CEO sold shares" (plaintiff arg, sign = -1, plaintiff lost)
  - Arg4 "safe harbor" (defendant arg, sign = +1, defendant won)

Case C: DEFENDANT_WINS (seed = -1)
  - Arg2 "corrective disclosure" (plaintiff arg, sign = -1, plaintiff lost)
  - Arg4 "safe harbor" (defendant arg, sign = +1, defendant won)
```

Sign matrix:
```
              Arg1   Arg2   Arg3   Arg4
Case A:      [ +1,    +1,    -1,     0 ]
Case B:      [ -1,     0,     0,    +1 ]
Case C:      [  0,    -1,     0,    +1 ]
```

**Iteration 1 — Authority step** (update argument scores):

Argument 1 "CEO sold shares":
```
raw  = (+1 × 1) + (-1 × -1) = +2
denom = |+1 × 1| + |-1 × -1| = 2
score = +2/2 = +1.0  (strong plaintiff argument)
```

Argument 4 "safe harbor":
```
raw  = (+1 × -1) + (+1 × -1) = -2
denom = 2
score = -2/2 = -1.0  (strong defendant argument)
```

Score convention: +1 = plaintiff-favorable, -1 = defendant-favorable. A successful defense argument scores -1.

**After iteration 1**: all scores are ±1.0. Iteration 2 produces same values → **converged**.

With shared arguments at scale (e.g., Arg1 in 5 PLT wins + 3 DEF wins), scores would be intermediate (~+0.25) showing genuinely contested ground.

## Results

| Metric | Value |
|---|---|
| Convergence | 2 iterations, 0.001s |
| AUC (PLT vs DEF) | 1.0 (trivially perfect — singletons) |
| Arguments scored +1 | 141 |
| Arguments scored -1 | 140 |
| Arguments scored 0 | 89 |
| All scores exactly ±1 or 0 | Yes — no intermediate values |

## The Singleton Problem

353/370 arguments connect to exactly 1 case. A singleton's score = its parent case's sign. No cross-case pattern discovery possible.

**Root cause**: LLM generates case-specific phrasing. "CEO sold shares during class period" and "Executives sold stock during class period" become separate nodes despite being the same legal concept.

## How the Papers Handle Semantic Grouping

### NARRA-SCALE (Triplet + DBSCAN)
- Extract key phrases with spaCy/YAKE → build (Entity, Issue, Value) triplets → BERT embed triplets → UMAP → DBSCAN clustering
- Found 52 narratives from 14M tweets
- Works because tweets reuse the same phrases
- **For our pipeline**: Triplet structure doesn't map cleanly to legal arguments (complex, multi-clause sentences)

### Beyond the Black Box (Codebook + LLM)
- Key phrases → predefined codebook of narrative templates → LLM classifies phrases into templates via few-shot prompting
- Found 306 patterns from 1.3M tweets
- Most rigorous — produces interpretable, labeled groups
- **For our pipeline**: Requires domain expertise to build legal argument codebook (~30-50 categories) + Gaudi 2 for LLM classification

### Neither Paper Has Our Problem
Their inputs are naturally shared:
- Partisan Scale: Bills have official IDs — same bill, same node
- NARRA-SCALE: Tweets reuse the same phrases — "systemic racism" appears in thousands of tweets
- Beyond the Black Box: Codebook categories are predefined — phrases map to existing templates

Our arguments are free-text extracted by LLM — each judge writes differently, producing unique strings for the same legal concept.

## Proposed Solution: SBERT Clustering

| | NARRA-SCALE | Black Box | SBERT (proposed) |
|---|---|---|---|
| **Requires domain expertise?** | Moderate | High (codebook) | Low |
| **Requires LLM/Gaudi 2?** | Yes | Yes | **No** |
| **Runs locally?** | No | No | **Yes, in seconds** |
| **Code effort** | Multiple preprocessing steps | Codebook + LLM prompts | **~20 lines** |
| **Quality** | Good for short text | Best (interpretable labels) | Good for first pass |

**Implementation** (not built yet):
```
1. Encode 370 arguments with SBERT (already installed)
2. Compute pairwise cosine similarity (370×370, instant)
3. Cluster with agglomerative clustering at threshold 0.85
4. Merge clusters into single argument nodes
5. Re-build bipartite graph → re-run ANCO-HITS
```

At 10K cases with ~3,000-5,000 arguments, SBERT clustering still handles it fine (5000×5000 matrix is trivial).

**Recommended path**:
1. **Now**: SBERT clustering (quick win, assess cluster quality)
2. **After assessment**: Discuss with teammates whether codebook + LLM method is worth the extra effort
3. **If scaling to 10K+ arguments**: Re-evaluate — SBERT may still suffice, or codebook adds interpretability for the paper

## Where Scores Are Used Downstream

| Phase | How |
|---|---|
| Phase 5 (Retrieval) | `abs(anco_hits_score)` as ranking signal |
| Phase 5 (Constraints) | Score in [-0.1, +0.1] → [CONTESTED] flag |
| Phase 7 (Evaluation) | AUC on held-out cases |

## Code Files

| File | Role |
|---|---|
| `script/scoring/__init__.py` | Package init |
| `script/scoring/bipartite.py` | Extract signed bipartite graph from Neo4j or SQLite, build numpy matrix |
| `script/scoring/anco_hits.py` | Core algorithm — pure numpy, no I/O, Equation 1 implementation |
| `script/scoring/validate.py` | AUC, Spearman correlation, score histograms, convergence plot |
| `script/scoring/write_scores.py` | Persist to Neo4j (node properties) + SQLite (anco_hits_scores table) |
| `script/score_arguments.py` | CLI — --source neo4j/sqlite, --dry-run, --no-plot |

## Elevator Pitch

"Phase 3 runs ANCO-HITS on the signed bipartite graph to score every legal argument on [-1, +1]. Near +1 means plaintiff-favorable across cases, near -1 means defendant-favorable. At current scale, most arguments are singletons so scores are extremal. Semantic argument clustering (using SBERT embeddings) would merge similar arguments and produce meaningful intermediate scores — that's the next improvement, following the methodology established in the lab's NARRA-SCALE paper."
