#!/usr/bin/env python3
"""
fetch_silergy_to_json.py — Silergy (6415) Quarterly Data → data.json
=====================================================================
Source: the TWSE / TPEx OpenAPI open-data platforms.

MOPS (mops.twse.com.tw) used to serve this, but it answers CI runners with
"FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED" — an IP-based block that
no change of endpoint, header or payload gets around. openapi.twse.com.tw
answers the same runner normally, so the data comes from there instead.

Two things are deliberately discovered at runtime rather than hardcoded:

  * Endpoints — resolved from each platform's swagger spec by matching the
    Chinese dataset titles, so a renamed path does not silently break us.
  * Field names — matched by keyword against the record's own keys, and every
    key is logged, so an unexpected schema shows up as a diagnosable warning
    rather than a KeyError or a silent zero.

The open-data tables are current-period snapshots: one call yields the latest
month of revenue and the latest quarter of income, not history. So each run
merges what it sees into data.json and quarters are recomputed from all the
monthly figures accumulated there. Periods predating the switch cannot be
back-filled from this source.

Usage:
    python fetch_silergy_to_json.py
    python fetch_silergy_to_json.py --debug
"""

import io
import csv
import json
import math
import logging
import argparse
import datetime
from pathlib import Path

import requests
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

CODE = "6415"
NAME = "Silergy"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# (label, swagger spec, api base). Silergy may be listed on either exchange;
# both are searched and whichever carries 6415 wins.
PLATFORMS = [
    ("TWSE", "https://openapi.twse.com.tw/v1/swagger.json", "https://openapi.twse.com.tw/v1"),
    ("TPEx", "https://www.tpex.org.tw/openapi/swagger.json", "https://www.tpex.org.tw/openapi/v1"),
]

# Dataset titles to look for in each spec.
REVENUE_TITLES = ("每月營業收入",)
INCOME_TITLES  = ("綜合損益表",)
# ...but skip the sector-specific income tables; Silergy is a general company.
INCOME_EXCLUDE = ("金融", "證券", "期貨", "金控", "保險", "異業")


def _clean(v) -> float | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(float(v), 4)


def _num(s) -> float | None:
    """Parse an open-data numeric cell: commas, parenthesised negatives, blanks."""
    if s is None:
        return None
    t = str(s).strip().replace(",", "")
    if not t or t in ("-", "--", "N/A"):
        return None
    if t.startswith("(") and t.endswith(")"):
        t = "-" + t[1:-1]
    try:
        return float(t)
    except ValueError:
        return None


# ── HTTP ───────────────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 40):
    return requests.get(url, headers=HEADERS, timeout=timeout, verify=False)


def _rows(url: str) -> list[dict]:
    """Fetch a dataset as a list of dicts, accepting either JSON or CSV.

    The platform negotiates on Accept and has served non-JSON before, so sniff
    the body rather than trusting the content type.
    """
    r = _get(url)
    log.debug("  GET %s -> HTTP %s %dB ct=%s",
              url, r.status_code, len(r.text), r.headers.get("Content-Type", "?"))
    r.raise_for_status()

    body = r.text.lstrip("﻿").strip()
    if body[:1] in ("[", "{"):
        data = json.loads(body)
        return data if isinstance(data, list) else [data]

    rows = list(csv.DictReader(io.StringIO(body)))
    if rows:
        log.debug("  parsed as CSV (%d rows)", len(rows))
        return rows

    log.warning("  %s: unrecognised body: %s", url, " ".join(body[:200].split()))
    return []


# ── Endpoint discovery ─────────────────────────────────────────────────────────

def _discover(spec_url: str, include: tuple, exclude: tuple = ()) -> list[tuple[str, str]]:
    """Return [(path, title)] for spec endpoints whose title matches."""
    try:
        spec = _get(spec_url).json()
    except Exception as e:
        log.warning("  spec %s failed: %s", spec_url, e)
        return []

    found = []
    for path, node in sorted((spec.get("paths") or {}).items()):
        title = ""
        for method in ("get", "post"):
            if method in node:
                title = node[method].get("summary") or node[method].get("description") or ""
                break
        if any(k in title for k in include) and not any(k in title for k in exclude):
            found.append((path, title))
    return found


