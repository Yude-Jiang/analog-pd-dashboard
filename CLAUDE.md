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

## Execution Environment Preferences

**市场情报分析 & AIGC 数据处理任务优先使用 Colab**，而非 Cloud Shell 或本地终端：

- 需要调用外部 API（Gemini、AkShare、CNINFO PDF 下载）的批量任务 → Colab
- 需要 GCP 认证但不依赖 Cloud Run 部署的脚本 → Colab（`google.colab.auth.authenticate_user()`）
- 数据清洗、PDF RAG 提取、LLM 辅助分析 → Colab notebook（结果可直接 commit push 回 GitHub）
- Cloud Shell 仅用于：`git` 操作、`gcloud run deploy` 部署、快速命令行验证

Colab 已与 GitHub 集成，打开 `.ipynb` 文件即可直接运行，结果 push 回对应分支。现有 notebook：`extract_segment_revenue.ipynb`（分部营收PDF提取）。

## Failure Modes That Keep Recurring

Every one of these has bitten this repo more than once, in different scripts.
They recur because each data pipeline (AkShare, EDGAR, MOPS/OpenAPI, Excel,
PDF) was written separately by copy-paste, so a fix in one never reaches the
others. **When you fix one of these, grep for the same pattern in every other
fetcher before you stop.**

### 1. Hardcoded years

Found in `fetch_yjbb_quarterly.py` (`DEFAULT_YEARS`), `fetch_silergy_to_json.py`
(`YEARS`), `dashboard.html` (Excel export `QTR_YEAR`), `app.py` (`FETCH_YEARS`).
Each silently stopped covering the current year once the calendar passed it —
no error, just quietly stale data.

Always derive from `datetime.date.today().year`, and expose `--years`.
`grep -rn "20[0-9][0-9]" *.py dashboard.html` before adding any new fetcher.

### 2. Derived values written only when absent

`fetch_edgar_to_json.py` had `if q4_key in d_dict: continue`, so a Q4 computed
from bad quarters (83.234 against a true 17.978) survived the run that fixed
its inputs. The same trap nearly hit Silergy: placeholder monthly values would
have had `quarters_from_months` overwrite a real quarter.

**A derived value must be recomputed every run and dropped when its inputs are
incomplete.** Never "fill in if missing". Q4 = annual − (Q1+Q2+Q3), quarterly
sums, CAGR and margins are all derived.

### 3. Green CI that does nothing

Four separate instances today:
- `refresh-quarterly.yml` POSTed to Cloud Run `/refresh`, which returns 202
  immediately; the workflow asserted on the HTTP code, so it passed every day
  for months while the fetchers it triggered wrote to a temp dir that was
  deleted.
- `/refresh` runs its work in a background thread — exceptions never reach the
  caller.
- Both EDGAR and MOPS fetchers `exit 0` when the upstream is unreachable, and
  the workflow steps are `continue-on-error`.
- `smart_sync.py` exits 1 by design when a sync runs, which `bash -e` treated
  as failure, skipping the commit step.

**Assert on the data outcome, not on an exit code or HTTP status.** Every fetch
workflow ends with a step that prints what actually landed (row counts, latest
period, quarters-vs-annual reconciliation) so a dead pipeline is visible in the
log rather than hidden behind a green check.

### 4. A wrong identifier returns someone else's data

`NVTS` was pinned to CIK `0001831868`, which is not Navitas. EDGAR answered
HTTP 200 with a shell company's filings — FY2024 revenue of $135K against
Navitas's $83.3M. Nothing errored.

**Resolve identifiers from the authoritative map** (SEC `company_tickers.json`)
and **log the identity the API echoes back** (`entityName`) on every fetch.

### 5. Debug by dumping the upstream payload, not by reasoning from symptoms

The NVTS diagnosis went wrong twice — first blaming dead code in
`_to_quarterly`, then the XBRL tag selection — while the cause was the CIK.
Same with MOPS: a constant 686-byte response looked like a retired endpoint
and was actually an anti-bot block page. Both were settled in one step by
printing the raw response.

