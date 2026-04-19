#!/usr/bin/env python3
"""
validate_data.py — SemiIntel Data Quality Validator
=====================================================
独立运行的数据核查脚本，检测 data.json 中的各类数据质量问题。

运行方式:
    python validate_data.py            # 完整检查
    python validate_data.py --fix      # 检查 + 自动修复可修复项
    python validate_data.py --json     # 输出 JSON 格式报告

退出码:
    0 = 无问题  |  1 = 有警告  |  2 = 有错误  |  3 = 有严重错误
"""

import io
import json
import sys
import argparse
from pathlib import Path
from datetime import date, datetime

# Force UTF-8 output on Windows (avoids GBK encoding errors for Chinese text)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── 配置 ──────────────────────────────────────────────────────────────────────

_HERE      = Path(__file__).parent
_JSON_PATH = _HERE / "data.json"

# 完整追踪宇宙（应出现在 data.json 中）
EXPECTED_COMPANIES = {
    "SG micro", "3-Peak", "Chipown", "Fortior", "Southchip",
    "Joulwatt", "Injoinic", "Novosense",            # Analog A股
    "Silergy",                                       # Analog 台股
    "MPWR", "NVTS",                                  # Analog 美股
    "Silan", "CR Micro", "Yangjie", "Sino-Micro",
    "star power", "NCE", "JieJie Micro", "Oriental",
    "Macmicst", "Sanan", "UNT",                      # P&D
}

EXCLUDED_COMPANIES = {"Nexperia"}   # 不在追踪范围

VALID_CATEGORIES = {"Analog", "P&D"}
VALID_CURRENCIES = {"M RMB", "M TWD", "M TW", "M USD"}
ANALYSIS_YEARS   = [2019, 2020, 2021, 2022, 2023, 2024, 2025]
PROFILE_FIELDS   = ["rd_staff", "total_emp", "rd_weight", "foundry", "update_time"]

# 合理性边界
MAX_YOY_RATIO    = 20    # 相邻年份营收比超过 20x 视为异常
NI_MARGIN_MIN    = -0.60 # 净利率下限 -60%
NI_MARGIN_MAX    =  0.80 # 净利率上限  80%
MARGIN_TOLERANCE =  0.01 # margin 字段与 ni/revenue 计算值允许差异 1%

# ── 报告收集器 ─────────────────────────────────────────────────────────────────

SEVERITY = {"CRITICAL": 3, "ERROR": 2, "WARN": 1, "INFO": 0}

findings = []   # [{severity, rule, company, detail}]

def report(severity: str, rule: str, company: str, detail: str):
    findings.append({
        "severity": severity,
        "rule":     rule,
        "company":  company,
        "detail":   detail,
    })


# ══════════════════════════════════════════════════════════════════════════════
# 检查规则
# ══════════════════════════════════════════════════════════════════════════════

def check_universe(data: dict):
    """R01 — 追踪宇宙完整性：期望公司是否全部存在"""
    present = set(data.keys())
    missing = EXPECTED_COMPANIES - present
    extra   = present - EXPECTED_COMPANIES - EXCLUDED_COMPANIES

    for c in sorted(missing):
        report("ERROR", "R01_MISSING_COMPANY", c,
               f"公司不在 data.json 中，但在追踪宇宙里")
    for c in sorted(extra):
        report("WARN", "R01_EXTRA_COMPANY", c,
               f"公司在 data.json 中，但不在追踪宇宙或排除名单里")


def check_metadata(data: dict):
    """R02 — 基本字段：category / currency / code 合法性"""
    for name, comp in data.items():
        cat  = comp.get("category", "")
        curr = comp.get("currency", "")
        code = comp.get("code", "")

        if cat not in VALID_CATEGORIES:
            report("ERROR", "R02_INVALID_CATEGORY", name,
                   f"category='{cat}' 不在 {VALID_CATEGORIES}")
        if curr not in VALID_CURRENCIES:
            report("WARN", "R02_INVALID_CURRENCY", name,
                   f"currency='{curr}' 不在 {VALID_CURRENCIES}")
        if not code or code in ("nan", "None"):
            report("WARN", "R02_MISSING_CODE", name, "code 字段为空")


