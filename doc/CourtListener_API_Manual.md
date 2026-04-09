# CourtListener REST API v4 — Operational Manual

> Purpose: A reference document to give Claude (or Claude Code) so it knows
> exactly **what the CourtListener API is, how to call it, what data each
> endpoint returns, and what its limits are**. Optimized for the S²NS / SCAC
> securities-fraud KG pipeline, but generally applicable.
>
> Source of truth: https://www.courtlistener.com/help/api/rest/ (v4.3, fetched Apr 2026)
> Maintained by: Zen (wwang360@asu.edu)

---

## 0. TL;DR for Claude

- Base URL: `https://www.courtlistener.com/api/rest/v4/`
- Auth: HTTP header `Authorization: Token <COURTLISTENER_API_TOKEN>`
- Format: JSON by default. Always send `Accept: application/json`.
- Rate limit: **5,000 authenticated requests / hour**. Anonymous = much lower.
- Pagination: cursor-based via `next` / `previous` keys when ordering by `id`,
  `date_modified`, or `date_created`. Do **not** use `?page=` for deep paging.
- The single most important optimization: use `fields=` / `omit=` to avoid
  pulling huge text blobs (`plain_text`, `html_with_citations`).
- Discovery: `curl -X OPTIONS <endpoint>` returns the full schema, available
  filters, ordering keys, and choices for any endpoint. **Use OPTIONS first
  before guessing field names.**
- **PACER endpoints** (`docket-entries`, `recap-documents`, `parties`,
  `attorneys`, `recap-query`) are **gated** — they require a paid membership +
  manual access grant from Free Law Project. Free-tier accounts get HTTP 403.
- **There is NO statute-citation endpoint.** Citation Network only tracks
  case→case citations. To get the statutes a case relies on, you must fetch
  the opinion text and run Eyecite yourself.

---

## 1. Authentication

### 1.1 Token (recommended)
```bash
curl "https://www.courtlistener.com/api/rest/v4/clusters/" \
  --header "Authorization: Token $COURTLISTENER_API_TOKEN"
```
The literal word `Token` must precede the token, separated by whitespace.
Forgetting it is the #1 cause of silent throttling — your code gets treated as
anonymous and rate-limited far more aggressively.

### 1.2 Debugging auth/throttling
1. Browse the API logged in via the website → if it works there but your code
   fails, your header is malformed.
2. Check usage: https://www.courtlistener.com/profile/api/#usage
3. One account per project/person/org. Multiple accounts = ToS violation.

---

## 2. Discovery: how to learn any endpoint

Always start by sending an `OPTIONS` request. It returns:
- All field names + types
- All filters with their `lookup_types` (exact, gte, lt, range, in, etc.)
- All `ordering` keys
- All `choices` for enum fields

```bash
curl -X OPTIONS \
  --header "Authorization: Token $COURTLISTENER_API_TOKEN" \
  "https://www.courtlistener.com/api/rest/v4/dockets/" | jq '.filters'
```

**Rule for Claude: if you are unsure about a field name, run OPTIONS first.
Do not guess.**

---

## 3. Filtering grammar

CourtListener uses Django-style double-underscore lookups.

| Pattern | Meaning | Example |
|---|---|---|
| `field=value` | exact match | `court=scotus` |
| `field__gt=` / `__lt=` / `__gte=` / `__lte=` | range | `id__gt=500` |
| `field__range=a,b` | inclusive range | `id__range=500,1000` |
| `field__in=a,b,c` | set membership | `pacer_doc_id__in=04505578698,04505578717` |
| `field__startswith=` | prefix | `court__full_name__startswith=district` |
| `!field=value` | exclusion (NOT) | `court__jurisdiction!=F` |
| `related__field=` | join across related models | `cluster__docket__court=scotus` |
| `count=on` | return only `{"count": N}` | useful for previewing query size |

**RelatedFilter chains** are how you express joins. Example: opinions belong
to clusters, which belong to dockets, which belong to courts. So to get all
SCOTUS opinions in one shot:

```
/api/rest/v4/opinions/?cluster__docket__court=scotus
```

Dates must be ISO-8601 (`YYYY-MM-DD`).

**Performance tip**: prefer `court=scotus` over `court__id=scotus`. The latter
introduces an extra SQL join.

---

## 4. Field selection (critical for performance)

Use `fields=` to whitelist or `omit=` to blacklist. Double-underscore for
nested fields.

```bash
# Only return id and date_modified
?fields=id,date_modified

# Only return nested educations.id + educations.date_modified
?fields=educations__id,educations__date_modified

# Strip the giant plain_text blob from opinions
?omit=plain_text,html,html_lawbox,html_columbia,xml_harvard
```

