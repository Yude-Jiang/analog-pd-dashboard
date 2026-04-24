#!/usr/bin/env python3
"""
fetch_silergy_to_json.py — Silergy (6415) Quarterly Data → data.json
=====================================================================
Fetches quarterly revenue and net income for Silergy from Taiwan MOPS,
then merges period keys (e.g. "2025Q1") into the existing data.json.

Revenue:  MOPS monthly revenue API (ajax_t21sc03) → summed to quarters.
Net income: MOPS quarterly earnings API (ajax_t05st01) → cumulative-to-
            single-quarter via sequential subtraction (Taiwan reports YTD).

Usage:
    python fetch_silergy_to_json.py
"""

import json
import math
import logging
import time
from pathlib import Path

import requests
import pandas as pd
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fetch_silergy")

_HERE      = Path(__file__).parent
_DATA_JSON = _HERE / "data.json"

CODE  = "6415"
NAME  = "Silergy"
YEARS = [2024, 2025]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://mops.twse.com.tw/",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}


def _clean(v) -> float | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(float(v), 4)


# ── Monthly Revenue ────────────────────────────────────────────────────────────

def _fetch_monthly_revenue() -> dict:
    """MOPS monthly revenue → {'{year}M{mm}': M_TWD}"""
    url  = "https://mops.twse.com.tw/mops/web/ajax_t21sc03"
    result: dict = {}

    for year in YEARS:
        roc = year - 1911
        payload = {
            "encodeURIComponent": "1", "step": "1", "firstin": "1",
            "off": "1", "queryName": "co_id", "inpuType": "co_id",
            "TYPEK": "all", "isnew": "false",
            "co_id": CODE, "year": str(roc), "season": "00",
        }
        try:
            r = requests.post(url, data=payload, headers=HEADERS, timeout=20, verify=False)
            r.raise_for_status()
            tables = pd.read_html(r.text, encoding="utf-8")
        except Exception as e:
            log.warning("  MOPS monthly %d failed: %s", year, e)
            time.sleep(2)
            continue

        tbl = next((t for t in tables
                    if any("月份" in str(c) for c in t.columns)), None)
        if tbl is None:
            log.warning("  MOPS monthly %d: no table", year)
            continue

        tbl.columns = [str(c).strip() for c in tbl.columns]
        rev_col   = next((c for c in tbl.columns if "當月" in c), None)
        month_col = next((c for c in tbl.columns if "月份" in c or c == "月"), None)
        if not rev_col or not month_col:
            log.warning("  MOPS monthly %d: unexpected columns %s", year, tbl.columns.tolist())
            continue

        found = 0
        for _, row in tbl.iterrows():
            try:
                m   = int(str(row[month_col]).replace("月", "").strip())
                rev = float(str(row[rev_col]).replace(",", "").strip()) / 1e6
            except ValueError:
                continue
            if 1 <= m <= 12:
                result[f"{year}M{m:02d}"] = _clean(rev)
                found += 1

        log.info("  MOPS monthly %d: %d months", year, found)
        time.sleep(1)

    return result


def _monthly_to_quarterly(monthly: dict) -> dict:
    """Sum monthly → quarterly revenue. Only emits complete quarters."""
    result: dict = {}
    for year in YEARS:
        for q, months in [(1, [1,2,3]), (2, [4,5,6]), (3, [7,8,9]), (4, [10,11,12])]:
            vals = [monthly.get(f"{year}M{m:02d}") for m in months]
            if all(v is not None for v in vals):
                result[f"{year}Q{q}"] = _clean(sum(vals))
    return result


# ── Quarterly Net Income ───────────────────────────────────────────────────────

_NI_KEYWORDS = ["歸屬", "母公司", "淨利"]   # all three must appear in the row

def _parse_ni_from_tables(tables: list) -> float | None:
    """
    Search MOPS quarterly report HTML tables for parent-attributable net income.
    MOPS financials are reported in thousands NTD (千元) → divide by 1000 for M TWD.
    """
    for t in tables:
        for _, row in t.iterrows():
            cells = [str(v) for v in row.values]
            row_text = " ".join(cells)
            if all(kw in row_text for kw in _NI_KEYWORDS):
                # Found the right row; scan cells for a numeric value
                for cell in cells[1:]:   # skip the label column
                    clean = cell.replace(",", "").replace("(", "-").replace(")", "").strip()
                    try:
                        val = float(clean)
                        # Sanity: M TWD for Silergy NI ≈ 0–3000, in thousands NTD ≈ 0–3_000_000
                        if abs(val) < 1e7:
                            return val / 1000   # thousands NTD → M TWD
                    except ValueError:
                        continue
    return None


