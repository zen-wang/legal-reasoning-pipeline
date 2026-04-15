# Topic 7: Phase 2 — Knowledge Graph

## What Phase 2 Does

Takes all data from SQLite (cases, opinions, citations, IRAC extractions, parties, judges) and loads it into a **Neo4j graph database** — a property graph where nodes are entities and edges are relationships. This is the memory layer that connects everything for downstream retrieval and scoring.

## Why a Graph?

Relational databases (SQLite) store data in tables. Good for "give me case #12345." Bad for "find all cases that cite the same precedent as case #12345, in the same circuit, decided by the same judge."

Graph databases are built for traversal:
- "What cases are 2 hops away in the citation network?"
- "What arguments appear in both plaintiff-win and defendant-win cases?"
- "Trace the citation chain from this case back to Tellabs v. Makor"

These are exactly the queries Phase 5 (RAG) needs for retrieval.

## The Graph Schema

### Nodes (5 main types)

| Node | Key Property | Count | Source |
|---|---|---|---|
| Case | docket_id | 416 | cases table |
| Opinion | opinion_id | 7,701 (486 internal + 7,215 external) | opinions table + citation targets |
| LegalArgument | text_hash (SHA-256) | 370 | IRAC arguments_plaintiff/defendant |
| Judge | name_normalized | 264 | opinions.author_str |
| Statute | citation | 104 | IRAC statutes_cited |

Plus Company (from parties) and LawFirm (from attorneys) — sparse, low priority.

### Edges (5 main types)

| Edge | Direction | Count | Purpose |
|---|---|---|---|
| HAS_OPINION | Case → Opinion | 486 | Links case to its opinions |
| CITES | Opinion → Opinion | 13,530 | Citation network — the backbone |
| INVOLVES | Case → LegalArgument | 389 | **Signed edges for ANCO-HITS** |
| DECIDED_BY | Case → Judge | 326 | Who decided the case |
| CHARGED_UNDER | Case → Statute | 269 | Which statutes apply |

## The Citation Network

The most valuable part. 13,530 citation edges connecting 7,701 opinion nodes.

**Internal vs external**: We have full text for 486 opinions (internal). Those cite 7,215 other opinions we don't have text for (external — Supreme Court cases, other circuit rulings). We create **placeholder nodes** for external ones — just opinion_id and `internal: false`.

Without external placeholders: only 440 edges (3% of network). With them: all 13,530 edges preserved. This matters for graph traversal in Phase 5.

## Signed Edges (INVOLVES) — Critical for Phase 3

The INVOLVES edge connects a Case to a LegalArgument with a **sign** (+1, -1, or 0). The sign encodes whether the argument's side won:

| Outcome | Plaintiff argument | Defendant argument |
|---|---|---|
| PLAINTIFF_WINS | +1 (won) | -1 (lost) |
| DEFENDANT_WINS | -1 (lost) | +1 (won) |
| MIXED | 0 (neutral) | 0 (neutral) |

**Example**: Case A is DEFENDANT_WINS:
- Plaintiff argument "CEO sold shares during class period" → sign = -1 (plaintiff lost)
- Defendant argument "no strong inference of scienter" → sign = +1 (defendant won)

This signed bipartite structure (Cases ↔ Arguments with +1/-1) is what ANCO-HITS needs in Phase 3.

**Distribution**: +1: 149, -1: 143, 0: 97. Roughly balanced — healthy for ANCO-HITS.

## Argument Deduplication

389 raw argument strings → 370 unique after normalization (lowercase, strip whitespace, remove trailing punctuation) + SHA-256 hashing.

353/370 are singletons (connect to 1 case) — LLM generates case-specific phrasing. Semantic clustering (grouping similar arguments) deferred to future phase.

## Judge Handling

`opinions.author_str` (251 distinct) as primary source. `cases.assigned_to_str` (13 non-null) as fallback only for cases without opinion author. No fuzzy name merging.

## How the Graph Is Built (build_graph.py)

Loading order:
```
1. Constraints + indexes (idempotent, IF NOT EXISTS)
2. Nodes: Case → Opinion → Statute → Argument → Judge → Company → Firm
3. Edges: HAS_OPINION → CITES → CHARGED_UNDER → INVOLVES → DECIDED_BY → etc.
4. Verification queries + summary
```

All loaders use **MERGE + UNWIND** — idempotent (safe to re-run), batched (500 per batch).

## How Phase 5 Uses the Graph

