#!/usr/bin/env python3
"""
fetch_edgar_to_json.py — EDGAR Quarterly Data → data.json (safe merge)
=======================================================================
Fetches quarterly revenue + NI for MPWR and NVTS from SEC EDGAR XBRL,
then merges only the quarterly period keys (e.g. "2025Q1") into the
existing data.json. All other data is preserved unchanged.

Usage:
    python fetch_edgar_to_json.py               # update MPWR + NVTS
    python fetch_edgar_to_json.py --tickers MPWR
"""

import json
import argparse
import logging
import math
from pathlib import Path

import pandas as pd

from fetch_semi_data import fetch_edgar_quarterly

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fetch_edgar_json")

_HERE      = Path(__file__).parent
_DATA_JSON = _HERE / "data.json"

US_COMPANIES = [
    ("MPWR", "MPWR", "0001280452"),
    ("NVTS", "NVTS", "0001831868"),
]


def _clean(v) -> float | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(float(v), 4)


def main():
    parser = argparse.ArgumentParser(description="Merge EDGAR quarterly data into data.json")
    parser.add_argument("--tickers", nargs="+", default=None,
                        help="Only update these tickers, e.g. --tickers MPWR")
    args = parser.parse_args()
    target = set(args.tickers) if args.tickers else None

    with open(_DATA_JSON, encoding="utf-8") as f:
        data = json.load(f)

    changed = False

    for name, ticker, cik in US_COMPANIES:
        if target and ticker not in target:
            continue

        log.info("Fetching %s (%s) from SEC EDGAR …", name, ticker)
        df = fetch_edgar_quarterly(name, ticker, cik)
        if df.empty:
            log.warning("  No data returned for %s", name)
            continue

        # Locate company entry in data.json by name or code
        entry = data.get(name) or next(
            (v for v in data.values() if isinstance(v, dict) and v.get("code") == ticker),
            None,
        )
        if entry is None:
            log.warning("  %s not found in data.json — skipping", name)
            continue

        rev_dict = entry.setdefault("revenue", {})
        ni_dict  = entry.setdefault("net_income", {})

        updated_periods = []
        for _, row in df.iterrows():
            period = row["period"]
            rl = _clean(row.get("revenue_local"))
            ni = _clean(row.get("net_income"))

            if rl is not None:
                rev_dict[period] = rl
                changed = True
            if ni is not None:
                ni_dict[period] = ni
                changed = True
            if rl is not None or ni is not None:
                updated_periods.append(period)

        log.info("  %s: merged %d periods (%s)",
                 name, len(updated_periods),
                 ", ".join(sorted(updated_periods)[-6:]) + ("…" if len(updated_periods) > 6 else ""))

    if changed:
        with open(_DATA_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log.info("data.json updated.")
    else:
        log.info("No changes — data.json unchanged.")


if __name__ == "__main__":
    main()
