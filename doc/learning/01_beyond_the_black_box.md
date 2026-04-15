# Topic 1: Beyond the Black Box — The Theoretical Framework

## The Core Problem

LLMs are **black boxes**. They can analyze text, but:
- You can't see WHY they reached a conclusion
- You can't FIX a specific error without retraining the whole model
- You can't VERIFY their reasoning is legally correct
- Their Chain-of-Thought explanations are often unfaithful to their actual reasoning

## The Solution: Programmable AI

The paper (Trivedi, Davulcu et al., IEEE TCSS 2026) proposes a **neuro-symbolic architecture** with two key operations:

**Lifting** — translate neural outputs (LLM text analysis) INTO symbolic representations (logic rules, structured patterns). The LLM reads unstructured text and produces structured, machine-readable patterns.

**Lowering** — inject symbolic knowledge (rules, constraints) BACK INTO neural models via constrained prompts and post-generation validation. The symbolic rules guide what the LLM can say.

## Algorithm 1 (the 5-step loop)

This is the core algorithm from the paper. Our entire pipeline maps to it:

```
Step 1: Camp Clustering         → Phase 0 (data preparation, filtering)
Step 2: Neural Theme Extraction → Phase 1 (LLM reads opinions, extracts themes)
Step 3: Symbolic Pattern Lifting → Phase 1 (structured IRAC extraction with rules)
Step 4: Pattern Lowering         → Phase 5 (constrained RAG — rules guide LLM generation)
Step 5: Exception Handling       → Phase 7 (evaluate failures, fix rules, re-run)
```

## The Key Insight: "How Can I Fix My AI?"

Traditional ML: model is wrong → retrain on more data → hope it works.

Programmable AI: model is wrong → inspect the symbolic rule → edit the rule → re-run. No retraining needed.

**Example in our pipeline**: If the system incorrectly labels "scienter" as SATISFIED for a case, you can:
1. Look at the rule: `Scienter ← MotiveAndOpportunity OR ConsciousMisbehavior OR RecklessDisregard`
2. See which sub-condition triggered
3. Edit the sub-condition or add a new one
4. Re-run Phase 1 on that case — no retraining of Llama 3.3

This is why the paper calls it "programmable" — the rules are editable code, not learned weights.

## Paper Results (on energy tweets, not legal)