**For S²NS ingestion this matters a lot**: a single opinion can be hundreds of
KB of HTML. If you only need metadata + `html_with_citations`, omit everything
else.

---

## 5. Pagination

- Default: `next` / `previous` cursor URLs in the response. Just follow them.
- The `?page=N` parameter exists but caps at **100 pages**. Do not use it for
  deep pagination.
- Deep pagination only works when ordering by `id`, `date_modified`, or
  `date_created`. (Plus `date_completed` on `recap-fetch`.)
- Tie-break non-unique sort keys: `?order_by=date_filed,id`.

---

## 6. Counting without fetching

```bash
?count=on
```
Returns just `{"count": N}`. Pagination is ignored. Use this to size queries
before committing to them.

In normal paginated responses, the `count` key is itself a URL — follow it to
get the total.

---

## 7. The data model (case law side)

Four core objects, top-down:

```
Court  ─┐
        └── Docket  ─┐
                     └── OpinionCluster  ─┐
                                          └── Opinion (text lives here)
```

| Object | Holds | Endpoint |
|---|---|---|
| `Court` | court ID, name, jurisdiction, founding date | `/courts/` |
| `Docket` | docket number, case name, dates, judge, PACER case ID | `/dockets/` |
| `OpinionCluster` | groups majority + dissent + concurrence; citations; panel | `/clusters/` |
| `Opinion` | actual decision text in multiple formats; cited opinions | `/opinions/` |

**Why clusters exist**: a single decision can produce a majority opinion + a
dissent + a concurrence. They share metadata (panel, date, citation), so the
shared metadata lives on the cluster, while each individual opinion text lives
on its own `Opinion` object.

**Case name nuance**: `Docket.case_name` can change as parties change (e.g. an
official defendant resigns mid-case). `Cluster.case_name` is frozen at
decision time. Use the cluster name for citation; use the docket name for
"current" identification.

---

## 8. The data model (PACER / RECAP side)

```
Court ── Docket ── DocketEntry ── RECAPDocument (PDFs + extracted text)
                ├── Party ── Attorney
                └── (Bankruptcy / OriginatingCourt / IDB metadata)
```

| Object | Holds | Endpoint | Gated? |
|---|---|---|---|
| `Docket` | shared with case-law side | `/dockets/` | No |
| `DocketEntry` | one row of the PACER docket sheet; has nested documents | `/docket-entries/` | **Yes** |
| `RECAPDocument` | the actual filed PDF + OCR'd text | `/recap-documents/` | **Yes** |
| `Party` | plaintiff / defendant / trustee with role info | `/parties/` | **Yes** |
| `Attorney` | lawyer record + which parties they represented | `/attorneys/` | **Yes** |
| `OriginatingCourtInfo` | for appeals: lower-court info | `/originating-court-information/` | No |
| `BankruptcyInformation` | chapter, trustee, key dates | `/bankruptcy-information/` | No |
| `FJC IDB` | Federal Judicial Center metadata | `/fjc-integrated-database/` | No (experimental) |
| `RECAP Query` | bulk doc-availability check by `pacer_doc_id` | `/recap-query/` | **Yes** |

> "Gated" = endpoint returns 403 unless Free Law Project has manually granted
> your token write/access permission. Tier 1 membership alone is **not enough**
> — you must also email FLP and request access. This is a known pain point.

---

## 9. Endpoint reference

### 9.1 `/courts/`
- Mostly static. Cache aggressively.
- Court IDs follow PACER subdomains, with a few exceptions:
  | PACER | CL |
  |---|---|
  | `azb` | `arb` |
  | `cofc` | `uscfc` |
  | `neb` | `nebraskab` |
  | `nysb-mega` | `nysb` |

### 9.2 `/dockets/`
Top-level case object. Free to access.

Key fields:
- `id` — CourtListener docket ID
- `court_id` — e.g. `cand`, `nysd`, `dcd`
- `docket_number` — e.g. `1:16-cv-00745`
- `docket_number_core` — normalized form, e.g. `1600745`
- `pacer_case_id` — PACER's internal ID
- `case_name`, `case_name_full`, `case_name_short`
- `date_filed`, `date_terminated`, `date_last_filing`
- `cause` — e.g. `28:1346 Tort Claim`
- `nature_of_suit` — e.g. `Other Statutory Actions`. **For SCAC, look for
  "Securities/Commodities/Exchange" (NOS code 850).**
