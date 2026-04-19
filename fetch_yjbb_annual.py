#!/usr/bin/env python3
"""
fetch_yjbb_annual.py — AkShare yjbb Annual Earnings Fetcher
=============================================================
Queries ak.stock_yjbb_em for each fiscal year-end (2019-2025) for the
19 A-share companies in the tracker universe.

Output: yjbb_annual.json — loaded by dashboard.html for trend charts.
        Revenue and NI stored in M RMB. Dashboard divides by 7.2 for M USD.

Usage:
    python fetch_yjbb_annual.py                  # fetch all years
    python fetch_yjbb_annual.py --years 2025     # refresh only 2025
    python fetch_yjbb_annual.py --years 2024 2025
"""

import io, sys, json, time, argparse, logging
from pathlib import Path
from datetime import datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fetch_yjbb")

_HERE = Path(__file__).parent
_OUT  = _HERE / "yjbb_annual.json"

# ── Company universe (A-share only) ───────────────────────────────────────────
ASHARE_COMPANIES = [
    ("SG micro",     "300661", "Analog"),
    ("3-Peak",       "688536", "Analog"),
    ("Chipown",      "688508", "Analog"),
    ("Fortior",      "688279", "Analog"),
    ("Southchip",    "688484", "Analog"),
    ("Joulwatt",     "688141", "Analog"),
    ("Injoinic",     "688209", "Analog"),
    ("Novosense",    "688052", "Analog"),
    ("Silan",        "600460", "P&D"),
    ("CR Micro",     "688396", "P&D"),
    ("Yangjie",      "300373", "P&D"),
    ("Sino-Micro",   "600360", "P&D"),
    ("star power",   "603290", "P&D"),
    ("NCE",          "605111", "P&D"),
    ("JieJie Micro", "300623", "P&D"),
    ("Oriental",     "688261", "P&D"),
    ("Macmicst",     "688711", "P&D"),
    ("Sanan",        "600703", "P&D"),
    ("UNT",          "688469", "P&D"),
]

CN_NAMES = {
    "300661": "圣邦股份", "688536": "思瑞浦", "688508": "芯朋微",
    "688279": "峰岹科技", "688484": "南芯科技", "688141": "杰华特",
    "688209": "英集芯",   "688052": "纳芯微",  "600460": "士兰微",
    "688396": "华润微",   "300373": "扬杰科技", "600360": "华微电子",
    "603290": "斯达半导", "605111": "新洁能",  "300623": "捷捷微电",
    "688261": "东微半导", "688711": "宏微科技", "600703": "三安光电",
    "688469": "芯联集成",
}

ANALYSIS_YEARS = [2019, 2020, 2021, 2022, 2023, 2024, 2025]
TARGET_CODES   = {code for _, code, _ in ASHARE_COMPANIES}

REV_COL = "营业总收入-营业总收入"
NI_COL  = "净利润-净利润"
DATE_COL = "最新公告日期"


# ── Fetch one fiscal year ──────────────────────────────────────────────────────

