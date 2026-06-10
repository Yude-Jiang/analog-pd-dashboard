# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SemiIntel China Competitors Dashboard — semiconductor competitive intelligence platform tracking 22 companies (Analog + P&D categories) across China, Taiwan, and US markets.

## Architecture

Single-page app (`dashboard.html`) + JSON data stores + Python pipeline scripts. No build step. Serve with `python -m http.server 8000`.

- `dashboard.html` — Chart.js 4.4.1, dual-axis charts (revenue bars + NI% lines). Two tabs: "19-26 Trends" (default active), "2026 Tracker" (hidden). Fetches `data.json` and `yjbb_annual.json` at runtime via `fetch()`. Theme in localStorage.
- `data.json` — Central DB. Per-company schema: `{category, currency, code, name, revenue, net_income, margin, audit, profile}`
- `yjbb_annual.json` — AkShare earnings for 19 A-share companies, 2019–2025. Units in 元; divide by 1e6 for M RMB.

## Data Pipeline (Standard Workflow)

1. Update `Semi_Maker_Revenue_202603.xlsx` with new annual data
2. `python sync_data.py` — pushes Excel → `data.json`
3. `python fetch_yjbb_annual.py --years 2025` — refreshes `yjbb_annual.json`
4. `python validate_data.py` — 13-rule quality check (exit 0=clean, 1=warn, 2=error, 3=critical)
5. Open `dashboard.html` via `python -m http.server 8000`

Automated: `python smart_sync.py` runs daily at 09:00 weekdays — checks AkShare disclosure calendar before fetching, skips if no target companies have filed.

## Critical Gotchas

**Silergy currency string**: stored as `"M TW"` (not `"M TWD"`). The `fxRate()` helper uses `includes('TW')`, not `includes('TWD')`. Do not "fix" this — it is intentional.

**FX conversion in `fxRate()`**: RMB ÷ 7.2, TWD ÷ 29.59, USD ÷ 1.0.

**A-share NI in Excel**: column is 归母净利润 (parent/attributable net profit), not total NI. Do not substitute consolidated NI.

**yjbb data priority**: for trend charts, `yjbb_annual.json` takes priority over `data.json` for A-share companies (stock code prefix 3, 6, or 688).

**yjbb units**: raw values are in 元 (Yuan). Divide by 1e6 to get M RMB, then by 7.2 for M USD.

**EDGAR XBRL segment routing**: route by duration (`end - start` in days): ≤95d = Q, ≥340d = Annual. Skip 6M and 9M segments entirely.

**MPWR 2024 NI**: was previously wrong (GrossProfit pulled instead of NetIncomeLoss). Correct value is 499.5M. Do not revert.

**R10 stale profile threshold**: 18 months = `year-1` + `month-6`, not simply `year-1`.

**`sync_data.py` file paths**: always uses `Path(__file__).parent` for relative resolution. Never hardcode absolute paths.

**Excluded company**: Nexperia is in the `EXCLUDED` set — `validate_data.py --fix` will remove it via rule R13.

**Segment revenue override**: Sanan (600703), Silan (600460), CR Micro (688396) carry a `segment_note` field in `data.json` — their `revenue` is segment-level (extracted from annual report PDFs via `fetch_segment_rev_pdf.py`), NOT company totals. When `segment_note` is present, `getRevUSD()` in dashboard.html prefers `data.json` revenue over yjbb. Margin/NI remain company-wide (yjbb). Do not let `sync_data.py` or AkShare refreshes overwrite these companies' revenue.

## Company Universe (22 companies)

**Analog A-share (8)**: SG micro (300661), 3-Peak (688536), Chipown (688508), Fortior (688279), Southchip (688484), Joulwatt (688141), Injoinic (688209), Novosense (688052)

**Analog non-A-share (3)**: Silergy (6415, TWD/M TW), MPWR (MPWR, USD), NVTS (NVTS, USD)

**P&D A-share (11)**: Silan (600460), CR Micro (688396), Yangjie (300373), Sino-Micro (600360), Star Power (603290), NCE (605111), JieJie Micro (300623), Oriental (688261), Macmicst (688711), Sanan (600703), UNT (688469)

## Script Reference

| Script | Purpose | Key flags |
|---|---|---|
| `sync_data.py` | Excel → `data.json`. Contains `CATEGORY_OVERRIDE` and `PROFILE_DATA` dicts. | — |
| `fetch_semi_data.py` | Fetches quarterly data (A-share via AkShare, Taiwan via MOPS, US via EDGAR). Writes to `QM_Data` Excel sheet. | — |
| `fetch_yjbb_annual.py` | Fetches AkShare yjbb annual data → `yjbb_annual.json`. | `--years 2025` |
| `validate_data.py` | 13-rule checker. `--fix` auto-repairs R05 (margin recalc) and R13 (remove excluded). | `--fix`, `--json` |
| `smart_sync.py` | Disclosure-gated sync; skips fetch if calendar shows no filings. | `--force`, `--dry-run`, `--window N` |
| `fetch_segment_rev_pdf.py` | CNINFO annual report PDF → Gemini → segment revenue → `data.json`. Sanan=集成电路产品, Silan=分立器件产品, CR Micro=产品与方案. Needs `CNINFO_COOKIE` + Gemini key (Secret Manager `VITE_GEMINI_API_KEY` or `GEMINI_API_KEY` env). PDFs cached in GCS. | `--dry-run`, `--companies`, `--years`, `--redownload` |
