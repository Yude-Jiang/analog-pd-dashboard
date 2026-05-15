#!/usr/bin/env python3
"""
fetch_edgar_to_json.py — EDGAR Quarterly Data → data.json (safe merge)
=======================================================================
Fetches quarterly revenue + NI for MPWR and NVTS from SEC EDGAR XBRL,
then merges only the quarterly period keys (e.g. "2025Q1") into the
existing data.json. All other data is preserved unchanged.

Handles both single-quarter and YTD-cumulative XBRL reporting styles:
- Companies that file single-quarter entries (e.g. NVTS): used directly.
- Companies that file YTD cumulative entries (e.g. MPWR): converted to
  single-quarter via sequential subtraction within each fiscal year.

Usage:
    python fetch_edgar_to_json.py               # update MPWR + NVTS
    python fetch_edgar_to_json.py --tickers MPWR
"""

import json
import argparse
import logging
import math
from pathlib import Path

import requests
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fetch_edgar_json")

_HERE      = Path(__file__).parent
_DATA_JSON = _HERE / "data.json"

HEADERS_SEC = {
    "User-Agent": "AnalogPD research-bot/1.0 contact@example.com",
    "Accept":     "application/json",
}

REVENUE_TAGS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
]
# Fallback: if no direct revenue tag found, derive from GrossProfit + CostOfRevenue
REVENUE_DERIVED_TAGS = ["GrossProfit", "CostOfRevenue"]
NI_TAGS = ["NetIncomeLoss", "ProfitLoss"]

YEAR_START = 2023
YEAR_END   = 2026

US_COMPANIES = [
    ("MPWR", "MPWR", "0001280452"),
    ("NVTS", "NVTS", "0001831868"),
]


def _clean(v) -> float | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(float(v), 4)


def _period_label(dt) -> str:
    """Convert a quarter-end date to a period label like '2025Q1'."""
    m = dt.month
    q = (m - 1) // 3 + 1
    return f"{dt.year}Q{q}"


