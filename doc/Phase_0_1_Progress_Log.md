# Phase 0 & 1 Progress Log — Neuro-Symbolic Legal Reasoning Pipeline

**Project**: Private 10b-5 Securities Fraud Analysis Pipeline
**Lab**: CIPS Lab, Arizona State University
**Date**: April 9-10, 2026

---

## Overview

Built an end-to-end neuro-symbolic pipeline that extracts structured legal reasoning from judicial opinions using Llama 3.3 70B on Intel Gaudi 2 accelerators. The system reads raw court opinions and produces machine-readable IRAC (Issue-Rule-Application-Conclusion) analyses following the 6-element Rule 10b-5 framework.

**Key result**: 128 structured extractions from 416 scraped cases, with 87% containing real element-by-element legal analysis, processed at 6.1 cases/min using 12-way concurrent inference on 8x Gaudi 2 HL-225 (768 GB HBM).

---

## Phase 0: Data Preparation

### 0.1 Data Collection (CourtListener API v4)

**Problem**: Need a large corpus of Private 10b-5 securities fraud opinions with full judicial analysis text, citation networks, and party metadata.

**Approach**:
- Built an async Python scraper (`scraper_private_10b5.py`) against the CourtListener REST API v4 using an EDU-tier account
- Two-pass strategy: opinion-bearing cases first (richest data), then metadata-only cases
- Crash-safe design: SQLite checkpointing with resume-from-failure, re-scrapes partial cases after disconnection
- Rate limiting: 5,000 req/hr with 1.5s intervals, 60s cooldown on HTTP 429
- Added discovery caching (`discovered_dockets` table) to avoid re-paginating ~170 API pages on restart

**Result**:
- 416 cases scraped (ongoing toward ~3,400 opinion cases + ~6,800 metadata-only)
- 486 opinions, 149 with full text (avg 50K chars)
- 13,530 citation edges (avg 26.7 per opinion)
- 196 parties, 192 attorneys, 1,507 docket entries
- Stored in SQLite: 6 tables + citation edge graph

**Data quality finding**: 13% contamination discovered — 22 SEC enforcement ("SEC v."), 29 DOJ criminal ("United States v."), 2 SEC appeal cases mixed into the Private 10b-5 dataset due to broad search queries.

### 0.2 Dataset Split + 0.3 Outcome Labeling

**Problem**: The pipeline plan assumed metadata-based labeling (FJC `idb_judgment`, `date_terminated`, `disposition` fields), but opinion-sourced cases have near-zero metadata coverage (~1% `idb_judgment`, 0% `disposition`). Labels must come from the opinion text itself.

**Approach** (`label_and_split.py`):
- Two-pass regex extraction on opinion text: scan last 2,000 chars (conclusion) first, then full text as fallback
- 14 regex patterns for outcome classification (DEFENDANT_WINS, PLAINTIFF_WINS, MIXED, UNCLEAR)
- AFFIRMED disambiguation: when standalone "AFFIRMED" appears, check full text for what the lower court did (MTD granted vs denied) to determine direction
- Contamination detection: regex on `case_name` to flag SEC/DOJ/SEC-appeal cases
- Stratified train/val/test split (70/15/15) on private + labeled + confidence >= 0.5 cases

**Result**:
- 93 cases labeled with clear outcomes (67 DEF_WINS, 18 PLT_WINS, 8 MIXED)
- 34 UNCLEAR (text present but no clear disposition signal)
- 53 contaminated cases flagged and excluded
- Train: 65, Val: 14, Test: 14 (stratified by outcome)
- New `case_labels` table added to SQLite (re-runnable, does not modify original tables)

**Technical decisions**:
- Stored labels in a separate `case_labels` table — never modify source data
- Included `matched_pattern` and `matched_text` fields for debuggability
- Script is idempotent: DROP + CREATE on each run

---

## Phase 1: Symbolic Lifting (Text → Structured Patterns)

### 1.1 IRAC Extraction Schema + 1.2 Rule-Based Pattern

