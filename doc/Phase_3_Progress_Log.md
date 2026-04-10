# Phase 3 Progress Log — ANCO-HITS Argument Scoring

**Project**: Private 10b-5 Securities Fraud Analysis Pipeline
**Lab**: CIPS Lab, Arizona State University
**Date**: April 10, 2026

---

## Overview

Implemented the ANCO-HITS algorithm to score every legal argument and case on a [-1, +1] scale using the signed bipartite case-argument graph from Phase 2. The algorithm, adapted from the NARRA-SCALE paper (Gokalp et al., ICTAI), propagates outcome signals through the graph: arguments that appear in winning cases push toward +1, arguments in losing cases push toward -1.

**Key result**: 370 arguments and 121 cases scored in 2 iterations (0.001s), with AUC = 1.0 separating plaintiff-win vs defendant-win cases. Scores are extremal (+1/-1/0) due to sparse graph structure — expected at 128 extractions, will produce intermediate values at scale.

---

## Problem

Phase 2 produced a signed bipartite graph:
- 121 cases with IRAC extractions (85 DEF_WINS, 14 PLT_WINS, 29 MIXED)
- 370 unique legal arguments
- 389 signed edges (+1: 149, -1: 143, 0: 97)

The raw graph encodes which arguments appear in which cases and whether they won or lost. But it doesn't answer: **how predictive is each argument?** An argument appearing only in defendant-win cases is strongly defendant-favorable. An argument appearing in both plaintiff-win and defendant-win cases is contested. ANCO-HITS quantifies this.

---

## Approach

### 3.1 Algorithm: ANCO-HITS (Paper Equation 1)

Implemented the exact update formula from Gokalp et al. "Partisan Scale":

```
x_i^(k) = Σ_j (a_ij * y_j^(k-1)) / Σ_j |a_ij * y_j^(k-1)|    (case score)
y_j^(k) = Σ_i (a_ij * x_i^(k)) / Σ_i |a_ij * x_i^(k)|          (argument score)
```

**Per-entity normalization**: Each entity's score is divided by the sum of its own absolute contributions. This is critical — it prevents high-degree nodes from dominating the scale. A case connected to 10 arguments and one connected to 2 arguments are both scaled to [-1, +1].