def _extract_facts(facts: dict, tags: list, unit: str = "USD") -> pd.DataFrame:
    """
    Extract all 10-Q and 10-K entries for the given XBRL tags.
    Merges across all matching tags so a company switching tags mid-history
    (e.g. Revenues → RevenueFromContractWithCustomer) loses no data.
    Deduplicates on (end, start) keeping latest filed revision.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    frames = []
    for tag in tags:
        node = us_gaap.get(tag, {}).get("units", {}).get(unit)
        if not node:
            continue
        df = pd.DataFrame(node)
        df = df[df["form"].isin(["10-Q", "10-K"])].copy()
        if not df.empty:
            log.debug("  tag=%s rows=%d end_range=[%s, %s]",
                      tag, len(df), df["end"].min(), df["end"].max())
            frames.append(df)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df["end"]   = pd.to_datetime(df["end"],   errors="coerce")
    df["start"] = pd.to_datetime(df["start"], errors="coerce") \
                  if "start" in df.columns else pd.NaT
    df = df.dropna(subset=["end"])
    # Keep latest revision per (form, end, start) — include "form" so that
    # 10-K re-filings of quarterly periods don't overwrite 10-Q entries.
    key = ["form", "end", "start"] if "start" in df.columns else ["form", "end"]
    df = df.sort_values("filed").drop_duplicates(key, keep="last")
    df["dur"] = (df["end"] - df["start"]).dt.days
    # When start is missing (instant facts), infer dur from EDGAR's fp field
    # so YTD-subtraction logic can still classify Q1/Q2/Q3/annual entries.
    if "fp" in df.columns:
        _FP_DUR = {"Q1": 91, "Q2": 182, "Q3": 273, "H1": 182, "9M": 273, "FY": 365}
        nan_mask = df["dur"].isna()
        df.loc[nan_mask, "dur"] = df.loc[nan_mask, "fp"].map(_FP_DUR)
    return df[["end", "start", "dur", "val", "form"]].reset_index(drop=True)


def _to_quarterly(df: pd.DataFrame, scale: float = 1e6) -> dict:
    """
    Convert a raw EDGAR facts DataFrame to single-quarter {period: value}.

    For each fiscal year:
    1. Prefer short-duration (≤95 day) single-quarter entries.
    2. If only YTD cumulative entries exist, derive single quarters by
       sequential subtraction of the running cumulative total.
    Annual entries (≥340 days) are skipped.
    """
    if df.empty:
        return {}

    result: dict = {}
    df_q = df[df["form"] == "10-Q"].copy()
    log.debug("  _to_quarterly: 10-Q end dates: %s",
              sorted(df_q["end"].dt.strftime("%Y-%m-%d").unique().tolist()))

    for yr in sorted(df_q["end"].dt.year.unique()):
        if yr not in range(YEAR_START, YEAR_END + 1):
            continue

        yr_rows = df_q[df_q["end"].dt.year == yr].copy()

        # For each quarter-end date, pick the best entry:
        #   - prefer single-quarter (dur ≤ 95) over YTD
        #   - among same-type entries, keep the one with the shortest duration
        def _pick_best(grp):
            short = grp[grp["dur"] <= 95]
            if not short.empty:
                return short.nsmallest(1, "dur")
            valid = grp[(grp["dur"] > 95) & (grp["dur"] < 340)]
            if not valid.empty:
                return valid.nsmallest(1, "dur")
            return pd.DataFrame()

        best_rows = []
        for _, grp in yr_rows.groupby("end"):
            picked = _pick_best(grp)
            if not picked.empty:
                best_rows.append(picked)
        if not best_rows:
            continue
        best = pd.concat(best_rows, ignore_index=True)
        best = best.sort_values("end").reset_index(drop=True)

        # Detect whether this year uses single-quarter or YTD reporting
        # A year is "YTD-style" when most Q entries have dur > 95
        ytd_count = (best["dur"] > 95).sum()
        sq_count  = (best["dur"] <= 95).sum()
        is_ytd    = ytd_count > sq_count

        cumul = 0.0
        for _, row in best.iterrows():
            dur = row["dur"]
            if pd.isna(dur) or dur >= 340:
                continue
            val_scaled = float(row["val"]) / scale

            if dur <= 95:
                # Single-quarter entry: use directly
                single_q = val_scaled
                cumul += single_q
            else:
                # YTD entry: subtract running cumulative
                single_q = val_scaled - cumul
                cumul = val_scaled

            period = _period_label(row["end"])
            if period not in result:
                result[period] = single_q

    return result


def fetch_edgar_quarterly_v2(name: str, cik: str) -> tuple[dict, dict]:
    """
    Fetch quarterly revenue and NI from SEC EDGAR XBRL.
    Returns (rev_periods, ni_periods) as {period_label: M_USD} dicts.
    """
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    try:
        resp = requests.get(url, headers=HEADERS_SEC, timeout=30)
        resp.raise_for_status()
        facts = resp.json()
    except Exception as e:
        log.warning("  [%s] EDGAR fetch failed: %s", name, e)
        return {}, {}

    rev_raw = _extract_facts(facts, REVENUE_TAGS)
    ni_raw  = _extract_facts(facts, NI_TAGS)

    if rev_raw.empty:
        # Fallback: derive Revenue = GrossProfit + CostOfRevenue
        log.info("  [%s] No direct revenue tag — deriving from GrossProfit + CostOfRevenue", name)
        gp_raw  = _extract_facts(facts, ["GrossProfit"])
        cor_raw = _extract_facts(facts, ["CostOfRevenue"])
        if not gp_raw.empty and not cor_raw.empty:
            gp_q  = _to_quarterly(gp_raw)
            cor_q = _to_quarterly(cor_raw)
            rev_q = {p: _clean(gp_q[p] + cor_q[p])
                     for p in gp_q if p in cor_q
                     and gp_q[p] is not None and cor_q[p] is not None}
        else:
            log.warning("  [%s] Cannot derive revenue — GrossProfit or CostOfRevenue missing", name)
            return {}, {}
    else:
        rev_q = _to_quarterly(rev_raw)

    ni_q  = _to_quarterly(ni_raw)

    log.info("  [%s] Revenue quarters: %s", name,
             ", ".join(sorted(rev_q)[-6:]) + ("…" if len(rev_q) > 6 else ""))
    log.info("  [%s] NI quarters:      %s", name,
             ", ".join(sorted(ni_q)[-6:])  + ("…" if len(ni_q)  > 6 else ""))
    return rev_q, ni_q


def main():
    parser = argparse.ArgumentParser(description="Merge EDGAR quarterly data into data.json")
    parser.add_argument("--tickers", nargs="+", default=None,
                        help="Only update these tickers, e.g. --tickers MPWR")
    parser.add_argument("--debug", action="store_true",
                        help="Enable DEBUG logging to diagnose missing data")
    args = parser.parse_args()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    target = set(args.tickers) if args.tickers else None

    with open(_DATA_JSON, encoding="utf-8") as f:
        data = json.load(f)

    changed = False

    for name, ticker, cik in US_COMPANIES:
        if target and ticker not in target:
            continue

        log.info("Fetching %s (%s) from SEC EDGAR …", name, ticker)
        rev_q, ni_q = fetch_edgar_quarterly_v2(name, cik)
        if not rev_q and not ni_q:
            log.warning("  No data returned for %s", name)
            continue

        entry = data.get(name) or next(
            (v for v in data.values() if isinstance(v, dict) and v.get("code") == ticker),
            None,
        )
        if entry is None:
            log.warning("  %s not found in data.json — skipping", name)
            continue

        rev_dict = entry.setdefault("revenue", {})
        ni_dict  = entry.setdefault("net_income", {})
        updated  = []

        for period, val in rev_q.items():
            v = _clean(val)
            if v is not None:
                rev_dict[period] = v
                changed = True
                if period not in updated:
                    updated.append(period)

        for period, val in ni_q.items():
            v = _clean(val)
            if v is not None:
                ni_dict[period] = v
                changed = True
                if period not in updated:
                    updated.append(period)

        log.info("  %s: merged periods: %s",
                 name, ", ".join(sorted(updated)[-8:]))

    if changed:
        with open(_DATA_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log.info("data.json updated.")
    else:
        log.info("No changes — data.json unchanged.")


if __name__ == "__main__":
    main()
