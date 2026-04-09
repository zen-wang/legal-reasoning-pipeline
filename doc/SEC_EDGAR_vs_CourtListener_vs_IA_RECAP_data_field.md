# SEC EDGAR vs CourtListener vs IA RECAP — Comprehensive Data Field Comparison

> Generated 2026-04-05. All three sources compared for securities fraud litigation research.
> - **SEC EDGAR**: 39 structured enforcement fields (scraped from SEC litigation releases)
> - **CourtListener (CL)**: API v4 with EDU-tier access (9 endpoints: dockets, docket-entries, recap-documents, parties, attorneys, clusters, opinions, recap-query, oral-arguments)
> - **IA RECAP**: Internet Archive RECAP collection (PACER docket JSON + document metadata; parties/attorneys often sparse)

## Availability Legend

| Symbol | Meaning |
|--------|---------|
| YES | Field is reliably available |
| PARTIAL | Field exists but often empty, unreliable, or requires derivation |
| NO | Field not available from this source |

---

## A. Case Identification

| # | Field | SEC EDGAR | CL | IA RECAP | CL Source | IA RECAP Source |
|---|-------|:---------:|:--:|:--------:|-----------|-----------------|
| 1 | `case_title` | YES | YES | YES | D `case_name` | `case_name` |
| 2 | `docket_number` | NO | YES | YES | D `docket_number` | `docket_number` |
| 3 | `docket_number_core` | NO | YES | NO | D `docket_number_core` | — |
| 4 | `pacer_case_id` | NO | YES | YES | D `pacer_case_id` | `pacer_case_id` |
| 5 | `citation` | YES | NO | NO | — | — |
| 6 | `slug` | NO | YES | NO | D `slug` | — |
| 7 | `source_url` | YES | YES | YES | D `absolute_url` | `absolute_url` / IA URL |

## B. Court & Jurisdiction

| # | Field | SEC EDGAR | CL | IA RECAP | CL Source | IA RECAP Source |
|---|-------|:---------:|:--:|:--------:|-----------|-----------------|
| 8 | `court` | YES | YES | YES | D `court_id`, `court` | `court` |
| 9 | `jurisdiction_type` | NO | YES | YES | D `jurisdiction_type` | `jurisdiction_type` |
| 10 | `federal_dn_case_type` | NO | YES | NO | D `federal_dn_case_type` | — |
| 11 | `federal_dn_office_code` | NO | YES | NO | D `federal_dn_office_code` | — |
| 12 | `federal_dn_judge_initials` | NO | YES | NO | D `federal_dn_judge_initials_assigned` | — |
| 13 | `appeal_from` | NO | YES | NO | D `appeal_from_str` | — |
| 14 | `mdl_status` | NO | YES | NO | D `mdl_status` | — |

## C. Dates & Timeline

| # | Field | SEC EDGAR | CL | IA RECAP | CL Source | IA RECAP Source |
|---|-------|:---------:|:--:|:--------:|-----------|-----------------|
| 15 | `date` (filing/release) | YES | YES | YES | D `date_filed` | `date_filed` |
| 16 | `complaint_filed_date` | YES | YES | YES | D `date_filed` | `date_filed` |
| 17 | `judgment_date` | YES | YES | YES | D `date_terminated` | `date_terminated` |
| 18 | `date_terminated` | NO | YES | YES | D `date_terminated` | `date_terminated` |
| 19 | `date_last_filing` | NO | YES | YES | D `date_last_filing` | `date_last_filing` |
| 20 | `date_created` | NO | YES | NO | D `date_created` | — |
| 21 | `date_modified` | NO | YES | NO | D `date_modified` | — |
| 22 | `scheme_start_date` | YES | NO | NO | — | — |
| 23 | `scheme_end_date` | YES | NO | NO | — | — |

## D. Judges

| # | Field | SEC EDGAR | CL | IA RECAP | CL Source | IA RECAP Source |
|---|-------|:---------:|:--:|:--------:|-----------|-----------------|
| 24 | `judges` | YES | YES | YES | D `assigned_to_str`, `referred_to_str`; CL `judges` | `assigned_to_str` |

## E. Parties & Roles