def check_revenue_outliers(data: dict):
    """R03 — YoY 异常值：相邻年份营收比超过 MAX_YOY_RATIO"""
    for name, comp in data.items():
        rev = comp.get("revenue", {})
        vals = sorted(
            [(int(k), v) for k, v in rev.items()
             if k.isdigit() and isinstance(v, (int, float)) and v > 0],
            key=lambda x: x[0]
        )
        for i in range(1, len(vals)):
            yr_prev, v_prev = vals[i - 1]
            yr_curr, v_curr = vals[i]
            if yr_curr - yr_prev != 1:
                continue   # 不连续年份跳过
            if v_prev > 0 and v_curr > 0:
                ratio = max(v_curr, v_prev) / min(v_curr, v_prev)
                if ratio > MAX_YOY_RATIO:
                    report("CRITICAL", "R03_REVENUE_OUTLIER", name,
                           f"{yr_prev}→{yr_curr}: {v_prev:.1f}→{v_curr:.1f} "
                           f"(比值 {ratio:.0f}x，超过阈值 {MAX_YOY_RATIO}x)")


def check_negative_revenue(data: dict):
    """R04 — 负营收：营收不应为负数"""
    for name, comp in data.items():
        for yr, v in comp.get("revenue", {}).items():
            if isinstance(v, (int, float)) and v < 0:
                report("ERROR", "R04_NEGATIVE_REVENUE", name,
                       f"revenue[{yr}] = {v:.2f}（营收不应为负）")


def check_margin_consistency(data: dict):
    """R05 — Margin 一致性：margin[yr] 应等于 net_income[yr] / revenue[yr]"""
    for name, comp in data.items():
        rev_d    = comp.get("revenue",    {})
        ni_d     = comp.get("net_income", {})
        margin_d = comp.get("margin",     {})

        for yr in ANALYSIS_YEARS:
            k    = str(yr)
            rev  = rev_d.get(k)
            ni   = ni_d.get(k)
            marg = margin_d.get(k)

            if rev and ni is not None and marg is not None and rev != 0:
                expected = ni / rev
                diff = abs(marg - expected)
                if diff > MARGIN_TOLERANCE:
                    report("ERROR", "R05_MARGIN_INCONSISTENT", name,
                           f"margin[{yr}]={marg:.4f} 但 ni/rev={expected:.4f} "
                           f"（差值 {diff:.4f}，超过容差 {MARGIN_TOLERANCE}）")


def check_margin_range(data: dict):
    """R06 — Margin 合理范围：净利率应在 [-60%, +80%]"""
    for name, comp in data.items():
        for yr, m in comp.get("margin", {}).items():
            if not isinstance(m, (int, float)):
                continue
            if m < NI_MARGIN_MIN or m > NI_MARGIN_MAX:
                report("WARN", "R06_MARGIN_RANGE", name,
                       f"margin[{yr}] = {m*100:.1f}% 超出合理区间 "
                       f"[{NI_MARGIN_MIN*100:.0f}%, {NI_MARGIN_MAX*100:.0f}%]")


def check_audit_revenue_sync(data: dict):
    """R07 — Audit ↔ Revenue 同步：audit[].rl 应与 revenue[yr] 一致"""
    for name, comp in data.items():
        rev_d = comp.get("revenue", {})
        for a in comp.get("audit", []):
            yr = str(a.get("yr", ""))
            rl = a.get("rl")
            rv = rev_d.get(yr)
            if rl is not None and rv is not None:
                if abs(rl - rv) > 0.01:
                    report("WARN", "R07_AUDIT_REVENUE_MISMATCH", name,
                           f"audit[{yr}].rl={rl:.2f} ≠ revenue[{yr}]={rv:.2f} "
                           f"（差值 {abs(rl-rv):.2f}）")


