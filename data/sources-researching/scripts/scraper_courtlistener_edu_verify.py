#!/usr/bin/env python3
"""
CourtListener EDU Account Verification Script
Queries all API endpoints for 3 sample cases to capture real field schemas
and confirm EDU-tier access to previously-blocked endpoints.
"""
import requests
import json
import time
import os
from datetime import datetime
from collections import defaultdict

from config import COURTLISTENER_TOKEN, REQUEST_DELAY

HEADERS = {"Authorization": f"Token {COURTLISTENER_TOKEN}"}
BASE = "https://www.courtlistener.com/api/rest/v4"
OUTPUT_DIR = "data/courtlistener_edu_verify"

# Known docket IDs from existing scraped data
SAMPLE_CASES = [
    (7643790, "In re Tesla Securities Litigation"),
    (66746322, "United States v. Elizabeth Holmes"),
    (6247234, "In re Under Armour Securities Litigation"),
]


def api_get(url, params=None):
    resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return resp.json()


def api_get_safe(url, params=None, label=""):
    try:
        return api_get(url, params)
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        print(f"    [{code}] {label}: {e}")
        return None
    except Exception as e:
        print(f"    [ERR] {label}: {e}")
        return None


def paginate_all(url, params=None, limit=20, label=""):
    all_results = []
    page_params = dict(params) if params else {}
    while url and len(all_results) < limit:
        data = api_get_safe(url, page_params, label)
        if data is None:
            break
        results = data.get("results", [])
        all_results.extend(results)
        url = data.get("next")
        page_params = {}
    return all_results[:limit]


def extract_field_info(obj, prefix=""):
    """Extract field names, types, and sample values from a dict."""
    fields = {}
    if not isinstance(obj, dict):
        return fields
    for key, val in obj.items():
        full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if val is None:
            fields[full_key] = {"type": "null", "sample": None, "filled": False}
        elif isinstance(val, dict):
            fields[full_key] = {"type": "dict", "sample": "{...}", "filled": bool(val)}
            # Recurse into nested dicts (1 level deep)
            if not prefix:  # only go 1 level deep
                nested = extract_field_info(val, full_key)
                fields.update(nested)
        elif isinstance(val, list):
            fields[full_key] = {"type": "list", "sample": f"[{len(val)} items]", "filled": bool(val)}
        elif isinstance(val, bool):
            fields[full_key] = {"type": "bool", "sample": val, "filled": True}
        elif isinstance(val, int):
            fields[full_key] = {"type": "int", "sample": val, "filled": True}
        elif isinstance(val, float):
            fields[full_key] = {"type": "float", "sample": val, "filled": True}
        elif isinstance(val, str):
            sample = val[:100] if len(val) > 100 else val
            fields[full_key] = {"type": "str", "sample": sample, "filled": bool(val)}
        else:
            fields[full_key] = {"type": str(type(val).__name__), "sample": str(val)[:100], "filled": bool(val)}
    return fields


def query_all_endpoints(docket_id, case_name):
    """Query all endpoints for a single case. Returns dict of endpoint -> response data."""
    print(f"\n{'='*70}")
    print(f"Case: {case_name} (docket_id={docket_id})")
    print(f"{'='*70}")

    results = {}

    # 1. Docket metadata
    print("  [1/9] Docket metadata...")
    data = api_get_safe(f"{BASE}/dockets/{docket_id}/", label="dockets")
    results["docket"] = data
    if data:
        print(f"         OK - {data.get('case_name', '?')}")

    # 2. Docket entries (WAS BLOCKED)
    print("  [2/9] Docket entries (was blocked)...")
    entries = paginate_all(
        f"{BASE}/docket-entries/", {"docket": docket_id, "page_size": 5},
        limit=5, label="docket-entries"
    )
    results["docket_entries"] = entries
    print(f"         {'OK' if entries else 'EMPTY/DENIED'} - {len(entries)} entries")

    # 3. RECAP documents
    print("  [3/9] RECAP documents...")
    recap_docs = paginate_all(
        f"{BASE}/recap-documents/", {"docket_entry__docket": docket_id, "page_size": 5},
        limit=5, label="recap-documents"
    )
    results["recap_documents"] = recap_docs
    print(f"         {'OK' if recap_docs else 'EMPTY/DENIED'} - {len(recap_docs)} docs")

    # 4. Parties (WAS BLOCKED)
    print("  [4/9] Parties (was blocked)...")
    parties = paginate_all(
        f"{BASE}/parties/", {"docket": docket_id, "page_size": 10},
        limit=20, label="parties"
    )
    results["parties"] = parties
    print(f"         {'OK' if parties else 'EMPTY/DENIED'} - {len(parties)} parties")

    # 5. Attorneys (WAS BLOCKED)
    print("  [5/9] Attorneys (was blocked)...")
    attorneys = paginate_all(
        f"{BASE}/attorneys/", {"parties__docket": docket_id, "page_size": 10},
        limit=20, label="attorneys"
    )
    results["attorneys"] = attorneys
    print(f"         {'OK' if attorneys else 'EMPTY/DENIED'} - {len(attorneys)} attorneys")

    # 6. Opinion clusters
    print("  [6/9] Opinion clusters...")
    clusters = paginate_all(
        f"{BASE}/clusters/", {"docket": docket_id, "page_size": 5},
        limit=5, label="clusters"
    )
    results["clusters"] = clusters
    print(f"         {'OK' if clusters else 'EMPTY/NONE'} - {len(clusters)} clusters")

    # 7. Individual opinion (from first cluster)
    print("  [7/9] Individual opinion...")
    opinion = None
    if clusters:
        sub_opinions = clusters[0].get("sub_opinions", [])
        if sub_opinions:
            op_url = sub_opinions[0] if isinstance(sub_opinions[0], str) else None
            if op_url:
                opinion = api_get_safe(op_url, label="opinion")
    results["opinion"] = opinion
    print(f"         {'OK' if opinion else 'NONE'}")

    # 8. RECAP query (WAS BLOCKED)
    print("  [8/9] RECAP query (was blocked)...")
    recap_query = api_get_safe(
        f"{BASE}/recap-query/", {"docket": docket_id, "page_size": 5},
        label="recap-query"
    )
    results["recap_query"] = recap_query
    print(f"         {'OK' if recap_query else 'EMPTY/DENIED'}")

    # 9. Oral arguments (via search)
    print("  [9/9] Oral arguments search...")
    oa_search = api_get_safe(
        f"{BASE}/search/", {"q": case_name, "type": "oa", "page_size": 3},
        label="oral-args"
    )
    results["oral_arguments"] = oa_search
    oa_count = oa_search.get("count", 0) if oa_search else 0
    print(f"         {'OK' if oa_search else 'NONE'} - {oa_count} results")

    return results


