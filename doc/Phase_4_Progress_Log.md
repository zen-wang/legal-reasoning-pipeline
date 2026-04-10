# Phase 4 Progress Log — GraphSAGE (Deferred)

**Project**: Private 10b-5 Securities Fraud Analysis Pipeline
**Lab**: CIPS Lab, Arizona State University
**Date**: April 10, 2026
**Status**: Deferred — insufficient graph scale for meaningful structural learning

---

## Overview

Phase 4 planned to train a GraphSAGE model (Hamilton et al., NeurIPS 2017) on the citation network to predict case outcomes from graph structure. After reviewing the paper's experimental requirements against our current data, we determined that GraphSAGE is not viable at our current scale and deferred this phase until the full dataset is scraped.

---

## Why GraphSAGE Was Planned

The pipeline plan (Section 4.2) identified a gap that symbolic patterns alone cannot fill:

| Symbolic patterns (Phase 1-3) tell you | GraphSAGE would tell you |
|----------------------------------------|--------------------------|
| WHAT the case says (elements, arguments) | WHERE the case sits in the citation network |
| Which elements are satisfied | Whether similar cases in the neighborhood won or lost |
| Deterministic rule matching | Structural similarity that text can't capture |

Two cases can have similar language but opposite outcomes because one cites a landmark precedent that changes the legal standard. GraphSAGE was intended to capture this citation-structural signal.

---

## Why We Deferred

### Paper Benchmark Scales vs Our Data

The GraphSAGE paper (Hamilton et al., "Inductive Representation Learning on Large Graphs", NeurIPS 2017) evaluated on three datasets:

| Dataset | Nodes | Avg Degree | Labels |
|---------|------:|-----------|--------|
| Citation (Web of Science) | 302,424 | 9.15 | 6 classes |
| Reddit | 232,965 | 492 | 50 classes |
| PPI (smallest benchmark) | 2,373 | 28.8 | 121 labels |

Our graph:

| Metric | Value |
|--------|------:|
| Case nodes (training targets) | 416 |
| Labeled cases (with outcomes) | 93 |
| Training split | 65 |
| Validation split | 14 |
| Test split | 14 |
| Avg case degree | 2.4 |
| Outcome classes | 3 (PLT_WINS, DEF_WINS, MIXED) |

Even the paper's smallest benchmark (PPI) has **36× more nodes** and **12× higher degree** than our graph.

### Three Specific Problems

**1. Training data too small for weight matrix learning**

GraphSAGE learns weight matrices W^k at each aggregation depth through SGD. With 65 training cases across 3 classes (~22 per class, heavily imbalanced: 46 DEF, 12 PLT, 7 MIXED), the model will overfit immediately. The paper's PPI experiment used 2,373 nodes per graph and that was already considered small-scale.

**2. Neighborhood sampling collapses the graph**

GraphSAGE samples S1=25 first-hop neighbors and S2=10 second-hop neighbors per node. Our cases connect to ~2-3 opinions (HAS_OPINION), and each opinion cites ~27 others (CITES). A 2-hop neighborhood from any case covers most of the 486 internal opinions — there is no meaningful local structure to learn because every node effectively sees the same global neighborhood.

**3. Raw features already saturate the signal**

The paper's Table 1 shows that "Raw features" baseline achieves 0.575 F1 on citation data, and GraphSAGE improves this by 39-63% by learning from structural neighborhoods. At our scale, a simple classifier (logistic regression or random forest) on the node features — ANCO-HITS score, 6-element satisfaction vector, citation in/out degree, court one-hot — will likely match or beat GraphSAGE because there isn't enough structural diversity for the GNN to learn beyond what the features already encode.

---

## What We Do Instead

The pipeline continues without GraphSAGE. The symbolic pipeline is self-sufficient:

1. **Phase 1** (IRAC extraction) provides element-level analysis per case
2. **Phase 3** (ANCO-HITS) provides argument and case scores on [-1, +1]
3. **Phase 5** (Constrained RAG) uses graph traversal for precedent retrieval — this uses the citation network directly via Cypher queries, not learned embeddings

The pipeline plan (Section 4.7) anticipated this: *"Build AFTER Phases 1-3 are working. GraphSAGE adds value but is not required for the MVP. Symbolic patterns alone can make predictions."*

---

## When to Revisit

GraphSAGE becomes viable when:

| Condition | Current | Target |
|-----------|---------|--------|
| Total cases | 416 | ~3,400 (opinion cases from scraper) |
| Labeled outcomes | 93 | ~1,000+ |
| Training cases | 65 | ~700+ |
| Avg case degree | 2.4 | Higher with more internal citations |

The scraper is still running toward ~3,400 opinion cases. Once complete:
1. Re-run Phase 1 (IRAC extraction) on new cases
2. Re-run Phase 2 (graph construction) with expanded dataset
3. Re-run Phase 3 (ANCO-HITS) on larger bipartite graph
4. **Then** train GraphSAGE with sufficient data

Additionally, sentence-transformer embeddings of opinion text (planned as a GraphSAGE input feature) are still useful for Phase 5 RAG semantic search regardless of whether GraphSAGE runs. These can be generated independently.

---

## Technical Reference

- **Paper**: Hamilton, Ying, Leskovec. "Inductive Representation Learning on Large Graphs." NeurIPS 2017. (`Project-Background/Inductive Representation Learning on Large Graphs.pdf`)
- **Pipeline plan**: Section 4.1-4.7 of `doc/Pipeline_Plan_Private_10b5.md`
- **Planned input features**: SBERT embedding (768d), ANCO-HITS score (1d), element vector (6d), citation in/out degree (2d), court one-hot (~20d), procedural stage one-hot (~5d)
- **Planned framework**: PyTorch Geometric on Sol A100 partition
