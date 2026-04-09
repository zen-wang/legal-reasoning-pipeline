# Neuro-Symbolic Legal Reasoning Pipeline — Private 10b-5
## Full Implementation Plan

---

## Context

The CIPS Lab is building a neuro-symbolic "thinking RAG" system that reasons about legal cases using interpretable symbolic patterns, not black-box prediction. The system must show WHY it reaches a conclusion through traceable, editable rules.

**Domain**: Private 10b-5 securities fraud litigation (investor sues company)
**Data source**: CourtListener API v4 only (48 fields, 6 endpoints, EDU account)
**Core rule**: Rule 10b-5 — 6 elements must ALL be satisfied for plaintiff to win
**Architecture**: Based on lab papers — Beyond the Black Box (lifting/lowering loop), NARRA-SCALE (ANCO-HITS bipartite scaling), Hybrids (human oversight)

---

## Phase 0: Data Preparation

### 0.1 Scraping from CourtListener

**Goal**: Build a dataset of ~6,000-10,000 Private 10b-5 cases with full opinions.

**Search queries**:
```python
# Dockets: Private 10b-5 (exclude SEC enforcement)
dockets: nature_of_suit=850 & q="10b-5" -"Securities and Exchange Commission"

# Opinions: 10b-5 with element analysis
opinions: q="10b-5" ("motion to dismiss" OR "summary judgment")
```

