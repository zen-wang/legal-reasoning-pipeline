# Phase 2 Progress Log — Knowledge Graph Construction

**Project**: Private 10b-5 Securities Fraud Analysis Pipeline
**Lab**: CIPS Lab, Arizona State University
**Date**: April 10, 2026

---

## Overview

Built a legal knowledge graph in Neo4j connecting 416 securities fraud cases through citation networks, legal arguments, judges, statutes, and parties. The graph's critical feature is **signed bipartite edges** between cases and legal arguments — each edge carries a +1/-1 weight indicating whether the argument's side prevailed, enabling the ANCO-HITS argument scoring algorithm in Phase 3.

**Key result**: 9,005 nodes and 15,149 edges loaded in 2.6 seconds, including 13,530 citation edges spanning 7,701 opinions and 389 signed argument edges across 121 cases with IRAC extractions.

---

## Problem

Phase 1 produced 128 structured IRAC extractions containing element-level legal analysis, legal arguments from both sides, statutes cited, and case outcomes. This data exists as isolated JSON records in SQLite. To enable:
- **ANCO-HITS** (Phase 3): Need a signed bipartite graph of arguments ↔ cases
- **GraphSAGE** (Phase 4): Need citation network neighborhoods for structural learning
- **Constrained RAG** (Phase 5): Need graph traversal for precedent retrieval

...all this data must be connected in a queryable graph structure.

---

## Approach

### 2.1 Graph Schema Design

Designed a 7-node, 7-edge property graph schema mapping the legal domain:

**Node types**:

| Label | Key Property | Source | Count |
|-------|-------------|--------|------:|
| Case | docket_id | CourtListener scraper | 416 |
| Opinion | opinion_id | Opinions table + external placeholders | 7,701 |
| Statute | citation (normalized) | cases.cause + IRAC statutes_cited | 104 |
| LegalArgument | text_hash (SHA-256) | IRAC arguments_plaintiff/defendant | 370 |
| Judge | name_normalized | opinions.author_str (primary) | 264 |
| Company | name_normalized | Parties with defendant type | 84 |
| LawFirm | name_normalized | Attorney contact blocks (regex) | 60 |

**Edge types**:

| Relationship | Count | Purpose |
|-------------|------:|---------|
| CITES (Opinion→Opinion) | 13,530 | Citation network for GraphSAGE + RAG traversal |
| HAS_OPINION (Case→Opinion) | 486 | Links cases to their judicial opinions |
| INVOLVES (Case→LegalArgument) | 389 | **Signed edges for ANCO-HITS** (+1/-1/0) |
| DECIDED_BY (Case→Judge) | 326 | Judicial decision patterns |
| CHARGED_UNDER (Case→Statute) | 269 | Statutory basis for each case |
| DEFENDANT_IS (Case→Company) | 85 | Corporate defendant identification |
| REPRESENTED_BY (Case→LawFirm) | 64 | Legal representation network |

### 2.2 Signed Edge Design (Critical for ANCO-HITS)

The `INVOLVES` edges carry signed weights that encode whether an argument's side **prevailed** in each case:

| Case Outcome | Plaintiff Argument | Defendant Argument |
|-------------|-------------------|-------------------|
| PLAINTIFF_WINS | +1 (won) | -1 (lost) |
| DEFENDANT_WINS | -1 (lost) | +1 (won) |
| MIXED | 0 (neutral) | 0 (neutral) |

**Sign distribution**: +1: 149, -1: 143, 0: 97 — roughly balanced, healthy for ANCO-HITS convergence.

**Example**:
```
"Defendant had motive and opportunity to commit fraud"  (plaintiff arg)
  ← sign: -1 from Case 6135547 (DEFENDANT_WINS — argument lost)
  ← sign: +1 from Case 73314   (PLAINTIFF_WINS — argument won)

"Forward-looking statements protected by safe harbor"    (defendant arg)
  ← sign: +1 from Case 6135547 (DEFENDANT_WINS — argument won)
  ← sign: -1 from Case 45952   (PLAINTIFF_WINS — argument lost)
```

### 2.3 Citation Network Resolution

Citation edges in the source data use CourtListener URLs, not opinion IDs:
```
source_opinion_id: 2421813
cited_opinion_url: "https://www.courtlistener.com/api/rest/v4/opinions/109009/"
```

**Resolution strategy**:
- Regex extraction: parse opinion_id from URL (`courtlistener.com/api/rest/v\d+/opinions/(\d+)`)
- Internal check: 486 opinions in our dataset, 7,215 external (landmark precedents like *Tellabs*, *Dura*, *Basic v. Levinson*)
- External opinions stored as placeholder nodes with `internal: false` — preserves the full citation topology (97% of citations are to external precedents)

### 2.4 Argument Deduplication

389 raw argument strings from 128 IRAC extractions → 370 unique arguments after deduplication.

**Strategy**: Exact-match-after-normalization
1. Lowercase, collapse whitespace, strip trailing punctuation
2. SHA-256 hash of normalized text → `text_hash` property
3. Two cases raising the same argument (e.g., "no strong inference of scienter under PSLRA") merge to one LegalArgument node with edges from both cases

Semantic deduplication (embedding similarity) deferred to Phase 3 — exact match is sufficient for initial ANCO-HITS since the algorithm converges based on edge structure, not node count.

### 2.5 Judge Resolution