**When data looks wrong, log the raw upstream payload first.** Every fetcher
has a `--debug` flag that dumps raw records, matched field names and each
derivation step. Use it before forming a hypothesis.

### 6. Diagnostics that need a CI round trip must be exhaustive

Each hypothesis tested through GitHub Actions costs a merge, a run and a log
download. Do not test one guess per run. Make the diagnostic self-describing:
discover endpoints from the service's own swagger spec, match fields by
keyword against the record's actual keys, try every candidate, and print
everything needed to write the final code in one pass.

## Serving Paths — What Needs a Deploy

Two different mechanisms, and confusing them costs a debugging cycle every time:

| File | Served from | Updated by | Live without deploy? |
|---|---|---|---|
| `yjbb_annual.json`, `yjbb_quarterly.json`, `profiles_xq.json` | GCS bucket | Cloud Run `/refresh` uploads | **Yes** |
| `data.json` | bundled container file | GitHub Actions commit | **No — needs `gcloud run deploy`** |
| `dashboard.html` | bundled container file | commit | **No — needs deploy** |

`data.json` is deliberately excluded from `GCS_BLOBS` so a stale GCS copy can
never overwrite `segment_note`. The consequence is that MPWR/NVTS/Silergy
quarterly updates only reach production on the next deploy — roughly 5 windows
a year, around earnings season. Check with:

```bash
U=https://analog-dashboard-460989091461.asia-east1.run.app
[ "$(curl -s $U/data.json | md5sum | cut -d" " -f1)" = "$(md5sum data.json | cut -d" " -f1)" ] \
  && echo "live is current" || echo "deploy needed"
```

**One writer per file.** `data.json` is written only by GitHub Actions
committing to the repo; Cloud Run must never upload it. Two writers is how
`segment_note` got clobbered, and the fix for that is what orphaned the
EDGAR/Silergy pipeline for months.

## Git / Cloud Shell Gotchas

- `git pull origin main` does **not** update other remote-tracking branches.
  Always `git fetch origin <branch>` explicitly before merging it, or you
  merge a stale ref — or nothing at all.
- `git merge` opens an editor for the merge commit. In Cloud Shell a pasted
  multi-line block then feeds its remaining lines into nano instead of the
  shell, so the push silently never happens. Use `git merge --no-edit`, and
  paste commands one at a time.
- Flask sends no `Cache-Control` when `max_age` is unset, so browsers applied
  heuristic caching to `dashboard.html` and served a pre-deploy copy. `app.py`
  now sets `no-cache` on every response (revalidate, cheap 304). The JSON
  fetches were never affected — the front end appends `?v=<timestamp>` — which
  is exactly what made a caching problem look like a failed deploy.

## Script Reference

| Script | Purpose | Key flags |
|---|---|---|
| `sync_data.py` | Excel → `data.json`. Contains `CATEGORY_OVERRIDE` and `PROFILE_DATA` dicts. | — |
| `fetch_semi_data.py` | Fetches quarterly data (A-share via AkShare, Taiwan via MOPS, US via EDGAR). Writes to `QM_Data` Excel sheet. | — |
| `fetch_yjbb_annual.py` | Fetches AkShare yjbb annual data → `yjbb_annual.json`. | `--years 2025` |
| `validate_data.py` | 13-rule checker. `--fix` auto-repairs R05 (margin recalc) and R13 (remove excluded). | `--fix`, `--json` |
| `smart_sync.py` | Disclosure-gated sync; skips fetch if calendar shows no filings. | `--force`, `--dry-run`, `--window N` |
| `fetch_segment_rev_pdf.py` | CNINFO annual report PDF → Gemini → segment revenue → `data.json`. Sanan=集成电路产品, Silan=分立器件产品, CR Micro=产品与方案. Needs `CNINFO_COOKIE` + Gemini key (Secret Manager `VITE_GEMINI_API_KEY` or `GEMINI_API_KEY` env). PDFs cached in GCS. | `--dry-run`, `--companies`, `--years`, `--redownload` |