def fetch_year(year: int) -> dict:
    """
    Fetch yjbb for fiscal year `year` (date = YYYY1231).
    Returns {code: {rev_mrm, ni_mrm, margin, announced}} for matching companies.
    Values in M RMB (Yuan ÷ 1,000,000). Missing 2025 disclosures return no entry.
    """
    import akshare as ak
    import pandas as pd

    date_str = f"{year}1231"
    log.info("Fetching FY%d  (yjbb date=%s) ...", year, date_str)

    df = pd.DataFrame()
    for attempt in range(3):
        try:
            df = ak.stock_yjbb_em(date=date_str)
            break
        except Exception as e:
            log.warning("  Attempt %d failed: %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(3)
            else:
                log.error("  FY%d: all retries failed", year)
                return {}

    hit = df[df["股票代码"].isin(TARGET_CODES)].copy()
    log.info("  -> %d / %d companies found", len(hit), len(TARGET_CODES))

    result = {}
    for _, row in hit.iterrows():
        code     = row["股票代码"]
        rev_yuan = row.get(REV_COL)
        ni_yuan  = row.get(NI_COL)
        announced = str(row.get(DATE_COL, ""))

        rev_mrm = float(rev_yuan) / 1e6 if pd.notna(rev_yuan) and rev_yuan != 0 else None
        ni_mrm  = float(ni_yuan)  / 1e6 if pd.notna(ni_yuan)  else None
        margin  = round(ni_mrm / rev_mrm, 6) if (rev_mrm and ni_mrm is not None) else None

        ann_clean = announced[:10] if announced and announced not in ("NaT", "None", "nan") else None

        result[code] = {
            "rev_mrm":   round(rev_mrm, 4) if rev_mrm else None,
            "ni_mrm":    round(ni_mrm,  4) if ni_mrm is not None else None,
            "margin":    margin,
            "announced": ann_clean,
        }

    return result


# ── Assemble output ────────────────────────────────────────────────────────────

def build_output(year_data: dict) -> dict:
    out = {
        "meta": {
            "source":     "AkShare stock_yjbb_em",
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "years":      ANALYSIS_YEARS,
            "unit":       "M RMB",
            "fx_note":    "Divide rev_mrm / ni_mrm by 7.2 to get M USD",
        },
        "companies": {},
    }

    for eng_name, code, category in ASHARE_COMPANIES:
        comp_years = {}
        for year in ANALYSIS_YEARS:
            d = year_data.get(year, {}).get(code)
            if d:
                comp_years[str(year)] = {
                    "rev_mrm":   d["rev_mrm"],
                    "ni_mrm":    d["ni_mrm"],
                    "margin":    d["margin"],
                    "announced": d["announced"],
                    "disclosed": True,
                }
            else:
                comp_years[str(year)] = {
                    "rev_mrm":   None,
                    "ni_mrm":    None,
                    "margin":    None,
                    "announced": None,
                    "disclosed": False,
                }

        out["companies"][code] = {
            "name":     eng_name,
            "cn_name":  CN_NAMES.get(code, ""),
            "code":     code,
            "category": category,
            "currency": "M RMB",
            "years":    comp_years,
        }

    return out


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch AkShare yjbb annual earnings")
    parser.add_argument("--years", nargs="+", type=int, default=ANALYSIS_YEARS,
                        help="Fiscal years to fetch (default: 2019-2025)")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="Seconds between API calls (default: 2.0)")
    args = parser.parse_args()

    # Load existing cache for years we're NOT refreshing
    existing = {}
    if _OUT.exists():
        try:
            cached = json.loads(_OUT.read_text(encoding="utf-8"))
            skip_years = [y for y in ANALYSIS_YEARS if y not in args.years]
            for year in skip_years:
                existing[year] = {}
                for code, comp in cached.get("companies", {}).items():
                    yd = comp["years"].get(str(year), {})
                    if yd.get("disclosed"):
                        existing[year][code] = {
                            "rev_mrm":   yd["rev_mrm"],
                            "ni_mrm":    yd["ni_mrm"],
                            "margin":    yd["margin"],
                            "announced": yd["announced"],
                        }
            if skip_years:
                log.info("Reusing cached data for: %s", skip_years)
        except Exception as e:
            log.warning("Could not load cache: %s", e)

    # Fetch requested years
    year_data = dict(existing)
    for i, year in enumerate(sorted(args.years)):
        year_data[year] = fetch_year(year)
        if i < len(args.years) - 1:
            time.sleep(args.delay)

    output = build_output(year_data)
    _OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    # Summary table
    print(f"\n{'='*56}")
    print("  yjbb_annual.json — Fetch Summary")
    print(f"  Updated: {output['meta']['fetched_at']}")
    print(f"{'='*56}")
    for year in ANALYSIS_YEARS:
        n     = len(year_data.get(year, {}))
        tag   = " (refreshed)" if year in args.years else " (cached)"
        miss  = sorted(TARGET_CODES - set(year_data.get(year, {}).keys()))
        print(f"  FY{year}: {n:2d}/19{tag}"
              + (f"  pending={miss}" if miss else ""))
    print(f"{'='*56}\n")


if __name__ == "__main__":
    main()
