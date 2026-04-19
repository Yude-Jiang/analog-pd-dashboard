"""fetch_profiles.py — Fetch company profiles from XueQiu (via AkShare) for all A-share companies.
Saves to profiles_xq.json for use by dashboard hover popup.

Usage:
    python fetch_profiles.py
"""
import io, sys, json, time
from pathlib import Path
from datetime import datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import akshare as ak

_HERE = Path(__file__).parent

# All A-share company codes (prefix determines exchange)
A_SHARE_CODES = [
    # Analog A-share
    "300661", "688536", "688508", "688279", "688484", "688141", "688209", "688052",
    # P&D A-share
    "600460", "688396", "300373", "600360", "603290", "605111", "300623", "688261",
    "688711", "600703", "688469",
]

def _xq_symbol(code: str) -> str:
    """Convert bare A-share code to XueQiu symbol (e.g. 300661 → SZ300661)."""
    if code.startswith(("6",)):
        return "SH" + code
    return "SZ" + code


FIELD_MAP = {
    "org_name_cn":           "注册全称",
    "classi_name":           "企业属性",
    "actual_controller":     "实控人",
    "chairman":              "董事长",
    "executives_nums":       "高管人数",
    "issue_price":           "发行价(元)",
    "main_operation_business": "主营业务",
    "org_cn_introduction":   "核心简介",
    # bonus fields
    "org_short_name_cn":     "简称",
    "legal_representative":  "法人代表",
    "staff_num":             "员工总数",
    "listed_date":           "上市日期",
    "provincial_name":       "注册省份",
    "org_website":           "官网",
}


def fetch_one(code: str) -> dict:
    symbol = _xq_symbol(code)
    df = ak.stock_individual_basic_info_xq(symbol=symbol)
    row = dict(zip(df["item"], df["value"]))
    out = {"code": code, "symbol": symbol}
    for src_key, dst_key in FIELD_MAP.items():
        val = row.get(src_key)
        # Convert timestamp ms → date string for listed_date
        if src_key == "listed_date" and val:
            try:
                val = datetime.fromtimestamp(int(val) / 1000).strftime("%Y-%m-%d")
            except Exception:
                pass
        # Convert issue_price to float if possible
        if src_key == "issue_price" and val:
            try:
                val = float(val)
            except Exception:
                pass
        # Convert executives_nums to int
        if src_key == "executives_nums" and val:
            try:
                val = int(val)
            except Exception:
                pass
        out[dst_key] = val if val not in (None, "", "None") else None
    return out


def main():
    out_path = _HERE / "profiles_xq.json"
    profiles = {}

    # Load existing to allow partial updates
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
            profiles = existing.get("profiles", {})
        except Exception:
            pass

    ok, fail = 0, 0
    for code in A_SHARE_CODES:
        try:
            print(f"  Fetching {code}…", end=" ", flush=True)
            p = fetch_one(code)
            profiles[code] = p
            print(f"OK — {p.get('简称', '?')} | {p.get('企业属性', '?')}")
            ok += 1
        except Exception as e:
            print(f"FAIL: {e}")
            fail += 1
        time.sleep(0.4)   # polite rate limit

    payload = {
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "count": len(profiles),
        "profiles": profiles,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {len(profiles)} profiles → {out_path.name}  (ok={ok}, fail={fail})")


if __name__ == "__main__":
    main()
