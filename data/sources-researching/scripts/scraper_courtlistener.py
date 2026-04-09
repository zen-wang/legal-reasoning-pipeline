#!/usr/bin/env python3
"""
來源 1 & 2: CourtListener / RECAP (含 PACER 資料)
抓取 10 個聯邦法院完整案件卷宗：metadata + docket entries + PDFs + opinions + parties
"""
import requests
import json
import time
import os
import sys

from config import COURTLISTENER_TOKEN, REQUEST_DELAY, PDF_DOWNLOAD_DELAY, MAX_PDFS_PER_CASE, MAX_ENTRIES_PER_CASE

# --- 設定 ---
HEADERS = {"Authorization": f"Token {COURTLISTENER_TOKEN}"}
BASE = "https://www.courtlistener.com/api/rest/v4"
OUTPUT_DIR = "data/courtlistener_cases"

# --- 10 個代表性案件 ---
CASES_TO_SEARCH = [
    "SEC v. Theranos",
    "Tesla Securities Litigation",
    "SEC v. Ripple Labs",
    "Enron Securities Litigation",
    "SEC v. Goldman Sachs Abacus",
    "United States v. Elizabeth Holmes",
    "Halliburton v. Erica P. John Fund",
    "Luckin Coffee Securities Litigation",
    "SEC v. Elon Musk Tesla",
    "Dura Pharmaceuticals v. Broudo",
]


def api_get(url, params=None):
    """帶速率限制的 GET 請求"""
    resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return resp.json()


def search_case(query):
    """搜尋案件，回傳搜尋結果"""
    return api_get(f"{BASE}/search/", {"q": query, "type": "r", "order_by": "score desc"})


def get_docket(docket_id):
    """取得完整案件摘要"""
    return api_get(f"{BASE}/dockets/{docket_id}/")


def get_docket_entries(docket_id, limit=MAX_ENTRIES_PER_CASE):
    """取得案件條目列表（分頁）"""
    url = f"{BASE}/docket-entries/"
    params = {"docket": docket_id, "order_by": "date_filed"}
    all_entries = []
    while url and len(all_entries) < limit:
        data = api_get(url, params)
        all_entries.extend(data["results"])
        url = data.get("next")
        params = {}  # next URL 已包含參數
    return all_entries[:limit]


def get_opinions(docket_id):
    """取得相關判決意見書"""
    return api_get(f"{BASE}/clusters/", {"docket": docket_id}).get("results", [])


def get_parties(docket_id):
    """取得當事人"""
    return api_get(f"{BASE}/parties/", {"docket": docket_id}).get("results", [])


def download_pdf(filepath_local, save_path):
    """下載 RECAP PDF 文件"""
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
        print(f"    下載失敗: {e}")
    return False


def sanitize_filename(name, max_len=60):
    """清理檔名"""
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[:max_len]


