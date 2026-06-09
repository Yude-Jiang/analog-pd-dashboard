"""
fetch_sanan_ic_pdf.py — Extract Sanan IC segment revenue from annual report PDFs.

Pipeline:
  1. Query CNINFO for Sanan (600703) 年报 PDF download URLs (2019–2024)
  2. Download each PDF
  3. Send to Claude API → extract "集成电路产品" row from 主营业务分产品情况 table
  4. Compute IC NI via proportional allocation: IC_NI = Total_NI × (IC_Rev / Total_Rev)
  5. Overwrite Sanan revenue / net_income / margin in data.json

Usage:
    python fetch_sanan_ic_pdf.py [--dry-run] [--years 2022 2023 2024]
"""

import argparse
import json
import os
import sys
import time
import tempfile
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
STOCK_CODE   = "600703"
COMPANY_KEY  = "Sanan"
TARGET_YEARS = [2019, 2020, 2021, 2022, 2023, 2024]

# CNINFO announcement query endpoint
CNINFO_QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_BASE  = "https://static.cninfo.com.cn/"

_HERE = Path(__file__).parent

# ---------------------------------------------------------------------------
# CNINFO helpers
# ---------------------------------------------------------------------------

def _cninfo_search(year: int) -> dict | None:
    """Return the first 年报 announcement record for STOCK_CODE in the given year."""
    # Annual reports filed in Jan–Apr of year+1
    start = f"{year + 1}-01-01"
    end   = f"{year + 1}-05-31"
    payload = {
        "stock":    f"{STOCK_CODE},三安光电",
        "category": "category_ndbg_szsh",   # 年度报告
        "plate":    "sh",
        "seDate":   f"{start}~{end}",
        "tabName":  "fulltext",
        "pageSize": 10,
        "pageNum":  1,
        "column":   "sse",
        "sortName": "pubdate",
        "sortType": "desc",
        "isHLtitle": True,
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer":    "https://www.cninfo.com.cn/",
    }
    try:
        r = requests.post(CNINFO_QUERY, data=payload, headers=headers, timeout=20)
        r.raise_for_status()
        items = r.json().get("announcements") or []
        # Pick the record whose title contains the report year
        for item in items:
            title = item.get("announcementTitle", "")
            if str(year) in title and "年度报告" in title and "摘要" not in title:
                return item
        return items[0] if items else None
    except Exception as e:
        print(f"  [CNINFO] search error for {year}: {e}")
        return None