def check_audit_np_sync(data: dict):
    """R08 — Audit np 一致性：audit[].np 应与 audit[].ni / audit[].rl * 100 一致"""
    for name, comp in data.items():
        for a in comp.get("audit", []):
            yr = a.get("yr")
            rl = a.get("rl")
            ni = a.get("ni")
            np_stored = a.get("np")
            if rl and ni is not None and np_stored is not None and rl != 0:
                expected = ni / rl * 100
                if abs(np_stored - expected) > 0.5:
                    report("WARN", "R08_AUDIT_NP_MISMATCH", name,
                           f"audit[{yr}].np={np_stored:.2f}% 但 ni/rl*100={expected:.2f}%")


def check_profile_completeness(data: dict):
    """R09 — Profile 完整性：必填字段不能为空或 '—'"""
    for name, comp in data.items():
        p = comp.get("profile", {})
        missing = [f for f in PROFILE_FIELDS if not p.get(f) or p[f] == "—"]
        if missing:
            report("INFO", "R09_PROFILE_INCOMPLETE", name,
                   f"profile 字段缺失或为 '—': {missing}")


def check_stale_profiles(data: dict):
    """R10 — Profile 时效性：update_time 超过 18 个月视为陈旧"""
    # 18 months = subtract 18 from absolute month count
    today = date.today()
    cutoff_month = today.month - 6          # 12 months back (year-1) + 6 more = 18
    cutoff_year  = today.year - 1
    if cutoff_month <= 0:
        cutoff_month += 12
        cutoff_year  -= 1
    cutoff = date(cutoff_year, cutoff_month, 1)
    for name, comp in data.items():
        t = comp.get("profile", {}).get("update_time", "")
        if t and t != "—":
            try:
                d = date.fromisoformat(t)
                if d < cutoff:
                    report("WARN", "R10_STALE_PROFILE", name,
                           f"update_time={t}，距今超过 18 个月，请核实是否有新报告")
            except ValueError:
                report("WARN", "R10_STALE_PROFILE", name,
                       f"update_time='{t}' 格式不合法（应为 YYYY-MM-DD）")


def check_data_coverage(data: dict):
    """R11 — 数据覆盖度：追踪年份内各公司是否有营收数据"""
    for name, comp in data.items():
        if name not in EXPECTED_COMPANIES:
            continue
        rev = comp.get("revenue", {})
        missing_yrs = [y for y in ANALYSIS_YEARS if str(y) not in rev or rev[str(y)] is None]
        if len(missing_yrs) > 2:
            report("WARN", "R11_LOW_COVERAGE", name,
                   f"缺失 {len(missing_yrs)}/{len(ANALYSIS_YEARS)} 个年份数据: {missing_yrs}")


def check_currency_magnitude(data: dict):
    """R12 — 货币量级：M 单位下各公司营收应在合理范围 (0.1M ~ 100,000M)"""
    for name, comp in data.items():
        curr = comp.get("currency", "")
        for yr, v in comp.get("revenue", {}).items():
            if not isinstance(v, (int, float)) or v <= 0:
                continue
            if v < 0.1:
                report("WARN", "R12_MAGNITUDE_TOO_SMALL", name,
                       f"revenue[{yr}]={v:.4f} {curr} 疑似单位错误（< 0.1M）")
            if v > 100_000:
                report("WARN", "R12_MAGNITUDE_TOO_LARGE", name,
                       f"revenue[{yr}]={v:.0f} {curr} 疑似单位错误（> 100,000M）")


def check_excluded_not_present(data: dict):
    """R13 — 排除公司不应出现在 data.json 中"""
    for exc in EXCLUDED_COMPANIES:
        if exc in data:
            report("ERROR", "R13_EXCLUDED_PRESENT", exc,
                   f"'{exc}' 在排除名单中，但仍存在于 data.json")


