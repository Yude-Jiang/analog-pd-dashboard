#!/usr/bin/env python3
"""
fetch_yjbb_quarterly.py — AkShare yjbb Quarterly Earnings Fetcher
==================================================================
Fetches Q1 / H1 / 9M / FY cumulative reports for each requested year,
then derives individual Q1/Q2/Q3/Q4 values by subtraction:

    Q1 = Q1 cumulative
    Q2 = H1  − Q1
    Q3 = 9M  − H1
    Q4 = FY  − 9M

Output: yjbb_quarterly.json  (loaded by dashboard.html quarterly charts)
        Revenue / NI stored in M RMB.  Dashboard divides by 7.2 for M USD.

Usage:
    python fetch_yjbb_quarterly.py               # fetch 2024 + 2025
    python fetch_yjbb_quarterly.py --years 2025  # refresh only 2025
    python fetch_yjbb_quarterly.py --years 2024 2025 2026
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
log = logging.getLogger("fetch_qtr")

_HERE = Path(__file__).parent
_OUT  = _HERE / "yjbb_quarterly.json"

# ── Company universe (A-share only, same as fetch_yjbb_annual.py) ─────────────
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

TARGET_CODES = {code for _, code, _ in ASHARE_COMPANIES}

# Period suffix → Q-label mapping for AkShare date parameter
PERIODS = [
    ("Q1", "0331"),   # 一季报 — 3-month cumulative
    ("H1", "0630"),   # 中报   — 6-month cumulative
    ("9M", "0930"),   # 三季报 — 9-month cumulative
    ("FY", "1231"),   # 年报   — 12-month cumulative
]

REV_COL  = "营业总收入-营业总收入"
NI_COL   = "净利润-净利润"
DATE_COL = "最新公告日期"

DEFAULT_YEARS = [2024, 2025]


# ── Fetch one period ───────────────────────────────────────────────────────────

def fetch_period(year: int, period_label: str, suffix: str) -> dict:
    """
    Call ak.stock_yjbb_em(date=YYYYsuffix).
    Returns {code: {rev_cumul_mrm, ni_cumul_mrm, announced}} in M RMB.
    """
    import akshare as ak
    import pandas as pd

    date_str = f"{year}{suffix}"
    log.info("Fetching %d-%s (date=%s) ...", year, period_label, date_str)

    df = pd.DataFrame()
    for attempt in range(3):
        try:
            df = ak.stock_yjbb_em(date=date_str)
            break
        except Exception as exc:
            log.warning("  Attempt %d failed: %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(3)
            else:
                log.error("  %d-%s: all retries exhausted", year, period_label)
                return {}

    hit = df[df["股票代码"].isin(TARGET_CODES)].copy()
    log.info("  -> %d / %d companies matched", len(hit), len(TARGET_CODES))

    result = {}
    for _, row in hit.iterrows():
        code      = row["股票代码"]
        rev_yuan  = row.get(REV_COL)
        ni_yuan   = row.get(NI_COL)
        announced = str(row.get(DATE_COL, ""))

        rev_mrm = float(rev_yuan) / 1e6 if pd.notna(rev_yuan) and rev_yuan != 0 else None
        ni_mrm  = float(ni_yuan)  / 1e6 if pd.notna(ni_yuan)                     else None
        ann     = announced[:10] if announced not in ("NaT", "None", "nan", "") else None

        result[code] = {
            "rev": round(rev_mrm, 4) if rev_mrm is not None else None,
            "ni":  round(ni_mrm,  4) if ni_mrm  is not None else None,
            "ann": ann,
        }
    return result


# ── Derive individual quarters ─────────────────────────────────────────────────

def _sub(a, b):
    """Subtract two nullable floats; return None if either is missing."""
    return round(a - b, 4) if (a is not None and b is not None) else None


def derive_quarters(cumul: dict) -> dict:
    """
    cumul = {"Q1": {rev, ni, ann}, "H1": {...}, "9M": {...}, "FY": {...}}
    Returns {"Q1": {...}, "Q2": {...}, "Q3": {...}, "Q4": {...}} with M RMB values.
    Individual quarters are derived by subtraction of cumulative figures.
    """
    q1 = cumul.get("Q1", {})
    h1 = cumul.get("H1", {})
    m9 = cumul.get("9M", {})
    fy = cumul.get("FY", {})

    def mkq(rev, ni, ann):
        if rev is None:
            return None
        margin = round(ni / rev, 6) if (rev and ni is not None) else None
        return {"rev_mrm": rev, "ni_mrm": ni, "margin": margin, "announced": ann}

    quarters = {}

    # Q1 = Q1 cumulative (standalone)
    if q1.get("rev") is not None:
        quarters["Q1"] = mkq(q1["rev"], q1["ni"], q1.get("ann"))

    # Q2 = H1 − Q1
    q2_rev = _sub(h1.get("rev"), q1.get("rev"))
    q2_ni  = _sub(h1.get("ni"),  q1.get("ni"))
    if q2_rev is not None:
        quarters["Q2"] = mkq(q2_rev, q2_ni, h1.get("ann"))

    # Q3 = 9M − H1
    q3_rev = _sub(m9.get("rev"), h1.get("rev"))
    q3_ni  = _sub(m9.get("ni"),  h1.get("ni"))
    if q3_rev is not None:
        quarters["Q3"] = mkq(q3_rev, q3_ni, m9.get("ann"))

    # Q4 = FY − 9M
    q4_rev = _sub(fy.get("rev"), m9.get("rev"))
    q4_ni  = _sub(fy.get("ni"),  m9.get("ni"))
    if q4_rev is not None:
        quarters["Q4"] = mkq(q4_rev, q4_ni, fy.get("ann"))

    return quarters


# ── Build output JSON ──────────────────────────────────────────────────────────

def build_output(raw: dict, years: list) -> dict:
    """
    raw = {year: {period_label: {code: {rev, ni, ann}}}}
    """
    out = {
        "meta": {
            "source":     "AkShare stock_yjbb_em (Q1/H1/9M/FY cumulative → individual quarters)",
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "years":      years,
            "unit":       "M RMB",
            "fx_note":    "Divide rev_mrm / ni_mrm by 7.2 to get M USD",
            "derivation": "Q1=Q1 | Q2=H1-Q1 | Q3=9M-H1 | Q4=FY-9M",
        },
        "companies": {},
    }

    for eng_name, code, category in ASHARE_COMPANIES:
        all_quarters = {}
        for year in years:
            cumul = {}
            for label, _ in PERIODS:
                d = raw.get(year, {}).get(label, {}).get(code)
                if d:
                    cumul[label] = d

            derived = derive_quarters(cumul)
            for q_label, q_data in derived.items():
                if q_data:
                    all_quarters[f"{year}Q{q_label[1]}"] = q_data   # e.g. "2025Q1"

        out["companies"][code] = {
            "name":     eng_name,
            "cn_name":  CN_NAMES.get(code, ""),
            "code":     code,
            "category": category,
            "quarters": all_quarters,
        }

    return out


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch AkShare yjbb quarterly earnings")
    parser.add_argument("--years", nargs="+", type=int, default=DEFAULT_YEARS,
                        help=f"Fiscal years to fetch (default: {DEFAULT_YEARS})")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="Seconds between API calls (default: 2.0)")
    args = parser.parse_args()

    # Load cached data for years we are NOT refreshing
    raw: dict = {}
    if _OUT.exists():
        try:
            cached = json.loads(_OUT.read_text(encoding="utf-8"))
            cached_years = cached.get("meta", {}).get("years", [])
            for year in cached_years:
                if year not in args.years:
                    raw.setdefault(year, {})
                    for code, comp in cached.get("companies", {}).items():
                        for qkey, qdata in comp.get("quarters", {}).items():
                            if qkey.startswith(str(year)):
                                raw[year].setdefault("__cached__", {})[code] = qdata
            if cached_years:
                log.info("Reusing cache for years: %s", [y for y in cached_years if y not in args.years])
        except Exception as exc:
            log.warning("Could not read cache: %s", exc)

    # Fetch all periods for requested years
    for year in sorted(args.years):
        raw[year] = {}
        for i, (label, suffix) in enumerate(PERIODS):
            raw[year][label] = fetch_period(year, label, suffix)
            if i < len(PERIODS) - 1:
                time.sleep(args.delay)
        if year != sorted(args.years)[-1]:
            time.sleep(args.delay)

    output = build_output(raw, sorted(set(list(raw.keys()))))
    _OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    # Summary
    print(f"\n{'='*64}")
    print("  yjbb_quarterly.json — Fetch Summary")
    print(f"  Updated : {output['meta']['fetched_at']}")
    print(f"  Years   : {output['meta']['years']}")
    print(f"{'='*64}")
    for year in args.years:
        print(f"\n  ── {year} ──")
        for code, comp in output["companies"].items():
            qs = sorted(k for k in comp["quarters"] if k.startswith(str(year)))
            status = ", ".join(qs) if qs else "no data"
            print(f"  {comp['name']:15s} ({code}): {status}")
    print(f"\n{'='*64}\n")


if __name__ == "__main__":
    main()