- `assigned_to` / `assigned_to_str` — judge link / fallback name string
- `referred_to` / `panel`
- `clusters` — list of cluster URLs (case-law decisions tied to this docket)
- `filepath_ia`, `filepath_ia_json` — Internet Archive RECAP mirror URLs
  (these work without auth and are how IA RECAP free access happens)

⚠️ A docket response **does not** inline its docket entries, parties, or
attorneys. You must query those endpoints separately filtered by `docket=<id>`.

### 9.3 `/clusters/`
Groups opinions for a single decision.

Key fields:
- `id` — used in CourtListener case URLs (`/opinion/<id>/<slug>/`)
- `docket` — link back to docket
- `sub_opinions` — list of `/opinions/` URLs (majority, dissent, concurrence)
- `citations` — parallel citations (e.g. `557 U.S. 305`, `129 S. Ct. 2504`)
- `judges`, `panel`, `non_participating_judges` — mix of strings and `/people/` links
- `date_filed`, `precedential_status`, `citation_count`

### 9.4 `/opinions/`
The actual decision text + per-opinion metadata.

Key fields:
- `cluster` — back-link
- `type` — `010combined`, `020lead`, `030concurrence`, `040dissent`, etc.
  Numeric prefixes sort by priority.
- `author` / `joined_by`
- `download_url` — original scrape URL (often dead, do not rely on)
- `local_path` — path to the binary on CL storage
- **`opinions_cited`** — list of other `/opinions/` URLs cited by this one.
  This is the case→case citation graph signal.
- `ordering_key` — sub-opinion order within a cluster (only for Harvard /
  Columbia sourced data)

**Text fields, in order of preference:**
1. **`html_with_citations`** ← always prefer this. Citations are linked. Used
   on the CL website itself.
2. `xml_harvard` — from Harvard CAP. OCR'd, sometimes imperfect.
3. `html_columbia`, `html_lawbox`, `html_anon_2020`, `html` — varies by source
4. `plain_text` — fallback when only PDF/Word was available

For ingestion, request **only** `html_with_citations` plus metadata, and
`omit` the rest.

### 9.5 `/docket-entries/` (gated)
A row on the PACER docket sheet. Each entry contains 1+ `RECAPDocument` items
nested in `recap_documents`.

```bash
curl --header "Authorization: Token $TOKEN" \
  "https://www.courtlistener.com/api/rest/v4/docket-entries/?docket=4214664"
```

Key fields:
- `entry_number` — usually the visible PACER doc number
- `description` — entry description (the longer one)
- `recap_sequence_number`, `pacer_sequence_number` — use these for sorting,
  not `entry_number`, since some courts skip numbers
- `recap_documents` — nested list (can be huge — use `omit=recap_documents__plain_text`)

Orderable by: `id`, `date_created`, `date_modified`, `date_filed`,
`recap_sequence_number`, `entry_number`.

### 9.6 `/recap-documents/` (gated)
The actual PDF documents.

Key fields:
- `plain_text` — extracted text via Doctor (Tesseract OCR fallback)
- `ocr_status` — tells you whether the text came from native PDF or OCR
- `filepath_local` — relative path on CL storage; combine with the storage
  base URL to download the PDF
- `is_available` — whether the PDF itself is in RECAP
- `pacer_doc_id` — PACER's document ID (note: 4th digit is normalized to 0)

### 9.7 `/parties/` (gated)
Returns parties + nested attorneys for a docket.

```bash
?docket=4214664
?docket=4214664&filter_nested_results=True   # also filters nested attorneys
```

⚠️ **Default behavior gotcha**: filtering by docket only filters the top
level. Each returned party still lists every attorney that party ever had
across every case. Add `filter_nested_results=True` to scope nested data.

Key fields:
- `name`
- `party_types[]` — role per docket: `Plaintiff`, `Defendant`, `Trustee`, etc.
  For criminal cases also includes `criminal_counts`, `criminal_complaints`,
  and `highest_offense_level_*`.
- `attorneys[]` — nested attorney records with role codes

### 9.8 `/attorneys/` (gated)
Same nested-filter gotcha applies. Includes `parties_represented[]` and
contact info (`contact_raw`, `phone`, `fax`, `email`).

### 9.9 `/recap-query/` (gated)
Fast bulk lookup: "do you have these specific PACER documents?"
```
?docket_entry__docket__court=dcd&pacer_doc_id__in=04505578698,04505578717
```
Returns up to 300 results, each with `pacer_doc_id`, `filepath_local`, `id`.
Use this before issuing Pray-and-Pay requests.

### 9.10 Other relevant endpoints

