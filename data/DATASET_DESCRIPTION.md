# Private 10b-5 Dataset Description

## What is this dataset?

Private securities fraud cases filed by **investors against companies** under Rule 10b-5 (17 C.F.R. Section 240.10b-5), scraped entirely from the CourtListener API v4.

These are NOT SEC enforcement cases. In these cases, private plaintiffs (individual investors, pension funds, class action groups) sue public companies and their officers for securities fraud.

## Why Private 10b-5?

We evaluated several legal domains and chose Private 10b-5 for these reasons:

| Factor | Private 10b-5 | SEC Enforcement (rejected) | Title VII (considered) |
|--------|--------------|---------------------------|----------------------|
| **Outcome variety** | ~50/50 (MTD granted vs denied) | 76% consent judgments (data leakage) | ~50/50 |
| **Element clarity** | 6 well-defined elements | 3-4 elements (simpler but less interesting) | 4 elements + burden shift |
| **Opinion richness** | Judges write element-by-element analysis | Mostly settlements, no judicial analysis | Rich but different domain |
| **Citation network** | Avg 26.7 citations per opinion | Thin (settlements don't cite precedent) | Dense |
| **Domain relevance** | Securities fraud (lab focus) | Securities but trivial prediction task | Employment law |

The key problem with SEC enforcement: 76% of cases end in consent judgments (defendant settles without admitting wrongdoing). A model can achieve 76% accuracy by simply predicting "consent judgment" every time -- no reasoning needed. This is called **data leakage**: the outcome distribution itself leaks the answer, so the model learns "always predict the majority class" instead of learning actual legal reasoning. Private 10b-5 at ~50/50 forces the model to learn which elements determine the outcome.

## SEC Enforcement vs Private 10b-5: Same Statute, Different Standards

Both use the **same statute** (Section 10(b) + Rule 10b-5), but the conditions for winning are different:

| | SEC Enforcement | Private 10b-5 (this dataset) |
|---|---|---|
| **Who sues** | SEC (government agency) | Investors (individuals, pension funds, class actions) |
| **Elements to prove** | 3-4 | 6 |
| **Material Misrepresentation** | Required | Required |
| **Scienter** | Required | Required |
| **Connection to Securities** | Required | Required |
| **Reliance** | NOT required | Required |
| **Economic Loss** | NOT required | Required |
| **Loss Causation** | NOT required | Required |
| **Why the difference** | SEC represents the public interest -- doesn't need to prove individual investor harm | Private plaintiff must prove they personally relied on the fraud and it caused their specific financial loss |
| **Typical outcome** | 76% consent judgment (settlement) | ~50/50 MTD granted vs denied |
| **Judges write analysis?** | Rarely (cases settle before ruling) | Yes -- element-by-element opinions |

In short: SEC just proves "the fraud happened." Private plaintiffs must prove "the fraud happened AND I relied on it AND it caused MY loss." The extra 3 elements make Private 10b-5 harder to win but much richer for our symbolic pattern pipeline -- more elements means more things to extract, predict, and explain.

## The 6-Element Rule (Rule 10b-5)

For a plaintiff to win a private 10b-5 case, ALL 6 elements must be satisfied:

```
PlaintiffWins(case) <- MaterialMisrep ^ Scienter ^ Connection ^ Reliance ^ EconomicLoss ^ LossCausation
```

| Element | What must be proven | Most contested? |
|---------|-------------------|----------------|
| **Material Misrepresentation** | Defendant made false statements or misleading omissions | Common |
| **Scienter** | Defendant acted with intent to deceive (or recklessness) | Most contested -- 3,631 opinions challenge this |
| **Connection to Securities** | The fraud was in connection with purchase or sale of securities | Rarely disputed |
| **Reliance** | Plaintiff relied on the misrepresentation (or fraud-on-the-market presumption) | 4,026 opinions -- key in class actions |
| **Economic Loss** | Plaintiff suffered actual financial loss | Sometimes disputed |
| **Loss Causation** | The fraud (not other factors) caused the loss | 1,512 opinions |

Judges analyze these elements one by one in their opinions. This is the text we extract for symbolic pattern lifting.

## Dataset Files

### private_10b5_sample_200.db (4.4 MB)

A frozen sample of 200 golden cases for immediate testing and prototyping.

| Table | Rows | Description |
|-------|-----:|------------|
| `cases` | 200 | Docket metadata (case_name, court, dates, idb_data) |
| `opinions` | 255 | Opinion text + cluster metadata (95 with full text, avg 47K chars) |
| `citation_edges` | 7,196 | Which opinions cite which (avg 26.7 per opinion) |
| `parties` | 123 | Party names + roles (plaintiff, defendant, lead plaintiff) |
| `attorneys` | 143 | Attorney names + firms + contact info |
| `docket_entries` | 540 | Procedural filings (motions, orders, judgments) |

### private_10b5_cases.db (estimated ~200-400 MB when complete)

The full dataset, currently being scraped in tiers:

| Tier | Cases | Status |
|------|------:|--------|
| Golden 200 | 200 | Complete |
| All opinion cases | ~3,400 | Scraping (~5-6 hours) |
| Metadata-only cases | ~6,800 | Not yet started |
| **Total** | **~10,200** | |

Note: Not all cases have opinion text. The breakdown:

| Category | Est. Count | What they have | Useful for |
|----------|-----------|---------------|------------|
| Cases with opinion text | ~1,500-1,700 | Full judicial analysis + citations | Phase 1 lifting, Phase 2 graph, Phase 3 ANCO-HITS |
| Cases with opinion metadata only | ~1,700 | Citation edges but no text | Phase 2 graph, Phase 4 GraphSAGE |
| Metadata-only cases | ~6,800 | Parties, attorneys, FJC outcome codes | Phase 2 graph nodes, Phase 4 GraphSAGE features |

## Database Schema

### cases

| Column | Type | Source | Description |
|--------|------|--------|------------|
| docket_id | INTEGER PK | Docket | Unique case identifier in CourtListener |
| case_name | TEXT | Docket | e.g., "Smith v. XYZ Corp" |
| docket_number | TEXT | Docket | Court's official number (e.g., "1:23-cv-02456") |
| pacer_case_id | TEXT | Docket | PACER system ID |
| slug | TEXT | Docket | URL-safe case name |
| absolute_url | TEXT | Docket | CourtListener link for verification |
| court_id | TEXT | Docket | Which court (e.g., "nysd"). Critical for binding authority. |
| cause | TEXT | Docket | Statute basis (e.g., "15:78 Securities Exchange Act") |
| nature_of_suit | TEXT | Docket | Case category ("850 Securities/Commodities") |
| jurisdiction_type | TEXT | Docket | Federal question vs diversity |
| date_filed | TEXT | Docket | When case was filed |
| date_terminated | TEXT | Docket | When case ended (null = ongoing) |
| date_last_filing | TEXT | Docket | Most recent activity |
| assigned_to_str | TEXT | Docket | Judge name -- strong outcome predictor |
| referred_to_str | TEXT | Docket | Magistrate judge |
| jury_demand | TEXT | Docket | Who demanded jury trial |
| idb_disposition | INTEGER | FJC | Standardized outcome code (0-19) |
| idb_judgment | INTEGER | FJC | Plaintiff wins / defendant wins / both |
| idb_procedural_progress | INTEGER | FJC | At which stage case resolved |
| idb_nature_of_suit | INTEGER | FJC | Numeric NOS code |
| idb_monetary_demand | REAL | FJC | Dollar amount sought |
| idb_pro_se | INTEGER | FJC | Whether party had no lawyer |
| idb_class_action | INTEGER | FJC | Class action flag |
| idb_origin | INTEGER | FJC | Original filing vs transfer |
| idb_jury_demand | TEXT | FJC | Who demanded jury |
| has_opinions | INTEGER | Scraper | 1 if opinions were fetched |
| scrape_status | TEXT | Scraper | "done" or "pending" |
| scraped_at | TEXT | Scraper | Timestamp |

### opinions

| Column | Type | Source | Description |
|--------|------|--------|------------|
| opinion_id | INTEGER PK | Opinion | Unique opinion identifier |
| docket_id | INTEGER FK | Opinion | Links to cases table |
| cluster_id | INTEGER | Cluster | Groups related opinions (majority + dissent) |
| plain_text | TEXT | Opinion | **Full judicial analysis** (avg 47K chars). The primary data for symbolic lifting. |
| type | TEXT | Opinion | "010combined" has full text; "020lead"/"040dissent" are metadata pointers |
| author_str | TEXT | Opinion | Judge who wrote the opinion |
| per_curiam | INTEGER | Opinion | Whether unanimous |
| download_url | TEXT | Opinion | Link to original PDF |
| cluster_date_filed | TEXT | Cluster | When opinion was issued |
| precedential_status | TEXT | Cluster | "Published" (binding) vs "Unpublished" (persuasive only) |
| citation_count | INTEGER | Cluster | How many other cases cite this one |
| syllabus | TEXT | Cluster | One-line topic summary (often empty) |
| disposition | TEXT | Cluster | Court's disposition text (often empty) |
| posture | TEXT | Cluster | Procedural posture |
| procedural_history | TEXT | Cluster | How the case got here |

Note on opinion types: CourtListener stores the full text on `010combined` records. The `020lead`, `030concurrence`, `040dissent` records are metadata labels pointing to the same decision -- they have 0 chars of text. This is by design, not missing data.

### citation_edges

| Column | Type | Description |
|--------|------|------------|
| source_opinion_id | INTEGER | The opinion that cites another |
| cited_opinion_url | TEXT | URL of the cited opinion |

This table builds the citation graph for Phase 2 (Knowledge Graph) and Phase 4 (GraphSAGE).

### parties

| Column | Type | Description |
|--------|------|------------|
| docket_id | INTEGER FK | Links to cases table |
| party_id | INTEGER | CourtListener party ID |
| name | TEXT | e.g., "Apple Inc.", "John Smith" |
| party_type | TEXT | "Plaintiff", "Defendant", "Lead Plaintiff", "Relief Defendant" |
| date_terminated | TEXT | When this party was dismissed/settled |
| criminal_counts | TEXT | JSON array of parallel criminal charges |
| extra_info | TEXT | Additional party information from CourtListener |

### attorneys

| Column | Type | Description |
|--------|------|------------|
| docket_id | INTEGER FK | Links to cases table |
| attorney_id | INTEGER | CourtListener attorney ID |
| name | TEXT | Attorney name |
| contact_raw | TEXT | Full address, firm name, phone |
| phone | TEXT | Phone number |
| fax | TEXT | Fax number |
| email | TEXT | Email address |
| roles | TEXT | JSON array of role codes |

### docket_entries

| Column | Type | Description |
|--------|------|------------|
| docket_id | INTEGER FK | Links to cases table |
| entry_id | INTEGER | CourtListener entry ID |
| entry_number | INTEGER | Filing order |
| date_filed | TEXT | When filed |
| description | TEXT | Full text (e.g., "MOTION TO DISMISS for failure to plead scienter -- GRANTED") |

## Why These Data Fields?

Each field maps to a specific pipeline phase:

| Pipeline Phase | Key Fields Used |
|---------------|----------------|
| **Phase 1: Lifting** | `opinions.plain_text` (extract elements), `cases.cause` (identify statute) |
| **Phase 2: Knowledge Graph** | `citation_edges` (case-to-case links), `cases.assigned_to_str` (judge nodes), `parties.name` (defendant nodes) |
| **Phase 3: ANCO-HITS** | `opinions.plain_text` (extract arguments), outcome labels from text or `idb_judgment` |
| **Phase 4: GraphSAGE** | `citation_edges` (graph structure), `opinions.citation_count` (node feature), `idb_*` fields (node features) |
| **Phase 5: Constrained RAG** | `cases.court_id` (binding authority check), `opinions.precedential_status` (authority weight), `cases.absolute_url` (verification link) |
| **Phase 6: IRAC Output** | All of the above combined |

Statutes and legal rules are **not stored separately** -- they are extracted from `opinions.plain_text` during Phase 1 lifting. Judges cite specific statutes (e.g., "15 U.S.C. Section 78j(b)") and landmark precedents (e.g., "Tellabs v. Makor") in their opinions.

## Data Coverage Reality

Not all 48 planned fields have good coverage. Here is what the opinion-sourced cases actually have:

| Field Group | Coverage | Notes |
|-------------|---------|-------|
| case_name, court_id, docket_number | ~100% | Always available |
| Opinion plain_text | ~47% of opinions | `010combined` type has 88% fill rate |
| opinions_cited (citation edges) | ~99% of cases | Rich network, avg 26.7 per opinion |
| precedential_status, citation_count | ~100% | Always on cluster |
| date_filed, date_terminated | ~5-8% | Most opinion-sourced dockets lack dates |
| cause, jurisdiction_type | ~0-1% | Not populated for opinion-sourced cases |
| parties, attorneys | ~6% | Only cases with PACER data |
| idb_data (all FJC fields) | ~0% for opinion cases | FJC data exists on PACER-sourced dockets (the ~6,800 metadata-only cases) |

The primary data for the pipeline is `opinions.plain_text` + `citation_edges`. The sparse metadata fields become more useful when the full ~10,200 dataset is complete (the ~6,800 metadata-only cases have 90%+ coverage on parties, attorneys, and idb_data).

## Data Source

All data scraped from CourtListener REST API v4 (https://www.courtlistener.com/api/rest/v4/) using an EDU-tier account. No PACER fees. No RECAP documents (poor coverage, quarterly updates).

Scraper: `script/scraper_private_10b5.py`

## Quick Start

```python
import sqlite3

conn = sqlite3.connect("private_10b5_sample_200.db")

# Get all cases
cases = conn.execute("SELECT docket_id, case_name, court_id FROM cases").fetchall()

# Get opinions with full text (the core data for Phase 1)
opinions = conn.execute("""
    SELECT o.opinion_id, o.docket_id, c.case_name, length(o.plain_text) as text_len
    FROM opinions o
    JOIN cases c ON o.docket_id = c.docket_id
    WHERE o.plain_text IS NOT NULL AND length(o.plain_text) > 100
""").fetchall()

# Get citation network
edges = conn.execute("SELECT source_opinion_id, cited_opinion_url FROM citation_edges").fetchall()

print(f"Cases: {len(cases)}")
print(f"Opinions with text: {len(opinions)}")
print(f"Citation edges: {len(edges)}")

conn.close()
```
