#!/usr/bin/env python3
"""
來源 6: RECAP on Internet Archive
從 IA 的 RECAP 歸檔中下載 PACER 法院文件 (PDF)
"""
import requests
import json
import time
import os

from config import REQUEST_DELAY, MAX_PDFS_PER_CASE

# --- 設定 ---
OUTPUT_DIR = "data/ia_recap_cases"

# --- IA RECAP 的搜尋查詢 ---
# Internet Archive 的 RECAP 集合名稱: usfederalcourts
# 每個案件的 identifier 格式: gov.uscourts.{court}.{pacer_case_id}
SEARCH_QUERIES = [
    "title:(Securities AND Exchange AND Commission)",
    "title:(Enron)",
    "title:(Tesla)",
    "title:(fraud)",
    "title:(insider AND trading)",
    "title:(Ponzi)",
    "title:(Madoff)",
    "title:(FTX)",
    "title:(Theranos)",
    "title:(Wirecard)",
]


def ia_search(query, max_results=3):
    """在 Internet Archive 搜尋 RECAP 案件"""
    url = "https://archive.org/advancedsearch.php"
    params = {
        "q": f'collection:usfederalcourts AND {query}',
        "fl[]": ["identifier", "title", "date", "description", "item_count"],
        "sort[]": "downloads desc",
        "rows": max_results,
        "output": "json",
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return resp.json()


def ia_get_metadata(identifier):
    """取得 IA 項目的完整元資料"""
    url = f"https://archive.org/metadata/{identifier}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    time.sleep(0.5)
    return resp.json()


def ia_list_files(identifier):
    """列出 IA 項目中的所有文件"""
    url = f"https://archive.org/metadata/{identifier}/files"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json().get("result", [])


def ia_download_file(identifier, filename, save_path):
    """從 IA 下載文件"""
    url = f"https://archive.org/download/{identifier}/{filename}"
    try:
        resp = requests.get(url, timeout=120)
        if resp.status_code == 200 and len(resp.content) > 100:
            with open(save_path, "wb") as f:
                f.write(resp.content)
            return True
    except Exception as e:
        print(f"    下載失敗: {e}")
    return False


def process_ia_case(identifier, title=""):
    """處理單一 IA RECAP 案件"""
    print(f"\n  處理: {identifier}")
    if title:
        print(f"  標題: {title[:80]}")

    case_dir = os.path.join(OUTPUT_DIR, identifier)
    os.makedirs(case_dir, exist_ok=True)

    # 取得元資料
    try:
        metadata = ia_get_metadata(identifier)
        with open(os.path.join(case_dir, "ia_metadata.json"), "w") as f:
            json.dump(metadata.get("metadata", {}), f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"    ⚠️ 元資料取得失敗: {e}")
        metadata = {}

    # 列出文件
    try:
        files = ia_list_files(identifier)
    except Exception as e:
        print(f"    ❌ 文件列表失敗: {e}")
        return None

    # 分類文件
    pdf_files = [f for f in files if f.get("name", "").lower().endswith(".pdf")]
    xml_files = [f for f in files if f.get("name", "").lower().endswith(".xml")]
    json_files = [f for f in files if f.get("name", "").lower().endswith(".json")]

    # 儲存文件清單
    file_manifest = {
        "identifier": identifier,
        "title": title,
        "total_files": len(files),
        "pdf_count": len(pdf_files),
        "xml_count": len(xml_files),
        "json_count": len(json_files),
        "pdf_files": [f["name"] for f in pdf_files[:20]],  # 只列前 20 個
    }
    with open(os.path.join(case_dir, "file_manifest.json"), "w") as f:
        json.dump(file_manifest, f, indent=2, ensure_ascii=False)

    # 下載 docket XML/JSON（通常很小）
    for df in (xml_files + json_files)[:3]:
        fname = df["name"]
        if "docket" in fname.lower():
            safe_fname = fname.replace("/", "_")
            save_path = os.path.join(case_dir, safe_fname)
            if not os.path.exists(save_path):
                ia_download_file(identifier, fname, save_path)
                print(f"    📋 Docket: {safe_fname}")

    # 下載 PDF（前 N 個）
    pdf_downloaded = 0
    for pdf in pdf_files[:MAX_PDFS_PER_CASE]:
        fname = pdf["name"]
        safe_fname = fname.replace("/", "_").split("/")[-1]
        save_path = os.path.join(case_dir, safe_fname)
        if not os.path.exists(save_path):
            if ia_download_file(identifier, fname, save_path):
                pdf_downloaded += 1
                size_mb = int(pdf.get("size", 0)) / (1024 * 1024)
                print(f"    📄 PDF: {safe_fname} ({size_mb:.1f} MB)")
            time.sleep(0.5)

    print(f"    📊 PDF: {pdf_downloaded}/{len(pdf_files)} 下載")

    return {
        "source": "RECAP on Internet Archive",
        "identifier": identifier,
        "title": title,
        "total_files": len(files),
        "pdfs_available": len(pdf_files),
        "pdfs_downloaded": pdf_downloaded,
    }


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 搜尋並收集案件
    print("===== 搜尋 Internet Archive RECAP 案件 =====")
    all_cases = []
    seen_ids = set()

    for query in SEARCH_QUERIES:
        print(f"\n搜尋: {query}")
        try:
            results = ia_search(query, max_results=2)
            docs = results.get("response", {}).get("docs", [])
            for doc in docs:
                ident = doc.get("identifier", "")
                if ident and ident not in seen_ids:
                    seen_ids.add(ident)
                    all_cases.append(doc)
                    print(f"  找到: {ident}")
        except Exception as e:
            print(f"  ❌ 搜尋錯誤: {e}")
        time.sleep(1)

    print(f"\n共找到 {len(all_cases)} 個不重複的案件")

    # 處理前 10 個
    all_summaries = []
    for case_doc in all_cases[:10]:
        try:
            summary = process_ia_case(
                case_doc.get("identifier", ""),
                case_doc.get("title", ""),
            )
            if summary:
                all_summaries.append(summary)
        except Exception as e:
            print(f"  ❌ 錯誤: {e}")
        time.sleep(2)

    with open(os.path.join(OUTPUT_DIR, "_all_cases_summary.json"), "w") as f:
        json.dump(all_summaries, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"✅ IA RECAP 完成: {len(all_summaries)} 案件")
    print(f"   資料位於: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
