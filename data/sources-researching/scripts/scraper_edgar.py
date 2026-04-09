#!/usr/bin/env python3
"""
來源 3: SEC EDGAR
抓取 10 家涉及詐欺醜聞公司的申報文件 (10-K, 8-K, 10-Q, 20-F)
+ 用 EFTS 搜尋未上市公司的執法文件
"""
import requests
import json
import time
import os
import sys

from config import SEC_USER_AGENT, REQUEST_DELAY

# --- 設定 ---
HEADERS = {
    "User-Agent": SEC_USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
}
OUTPUT_DIR = "data/edgar_cases"
MAX_FILINGS_PER_COMPANY = 10
TARGET_FORMS = {"10-K", "8-K", "10-Q", "20-F", "6-K"}

# --- 10 家代表性公司 ---
COMPANIES = [
    {"name": "Tesla", "cik": 1318605, "why": "Musk tweets 證券詐欺"},
    {"name": "Enron", "cik": 1024401, "why": "會計詐欺典型案例"},
    {"name": "Luckin_Coffee", "cik": 1767582, "why": "跨國財務造假"},
    {"name": "Nikola", "cik": 1731289, "why": "SPAC 詐欺"},
    {"name": "Wells_Fargo", "cik": 72971, "why": "假帳戶醜聞"},
    {"name": "WorldCom", "cik": 723527, "why": "會計詐欺 — $11B revenue inflation"},
    {"name": "Boeing", "cik": 12927, "why": "737 MAX 安全隱瞞"},
    {"name": "Valeant_Pharmaceuticals", "cik": 885590, "why": "藥品定價詐欺"},
    {"name": "Kraft_Heinz", "cik": 1637459, "why": "SEC 調查會計問題"},
    {"name": "Under_Armour", "cik": 1336917, "why": "營收認列調查"},
]

# 沒有 CIK 的案件用 EFTS 搜尋
ENFORCEMENT_SEARCHES = [
    "Theranos Holmes fraud",
    "FTX Bankman-Fried",
    "Wirecard fraud",
]


def sec_get(url, params=None):
    """帶速率限制的 SEC API 請求 (10 req/sec)"""
    resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
    resp.raise_for_status()
    time.sleep(0.15)  # SEC 限制 10/sec
    return resp


def get_company_submissions(cik):
    """取得公司的完整申報歷史"""
    cik_padded = str(cik).zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    return sec_get(url).json()


def download_filing(cik, accession_no_raw, primary_doc, save_path):
    """下載特定申報文件"""
    accession = accession_no_raw.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{primary_doc}"
    try:
        resp = sec_get(url)
        if resp.status_code == 200:
            with open(save_path, "wb") as f:
                f.write(resp.content)
            return True
    except Exception as e:
        print(f"    下載失敗: {e}")
    return False


def search_efts(query):
    """用 EDGAR Full-Text Search 搜尋（官方免費端點）"""
    url = "https://efts.sec.gov/LATEST/search-index"
    params = {"q": f'"{query}"', "from": 0, "size": 10}
    try:
        resp = sec_get(url, params)
        return resp.json()
    except Exception:
        # 嘗試不加引號
        params2 = {"q": query, "from": 0, "size": 10}
        try:
            resp = sec_get(url, params2)
            return resp.json()
        except Exception as e2:
            print(f"    EFTS 搜尋失敗: {e2}")
            return None


