# Topic 3: Why Private 10b-5?

## What is Rule 10b-5?

Rule 10b-5 (17 C.F.R. Section 240.10b-5) is the primary federal anti-fraud rule for securities. It says: you can't make false statements or omissions in connection with buying or selling securities.

Two types of cases use this rule:

| | SEC Enforcement | Private 10b-5 (ours) |
|---|---|---|
| **Who sues** | SEC (government agency) | Investors (individuals, pension funds, class actions) |
| **Elements to prove** | 3-4 | **6** |
| **Reliance required?** | No | Yes |
| **Economic loss required?** | No | Yes |
| **Loss causation required?** | No | Yes |
| **Typical outcome** | 76% consent judgment (settlement) | **~50/50 MTD granted vs denied** |
| **Judge writes analysis?** | Rarely (cases settle) | **Yes — element-by-element** |

## Why Private 10b-5 Over SEC Enforcement?

| Factor | Private 10b-5 | SEC Enforcement | Winner |
|---|---|---|---|
| **Outcome variety** | ~50/50 | 76% consent judgments | Private 10b-5 |
| **Element richness** | 6 elements with sub-conditions | 3-4 elements (simpler) | Private 10b-5 |
| **Opinion quality** | Judges write detailed element analysis | Mostly settlements, no analysis | Private 10b-5 |
| **Citation network** | Avg 28.3 citations per opinion | Thin (settlements don't cite) | Private 10b-5 |
| **Data leakage risk** | Low (50/50 forces model to learn) | **High** (76% consent = free accuracy) | Private 10b-5 |

**The data leakage problem**: With SEC enforcement, a model can achieve 76% accuracy by simply predicting "consent judgment" every time. No reasoning needed. The outcome distribution itself leaks the answer. Private 10b-5 at ~50/50 forces the model to actually learn which elements determine the outcome.

## Current Dataset (416-case sample)

### Summary Stats

| Metric | Value |
|---|---|
| Total cases | 416 |
| Total opinions | 486 |
| Opinions with full text (>1K chars) | 149 (36%) |
| Avg opinion length | 52,650 chars (~20 pages) |
| Citation edges | 13,530 |
| Avg citations per opinion | 28.3 |
| Parties recorded | 196 (sparse — only 26 dockets) |
| Attorneys recorded | 192 (sparse — only 24 dockets) |

### Outcome Distribution

**Regex labeling (Phase 0.3):**

| Outcome | Count | % of labeled |
|---|---|---|
| DEFENDANT_WINS | 74 | 68% |
| PLAINTIFF_WINS | 27 | 25% |
| MIXED | 8 | 7% |
| UNCLEAR | 40 | — |
| UNLABELED (no text) | 273 | — |

**LLM extraction (Phase 1, 128 cases):**

| Outcome | Count | % |
|---|---|---|
| DEFENDANT_WINS | 85 | 66% |
| MIXED | 29 | 23% |
| PLAINTIFF_WINS | 14 | 11% |

The skew toward DEFENDANT_WINS is real — most 10b-5 MTD motions are granted. MIXED is larger in LLM extraction because the LLM identifies partial rulings that regex missed.

### Contamination

53 cases (13%) were not Private 10b-5:

| Type | Count | Detection |
|---|---|---|
| PRIVATE (correct) | 368 | — |
| DOJ_CRIMINAL | 29 | "United States v. ..." |
| SEC_ENFORCEMENT | 23 | "SEC v. ..." |
| SEC_APPEAL | 2 | "... v. SEC" |

Filtered before Phase 1. Broad search query pulled in related but different case types.

### Court Distribution (top 5)

| Court | Count | Note |
|---|---|---|
| CA2 (2nd Circuit) | 52 | Appeals from SDNY |
| NYSD (S.D.N.Y.) | 40 | Busiest securities fraud court |
| CA9 (9th Circuit) | 29 | West coast securities |
| CA5 (5th Circuit) | 27 | Texas/Louisiana |
| CA7 (7th Circuit) | 17 | Chicago |

### Procedural Stage

| Stage | Count | Note |
|---|---|---|
| APPEAL | 136 | Heavy appellate bias — CourtListener has better coverage |
| SJ | 6 | Summary judgment |
| MTD | 5 | Motion to dismiss |

### Scaling Target

| Tier | Cases | Status |
|---|---|---|
| Current sample | 416 | Complete |
| Scaling run | ~5,855 | In progress |
| Full target | ~10,200 | — |
| With opinion text | ~1,500-1,700 | For Phase 1 lifting |
| Citation edges only | ~1,700 | For Phase 2 graph |
| Metadata-only | ~6,800 | Graph node features |

---

## Lifecycle of a Private 10b-5 Case

### The Full Procedural Flow

```
Plaintiff files COMPLAINT
│  Lists claims: Rule 10b-5, Section 11, Section 12, state fraud, etc.
│  One complaint, multiple legal theories (charges).
│
▼
MOTION TO DISMISS (MTD)
│  Defendant asks judge to throw out the case.
│  Judge only reads the complaint — no evidence yet.
│  Standard: "Assuming everything plaintiff says is true, is it enough?"
│  For 10b-5: PSLRA requires "strong inference of scienter" (heightened standard).
│  Judge analyzes each claim separately in ONE opinion.
│
│  Possible outcomes:
│    DENIED → case survives, moves to discovery
│    GRANTED → case dismissed (defendant wins)
│    GRANTED IN PART → some claims survive, some dismissed
│
▼
DISCOVERY
│  Both sides gather evidence: emails, financial records, depositions.
│  Most expensive phase. Strong incentive for defendant to SETTLE here.
│  Settlement = no judicial opinion, no outcome in our dataset.
│
▼
SUMMARY JUDGMENT (SJ)
│  Either side asks judge to rule without trial.
│  Standard: "The evidence is so clear that no reasonable jury could disagree."
│  Higher bar than MTD — must show actual evidence, not just allegations.
│  Same element can have opposite results at MTD vs SJ:
│    MTD: "Scienter adequately pled" (SATISFIED)
│    SJ:  "Evidence shows trades were pre-scheduled" (NOT_SATISFIED)
│
▼
TRIAL
│  Only stage where facts are decided by a jury (or judge in bench trial).
│  Witnesses testify, experts present analysis, jury deliberates.
│  Very rare for 10b-5 — only 5 cases in our dataset.
│  Most cases settle before trial (discovery revealed too much risk).
│
▼
APPEAL
│  Appellate court reviews LEGAL REASONING, not facts.
│  Does NOT re-examine evidence or hear witnesses.
│  Checks: "Did the lower court apply the correct legal standard?"
│  Outcomes: AFFIRMED, REVERSED, REVERSED AND REMANDED, MIXED
│  93% of our labeled opinions are appeals.
```

### Multiple Claims in One Case

A plaintiff typically files multiple claims (charges) in one complaint. The judge rules on ALL of them in a single opinion:

```
Smith v. XYZ Corp — MTD Ruling (one opinion):
  Rule 10b-5:     6 elements analyzed → DISMISSED (failed scienter)
  Section 11:     different elements  → SURVIVES (no scienter needed)
  Section 12:     different standard  → SURVIVES
  State fraud:    state law standard  → DISMISSED

  Opinion outcome: MIXED (granted in part, denied in part)
```

A plaintiff can LOSE on 10b-5 but WIN on other claims. The pipeline focuses specifically on the 10b-5 claim analysis, not the overall case outcome.

### What the Pipeline Predicts

The pipeline predicts **per-opinion, per-claim (Rule 10b-5) outcomes** — not case-level outcomes:

```
Input:  One judicial opinion text
Output: For the Rule 10b-5 claim in this opinion:
        1. Each of the 6 elements: SATISFIED / NOT_SATISFIED / CONTESTED / NOT_ANALYZED
        2. Outcome: PLAINTIFF_WINS / DEFENDANT_WINS / MIXED
        3. Supporting facts, judge reasoning, precedents cited
```

| Level | How we get outcome | Reliable? |
|-------|-------------------|-----------|
| **Opinion/cluster (decision)** | Extract from opinion text | Yes — regex + LLM |
| **Case (ultimate)** | `idb_judgment` from FJC | No — only 63/11,511 cases |
| **Case (inferred)** | Latest cluster's outcome | Best we can do, imperfect |

Case-level outcome inference breaks when:
- Case settled after the last opinion (no written record)
- Last opinion is MTD denial but defendant won later at SJ (opinion not captured)
- Case is still ongoing

For the pipeline, opinion-level outcomes are sufficient. Phase 1 lifting works per-opinion. Phase 3 ANCO-HITS uses per-opinion outcomes for signed edges. The `procedural_stage` label lets downstream phases interpret results correctly.

### Key Terms

| Term | Meaning |
|------|---------|
| **Complaint** | Document filed by plaintiff listing all claims and factual allegations |
| **MTD** | Motion to Dismiss — defendant asks judge to throw out the case before evidence |
| **SJ** | Summary Judgment — either side asks judge to rule based on evidence, no trial |
| **Consent judgment** | Both sides agree to a deal, judge formalizes it — no winner/loser |
| **Cluster** | One decision event within a case (each ruling = one cluster) |
| **Opinion** | The judge's written ruling within a cluster |
| **PSLRA** | Private Securities Litigation Reform Act — heightened pleading standard for 10b-5 |

### Data Hierarchy

```
Case (docket_id) — one lawsuit
  ├── Cluster 1 (MTD ruling, 2021-03-15)
  │     ├── 010combined: full text of the ruling
  │     ├── 020lead: tag (0 chars)
  │     └── 040dissent: tag (0 chars)
  ├── Cluster 2 (SJ ruling, 2022-06-01)
  │     └── 010combined: full text
  └── Cluster 3 (Appeal, 2023-09-20)
        └── 010combined: full text

Most cases (8,374 / 8,418) have only 1 cluster captured.
Only 44 cases have 2+ clusters with opinions.
```

### The Complaint Problem

The pipeline would ideally use complaint text for prediction: "Given this complaint, will the 10b-5 claim survive MTD?" But complaint documents are NOT available in our dataset:

| Source | Status |
|--------|--------|
| CourtListener RECAP documents | 0 available for our cases (PDFs not uploaded to RECAP) |
| Internet Archive mirror | 0 with `filepath_ia` |
| PACER direct purchase | $0.10/page, only 391 cases have `pacer_case_id` |
| Stanford SCAC | Paused for restructuring, class actions only, no API |

**Workaround**: Judges summarize the complaint in MTD opinions. 783+ opinions contain phrases like "the complaint alleges..." in their BACKGROUND section. After backfilling `html_with_citations` (1,290 → ~8,500 opinions with text), this number should grow to 4,000-5,000 cases. The judge's summary is actually more useful than the raw complaint — it's already filtered to the facts that matter for the 6 elements.

---

## Complete Data Field Reference

### Table 1: All Fields in the Dataset

#### cases (28 fields)

| Field | Type | Coverage | Description |
|---|---|---|---|
| docket_id | INTEGER PK | 100% | Unique case identifier in CourtListener |
| case_name | TEXT | ~100% | e.g., "Smith v. XYZ Corp" |
| docket_number | TEXT | ~90% | Court's official number (e.g., "1:23-cv-02456") |
| pacer_case_id | TEXT | ~5% | PACER system ID |
| slug | TEXT | ~90% | URL-safe case name |
| absolute_url | TEXT | ~100% | CourtListener link |
| court_id | TEXT | ~95% | Which court (e.g., "nysd", "ca2") |
| cause | TEXT | ~0.2% | Statute basis (nearly empty for opinion-sourced cases) |
| nature_of_suit | TEXT | ~1% | Case category ("850 Securities/Commodities") |
| jurisdiction_type | TEXT | ~1% | Federal question vs diversity |
| date_filed | TEXT | ~5-8% | When case was filed |
| date_terminated | TEXT | ~5% | When case ended |
| date_last_filing | TEXT | ~5% | Most recent activity |
| assigned_to_str | TEXT | ~3% (13/416) | Assigned judge name |
| referred_to_str | TEXT | ~1% | Magistrate judge |
| jury_demand | TEXT | ~1% | Who demanded jury trial |
| idb_disposition | INTEGER | ~0% | FJC standardized outcome code |
| idb_judgment | INTEGER | ~0% | Plaintiff wins / defendant wins |
| idb_procedural_progress | INTEGER | ~0% | At which stage case resolved |
| idb_nature_of_suit | INTEGER | ~0% | Numeric NOS code |
| idb_monetary_demand | REAL | ~0% | Dollar amount sought |
| idb_pro_se | INTEGER | ~0% | Whether party had no lawyer |
| idb_class_action | INTEGER | ~0% | Class action flag |
| idb_origin | INTEGER | ~0% | Original filing vs transfer |
| idb_jury_demand | TEXT | ~0% | Who demanded jury |
| has_opinions | INTEGER | 100% | 1 if opinions were fetched |
| scrape_status | TEXT | 100% | "done" or "pending" |
| scraped_at | TEXT | 100% | Timestamp |

Note: idb_* fields (FJC data) have ~0% coverage on opinion-sourced cases. They exist on the ~6,800 metadata-only cases from PACER.

#### opinions (15 fields)

| Field | Type | Coverage | Description |
|---|---|---|---|
| opinion_id | INTEGER PK | 100% | Unique opinion identifier |
| docket_id | INTEGER FK | 100% | Links to cases table |
| cluster_id | INTEGER | 100% | Groups related opinions (majority + dissent) |
| plain_text | TEXT | 36% (149/416) | Full judicial analysis (avg 52K chars) |
| type | TEXT | 100% | "010combined" has text; others are metadata |
| author_str | TEXT | 67% (327/486) | Judge who wrote the opinion |
| per_curiam | INTEGER | 100% | Whether unanimous |
| download_url | TEXT | ~80% | Link to original PDF |
| cluster_date_filed | TEXT | ~95% | When opinion was issued |
| precedential_status | TEXT | ~95% | "Published" vs "Unpublished" |
| citation_count | INTEGER | 100% | How many other cases cite this one |
| syllabus | TEXT | ~5% | One-line topic summary |
| disposition | TEXT | ~10% | Court's disposition text |
| posture | TEXT | ~10% | Procedural posture |
| procedural_history | TEXT | ~10% | How the case got here |

#### citation_edges (3 fields)

| Field | Type | Description |
|---|---|---|
| id | INTEGER PK | Row ID |
| source_opinion_id | INTEGER | The opinion that cites another |
| cited_opinion_url | TEXT | URL of the cited opinion (parsed to opinion_id) |

#### parties (8 fields)

| Field | Type | Description |
|---|---|---|
| id | INTEGER PK | Row ID |
| docket_id | INTEGER FK | Links to cases |
| party_id | INTEGER | CourtListener party ID |
| name | TEXT | e.g., "Apple Inc." |
| party_type | TEXT | "Plaintiff", "Defendant", etc. |
| date_terminated | TEXT | When party was dismissed |
| criminal_counts | TEXT | JSON array of criminal charges |
| extra_info | TEXT | Additional info |

#### attorneys (9 fields)

| Field | Type | Description |
|---|---|---|
| id | INTEGER PK | Row ID |
| docket_id | INTEGER FK | Links to cases |
| attorney_id | INTEGER | CourtListener attorney ID |
| name | TEXT | Attorney name |
| contact_raw | TEXT | Full address, firm name |
| phone | TEXT | Phone number |
| fax | TEXT | Fax number |
| email | TEXT | Email address |
| roles | TEXT | JSON array of role codes |

#### docket_entries (6 fields)

| Field | Type | Description |
|---|---|---|
| id | INTEGER PK | Row ID |
| docket_id | INTEGER FK | Links to cases |
| entry_id | INTEGER | CourtListener entry ID |
| entry_number | INTEGER | Filing order |
| date_filed | TEXT | When filed |
| description | TEXT | e.g., "MOTION TO DISMISS — GRANTED" |

#### case_labels (11 fields — Phase 0.3 output)

| Field | Type | Description |
|---|---|---|
| id | INTEGER PK | Row ID |
| docket_id | INTEGER FK | Links to cases |
| opinion_id | INTEGER | Which opinion was labeled |
| outcome_label | TEXT | DEFENDANT_WINS / PLAINTIFF_WINS / MIXED / UNCLEAR / UNLABELED |
| procedural_stage | TEXT | MTD / SJ / TRIAL / APPEAL |
| contamination_type | TEXT | PRIVATE / SEC_ENFORCEMENT / DOJ_CRIMINAL / SEC_APPEAL |
| label_source | TEXT | conclusion_regex / fulltext_regex / no_opinion |
| label_confidence | REAL | 0.0-1.0 |
| matched_pattern | TEXT | Which regex pattern matched |
| matched_text | TEXT | Snippet of matched text |
| split | TEXT | train / val / test |

#### irac_extractions (9 fields — Phase 1 output)

| Field | Type | Description |
|---|---|---|
| id | INTEGER PK | Row ID |
| docket_id | INTEGER FK | Links to cases |
| opinion_id | INTEGER FK | Which opinion was extracted |
| extraction | TEXT | Full IRACExtraction as JSON blob |
| llm_model | TEXT | "llama-3.3-70b" or "mock" |
| llm_raw | TEXT | Raw LLM response (for debugging) |
| is_valid | INTEGER | 1 if passed Pydantic + rule validation |
| errors | TEXT | Validation error messages (JSON) |
| created_at | TEXT | Timestamp |

#### anco_hits_scores (5 fields — Phase 3 output)

| Field | Type | Description |
|---|---|---|
| id | INTEGER PK | Row ID |
| entity_type | TEXT | "case" or "argument" |
| entity_id | TEXT | docket_id (for cases) or text_hash (for arguments) |
| score | REAL | [-1, +1] ANCO-HITS score |
| created_at | TEXT | Timestamp |

#### opinion_embeddings (5 fields — Phase 5 output, on Sol)

| Field | Type | Description |
|---|---|---|
| opinion_id | INTEGER PK | Links to opinions |
| model_name | TEXT | "all-mpnet-base-v2" |
| embedding | BLOB | 768-dim float32 vector |
| text_chars | INTEGER | Length of embedded text |
| created_at | TEXT | Timestamp |

---

### Table 2: Which Fields Does Each Phase/Component Use?

| Phase | Primary Data Fields | Purpose |
|---|---|---|
| **Phase 0: Scraping** | All cases.* fields, opinions.*, citation_edges.*, parties.*, attorneys.*, docket_entries.* | Populate the database from CourtListener API |
| **Phase 0.3: Labeling** | opinions.plain_text, cases.case_name, cases.court_id | Regex outcome extraction → case_labels table |
| **Phase 1: Lifting** | opinions.plain_text, cases.case_name, cases.court_id, case_labels.procedural_stage | LLM reads opinion text → irac_extractions table |
| **Phase 2: Graph nodes** | cases.* (Case nodes), opinions.* (Opinion nodes), irac_extractions.extraction → statutes_cited, arguments (Statute/Argument nodes), opinions.author_str (Judge nodes), parties.name (Company nodes), attorneys.contact_raw (Firm nodes) | Build Neo4j knowledge graph |
| **Phase 2: Graph edges** | citation_edges.* (CITES), opinions.docket_id (HAS_OPINION), irac_extractions.extraction → arguments + outcome (INVOLVES with sign), cases.court_id + opinions.author_str (DECIDED_BY) | Create relationships |
| **Phase 3: ANCO-HITS** | irac_extractions.extraction → arguments_plaintiff/defendant + outcome (bipartite graph) | Score arguments and cases → anco_hits_scores table |
| **Phase 5: Embeddings** | opinions.plain_text (preprocessed) | SBERT encode → opinion_embeddings table |
| **Phase 5: Retrieval** | opinion_embeddings.embedding (semantic), Neo4j CITES/CHARGED_UNDER/DECIDED_BY edges (graph), anco_hits_scores.score (ranking) | Hybrid retrieval |
| **Phase 5: Constraints** | cases.case_name + docket_id (citation check), irac_extractions → statutes_cited (statute grounding), cases.court_id (binding authority), cases.date_filed (temporal validity), anco_hits_scores.score (ambiguity), irac_extractions → element statuses (missing elements) | 6 hard validators |
| **Phase 5: Lowering** | All retrieval outputs + irac_extractions (query case IRAC) | Context packing → LLM prompt |
| **Phase 7: Evaluation** | case_labels.split (train/val/test), case_labels.outcome_label, irac_extractions.extraction, anco_hits_scores.score | Metrics computation |

---

## What About Unlabeled Cases (No Opinion Text)?

The 273 unlabeled cases have no opinion text → no Phase 1 extraction possible.

**Where they help:**

| Phase | How |
|---|---|
| Phase 2 (Graph) | Case nodes with citation edges add density to the network — bridges between labeled cases |
| Phase 5 (Retrieval) | Graph traversal uses them as intermediate hops: Case A → cites → unlabeled Case B → cites → Case C |

**Where they DON'T help:**

| Phase | Why not |
|---|---|
| Phase 1 (Lifting) | No text → nothing to extract |
| Phase 3 (ANCO-HITS) | No IRAC → no arguments → not in bipartite graph |
| Phase 5 (Semantic search) | No text → no embedding → invisible to cosine similarity |
| Phase 7 (Evaluation) | No ground truth possible |

They're graph infrastructure, not analysis targets. They make the citation network denser. At 10,200 cases with 6,800 metadata-only nodes, they become the connective tissue of the knowledge graph.