| Endpoint | Use |
|---|---|
| `/search/?type=o&q=...` | Full-text Solr search across opinions. Supports `type=o` (opinions), `type=r` (RECAP), `type=oa` (oral args), `type=p` (people). The only way to do fuzzy / phrase search. |
| `/citation-lookup/` | POST a citation string or block of text → get matching CL opinions. Anti-hallucination tool. |
| `/opinions-cited/` | The case→case citation graph (case law only). |
| `/people/` | Judge biographical records. |
| `/financial-disclosures/` | Federal judge financial disclosures. |
| `/audio/` | Oral argument recordings. |
| `/recap-fetch/` | Programmatically purchase a PACER doc (costs $$, gated). |
| `/alerts/`, `/docket-alerts/` | Email/webhook alerts. |
| `/tags/` | Organize dockets into named collections. |

---

## 10. Important things the API does **NOT** have

This section exists because Claude tends to confidently invent endpoints.
**These do not exist:**

1. **No statute-citation endpoint.** `opinions-cited` only links opinions to
   other opinions. To get statutes used by a case (e.g. `15 U.S.C. § 78j(b)`,
   `17 C.F.R. § 240.10b-5`), you must:
   - fetch the opinion's `html_with_citations` or `plain_text`, then
   - run Eyecite locally:
     ```python
     from eyecite import get_citations
     from eyecite.models import FullLawCitation, FullJournalCitation
     cites = get_citations(text)
     statutes = [c for c in cites if isinstance(c, FullLawCitation)]
     ```
2. **No "rulings on motions" endpoint.** Motion outcomes are buried in
   docket-entry descriptions and document text. Extraction is your job.
3. **No structured "holding" or "issue" extraction.** That's what your S²NS
   pipeline is for.
4. **No Westlaw / Lexis content.** And per *Thomson Reuters v. Ross
   Intelligence* (D. Del. 2025), do not try to bridge from those sources.
5. **No bulk PDF download endpoint.** Use `filepath_ia` (Internet Archive
   RECAP mirror) for free PDF access; that path goes through `archive.org`,
   not CourtListener.
6. **`page=` paginates only the first 100 pages.** Use cursor pagination.
7. **Case-name search is fuzzy in the search API, not the filter API.** The
   `dockets/?case_name=...` filter is exact-match. Use `/search/?type=r&q=...`
   for fuzzy.

---

## 11. Rate limits and etiquette

- **5,000 requests/hour** authenticated. Plan accordingly.
- Maintenance window: **Thursdays 21:00–23:59 PT**. Schedule cron around it.
- Bulk-processing windows are listed on the public Google Calendar linked
  from the API docs page.
- For >5K queries/hour or large bulk needs, contact FLP — they offer paid
  bulk data services rather than rate-limit hikes.

---

## 12. Cookbook (S²NS-relevant patterns)

### 12.1 Find all securities-fraud dockets in SDNY filed after 2010
```bash
curl --header "Authorization: Token $TOKEN" \
  "https://www.courtlistener.com/api/rest/v4/dockets/?court=nysd&nature_of_suit=850&date_filed__gte=2010-01-01&fields=id,case_name,docket_number,date_filed,assigned_to_str&count=on"
```
Then drop `count=on` and follow `next` cursors to actually paginate.

NOS code `850` = "Securities/Commodities/Exchange". Confirm via OPTIONS that
the filter accepts the numeric code vs string label — some endpoints want one
or the other.

### 12.2 Get a docket + all its opinions in one chain
```bash
# 1. Get docket
DOCKET_URL="https://www.courtlistener.com/api/rest/v4/dockets/4214664/"
curl -H "Authorization: Token $TOKEN" "$DOCKET_URL"
# → grab clusters[]

# 2. For each cluster URL, GET it
curl -H "Authorization: Token $TOKEN" "https://.../clusters/9502621/"
# → grab sub_opinions[]

# 3. For each opinion URL, GET only the fields you need
curl -H "Authorization: Token $TOKEN" \
  "https://.../opinions/9969234/?fields=id,type,author,html_with_citations,opinions_cited"
```

### 12.3 Extract the case→case citation graph for a corpus
For each opinion you have, the `opinions_cited` field is already a list of
`/opinions/` URLs. You don't need a separate API call — those URLs are the
edges of your citation graph. Resolve the linked opinions in batches.

### 12.4 Resolve a CourtListener URL like `/opinion/2812209/obergefell-v-hodges/`
The number in the URL is a **cluster ID**, not an opinion ID:
```bash
curl -H "Authorization: Token $TOKEN" \
  "https://www.courtlistener.com/api/rest/v4/clusters/2812209/"
```

