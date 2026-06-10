"""
fetch_segment_rev_pdf.py — Extract segment revenue from annual report PDFs.

Companies & segments (revenue ONLY; net_income/margin stay company-wide yjbb):
  - Sanan    600703  集成电路产品   (orgId gssh0600703)
  - Silan    600460  分立器件产品   (orgId gssh0600460)
  - CR Micro 688396  产品与方案     (orgId gshk0000597)

Pipeline:
  1. Query CNINFO for the 年报 PDF (cookie auth)
  2. Download PDF — skip if already cached in GCS at <gcs_dir>/<code>_YEAR.pdf
  3. Upload PDF to GCS for reuse
  4. Send to Gemini API → extract segment revenue from 主营业务分产品/分行业 table
  5. Overwrite ONLY `revenue` in data.json + set `segment_note` on the company

Usage:
    export CNINFO_COOKIE="your_cookie_here"
    export GEMINI_API_KEY=$(gcloud secrets versions access latest \
        --secret=VITE_GEMINI_API_KEY --project=st-china-ai-force)
    python fetch_segment_rev_pdf.py [--dry-run] [--companies Sanan Silan "CR Micro"]
                                    [--years 2021 2022 ...] [--redownload]

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
COMPANIES = {
    "Sanan": {
        "code": "600703", "org_id": "gssh0600703", "cn_name": "三安光电",
        "segment": "集成电路产品", "segment_alt": ["集成电路芯片"],
        "gcs_dir": "sanan_pdfs",   # keep existing cache location
    },
    "Silan": {
        "code": "600460", "org_id": "gssh0600460", "cn_name": "士兰微",
        "segment": "分立器件产品", "segment_alt": ["分立器件"],
        "gcs_dir": "silan_pdfs",
    },
    "CR Micro": {
        "code": "688396", "org_id": "gshk0000597", "cn_name": "华润微",
        "segment": "产品与方案", "segment_alt": ["产品与方案业务"],
        "gcs_dir": "crmicro_pdfs",
    },
}

TARGET_YEARS = [2019, 2020, 2021, 2022, 2023, 2024, 2025]
GCS_BUCKET   = os.environ.get("GCS_BUCKET", "st-china-ai-force-dashboard")

CNINFO_QUERY    = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_BASE     = "https://static.cninfo.com.cn/"
CATEGORY_ANNUAL = "category_ndbg_szsh"

EXCLUDE_KEYWORDS = ["摘要", "提示性", "英文版", "English", "补充", "更正", "取消", "撤销"]

_HERE = Path(__file__).parent

# ---------------------------------------------------------------------------
# CNINFO session
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

# ---------------------------------------------------------------------------
# Announcement query + PDF selection (ported from MCU download_reports.py)
# ---------------------------------------------------------------------------

def _query_announcements(session: requests.Session, code: str, org_id: str,
                          page: int = 1) -> dict:
    payload = {
        "stock":     f"{code},{org_id}",
        "tabName":   "fulltext",
        "pageSize":  30,
        "pageNum":   page,
        "column":    "sse",          # all three are Shanghai-listed (600xxx / 688xxx)
        "category":  CATEGORY_ANNUAL,
        "plate":     "",
        "seDate":    "",
        "isHLtitle": True,
    }
    resp = session.post(CNINFO_QUERY, data=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _detect_year(title: str) -> int | None:
    # Title only — the adjunctUrl contains the filing date (year+1), not the report year
    m = re.search(r"(20\d{2})", title)
    return int(m.group(1)) if m else None


def _select_best_pdf(announcements: list, year: int) -> dict | None:
    """Pick the main annual report: correct year, not a summary.
    adjunctSize is unreliable on CNINFO; prefer the shortest matching title
    (the bare 年度报告 over 全文/修订版 variants come out equivalent)."""
    candidates = []
    for ann in announcements:
        title = ann.get("announcementTitle", "")
        if any(kw in title for kw in EXCLUDE_KEYWORDS):
            continue
        if _detect_year(title) != year:
            continue
        if ann.get("adjunctType") != "PDF":
            continue
        candidates.append((len(title), ann))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _fetch_all_announcements(session: requests.Session, code: str, org_id: str) -> list:
    all_items: list = []
    page = 1
    while True:
        data = _query_announcements(session, code, org_id, page)
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

def _gcs_blob_name(co: dict, year: int) -> str:
    # Sanan keeps legacy naming sanan_YEAR.pdf; others use <code>_YEAR.pdf
    stem = "sanan" if co["code"] == "600703" else co["code"]
    return f"{co['gcs_dir']}/{stem}_{year}.pdf"


def _gcs_exists(co: dict, year: int) -> bool:
    try:
        from google.cloud import storage
        return storage.Client().bucket(GCS_BUCKET).blob(_gcs_blob_name(co, year)).exists()
    except Exception:
        return False


def _upload_to_gcs(local_path: str, co: dict, year: int):
    try:
        from google.cloud import storage
        storage.Client().bucket(GCS_BUCKET).blob(_gcs_blob_name(co, year))\
            .upload_from_filename(local_path, content_type="application/pdf")
        print(f"  [GCS] uploaded → gs://{GCS_BUCKET}/{_gcs_blob_name(co, year)}")
    except Exception as e:
        print(f"  [GCS] upload failed: {e}")


def _download_from_gcs(co: dict, year: int, dest_dir: str) -> str | None:
    try:
        from google.cloud import storage
        dest = os.path.join(dest_dir, f"{co['code']}_{year}.pdf")
        storage.Client().bucket(GCS_BUCKET).blob(_gcs_blob_name(co, year))\
            .download_to_filename(dest)
        print(f"  [GCS] ✓ {Path(dest).name} ({os.path.getsize(dest)/1e6:.1f} MB)")
        return dest
    except Exception as e:
        print(f"  [GCS] download failed: {e}")
        return None

# ---------------------------------------------------------------------------
# get_pdf: GCS cache → CNINFO download
# ---------------------------------------------------------------------------

def get_pdf(co: dict, year: int, dest_dir: str, session: requests.Session,
            all_announcements: list, redownload: bool = False) -> str | None:
    if not redownload and _gcs_exists(co, year):
        print(f"  [GCS] cache hit for {year}")
        return _download_from_gcs(co, year, dest_dir)

    ann = _select_best_pdf(all_announcements, year)
    if not ann:
        print(f"  [CNINFO] no suitable PDF found for {year}")
        return None

    title   = ann.get("announcementTitle", "")
    pdf_url = CNINFO_BASE + ann.get("adjunctUrl", "").lstrip("/")
    dest    = os.path.join(dest_dir, f"{co['code']}_{year}.pdf")

    print(f"  [{year}] {title}")
    print(f"         {pdf_url}")
    try:
        r = session.get(pdf_url, stream=True, timeout=120)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        print(f"         → saved ({os.path.getsize(dest)/1e6:.1f} MB)")
        _upload_to_gcs(dest, co, year)
        return dest
    except Exception as e:
        print(f"  [CNINFO download] error: {e}")
        return None

# ---------------------------------------------------------------------------
# Gemini API — extract segment revenue from PDF
# ---------------------------------------------------------------------------

EXTRACT_PROMPT_TMPL = """
You are a financial data extraction assistant.

