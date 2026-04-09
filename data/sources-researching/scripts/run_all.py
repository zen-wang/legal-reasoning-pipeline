#!/usr/bin/env python3
"""
主執行腳本：依序跑所有來源的爬蟲
用法: python run_all.py          ← 跑全部
      python run_all.py 1        ← 只跑來源 1 (CourtListener)
      python run_all.py 3 4      ← 只跑來源 3 和 4
"""
import subprocess
import sys
import os
import time

SCRAPERS = {
    "1": ("scraper_courtlistener.py", "CourtListener / RECAP"),
    "3": ("scraper_edgar.py", "SEC EDGAR"),
    "4": ("scraper_cap.py", "Caselaw Access Project"),
    "5": ("scraper_pile_of_law.py", "Pile-of-Law (HuggingFace)"),
    "6": ("scraper_ia_recap.py", "RECAP on Internet Archive"),
}


def run_scraper(num, script, name):
    print(f"\n{'#'*60}")
    print(f"# 來源 {num}: {name}")
    print(f"# 腳本: {script}")
    print(f"{'#'*60}\n")

    result = subprocess.run(
        [sys.executable, script],
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )

    if result.returncode == 0:
        print(f"\n✅ 來源 {num} ({name}) 完成")
    else:
        print(f"\n❌ 來源 {num} ({name}) 失敗 (exit code: {result.returncode})")

    return result.returncode


def main():
    # 確保 data 目錄存在
    os.makedirs("data", exist_ok=True)

    # 決定要跑哪些
    if len(sys.argv) > 1:
        targets = sys.argv[1:]
    else:
        targets = sorted(SCRAPERS.keys())

    print("=" * 60)
    print("法律資料收集管線")
    print("=" * 60)
    print(f"目標來源: {', '.join(targets)}")
    print()

    results = {}
    for num in targets:
        if num not in SCRAPERS:
            print(f"⚠️ 未知來源: {num} (可用: {', '.join(sorted(SCRAPERS.keys()))})")
            continue

        script, name = SCRAPERS[num]
        code = run_scraper(num, script, name)
        results[num] = code
        time.sleep(2)

    # 總結
    print(f"\n{'='*60}")
    print("執行總結")
    print(f"{'='*60}")
    for num in sorted(results.keys()):
        _, name = SCRAPERS[num]
        status = "✅ 成功" if results[num] == 0 else "❌ 失敗"
        print(f"  來源 {num} ({name}): {status}")

    # 統計檔案數
    print(f"\n檔案統計:")
    for dirpath in ["data/courtlistener_cases", "data/edgar_cases",
                     "data/cap_cases", "data/pile_of_law_samples",
                     "data/ia_recap_cases"]:
        if os.path.exists(dirpath):
            file_count = sum(1 for _, _, files in os.walk(dirpath) for _ in files)
            print(f"  {dirpath}: {file_count} 個檔案")


if __name__ == "__main__":
    main()