4 Cypher queries per case:
1. **1-hop citations**: opinions directly cited by or citing this case
2. **2-hop citations**: transitive neighbors (A→B→C)
3. **Same statute**: cases charged under same statutes
4. **Same court**: cases in same court (lowest priority)

Combined with semantic search + ANCO-HITS in weighted fusion re-ranker.

## Code Files

| File | Role |
|---|---|
| `script/graph/__init__.py` | Package init |
| `script/graph/schema.py` | Node label constants, edge type constants, Cypher constraints + indexes |
| `script/graph/connect.py` | Neo4j driver, session context manager, ensure_constraints(), clear_graph() |
| `script/graph/resolve.py` | URL→opinion_id parsing, argument normalization + SHA-256, name/statute normalization, firm name extraction |
| `script/graph/load_nodes.py` | Node loaders — load_case_nodes(), load_opinion_nodes(), load_statute_nodes(), load_argument_nodes(), load_judge_nodes(), etc. |
| `script/graph/load_edges.py` | Edge loaders — load_citation_edges(), load_involves_edges() with _compute_sign(), load_decided_by_edges(), etc. |
| `script/build_graph.py` | CLI entry point — --clear, --verify, --dry-run |

## Key Properties on Nodes

### precedential_status (Opinion property)

Whether the opinion is "Published" or "Unpublished."
- **Published**: Goes into official law reporters. Binding or persuasive authority. Other courts must consider it.
- **Unpublished**: Judge issued a decision but not officially published. Some circuits don't allow citing unpublished opinions.
- For pipeline: published opinions are stronger evidence. Phase 5 could boost published opinions in ranking.

### citation_count (Opinion property)

How many other opinions cite this one.
- High = landmark/influential (Tellabs v. Makor has thousands)
- Low = routine decision
- Could be a ranking signal in Phase 5 (not currently used).

### idb_class_action (Case property)

Class action vs individual lawsuit. Nearly zero coverage on opinion-sourced cases.
- **Class action**: Reliance element uses "fraud-on-the-market" presumption (all investors presumed to have relied)
- **Individual**: Must prove direct reliance — actually read and believed the false statement. Much harder.
- Same element (reliance) has different sub-conditions depending on this flag.

### procedural_stage (Case property)

At which point in litigation the opinion was issued:

| Stage | What happens | What the opinion analyzes |
|---|---|---|
| **MTD** | Defendant says "even if everything is true, it's not enough" | Are elements **adequately pled**? (lower bar) |
| **SJ** | After evidence gathering, one side says "evidence is so clear, no trial needed" | Are elements **proven by evidence**? (higher bar) |
| **TRIAL** | Full trial | **Proven beyond preponderance of evidence**? |
| **APPEAL** | Higher court reviews lower court | Did lower court **apply the law correctly**? |

Same element can be SATISFIED at MTD but NOT_SATISFIED at SJ. Kept as property, not promoted to node — a property filter achieves the same as graph traversal for this field.

## Future Improvement: Promote court_id to Court Node

Currently court is a property on Case nodes: `(Case {court_id: "nysd"})`.

**Proposed for the 5,855-case rebuild:**
```
(Case)-[:FILED_IN]->(Court {court_id: "nysd", circuit: "ca2", name: "S.D.N.Y."})
```

~94 Court nodes with circuit as property. Benefits:

| Query | Current (property) | With Court node |
|---|---|---|
| Cases in same court | Works | Works |
| Cases in same circuit | Requires COURT_TO_CIRCUIT dict lookup | Direct traversal: Case→Court, filter by circuit |
| All cases in 2nd Circuit | Scan all cases, filter | Traverse from circuit value |
| Judge's court history | Can't express | Judge→DECIDED_BY→Case→FILED_IN→Court |

Also makes the binding authority constraint a graph query instead of a Python dict lookup.

**Decision**: Promote court_id to Court node in next graph rebuild. Minimal effort (~94 nodes), cleaner circuit-level queries.

## Elevator Pitch

"Phase 2 loads everything into a Neo4j knowledge graph — 416 cases, 7,700 opinions, 13,500 citation edges, 370 legal arguments with signed weights. The key structure is the signed bipartite graph connecting cases to arguments with +1/-1 edges based on who won — this feeds Phase 3's ANCO-HITS scoring. The citation network preserves 97% of links by creating placeholder nodes for external opinions. Phase 5 uses 4 Cypher traversals on this graph for retrieval."
