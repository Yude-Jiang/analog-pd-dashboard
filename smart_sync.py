#!/usr/bin/env python3
"""
smart_sync.py — Disclosure-Gated Data Sync
===========================================
在运行任何抓取或同步之前，先查询交易所预约披露日历。
只有当目标公司有新报告披露时，才触发相应的数据更新流程。

运行方式:
    python smart_sync.py               # 检查今日披露，自动决定是否同步
    python smart_sync.py --force       # 跳过日历检查，强制执行全量同步
    python smart_sync.py --dry-run     # 只显示将触发的操作，不实际执行
    python smart_sync.py --window 3    # 检查今日前后 N 天的披露（默认 1）

退出码:
    0 = 无披露，跳过   |   1 = 已触发同步   |   2 = 同步出错
"""

import io
import sys
import argparse
import subprocess
import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

# ── UTF-8 console (Windows GBK fix) ───────────────────────────────────────────
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE = Path(__file__).parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("smart_sync")

# ── Import company universe from fetch_semi_data ───────────────────────────────
try:
    from fetch_semi_data import ASHARE_COMPANIES
except ImportError:
    log.error("Cannot import ASHARE_COMPANIES from fetch_semi_data.py")
    sys.exit(2)

# Map stock code → (display_name, category)
CODE_TO_NAME = {code: name for name, code, _ in ASHARE_COMPANIES}
TARGET_CODES  = set(CODE_TO_NAME.keys())


# ── Period helpers ─────────────────────────────────────────────────────────────

def _code_market(code: str) -> str:
    """Map A-share code prefix to exchange market string for AkShare."""
    if code.startswith("6"):
        return "沪市"                     # 600xxx, 603xxx, 605xxx, 688xxx
    if code.startswith(("0", "3")):
        return "深市"                     # 000xxx, 002xxx, 300xxx
    if code.startswith(("8", "4")):
        return "北交所"
    return "沪市"


def _periods_for_today(today: date) -> list[tuple[str, str]]:
    """
    Return [(period_label, report_type), ...] relevant for today's date.
    report_type ∈ {'annual', 'q1', 'q2', 'q3'}

    Disclosure calendar schedule (approximate):
      Jan-Apr  →  年报  (prior fiscal year)
      Apr-May  →  一季报  (current year Q1)
      Jul-Sep  →  半年报  (current year H1)
      Oct-Nov  →  三季报  (current year Q3)
    """
    m, y = today.month, today.year
    results = []

    if 1 <= m <= 4:
        results.append((f"{y - 1}年报", "annual"))
    if m in (4, 5):
        results.append((f"{y}一季报", "q1"))
    if 7 <= m <= 9:
        results.append((f"{y}半年报", "q2"))
    if 10 <= m <= 11:
        results.append((f"{y}三季报", "q3"))

    return results


# ── Core: Disclosure Calendar Check ───────────────────────────────────────────

def check_disclosures(window_days: int = 1) -> pd.DataFrame:
    """
    Query AkShare disclosure calendar for target companies.

    Returns a DataFrame with columns:
        股票代码, 股票简称, company_name, 首次预约, 实际披露,
        period_label, report_type, market
    Only rows where scheduled OR actual date falls within today ± window_days.
    """
    try:
        import akshare as ak
    except ImportError:
        log.error("akshare not installed. Run: pip install akshare")
        sys.exit(2)

    today = date.today()
    window_dates = {
        str(today + timedelta(days=d)) for d in range(-window_days, window_days + 1)
    }

    periods = _periods_for_today(today)
    if not periods:
        log.info("No standard report period active for today (%s)", today)
        return pd.DataFrame()

    log.info("Checking periods: %s", [p for p, _ in periods])
    log.info("Date window: %s", sorted(window_dates))

    frames = []

    for period_label, report_type in periods:
        # Determine which markets to query (avoid querying wrong exchange)
        markets_needed = set(_code_market(c) for c in TARGET_CODES)

        for market in markets_needed:
            try:
                df = ak.stock_report_disclosure(market=market, period=period_label)
            except Exception as e:
                log.debug("Skip %s/%s: %s", market, period_label, e)
                continue

            # Normalise date columns to string "YYYY-MM-DD"
            for col in ["首次预约", "实际披露"]:
                if col in df.columns:
                    df[col] = (
                        pd.to_datetime(df[col], errors="coerce")
                        .dt.date.astype(str)
                        .replace("NaT", "")
                    )

            # Filter: target company AND (scheduled OR actual) in window
            in_window = (
                df["股票代码"].isin(TARGET_CODES)
                & (df["首次预约"].isin(window_dates) | df["实际披露"].isin(window_dates))
            )
            hit = df[in_window].copy()
            if hit.empty:
                continue

            hit["period_label"] = period_label
            hit["report_type"]  = report_type
            hit["market"]       = market
            hit["company_name"] = hit["股票代码"].map(CODE_TO_NAME)
            frames.append(hit)

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    # Deduplicate if same company appears in multiple markets (shouldn't happen)
    result = result.drop_duplicates(subset=["股票代码", "period_label"])
    return result