**Problem**: Define the structured output format for extracting legal reasoning from opinion text, based on the Private 10b-5 6-element conjunctive rule.

**Approach** (`script/lifting/` package):
- **schema.py**: Pydantic v2 models for IRAC extraction — `IRACExtraction` with 6 `ElementAnalysis` objects, each containing `status` (SATISFIED/NOT_SATISFIED/CONTESTED/NOT_ANALYZED), `sub_conditions`, `key_facts`, `judge_reasoning`
- **rules.py**: 6-element conjunctive rule with 14 disjunctive sub-conditions. `evaluate_outcome()` implements the legal standard: any NOT_SATISFIED → DEFENDANT_WINS, all SATISFIED → PLAINTIFF_WINS, otherwise MIXED
- **preprocess.py**: Coarse section splitter — strips court header boilerplate (~500-2K chars) using heading pattern detection (ALL-CAPS, Roman numerals, lettered subsections). For appellate opinions (88% of dataset), keeps full body as analysis since element discussion is woven into narrative prose
- **store.py**: SQLite storage for extraction results — `irac_extractions` table with JSON blob, LLM model info, validation status, and error messages

**Design decisions**:
- `confidence` field (0.0-1.0) included in schema but defaults to 0.0 — computed post-hoc from proxy signals after seeing real LLM outputs, NOT filled by the LLM (LLMs are unreliable at self-assessing confidence)
- `case_id`, `opinion_id`, `procedural_stage` stripped from the LLM prompt schema — injected post-hoc by the pipeline to prevent hallucinated values
- Sub-condition validation: `validate_extraction_rules()` checks that LLM-provided sub-conditions are valid for each element (e.g., "FraudOnTheMarket" is valid for reliance but not for scienter)

### 1.3 LLM Extraction Pipeline

**Problem**: Extract structured IRAC analyses from 128+ judicial opinions using Llama 3.3 70B Instruct on Intel Gaudi 2.

**Architecture**:
```
Opinion text (SQLite)
  → Preprocess (strip header, ~5-15% size reduction)
  → Dynamic token budgeting (fit within 8192 context)
  → Truncation if needed (keep 60% beginning + 40% end)
  → Build prompt (system + user with schema + sub-condition list)
  → HTTP POST to vLLM OpenAI-compatible API
  → JSON extraction with fallbacks (strip markdown fences, regex {…} block)
  → Pydantic validation (schema enforcement)
  → Sub-condition validation (rule checking)
  → Store in SQLite (valid/invalid + raw LLM response for debugging)
```

**Infrastructure**:
- **LLM**: Llama 3.3 70B Instruct (BF16) on 8x Intel Gaudi 2 HL-225 (768 GB total HBM)
- **Serving**: vLLM (Habana fork) with tensor parallelism across 8 cards, `--max-model-len 8192`, `--max-num-seqs 16`
- **Client**: Raw HTTP via `requests` library — zero extra dependencies, OpenAI-compatible API
- **Cluster**: ASU Sol HPC — SLURM scheduler, `--partition=gaudi`, `--gres=gpu:hl225:8`
- **Concurrency**: ThreadPoolExecutor with per-thread SQLite connections, tested up to 20 concurrent LLM requests

**Challenges solved during production testing**:

| Problem | Root Cause | Fix |
|---------|-----------|-----|
| vLLM warmup crash (`AssertionError: req_index < max_num_reqs`) | `--max-num-seqs 4` too low for warmup buckets (up to 16) | Kept `--max-num-seqs 16`, reduced `--max-model-len` instead |
| 404 on `/v1/chat/completions` | Model name mismatch — vLLM expects full snapshot path | Used `/data/datasets/community/.../snapshots/6f6073b...` |
| 400 context overflow | Token estimate (chars/4) underestimated — legal text tokenizes at ~2.6 chars/token | Dynamic budgeting: `opinion_tokens = chars / 2.6`, compute `max_tokens` per request |
| Preprocessor stripped 95% of text | `get_analysis_text()` only kept ANALYSIS+CONCLUSION sections | Changed to keep everything except HEADER boilerplate |
| Timeout on longer opinions | 120s timeout too short at ~5-10 tokens/s generation speed | Increased to 300s |
| All elements NOT_ANALYZED on first tests | Test opinions were contamination (criminal case) or procedural dismissals | Expected — LLM correctly identified no 10b-5 analysis present |

