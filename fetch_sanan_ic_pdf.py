"""
fetch_sanan_ic_pdf.py — Extract Sanan IC segment revenue from annual report PDFs.

Pipeline:
  1. Query CNINFO for Sanan (600703) 年报 PDF (cookie auth, orgId lookup)
  2. Download PDF — skip if already cached in GCS at sanan_pdfs/sanan_YEAR.pdf
  3. Upload PDF to GCS for reuse
  4. Send to Gemini API → extract "集成电路产品" row from 主营业务分产品情况 table
  5. Compute IC NI via proportional allocation: IC_NI = Total_NI × (IC_Rev / Total_Rev)
  6. Overwrite Sanan revenue / net_income / margin in data.json

Usage:
    export CNINFO_COOKIE="your_cookie_here"
    export GEMINI_API_KEY="your_key_here"
    python fetch_sanan_ic_pdf.py [--dry-run] [--years 2022 2023 2024] [--redownload]

Cookie: visit https://www.cninfo.com.cn, open DevTools → Network,
        copy the Cookie header from any XHR request.
"""

import argparse
import json
import os
import re
import time
import tempfile
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
STOCK_CODE   = "600703"
STOCK_NAME   = "三安光电"
COMPANY_KEY  = "Sanan"
TARGET_YEARS = [2019, 2020, 2021, 2022, 2023, 2024]
GCS_BUCKET   = os.environ.get("GCS_BUCKET", "st-china-ai-force-dashboard")
GCS_PDF_DIR  = "sanan_pdfs"

CNINFO_QUERY  = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_SEARCH = "https://www.cninfo.com.cn/new/information/topSearch/query"
CNINFO_BASE   = "https://static.cninfo.com.cn/"
CATEGORY_ANNUAL = "category_ndbg_szsh"

EXCLUDE_KEYWORDS = ["摘要", "提示性", "英文版", "English", "补充", "更正", "取消", "撤销"]

_HERE = Path(__file__).parent

# ---------------------------------------------------------------------------
# CNINFO session + orgId
# ---------------------------------------------------------------------------

def _build_session() -> requests.Session:
    cookie = os.environ.get("CNINFO_COOKIE", "")
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.cninfo.com.cn/",
        "Origin":  "https://www.cninfo.com.cn",
    })
    if cookie:
        s.headers["Cookie"] = cookie
    else:
        print("⚠  CNINFO_COOKIE not set — downloads may fail for authenticated content.")
    return s


def _get_org_id(session: requests.Session) -> str:
    """Look up Sanan's CNINFO internal orgId dynamically."""
    try:
        r = session.get(CNINFO_SEARCH,
                        params={"keyWord": STOCK_CODE, "maxNum": 5}, timeout=10)
        r.raise_for_status()
        for item in r.json():
            if item.get("code") == STOCK_CODE:
                return item.get("orgId", "")
    except Exception as e:
        print(f"  [CNINFO] orgId lookup failed: {e}")
    return ""


# ---------------------------------------------------------------------------
# Announcement query + PDF selection (ported from MCU download_reports.py)
# ---------------------------------------------------------------------------