def download_pdf(year: int, dest_dir: str) -> str | None:
    """Download the annual report PDF for the given year into dest_dir. Returns local path."""
    record = _cninfo_search(year)
    if not record:
        print(f"  [CNINFO] no record found for {year}")
        return None

    pdf_url  = CNINFO_BASE + record.get("adjunctUrl", "")
    title    = record.get("announcementTitle", "")
    filename = f"sanan_{year}.pdf"
    dest     = os.path.join(dest_dir, filename)

    print(f"  [{year}] {title}")
    print(f"         {pdf_url}")
    try:
        r = requests.get(pdf_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        size_mb = os.path.getsize(dest) / 1e6
        print(f"         → saved {filename} ({size_mb:.1f} MB)")
        return dest
    except Exception as e:
        print(f"  [download] error: {e}")
        return None

# ---------------------------------------------------------------------------
# Claude API — extract IC segment from PDF
# ---------------------------------------------------------------------------

EXTRACT_PROMPT = """
You are a financial data extraction assistant.

In this annual report PDF for 三安光电 (Sanan Optoelectronics, stock 600703), find the table titled
"主营业务分产品情况" (Main Business Breakdown by Product).

Extract ONLY the row for "集成电路产品" (Integrated Circuit Products).

Return a JSON object with these exact keys (all values in 元/Yuan as reported):
{
  "ic_revenue": <float>,        // 营业收入 (主营收入)
  "ic_cost": <float>,           // 营业成本 (主营成本)
  "ic_gross_margin_pct": <float> // 毛利率 as a decimal, e.g. 0.0564 for 5.64%
}

If the table is not found or the IC row does not exist in this report, return:
{"not_found": true, "reason": "<brief explanation>"}

Return only the JSON object, no other text.
"""


def extract_ic_from_pdf(pdf_path: str, year: int) -> dict | None:
    """Send PDF to Gemini API and extract IC segment figures."""
    import google.generativeai as genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("  [Gemini] GEMINI_API_KEY not set — skipping extraction")
        return None

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-pro")

    file_size = os.path.getsize(pdf_path)
    print(f"  [Gemini] uploading {Path(pdf_path).name} ({file_size/1e6:.1f} MB)…")

    try:
        # Upload via Files API (handles large PDFs gracefully)
        uploaded = genai.upload_file(pdf_path, mime_type="application/pdf")
        print(f"  [Gemini] file uploaded, extracting…")

        response = model.generate_content([uploaded, EXTRACT_PROMPT])
        raw = response.text.strip()
        print(f"  [Gemini] response: {raw}")

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        result = json.loads(raw)
        if result.get("not_found"):
            print(f"  [Gemini] IC row not found: {result.get('reason', '')}")
            return None

        # Clean up uploaded file
        try:
            genai.delete_file(uploaded.name)
        except Exception:
            pass

        return result
    except Exception as e:
        print(f"  [Gemini] error: {e}")
        return None

# ---------------------------------------------------------------------------
# data.json update
# ---------------------------------------------------------------------------

def update_data_json(dry_run: bool, ic_by_year: dict):
    """
    ic_by_year: {year_int: {"ic_revenue_mrm": float, "ic_ni_mrm": float, "ic_margin": float}}
    All values in M RMB.
    """
    data_path = _HERE / "data.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    sanan = data[COMPANY_KEY]

    print("\n=== Proposed changes to Sanan in data.json ===")
    print(f"{'Year':<6} {'Old Rev':>10} {'New Rev':>10} {'Old NI':>10} {'New NI':>10} {'New Margin':>12}")
    print("-" * 60)

    for yr, vals in sorted(ic_by_year.items()):
        y = str(yr)
        old_rev = sanan["revenue"].get(y)
        old_ni  = sanan["net_income"].get(y)
        new_rev = round(vals["ic_revenue_mrm"], 1)
        new_ni  = round(vals["ic_ni_mrm"], 1)
        new_mg  = round(vals["ic_margin"], 4)
        print(f"{y:<6} {old_rev!s:>10} {new_rev!s:>10} {old_ni!s:>10} {new_ni!s:>10} {new_mg*100:>11.2f}%")

        if not dry_run:
            sanan["revenue"][y]    = new_rev
            sanan["net_income"][y] = new_ni
            sanan["margin"][y]     = new_mg

    if not dry_run:
        # Mark source as segment-adjusted
        if "audit" not in sanan:
            sanan["audit"] = {}
        sanan["audit"]["segment_note"] = "集成电路产品分部数据（年报PDF提取）; NI按营收比例分摊"
        data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n✅ data.json updated ({len(ic_by_year)} years)")
    else:
        print("\n(dry-run — data.json not modified)")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Show proposed changes without writing")
    parser.add_argument("--years",   nargs="+", type=int, default=TARGET_YEARS)
    args = parser.parse_args()

    data_path = _HERE / "data.json"
    raw_data  = json.loads(data_path.read_text(encoding="utf-8"))
    sanan     = raw_data[COMPANY_KEY]

    ic_by_year = {}
    tmpdir = tempfile.mkdtemp()

    for year in args.years:
        print(f"\n── {year} ──────────────────────────────")

        # Download PDF
        pdf_path = download_pdf(year, tmpdir)
        if not pdf_path:
            print(f"  skipping {year} (no PDF)")
            continue

        time.sleep(1)   # polite delay

        # Extract IC segment via Claude
        result = extract_ic_from_pdf(pdf_path, year)
        if not result:
            print(f"  skipping {year} (extraction failed)")
            continue

        ic_rev_yuan = result["ic_revenue"]           # 元
        ic_gm_pct   = result["ic_gross_margin_pct"]  # e.g. 0.0564

        # Convert revenue Yuan → M RMB
        ic_rev_mrm = ic_rev_yuan / 1e6

        # Total revenue and NI from existing data (M RMB)
        total_rev_mrm = sanan["revenue"].get(str(year))
        total_ni_mrm  = sanan["net_income"].get(str(year))

        if total_rev_mrm and total_ni_mrm and total_rev_mrm > 0:
            # Proportional NI allocation
            ic_ni_mrm = total_ni_mrm * (ic_rev_mrm / total_rev_mrm)
        else:
            # Fallback: use gross profit as proxy for NI
            ic_cost_yuan = result.get("ic_cost", ic_rev_yuan * (1 - ic_gm_pct))
            ic_ni_mrm    = (ic_rev_yuan - ic_cost_yuan) / 1e6

        ic_margin = ic_ni_mrm / ic_rev_mrm if ic_rev_mrm else 0

        print(f"  IC Rev: {ic_rev_mrm:.1f} M RMB  |  IC NI: {ic_ni_mrm:.1f} M RMB  |  IC Margin: {ic_margin*100:.2f}%")
        ic_by_year[year] = {
            "ic_revenue_mrm": ic_rev_mrm,
            "ic_ni_mrm":      ic_ni_mrm,
            "ic_margin":      ic_margin,
        }

    if ic_by_year:
        update_data_json(args.dry_run, ic_by_year)
    else:
        print("\nNo data extracted — data.json unchanged.")


if __name__ == "__main__":
    main()