**Departures from the paper**:
- Case scores seeded from outcomes (+1 PLT_WINS, -1 DEF_WINS, 0 MIXED) instead of all-ones. This fixes the sign orientation — the paper notes signs are arbitrary and requires post-hoc alignment between consecutive sessions.
- Argument scores initialized to 1.0 (matching the paper's default).

### 3.2 Data Loading

Two data sources implemented:
- **Neo4j (primary)**: Single Cypher query fetches all INVOLVES edges with signs
- **SQLite (fallback)**: Reads `irac_extractions`, recomputes signs for offline use

Both produce the same `BipartiteGraph` dataclass: a (121 × 370) dense numpy sign matrix with index mappings.

### 3.3 Signed Edge Convention

The sign encodes whether the argument's side **prevailed**:

| Case Outcome | Plaintiff Argument | Defendant Argument |
|-------------|-------------------|-------------------|
| PLAINTIFF_WINS | +1 (won) | -1 (lost) |
| DEFENDANT_WINS | -1 (lost) | +1 (won) |
| MIXED | 0 (neutral) | 0 (neutral) |

This convention was established in Phase 2's `_compute_sign()` and duplicated in Phase 3's `compute_sign()` to avoid cross-phase private function dependencies.

---

## Result

### Scores

| Metric | Value |
|--------|-------|
| **AUC (PLT vs DEF)** | **1.0000** |
| Iterations to converge | 2 |
| Wall time | 0.001s |
| Arguments scored | 370 |
| Cases scored | 121 |

### Score Distribution

| Range | Interpretation | Arguments |
|-------|---------------|----------|
| +0.5 to +1.0 | Strong plaintiff | 141 |
| +0.1 to +0.5 | Moderate plaintiff | 0 |
| -0.1 to +0.1 | Contested | 89 |
| -0.5 to -0.1 | Moderate defendant | 0 |
| -1.0 to -0.5 | Strong defendant | 140 |

### Case Scores by Outcome

| Outcome | Count | Mean Score | Std |
|---------|------:|-----------|-----|
| PLAINTIFF_WINS | 13 | +1.000 | 0.000 |
| DEFENDANT_WINS | 83 | -1.000 | 0.000 |
| MIXED | 25 | 0.000 | 0.000 |

### Sample Top Arguments (Plaintiff-Favorable, Score = +1.0)

- "Omega's failure to disclose the Loan was a material misrepresentation"
- "Morgan Stanley made material misstatements and omissions to conceal its exposure"
- "Defendants acted recklessly in choosing to disclose incomplete and misleading information"
- "Oppenheimer made material misrepresentations about ARS"
- "Ashland justifiably relied on Oppenheimer's representations"

### Sample Bottom Arguments (Defendant-Favorable, Score = -1.0)

- "Twitter's statements were not false or misleading"
- "Twitter had no duty to disclose more than it did under federal securities law"
- "Advance Auto argued that the allegations don't satisfy the scienter standards"
- "Defendant argued that SLUSA precludes Plaintiffs' claims"
- "The suits were frivolous and only served to enrich the plaintiffs' lawyers"

### Why All Scores Are Extremal

All scores are exactly +1.0, -1.0, or 0.0 — no intermediate values. This is mathematically expected:

- **353 of 370 arguments are singletons** (connected to exactly 1 case). Per-entity normalization on a singleton: `raw / |raw| = ±1.0` always.
- **17 shared arguments** connect to 2-3 cases but all have uniform signs (no conflicting signals).
- **AUC = 1.0 is trivial** — seeded outcomes flow through singletons and back unchanged.

This is correct behavior for 128 extractions. At 10,000 cases with semantic argument deduplication (merging "no scienter shown" with "scienter not adequately pled"), shared arguments will have mixed signals and produce genuinely intermediate scores.

---

## Data Quality Notes

1. **Multi-label docket** (docket 70353466, Lee v. McDowell): Has 3 opinions with conflicting IRAC outcomes (MIXED, DEF_WINS, PLT_WINS). The bipartite builder picks one non-deterministically. Low impact — 1 case.

2. **Criminal case contamination**: A few arguments from contaminated cases (e.g., "The district court erred in increasing his offense by two levels") appear in the scores. The bipartite builder filters on `is_valid = 1` but not `contamination_type`. Could add a contamination filter — low priority since only a few arguments affected.

3. **MIXED cases**: 25 MIXED cases seed at 0.0 and their arguments receive 0.0 scores. These 89 "contested" arguments are genuinely ambiguous — the judge ruled partially for each side.

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Per-entity normalization (Eq. 1) | Faithful to the paper. Prevents high-degree node dominance. Critical difference from global L-infinity. |
| Outcome-seeded initialization | Fixes sign orientation without post-hoc alignment. Departure from paper's all-ones init. |
| Dual write (Neo4j + SQLite) | Neo4j enables Cypher queries in Phase 5 RAG. SQLite is source of truth that survives Neo4j reloads. |
| Pure numpy core | No I/O in algorithm — fully unit-testable. Entire 121×370 matrix fits in memory. |
| Neo4j primary, SQLite fallback | Graph data already in Neo4j from Phase 2. SQLite fallback for offline/headless use. |
| Duplicate compute_sign() | Avoids importing a private `_compute_sign()` from Phase 2. 5-line function, cleaner than cross-phase coupling. |

---

## Technical Stack

| Component | Technology |
|-----------|-----------|
| Algorithm | ANCO-HITS (Gokalp et al., ICTAI) |
| Core computation | NumPy (dense matrix operations) |
| Validation | scikit-learn (ROC AUC) |
| Plots | Matplotlib (Agg backend for headless) |
| Data sources | Neo4j (primary) + SQLite (fallback) |
| Persistence | Neo4j node properties + SQLite `anco_hits_scores` table |

## Repository Structure (Phase 3)

```
script/
├── score_arguments.py          # Phase 3 CLI: --db, --source, --dry-run, --no-plot
└── scoring/
    ├── __init__.py
    ├── bipartite.py            # BipartiteGraph dataclass, Neo4j/SQLite loaders
    ├── anco_hits.py            # Core algorithm — pure numpy, per-entity normalization
    ├── validate.py             # AUC, score summaries, matplotlib plots
    └── write_scores.py         # Dual write to Neo4j + SQLite
data/
├── anco_hits_case_scores.png           # Case score histogram by outcome
├── anco_hits_argument_distribution.png # Argument score distribution
└── anco_hits_convergence.png           # Convergence curve (2 iterations)
```

## CLI Usage

```bash
# Full run (Neo4j + SQLite + plots)
python -m script.score_arguments --db data/private_10b5_sample_416.db

# SQLite only (no Neo4j needed)
python -m script.score_arguments --db data/private_10b5_sample_416.db --source sqlite

# Dry run (bipartite stats only)
python -m script.score_arguments --db data/private_10b5_sample_416.db --dry-run

# Skip plots (headless server)
python -m script.score_arguments --db data/private_10b5_sample_416.db --no-plot
```

---

## Next Steps

- **Phase 4**: GraphSAGE — structural predictions from citation network. ANCO-HITS case scores become an input feature per node.
- **Semantic argument deduplication**: Cluster similar arguments (e.g., "no scienter" / "scienter not adequately pled") to create more shared nodes with mixed signals → intermediate ANCO-HITS scores.
- **Scale to 10K cases**: Re-run ANCO-HITS after scraping completes. Expect intermediate scores, lower AUC (harder separation), and more informative argument rankings.