def process_case(case_query):
    """處理單一案件：搜尋 → 取得所有可用資料 → 儲存
    注意：docket-entries, recap-documents, parties 端點需要付費會員，
    免費 token 只能用 search, dockets, clusters, opinions"""
    print(f"\n{'='*60}")
    print(f"搜尋: {case_query}")

    # 搜尋
    results = search_case(case_query)
    if not results.get("results"):
        print(f"  ❌ 未找到: {case_query}")
        return None

    first = results["results"][0]
    docket_id = first.get("docket_id")
    case_name = first.get("caseName", "unknown")
    print(f"  ✅ 找到: {case_name} (docket_id: {docket_id})")

    # 建立資料夾
    safe_name = sanitize_filename(case_name)
    case_dir = os.path.join(OUTPUT_DIR, f"{docket_id}_{safe_name}")
    os.makedirs(case_dir, exist_ok=True)

    # 儲存搜尋結果（包含 snippet 和基本資訊）
    with open(os.path.join(case_dir, "search_result.json"), "w") as f:
        json.dump(first, f, indent=2, ensure_ascii=False)

    # 1. Docket metadata（免費可用）
    print("  📋 取得 docket metadata...")
    docket = get_docket(docket_id)
    with open(os.path.join(case_dir, "docket_metadata.json"), "w") as f:
        json.dump(docket, f, indent=2, ensure_ascii=False)

    # 2. Docket entries（需要付費，graceful fallback）
    entries = []
    try:
        print("  📋 取得 docket entries...")
        entries = get_docket_entries(docket_id)
        with open(os.path.join(case_dir, "docket_entries.json"), "w") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 403:
            print("  ⚠️ docket-entries 需要付費會員，跳過")
        else:
            raise

    # 3. 下載 PDF（依賴 docket entries，跳過如無）
    pdf_count = 0
    if entries:
        print("  📄 下載 PDF 文件...")
        for entry in entries:
            if pdf_count >= MAX_PDFS_PER_CASE:
                break
            for doc in entry.get("recap_documents", []):
                if pdf_count >= MAX_PDFS_PER_CASE:
                    break
                fp = doc.get("filepath_local")
                if fp:
                    desc = doc.get("description", "unknown")
                    fname = f"doc_{doc['id']}_{sanitize_filename(desc)}.pdf"
                    save_path = os.path.join(case_dir, fname)
                    if not os.path.exists(save_path):
                        if download_pdf(fp, save_path):
                            pdf_count += 1
                            print(f"    📄 {fname}")
                        time.sleep(PDF_DOWNLOAD_DELAY)

    # 4. Opinion clusters（免費可用）
    print("  ⚖️ 取得判決意見書...")
    opinions = get_opinions(docket_id)
    with open(os.path.join(case_dir, "opinions.json"), "w") as f:
        json.dump(opinions, f, indent=2, ensure_ascii=False)

    # 取得每個 opinion cluster 的子意見書全文
    opinion_count = 0
    for cluster in opinions:
        cluster_id = cluster.get("id")
        sub_opinions = cluster.get("sub_opinions", [])
        for j, op_url in enumerate(sub_opinions):
            if isinstance(op_url, str) and op_url.startswith("http"):
                try:
                    op = api_get(op_url)
                    text = (op.get("plain_text")
                            or op.get("html_with_citations")
                            or op.get("html")
                            or "")
                    if text:
                        op_type = op.get("type", "unknown")
                        fname = f"opinion_c{cluster_id}_{j}_{op_type}.txt"
                        with open(os.path.join(case_dir, fname), "w", encoding="utf-8") as f:
                            f.write(text)
                        opinion_count += 1
                        print(f"    📝 {fname} ({len(text)} chars)")
                except Exception as e:
                    print(f"    ⚠️ opinion {j} 失敗: {e}")

    # 5. Parties（需要付費，graceful fallback）
    parties = []
    try:
        print("  👥 取得當事人...")
        parties = get_parties(docket_id)
        with open(os.path.join(case_dir, "parties.json"), "w") as f:
            json.dump(parties, f, indent=2, ensure_ascii=False)
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 403:
            print("  ⚠️ parties 需要付費會員，跳過")
        else:
            raise

    # 摘要
    summary = {
        "source": "CourtListener/RECAP",
        "search_query": case_query,
        "docket_id": docket_id,
        "case_name": case_name,
        "court": docket.get("court_id", ""),
        "date_filed": docket.get("date_filed", ""),
        "date_terminated": docket.get("date_terminated", ""),
        "num_entries": len(entries),
        "num_pdfs_downloaded": pdf_count,
        "num_opinion_clusters": len(opinions),
        "num_opinion_texts": opinion_count,
        "num_parties": len(parties),
        "nature_of_suit": docket.get("nature_of_suit", ""),
        "pacer_case_id": docket.get("pacer_case_id", ""),
        "ia_link": docket.get("filepath_ia", ""),
    }
    with open(os.path.join(case_dir, "case_summary.json"), "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"  📊 完成: entries={len(entries)}, PDFs={pdf_count}, opinion_texts={opinion_count}, parties={len(parties)}")
    return summary


def main():
    if COURTLISTENER_TOKEN == "YOUR_COURTLISTENER_TOKEN_HERE":
        print("❌ 請先在 config.py 中填入你的 CourtListener API token")
        print("   到 https://www.courtlistener.com/sign-in/ 免費註冊")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_summaries = []
    for query in CASES_TO_SEARCH:
        try:
            summary = process_case(query)
            if summary:
                all_summaries.append(summary)
        except Exception as e:
            print(f"  ❌ 錯誤: {e}")
        time.sleep(2)

    # 總摘要
    with open(os.path.join(OUTPUT_DIR, "_all_cases_summary.json"), "w") as f:
        json.dump(all_summaries, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"✅ CourtListener/RECAP 完成: {len(all_summaries)}/{len(CASES_TO_SEARCH)} 案件")
    print(f"   資料位於: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
