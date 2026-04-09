#!/usr/bin/env python3
"""
來源 5: Pile-of-Law (HuggingFace)
從預建法律語料庫中擷取代表性樣本（不是爬取，是從 dataset 取樣）
"""
import json
import os
import sys

OUTPUT_DIR = "data/pile_of_law_samples"

# --- 要取樣的子集 ---
# Pile-of-Law 的子集名稱可能隨版本變動
# 如果某個子集名稱不對，腳本會跳過並記錄
SUBSETS_TO_SAMPLE = [
    {
        "name": "courtlistener_opinions",
        "description": "CourtListener 法院判決意見書",
        "why": "判決書全文，可用於 RAG 語料庫",
    },
    {
        "name": "courtlistener_docket_entry_documents",
        "description": "CourtListener 案件摘要條目",
        "why": "程序性文件紀錄",
    },
    {
        "name": "atticus_contracts",
        "description": "合約文件",
        "why": "法律文件的另一種類型",
    },
    {
        "name": "federal_register",
        "description": "聯邦公報",
        "why": "行政法規和公告",
    },
]

SAMPLES_PER_SUBSET = 10


def sample_subset(subset_info):
    """從一個子集中取樣"""
    name = subset_info["name"]
    print(f"\n{'='*60}")
    print(f"載入子集: {name}")
    print(f"  說明: {subset_info['description']}")

    subset_dir = os.path.join(OUTPUT_DIR, name)
    os.makedirs(subset_dir, exist_ok=True)

    try:
        from datasets import load_dataset

        # streaming 模式：不下載整個資料集，逐筆讀取
        ds = load_dataset(
            "pile-of-law/pile-of-law",
            name,
            split="train",
            streaming=True,
            trust_remote_code=True,
        )

        samples = []
        for i, item in enumerate(ds):
            if i >= SAMPLES_PER_SUBSET:
                break

            samples.append(item)

            # 儲存個別樣本
            with open(os.path.join(subset_dir, f"sample_{i}.json"), "w", encoding="utf-8") as f:
                json.dump(item, f, indent=2, ensure_ascii=False, default=str)

        # 儲存子集摘要
        summary = {
            "source": "Pile-of-Law (HuggingFace)",
            "subset": name,
            "description": subset_info["description"],
            "why_selected": subset_info["why"],
            "num_samples": len(samples),
            "sample_keys": list(samples[0].keys()) if samples else [],
            "first_sample_preview": {
                k: str(v)[:300] for k, v in samples[0].items()
            } if samples else {},
        }
        with open(os.path.join(subset_dir, "subset_summary.json"), "w") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        print(f"  ✅ 取得 {len(samples)} 個樣本")
        print(f"     欄位: {list(samples[0].keys()) if samples else 'N/A'}")
        return summary

    except Exception as e:
        error_msg = str(e)
        print(f"  ❌ 錯誤: {error_msg}")

        # 記錄錯誤
        with open(os.path.join(subset_dir, "error.json"), "w") as f:
            json.dump({
                "subset": name,
                "error": error_msg,
                "hint": "子集名稱可能不對，嘗試: load_dataset('pile-of-law/pile-of-law', streaming=True) 查看可用子集",
            }, f, indent=2)

        return None


def sample_cap_huggingface():
    """額外：從 CAP 的 HuggingFace 版本取樣"""
    print(f"\n{'='*60}")
    print("載入 CAP HuggingFace 資料集")

    cap_dir = os.path.join(OUTPUT_DIR, "cap_huggingface")
    os.makedirs(cap_dir, exist_ok=True)

    try:
        from datasets import load_dataset

        ds = load_dataset(
            "free-law/Caselaw_Access_Project",
            split="train",
            streaming=True,
            trust_remote_code=True,
        )

        samples = []
        for i, item in enumerate(ds):
            if i >= SAMPLES_PER_SUBSET:
                break
            samples.append(item)
            with open(os.path.join(cap_dir, f"sample_{i}.json"), "w", encoding="utf-8") as f:
                json.dump(item, f, indent=2, ensure_ascii=False, default=str)

        summary = {
            "source": "Caselaw Access Project (HuggingFace)",
            "num_samples": len(samples),
            "sample_keys": list(samples[0].keys()) if samples else [],
        }
        with open(os.path.join(cap_dir, "subset_summary.json"), "w") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        print(f"  ✅ 取得 {len(samples)} 個 CAP 樣本")
        return summary

    except Exception as e:
        print(f"  ❌ 錯誤: {e}")
        return None


def main():
    # 檢查 datasets 是否安裝
    try:
        import datasets
    except ImportError:
        print("❌ 需要安裝 datasets:")
        print("   pip install datasets --break-system-packages")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_summaries = []

    # Pile-of-Law 子集
    for subset_info in SUBSETS_TO_SAMPLE:
        summary = sample_subset(subset_info)
        if summary:
            all_summaries.append(summary)

    # CAP HuggingFace
    cap_summary = sample_cap_huggingface()
    if cap_summary:
        all_summaries.append(cap_summary)

    with open(os.path.join(OUTPUT_DIR, "_all_subsets_summary.json"), "w") as f:
        json.dump(all_summaries, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"✅ Pile-of-Law 完成: {len(all_summaries)} 子集取樣成功")
    print(f"   資料位於: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
