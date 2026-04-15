# Topic 2: What Makes This "Neuro-Symbolic"?

## The Two Paradigms

**Neural** = learns patterns from data. LLMs, embeddings, GraphSAGE. Powerful but opaque — you can't inspect why it made a decision.

**Symbolic** = follows explicit rules written by humans. If-then logic, knowledge graphs, constraint validators. Transparent but brittle — can't handle ambiguity or unseen patterns.

**Neuro-symbolic** = combine both. Use neural models for what they're good at (reading text, finding similarity), use symbolic systems for what they're good at (enforcing rules, providing explanations).

## Where Is Each in the Pipeline?

| Component | Neural or Symbolic? | Why? |
|---|---|---|
| Llama 3.3 reading opinions (Phase 1) | **Neural** | Pattern recognition on unstructured text |
| 6-element conjunctive rule (rules.py) | **Symbolic** | Hardcoded legal standard, human-authored |
| Sub-condition validation | **Symbolic** | Checks LLM output against allowed values |
| Pydantic schema enforcement | **Symbolic** | Structural constraint — JSON must match schema |
| SBERT embeddings (Phase 5) | **Neural** | Learned similarity between opinion texts |
| Neo4j graph traversal (Phase 5) | **Symbolic** | Explicit relationships (cites, same statute) |
| ANCO-HITS scoring (Phase 3) | **Symbolic** | Deterministic algorithm on signed bipartite graph |
| 6 hard constraints (Phase 5) | **Symbolic** | Rule-based validators, no learning involved |
| LLM lowering generation (Phase 5) | **Neural** | Natural language synthesis from context |
| Weighted fusion re-ranking (Phase 5) | **Hybrid** | Combines neural scores (semantic) with symbolic scores (graph, ANCO) |

## The Interaction Pattern

The neural and symbolic components don't just coexist — they **constrain each other**:

```
Neural → Symbolic (Lifting):
  LLM reads opinion text → produces structured IRAC extraction
  → Pydantic validates structure
  → rules.py validates sub-conditions
  → store.py marks valid/invalid

Symbolic → Neural (Lowering):
  6-element rule defines what to extract
  → Graph traversal finds relevant precedents
  → ANCO-HITS scores rank arguments
  → Constrained prompt tells LLM what rules to follow
  → 6 validators check LLM output after generation
```

The neural component does the heavy lifting (reading 50K-char opinions, generating natural language). The symbolic component ensures correctness (valid elements, real citations, proper legal structure).

## Why Not Pure Neural?

If you just give Llama 3.3 a raw opinion and ask "who wins?", it will:
- Often get the right answer (~60-75%)
- Sometimes hallucinate case citations that don't exist
- Give no structured breakdown of which elements determined the outcome
- Provide no traceable path from facts → rule → conclusion
- Be unfixable when wrong — you can't edit its reasoning

## Why Not Pure Symbolic?

If you tried to do this with only regex and rules (no LLM):
- Phase 0.3 regex labeling got ~55-65% accuracy — decent but limited
- Can't extract nuanced element analysis from 50K chars of legal prose
- Can't produce natural language IRAC analysis
- Can't handle the variability of judicial writing styles

## The Evidence It Works

**LLM end-to-end test**: Phase 1 LLM extraction matched ground truth on 12/12 elements across 2 test cases (2 cases × 6 elements each). The LLM correctly identified element statuses for all 6 elements in both Ashland v. Oppenheimer and Ketan Patel v. Portfolio Diversification.

**Constraint system in action**: When testing Ketan Patel (a CA7 case), the 6 hard constraints caught 7 cross-circuit citations (CA4, CA3, CA9, CA6, CA8 precedents cited in a CA7 case — persuasive but not binding authority) and 1 anachronistic citation (Newtyn Partners filed 2025, cited in Cory v. Stewart filed 2019 — can't cite a case that didn't exist yet).

The LLM got the legal analysis right (neural strength), but the constraint system caught citation errors the LLM couldn't detect (symbolic strength). Neither alone does both.

## Why Do 10b-5 Opinions Cite Non-10b-5 Cases?

Legal reasoning builds on **general principles**, not just same-domain precedent:
- **Twombly / Iqbal** — general pleading standards (applies to ALL federal cases)
- **Contract law cases** — for interpreting what "material" means
- **Criminal fraud cases** — for defining "scienter" (intent to deceive)
- **Procedural cases** — for MTD and summary judgment standards

Only ~30-40% of citations in a 10b-5 opinion point to other 10b-5 cases. The rest are general legal infrastructure.

### The "UNVERIFIED" Citation Issue

The constraint system flags landmark cases (Twombly, Iqbal, Frank v. Dana Corp.) as UNVERIFIED because they're not in our 416-case Private 10b-5 dataset. They exist as external placeholder nodes in Neo4j (cited BY our cases), but the constraint checker correctly reports it can't verify them against our domain-specific dataset.

**Demo strategy**: Both approaches combined:
1. Whitelist ~20 well-known landmarks so they don't trigger violations in the demo
2. Also explain to the audience: "These are general federal procedure landmarks, not 10b-5 cases. Our dataset intentionally covers only Private 10b-5. The constraint system correctly identifies that we can't verify these citations against our domain-specific dataset." — This demonstrates the constraint system working correctly.

### Should We Scrape Those External Cases?

No. The 7,360 external citation targets are already in the knowledge graph as placeholder nodes. Scraping their full text would add ~7,000 non-10b-5 opinions that aren't useful for Phase 1 lifting (no 6-element analysis to extract). Better to scrape more Private 10b-5 cases (scaling from 416 → 5,855) for more internal citation links, more shared ANCO-HITS arguments, and better semantic search coverage.

## Elevator Pitch

"Neuro-symbolic means the neural parts (LLM, embeddings) handle the messy text understanding, while the symbolic parts (rules, constraints, knowledge graph) enforce correctness and provide explainability. Neither alone is sufficient — the LLM can read opinions but hallucinates citations; the rules can enforce legal structure but can't read free text. Combined, we get accurate extraction WITH traceable reasoning."