def _find_company(rows: list[dict]) -> dict | None:
    """Locate Silergy's row by company code, whatever the code column is named."""
    for row in rows:
        for key, val in row.items():
            if "代號" in key or "代碼" in key.lower() or key.lower() in ("code", "companycode"):
                if str(val).strip() == CODE:
                    return row
    return None


def _field(row: dict, *keywords: str) -> tuple[str | None, float | None]:
    """First field whose name contains every keyword. Returns (key, value)."""
    for key, val in row.items():
        if all(k in key for k in keywords):
            return key, _num(val)
    return None, None


# ── Fetch: monthly revenue ─────────────────────────────────────────────────────

def fetch_monthly_revenue() -> dict:
    """Latest monthly-revenue snapshot → {'YYYYMmm': M_TWD}."""
    for platform, spec_url, base in PLATFORMS:
        for path, title in _discover(spec_url, REVENUE_TITLES):
            log.info("[%s] revenue dataset: %s (%s)", platform, path, title)
            try:
                rows = _rows(base + path)
            except Exception as e:
                log.warning("  fetch failed: %s", e)
                continue
            if not rows:
                continue
            log.debug("  %d rows, fields: %s", len(rows), list(rows[0].keys()))

            row = _find_company(rows)
            if row is None:
                log.info("  %s not in this dataset", CODE)
                continue

            log.info("  found %s: %s", CODE, json.dumps(row, ensure_ascii=False)[:300])
            ym_key, ym = _field(row, "資料年月")
            rev_key, rev = _field(row, "當月營收")
            if rev is None:
                rev_key, rev = _field(row, "營business收入")   # unlikely, keeps _field honest
            if ym is None or rev is None:
                log.warning("  could not locate 資料年月/當月營收 in keys: %s",
                            list(row.keys()))
                continue

            # 資料年月 is ROC-based YYYMM (e.g. 11509 = 2026-09).
            ym_i  = int(ym)
            month = ym_i % 100
            year  = ym_i // 100
            if year < 1911:            # ROC year
                year += 1911
            period = f"{year}M{month:02d}"
            # Open data reports revenue in NTD thousands.
            value = _clean(rev / 1000)
            log.info("  %s (%s=%s) -> %s = %.4f M TWD",
                     platform, ym_key, ym, period, value)
            return {period: value}

    log.warning("No monthly revenue found for %s on any platform", CODE)
    return {}


# ── Fetch: quarterly income ────────────────────────────────────────────────────

def fetch_cumulative_ni() -> dict:
    """Latest income-statement snapshot → {'YYYY': {'season': n, 'ni': M_TWD}}."""
    for platform, spec_url, base in PLATFORMS:
        for path, title in _discover(spec_url, INCOME_TITLES, INCOME_EXCLUDE):
            log.info("[%s] income dataset: %s (%s)", platform, path, title)
            try:
                rows = _rows(base + path)
            except Exception as e:
                log.warning("  fetch failed: %s", e)
                continue
            if not rows:
                continue
            log.debug("  %d rows, fields: %s", len(rows), list(rows[0].keys()))

            row = _find_company(rows)
            if row is None:
                log.info("  %s not in this dataset", CODE)
                continue

            log.info("  found %s: %s", CODE, json.dumps(row, ensure_ascii=False)[:400])
            _, year   = _field(row, "年度")
            _, season = _field(row, "季別")
            ni_key, ni = _field(row, "母公司業主")
            if ni is None:
                ni_key, ni = _field(row, "本期淨利")
            if None in (year, season) or ni is None:
                log.warning("  could not locate 年度/季別/淨利 in keys: %s", list(row.keys()))
                continue

            y = int(year) + 1911 if int(year) < 1911 else int(year)
            value = _clean(ni / 1000)     # NTD thousands → M TWD
            log.info("  %s Q%d cumulative NI (%s) = %.4f M TWD",
                     y, int(season), ni_key, value)
            return {str(y): {"season": int(season), "ni": value}}

    log.warning("No income statement found for %s on any platform", CODE)
    return {}