# ── Action: Targeted AkShare Quarterly Fetch ──────────────────────────────────

def fetch_quarterly_for(companies: list[tuple[str, str]], report_type: str) -> bool:
    """
    Fetch updated quarterly data for specific (name, code) pairs via AkShare.
    Merges results into the QM_Data sheet in the Excel workbook so that
    sync_data.py picks up the new data on its next run.
    Returns True if any data was updated.
    """
    import time
    try:
        from fetch_semi_data import (
            fetch_ashare_quarterly,
            write_qm_sheet,
            OUTPUT_XLSX,
            _COL_ORDER,
            ASHARE_COMPANIES as _AC,
        )
    except ImportError as e:
        log.error("Cannot import from fetch_semi_data: %s", e)
        return False

    xlsx_path = _HERE / OUTPUT_XLSX
    if not xlsx_path.exists():
        log.error("Excel not found: %s", xlsx_path)
        return False

    # Read existing QM_Data sheet if present
    try:
        existing = pd.read_excel(xlsx_path, sheet_name="QM_Data")
        log.info("Loaded existing QM_Data: %d rows", len(existing))
    except Exception:
        existing = pd.DataFrame()
        log.info("No existing QM_Data sheet; starting fresh")

    new_frames = []
    for name, code in companies:
        log.info("  Fetching %s (%s) …", name, code)
        try:
            df = fetch_ashare_quarterly(name, code)
            if not df.empty:
                new_frames.append(df)
                log.info("    -> %d rows fetched", len(df))
            else:
                log.warning("    -> No data returned")
        except Exception as e:
            log.warning("    -> Failed: %s", e)
        time.sleep(0.4)   # polite API delay

    if not new_frames:
        log.warning("No new quarterly data retrieved.")
        return False

    new_data = pd.concat(new_frames, ignore_index=True)

    # Merge: remove stale rows for updated companies, append new rows
    updated_names = {n for n, _ in companies}
    if not existing.empty and "company" in existing.columns:
        existing = existing[~existing["company"].isin(updated_names)]

    # Ensure column alignment before concat
    for col in _COL_ORDER:
        if col not in existing.columns:
            existing[col] = None
        if col not in new_data.columns:
            new_data[col] = None

    merged = pd.concat(
        [existing[_COL_ORDER], new_data[_COL_ORDER]],
        ignore_index=True
    )

    write_qm_sheet(merged, str(xlsx_path))
    log.info("QM_Data sheet updated: %d total rows", len(merged))
    return True


# ── Action: Run Subprocess ────────────────────────────────────────────────────

