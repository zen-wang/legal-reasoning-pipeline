#!/usr/bin/env python3
"""
來源 4: Caselaw Access Project (Harvard) — 透過 CourtListener API 存取
CAP 已將 API 存取導向 CourtListener，所以用 CourtListener 的 search + opinions 端點
抓取 10 個奠定證券法基礎的最高法院判決意見書全文
"""
import requests
import json
import time
import os
import sys

from config import COURTLISTENER_TOKEN, REQUEST_DELAY

# --- 設定 ---
HEADERS = {"Authorization": f"Token {COURTLISTENER_TOKEN}"}
BASE = "https://www.courtlistener.com/api/rest/v4"
OUTPUT_DIR = "data/cap_cases"

# --- 10 個標誌性證券法判決 ---
LANDMARK_CASES = [
    {"name": "Basic Inc. v. Levinson",
     "search": "Basic Levinson fraud market",
     "court": "scotus", "year": 1988,
     "principle": "fraud-on-the-market presumption of reliance"},

    {"name": "Dura Pharmaceuticals v. Broudo",
     "search": "Dura Pharmaceuticals Broudo loss causation",
     "court": "scotus", "year": 2005,
     "principle": "loss causation pleading standard"},

    {"name": "Tellabs v. Makor Issues & Rights",
     "search": "Tellabs Makor scienter",
     "court": "scotus", "year": 2007,
     "principle": "strong inference of scienter"},

    {"name": "Stoneridge v. Scientific-Atlanta",
     "search": "Stoneridge Scientific-Atlanta scheme liability",
     "court": "scotus", "year": 2008,
     "principle": "limits of scheme liability"},

    {"name": "Morrison v. National Australia Bank",
     "search": "Morrison National Australia extraterritorial",
     "court": "scotus", "year": 2010,
     "principle": "extraterritorial reach of 10b-5"},

    {"name": "Janus Capital v. First Derivative",
     "search": "Janus Capital First Derivative maker",
     "court": "scotus", "year": 2011,
     "principle": "who is the 'maker' of a statement"},

    {"name": "Halliburton v. Erica P. John Fund II",
     "search": "Halliburton Erica John price impact",
     "court": "scotus", "year": 2014,
     "principle": "rebutting fraud-on-market at class cert"},

    {"name": "Omnicare v. Laborers District Council",
     "search": "Omnicare Laborers opinion statements",
     "court": "scotus", "year": 2015,
     "principle": "liability for opinion statements"},

    {"name": "Lorenzo v. SEC",
     "search": "Lorenzo SEC scheme disseminating",
     "court": "scotus", "year": 2019,
     "principle": "scheme liability for disseminating falsehoods"},

    {"name": "Goldman Sachs v. Arkansas Teacher Retirement",
     "search": "Goldman Sachs Arkansas Teacher price impact",
     "court": "scotus", "year": 2021,
     "principle": "price impact and class certification"},
]


def api_get(url, params=None):
    """帶速率限制的 GET 請求"""
    resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return resp.json()


def search_opinion(query, court="scotus"):
    """用 CourtListener search API 搜尋判決意見書 (type=o)"""
    return api_get(f"{BASE}/search/", {
        "q": query,
        "type": "o",
        "court": court,
        "order_by": "score desc",
    })


def get_cluster(cluster_id):
    """取得 opinion cluster（一個案件的所有意見書集合）"""
    return api_get(f"{BASE}/clusters/{cluster_id}/")


def get_opinion_detail(opinion_url):
    """取得單一意見書的完整內容（從 URL）"""
    return api_get(opinion_url)


def get_citing_opinions(cluster_id, max_results=10):
    """取得引用此案的其他判決（citation network）"""
    return api_get(f"{BASE}/search/", {
        "q": f"cites:({cluster_id})",
        "type": "o",
        "order_by": "dateFiled desc",
        "page_size": max_results,
    })


