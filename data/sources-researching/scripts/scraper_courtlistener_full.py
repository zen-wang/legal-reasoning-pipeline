#!/usr/bin/env python3
"""
CourtListener Full Exploration Scraper (Membership Edition)
Scrapes 50 securities fraud cases — exhaustively exploring ALL available
metadata and content per case to document what CourtListener provides.
"""
import requests
import json
import time
import os
import sys
import traceback
from datetime import datetime

from config import COURTLISTENER_TOKEN, REQUEST_DELAY, PDF_DOWNLOAD_DELAY

HEADERS = {"Authorization": f"Token {COURTLISTENER_TOKEN}"}
BASE = "https://www.courtlistener.com/api/rest/v4"
OUTPUT_DIR = "data/courtlistener_full_50"

# Rate limiting
MAX_ENTRIES_PER_CASE = 100      # docket entries
MAX_PDFS_PER_CASE = 5           # keep PDF downloads reasonable
MAX_RECAP_DOCS_PER_CASE = 50    # recap document metadata (not PDFs)
MAX_PARTIES = 100
MAX_ATTORNEYS = 100

# --- 50 Securities Fraud / Financial Litigation Cases ---
CASES_TO_SEARCH = [
    # Classic SEC enforcement
    "SEC v. Theranos",
    "SEC v. Ripple Labs",
    "SEC v. Elon Musk Tesla",
    "SEC v. Goldman Sachs Abacus",
    "SEC v. Madoff",
    "SEC v. Stanford Financial",
    "SEC v. Citigroup",
    "SEC v. FTX Trading",
    "SEC v. Binance",
    "SEC v. Coinbase",
    # Famous securities class actions
    "In re Tesla Securities Litigation",
    "In re Enron Securities Litigation",
    "In re Luckin Coffee Securities Litigation",
    "In re Nikola Corporation Securities Litigation",
    "In re Valeant Pharmaceuticals Securities Litigation",
    "In re Worldcom Securities Litigation",
    "In re Facebook Securities Litigation",
    "In re Alibaba Group Securities Litigation",
    "In re Uber Technologies Securities Litigation",
    "In re Boeing Securities Litigation",
    # Criminal cases
    "United States v. Elizabeth Holmes",
    "United States v. Sam Bankman-Fried",
    "United States v. Martin Shkreli",
    "United States v. Raj Rajaratnam",
    "United States v. Skilling",
    "United States v. Ebbers",
    "United States v. Kozlowski",
    "United States v. Stewart Martha",
    "United States v. Cohen SAC Capital",
    "United States v. Aleynikov",
    # Supreme Court landmark securities cases
    "Basic v. Levinson",
    "Dura Pharmaceuticals v. Broudo",
    "Halliburton v. Erica John Fund",
    "Tellabs v. Makor Issues",
    "Morrison v. National Australia Bank",
    "Stoneridge v. Scientific-Atlanta",
    "Janus Capital v. First Derivative",
    "Omnicare v. Laborers District Council",
    "Lorenzo v. SEC",
    "Liu v. SEC",
    # Regulatory / whistleblower / accounting fraud
    "SEC v. Wirecard",
    "In re Wells Fargo Securities Litigation",
    "In re General Electric Securities Litigation",
    "In re Peloton Securities Litigation",
    "In re Lordstown Motors Securities Litigation",
    "In re Kraft Heinz Securities Litigation",
    "In re Teva Pharmaceutical Securities Litigation",
    "In re Under Armour Securities Litigation",
    "In re Mylan Securities Litigation",
    "In re Bed Bath Beyond Securities Litigation",
]


def api_get(url, params=None):
    """GET with rate limiting and error handling."""
    resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return resp.json()


def api_get_safe(url, params=None, label=""):
    """GET that returns None on 403/404 instead of raising."""
    try:
        return api_get(url, params)
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        print(f"    [{code}] {label}: {e}")
        return None
    except Exception as e:
        print(f"    [ERR] {label}: {e}")
        return None


def paginate_all(url, params=None, limit=100, label=""):
    """Paginate through results up to limit."""
    all_results = []
    page_params = dict(params) if params else {}
    while url and len(all_results) < limit:
        data = api_get_safe(url, page_params, label)
        if data is None:
            break
        results = data.get("results", [])
        all_results.extend(results)
        url = data.get("next")
        page_params = {}  # next URL has params built in
    return all_results[:limit]