The paper demonstrated this on 1.3 million tweets about global energy:
- **Rule coverage**: 72-82% of discourse explained by symbolic rules after 3 iterations
- **Human-AI agreement**: 90.33% (Cohen's kappa = 0.84) — symbolic rules matched human interpretation
- **Iterative improvement**: Each lifting iteration covered more text without retraining

## How This Maps to Our Legal Pipeline

| Paper Concept | Our Implementation |
|---|---|
| Text corpus D | 416 Private 10b-5 cases from CourtListener |
| Camp clustering | Phase 0: filter private vs SEC enforcement |
| Neural themes | Phase 1: LLM extracts element statuses, facts, reasoning |
| Symbolic rules R | The 6-element conjunctive rule + 14 sub-conditions |
| Pattern lowering | Phase 5: constrained RAG with 6 hard validators |
| Exception handling | Phase 7: evaluate, find failures, edit rules |
| Patterned narratives | IRAC extractions — structured, traceable legal analysis |

## Elevator Pitch

"Our pipeline is based on the Beyond the Black Box framework from our lab. Instead of using an LLM as a black box, we lift its outputs into symbolic rules that humans can inspect and edit, then lower those rules back into the LLM to constrain its generation. If the system makes an error, we fix the rule — we don't retrain the model. This is what makes it explainable and trustworthy for legal analysis."

---

## Q&A

### Q: How do you inject symbolic knowledge?

Three types of symbolic knowledge, injected at three layers:

1. **The 6-element conjunctive rule** (rules.py) — the legal standard: PlaintiffWins ← all 6 elements satisfied. This is domain knowledge, NOT the IRAC format. IRAC (Issue-Rule-Application-Conclusion) is the output format. The 6-element rule is what gets applied inside the Application section.

2. **The 6 hard constraints** (constraints.py) — post-generation validators: citation check, statute grounding, binding authority, temporal validity, ambiguity flag, missing element.

3. **Constrained prompts** — system prompts that tell the LLM what rules to follow.

Injection happens at three layers:
- **Before generation**: prompt constrains what the LLM should do
- **During generation**: retrieved context limits what cases/statutes the LLM can reference
- **After generation**: 6 validators catch anything the LLM got wrong

### Q: Did we implement attention masks and training filters from the paper?

**No.** The paper describes three methods for lowering (injecting symbolic knowledge back into neural models). We implemented one of them:

#### Attention Masks (from the paper — NOT implemented)

**What it is**: During model training or fine-tuning, you modify the attention mechanism so the model can only "see" certain tokens. The symbolic rules determine which parts of the input are relevant.

**What it's for**: Forces the model to learn from rule-relevant portions of text only. For example, when training on a 10b-5 opinion, an attention mask could restrict the model to only attend to the scienter discussion section, ignoring procedural boilerplate.

**Why we didn't implement it**: Requires fine-tuning the LLM. We use Llama 3.3 70B as a pre-trained model without any weight modification. Fine-tuning a 70B model requires significant compute and training data that we don't have.

#### Training Filters (from the paper — NOT implemented)

**What it is**: Use symbolic rules to generate pseudo-labels for the training data, then train (or fine-tune) the model on this rule-labeled data. The symbolic patterns act as a supervisory signal — the model learns to reproduce the rule-based classifications.

**What it's for**: Creates a feedback loop where symbolic rules improve the neural model. In the paper's energy tweet example, after lifting produces symbolic rules that classify tweets into narrative patterns, those classifications become training labels for a downstream classifier.

**Why we didn't implement it**: Same reason — requires fine-tuning. Also, with only 128 IRAC extractions, we don't have enough pseudo-labeled data for meaningful fine-tuning. This could become viable when scaled to 3,400+ cases.

#### Constrained Prompting (our implementation — YES)

**What it is**: Include symbolic rules directly in the LLM prompt. The system prompt tells the LLM what rules to follow, what format to use, and what constraints to obey.

**What it's for**: Achieves a similar effect to attention masks and training filters WITHOUT modifying model weights. The constraint is in the prompt, not in the architecture.

**Our two constrained prompts**:
1. **Lifting prompt** (Phase 1): "Extract element statuses. Only use sub-conditions from this list. Set NOT_ANALYZED if the judge didn't discuss it."
2. **Lowering prompt** (Phase 5): "Analyze using ONLY the provided context. Never fabricate citations. Flag cross-circuit citations."

**Limitation**: Prompt-based constraints are "soft" — the LLM can still violate them. That's why we added the 6 post-generation validators as a second line of defense. The validators are "hard" constraints that catch what the prompt couldn't prevent.

| Method | Constraint type | Requires fine-tuning? | We implemented? |
|---|---|---|---|
| Attention masks | Architectural (hard) | Yes | No |
| Training filters | Training signal (soft→hard over time) | Yes | No |
| Constrained prompting | Prompt-level (soft) | No | **Yes** |
| Post-generation validation | Output-level (hard) | No | **Yes** |

### Open Discussion with Teammates

- **Could we implement attention masks or training filters in the future?** If we fine-tune a smaller model (e.g., Llama 8B) on our 128+ IRAC extractions as pseudo-labels, we could use training filters. This would be a Phase 4 alternative to GraphSAGE.
- **Is constrained prompting sufficient?** For our current scale (128 cases, 6 elements), yes. The 6 post-generation validators catch what the prompt misses. At scale, fine-tuning might be needed for reliability.
- **What's the tradeoff?** Constrained prompting is fast to iterate (change the prompt, re-run). Fine-tuning is more robust but slower to iterate (retrain, evaluate, repeat). The "programmable" philosophy favors fast iteration.
