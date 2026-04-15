# Topic 6: Phase 1 — Symbolic Lifting

## What Phase 1 Does

Reads each opinion's raw text (~50K chars of judicial prose) and extracts a structured IRAC object — element statuses, sub-conditions, key facts, judge reasoning, outcome, statutes, precedents, arguments. This is the **lifting step** from Beyond the Black Box: neural output (LLM reading text) → symbolic representation (structured JSON).

## The Pipeline (per opinion)

```
Opinion text (SQLite)
  → Preprocess: strip court header boilerplate (preprocess.py)
  → Dynamic token budgeting: fit within 8192 context (extract.py)
  → Truncation if needed: keep 60% beginning + 40% end (extract.py)
  → Build prompt: system + user + schema + sub-condition list (prompt.py)
  → HTTP POST to vLLM on Gaudi 2 (llm_client.py)
  → JSON extraction with fallbacks (llm_client.py)
  → Pydantic validation: schema enforcement (schema.py)
  → Sub-condition validation: rule checking (rules.py)
  → Store in SQLite: valid/invalid + raw LLM response (store.py)
```

## Preprocessing (preprocess.py)

Opinions start with ~500-2,000 chars of boilerplate (court name, party lists, docket numbers). The preprocessor:
1. Detects header boundary using heading patterns (ALL-CAPS, Roman numerals)
2. Classifies sections: HEADER, BACKGROUND, ANALYSIS, CONCLUSION, OTHER
3. Returns analysis + conclusion text for LLM input

**Key design**: For appellate opinions (88%), element analysis is woven into narrative prose — preprocessor keeps full body as ANALYSIS. For district court (12%), sections are explicit and splitting works well.

**Fallback**: No boundaries found → returns full text as single ANALYSIS. Never fails.

## The Prompt Design (prompt.py)

**System prompt**: "You are a legal analysis assistant. Extract a structured IRAC analysis following the exact JSON schema."

**User prompt** contains:
1. Case metadata (name, court, docket_id)
2. Instructions for the 6 elements
3. Valid sub-conditions list (explicitly enumerated)
4. Rules: "NOT_ANALYZED if judge didn't discuss", "Only use listed sub-conditions", "Quote judge's reasoning"
5. The opinion text
6. JSON schema to match

**Stripped from schema** (injected post-hoc): case_id, opinion_id (LLM would guess wrong), confidence (computed post-hoc), procedural_stage (from Phase 0.3).

## Dynamic Token Budgeting (extract.py)

```
8192 total context
- ~1200: prompt template + system + schema
-  ~100: safety margin
- 1024: reserved for output JSON
= ~5868 tokens for opinion text (~15,000 chars at 2.6 chars/token)
```

If opinion exceeds budget: truncate from middle — keep 60% beginning + 40% end. Conclusion (end) matters most. ~85% of opinions fit without truncation.

## The LLM Client (llm_client.py)

Raw HTTP via `requests` to vLLM OpenAI-compatible API. No `openai` package.

- **Temperature 0.0**: Greedy decoding — deterministic, reproducible
- **Timeout**: 300s per request

**JSON extraction fallbacks** (LLMs sometimes wrap JSON in markdown):
1. Try `json.loads()` on raw response
2. Strip markdown fences (```json ... ```)
3. Regex extract first `{...}` block
4. All fail → store as invalid

## Validation (schema.py + rules.py)

**Layer 1 — Pydantic**: Does JSON match IRACExtraction schema? All 6 elements present? Status is valid enum?

**Layer 2 — Rule validation**: Are sub-conditions valid per element? Exact string matching against predefined list:

```python
ELEMENT_RULES = {
    "material_misrepresentation": ["FalseStatements", "MisleadingOmissions", "SchemeToDefraud"],
    "scienter": ["MotiveAndOpportunity", "ConsciousMisbehavior", "RecklessDisregard"],
    ...
}

# Validation: is each LLM-provided sub-condition in the allowed list?
def validate_sub_conditions(element_name, sub_conditions):
    valid = set(ELEMENT_RULES[element_name])
    return [sc for sc in sub_conditions if sc not in valid]
```