**Challenge**: Two data sources with different formats:
- `opinions.author_str`: ~251 distinct values (last names: "Barbadoro", "Posner")
- `cases.assigned_to_str`: 13 non-null values (full names: "Paul J. Barbadoro")
- 403/416 cases have NULL `assigned_to_str`

**Strategy**: `author_str` as primary source (covers most cases through their opinions). `assigned_to_str` used only as fallback for cases with no opinion author. No fuzzy matching — avoids false merges between judges with similar names.

### 2.6 Data Loading Architecture

All loaders use Neo4j `MERGE` for idempotency (safe to re-run) with `UNWIND` batching (500 rows per batch) for performance:

```
SQLite (source of truth)
  → Read tables with row_factory
  → Normalize/deduplicate in Python
  → Batch into UNWIND parameter lists
  → MERGE nodes/edges in Neo4j (idempotent)
  → Verify with Cypher count queries
```

**Loading order** (respects node dependencies for edge creation):
1. Constraints + indexes (7 uniqueness constraints, 7 indexes)
2. Case → Opinion → Statute → LegalArgument → Judge → Company → LawFirm nodes
3. HAS_OPINION → CITES → CHARGED_UNDER → INVOLVES → DECIDED_BY → DEFENDANT_IS → REPRESENTED_BY edges
4. Automated verification queries

---

## Result

### Graph Statistics

| Metric | Count |
|--------|------:|
| **Total nodes** | **9,005** |
| Case nodes | 416 |
| Opinion nodes (internal) | 486 |
| Opinion nodes (external) | 7,215 |
| LegalArgument nodes | 370 |
| Judge nodes | 264 |
| Statute nodes | 104 |
| Company nodes | 84 |
| LawFirm nodes | 60 |
| **Total edges** | **15,149** |
| CITES | 13,530 |
| HAS_OPINION | 486 |
| INVOLVES (signed) | 389 |
| DECIDED_BY | 326 |
| CHARGED_UNDER | 269 |
| DEFENDANT_IS | 85 |
| REPRESENTED_BY | 64 |

### ANCO-HITS Readiness

| Metric | Value |
|--------|------:|
| Cases with arguments | 121 |
| Unique arguments | 370 |
| Signed edges | 389 |
| Sign +1 (won) | 149 |
| Sign -1 (lost) | 143 |
| Sign 0 (neutral) | 97 |

### Performance

| Metric | Value |
|--------|-------|
| Total load time | 2.6 seconds |
| Neo4j version | 5.x Community (Docker) |
| Batch size | 500 rows per UNWIND |
| Idempotent | Yes (MERGE-based, safe to re-run) |
| Orphan nodes | 15 (external opinions with no outgoing citations) |

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Include all 416 cases, not just 128 with IRAC | GraphSAGE (Phase 4) needs the full citation network. `has_irac` property lets Phase 3 filter. |
| Opinion as separate node from Case | Citations are opinion-to-opinion. One case can have multiple opinions (73 cases have 2+). |
| External citation placeholders | Dropping external opinions would lose 97% of citations. Landmark precedents appear repeatedly. |
| Exact-match argument dedup (not semantic) | Sufficient for initial ANCO-HITS. Avoids premature threshold decisions. Semantic clustering can be added in Phase 3. |
| `author_str` as primary judge source | Covers most cases. `assigned_to_str` is NULL for 97% of cases. No fuzzy matching avoids false merges. |
| Signed edges encode "side prevailed" | Aligns with ANCO-HITS paper (NARRA-SCALE, Gokalp et al. 2013). Sign = +1 when argument's side won. |

---

## Technical Stack

| Component | Technology |
|-----------|-----------|
| Graph database | Neo4j 5.x Community Edition (Docker) |
| Driver | Python `neo4j` 6.1 |
| Data source | SQLite (Phase 0+1 output) |
| Deduplication | SHA-256 hashing after text normalization |
| Name resolution | Regex-based (CourtListener URLs, firm names, judge titles) |
| Environment | python-dotenv for Neo4j credentials |
| Idempotency | MERGE-based node/edge creation with uniqueness constraints |
| Batching | UNWIND with 500-row batches |

## Repository Structure (Phase 2)

```
script/
├── build_graph.py              # Phase 2 CLI: --db, --clear, --verify, --dry-run
└── graph/
    ├── __init__.py
    ├── schema.py               # Node labels, edge types, Cypher constraints/indexes
    ├── connect.py              # Neo4j driver, session context manager, schema setup
    ├── resolve.py              # URL parsing, argument dedup, name/statute normalization
    ├── load_nodes.py           # 7 node loaders (Case, Opinion, Statute, Argument, Judge, Company, Firm)
    └── load_edges.py           # 7 edge loaders (CITES, INVOLVES with signed weights, etc.)
```

## CLI Usage

```bash
# Full load (2.6s)
python -m script.build_graph --db data/private_10b5_sample_416.db

# Wipe and reload
python -m script.build_graph --db data/private_10b5_sample_416.db --clear

# Verify graph integrity
python -m script.build_graph --db data/private_10b5_sample_416.db --verify

# Dry run (print SQLite stats, no Neo4j needed)
python -m script.build_graph --db data/private_10b5_sample_416.db --dry-run
```

---

## Next Steps

- **Phase 3**: ANCO-HITS — run the signed bipartite graph algorithm to score every argument and case on [-1, +1]
- **Phase 4**: GraphSAGE — structural predictions from the citation network (7,701 opinion nodes, 13,530 citation edges)
- **Phase 5**: Constrained RAG — retrieve precedents via graph traversal with hard constraints (citation check, statute grounding, binding authority)
