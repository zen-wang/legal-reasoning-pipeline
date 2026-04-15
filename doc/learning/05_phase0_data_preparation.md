# Topic 5: Phase 0 — Data Preparation

## What Phase 0 Does

Three sub-phases:
- **0.1**: Scrape Private 10b-5 cases from CourtListener API
- **0.2**: Split into train/val/test
- **0.3**: Label outcomes from opinion text

## 0.1 Scraping

**Data source**: CourtListener REST API v4 — a free legal database with court opinions, citations, and metadata. EDU-tier account (free, 5,000 requests/hour).

**Why CourtListener?**

| Source | Cost | Opinion text? | Citations? | Why not? |
|---|---|---|---|---|
| PACER | $0.10/page | Yes | No | Expensive at scale |
| SEC EDGAR | Free | No (only filings) | No | Has SEC filings, not court opinions |
| CourtListener | Free (EDU) | Yes | Yes (opinions_cited) | **Best fit** — free, has text + citations |

**Scraping order per case** (6 API calls):
1. Search dockets → get `docket_id`
2. Fetch docket details (case metadata)
3. Fetch opinion clusters → get `cluster_id`
4. Fetch opinions (plain_text + opinions_cited)
5. Fetch parties
6. Fetch attorneys + docket entries

**Engineering decisions**:
- **Async scraping** with `aiohttp` — parallel API calls within rate limits
- **SQLite with checkpoint/resume** — if scraper crashes, resumes from where it stopped
- **Discovery caching** — `discovered_dockets` table stores docket IDs so restarts don't re-paginate ~170 API pages
- **Rate limiting**: 5,000 req/hour with 1.5s intervals, 60s cooldown on HTTP 429

**Result**: 416 cases with 6 tables (cases, opinions, citation_edges, parties, attorneys, docket_entries).

## 0.2 + 0.3 Outcome Labeling

**The problem**: Pipeline plan assumed FJC metadata (`idb_judgment`, `idb_disposition`) for labels. These have **~0% coverage** on opinion-sourced cases — they only exist on PACER-sourced dockets.

**The solution**: Extract outcomes from **opinion text** using regex.

**Two-pass regex approach**:

```
Pass 1: Scan last 2,000 chars (conclusion region)
  → MIXED patterns first ("granted in part and denied in part")
  → DEFENDANT_WINS ("motion to dismiss is granted", "complaint dismissed")
  → PLAINTIFF_WINS ("motion to dismiss is denied", "reversed and remanded")
  → Standalone "AFFIRMED" → disambiguate by checking full text

Pass 2: If Pass 1 found nothing, scan full text with same patterns
  → Lower confidence (multiplied by 0.8)

If nothing → UNCLEAR
If no opinion text → UNLABELED
```

14 regex patterns total — 4 MIXED, 5 DEFENDANT_WINS, 5 PLAINTIFF_WINS, plus AFFIRMED disambiguator.

**The AFFIRMED problem**: Appellate "AFFIRMED" could mean:
- Affirming a dismissal → DEFENDANT_WINS
- Affirming a denial of dismissal → PLAINTIFF_WINS

Disambiguator checks full text for what the lower court did.

**Contamination detection**: Regex on `case_name`:
- "SEC v. ..." → SEC_ENFORCEMENT
- "United States v. ..." → DOJ_CRIMINAL
- "... v. SEC" → SEC_APPEAL

**Stratified split**: 70/15/15, stratified by outcome, only private + labeled + confidence ≥ 0.5.

**Result**:

| Category | Count |
|---|---|
| Labeled (DEF/PLT/MIXED) | 93 |
| UNCLEAR (text but no signal) | 40 |
| UNLABELED (no text) | 273 |
| Contaminated (excluded) | 53 |
| Train / Val / Test | 65 / 14 / 14 |

## Code Files

| File | Role |
|---|---|
| `script/scraper_private_10b5.py` | Async CourtListener API scraper with checkpoint/resume. Handles pagination, rate limiting, crash recovery. Produces 6 SQLite tables. |
| `script/label_and_split.py` | Regex outcome labeler + stratified train/val/test splitter. Two-pass regex on opinion text. Contamination detection. Produces `case_labels` table. |

## Key Decisions

| Decision | Why |
|---|---|
| Regex labeling instead of metadata | FJC metadata has ~0% coverage on opinion-sourced cases |
| Two-pass (conclusion first, then full text) | Conclusion section has the ruling; full text is fallback |
| Separate `case_labels` table | Never modify source data — labels are a layer on top |
| Confidence scores on labels | Some patterns more reliable (0.9) than others (0.5 for disambiguated AFFIRMED) |
| Idempotent script (DROP + CREATE) | Safe to re-run without accumulating duplicates |

## Future Improvement: Smarter Conclusion Detection

Current Pass 1 uses "last 2,000 chars" as a heuristic for the conclusion region. From exploration data, 96% of opinions have a labeled CONCLUSION section heading.

**Better approach for the 5,855-case re-run:**

```
Pass 1a: Use split_sections() from Phase 1 preprocessor to find actual
         CONCLUSION section → parse that entire section
Pass 1b: If no CONCLUSION heading found → fall back to last 2,000 chars
Pass 2:  Full text fallback (same as now)
```

`preprocess.py` (Phase 1) already detects CONCLUSION headings via regex on ALL-CAPS lines. The Phase 0 labeler was written before the preprocessor existed, so it uses the simpler heuristic. Reusing `split_sections()` would give better accuracy for the 96% that have CONCLUSION headings, with the same fallback for the 4% that don't.

## Elevator Pitch

"Phase 0 scrapes Private 10b-5 cases from CourtListener and labels outcomes. The tricky part was that metadata-based labels have zero coverage, so we built a regex labeler that reads the conclusion section of each opinion — patterns like 'motion to dismiss is granted' → DEFENDANT_WINS. We got 93 labeled cases with a stratified train/val/test split. The scraper is crash-safe with checkpoint/resume."
