# Annotation Rubric — Private 10b-5 Element Analysis

**Project**: Private 10b-5 Securities Fraud Analysis Pipeline
**Lab**: CIPS Lab, Arizona State University

---

## Instructions

For each case, you will read the **raw opinion text** and annotate the judge's analysis of the 6 elements of a Private Rule 10b-5 securities fraud claim.

**Important rules:**
- Read the full opinion text before annotating
- Base your annotations ONLY on what the judge wrote — do not infer or speculate
- If the judge did not address an element, mark it NOT_ANALYZED
- Cases are presented in randomized order
- Do NOT look at any pipeline output while annotating

---

## Section A — Issue Spotting

1. **Legal issue(s)**: What legal issue(s) does this opinion address? (1-2 sentences)

2. **Procedural stage** (select one):
   - [ ] MTD — Motion to dismiss
   - [ ] SJ — Summary judgment
   - [ ] TRIAL — Trial verdict
   - [ ] APPEAL — Appellate review

---

## Section B — Rule Recall

3. **Which 10b-5 elements does the judge analyze?** (check all that apply):
   - [ ] Material misrepresentation (false statements or misleading omissions)
   - [ ] Scienter (intent to deceive or recklessness)
   - [ ] Connection (fraud in connection with purchase/sale of securities)
   - [ ] Reliance (plaintiff relied on the misrepresentation)
   - [ ] Economic loss (plaintiff suffered actual financial loss)
   - [ ] Loss causation (the fraud caused the loss)

4. **Key statutes cited by the judge** (list up to 5):
   - ________________________________________
   - ________________________________________
   - ________________________________________

5. **Key precedents cited by the judge** (list up to 5):
   - ________________________________________
   - ________________________________________
   - ________________________________________
   - ________________________________________
   - ________________________________________

---

## Section C — Rule Application (Per Element)

For EACH of the 6 elements, fill out the following. If the judge did not analyze an element, select NOT_ANALYZED and skip the rest.

### C.1 Material Misrepresentation

**Status** (select one):
- [ ] SATISFIED — Judge found this element adequately pled/proven
- [ ] NOT_SATISFIED — Judge found this element failed
- [ ] CONTESTED — Element is disputed but not yet resolved
- [ ] NOT_ANALYZED — Judge did not reach this element

**Key facts relied on** (1-3 bullet points):
- ________________________________________
- ________________________________________
- ________________________________________

**Judge's reasoning** (1-2 sentences paraphrasing the holding):

________________________________________

**Your confidence**: [ ] HIGH  [ ] MEDIUM  [ ] LOW

---

### C.2 Scienter

**Status**: [ ] SATISFIED  [ ] NOT_SATISFIED  [ ] CONTESTED  [ ] NOT_ANALYZED

**Key facts**: ________________________________________

**Judge's reasoning**: ________________________________________

**Confidence**: [ ] HIGH  [ ] MEDIUM  [ ] LOW

---

### C.3 Connection

**Status**: [ ] SATISFIED  [ ] NOT_SATISFIED  [ ] CONTESTED  [ ] NOT_ANALYZED

**Key facts**: ________________________________________

**Judge's reasoning**: ________________________________________

**Confidence**: [ ] HIGH  [ ] MEDIUM  [ ] LOW

---

### C.4 Reliance

**Status**: [ ] SATISFIED  [ ] NOT_SATISFIED  [ ] CONTESTED  [ ] NOT_ANALYZED

**Key facts**: ________________________________________

**Judge's reasoning**: ________________________________________

**Confidence**: [ ] HIGH  [ ] MEDIUM  [ ] LOW

---

### C.5 Economic Loss

**Status**: [ ] SATISFIED  [ ] NOT_SATISFIED  [ ] CONTESTED  [ ] NOT_ANALYZED

**Key facts**: ________________________________________

**Judge's reasoning**: ________________________________________

**Confidence**: [ ] HIGH  [ ] MEDIUM  [ ] LOW

---

### C.6 Loss Causation

**Status**: [ ] SATISFIED  [ ] NOT_SATISFIED  [ ] CONTESTED  [ ] NOT_ANALYZED

**Key facts**: ________________________________________

**Judge's reasoning**: ________________________________________

**Confidence**: [ ] HIGH  [ ] MEDIUM  [ ] LOW

---

## Section D — Conclusion

6. **Overall outcome** (select one):
   - [ ] PLAINTIFF_WINS — Motion denied, plaintiff prevails, reversed in plaintiff's favor
   - [ ] DEFENDANT_WINS — Motion granted, defendant prevails, affirmed dismissal
   - [ ] MIXED — Granted in part / denied in part

7. **Brief rationale** (1-2 sentences):

________________________________________

---

## Section E — Quality Flags

8. **Is this a Private 10b-5 securities fraud case?**
   - [ ] Yes
   - [ ] No — Describe: ________________________________________

9. **Is the opinion text sufficient for element-level analysis?**
   - [ ] Yes
   - [ ] No — Why: ________________________________________

10. **Are there elements where reasonable experts might disagree?**
    - [ ] No
    - [ ] Yes — Which elements: ________________________________________

---

## Status Definitions

| Status | Meaning | Example |
|--------|---------|---------|
| SATISFIED | Judge explicitly found element met | "Plaintiffs have adequately pled scienter" |
| NOT_SATISFIED | Judge explicitly found element failed | "The complaint fails to allege loss causation" |
| CONTESTED | Element is disputed, ruling deferred or ambiguous | "The parties dispute whether reliance is presumed" |
| NOT_ANALYZED | Judge did not reach this element | Dismissed on other grounds before reaching this element |

## Analysis Quality Scale (for grading LLM output — Phase 2 of annotation)

| Score | Meaning |
|-------|---------|
| 0 | No analysis — just states conclusion without reasoning |
| 1 | Restates facts and rule but does not connect them |
| 2 | Partially connects facts to rule, missing key inferences |
| 3 | Full analysis with proper inferences from facts to elements |