The prompt tells the LLM exact allowed values. 97% of the time it copies them correctly. When it doesn't (e.g., "Misrepresentation" instead of "FalseStatements"), the string match catches it.

**What validation does NOT check**: Whether the LLM chose the *correct* sub-condition for the facts, or whether all relevant sub-conditions were found. Those require human review.

## Infrastructure (Gaudi 2 on Sol)

| Component | Detail |
|---|---|
| LLM | Llama 3.3 70B Instruct (BF16) |
| Hardware | 8x Intel Gaudi 2 HL-225 (96 GB HBM each, 768 GB total) |
| Serving | vLLM (Habana fork), tensor parallel across 8 cards |
| Context | `--max-model-len 8192`, `--max-num-seqs 16` (initial run) |
| Concurrency | ThreadPoolExecutor, 12-20 parallel requests |
| Throughput | 6.1 cases/min, avg 130.8s per case |

### Gaudi 2 Memory Budget for KV Cache

With Llama 3.3 70B BF16 on TP=8, most GPU memory is **available for KV cache**:

| Component | Per GPU (96 GB) | Total (8 GPUs) |
|---|---|---|
| Usable memory (0.9 utilization) | 86.4 GB | 691 GB |
| Model weights (TP=8) | ~17.5 GB | 140 GB |
| Activations + overhead | ~3 GB | ~24 GB |
| **Available for KV cache** | **~66 GB** | **~527 GB** |

**KV cache per token per GPU** (Llama 3.3 70B uses GQA with 8 KV heads):

```
2 (K+V) × 80 (layers) × 1 (kv_head per GPU at TP=8) × 128 (head_dim) × 2 (BF16 bytes)
= 40,960 bytes ≈ 40 KB per token per GPU
```

### Recommended Configurations