def _fetch_cumulative_ni() -> dict:
    """
    MOPS quarterly earnings (seasons 1/2/3) → cumulative NI {(year, season): M_TWD}.
    Season 1 = Q1 cumulative (= Q1 actual).
    Season 2 = H1 cumulative.
    Season 3 = 9M cumulative.
    """
    url    = "https://mops.twse.com.tw/mops/web/ajax_t05st01"
    result: dict = {}

    for year in YEARS:
        roc = year - 1911
        for season in [1, 2, 3]:
            payload = {
                "encodeURIComponent": "1", "step": "1", "firstin": "1",
                "off": "1", "queryName": "co_id", "inpuType": "co_id",
                "TYPEK": "all", "isnew": "false",
                "co_id": CODE, "year": str(roc), "season": str(season),
            }
            try:
                r = requests.post(url, data=payload, headers=HEADERS, timeout=25, verify=False)
                r.raise_for_status()
                tables = pd.read_html(r.text, encoding="utf-8")
            except Exception as e:
                log.warning("  MOPS Q%d %d NI failed: %s", season, year, e)
                time.sleep(2)
                continue

            ni = _parse_ni_from_tables(tables)
            if ni is not None:
                result[(year, season)] = _clean(ni)
                log.info("  MOPS Q%d %d NI cumulative: %.2f M TWD", season, year, ni)
            else:
                log.warning("  MOPS Q%d %d NI: not found", season, year)

            time.sleep(1)

    return result


def _cumulative_to_quarterly_ni(cumul: dict, annual_ni: dict) -> dict:
    """
    Convert cumulative NI (YTD) to single-quarter NI via subtraction.
    Q4 = Annual - 9M (if 9M and annual are both available).
    """
    result: dict = {}
    for year in YEARS:
        c1 = cumul.get((year, 1))   # Q1 (= single quarter)
        c2 = cumul.get((year, 2))   # H1 cumulative
        c3 = cumul.get((year, 3))   # 9M cumulative
        ann = annual_ni.get(str(year))

        if c1 is not None:
            result[f"{year}Q1"] = c1
        if c2 is not None and c1 is not None:
            result[f"{year}Q2"] = _clean(c2 - c1)
        if c3 is not None and c2 is not None:
            result[f"{year}Q3"] = _clean(c3 - c2)
        if ann is not None and c3 is not None:
            result[f"{year}Q4"] = _clean(ann - c3)

    return result


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    with open(_DATA_JSON, encoding="utf-8") as f:
        data = json.load(f)

    entry = data.get(NAME)
    if entry is None:
        log.error("%s not found in data.json", NAME)
        return

    log.info("=== Silergy (6415) quarterly data via MOPS ===")

    log.info("Step 1: monthly revenue → quarterly")
    monthly     = _fetch_monthly_revenue()
    q_rev       = _monthly_to_quarterly(monthly)
    log.info("  Revenue periods: %s", sorted(q_rev))

    log.info("Step 2: quarterly earnings → NI")
    cumul_ni    = _fetch_cumulative_ni()
    annual_ni   = entry.get("net_income", {})
    q_ni        = _cumulative_to_quarterly_ni(cumul_ni, annual_ni)
    log.info("  NI periods: %s", sorted(q_ni))

    rev_dict = entry.setdefault("revenue",    {})
    ni_dict  = entry.setdefault("net_income", {})
    changed  = False

    for period, val in q_rev.items():
        if val is not None:
            rev_dict[period] = val
            changed = True

    for period, val in q_ni.items():
        if val is not None:
            ni_dict[period] = val
            changed = True

    if changed:
        with open(_DATA_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log.info("data.json updated.")
    else:
        log.info("No changes — data.json unchanged.")


if __name__ == "__main__":
    main()