def build_schema_report(all_case_data):
    """Build consolidated schema report across all cases."""
    schema = {}  # endpoint -> field_name -> {type, samples[], fill_count, total_count}

    for case_name, endpoints in all_case_data.items():
        for ep_name, ep_data in endpoints.items():
            if ep_name not in schema:
                schema[ep_name] = {}

            # Normalize to list of dicts
            if ep_data is None:
                continue
            elif isinstance(ep_data, dict):
                # Could be a single object or paginated response
                if "results" in ep_data:
                    items = ep_data["results"][:5]
                else:
                    items = [ep_data]
            elif isinstance(ep_data, list):
                items = ep_data[:5]
            else:
                continue

            for item in items:
                fields = extract_field_info(item)
                for field_name, info in fields.items():
                    if field_name not in schema[ep_name]:
                        schema[ep_name][field_name] = {
                            "type": info["type"],
                            "samples": [],
                            "fill_count": 0,
                            "total_count": 0,
                        }
                    entry = schema[ep_name][field_name]
                    entry["total_count"] += 1
                    if info["filled"]:
                        entry["fill_count"] += 1
                        if len(entry["samples"]) < 2 and info["sample"] is not None:
                            sample = info["sample"]
                            if isinstance(sample, str) and len(sample) > 80:
                                sample = sample[:80] + "..."
                            entry["samples"].append(sample)

    return schema


def print_schema_report(schema):
    """Print human-readable schema report."""
    print(f"\n{'='*80}")
    print("SCHEMA REPORT — All CourtListener Endpoints")
    print(f"{'='*80}")

    for ep_name in sorted(schema.keys()):
        fields = schema[ep_name]
        print(f"\n--- {ep_name.upper()} ({len(fields)} fields) ---")
        for fname in sorted(fields.keys()):
            info = fields[fname]
            fill = f"{info['fill_count']}/{info['total_count']}"
            sample = info["samples"][0] if info["samples"] else "—"
            if isinstance(sample, str) and len(sample) > 60:
                sample = sample[:60] + "..."
            print(f"  {fname:<50} [{info['type']:<5}] fill={fill:<5} ex: {sample}")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_case_data = {}

    for docket_id, case_name in SAMPLE_CASES:
        results = query_all_endpoints(docket_id, case_name)
        all_case_data[case_name] = results

        # Save raw responses per case
        safe_name = case_name.replace(" ", "_").replace("/", "_")[:60]
        case_dir = os.path.join(OUTPUT_DIR, f"{docket_id}_{safe_name}")
        os.makedirs(case_dir, exist_ok=True)
        for ep_name, ep_data in results.items():
            if ep_data is not None:
                with open(os.path.join(case_dir, f"{ep_name}.json"), "w") as f:
                    json.dump(ep_data, f, indent=2, default=str)

    # Build and save schema report
    schema = build_schema_report(all_case_data)
    report_path = os.path.join(OUTPUT_DIR, "_edu_schema_report.json")
    with open(report_path, "w") as f:
        json.dump(schema, f, indent=2, default=str)
    print(f"\nSchema report saved to: {report_path}")

    # Print report
    print_schema_report(schema)

    # Print access summary
    print(f"\n{'='*80}")
    print("EDU ACCESS SUMMARY")
    print(f"{'='*80}")
    for ep_name in sorted(schema.keys()):
        n_fields = len(schema[ep_name])
        was_blocked = ep_name in ("docket_entries", "parties", "attorneys", "recap_query")
        tag = " << NEWLY UNLOCKED" if was_blocked else ""
        print(f"  {ep_name:<25} {n_fields:>3} fields{tag}")


if __name__ == "__main__":
    main()