# ══════════════════════════════════════════════════════════════════════════════
# 自动修复
# ══════════════════════════════════════════════════════════════════════════════

def auto_fix(data: dict) -> list[str]:
    """对可以安全自动修复的问题执行修复，返回修复描述列表。"""
    fixed = []

    # Fix R13: 删除排除名单中的公司
    for exc in list(EXCLUDED_COMPANIES):
        if exc in data:
            del data[exc]
            fixed.append(f"[R13] 已删除排除公司 '{exc}'")

    # Fix R05: 重新计算 margin，确保与 ni/revenue 一致
    for name, comp in data.items():
        rev_d = comp.get("revenue", {})
        ni_d  = comp.get("net_income", {})
        for yr in ANALYSIS_YEARS:
            k   = str(yr)
            rev = rev_d.get(k)
            ni  = ni_d.get(k)
            if rev and ni is not None and rev != 0:
                correct = ni / rev
                current = comp.get("margin", {}).get(k)
                if current is None or abs(current - correct) > MARGIN_TOLERANCE:
                    comp.setdefault("margin", {})[k] = round(correct, 8)
                    fixed.append(f"[R05] {name} margin[{yr}] 重新计算: {correct:.4f}")

    return fixed


# ══════════════════════════════════════════════════════════════════════════════
# 主程序
# ══════════════════════════════════════════════════════════════════════════════

def run_all_checks(data: dict):
    check_universe(data)
    check_metadata(data)
    check_revenue_outliers(data)
    check_negative_revenue(data)
    check_margin_consistency(data)
    check_margin_range(data)
    check_audit_revenue_sync(data)
    check_audit_np_sync(data)
    check_profile_completeness(data)
    check_stale_profiles(data)
    check_data_coverage(data)
    check_currency_magnitude(data)
    check_excluded_not_present(data)


def print_report():
    counts = {s: 0 for s in SEVERITY}
    for f in findings:
        counts[f["severity"]] += 1

    by_sev = {s: [] for s in SEVERITY}
    for f in findings:
        by_sev[f["severity"]].append(f)

    print("\n" + "=" * 68)
    print("  SemiIntel Data Quality Report")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Source:    {_JSON_PATH}")
    print("=" * 68)

    for sev in ["CRITICAL", "ERROR", "WARN", "INFO"]:
        items = by_sev[sev]
        if not items:
            continue
        icons = {"CRITICAL": "[!!]", "ERROR": "[EE]", "WARN": "[WW]", "INFO": "[II]"}
        print(f"\n{icons[sev]} {sev} ({len(items)})")
        print("-" * 60)
        for f in sorted(items, key=lambda x: x["company"]):
            print(f"  [{f['rule']}] {f['company']}")
            print(f"    -> {f['detail']}")

    print("\n" + "-" * 68)
    print(f"  Summary: CRITICAL={counts['CRITICAL']}  ERROR={counts['ERROR']}  "
          f"WARN={counts['WARN']}  INFO={counts['INFO']}")
    print("=" * 68 + "\n")


def main():
    parser = argparse.ArgumentParser(description="SemiIntel data quality validator")
    parser.add_argument("--fix",  action="store_true", help="自动修复可修复项并写回 data.json")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出报告")
    args = parser.parse_args()

    if not _JSON_PATH.exists():
        print(f"[ERROR] data.json 不存在: {_JSON_PATH}", file=sys.stderr)
        sys.exit(2)

    data = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
    run_all_checks(data)

    if args.fix:
        fixed = auto_fix(data)
        if fixed:
            _JSON_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"\n[OK] Auto-fixed {len(fixed)} items:")
            for f in fixed:
                print(f"  {f}")
        else:
            print("\n[OK] No auto-fixable items found")

    if args.json:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
    else:
        print_report()

    # 退出码反映最高严重等级
    max_sev = max((SEVERITY[f["severity"]] for f in findings), default=0)
    sys.exit(max_sev)


if __name__ == "__main__":
    main()