This is the {year} annual report PDF for {cn_name} (stock {code}).

Find the main business breakdown table — usually titled "主营业务分产品情况",
"主营业务分行业情况", or similar — in the section "主营业务分析" or
"经营情况讨论与分析".

IMPORTANT: Extract data for the CURRENT REPORTING YEAR ({year}), NOT the comparative prior year column.
The table usually shows two sets of columns — take the LEFT/FIRST set which is the current year {year}.

Extract ONLY the row for "{segment}"{alt_clause}.

Read each number digit by digit from the PDF — do NOT estimate or round. Copy the exact integer or decimal as printed.

Return a JSON object with these exact keys (values in 元/Yuan as reported):
{{
  "segment_revenue": <float>   // 营业收入 (主营收入) for {year} — exact figure
}}

If the table is not found or the segment row does not exist, return:
{{"not_found": true, "reason": "<brief explanation>"}}

Return only the JSON object, no other text.
"""


def _get_gemini_key() -> str:
    """Fetch Gemini API key once: Secret Manager → env var fallback."""
    try:
        from google.cloud import secretmanager
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "st-china-ai-force")
        client  = secretmanager.SecretManagerServiceClient()
        name    = f"projects/{project}/secrets/VITE_GEMINI_API_KEY/versions/latest"
        resp    = client.access_secret_version(request={"name": name}, timeout=20)
        key     = resp.payload.data.decode("UTF-8").strip()
        print("[Secret Manager] GEMINI_API_KEY loaded ✓")
        return key
    except Exception as e:
        print(f"[Secret Manager] {e} — falling back to env var")
    return os.environ.get("GEMINI_API_KEY", "")


def extract_segment_from_pdf(client, co: dict, pdf_path: str, year: int) -> dict | None:
    """Send PDF to Gemini API and extract segment revenue."""
    from google.genai import types

    model_id  = "gemini-2.5-flash"
    file_size = os.path.getsize(pdf_path)
    print(f"  [Gemini] uploading {Path(pdf_path).name} ({file_size/1e6:.1f} MB)…")

    alt_clause = ""
    if co["segment_alt"]:
        alts = " or ".join(f'"{a}"' for a in co["segment_alt"])
        alt_clause = f" (or {alts})"

    uploaded = None
    try:
        with open(pdf_path, "rb") as f:
            uploaded = client.files.upload(
                file=f,
                config=types.UploadFileConfig(mime_type="application/pdf"),
            )
        print("  [Gemini] file uploaded, extracting…")

        prompt = EXTRACT_PROMPT_TMPL.format(
            year=year, cn_name=co["cn_name"], code=co["code"],
            segment=co["segment"], alt_clause=alt_clause,
        )
        response = client.models.generate_content(
            model=model_id,
            contents=[uploaded, prompt],
        )
        raw = response.text.strip()
        print(f"  [Gemini] response: {raw}")

        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        result = json.loads(raw)
        if result.get("not_found"):
            print(f"  [Gemini] segment row not found: {result.get('reason', '')}")
            return None
        return result
    except Exception as e:
        print(f"  [Gemini] error: {e}")
        return None
    finally:
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass

# ---------------------------------------------------------------------------
# data.json update — revenue only
# ---------------------------------------------------------------------------

def update_data_json(dry_run: bool, results: dict):
    """
    results: {company_key: {year_int: rev_mrm_float}}
    Only the `revenue` field is overwritten; net_income/margin untouched.
    """
    data_path = _HERE / "data.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))

    for key, rev_by_year in results.items():
        co    = COMPANIES[key]
        entry = data[key]
        print(f"\n=== {key} ({co['code']}) — segment 「{co['segment']}」 ===")
        print(f"{'Year':<6} {'Old Rev':>10} {'New Rev':>10}")
        print("-" * 30)
        for yr, new_rev in sorted(rev_by_year.items()):
            y = str(yr)
            old_rev = entry["revenue"].get(y)
            new_rev = round(new_rev, 1)
            print(f"{y:<6} {old_rev!s:>10} {new_rev!s:>10}")
            if not dry_run:
                entry["revenue"][y] = new_rev
        if not dry_run:
            entry["segment_note"] = (
                f"revenue为「{co['segment']}」分部数据（年报PDF提取）; "
                f"净利润/净利润率为公司整体归母口径(yjbb)"
            )

    if not dry_run:
        data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n✅ data.json updated ({len(results)} companies)")
    else:
        print("\n(dry-run — data.json not modified)")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run",    action="store_true", help="Show proposed changes without writing")
    parser.add_argument("--redownload", action="store_true", help="Force re-fetch from CNINFO even if GCS has PDF")
    parser.add_argument("--companies",  nargs="+", default=list(COMPANIES.keys()),
                        choices=list(COMPANIES.keys()))
    parser.add_argument("--years",      nargs="+", type=int, default=TARGET_YEARS)
    args = parser.parse_args()

    api_key = _get_gemini_key()
    if not api_key:
        print("✗ no Gemini API key (Secret Manager + GEMINI_API_KEY env both unavailable)")
        return
    from google import genai
    client = genai.Client(api_key=api_key)

    session = _build_session()
    results: dict = {}
    tmpdir = tempfile.mkdtemp()

    for key in args.companies:
        co = COMPANIES[key]
        print(f"\n════ {key} ({co['cn_name']} {co['code']}) — 「{co['segment']}」 ════")

        print("Fetching announcement list from CNINFO…")
        announcements = _fetch_all_announcements(session, co["code"], co["org_id"])
        print(f"  {len(announcements)} announcement(s) found")

        rev_by_year: dict = {}
        for year in args.years:
            print(f"\n── {year} ──────────────────────────────")
            pdf_path = get_pdf(co, year, tmpdir, session, announcements,
                               redownload=args.redownload)
            if not pdf_path:
                print(f"  skipping {year} (no PDF)")
                continue

            time.sleep(1)   # polite delay

            result = extract_segment_from_pdf(client, co, pdf_path, year)
            if not result:
                print(f"  skipping {year} (extraction failed)")
                continue

            rev_mrm = result["segment_revenue"] / 1e6   # 元 → M RMB
            print(f"  Segment Rev: {rev_mrm:.1f} M RMB")
            rev_by_year[year] = rev_mrm

        if rev_by_year:
            results[key] = rev_by_year

    if results:
        update_data_json(args.dry_run, results)
    else:
        print("\nNo data extracted — data.json unchanged.")


if __name__ == "__main__":
    main()