| # | Field | SEC EDGAR | CL | IA RECAP | CL Source | IA RECAP Source |
|---|-------|:---------:|:--:|:--------:|-----------|-----------------|
| 25 | `petitioner` | YES | YES | PARTIAL | P filter `party_types[].name` = "Plaintiff" | `parties[]` (often empty) |
| 26 | `respondent` | YES | YES | PARTIAL | P filter `party_types[].name` = "Defendant" | `parties[]` (often empty) |
| 27 | `defendant_roles` | YES | YES | PARTIAL | P `party_types[].name` | `party_types[].name` (sparse) |
| 28 | `co_defendants` | YES | YES | PARTIAL | P all parties with defendant type | `parties[]` (sparse) |
| 29 | `relief_defendants` | YES | YES | PARTIAL | P filter "Relief Defendant" | `parties[]` (sparse) |
| 30 | `defendant_employer` | YES | NO | NO | — | — |
| 31 | `employer_crd_cik` | YES | NO | NO | — | — |
| 32 | `party_name` | NO | YES | PARTIAL | P `name` | `parties[].name` (sparse) |
| 33 | `party_type` | NO | YES | PARTIAL | P `party_types[].name` | `party_types[].name` (sparse) |
| 34 | `party_date_terminated` | NO | YES | PARTIAL | P `party_types[].date_terminated` | `party_types[].date_terminated` (sparse) |
| 35 | `party_criminal_counts` | NO | YES | PARTIAL | P `party_types[].criminal_counts[]` | `party_types[].criminal_counts` (sparse) |
| 36 | `party_criminal_complaints` | NO | YES | PARTIAL | P `party_types[].criminal_complaints[]` | `party_types[].criminal_complaints` (sparse) |
| 37 | `party_offense_level` | NO | YES | PARTIAL | P `highest_offense_level_opening/terminated` | `highest_offense_level_*` (sparse) |

## F. Attorneys

| # | Field | SEC EDGAR | CL | IA RECAP | CL Source | IA RECAP Source |
|---|-------|:---------:|:--:|:--------:|-----------|-----------------|
| 38 | `sec_attorneys` | YES | YES | PARTIAL | A attorneys linked to SEC party | `parties[].attorneys[]` (sparse) |
| 39 | `sec_regional_office` | YES | PARTIAL | PARTIAL | A parse from `contact_raw` | parse from `contact_raw` (sparse) |
| 40 | `attorney_name` | NO | YES | PARTIAL | A `name` | `parties[].attorneys[].name` (sparse) |
| 41 | `attorney_phone` | NO | YES | YES | A `phone` | `attorneys[].phone` |
| 42 | `attorney_email` | NO | YES | YES | A `email` | `attorneys[].email` |
| 43 | `attorney_fax` | NO | YES | NO | A `fax` | — |
| 44 | `attorney_contact_raw` | NO | YES | NO | A `contact_raw` (full address) | — |
| 45 | `attorney_role` | NO | YES | NO | P `attorneys[].role` (int code) | — |

## G. Legal Classification

| # | Field | SEC EDGAR | CL | IA RECAP | CL Source | IA RECAP Source |
|---|-------|:---------:|:--:|:--------:|-----------|-----------------|
| 46 | `legal_topic` | YES | YES | YES | D `nature_of_suit` + `cause` | `nature_of_suit` + `cause` |
| 47 | `charges_and_sections` | YES | YES | YES | D `cause` + `idb_data.section` | `cause` + `idb_data.section` |
| 48 | `jury_demand` | NO | YES | YES | D `jury_demand` | `jury_demand` |
| 49 | `case_status` | YES | YES | YES | D derive from `date_terminated` | derive from `date_terminated` |
| 50 | `company_domain` | YES | NO | NO | — | — |

## H. Judgment & Outcome

| # | Field | SEC EDGAR | CL | IA RECAP | CL Source | IA RECAP Source |
|---|-------|:---------:|:--:|:--------:|-----------|-----------------|
| 51 | `judgment_type` | YES | YES | YES | D `idb_data.judgment` + `nature_of_judgement` | `idb_data.judgment` + `nature_of_judgement` |
| 52 | `outcome` | YES | YES | YES | D `idb_data.disposition`; CL `disposition` | `idb_data.disposition` + `judgment` |
| 53 | `summary` | YES | PARTIAL | NO | CL `summary`, `syllabus` (often empty) | — |
| 54 | `total_fine_amount` | YES | PARTIAL | PARTIAL | D `idb_data.amount_received` (often 0) | `idb_data.amount_received` (often 0) |
| 55 | `defendant_sentence` | YES | PARTIAL | PARTIAL | P `criminal_counts[]`, `offense_level` | criminal cases only |
| 56 | `final_judgment_details` | YES | PARTIAL | PARTIAL | D `idb_data.disposition` + DE `description` | `idb_data.disposition` + entry text |

## I. SEC Enforcement-Specific