def process_case(case_info):
    """處理單一案件：搜尋 → 取得意見書全文 → 儲存"""
    name = case_info["name"]
    print(f"\n{'='*60}")
    print(f"搜尋: {name} ({case_info['year']})")
    print(f"  法律原則: {case_info['principle']}")

    safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in name)
    case_dir = os.path.join(OUTPUT_DIR, safe_name)
    os.makedirs(case_dir, exist_ok=True)

    # 步驟 1: 搜尋
    results = search_opinion(case_info["search"], case_info["court"])
    hits = results.get("results", [])

    if not hits:
        print(f"  SCOTUS 未找到，放寬搜尋...")
        results = search_opinion(case_info["search"])
        hits = results.get("results", [])

    if not hits:
        print(f"  ❌ 完全未找到: {name}")
        return None

    best = hits[0]
    cluster_id = best.get("cluster_id")
    print(f"  ✅ 找到: {best.get('caseName', name)}")
    print(f"     cluster_id: {cluster_id}")
    print(f"     日期: {best.get('dateFiled')}")

    with open(os.path.join(case_dir, "search_result.json"), "w") as f:
        json.dump(best, f, indent=2, ensure_ascii=False)

    # 步驟 2: 取得 cluster 詳情
    print(f"  📋 取得 cluster 詳情...")
    cluster = get_cluster(cluster_id)
    with open(os.path.join(case_dir, "cluster.json"), "w") as f:
        json.dump(cluster, f, indent=2, ensure_ascii=False)

    # 步驟 3: 取得每個意見書的全文
    print(f"  ⚖️ 取得意見書全文...")
    sub_opinions = cluster.get("sub_opinions", [])
    opinion_texts = []

    for i, op_ref in enumerate(sub_opinions):
        try:
            # sub_opinions 通常是 URL 列表
            if isinstance(op_ref, str) and op_ref.startswith("http"):
                opinion = api_get(op_ref)
            elif isinstance(op_ref, dict):
                opinion = op_ref
            else:
                opinion = api_get(f"{BASE}/opinions/{op_ref}/")

            opinion_texts.append(opinion)

            # 提取文字：CourtListener 有多種格式
            text = (opinion.get("plain_text")
                    or opinion.get("html_with_citations")
                    or opinion.get("html")
                    or opinion.get("xml_harvard")
                    or "")
            op_type = opinion.get("type", "unknown")

            if text:
                fname = f"opinion_{i}_{op_type}.txt"
                with open(os.path.join(case_dir, fname), "w", encoding="utf-8") as f:
                    f.write(text)
                print(f"    📝 opinion_{i}: type={op_type}, {len(text)} chars")

        except Exception as e:
            print(f"    ⚠️ opinion {i} 失敗: {e}")

    # 備選：如果 sub_opinions 為空，用搜尋結果的 snippet
    if not opinion_texts and best.get("snippet"):
        with open(os.path.join(case_dir, "opinion_snippet.txt"), "w", encoding="utf-8") as f:
            f.write(best.get("snippet", ""))
        print(f"    📝 僅取得 snippet（意見書全文可能需要不同端點）")

    # 步驟 4: 引用關係（知識圖譜用）
    print(f"  🔗 取得引用關係...")
    citing_cases = []
    try:
        citing = get_citing_opinions(cluster_id)
        citing_cases = citing.get("results", [])
        cite_summary = [{
            "name": c.get("caseName"),
            "date": c.get("dateFiled"),
            "court": c.get("court"),
            "citation": c.get("citation", []),
        } for c in citing_cases]

        with open(os.path.join(case_dir, "cited_by.json"), "w") as f:
            json.dump(cite_summary, f, indent=2, ensure_ascii=False)
        print(f"    被 {len(citing_cases)} 個判決引用")
    except Exception as e:
        print(f"    ⚠️ 引用關係失敗: {e}")

    # 步驟 5: 結構化元資料
    metadata = {
        "source": "Caselaw Access Project via CourtListener",
        "case_name": name,
        "cluster_id": cluster_id,
        "case_name_cl": best.get("caseName"),
        "date_filed": best.get("dateFiled"),
        "court": best.get("court"),
        "court_citation_string": best.get("court_citation_string"),
        "citations": best.get("citation", []),
        "docket_number": best.get("docketNumber"),
        "docket_id": best.get("docket_id"),
        "judges": cluster.get("judges", ""),
        "num_opinions": len(opinion_texts),
        "num_cited_by": len(citing_cases),
        "absolute_url": best.get("absolute_url"),
        "legal_principle": case_info["principle"],
        "year": case_info["year"],
    }
    with open(os.path.join(case_dir, "metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    print(f"  📊 完成: {len(opinion_texts)} 意見書, 被引用 {len(citing_cases)} 次")
    return metadata


def main():
    if COURTLISTENER_TOKEN == "YOUR_COURTLISTENER_TOKEN_HERE":
        print("❌ 請先在 config.py 中填入你的 CourtListener API token")
        print("   到 https://www.courtlistener.com/sign-in/ 免費註冊")
        print("")
        print("   注意：CAP 已將 API 導向 CourtListener，")
        print("   所以這個腳本使用 CourtListener token（跟來源 1 相同）")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_summaries = []
    for case_info in LANDMARK_CASES:
        try:
            metadata = process_case(case_info)
            if metadata:
                all_summaries.append(metadata)
        except Exception as e:
            print(f"  ❌ 錯誤: {e}")
        time.sleep(2)

    with open(os.path.join(OUTPUT_DIR, "_all_cases_summary.json"), "w") as f:
        json.dump(all_summaries, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"✅ CAP (via CourtListener) 完成: {len(all_summaries)}/{len(LANDMARK_CASES)} 判決")
    print(f"   資料位於: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()