def run_script(script_name: str, extra_args: list[str] = None) -> int:
    """Run a Python script in the same directory. Return exit code."""
    cmd = [sys.executable, str(_HERE / script_name)] + (extra_args or [])
    log.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(_HERE))
    return result.returncode


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Disclosure-gated SemiIntel sync")
    parser.add_argument("--force",   action="store_true", help="跳过披露检查，强制全量同步")
    parser.add_argument("--dry-run", action="store_true", help="只显示将触发的操作")
    parser.add_argument("--window",  type=int, default=1,  help="日期窗口（天），默认 ±1")
    args = parser.parse_args()

    today = date.today()
    print(f"\n{'='*64}")
    print(f"  SemiIntel Smart Sync  |  {today}")
    print(f"{'='*64}\n")

    # ── Step 1: Disclosure Check ──────────────────────────────────────────────
    if args.force:
        log.info("--force: skipping disclosure calendar check")
        due = pd.DataFrame()   # empty → will trigger full sync below
        annual_companies  = [(n, c) for n, c, _ in ASHARE_COMPANIES]
        quarterly_trigger = False
        annual_trigger    = True
    else:
        due = check_disclosures(window_days=args.window)

        if due.empty:
            print(f"[--] No target company disclosures within ±{args.window}d of {today}.")
            print("     No sync needed. Exiting.")
            print(f"\n{'='*64}\n")
            sys.exit(0)

        print(f"[!!] {len(due)} disclosure(s) detected:\n")
        display_cols = ["stock_code", "company_name", "首次预约", "实际披露", "period_label"]
        due_display  = due.rename(columns={"股票代码": "stock_code"})
        print(due_display[["stock_code", "company_name", "首次预约", "实际披露", "period_label"]]
              .to_string(index=False))
        print()

        # Separate annual vs quarterly disclosures
        annual_due    = due[due["report_type"] == "annual"]
        quarterly_due = due[due["report_type"].isin(["q1", "q2", "q3"])]

        annual_companies  = list(zip(annual_due["company_name"], annual_due["股票代码"]))
        quarterly_trigger = not quarterly_due.empty
        annual_trigger    = not annual_due.empty

    # ── Step 2: Annual Report Action ─────────────────────────────────────────
    if annual_trigger:
        print("[年报] Annual report(s) detected:")
        for name, code in annual_companies:
            print(f"       {name} ({code})")
        print()
        print("  NOTE: Annual revenue figures come from the Excel file.")
        print("  Please update 'Semi_Maker_Revenue_202603.xlsx' with new annual data,")
        print("  then re-run: python smart_sync.py --force")
        print()

        if not args.dry_run:
            # Refresh yjbb annual data for current year
            current_year = str(today.year)
            log.info("Running fetch_yjbb_annual.py --years %s …", current_year)
            rc = run_script("fetch_yjbb_annual.py", ["--years", current_year])
            if rc != 0:
                log.warning("fetch_yjbb_annual.py exited with code %d (non-fatal)", rc)

            # Still run sync_data.py to pick up any already-updated Excel rows
            log.info("Running sync_data.py to integrate any Excel updates …")
            rc = run_script("sync_data.py")
            if rc != 0:
                log.error("sync_data.py exited with code %d", rc)
                sys.exit(2)

    # ── Step 3: Quarterly Report Action ──────────────────────────────────────
    if quarterly_trigger and not args.force:
        quarterly_due = due[due["report_type"].isin(["q1", "q2", "q3"])]
        q_companies   = list(zip(quarterly_due["company_name"], quarterly_due["股票代码"]))
        report_type   = quarterly_due["report_type"].iloc[0]  # q1/q2/q3

        print(f"[季报] Quarterly report ({report_type.upper()}) detected for:")
        for name, code in q_companies:
            print(f"       {name} ({code})")
        print()

        if args.dry_run:
            log.info("[dry-run] Would fetch quarterly data and run sync + validate")
        else:
            log.info("Fetching updated quarterly data …")
            updated = fetch_quarterly_for(q_companies, report_type)

            if updated:
                log.info("Running fetch_yjbb_quarterly.py …")
                run_script("fetch_yjbb_quarterly.py")   # non-fatal if fails

                log.info("Running sync_data.py …")
                rc = run_script("sync_data.py")
                if rc != 0:
                    log.error("sync_data.py exited with code %d", rc)
                    sys.exit(2)
            else:
                log.warning("No quarterly data retrieved; skipping sync.")

    # ── Step 4: Validate ─────────────────────────────────────────────────────
    if not args.dry_run and (annual_trigger or quarterly_trigger or args.force):
        print()
        log.info("Running validate_data.py …")
        rc = run_script("validate_data.py")
        if rc >= 2:
            log.error("Validation found ERROR/CRITICAL issues (exit code %d)", rc)
            sys.exit(2)
        elif rc == 1:
            log.warning("Validation completed with warnings.")
        else:
            log.info("Validation passed cleanly.")

    print(f"\n{'='*64}")
    print("  Smart Sync complete.")
    print(f"{'='*64}\n")
    sys.exit(1)   # exit 1 = sync was triggered (per spec)


if __name__ == "__main__":
    main()