**Performance**:

| Metric | Value |
|--------|-------|
| Concurrency | 12-20 parallel requests |
| Throughput | 6.1 cases/min |
| Avg latency per case | 130.8s |
| GPU utilization | 19-21% (memory-bound, not compute-bound) |
| HBM usage | 82.7 / 98.3 GB per card (84%) |
| Temperature | 29-33°C |
| Power | 109-133W / 600W cap |

**Result**:

| Metric | Count |
|--------|-------|
| Total extractions | 132 (128 valid, 4 invalid from early bugs) |
| With real element analysis | 111 (87%) |
| All NOT_ANALYZED (procedural/contamination) | 17 (13%) |
| DEFENDANT_WINS | 85 |
| MIXED | 29 |
| PLAINTIFF_WINS | 14 |
| Success rate | 94.5% (later runs: 100%) |

**Sample extraction** (Minneapolis Firefighters v. MEMC Electronic Materials):
```json
{
  "material_misrepresentation": {
    "status": "NOT_SATISFIED",
    "sub_conditions": ["MisleadingOmissions"],
    "key_facts": ["MEMC's pre-class period disclosures of production problems"],
    "judge_reasoning": "The district court concluded the pre-class period disclosures
     could not create a duty on MEMC's part to disclose the incidents."
  },
  "scienter": {
    "status": "NOT_SATISFIED",
    "sub_conditions": ["RecklessDisregard"],
    "judge_reasoning": "We do not believe the inference of scienter is as compelling
     as the more innocent, simpler inference that the defendants did not believe
     they had a continuing duty to disclose information."
  }
}
```

---

## Technical Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.12+ |
| Data storage | SQLite (WAL mode, crash-safe checkpointing) |
| Schema validation | Pydantic v2 (strict typing, JSON schema generation) |
| LLM | Llama 3.3 70B Instruct (BF16) |
| LLM serving | vLLM (Habana fork), tensor parallel across 8x Gaudi 2 |
| Accelerator | Intel Gaudi 2 HL-225 (8 cards, 768 GB HBM total) |
| HPC cluster | ASU Sol (SLURM, Singularity containers) |
| Data source | CourtListener API v4 (EDU tier) |
| HTTP client | `requests` (zero extra dependencies) |
| Concurrency | `concurrent.futures.ThreadPoolExecutor` |

## Repository Structure (Phase 0-1)

```
script/
├── scraper_private_10b5.py      # CourtListener async scraper with crash recovery
├── label_and_split.py           # Phase 0.2+0.3: regex outcome labeling + dataset split
├── lift_opinions.py             # Phase 1.3: batch IRAC extraction CLI
├── run_vllm_legal.sh            # SLURM script for vLLM on Gaudi 2
└── lifting/
    ├── __init__.py
    ├── schema.py                # Pydantic IRAC extraction models (6 elements)
    ├── rules.py                 # 10b-5 conjunctive rule + sub-condition validation
    ├── preprocess.py            # Opinion section splitter
    ├── prompt.py                # LLM prompt template builder
    ├── llm_client.py            # vLLM HTTP client with JSON fallbacks
    ├── extract.py               # Per-opinion extraction orchestrator
    └── store.py                 # SQLite storage for extractions
```

---

## Next Steps

- **Phase 2**: Knowledge Graph — load cases, citations, arguments, judges into Neo4j with signed edges for ANCO-HITS
- **Phase 3**: ANCO-HITS — score arguments on [-1, +1] using signed bipartite graph algorithm
- **Phase 4**: GraphSAGE — structural predictions from citation network neighborhoods
- **Phase 5**: Constrained RAG — retrieval with hard rules (citation check, statute grounding, binding authority)
