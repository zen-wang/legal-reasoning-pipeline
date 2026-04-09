#!/usr/bin/env python3
"""
IA RECAP Data Analysis — Field Coverage & Case Status Distribution
===================================================================
Analyzes scraped IA RECAP case data for:
  1. Data field coverage (fill rates across 63-field unified schema)
  2. Case status/outcome distribution

All output is printed directly to the terminal (no PNG files).

Usage:
    python analyze_ia_recap.py [csv_path]
    python analyze_ia_recap.py ia_recap_cases.csv
"""

import argparse
import csv
import os
from collections import Counter

# ---------------------------------------------------------------------------
# Field categories from IA_RECAP_vs_SEC_EDGAR_data_field.md
# ---------------------------------------------------------------------------
FIELD_CATEGORIES = {
    "Both": [
        "case_title", "court", "date", "judges", "judgment_type", "outcome",
        "legal_topic", "charges_and_sections", "case_status", "complaint_filed_date",
        "judgment_date", "source_url", "associated_documents",
    ],
    "SEC EDGAR only": [
        "citation", "defendant_employer", "employer_crd_cik", "summary",
        "company_domain", "total_victim_losses", "scheme_duration", "scheme_method",
        "victim_count", "admission_status", "parallel_actions", "related_releases",
        "scheme_start_date", "scheme_end_date", "regulatory_registrations", "pdf_insights",
    ],
    "IA-RECAP-partial": [
        "petitioner", "respondent", "defendant_roles", "co_defendants",
        "relief_defendants", "sec_attorneys", "sec_regional_office",
        "total_fine_amount", "defendant_sentence", "final_judgment_details",
    ],
    "IA-RECAP-only": [
        "docket_number", "pacer_case_id", "jurisdiction_type", "jury_demand",
        "disposition_code", "judgment_code", "procedural_progress", "case_origin",
        "monetary_demand", "class_action_flag", "diversity_of_residence", "pro_se",
        "arbitration_at_filing", "arbitration_at_termination", "county_of_residence",
        "date_last_filing", "date_terminated", "attorney_phone", "attorney_email",
        "doc_page_count", "doc_file_size", "doc_is_sealed", "doc_ocr_status",
        "docket_entry_text",
    ],
}
CATEGORY_ORDER = ["Both", "SEC EDGAR only", "IA-RECAP-partial", "IA-RECAP-only"]