| # | Field | SEC EDGAR | CL | IA RECAP | CL Source | IA RECAP Source |
|---|-------|:---------:|:--:|:--------:|-----------|-----------------|
| 57 | `total_victim_losses` | YES | NO | NO | — | — |
| 58 | `scheme_duration` | YES | NO | NO | — | — |
| 59 | `scheme_method` | YES | NO | NO | — | — |
| 60 | `victim_count` | YES | NO | NO | — | — |
| 61 | `admission_status` | YES | NO | NO | — | — |
| 62 | `parallel_actions` | YES | NO | NO | — | — |
| 63 | `related_releases` | YES | NO | NO | — | — |
| 64 | `regulatory_registrations` | YES | NO | NO | — | — |
| 65 | `pdf_insights` | YES | NO | NO | — | — |

## J. Procedural / FJC Integrated Database (idb_data)

| # | Field | SEC EDGAR | CL | IA RECAP | CL Source | IA RECAP Source |
|---|-------|:---------:|:--:|:--------:|-----------|-----------------|
| 66 | `disposition_code` | NO | YES | YES | D `idb_data.disposition` (int 0-19) | `idb_data.disposition` |
| 67 | `judgment_code` | NO | YES | YES | D `idb_data.judgment` (int 0-4) | `idb_data.judgment` |
| 68 | `procedural_progress` | NO | YES | YES | D `idb_data.procedural_progress` | `idb_data.procedural_progress` |
| 69 | `case_origin` | NO | YES | YES | D `idb_data.origin` | `idb_data.origin` |
| 70 | `monetary_demand` | NO | YES | YES | D `idb_data.monetary_demand` | `idb_data.monetary_demand` |
| 71 | `class_action_flag` | NO | YES | YES | D `idb_data.class_action` | `idb_data.class_action` |
| 72 | `diversity_of_residence` | NO | YES | YES | D `idb_data.diversity_of_residence` | `idb_data.diversity_of_residence` |
| 73 | `pro_se` | NO | YES | YES | D `idb_data.pro_se` | `idb_data.pro_se` |
| 74 | `arbitration_at_filing` | NO | YES | YES | D `idb_data.arbitration_at_filing` | `idb_data.arbitration_at_filing` |
| 75 | `arbitration_at_termination` | NO | YES | YES | D `idb_data.arbitration_at_termination` | `idb_data.arbitration_at_termination` |
| 76 | `county_of_residence` | NO | YES | YES | D `idb_data.county_of_residence` | `idb_data.county_of_residence` |

## K. Docket Entries

| # | Field | SEC EDGAR | CL | IA RECAP | CL Source | IA RECAP Source |
|---|-------|:---------:|:--:|:--------:|-----------|-----------------|
| 77 | `docket_entry_text` | NO | YES | YES | DE `description` | `docket_entries[].description` |
| 78 | `entry_number` | NO | YES | YES | DE `entry_number` | `docket_entries[].entry_number` |
| 79 | `entry_date_filed` | NO | YES | YES | DE `date_filed` | `docket_entries[].date_filed` |
| 80 | `pacer_sequence_number` | NO | YES | YES | DE `pacer_sequence_number` | `docket_entries[].pacer_sequence_number` |
| 81 | `recap_sequence_number` | NO | YES | NO | DE `recap_sequence_number` | — |

## L. Documents / RECAP

| # | Field | SEC EDGAR | CL | IA RECAP | CL Source | IA RECAP Source |
|---|-------|:---------:|:--:|:--------:|-----------|-----------------|
| 82 | `associated_documents` | YES | YES | YES | RD `filepath_local`; RQ `filepath_local` | `recap_documents[].filepath_ia` |
| 83 | `doc_page_count` | NO | YES | YES | RD `page_count` | `recap_documents[].page_count` |
| 84 | `doc_file_size` | NO | YES | YES | RD `file_size` | `recap_documents[].file_size` |
| 85 | `doc_is_available` | NO | YES | YES | RD `is_available` | `recap_documents[].is_available` |
| 86 | `doc_is_sealed` | NO | YES | YES | RD `is_sealed` | `recap_documents[].is_sealed` |
| 87 | `doc_is_free_on_pacer` | NO | YES | NO | RD `is_free_on_pacer` | — |
| 88 | `doc_ocr_status` | NO | YES | YES | RD `ocr_status` | `recap_documents[].ocr_status` |
| 89 | `doc_plain_text` | NO | YES | NO | RD `plain_text` (CL runs OCR) | — |
| 90 | `doc_pacer_doc_id` | NO | YES | YES | RD `pacer_doc_id` | `recap_documents[].pacer_doc_id` |
| 91 | `doc_document_type` | NO | YES | NO | RD `document_type` | — |
| 92 | `doc_thumbnail` | NO | YES | NO | RD `thumbnail` | — |
| 93 | `recap_query_filepath` | NO | YES | NO | RQ `filepath_local` | — |