# ── Derivation ─────────────────────────────────────────────────────────────────

def quarters_from_months(rev_dict: dict) -> dict:
    """Recompute every complete quarter from the monthly figures on file.

    Derived from the accumulated months rather than this run's snapshot, and
    recomputed rather than filled in only when absent — a derived value left
    untouched goes stale the moment its inputs change.
    """
    months = {}
    for key, val in rev_dict.items():
        if "M" in key and val is not None:
            y, _, m = key.partition("M")
            if y.isdigit() and m.isdigit():
                months[(int(y), int(m))] = val

    out = {}
    for (year, _), _ in list(months.items()):
        for q, ms in ((1, (1, 2, 3)), (2, (4, 5, 6)), (3, (7, 8, 9)), (4, (10, 11, 12))):
            vals = [months.get((year, m)) for m in ms]
            if all(v is not None for v in vals):
                out[f"{year}Q{q}"] = _clean(sum(vals))
    return out


def quarters_from_cumulative_ni(latest: dict, ni_dict: dict) -> dict:
    """Single-quarter NI from a cumulative snapshot (Taiwan reports YTD).

    Season 1 is Q1 outright; later seasons need the preceding cumulative total,
    which is only available once earlier snapshots have been recorded.
    """
    out = {}
    for year, info in latest.items():
        season, cumul = info["season"], info["ni"]
        if cumul is None:
            continue
        if season == 1:
            out[f"{year}Q1"] = cumul
            continue
        prev = ni_dict.get(f"{year}C{season - 1}")     # stored cumulative
        if prev is None:
            log.info("  %s Q%d: no Q%d cumulative on file yet — single quarter "
                     "cannot be derived this run", year, season, season - 1)
            continue
        out[f"{year}Q{season}"] = _clean(cumul - prev)
    return out


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Fetch Silergy quarterly data from TWSE/TPEx OpenAPI")
    ap.add_argument("--debug", action="store_true", help="Verbose request and schema logging")
    args = ap.parse_args()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    with open(_DATA_JSON, encoding="utf-8") as f:
        data = json.load(f)

    entry = data.get(NAME)
    if entry is None:
        log.error("%s not found in data.json", NAME)
        return

    log.info("=== Silergy (%s) via TWSE/TPEx OpenAPI ===", CODE)
    rev_dict = entry.setdefault("revenue", {})
    ni_dict  = entry.setdefault("net_income", {})
    before   = json.dumps({"r": rev_dict, "n": ni_dict}, sort_keys=True, ensure_ascii=False)

    log.info("Step 1: monthly revenue snapshot")
    for period, val in fetch_monthly_revenue().items():
        rev_dict[period] = val

    log.info("Step 2: quarterly revenue from accumulated months")
    q_rev = quarters_from_months(rev_dict)
    for period, val in q_rev.items():
        rev_dict[period] = val
    log.info("  revenue quarters: %s", sorted(q_rev) or "none complete yet")

    log.info("Step 3: cumulative net income snapshot")
    latest_ni = fetch_cumulative_ni()
    for year, info in latest_ni.items():
        ni_dict[f"{year}C{info['season']}"] = info["ni"]     # keep the cumulative
    q_ni = quarters_from_cumulative_ni(latest_ni, ni_dict)
    for period, val in q_ni.items():
        ni_dict[period] = val
    log.info("  NI quarters: %s", sorted(q_ni) or "none derivable yet")

    after = json.dumps({"r": rev_dict, "n": ni_dict}, sort_keys=True, ensure_ascii=False)
    if after != before:
        with open(_DATA_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log.info("data.json updated.")
    else:
        log.info("No changes — data.json unchanged.")


if __name__ == "__main__":
    main()