BAR_WIDTH = 40  # max width for ASCII bar charts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def bar(pct, width=BAR_WIDTH):
    """Return an ASCII bar: filled blocks proportional to pct (0-100)."""
    filled = round(pct / 100 * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def load_data(csv_path):
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = [r for r in reader]
    # strip whitespace
    for r in rows:
        for k in r:
            r[k] = r[k].strip()
    print(f"Loaded {len(rows)} cases, {len(fields)} columns from {csv_path}")
    return rows, fields


# ---------------------------------------------------------------------------
# Part 1: Field Coverage
# ---------------------------------------------------------------------------
def analyze_field_coverage(rows, fields):
    n = len(rows)
    print("\n" + "=" * 90)
    print(f"  PART 1: DATA FIELD COVERAGE ({n} cases, 63 fields)")
    print("=" * 90)

    for cat in CATEGORY_ORDER:
        cat_fields = [f for f in FIELD_CATEGORIES[cat] if f in fields]
        fill_rates = []
        for field in cat_fields:
            count = sum(1 for r in rows if r[field])
            pct = count / n * 100
            fill_rates.append((field, count, pct))

        avg_pct = sum(p for _, _, p in fill_rates) / len(fill_rates) if fill_rates else 0

        print(f"\n  [{cat}] ({len(cat_fields)} fields, avg {avg_pct:.1f}%)")
        print(f"  {'Field':<35} {'Filled':>9}  {'Pct':>6}  Bar")
        print(f"  {'-'*35} {'-'*9}  {'-'*6}  {'-'*BAR_WIDTH}")
        for field, count, pct in fill_rates:
            print(f"  {field:<35} {count:>4}/{n:<4} {pct:>5.1f}%  {bar(pct)}")

        if cat == "SEC EDGAR only" and avg_pct == 0:
            print(f"  ** All SEC EDGAR-only fields are empty (expected: this is IA RECAP data)")

    # Top filled fields summary
    all_rates = []
    for cat in CATEGORY_ORDER:
        for field in FIELD_CATEGORIES[cat]:
            if field in fields:
                count = sum(1 for r in rows if r[field])
                all_rates.append((field, count, count / n * 100))
    all_rates.sort(key=lambda x: -x[2])

    print(f"\n  --- Top 10 Most Populated Fields ---")
    for i, (field, count, pct) in enumerate(all_rates[:10], 1):
        print(f"  {i:>2}. {field:<35} {pct:>5.1f}%  {bar(pct)}")

    filled_any = sum(1 for _, _, p in all_rates if p > 0)
    print(f"\n  Fields with any data:  {filled_any}/63")
    print(f"  Fields completely empty: {63 - filled_any}/63")


# ---------------------------------------------------------------------------
# Part 2: Case Status & Outcome Distribution
# ---------------------------------------------------------------------------
def analyze_case_status(rows, fields):
    n = len(rows)
    print("\n" + "=" * 90)
    print(f"  PART 2: CASE STATUS & OUTCOME DISTRIBUTION ({n} cases)")
    print("=" * 90)

    # --- Case status ---
    status_counts = Counter(r["case_status"] if r["case_status"] else "(empty)" for r in rows)
    print(f"\n  --- Case Status ---")
    max_cnt = max(status_counts.values())
    for label in ["Open", "Closed", "(empty)"]:
        cnt = status_counts.get(label, 0)
        pct = cnt / n * 100
        b = "\u2588" * round(cnt / max_cnt * BAR_WIDTH)
        print(f"  {label:<12} {cnt:>4}  ({pct:>5.1f}%)  {b}")

    # --- Inference analysis ---
    empty_rows = [r for r in rows if not r["case_status"]]
    date_cols = ["date", "complaint_filed_date", "judgment_date", "date_last_filing", "date_terminated"]
    empty_with_terminated = sum(1 for r in empty_rows if r.get("date_terminated", ""))
    empty_with_any_date = sum(1 for r in empty_rows if any(r.get(c, "") for c in date_cols))

    print(f"\n  --- Status Inference Analysis ---")
    print(f"  Empty-status cases with date_terminated:  {empty_with_terminated}/{len(empty_rows)}")
    print(f"  Empty-status cases with any date field:   {empty_with_any_date}/{len(empty_rows)}")
    if empty_with_terminated == 0:
        print(f"  -> No additional cases can be inferred as Closed.")
        print(f"  -> The {len(empty_rows)} empty-status cases have minimal metadata.")

    # --- Data density by status group ---
    def fields_filled(row):
        return sum(1 for v in row.values() if v)

    groups = {"Open": [], "Closed": [], "(empty)": []}
    for r in rows:
        key = r["case_status"] if r["case_status"] else "(empty)"
        groups.setdefault(key, []).append(fields_filled(r))

    print(f"\n  --- Data Density by Status Group ---")
    print(f"  {'Group':<12} {'Count':>5}  {'Mean':>6}  {'Median':>6}  Distribution")
    print(f"  {'-'*12} {'-'*5}  {'-'*6}  {'-'*6}  {'-'*BAR_WIDTH}")
    for label in ["Open", "Closed", "(empty)"]:
        vals = groups.get(label, [])
        if not vals:
            continue
        mean_v = sum(vals) / len(vals)
        sorted_v = sorted(vals)
        median_v = sorted_v[len(sorted_v) // 2]
        b = bar(mean_v / 63 * 100)
        print(f"  {label:<12} {len(vals):>5}  {mean_v:>5.1f}  {median_v:>6}  {b}  ({mean_v:.1f}/63)")

    # --- Data density histogram (ASCII) ---
    all_filled = [fields_filled(r) for r in rows]
    max_filled = max(all_filled)
    bins = {}
    for v in all_filled:
        bucket = (v // 5) * 5  # group into bins of 5
        bins[bucket] = bins.get(bucket, 0) + 1

    print(f"\n  --- Data Completeness Histogram (fields filled per case, bin=5) ---")
    max_bin_cnt = max(bins.values())
    for bucket in sorted(bins):
        cnt = bins[bucket]
        b = "\u2588" * round(cnt / max_bin_cnt * BAR_WIDTH)
        print(f"  {bucket:>2}-{bucket+4:<2} fields: {cnt:>4} cases  {b}")

    # --- Outcome distribution ---
    outcome_counts = Counter(r["outcome"] for r in rows if r["outcome"])
    print(f"\n  --- Outcome Distribution ({sum(outcome_counts.values())}/{n} cases have values) ---")
    if outcome_counts:
        max_oc = max(outcome_counts.values())
        for val, cnt in outcome_counts.most_common():
            b = "\u2588" * round(cnt / max_oc * 20)
            print(f"  {cnt:>3}x  {b:<20}  {val}")
    else:
        print(f"  (no outcome data)")

    # --- Disposition code ---
    disp_counts = Counter(r["disposition_code"] for r in rows if r["disposition_code"])
    print(f"\n  --- Disposition Code ({sum(disp_counts.values())}/{n} cases have values) ---")
    if disp_counts:
        max_dc = max(disp_counts.values())
        for val, cnt in disp_counts.most_common():
            b = "\u2588" * round(cnt / max_dc * 20)
            print(f"  {cnt:>3}x  {b:<20}  {val}")
    else:
        print(f"  (no disposition data)")

    # --- Judgment type ---
    jt_counts = Counter(r["judgment_type"] for r in rows if r["judgment_type"])
    print(f"\n  --- Judgment Type ({sum(jt_counts.values())}/{n} cases have values) ---")
    if jt_counts:
        max_jt = max(jt_counts.values())
        for val, cnt in jt_counts.most_common():
            b = "\u2588" * round(cnt / max_jt * 20)
            print(f"  {cnt:>3}x  {b:<20}  {val}")
    else:
        print(f"  (no judgment type data)")

    # --- Note about sparsity ---
    fjd_count = sum(1 for r in rows if r.get("final_judgment_details", ""))
    pct_no_outcome = (1 - sum(outcome_counts.values()) / n) * 100
    print(f"\n  NOTE: {pct_no_outcome:.0f}% of cases lack outcome/judgment data.")
    print(f"  The final_judgment_details field ({fjd_count} cases) has the most structured outcome info.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_csv = os.path.join(script_dir, "ia_recap_cases.csv")

    parser = argparse.ArgumentParser(description="Analyze IA RECAP scraped case data")
    parser.add_argument("csv_path", nargs="?", default=default_csv,
                        help="Path to CSV file (default: ia_recap_cases.csv next to this script)")
    args = parser.parse_args()

    rows, fields = load_data(args.csv_path)

    analyze_field_coverage(rows, fields)
    analyze_case_status(rows, fields)

    print()


if __name__ == "__main__":
    main()