## M. Opinions & Case Law

| # | Field | SEC EDGAR | CL | IA RECAP | CL Source | IA RECAP Source |
|---|-------|:---------:|:--:|:--------:|-----------|-----------------|
| 94 | `opinion_text` | NO | YES | NO | OP `plain_text`, `html_with_citations` | — |
| 95 | `opinion_type` | NO | YES | NO | OP `type` (combined/dissent/concur) | — |
| 96 | `opinion_author` | NO | YES | NO | OP `author_str`, `per_curiam` | — |
| 97 | `opinion_page_count` | NO | YES | NO | OP `page_count` | — |
| 98 | `opinions_cited` | NO | YES | NO | OP `opinions_cited[]` | — |
| 99 | `precedential_status` | NO | YES | NO | CL `precedential_status` | — |
| 100 | `citation_count` | NO | YES | NO | CL `citation_count` | — |
| 101 | `cluster_procedural_history` | NO | YES | NO | CL `procedural_history` | — |
| 102 | `cluster_posture` | NO | YES | NO | CL `posture` | — |
| 103 | `cluster_syllabus` | NO | YES | NO | CL `syllabus` | — |
| 104 | `cluster_headnotes` | NO | YES | NO | CL `headnotes` | — |
| 105 | `cluster_disposition` | NO | YES | NO | CL `disposition` (text) | — |
| 106 | `scdb_id` | NO | YES | NO | CL `scdb_id` | — |
| 107 | `scdb_decision_direction` | NO | YES | NO | CL `scdb_decision_direction` | — |
| 108 | `scdb_votes_majority` | NO | YES | NO | CL `scdb_votes_majority` | — |
| 109 | `scdb_votes_minority` | NO | YES | NO | CL `scdb_votes_minority` | — |

## N. Oral Arguments

| # | Field | SEC EDGAR | CL | IA RECAP | CL Source | IA RECAP Source |
|---|-------|:---------:|:--:|:--------:|-----------|-----------------|
| 110 | `oral_arg_date` | NO | YES | NO | OA `dateArgued` | — |
| 111 | `oral_arg_duration` | NO | YES | NO | OA `duration` (seconds) | — |
| 112 | `oral_arg_download_url` | NO | YES | NO | OA `download_url` (MP3) | — |
| 113 | `oral_arg_file_size` | NO | YES | NO | OA `file_size_mp3` | — |
| 114 | `oral_arg_judge` | NO | YES | NO | OA `judge` | — |
| 115 | `oral_arg_snippet` | NO | YES | NO | OA `snippet` (transcript excerpt) | — |

## O. Source Metadata

| # | Field | SEC EDGAR | CL | IA RECAP | CL Source | IA RECAP Source |
|---|-------|:---------:|:--:|:--------:|-----------|-----------------|
| 116 | `source_code` | NO | YES | NO | D `source` (int code) | — |

---

## Summary

### Per-source field counts

| Source | YES | PARTIAL | NO | Total available (YES+PARTIAL) |
|--------|----:|--------:|---:|------------------------------:|
| **SEC EDGAR** | 39 | 0 | 77 | 39 |
| **CourtListener** | 96 | 5 | 15 | 101 |
| **IA RECAP** | 42 | 17 | 57 | 59 |

### Availability categories