def _query_announcements(session: requests.Session, org_id: str,
                          category: str, page: int = 1) -> dict:
    payload = {
        "stock":     f"{STOCK_CODE},{org_id}",
        "tabName":   "fulltext",
        "pageSize":  30,
        "pageNum":   page,
        "column":    "sse",          # 600xxx = Shanghai
        "category":  category,
        "plate":     "",
        "seDate":    "",
        "isHLtitle": True,
    }
    resp = session.post(CNINFO_QUERY, data=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _detect_year(title: str, url: str) -> int | None:
    for text in (title, url):
        m = re.search(r"(20\d{2})", text)
        if m:
            return int(m.group(1))
    return None


def _select_best_pdf(announcements: list, year: int) -> dict | None:
    """Pick the main annual report: correct year, not a summary, largest file."""
    candidates = []
    for ann in announcements:
        title = ann.get("announcementTitle", "")
        if any(kw in title for kw in EXCLUDE_KEYWORDS):
            continue
        if _detect_year(title, ann.get("adjunctUrl", "")) != year:
            continue
        if ann.get("adjunctType") != "PDF":
            continue
        candidates.append((int(ann.get("adjunctSize", 0)), ann))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _fetch_all_announcements(session: requests.Session, org_id: str) -> list:
    all_items: list = []
    page = 1
    while True:
        data = _query_announcements(session, org_id, CATEGORY_ANNUAL, page)
        items = data.get("announcements") or []
        if not items:
            break
        all_items.extend(items)
        if page >= data.get("totalPages", 1):
            break
        page += 1
        time.sleep(0.4)
    return all_items

# ---------------------------------------------------------------------------
# GCS helpers
# ---------------------------------------------------------------------------

def _gcs_blob_name(year: int) -> str:
    return f"{GCS_PDF_DIR}/sanan_{year}.pdf"


def _gcs_exists(year: int) -> bool:
    try:
        from google.cloud import storage
        return storage.Client().bucket(GCS_BUCKET).blob(_gcs_blob_name(year)).exists()
    except Exception:
        return False


def _upload_to_gcs(local_path: str, year: int):
    try:
        from google.cloud import storage
        storage.Client().bucket(GCS_BUCKET).blob(_gcs_blob_name(year))\
            .upload_from_filename(local_path, content_type="application/pdf")
        print(f"  [GCS] uploaded → gs://{GCS_BUCKET}/{_gcs_blob_name(year)}")
    except Exception as e:
        print(f"  [GCS] upload failed: {e}")


def _download_from_gcs(year: int, dest_dir: str) -> str | None:
    try:
        from google.cloud import storage
        dest = os.path.join(dest_dir, f"sanan_{year}.pdf")
        storage.Client().bucket(GCS_BUCKET).blob(_gcs_blob_name(year))\
            .download_to_filename(dest)
        print(f"  [GCS] ✓ sanan_{year}.pdf ({os.path.getsize(dest)/1e6:.1f} MB)")
        return dest
    except Exception as e:
        print(f"  [GCS] download failed: {e}")
        return None

# ---------------------------------------------------------------------------
# get_pdf: GCS cache → CNINFO download
# ---------------------------------------------------------------------------

def get_pdf(year: int, dest_dir: str, session: requests.Session,
            all_announcements: list, redownload: bool = False) -> str | None:
    # 1. GCS cache
    if not redownload and _gcs_exists(year):
        print(f"  [GCS] cache hit for {year}")
        return _download_from_gcs(year, dest_dir)

    # 2. Select best PDF from pre-fetched announcement list
    ann = _select_best_pdf(all_announcements, year)
    if not ann:
        print(f"  [CNINFO] no suitable PDF found for {year}")
        return None

    title    = ann.get("announcementTitle", "")
    pdf_url  = CNINFO_BASE + ann.get("adjunctUrl", "").lstrip("/")
    dest     = os.path.join(dest_dir, f"sanan_{year}.pdf")
    size_kb  = int(ann.get("adjunctSize", 0)) // 1024

    print(f"  [{year}] {title}  ({size_kb:,} KB)")
    print(f"         {pdf_url}")
    try:
        r = session.get(pdf_url, stream=True, timeout=120)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        print(f"         → saved ({os.path.getsize(dest)/1e6:.1f} MB)")
        _upload_to_gcs(dest, year)
        return dest
    except Exception as e:
        print(f"  [CNINFO download] error: {e}")
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
    parser.add_argument("--dry-run",    action="store_true", help="Show proposed changes without writing")
    parser.add_argument("--redownload", action="store_true", help="Force re-fetch from CNINFO even if GCS has PDF")
    parser.add_argument("--years",      nargs="+", type=int, default=TARGET_YEARS)
    args = parser.parse_args()

    data_path = _HERE / "data.json"
    raw_data  = json.loads(data_path.read_text(encoding="utf-8"))
    sanan     = raw_data[COMPANY_KEY]

    # Build CNINFO session and pre-fetch all announcements once
    session = _build_session()
    print("Resolving Sanan orgId from CNINFO…")
    org_id = _get_org_id(session)
    print(f"  orgId = {org_id or '(not found — will use stock code only)'}")

    print("Fetching announcement list from CNINFO…")
    all_announcements = _fetch_all_announcements(session, org_id)
    print(f"  {len(all_announcements)} announcement(s) found")

    ic_by_year = {}
    tmpdir = tempfile.mkdtemp()

    for year in args.years:
        print(f"\n── {year} ──────────────────────────────")

        # Get PDF (GCS cache or CNINFO download)
        pdf_path = get_pdf(year, tmpdir, session, all_announcements,
                           redownload=args.redownload)
        if not pdf_path:
            print(f"  skipping {year} (no PDF)")
            continue

        time.sleep(1)   # polite delay

        # Extract IC segment via Gemini
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