**Scraping order** (per case):
1. Search dockets → get `docket_id`
2. Fetch docket details (fields #1-15, #40-48 idb_data)
3. Fetch opinion clusters for each docket → get `cluster_id`
4. Fetch opinions (plain_text, opinions_cited[]) → fields #24-29
5. Fetch cluster metadata → fields #30-36
6. Fetch parties → fields #16-19
7. Fetch attorneys → fields #20-23
8. Fetch docket entries → fields #37-39

**Storage**: SQLite database with separate tables:
- `cases` (docket-level, 1 row per case)
- `opinions` (1+ per case, with `plain_text` and `opinions_cited`)
- `parties` (N per case)
- `attorneys` (N per case)
- `docket_entries` (N per case)
- `citation_edges` (extracted from `opinions_cited[]`, source→target pairs)

**Rate limiting**: 5,000 req/hour (CL limit). With 6 API calls per case, ~830 cases/hour. Full scrape of 10,000 cases ≈ 12 hours.

**Data fields**: 48 fields documented in `doc/CourtListener_Data_Fields_for_Pipeline.md`

### 0.2 Dataset Split

| Split | Count | Use | Criteria |
|-------|------:|-----|----------|
| **Labeled (closed cases)** | ~5,000-7,000 | Training + validation + test | `date_terminated` is not null AND has opinion with disposition |
| **Unlabeled (open cases)** | ~1,000-3,000 | Prediction targets | `date_terminated` is null OR no final disposition |

Labeled split:
- Train: 70%
- Validation: 15%
- Test: 15% (held out entirely until final evaluation)

### 0.3 Outcome Labels

Extract from FJC `idb_data.judgment` (#41) + `idb_data.disposition` (#40) + opinion text:

| Label | Definition | Source |
|-------|-----------|--------|
| `PLAINTIFF_WINS` | MTD denied + SJ denied (or favorable trial verdict) | FJC judgment = plaintiff, or opinion text says "denied" |
| `DEFENDANT_WINS` | MTD granted or SJ granted (case dismissed) | FJC judgment = defendant, or opinion text says "granted" |
| `MIXED` | MTD/SJ granted in part, denied in part | Opinion text: "granted in part" |
| `SETTLED` | Case terminated by settlement | FJC disposition = consent/settlement |

**For element-level labels** (more granular, extracted in Phase 1):

| Element Status | Meaning |
|----------------|---------|
| `SATISFIED` | Judge found element adequately pled/proven |
| `NOT_SATISFIED` | Judge found element failed |
| `CONTESTED` | Disputed but not yet resolved |
| `NOT_ANALYZED` | Judge didn't reach this element (dismissed on other grounds) |

---

## Phase 1: Symbolic Lifting (Text → Structured Patterns)

### 1.1 Goal

Read each opinion's `plain_text` and extract structured element-level assessments following the 10b-5 rule-based pattern.

### 1.2 The Rule-Based Pattern

**Top-level rule** (6 elements, ALL must be satisfied):
```
PlaintiffWins(case) ← MaterialMisrep(case) ∧ Scienter(case) ∧ Connection(case)
                     ∧ Reliance(case) ∧ EconomicLoss(case) ∧ LossCausation(case)
```

**Sub-rules per element**:
```
MaterialMisrep(case) ←
    FalseStatements(case) ∨
    MisleadingOmissions(case) ∨
    SchemeToDefraud(case)

Scienter(case) ←
    MotiveAndOpportunity(case) ∨
    ConsciousMisbehavior(case) ∨
    RecklessDisregard(case)

Connection(case) ←
    InConnectionWithPurchase(case) ∨
    InConnectionWithSale(case)

Reliance(case) ←
    FraudOnTheMarket(case) ∨
    DirectReliance(case) ∨
    AffiliateOmission(case)

EconomicLoss(case) ←
    ActualDamages(case) ∨
    DiminishedValue(case)

LossCausation(case) ←
    CorrectiveDisclosurePriceDrop(case) ∨
    MaterializationOfConcealedRisk(case)
```

### 1.3 Lifting Process (per opinion)

**Step 1: Preprocessing**
- Split opinion text into sections using heading detection (regex for "I.", "II.", "A.", "B.", "CONCLUSION", etc.)
- Identify the legal standard section (where judge states the McDonnell Douglas / 10b-5 framework)
- Identify per-element analysis sections

**Step 2: Entity & keyphrase extraction**
- spaCy `en_core_web_lg`: named entities (companies, people, courts)
- YAKE + RAKE: legal keyphrases ("material misrepresentation", "scienter", "fraud on the market")

**Step 3: Structured LLM prompting**
- Input: opinion text sections + extracted keyphrases
- Prompt instructs LLM to fill a strict schema:

```json
{
  "case_id": "docket_id",
  "procedural_stage": "MTD | SJ | TRIAL | APPEAL",
  "elements": {
    "material_misrepresentation": {
      "status": "SATISFIED | NOT_SATISFIED | CONTESTED | NOT_ANALYZED",
      "sub_conditions": ["FalseStatements", "MisleadingOmissions"],
      "key_facts": ["defendant stated revenue was $X when actual was $Y"],
      "judge_reasoning": "extracted quote from opinion"
    },
    "scienter": { ... },
    "connection": { ... },
    "reliance": { ... },
    "economic_loss": { ... },
    "loss_causation": { ... }
  },
  "outcome": "PLAINTIFF_WINS | DEFENDANT_WINS | MIXED",
  "statutes_cited": ["15 U.S.C. § 78j(b)", "17 C.F.R. § 240.10b-5"],
  "precedents_cited": ["Tellabs v. Makor", "Dura v. Broudo"],
  "arguments_plaintiff": ["defendant had motive and opportunity..."],
  "arguments_defendant": ["no strong inference of scienter..."]
}
```

**Step 4: Pydantic validation**
- Validate every LLM output against schema
- Check: all 6 elements present, status is valid enum, statutes match known vocabulary
- Flag failures for human review rather than passing through

**Step 5: Human validation (MVP benchmark)**
- Hand-label ~5 cases per outcome type with Emre (law student)
- Compare LLM extraction against human ground truth
- Fix prompt issues and rerun until >90% agreement

### 1.4 Output

For each case: a structured IRAC object stored in PostgreSQL JSONB:
- **Issue**: which elements are at stake
- **Rule**: statutes + landmark precedents (from `opinions_cited[]`)
- **Application**: element-by-element satisfaction status with supporting facts
- **Conclusion**: outcome

### 1.5 Tools

| Tool | Where | Purpose |
|------|-------|---------|
| spaCy `en_core_web_lg` | Gaudi 2 | Named entity extraction |
| YAKE + RAKE | Gaudi 2 | Legal keyphrase extraction |
| Llama 3.3 70B Instruct (BF16) | Gaudi 2 (vllm-fork, TP=8) | Structured lifting prompt |
| Outlines / xgrammar | Gaudi 2 | Enforce JSON schema at decode time |
| Pydantic v2 | Gaudi 2 | Post-decode schema validation |
| DuckDB | Sol / Local | Store structured case objects (replaces PostgreSQL) |

---

## Phase 2: Knowledge Graph Construction

### 2.1 Goal

Build a graph connecting cases, statutes, legal arguments, judges, and parties. This graph is the system's memory for retrieval and structural learning.

### 2.2 Nodes

| Node Type | Source | Count (est.) |
|-----------|--------|----------:|
| Case | 1 per docket_id | ~10,000 |
| Statute/Rule | Extracted from `cause` (#8) + opinion text (#24) | ~20-50 |
| Legal Argument | Extracted from `arguments_plaintiff` / `arguments_defendant` in Phase 1 | ~200-500 |
| Judge | `assigned_to_str` (#14) | ~500-1,000 |
| Law Firm | Attorney `firm` (#21) | ~500-1,000 |
| Company (defendant) | Party `name` (#16) | ~5,000-8,000 |

### 2.3 Edges

| Edge Type | Source | Signed? |
|-----------|--------|---------|
| Case → cites → Case | `opinions_cited[]` (#27) | No (direction only) |
| Case → charged_under → Statute | `cause` (#8) + opinion text | No |
| Case → involves → Argument | Phase 1 extraction | **Yes**: +1 if argument appears in plaintiff-win case, -1 if defendant-win |
| Case → decided_by → Judge | `assigned_to_str` (#14) | No |
| Case → defendant_is → Company | Party `name` (#16) | No |
| Case → represented_by → Firm | Attorney `firm` (#21) | No |

### 2.4 The Signed Edges (critical for ANCO-HITS)

For each legal argument extracted in Phase 1:
- Look at every case it appears in
- If that case: plaintiff wins → draw **+1 edge** (argument→case)
- If that case: defendant wins → draw **-1 edge** (argument→case)

Example:
```
"strong inference of scienter established" ←(+1)— Case_A (plaintiff won)
"strong inference of scienter established" ←(-1)— Case_B (defendant won on scienter)
"no motive and opportunity shown"          ←(-1)— Case_C (plaintiff lost on scienter)
"fraud on the market presumption applies"  ←(+1)— Case_D (plaintiff won on reliance)
```

### 2.5 Storage

KuzuDB embedded graph database (replaces Neo4j — no server needed, Sol-friendly). Key queries:
- "Find all cases citing Case X in the same circuit with the same charge"
- "Find all arguments that appear in defendant-win cases in SDNY"
- "Trace the citation chain from Case A to landmark precedent Tellabs"

KuzuDB supports Cypher, property graphs, and vector index. Single `.kuzu` directory, portable between Sol/Local/Gaudi 2.

### 2.6 Tools

| Tool | Where | Purpose |
|------|-------|---------|
| KuzuDB | Sol / Local | Graph storage + Cypher traversal + vector search |
| Python `kuzu` | Sol / Local | Load nodes and edges |

---

## Phase 3: ANCO-HITS Scaling

### 3.1 Goal

Score every legal argument and every case on a [-1, +1] scale:
- Score near **+1** = strongly predicts plaintiff wins
- Score near **-1** = strongly predicts defendant wins
- Score near **0** = contested, goes either way

### 3.2 Input

The signed bipartite graph from Phase 2:
- Left nodes: Cases (with known outcome labels)
- Right nodes: Legal arguments
- Edges: +1 (argument in plaintiff-win case) or -1 (argument in defendant-win case)

### 3.3 Algorithm

ANCO-HITS (from NARRA-SCALE paper, Gokalp et al. 2013):

```
1. Initialize: random scores for all cases and arguments
2. Update each argument's score = weighted sum of connected case scores × edge sign
3. Update each case's score = weighted sum of connected argument scores × edge sign
4. Normalize scores to [-1, +1]
5. Repeat steps 2-4 until convergence (typically 20-50 iterations)
```

### 3.4 Expected Output

| Score Range | Interpretation | Example Arguments |
|-------------|---------------|-------------------|
| +0.8 to +1.0 | Strongly predicts plaintiff wins | "Ponzi scheme established", "defendant sold stock during class period" |
| +0.3 to +0.7 | Moderately favors plaintiff | "corrective disclosure caused price drop" |
| -0.1 to +0.2 | Contested — cases go either way | "scienter inference is reasonable but not compelling" |
| -0.3 to -0.6 | Moderately favors defendant | "no motive shown", "puffery defense" |
| -0.7 to -1.0 | Strongly predicts defendant wins | "forward-looking statement with safe harbor", "no loss causation" |

### 3.5 Validation

- Plot ANCO-HITS case scores split by known outcome labels
- Plaintiff-win cases should cluster toward +1, defendant-win toward -1
- If they overlap heavily → argument extraction in Phase 1 needs improvement
- Target: AUC > 0.85 for separating plaintiff-win vs defendant-win using ANCO-HITS scores

### 3.6 Downstream Use

1. **Predict outcomes** for unlabeled cases: weighted average of their argument scores
2. **Feature for GraphSAGE** (Phase 4): each case node gets its ANCO-HITS score as input
3. **Ranking signal for RAG** (Phase 5): arguments with strong scores ranked higher in retrieval

---

## Phase 4: GraphSAGE (Structural Learning)

### 4.1 Goal

Learn to predict case outcomes from the **citation network structure** — not just what a case says, but where it sits in jurisprudence.

### 4.2 Why GraphSAGE (separate from symbolic patterns)

| Symbolic patterns tell you | GraphSAGE tells you |
|---|---|
| WHAT the case says (elements, arguments) | WHERE the case sits in the citation network |
| Which elements are satisfied | Whether similar cases in the neighborhood won or lost |
| Deterministic rule matching | Structural similarity that text can't capture |

Two cases can have similar language but opposite outcomes because one cites a landmark precedent. GraphSAGE captures this.

### 4.3 Input Features Per Case Node

| Feature | Dimension | Source |
|---------|----------|--------|
| Sentence-BERT embedding of opinion summary | 768 | Opinion `plain_text` (#24) |
| ANCO-HITS score | 1 | Phase 3 |
| Element satisfaction vector | 6 | Phase 1 (one per element: +1/0/-1) |
| Citation in-degree | 1 | `citation_count` (#31) |
| Citation out-degree | 1 | Count of `opinions_cited[]` (#27) |
| Court one-hot | ~20 | `court_id` (#7) |
| Procedural stage one-hot | ~5 | `idb_data.procedural_progress` (#42) |
| Class action flag | 1 | `idb_data.class_action` (#46) |
| Pro se flag | 1 | `idb_data.pro_se` (#45) |

### 4.4 Training

| Setting | Value |
|---------|-------|
| Model | GraphSAGE with 2-hop neighborhood aggregation |
| Training | 70% of labeled cases |
| Validation | 15% of labeled cases |
| Test | 15% held-out cases |
| Task | Predict: (a) outcome (plaintiff/defendant/mixed), (b) judgment type |
| Loss | Cross-entropy |

### 4.5 Important Note

GraphSAGE output is a **signal**, not a final answer. It ranks which precedents are most structurally relevant. The final answer always comes from Phase 6 IRAC reasoning.

### 4.6 Tools

| Tool | Purpose |
|------|---------|
| PyTorch Geometric | GraphSAGE implementation |
| sentence-transformers (`all-mpnet-base-v2`) | Case embedding |

### 4.7 Build Priority

**Build AFTER Phases 1-3 are working.** GraphSAGE adds value but is not required for the MVP. Symbolic patterns alone can make predictions.

---

## Phase 5: Constrained RAG (Retrieve & Lower)

### 5.1 Goal

When a user asks about a case, retrieve the most relevant real precedents and generate an answer constrained by symbolic rules. This is the "lowering" step — injecting symbolic knowledge back into the neural model.

### 5.2 Retrieval (3 methods combined)

| Method | What it finds | Tool |
|--------|--------------|------|
| **Semantic search** | Cases with similar text (embedding similarity) | Neo4j vector index on BERT embeddings |
| **Graph traversal** | Cases connected through citations, same statute, same judge | Neo4j Cypher queries |
| **Score-based ranking** | Re-rank by ANCO-HITS argument scores + GraphSAGE confidence | Custom ranker |

### 5.3 Hard Constraints (non-negotiable rules)

These run on EVERY output before returning to the user:

| Constraint | What it prevents | Implementation |
|-----------|-----------------|----------------|
| **Citation check** | Any cited case must exist in our dataset | Lookup against `docket_id` table |
| **Statute grounding** | Any statute cited must come from known vocabulary | Check against extracted statutes from Phase 1 |
| **Binding authority** | If retrieved cases are from a different circuit, flag explicitly | Compare `court_id` (#7) |
| **Temporal validity** | Don't cite overruled precedents | Check `date_filed` ordering + citation direction |
| **Ambiguity flag** | If argument ANCO-HITS scores are near zero, flag as "contested" | Threshold on Phase 3 scores |
| **Missing element flag** | If any element can't be assessed from retrieved cases, say so | Check Phase 1 element_status for NOT_ANALYZED |

### 5.4 The Lowering Step

The symbolic patterns from Phase 1 constrain the LLM's generation:
- LLM receives: retrieved precedents + element assessments + ANCO-HITS scores
- LLM must: follow IRAC structure, cite only real cases, flag uncertainty
- Symbolic rules act as **attention masks** — the LLM can only reason about elements and arguments that exist in the retrieved context

### 5.5 Tools

| Tool | Where | Purpose |
|------|-------|---------|
| LlamaIndex | Local | RAG orchestration |
| KuzuDB vector index | Local | Embedding search + graph traversal |
| Pydantic v2 | Local | Constraint enforcement on outputs |
| Llama 3.3 70B Instruct | Gaudi 2 (via HTTP/SSH tunnel) | Reasoning model |
| DuckDB cache | Local | Cache IRAC outputs for demo + re-evaluation |

---

## Phase 6: IRAC Output (Explainable Reasoning)

### 6.1 Goal

Generate a structured legal analysis that a lawyer can verify, following Issue → Rule → Application → Conclusion format. Every statement traces back to a real case and a real statute.

### 6.2 Output Structure

```
ISSUE:
  "Whether defendant's statements about revenue constituted material
   misrepresentation under Section 10(b) and Rule 10b-5."
  [Element at stake: MaterialMisrep, Scienter]

RULE:
  "Under Rule 10b-5, 17 C.F.R. § 240.10b-5, plaintiff must establish:
   (1) material misrepresentation [Cite: Basic v. Levinson, 485 U.S. 224],
   (2) scienter [Cite: Tellabs v. Makor, 551 U.S. 308], ..."
  [Source: Phase 1 rule extraction + opinions_cited[]]

APPLICATION:
  Element 1 - Material Misrepresentation: SATISFIED
    Facts: "Defendant stated Q3 revenue was $2.1B when actual was $1.4B"
    Pattern matched: FalseStatements
    Supporting case: Smith v. XYZ Corp (SDNY, 2022) — similar revenue overstatement
    [Source: Phase 1 fact extraction + Phase 5 retrieval]

  Element 2 - Scienter: CONTESTED (ANCO-HITS score: +0.15)
    Plaintiff argues: MotiveAndOpportunity — CEO sold $5M stock during class period
    Defendant argues: forward-looking statements protected by safe harbor
    Supporting case: Jones v. ABC Inc (2d Cir, 2021) — similar insider sales pattern
    [Source: Phase 3 ANCO-HITS + Phase 5 retrieval]

  ... [remaining elements] ...

CONCLUSION:
  Predicted outcome: MIXED (MTD likely granted in part)
  Confidence: 0.62 (moderate — scienter is contested)
  ANCO-HITS case score: +0.15
  GraphSAGE prediction: plaintiff-favorable (0.58)

UNCERTAINTY FLAGS:
  ⚠ Scienter element is contested (ANCO-HITS near zero)
  ⚠ Retrieved precedents are from 2d Circuit; query case is in 9th Circuit

GRAPH PATH:
  Query Case → cites → Jones v. ABC (2021) → cites → Tellabs v. Makor (2007)
  [Verifiable citation chain through Neo4j]
```

### 6.3 XAI (Explainable AI)

The explanation IS the symbolic pattern:
- "This case was classified as PLAINTIFF_WINS because **Pattern R17** matched: `FalseStatements ∧ MotiveAndOpportunity ∧ FraudOnTheMarket ∧ CorrectiveDisclosure`"
- A human can inspect, edit, and fix that pattern
- If the pattern is wrong → edit the rule in Phase 1 → re-run → no full retraining needed

This is the "programmable" in "Programmable AI" from Beyond the Black Box.

---

## Phase 7: Evaluation

### 7.1 Metrics

| Metric | How measured | Target |
|--------|------------|--------|
| **Citation accuracy** | Every cited case checked against dataset | 100% (zero invented) |
| **Rule extraction accuracy** | Extracted statute matches `cause` field + opinion text | >90% |
| **Element extraction accuracy** | LLM element assessment matches human label | >85% (validated with Emre) |
| **Outcome prediction** | Predicted outcome matches actual on held-out test set | >80% |
| **ANCO-HITS scaling validity** | High-score arguments correlate with plaintiff-win cases | AUC >0.85 |
| **Uncertainty calibration** | When system flags uncertainty, actual outcome is indeed ambiguous | Precision >0.75 |
| **Fact-to-element matching** | Application facts correctly describe case's allegations | >85% on human sample |

### 7.2 MVP Benchmark (First)

Before running automated evaluation:
1. Hand-label ~5 cases per outcome type with Emre (law student)
2. For each: material facts, governing rules, arguments both sides, outcome and why
3. Run pipeline on same cases, compare output against human labels
4. Fix prompt/pattern issues until >90% agreement

### 7.3 Evaluation Loop

```
Run pipeline on 500 test cases
  → Compute all metrics
  → Identify failures
  → Trace failures to source phase:
      Citation wrong?     → Fix Phase 5 constraints
      Element wrong?      → Fix Phase 1 lifting prompt
      Argument wrong?     → Fix Phase 3 ANCO-HITS input
      Outcome wrong?      → Fix Phase 1 patterns OR Phase 4 GraphSAGE
  → Re-run pipeline (incremental, no full retraining)
```

This is the **exception handling loop** from Beyond the Black Box (Algorithm 1, Step 5).

---

## Implementation Timeline

| Weeks | Phase | Deliverable |
|-------|-------|------------|
| 1-2 | **Phase 0: Data prep** | Scraper built. ~10,000 cases in SQLite. Dataset split done. |
| 3-4 | **Phase 1: Lifting** | Structured IRAC extraction for all cases. 20 human-validated. Pydantic schema enforced. |
| 5 | **Phase 2: Graph** | Neo4j loaded with all nodes/edges. Citation network from `opinions_cited[]`. Signed argument edges. |
| 6 | **Phase 3: ANCO-HITS** | Argument scores computed. Validation: case scores separate plaintiff-win vs defendant-win. |
| 7-8 | **Phase 4: GraphSAGE** | Model trained on labeled cases. Predictions for unlabeled cases. |
| 9-10 | **Phase 5: RAG** | Retrieval pipeline with all constraints. Zero-hallucination verified on test batch. |
| 11 | **Phase 6: Output** | End-to-end IRAC outputs generated. Emre reviews 20 samples. |
| 12+ | **Phase 7: Eval** | Full evaluation on held-out test set. Metrics computed. Feedback loop to earlier phases. |

---

## Technology Stack (Sol / Gaudi 2 / Local)

### LLM: Llama 3.3 70B Instruct (replaces Claude Sonnet 4.6)

- **Why**: Budget constraint. Open-source, Meta license (research OK), 128K context, strong instruction following.
- **Where**: Gaudi 2 (BF16, TP=8 across 8×96GB HBM, no quantization needed)
- **Serving**: vllm-fork (Habana) with guided decoding (outlines/xgrammar) to enforce Pydantic JSON schema at decode time
- **Hallucination risk**: Higher than Claude — Phase 5 hard constraints (citation check, statute grounding) become MORE critical. Pydantic validation + dataset lookup must catch all fabricated cases.
- **Cold start**: ~5-10 min for graph compilation on Gaudi 2. Use bucketing (2K/4K/8K/16K/32K) to minimize recompiles.

### Compute Strategy

| Task | Where | Why |
|------|-------|-----|
| Phase 0: Scraping (CourtListener API) | **Local machine** | Needs stable internet. Sol compute nodes may block outbound HTTP. One-time ~12h job. |
| Phase 1: Lifting (Llama 3.3 70B batch) | **Gaudi 2** | Heavy GPU compute. Batch 10K opinions. Almost-dedicated access. |
| Phase 1: Schema validation (Pydantic + spaCy) | **Gaudi 2** (same job) | Pipeline with LLM in single job. |
| Phase 2: Graph construction | **Sol CPU** or **local** | Loading nodes/edges, no GPU needed. |
| Phase 3: ANCO-HITS | **Local** or **Sol CPU** | Pure Python + scipy. ~10K nodes, minutes to converge. |
| Phase 4: GraphSAGE training | **Sol A100** | PyTorch Geometric ops optimized for CUDA. A100 queue OK (few hours). |
| Phase 4: Embedding generation | **Gaudi 2** or **local GPU** | One-time batch encode with sentence-transformers. |
| Phase 5/6: RAG + IRAC (interactive demo) | **Local** (frontend) + **Gaudi 2** (LLM endpoint) | Local FastAPI calls Gaudi 2 vLLM via SSH tunnel. |
| Phase 7: Evaluation | **Sol** or **local** | Batch scoring on pre-computed outputs. |

### Demo Strategy (Gaudi 2 cannot be always-on backend)

- **Development**: Manually start vLLM on Gaudi 2, run lifting/tests, shut down.
- **Demo for professor**: Pre-compute ~50-100 golden demo cases (full IRAC output cached in DuckDB). Interactive demo reads from cache. If professor asks about a new case, spawn Gaudi 2 job (~5 min warm-up).
- **Graceful degradation**: If LLM offline, system returns symbolic-only results (ANCO-HITS scores, element status, precedent list from KuzuDB) — no natural language IRAC but structured analysis still works. This reinforces the Beyond the Black Box thesis that symbolic layer is independent of LLM.

### Full Tool Table

| Tool | Layer | Where | Purpose |
|------|-------|-------|---------|
| CourtListener API v4 | Data | Local | Source for all 48 fields |
| SQLite | Storage | Local → Sol | Raw scraped data with checkpoint/resume |
| DuckDB | Storage | Sol / Local | Structured IRAC objects (replaces PostgreSQL — embedded, single file, native JSON, no server) |
| KuzuDB | Graph DB | Sol / Local | Knowledge graph + Cypher + vector search (replaces Neo4j — embedded, no server, Sol-friendly) |
| spaCy `en_core_web_lg` | NLP | Gaudi 2 / Sol | Named entity extraction |
| YAKE + RAKE | NLP | Gaudi 2 / Sol | Legal keyphrase extraction |
| sentence-transformers (`all-mpnet-base-v2`) | Embeddings | Gaudi 2 / Local | 768-dim BERT embeddings for cases |
| Llama 3.3 70B Instruct (BF16) | LLM | Gaudi 2 | Structured lifting (Phase 1) + IRAC generation (Phase 6) |
| vllm-fork (Habana) | Inference | Gaudi 2 | LLM serving with TP=8, guided decoding |
| Outlines / xgrammar | Decoding | Gaudi 2 | Enforce Pydantic JSON schema at decode time (first line of defense) |
| Pydantic v2 | Validation | Gaudi 2 / Sol | Post-decode schema validation (second line of defense) |
| PyTorch Geometric | Graph ML | Sol A100 | GraphSAGE implementation (Phase 4) |
| LlamaIndex | RAG | Local | Retrieval orchestration (Phase 5) |
| FastAPI | Serving | Local | Demo API endpoint (calls Gaudi 2 for LLM) |
| MLflow | Tracking | Sol (file backend) | Model versions + eval metrics (`mlflow.set_tracking_uri("file:./mlruns")`) |

### Key Replacements from Original Plan

| Original | Replaced With | Why |
|----------|--------------|-----|
| Anthropic API (Claude Sonnet 4.6) | Llama 3.3 70B on Gaudi 2 | Budget. Open-source. Dedicated GPU access. |
| PostgreSQL + JSONB | DuckDB | Sol can't run DB servers. DuckDB is embedded, single file, native JSON. |
| Neo4j 5.x | KuzuDB | Sol can't run DB servers. KuzuDB is embedded, supports Cypher + vector index. ~10K node scale fits perfectly. |

### Gaudi 2 Smoke Test (do before Phase 1)

Before investing in full pipeline, verify in ~half a day:
1. vllm-fork loads Llama 3.3 70B BF16 with TP=8 successfully
2. Guided decoding (outlines/xgrammar) works on Habana vLLM
3. Bucketing config (2K/4K/8K/16K/32K) avoids graph recompile storms
4. Gaudi 2 node can accept HTTP requests (for Phase 5/6 demo via SSH tunnel)

---

## Research Foundation

| Pipeline Component | Paper | What it proved |
|---|---|---|
| ANCO-HITS scaling (Phase 3) | NARRA-SCALE, ICTAI 2025 | 91% accuracy on political scaling with same algorithm |
| Lifting & lowering loop (Phases 1, 5) | Beyond the Black Box, IEEE TCSS 2026 | 72-82% rule coverage, 90% human agreement on pattern matching |
| Human oversight (Phase 7) | Hybrids, 2025 | Practical wisdom requires human review as structural requirement |
| GraphSAGE (Phase 4) | New addition | Not in papers — added for legal citation network structure |

---

## Key Files

| File | Contents |
|------|----------|
| `doc/CourtListener_Data_Fields_for_Pipeline.md` | 48 fields with endpoint, access tier, and why needed |
| `doc/SEC_EDGAR_vs_CourtListener_vs_IA_RECAP_data_field.md` | Cross-source field comparison (116 fields) |
| `CourtListener_API_Manual.md` | API v4 reference (auth, filtering, pagination, endpoints) |
| `professor-new-goal.md` | Professor's requirements and evaluation criteria |
| `script/config.py` | CourtListener API token + scraping config |
