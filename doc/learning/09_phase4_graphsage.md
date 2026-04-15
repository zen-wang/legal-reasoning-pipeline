# Topic 9: Phase 4 — GraphSAGE (Deferred)

## What GraphSAGE Is

GraphSAGE (Graph SAmple and aggrEgatE) is a graph neural network that learns node embeddings by **sampling and aggregating features from a node's neighborhood**. Instead of training a separate embedding for each node (transductive), it learns an aggregation function that works on any node — including unseen ones (inductive).

From the paper (Hamilton et al., NeurIPS 2017):

```
For each node v, at each layer k:
  1. Sample neighbors of v
  2. Aggregate their feature vectors (mean, LSTM, or pooling)
  3. Concatenate with v's own vector
  4. Pass through a neural network layer
  5. Normalize

After K layers, node v's embedding encodes information from its K-hop neighborhood.
```

## What It Would Do in Our Pipeline

GraphSAGE would learn to predict case outcomes from the **citation network structure** — not what a case says, but where it sits in the legal citation graph.

| Symbolic patterns tell you | GraphSAGE would tell you |
|---|---|
| WHAT the case says (elements, arguments) | WHERE the case sits in the citation network |
| Which elements are satisfied | Whether similar cases in the neighborhood won or lost |
| Deterministic rule matching | Structural similarity that text can't capture |

## What Is "Citation Network Structure"?

The citation network is just **which opinion cites which** — the edges, not the content of cited cases.

```
Opinion A (our case, has full text)
  → CITES → Opinion B (external, placeholder, no text)
  → CITES → Opinion C (external, placeholder)
  → CITES → Opinion D (another of our cases, has full text)

Opinion E (our case)
  → CITES → Opinion B (same external opinion cited by A)
  → CITES → Opinion F (external)
```

GraphSAGE learns: **Opinion A and Opinion E both cite Opinion B** — structurally similar (shared citation neighbor). If A was PLAINTIFF_WINS, that's a signal about E.

It doesn't need to read Opinion B's text. The **pattern of connections** is the information:
- Cases citing the same precedents tend to involve similar legal issues
- Cases cited by the same later opinions are considered analogous by judges
- Dense citation clusters indicate related lines of jurisprudence

That's why the 7,215 external placeholder nodes matter — they're the **hubs** connecting our cases.

## Concrete Example: How Citation Patterns Predict Outcomes

```
Case X (unknown outcome — we want to predict)
  → cites Opinion 78j (Tellabs v. Makor — landmark scienter case)
  → cites Opinion 336 (Dura v. Broudo — landmark loss causation case)

Case A (PLAINTIFF_WINS) → also cites 78j and 336
Case B (PLAINTIFF_WINS) → also cites 78j and 336
Case C (DEFENDANT_WINS) → cites 78j but NOT 336
Case D (DEFENDANT_WINS) → cites neither
```

GraphSAGE aggregates Case X's 2-hop neighborhood:
```
Case X's neighbors (via shared citations):
  - Case A: PLT_WINS, shares 2 citations
  - Case B: PLT_WINS, shares 2 citations
  - Case C: DEF_WINS, shares 1 citation

Learns: "cases citing BOTH Tellabs AND Dura tend to be PLAINTIFF_WINS"
→ predicts Case X = PLAINTIFF_WINS
```

**The insight**: A judge citing both Tellabs (scienter standard) and Dura (loss causation standard) is analyzing both elements in depth — plaintiff's case survived initial screening. Cases only citing Tellabs but not Dura often dismissed on scienter alone (defendant wins before reaching loss causation).

## What GraphSAGE Would Capture That Our Current Pipeline Doesn't

| Current pipeline | What it misses |
|---|---|
| ANCO-HITS scores arguments individually | Doesn't see citation patterns between cases |
| SBERT finds semantically similar opinions | Two opinions can discuss different topics but cite the same authorities |
| Neo4j traversal finds 1-2 hop neighbors | Returns flat list — doesn't learn which citation PATTERNS predict outcomes |

GraphSAGE would learn **specific combinations of citations** predict outcomes — not just "27 citations" (a feature) but "cites these particular landmarks in this combination."

## Planned Input Features Per Case Node

| Feature | Dimensions | Source |
|---|---|---|
| SBERT embedding of opinion | 768 | Phase 5 embeddings |
| ANCO-HITS score | 1 | Phase 3 |
| Element satisfaction vector | 6 | Phase 1 (+1/0/-1 per element) |
| Citation in-degree | 1 | citation_count |
| Citation out-degree | 1 | Count of outgoing citations |
| Court one-hot | ~20 | court_id |
| Procedural stage one-hot | ~5 | MTD/SJ/TRIAL/APPEAL |
| Class action flag | 1 | idb_class_action |

## Why We Deferred It

**Not enough nodes.** The paper's benchmarks:

| Dataset | Nodes | Avg Degree | Result |
|---|---|---|---|
| Citation (paper) | 302,424 | 9.15 | Strong gains |
| Reddit (paper) | 232,965 | 492 | Strong gains |
| PPI (paper, smallest) | 2,373 | 28.8 | Moderate gains |
| **Our graph** | **416 cases** | varies | **Too small** |

Three specific problems at 416 cases:

1. **Training data**: 65 training cases across 3 classes. More parameters than examples → guaranteed overfitting.
2. **Neighborhood saturation**: 2-hop sampling reaches most of the graph for every node. No local structure to learn — everyone's neighborhood looks the same.
3. **Features already sufficient**: Logistic regression on node features (ANCO-HITS + element vector + citation count) would likely match or beat GraphSAGE at this scale.

## What We Did Instead

Skipped GraphSAGE. The pipeline achieves similar signals through:
- **ANCO-HITS scores** (Phase 3) — structural signal from argument graph
- **SBERT embeddings** (Phase 5) — semantic similarity
- **Neo4j Cypher traversal** (Phase 5) — direct citation neighborhood lookup

These cover the same ground without overfitting risk.

## When Would GraphSAGE Make Sense?

| Scale | Feasibility |
|---|---|
| 416 cases (current) | No — overfitting guaranteed |
| 3,400 cases with opinions | Maybe — borderline |
| 10,200 cases (full dataset) | **Yes** — ~1,000 training cases, meaningful citation patterns |

At 10K cases, the same landmark opinions (Tellabs, Dura, Basic v. Levinson) appear in hundreds of cases. Enough signal for GraphSAGE to learn which citation combinations predict outcomes.

## Elevator Pitch

"GraphSAGE is a graph neural network that learns from citation network structure — which cases cite which landmarks, and whether their citation neighborhoods predict outcomes. We deferred it because 416 cases is too small — the paper's smallest benchmark used 2,373 nodes. We achieve similar structural signals through ANCO-HITS scoring and Neo4j citation traversal. GraphSAGE becomes viable at the full 10,200-case dataset, where specific citation patterns (e.g., citing both Tellabs and Dura) would appear in enough cases for the model to learn meaningful combinations."