| max-model-len | KV per seq per GPU | max-num-seqs | Covers opinions | Use case |
|---:|---:|---:|---|---|
| 8,192 | 0.3 GB | ~200 | ~15% (most truncated) | Initial testing (what we used) |
| 32,768 | 1.3 GB | ~50 | ~90% | Good balance |
| **65,536** | **2.6 GB** | **~25** | **~98%** | **Recommended for production run** |
| 131,072 | 5.1 GB | ~12 | ~100% (Llama's full 128K) | Maximum context, fewer concurrent |

**KV cache compression (e.g., TurboQuant+) is NOT needed.** With 66 GB free per GPU, even 131K context × 12 concurrent sequences only uses ~61 GB. We are memory-rich on Gaudi 2.

**Plan**: Use `--max-model-len=65536 --max-num-seqs=25` for the full dataset run. This covers ~98% of opinions without truncation. If some opinions exceed 65K tokens (~170K chars), fall back to `--max-model-len=131072 --max-num-seqs=12`.

## Challenges Solved

| Problem | Fix |
|---|---|
| vLLM warmup crash | Kept `--max-num-seqs 16`, reduced `--max-model-len` |
| 404 on API endpoint | Model name mismatch — used full snapshot path |
| Context overflow | Changed token estimate from chars/4 to chars/2.6 |
| Preprocessor stripped 95% of text | Changed to keep everything except HEADER |
| Timeout on long opinions | Increased from 120s to 300s |

## Results

| Metric | Value |
|---|---|
| Total extractions | 132 (128 valid, 4 invalid) |
| With real element analysis | 111 (87%) |
| All NOT_ANALYZED | 17 (13%) |
| DEFENDANT_WINS / MIXED / PLAINTIFF_WINS | 85 / 29 / 14 |
| Success rate | 97% |

## Code Files

| File | Role |
|---|---|
| `script/lifting/__init__.py` | Package init |
| `script/lifting/schema.py` | Pydantic models — IRACExtraction, ElementAnalysis, Elements, ElementStatus |
| `script/lifting/rules.py` | 6-element conjunctive rule, 14 sub-conditions, validate_sub_conditions(), evaluate_outcome() |
| `script/lifting/preprocess.py` | Opinion section splitter — strip header, classify sections, get_analysis_text() |
| `script/lifting/prompt.py` | Prompt template builder — system + user messages with schema and sub-condition list |
| `script/lifting/llm_client.py` | vLLM HTTP client — chat_completion(), extract_json() with markdown/regex fallbacks |
| `script/lifting/extract.py` | Per-opinion orchestrator — budget, truncate, prompt, call LLM, validate, store |
| `script/lifting/store.py` | SQLite storage — irac_extractions table, save/load functions |
| `script/lift_opinions.py` | CLI batch runner — --dry-run, --mock, --limit, --concurrency, --llm-url |
| `script/run_vllm_legal.sh` | SLURM script for vLLM on Gaudi 2 (8x HL-225, TP=8) |

## Elevator Pitch

"Phase 1 is the lifting step — we feed each judicial opinion to Llama 3.3 70B and it extracts a structured IRAC analysis with element statuses, sub-conditions, key facts, and judge reasoning. The extraction is constrained by a Pydantic schema and validated against our rule definitions. We processed 128 opinions at 6 cases/minute on 8x Gaudi 2 with 97% success rate."

---

## Q&A

### Q: Can we split long opinions across two LLM calls?

Yes — **chunked/map-reduce extraction**. Call 1 processes the first half, Call 2 processes the second half, then merge results.

**Why we didn't do this:**
1. Element analysis often spans sections — judge starts scienter on page 5, concludes on page 8
2. Merging is hard — what if Call 1 says NOT_SATISFIED and Call 2 says SATISFIED for the same element?
3. Truncation (60/40 split) works well enough — preserves factual setup and conclusion
4. 85% of opinions fit without any truncation

**Better alternative**: Increase `--max-model-len` to 65536 on Gaudi 2, which covers ~98% of opinions without truncation (see Gaudi 2 Memory Budget section above). Chunked extraction is only needed for the rare opinions exceeding 128K tokens (~330K chars).

### Q: How does rules.py check the LLM output?

Exact string matching against a predefined list. The prompt tells the LLM "use these exact names: FalseStatements, MisleadingOmissions, SchemeToDefraud." If the LLM outputs "Misrepresentation" instead, it's not in the list → marked invalid.

Simple but effective — catches hallucinated terminology at zero cost. Does NOT check whether the sub-condition is semantically correct for the case (requires human review).

### Q: What is --max-model-len and --max-num-seqs?

**`--max-model-len`** = maximum tokens per request (how long each opinion can be)
**`--max-num-seqs`** = maximum concurrent requests (how many opinions processed simultaneously)

Both consume GPU memory (KV cache). Their product is bounded by available memory after model weights are loaded:

```
768 GB total - ~140 GB model weights - ~24 GB overhead = ~527 GB for KV cache
KV cache per token per GPU = 40 KB (see memory budget above)

Config A: 8,192  × 16 seqs =  131K token-slots, 0.3 GB/seq/GPU  ← initial run (worked, most opinions truncated)
Config B: 65,536 × 25 seqs = 1.6M token-slots, 2.6 GB/seq/GPU  ← recommended (covers ~98% of opinions)
Config C: 131,072 × 12 seqs = 1.6M token-slots, 5.1 GB/seq/GPU  ← full 128K context, fewer concurrent
```

**Analogy**: Restaurant tables.
- `max-num-seqs` = number of tables (concurrent customers)
- `max-model-len` = table size (how big a meal each can order)
- GPU memory = total floor space

### Next Steps: Larger Context for Full Dataset Run

The initial run used `--max-model-len 8192` which truncated ~85% of opinions. With the Gaudi 2 memory budget analysis, we can go much larger:

1. **Primary config**: `--max-model-len 65536 --max-num-seqs 25` — covers ~98% of opinions untruncated, 25 concurrent sequences for throughput
2. **Fallback config**: `--max-model-len 131072 --max-num-seqs 12` — Llama's full 128K context, for the longest opinions if needed

KV cache compression (TurboQuant+, FP8 KV, etc.) was evaluated and is **not needed** — we have 66 GB free per GPU, well beyond what either config requires.

Larger context means fewer opinions need truncation → better extraction quality, especially for long appellate opinions where element analysis spans many pages.