| Category | Count | Fields |
|----------|------:|--------|
| All three (YES/YES/YES) | 13 | case_title, court, date, complaint_filed_date, judgment_date, judges, legal_topic, charges_and_sections, case_status, judgment_type, outcome, source_url, associated_documents |
| SEC YES + CL YES + IA PARTIAL | 6 | petitioner, respondent, defendant_roles, co_defendants, relief_defendants, sec_attorneys |
| SEC YES + CL PARTIAL + IA NO | 1 | summary |
| SEC YES + CL PARTIAL + IA PARTIAL | 4 | sec_regional_office, total_fine_amount, defendant_sentence, final_judgment_details |
| SEC EDGAR only | 15 | citation, defendant_employer, employer_crd_cik, company_domain, total_victim_losses, scheme_duration, scheme_method, victim_count, admission_status, parallel_actions, related_releases, scheme_start_date, scheme_end_date, regulatory_registrations, pdf_insights |
| CL YES + IA YES (not SEC) | 29 | docket_number, pacer_case_id, jurisdiction_type, jury_demand, date_terminated, date_last_filing, all idb_data fields (11), docket_entry_text, entry_number, entry_date_filed, pacer_sequence_number, doc_page_count, doc_file_size, doc_is_available, doc_is_sealed, doc_ocr_status, doc_pacer_doc_id, attorney_phone, attorney_email |
| CL YES + IA PARTIAL (not SEC) | 7 | party_name, party_type, party_date_terminated, party_criminal_counts, party_criminal_complaints, party_offense_level, attorney_name |
| CL only (SEC NO + IA NO) | 41 | docket_number_core, slug, source_code, federal_dn_case_type, federal_dn_office_code, federal_dn_judge_initials, appeal_from, mdl_status, date_created, date_modified, recap_sequence_number, doc_is_free_on_pacer, doc_plain_text, doc_document_type, doc_thumbnail, recap_query_filepath, attorney_fax, attorney_contact_raw, attorney_role, all opinion fields (16), all oral argument fields (6) |
| **Total unique fields** | **116** | |

### CL endpoint coverage

| CL Endpoint | Key | Fields | EDU Required? |
|-------------|-----|-------:|:-------------:|
| Dockets | D | 26 | No |
| Docket entries | DE | 5 | **Yes** |
| RECAP documents | RD | 12 | No |
| Parties | P | 13 | **Yes** |
| Attorneys | A | 8 | **Yes** |
| Opinion clusters | CL | 12 | No |
| Individual opinions | OP | 5 | No |
| RECAP query | RQ | 1 | **Yes** |
| Oral arguments | OA | 6 | No |

### Source strengths at a glance

| Dimension | Best Source | Why |
|-----------|------------|-----|
| SEC enforcement details | SEC EDGAR | Only source with scheme details, victim losses, fines, admission status, parallel actions |
| Party/attorney structure | CourtListener | Dedicated `/parties/` and `/attorneys/` endpoints with name, role, phone, email, firm |
| Docket filings & entries | CL + IA RECAP | Both have full docket entry text; CL adds sequence numbers |
| Opinion full text | CourtListener | Plain text + HTML with citations; dissent/concurrence typed |
| Citation network | CourtListener | `opinions_cited[]` + `citation_count` for precedent analysis |
| Oral arguments | CourtListener | Audio MP3 with duration, judges, transcript snippets |
| Document OCR text | CourtListener | Only source that runs OCR on PACER PDFs (`plain_text`) |
| SCDB (Supreme Court) | CourtListener | Links to Supreme Court Database IDs and vote counts |
| Procedural/FJC data | CL + IA RECAP | Both provide FJC Integrated Database fields (~20% fill rate) |
| Criminal case detail | CourtListener | Structured `criminal_counts[]`, `criminal_complaints[]`, offense levels |

---

## Notes

1. **IA RECAP vs CourtListener overlap**: Both sources draw from the same underlying PACER/RECAP data. IA RECAP stores raw docket JSON from Internet Archive; CourtListener provides the same data through structured API endpoints with richer fill rates (especially parties/attorneys). CL additionally processes opinions, oral arguments, and OCR text that IA RECAP does not.

2. **IA RECAP party/attorney sparseness**: IA RECAP `parties[]` arrays are frequently empty or incomplete in practice. CourtListener's dedicated `/parties/` and `/attorneys/` endpoints (EDU access) provide the same data with much higher fill rates. Fields marked PARTIAL for IA RECAP in sections E/F reflect this.

3. **idb_data availability**: Only ~20% of cases have FJC Integrated Database data. This affects fields #66-76 across both CL and IA RECAP. Primarily available for district court civil/criminal cases; appellate and Supreme Court cases typically lack idb_data.

4. **SEC EDGAR uniqueness**: SEC EDGAR is the only source providing enforcement-specific details (victim losses, scheme methods, fines, admission status, parallel actions). These 15 fields cannot be obtained from court docket data.

5. **CL opinion fill rates**: Many cluster text fields (`summary`, `syllabus`, `headnotes`, `procedural_history`, `posture`) are often empty strings. `precedential_status` and `citation_count` are consistently filled.

6. **EDU account requirement**: CourtListener `/parties/`, `/attorneys/`, `/docket-entries/`, and `/recap-query/` endpoints require EDU or paid membership (free-tier tokens get HTTP 403). This affects 25 fields in sections E, F, K, and L (rows 25-29, 32-37, 38-45, 77-81, 93).