def process_company(company):
    """處理單一公司：取得申報歷史 + 下載文件"""
    name = company["name"]
    cik = company["cik"]

    print(f"\n{'='*60}")
    print(f"處理: {name} (CIK: {cik}) - {company['why']}")

    case_dir = os.path.join(OUTPUT_DIR, name)
    os.makedirs(case_dir, exist_ok=True)

    # 取得申報歷史
    print("  📋 取得申報歷史...")
    submissions = get_company_submissions(cik)

    # 儲存完整元資料
    with open(os.path.join(case_dir, "submissions.json"), "w") as f:
        json.dump(submissions, f, indent=2, ensure_ascii=False)

    # 提取公司基本資訊
    company_info = {
        "source": "SEC EDGAR",
        "name": submissions.get("name", ""),
        "cik": cik,
        "sic": submissions.get("sic", ""),
        "sic_description": submissions.get("sicDescription", ""),
        "tickers": submissions.get("tickers", []),
        "exchanges": submissions.get("exchanges", []),
        "stateOfIncorporation": submissions.get("stateOfIncorporation", ""),
        "why_selected": company["why"],
    }

    # 下載目標類型的申報文件
    print("  📄 下載申報文件...")
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])
    descriptions = recent.get("primaryDocDescription", [])

    downloaded = 0
    downloaded_files = []

    for i, form in enumerate(forms):
        if downloaded >= MAX_FILINGS_PER_COMPANY:
            break
        if form in TARGET_FORMS:
            accession = accessions[i]
            doc = primary_docs[i]
            date = dates[i]
            desc = descriptions[i] if i < len(descriptions) else ""

            ext = os.path.splitext(doc)[1] or ".htm"
            fname = f"{form}_{date}_{doc}"
            fname = "".join(c if c.isalnum() or c in "._-" else "_" for c in fname)
            save_path = os.path.join(case_dir, fname)

            if not os.path.exists(save_path):
                if download_filing(cik, accession, doc, save_path):
                    downloaded += 1
                    downloaded_files.append({
                        "form": form,
                        "date": date,
                        "accession": accession,
                        "filename": fname,
                        "description": desc,
                    })
                    print(f"    📄 {form} ({date}): {fname}")

    company_info["num_filings_downloaded"] = downloaded
    company_info["downloaded_files"] = downloaded_files
    company_info["total_filings_available"] = len(forms)

    with open(os.path.join(case_dir, "case_summary.json"), "w") as f:
        json.dump(company_info, f, indent=2, ensure_ascii=False)

    print(f"  📊 完成: {downloaded}/{MAX_FILINGS_PER_COMPANY} 文件下載")
    return company_info


def process_enforcement_search(query):
    """用 EFTS 搜尋沒有 CIK 的執法案件"""
    print(f"\n{'='*60}")
    print(f"EFTS 搜尋: {query}")

    safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in query)
    case_dir = os.path.join(OUTPUT_DIR, f"enforcement_{safe_name}")
    os.makedirs(case_dir, exist_ok=True)

    result = search_efts(query)
    if result:
        with open(os.path.join(case_dir, "efts_results.json"), "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"  ✅ 搜尋結果已儲存")
    else:
        print(f"  ⚠️ EFTS 搜尋無結果或端點格式不同")
        print(f"     備選：用 Saurabh 的 sec_litigation_scraper 搜尋")


def main():
    if "your_email" in SEC_USER_AGENT.lower():
        print("⚠️ 請在 config.py 中更新 SEC_USER_AGENT 為你的真實 email")
        print("   格式: 'ASU_CIPS_Lab your_real_email@asu.edu'")
        print("   繼續執行但可能被限制...\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 處理有 CIK 的公司
    all_summaries = []
    for company in COMPANIES:
        try:
            summary = process_company(company)
            all_summaries.append(summary)
        except Exception as e:
            print(f"  ❌ 錯誤: {e}")
        time.sleep(1)

    # 處理執法搜尋
    for query in ENFORCEMENT_SEARCHES:
        try:
            process_enforcement_search(query)
        except Exception as e:
            print(f"  ❌ 錯誤: {e}")
        time.sleep(1)

    # 總摘要
    with open(os.path.join(OUTPUT_DIR, "_all_cases_summary.json"), "w") as f:
        json.dump(all_summaries, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"✅ SEC EDGAR 完成: {len(all_summaries)} 公司 + {len(ENFORCEMENT_SEARCHES)} 執法搜尋")
    print(f"   資料位於: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