### 12.5 Fuzzy docket-number search across all federal courts
```bash
curl -H "Authorization: Token $TOKEN" \
  "https://www.courtlistener.com/api/rest/v4/search/?type=r&q=docketNumber:1:16-cv-00745"
```

### 12.6 Statute extraction (NOT directly supported — local Eyecite)
```python
import requests
from eyecite import get_citations
from eyecite.models import FullLawCitation

H = {"Authorization": f"Token {TOKEN}"}
r = requests.get(
    "https://www.courtlistener.com/api/rest/v4/opinions/9969234/",
    params={"fields": "id,plain_text,html_with_citations"},
    headers=H,
).json()
text = r.get("plain_text") or r.get("html_with_citations") or ""
statutes = [c.corrected_citation() for c in get_citations(text)
            if isinstance(c, FullLawCitation)]
```

### 12.7 Check whether RECAP already has a PDF before paying for it
```bash
curl -H "Authorization: Token $TOKEN" \
  "https://www.courtlistener.com/api/rest/v4/recap-query/?docket_entry__docket__court=nysd&pacer_doc_id__in=12345678901,12345678902"
```
Returns one row per document found. Anything missing, you'd need to
Pray-and-Pay or purchase via `recap-fetch`.

---

## 13. Error handling cheatsheet

| Status | Meaning | Fix |
|---|---|---|
| 401 | Missing or malformed `Authorization` header | Check that the literal word `Token` precedes the token |
| 403 | Endpoint is gated and your token lacks permission | Email FLP from your account email; mention the endpoint |
| 404 | Object ID doesn't exist (or you stripped it via `fields=`) | Verify with a wider `fields=` |
| 429 | Rate-limited (5K/hour) | Back off; implement exponential retry; check you're really authenticated |
| 5xx | CL is down or in maintenance | Retry after Thursday 21–24 PT window |

---

## 14. Things specifically relevant to Zen / S²NS

- **Account**: `wwang360` (wwang360@asu.edu). Tier 1 membership active. Open
  ticket with FLP to (a) consolidate the duplicate account and (b) enable the
  gated PACER endpoints (`docket-entries`, `recap-documents`, `parties`,
  `attorneys`, `recap-query`).
- For SCAC complaint PDFs specifically: CL's metadata coverage is broad, but
  the **complaint PDF** itself depends on whether someone ran the RECAP
  browser extension on it. Expect gaps. Check `is_available` before assuming
  text extraction will work.
- The free **Internet Archive RECAP mirror** (`filepath_ia` /
  `filepath_ia_json` on dockets) is the back-door for free PDF access. Use it
  in parallel with CL's API to fill gaps without burning $$$.
- For statute extraction, do not try to make CL do it. Run Eyecite in your
  ingestion pipeline and store statute citations as their own typed nodes
  (`Statute`) with `cites_statute` edges in Neo4j.
- For HeteroConv input, the natural typed-edge set from CL data is:
  `(Case)-[FILED_IN]->(Court)`,
  `(Case)-[ASSIGNED_TO]->(Judge)`,
  `(Case)-[CITES]->(Case)` (from `opinions_cited`),
  `(Case)-[REPRESENTED_BY]->(Attorney)` (gated, from `/parties/`),
  `(Case)-[AGAINST]->(Party)` (gated),
  `(Case)-[CITES_STATUTE]->(Statute)` (your Eyecite output, not CL).

---

## 15. Quick reference card

```
BASE = https://www.courtlistener.com/api/rest/v4
AUTH = -H "Authorization: Token $TOKEN"

# Discover
curl -X OPTIONS $AUTH $BASE/<endpoint>/

# Free / open endpoints
$BASE/courts/
$BASE/dockets/
$BASE/clusters/
$BASE/opinions/
$BASE/search/?type=o|r|oa|p&q=...
$BASE/citation-lookup/
$BASE/opinions-cited/
$BASE/people/
$BASE/financial-disclosures/
$BASE/audio/
$BASE/originating-court-information/
$BASE/bankruptcy-information/
$BASE/fjc-integrated-database/
$BASE/alerts/  $BASE/docket-alerts/  $BASE/tags/

# GATED (need FLP grant)
$BASE/docket-entries/
$BASE/recap-documents/
$BASE/parties/
$BASE/attorneys/
$BASE/recap-query/
$BASE/recap-fetch/    # costs money

# Common GET param toolbox
?fields=a,b,c
?omit=plain_text,html
?count=on
?order_by=-date_modified,id
?<field>__gte=YYYY-MM-DD
?<field>__in=a,b,c
?!<field>=value           # exclusion
?filter_nested_results=True   # parties / attorneys only
```