def sanitize_filename(name, max_len=60):
    return "".join(c if c.isalnum() or c in "._- " else "_" for c in name)[:max_len].strip()


def save_json(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def save_text(text, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def download_pdf(filepath_local, save_path):
    if not filepath_local:
        return False
    url = f"https://storage.courtlistener.com/{filepath_local}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=60)
        if resp.status_code == 200 and len(resp.content) > 100:
            with open(save_path, "wb") as f:
                f.write(resp.content)
            return True
    except Exception as e:
        print(f"      PDF download failed: {e}")
    return False


def fetch_full_opinion_text(sub_opinion_url):
    """Fetch a single opinion's full text from its URL."""
    op = api_get_safe(sub_opinion_url, label="opinion text")
    if not op:
        return None, None
    # Try different text formats in priority order
    text = (op.get("plain_text") or
            op.get("html_with_citations") or
            op.get("html") or
            op.get("xml_harvard") or
            "")
    return op, text


def process_case(idx, case_query):
    """Full exploration of a single case."""
    print(f"\n{'='*70}")
    print(f"[{idx+1}/50] Searching: {case_query}")

    # --- STEP 1: Search ---
    results = api_get_safe(f"{BASE}/search/",
                           {"q": case_query, "type": "r", "order_by": "score desc"},
                           "search")
    if not results or not results.get("results"):
        print(f"  NOT FOUND: {case_query}")
        return None

    first = results["results"][0]
    docket_id = first.get("docket_id")
    case_name = first.get("caseName", "unknown")
    print(f"  Found: {case_name} (docket_id={docket_id})")

    # Create output directory
    safe_name = sanitize_filename(case_name)
    case_dir = os.path.join(OUTPUT_DIR, f"{idx:02d}_{docket_id}_{safe_name}")
    os.makedirs(case_dir, exist_ok=True)

    # Save raw search result
    save_json(first, os.path.join(case_dir, "01_search_result.json"))
    # Also save top 5 search results for reference
    save_json(results["results"][:5], os.path.join(case_dir, "01_search_top5.json"))

    data_inventory = {
        "case_query": case_query,
        "case_name": case_name,
        "docket_id": docket_id,
        "scraped_at": datetime.now().isoformat(),
        "endpoints": {},
    }

    # --- STEP 2: Docket Metadata ---
    print("  [1/8] Docket metadata...")
    docket = api_get_safe(f"{BASE}/dockets/{docket_id}/", label="docket")
    if docket:
        save_json(docket, os.path.join(case_dir, "02_docket_metadata.json"))
        data_inventory["endpoints"]["docket"] = {
            "status": "OK",
            "fields_present": [k for k, v in docket.items() if v not in (None, "", [], {})],
            "fields_null_or_empty": [k for k, v in docket.items() if v in (None, "", [], {})],
            "court": docket.get("court"),
            "court_id": docket.get("court_id"),
            "date_filed": docket.get("date_filed"),
            "date_terminated": docket.get("date_terminated"),
            "nature_of_suit": docket.get("nature_of_suit"),
            "cause": docket.get("cause"),
            "jury_demand": docket.get("jury_demand"),
            "jurisdiction_type": docket.get("jurisdiction_type"),
            "pacer_case_id": docket.get("pacer_case_id"),
            "assigned_to_str": docket.get("assigned_to_str"),
            "referred_to_str": docket.get("referred_to_str"),
            "filepath_ia": docket.get("filepath_ia"),
            "filepath_local": docket.get("filepath_local"),
            "source": docket.get("source"),
        }
    else:
        data_inventory["endpoints"]["docket"] = {"status": "FAILED"}

    # --- STEP 3: Docket Entries ---
    print("  [2/8] Docket entries...")
    entries = paginate_all(
        f"{BASE}/docket-entries/",
        {"docket": docket_id, "order_by": "date_filed"},
        limit=MAX_ENTRIES_PER_CASE,
        label="docket-entries"
    )
    if entries:
        save_json(entries, os.path.join(case_dir, "03_docket_entries.json"))
        # Analyze entry structure
        sample_entry = entries[0] if entries else {}
        recap_docs_in_entries = sum(len(e.get("recap_documents", [])) for e in entries)
        data_inventory["endpoints"]["docket_entries"] = {
            "status": "OK",
            "count": len(entries),
            "fields_in_entry": list(sample_entry.keys()) if sample_entry else [],
            "total_recap_documents_referenced": recap_docs_in_entries,
            "date_range": {
                "earliest": entries[0].get("date_filed") if entries else None,
                "latest": entries[-1].get("date_filed") if entries else None,
            }
        }
    else:
        data_inventory["endpoints"]["docket_entries"] = {"status": "EMPTY_OR_FAILED", "count": 0}

    # --- STEP 4: RECAP Documents (metadata) ---
    print("  [3/8] RECAP documents metadata...")
    recap_docs = paginate_all(
        f"{BASE}/recap-documents/",
        {"docket_entry__docket": docket_id},
        limit=MAX_RECAP_DOCS_PER_CASE,
        label="recap-documents"
    )
    if recap_docs:
        save_json(recap_docs, os.path.join(case_dir, "04_recap_documents.json"))
        sample_doc = recap_docs[0] if recap_docs else {}
        docs_with_pdf = [d for d in recap_docs if d.get("filepath_local")]
        docs_with_text = [d for d in recap_docs if d.get("plain_text")]
        docs_with_ocr = [d for d in recap_docs if d.get("ocr_status") and d.get("ocr_status") != 0]
        doc_types = {}
        for d in recap_docs:
            dt = str(d.get("document_type", "unknown"))
            doc_types[dt] = doc_types.get(dt, 0) + 1
        data_inventory["endpoints"]["recap_documents"] = {
            "status": "OK",
            "count": len(recap_docs),
            "fields_in_document": list(sample_doc.keys()) if sample_doc else [],
            "with_pdf_filepath": len(docs_with_pdf),
            "with_plain_text": len(docs_with_text),
            "with_ocr": len(docs_with_ocr),
            "document_type_breakdown": doc_types,
            "page_count_range": {
                "min": min((d.get("page_count") or 0) for d in recap_docs) if recap_docs else 0,
                "max": max((d.get("page_count") or 0) for d in recap_docs) if recap_docs else 0,
            }
        }
    else:
        data_inventory["endpoints"]["recap_documents"] = {"status": "EMPTY_OR_FAILED", "count": 0}

    # --- STEP 5: Download a few PDFs as samples ---
    print("  [4/8] Downloading sample PDFs...")
    pdf_count = 0
    pdf_dir = os.path.join(case_dir, "pdfs")
    if recap_docs:
        os.makedirs(pdf_dir, exist_ok=True)
        for doc in recap_docs:
            if pdf_count >= MAX_PDFS_PER_CASE:
                break
            fp = doc.get("filepath_local")
            if fp:
                desc = doc.get("description", f"doc_{doc.get('id','x')}")
                fname = f"doc_{doc.get('id',0)}_{sanitize_filename(desc)}.pdf"
                save_path = os.path.join(pdf_dir, fname)
                if not os.path.exists(save_path):
                    if download_pdf(fp, save_path):
                        pdf_count += 1
                        print(f"      PDF: {fname}")
                    time.sleep(PDF_DOWNLOAD_DELAY)
    data_inventory["pdfs_downloaded"] = pdf_count

    # Also save plain_text from recap_documents if available
    text_count = 0
    text_dir = os.path.join(case_dir, "recap_texts")
    if recap_docs:
        os.makedirs(text_dir, exist_ok=True)
        for doc in recap_docs:
            plain = doc.get("plain_text")
            if plain and len(plain) > 50:
                fname = f"recap_{doc.get('id',0)}_{sanitize_filename(doc.get('description',''))}.txt"
                save_text(plain, os.path.join(text_dir, fname))
                text_count += 1
    data_inventory["recap_texts_saved"] = text_count

    # --- STEP 6: Opinion Clusters + Full Opinion Text ---
    print("  [5/8] Opinion clusters & full text...")
    clusters = paginate_all(
        f"{BASE}/clusters/",
        {"docket": docket_id},
        limit=50,
        label="clusters"
    )
    if clusters:
        save_json(clusters, os.path.join(case_dir, "05_opinion_clusters.json"))

    opinion_texts_saved = 0
    opinion_dir = os.path.join(case_dir, "opinions")
    os.makedirs(opinion_dir, exist_ok=True)

    for cluster in clusters:
        cluster_id = cluster.get("id")
        sub_opinions = cluster.get("sub_opinions", [])
        for j, op_url in enumerate(sub_opinions):
            if isinstance(op_url, str) and op_url.startswith("http"):
                op_data, text = fetch_full_opinion_text(op_url)
                if op_data:
                    # Save full opinion metadata
                    save_json(op_data, os.path.join(opinion_dir, f"opinion_c{cluster_id}_{j}_meta.json"))
                if text and len(text) > 50:
                    save_text(text, os.path.join(opinion_dir, f"opinion_c{cluster_id}_{j}_text.txt"))
                    opinion_texts_saved += 1
                    print(f"      Opinion: cluster={cluster_id} sub={j} ({len(text)} chars)")

    data_inventory["endpoints"]["opinion_clusters"] = {
        "status": "OK" if clusters else "EMPTY",
        "cluster_count": len(clusters),
        "opinion_texts_saved": opinion_texts_saved,
        "sample_cluster_fields": list(clusters[0].keys()) if clusters else [],
    }

    # --- STEP 7: Parties ---
    print("  [6/8] Parties...")
    parties = paginate_all(
        f"{BASE}/parties/",
        {"docket": docket_id},
        limit=MAX_PARTIES,
        label="parties"
    )
    if parties:
        save_json(parties, os.path.join(case_dir, "06_parties.json"))
        party_types = {}
        for p in parties:
            pt = p.get("party_type", {})
            # party_type can be a dict with 'name' or just a string
            if isinstance(pt, dict):
                pt_name = pt.get("name", "unknown")
            else:
                pt_name = str(pt)
            party_types[pt_name] = party_types.get(pt_name, 0) + 1
        data_inventory["endpoints"]["parties"] = {
            "status": "OK",
            "count": len(parties),
            "party_type_breakdown": party_types,
            "sample_fields": list(parties[0].keys()) if parties else [],
        }
    else:
        data_inventory["endpoints"]["parties"] = {"status": "EMPTY_OR_FAILED", "count": 0}

    # --- STEP 8: Attorneys ---
    print("  [7/8] Attorneys...")
    attorneys = paginate_all(
        f"{BASE}/attorneys/",
        {"parties__docket": docket_id},
        limit=MAX_ATTORNEYS,
        label="attorneys"
    )
    if attorneys:
        save_json(attorneys, os.path.join(case_dir, "07_attorneys.json"))
        data_inventory["endpoints"]["attorneys"] = {
            "status": "OK",
            "count": len(attorneys),
            "sample_fields": list(attorneys[0].keys()) if attorneys else [],
        }
    else:
        data_inventory["endpoints"]["attorneys"] = {"status": "EMPTY_OR_FAILED", "count": 0}

    # --- STEP 9: Oral Arguments (if any) ---
    print("  [8/8] Oral arguments (if any)...")
    oral_args = api_get_safe(
        f"{BASE}/search/",
        {"q": case_query, "type": "oa"},
        label="oral-arguments"
    )
    if oral_args and oral_args.get("results"):
        save_json(oral_args["results"][:10], os.path.join(case_dir, "08_oral_arguments.json"))
        data_inventory["endpoints"]["oral_arguments"] = {
            "status": "OK",
            "count": len(oral_args["results"]),
            "sample_fields": list(oral_args["results"][0].keys()) if oral_args["results"] else [],
        }
    else:
        data_inventory["endpoints"]["oral_arguments"] = {"status": "NONE_FOUND", "count": 0}

    # --- Save case inventory ---
    save_json(data_inventory, os.path.join(case_dir, "00_data_inventory.json"))

    # Print summary
    ep = data_inventory["endpoints"]
    print(f"  SUMMARY: entries={ep.get('docket_entries',{}).get('count',0)}, "
          f"recap_docs={ep.get('recap_documents',{}).get('count',0)}, "
          f"PDFs={pdf_count}, opinions={opinion_texts_saved}, "
          f"parties={ep.get('parties',{}).get('count',0)}, "
          f"attorneys={ep.get('attorneys',{}).get('count',0)}, "
          f"oral_args={ep.get('oral_arguments',{}).get('count',0)}")

    return data_inventory


def generate_report(all_inventories):
    """Generate a comprehensive data availability report."""
    report_lines = []
    report_lines.append("=" * 80)
    report_lines.append("COURTLISTENER FULL DATA EXPLORATION REPORT")
    report_lines.append(f"Generated: {datetime.now().isoformat()}")
    report_lines.append(f"Cases scraped: {len(all_inventories)}")
    report_lines.append("=" * 80)

    # Aggregate stats
    endpoint_names = ["docket", "docket_entries", "recap_documents",
                      "opinion_clusters", "parties", "attorneys", "oral_arguments"]

    report_lines.append("\n## ENDPOINT AVAILABILITY ACROSS ALL CASES\n")
    for ep_name in endpoint_names:
        ok_count = sum(1 for inv in all_inventories
                       if inv["endpoints"].get(ep_name, {}).get("status") == "OK")
        report_lines.append(f"  {ep_name:25s}: {ok_count}/{len(all_inventories)} cases have data")

    # Per-case summary table
    report_lines.append("\n## PER-CASE DATA SUMMARY\n")
    report_lines.append(f"{'#':>3} {'Case Name':50s} {'Entries':>8} {'RECAP':>6} {'PDFs':>5} "
                        f"{'Opinions':>9} {'Parties':>8} {'Attys':>6} {'OralArg':>8}")
    report_lines.append("-" * 120)

    for i, inv in enumerate(all_inventories):
        ep = inv["endpoints"]
        report_lines.append(
            f"{i+1:3d} {inv['case_name'][:50]:50s} "
            f"{ep.get('docket_entries',{}).get('count',0):8d} "
            f"{ep.get('recap_documents',{}).get('count',0):6d} "
            f"{inv.get('pdfs_downloaded',0):5d} "
            f"{ep.get('opinion_clusters',{}).get('opinion_texts_saved',0):9d} "
            f"{ep.get('parties',{}).get('count',0):8d} "
            f"{ep.get('attorneys',{}).get('count',0):6d} "
            f"{ep.get('oral_arguments',{}).get('count',0):8d}"
        )

    # Field-level analysis from first successful case
    report_lines.append("\n## FIELDS AVAILABLE IN EACH ENDPOINT\n")
    for ep_name in endpoint_names:
        for inv in all_inventories:
            ep_data = inv["endpoints"].get(ep_name, {})
            fields = (ep_data.get("fields_present") or
                      ep_data.get("fields_in_entry") or
                      ep_data.get("fields_in_document") or
                      ep_data.get("sample_fields") or
                      ep_data.get("sample_cluster_fields") or [])
            if fields:
                report_lines.append(f"  [{ep_name}] ({len(fields)} fields):")
                for f in sorted(fields):
                    report_lines.append(f"    - {f}")
                report_lines.append("")
                break

    # Docket metadata field analysis
    report_lines.append("\n## DOCKET METADATA FIELD FILL RATES\n")
    if all_inventories:
        all_fields = set()
        for inv in all_inventories:
            present = inv["endpoints"].get("docket", {}).get("fields_present", [])
            null_empty = inv["endpoints"].get("docket", {}).get("fields_null_or_empty", [])
            all_fields.update(present)
            all_fields.update(null_empty)

        for field in sorted(all_fields):
            filled = sum(1 for inv in all_inventories
                         if field in inv["endpoints"].get("docket", {}).get("fields_present", []))
            report_lines.append(f"  {field:40s}: {filled}/{len(all_inventories)} "
                                f"({100*filled/len(all_inventories):.0f}%)")

    report = "\n".join(report_lines)
    save_text(report, os.path.join(OUTPUT_DIR, "_DATA_REPORT.txt"))
    save_json(all_inventories, os.path.join(OUTPUT_DIR, "_all_inventories.json"))
    print(f"\n{'='*70}")
    print(report)
    return report


def main():
    if not COURTLISTENER_TOKEN or COURTLISTENER_TOKEN == "YOUR_COURTLISTENER_TOKEN_HERE":
        print("Set COURTLISTENER_TOKEN in config.py first")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_inventories = []
    for i, query in enumerate(CASES_TO_SEARCH):
        try:
            inventory = process_case(i, query)
            if inventory:
                all_inventories.append(inventory)
        except Exception as e:
            print(f"  FATAL ERROR on '{query}': {e}")
            traceback.print_exc()
        time.sleep(1.5)

    generate_report(all_inventories)
    print(f"\nDone: {len(all_inventories)}/{len(CASES_TO_SEARCH)} cases scraped")
    print(f"Output: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
