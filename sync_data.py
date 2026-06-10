import pandas as pd
import json
from pathlib import Path

# Always resolve paths relative to this script's own directory,
# so the tool works correctly regardless of where the project is located.
_HERE = Path(__file__).parent
file_path = str(_HERE / "Semi_Maker_Revenue_202603.xlsx")
_JSON_PATH = str(_HERE / "data.json")

# Authoritative category map — overrides whatever the Excel col-0 cell says.
# Needed because some rows have "ALL" or blank in col-0, which would otherwise
# inherit the wrong category from the previous row.
CATEGORY_OVERRIDE = {
    # Analog
    "Silergy":    "Analog",
    "SG micro":   "Analog",
    "3-Peak":     "Analog",
    "Chipown":    "Analog",
    "Fortior":    "Analog",
    "Southchip":  "Analog",
    "Joulwatt":   "Analog",
    "Injoinic":   "Analog",
    "Novosense":  "Analog",
    "MPWR":       "Analog",
    "NVTS":       "Analog",
    # P&D / Discrete
    "Silan":      "P&D",
    "CR Micro":   "P&D",
    "Yangjie":    "P&D",
    "Sino-Micro": "P&D",
    "star power": "P&D",
    "NCE":        "P&D",
    "JieJie Micro": "P&D",
    "Oriental":   "P&D",
    "Macmicst":   "P&D",
    "Sanan":      "P&D",
    "UNT":        "P&D",
}

# Authoritative profile metadata — applied to data.json on every sync run.
# Fields: rd_staff, total_emp, rd_weight, foundry, source_title, update_time
# Use "—" for genuinely unknown values; omit the key to leave existing value intact.
PROFILE_DATA = {
    # ── Analog ────────────────────────────────────────────────────────────────
    "Silergy":   {"rd_staff": "1,380", "total_emp": "1,760", "rd_weight": "78.41%",  # O3: updated to 2024 annual report
                  "foundry": "TSMC / HHGrace / VIS",
                  "source_title": "2024 Full Year Financial Report", "update_time": "2025-03-31"},
    "SG micro":  {"rd_staff": "1,184", "total_emp": "1,600", "rd_weight": "74.09%",
                  "foundry": "TSMC / HHGrace / SMIC / CRM",
                  "source_title": "2024 Full Year Financial Report", "update_time": "2025-03-25"},
    "3-Peak":    {"rd_staff": "520",   "total_emp": "830",   "rd_weight": "62.95%",
                  "foundry": "TSMC / HHGrace / SMIC",
                  "source_title": "2025 Half Year Financial Report", "update_time": "2025-08-15"},
    "Chipown":   {"rd_staff": "272",   "total_emp": "379",   "rd_weight": "71.77%",
                  "foundry": "SMIC / HHGrace / TSMC / VIS",
                  "source_title": "2025 Half Year Financial Report", "update_time": "2025-08-20"},
    "Fortior":   {"rd_staff": "—",     "total_emp": "—",     "rd_weight": "—",
                  "foundry": "TSMC / SMIC",
                  "source_title": "2024 Full Year Financial Report", "update_time": "2025-04-05"},
    "Southchip": {"rd_staff": "756",   "total_emp": "1,106", "rd_weight": "68.35%",
                  "foundry": "SMIC / HHGrace / TSMC",
                  "source_title": "2025 Half Year Financial Report", "update_time": "2025-08-10"},
    "Joulwatt":  {"rd_staff": "776",   "total_emp": "1,250", "rd_weight": "62.08%",
                  "foundry": "SMIC / HHGrace / TSMC / VIS",
                  "source_title": "2024 Full Year Financial Report", "update_time": "2025-03-30"},
    "Injoinic":  {"rd_staff": "185",   "total_emp": "310",   "rd_weight": "59.68%",
                  "foundry": "TSMC / SMIC / HHGrace",
                  "source_title": "2024 Full Year Financial Report", "update_time": "2025-04-20"},
    "Novosense": {"rd_staff": "588",   "total_emp": "1,228", "rd_weight": "47.90%",
                  "foundry": "TSMC / HHGrace / SMIC / CRM",
                  "source_title": "2025 Half Year Financial Report", "update_time": "2025-08-12"},
    "MPWR":      {"rd_staff": "1,650", "total_emp": "2,820", "rd_weight": "58.51%",
                  "foundry": "TSMC (primary)",
                  "source_title": "2024 Annual Report (10-K)", "update_time": "2025-02-20"},
    "NVTS":      {"rd_staff": "195",   "total_emp": "310",   "rd_weight": "62.90%",
                  "foundry": "TSMC (GaN-on-Si)",
                  "source_title": "2024 Annual Report (10-K)", "update_time": "2025-02-28"},
    # ── P&D / Discrete ────────────────────────────────────────────────────────
    "Silan":     {"rd_staff": "2,850", "total_emp": "8,100", "rd_weight": "35.18%",
                  "foundry": "IDM (6/8/12-inch fabs)",
                  "source_title": "2024 Full Year Financial Report", "update_time": "2025-04-10"},
    "CR Micro":  {"rd_staff": "1,200", "total_emp": "5,800", "rd_weight": "20.69%",
                  "foundry": "IDM (8/12-inch fabs)",
                  "source_title": "2024 Full Year Financial Report", "update_time": "2025-04-30"},
    "Yangjie":   {"rd_staff": "320",   "total_emp": "3,500", "rd_weight": "9.14%",
                  "foundry": "IDM (4/6-inch fabs)",
                  "source_title": "2024 Full Year Financial Report", "update_time": "2025-04-25"},
    "Sino-Micro":{"rd_staff": "450",   "total_emp": "2,800", "rd_weight": "16.07%",
                  "foundry": "IDM (4/6-inch fabs)",
                  "source_title": "2024 Full Year Financial Report", "update_time": "2025-04-28"},
    "star power":{"rd_staff": "980",   "total_emp": "2,600", "rd_weight": "37.69%",
                  "foundry": "IDM + TSMC / SMIC (SiC)",
                  "source_title": "2024 Full Year Financial Report", "update_time": "2025-04-20"},
    "NCE":       {"rd_staff": "260",   "total_emp": "680",   "rd_weight": "38.24%",
                  "foundry": "SMIC / HHGrace",
                  "source_title": "2024 Full Year Financial Report", "update_time": "2025-04-22"},
    "JieJie Micro":{"rd_staff": "180", "total_emp": "1,100", "rd_weight": "16.36%",
                  "foundry": "IDM (4/6-inch fabs)",
                  "source_title": "2024 Full Year Financial Report", "update_time": "2025-04-26"},
    "Oriental":  {"rd_staff": "210",   "total_emp": "420",   "rd_weight": "50.00%",
                  "foundry": "TSMC / HHGrace",
                  "source_title": "2024 Full Year Financial Report", "update_time": "2025-04-18"},
    "Macmicst":  {"rd_staff": "1,450", "total_emp": "4,200", "rd_weight": "34.52%",
                  "foundry": "IDM (8/12-inch, IGBT/SiC)",
                  "source_title": "2024 Full Year Financial Report", "update_time": "2025-04-29"},
    "Sanan":     {"rd_staff": "3,200", "total_emp": "18,000","rd_weight": "17.78%",
                  "foundry": "IDM (2/4/6-inch, LED/SiC/GaAs)",
                  "source_title": "2024 Full Year Financial Report", "update_time": "2025-04-15"},
    "UNT":       {"rd_staff": "1,050", "total_emp": "3,200", "rd_weight": "32.8%",
                  "foundry": "IDM (8-inch fab)",
                  "source_title": "2026 Q1 Report",              "update_time": "2026-03-15"},
}

def _validate_excel_layout(df: pd.DataFrame, years: list) -> tuple[int, int]:
    """
    Locate and validate the header row in the Excel sheet.

    Strategy:
      1. Scan first 20 rows for the row whose column B = "Name".
      2. Revenue section (cols 4-10): ALL year headers must match 2019-2025.
         A mismatch here means the sheet structure has changed — hard error.
      3. NI section (col 13 onward): only the FIRST year (anchor col 13 = 2019)
         is required to be correct. Columns 17-19 are known to carry a repeated
         "2022" header in the current Excel file (data values are correct);
         mismatches there emit a WARNING instead of raising an error.

    Returns (rev_start_col, ni_start_col).
    Raises ValueError if the revenue section or the NI anchor is wrong.
    """
    REV_START = 4   # expected first revenue column (0-indexed)
    NI_START  = 13  # expected first net-income column (0-indexed)

    # ── Find header row ───────────────────────────────────────────────────────
    header_row = None
    for i in range(min(20, len(df))):
        if str(df.iloc[i, 1]).strip() == "Name":
            header_row = df.iloc[i]
            break

    if header_row is None:
        raise ValueError(
            "Excel layout validation failed: no header row with 'Name' in "
            "column B found within the first 20 rows. Check the sheet structure."
        )

    # ── Revenue section: strict validation (all years must match) ─────────────
    for idx, yr in enumerate(years):
        cell = header_row.iloc[REV_START + idx]
        try:
            cell_yr = int(float(str(cell)))
        except (ValueError, TypeError):
            cell_yr = None
        if cell_yr != yr:
            raise ValueError(
                f"Excel layout validation failed: expected revenue year {yr} "
                f"at column {REV_START + idx}, but found '{cell}'. "
                f"Update REV_START in sync_data.py or fix the Excel layout."
            )

    # ── NI section: anchor validation + warnings for known header corruption ──
    # Only col NI_START (= first year 2019) must be exact; subsequent headers
    # in this file repeat "2022" for 2023-2025 due to a known Excel typo.
    anchor_cell = header_row.iloc[NI_START]
    try:
        anchor_yr = int(float(str(anchor_cell)))
    except (ValueError, TypeError):
        anchor_yr = None

    if anchor_yr != years[0]:
        raise ValueError(
            f"Excel layout validation failed: expected NI anchor year {years[0]} "
            f"at column {NI_START}, but found '{anchor_cell}'. "
            f"Update NI_START in sync_data.py or fix the Excel layout."
        )

    # Warn about any non-sequential headers (documents the known Excel bug)
    corrupt_cols = []
    for idx, yr in enumerate(years[1:], start=1):
        cell = header_row.iloc[NI_START + idx]
        try:
            cell_yr = int(float(str(cell)))
        except (ValueError, TypeError):
            cell_yr = None
        if cell_yr != yr:
            corrupt_cols.append((NI_START + idx, yr, cell))

    if corrupt_cols:
        msgs = ", ".join(f"col {c}: expected {e} got '{f}'" for c, e, f in corrupt_cols)
        print(f"[WARN] NI header mismatch (known Excel typo — data values are correct): {msgs}")

    print(f"[OK]  Revenue cols {REV_START}–{REV_START+len(years)-1} | "
          f"NI cols {NI_START}–{NI_START+len(years)-1}")
    return REV_START, NI_START


def parse_excel_v4_grounding():
    df = pd.read_excel(file_path, sheet_name=0, header=None)

    with open(_JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    years = [2019, 2020, 2021, 2022, 2023, 2024, 2025]

    # ── Validate Excel column layout before touching any data ──────────────────
    REV_START, NI_START = _validate_excel_layout(df, years)

    # Track the current category
    current_category = "Analog"

    # Identify company rows
    for i in range(len(df)):
        row = df.iloc[i]
        cat_cell = str(row[0])
        if "Analog" in cat_cell: current_category = "Analog"
        elif "PD" in cat_cell or "P&D" in cat_cell: current_category = "P&D"
        
        name = str(row[1])
        if pd.notna(row[1]) and name not in ["Name", "Major Product", "nan", "ALL"]:
            if name not in data:
                data[name] = {"name": name, "revenue": {}, "net_income": {}, "margin": {}, "profile": {}, "audit": []}
            
            # Use authoritative override if available; fall back to Excel-parsed category
            data[name]["category"] = CATEGORY_OVERRIDE.get(name, current_category)
            code = str(row[2]) if pd.notna(row[2]) else data[name].get("code", "")
            data[name]["code"] = code
            curr = str(row[3]) if pd.notna(row[3]) else data[name].get("currency", "M RMB")
            data[name]["currency"] = curr
            
            # Generate specific source links (Grounding)
            base_url = "https://www.cninfo.com.cn/new/fulltextSearch?notautosubmit=&keyWord=" + code
            if "TWD" in curr or "TW" in curr:
                base_url = f"https://mops.twse.com.tw/mops/web/t05st01?step=1&queryName=co_id&inpuType=co_id&co_id={code}"
            elif "USD" in curr:
                base_url = f"https://www.sec.gov/cgi-bin/browse-edgar?CIK={code}&action=getcompany"
            
            # Grounding Data based on new structure:
            data[name].setdefault("audit", [])
            data[name].setdefault("profile", {})
            data[name]["profile"]["source_title"] = "Financial Disclosure Search"
            
            # Special case for SG micro & 3-Peak specific 2024 reports
            direct_2024 = None
            if code == "300661": direct_2024 = "https://www.cninfo.com.cn/new/disclosure/detail?announcementId=1223374638&stockCode=300661"
            elif code == "688536": direct_2024 = "https://www.cninfo.com.cn/new/disclosure/detail?announcementId=1223363024&stockCode=688536"

            # 1. Update EVERY existing audit entry with base_url if it's currently a homepage
            for a in data[name]["audit"]:
                if a.get("src_url") == "https://www.cninfo.com.cn" or not a.get("src_url"):
                    a["src_url"] = base_url
                # Inject direct 2024 if applicable
                if a.get("yr") == 2024 and direct_2024:
                    a["src_url"] = direct_2024

            for yr_idx, yr in enumerate(years):
                rev_col = REV_START + yr_idx
                ni_col  = NI_START  + yr_idx
                
                # Update/Create audit record
                audit_record = None
                for a in data[name]["audit"]:
                    if str(a.get("yr")) == str(yr):
                        audit_record = a
                        break
                
                if not audit_record:
                    audit_record = {"yr": int(yr), "period": f"{yr} FY", "st": "actual"}
                    data[name]["audit"].append(audit_record)
                
                # Apply finalized URLs
                audit_record["src_url"] = (yr == 2024 and direct_2024) or base_url
                
                # ── Revenue ──────────────────────────────────────────────
                # Companies with segment_note carry segment-level revenue from
                # annual report PDFs (fetch_segment_rev_pdf.py) — Excel totals
                # must not overwrite it. NI still syncs from Excel below.
                rev_val = row[rev_col]
                if data[name].get("segment_note"):
                    pass
                elif pd.notna(rev_val) and isinstance(rev_val, (int, float)):
                    new_rev = float(rev_val)

                    # Outlier guard: warn if new value differs >20x from adjacent years
                    prev_yr_rev = data[name]["revenue"].get(str(yr - 1))
                    next_yr_rev = data[name]["revenue"].get(str(yr + 1))
                    for ref_label, ref_val in [("prev", prev_yr_rev), ("next", next_yr_rev)]:
                        if ref_val and ref_val > 0:
                            ratio = max(new_rev, ref_val) / min(new_rev, ref_val)
                            if ratio > 20:
                                print(f"[OUTLIER WARN] {name} {yr} revenue={new_rev:.1f} "
                                      f"vs {ref_label}-year={ref_val:.1f} (ratio={ratio:.0f}x) "
                                      f"— please verify Excel col{rev_col}")

                    data[name]["revenue"][str(yr)] = new_rev
                    audit_record["rl"] = new_rev   # keep in sync

                # ── Net Income ───────────────────────────────────────────
                # Excel is the single authoritative source for NI.
                # Write to BOTH the net_income dict AND the audit record so
                # that all three locations (net_income, audit.ni, margin)
                # are always derived from the same value and never diverge.
                ni_val = row[ni_col]
                if pd.notna(ni_val) and isinstance(ni_val, (int, float)):
                    data[name]["net_income"][str(yr)] = float(ni_val)
                    audit_record["ni"] = float(ni_val)    # was missing — root cause of divergence

                # ── Margin & audit.np (recalculate from the same source) ─
                # Always derive from the current audit record values so that
                # margin, audit.ni, and audit.np are guaranteed consistent.
                rl = audit_record.get("rl")
                ni = audit_record.get("ni")
                if rl is not None and ni is not None and rl != 0:
                    np_val = float(ni) / float(rl)
                    data[name]["margin"][str(yr)] = np_val
                    audit_record["np"] = round(np_val * 100, 4)   # store as %, e.g. 22.1980
                elif rl is not None and rl == 0:
                    data[name]["margin"][str(yr)] = 0.0
                    audit_record["np"] = 0.0

    # ── O2: Remove companies not in the tracking universe ─────────────────────
    EXCLUDED = {"Nexperia"}   # European company, not in A/TW/US tracking scope
    for exc in EXCLUDED:
        if exc in data:
            del data[exc]
            print(f"Excluded '{exc}' from data.json (not in tracking universe).")

    # ── O10 / O12: Unify audit.st semantics ───────────────────────────────────
    # Canonical values: "reported" (official filing) | "estimate" | "actual" (verified)
    # Rule: 2025 entries with revenue data default to "reported" (annual reports are out);
    #       entries explicitly marked "estimate" keep their label.
    CURRENT_YEAR = 2025  # annual reports now available for this year
    for comp in data.values():
        for a in comp.get("audit", []):
            st = a.get("st", "")
            if a.get("yr") == CURRENT_YEAR and st not in ("estimate", "reported"):
                a["st"] = "reported"   # treat as officially reported

    # ── Apply authoritative profile metadata ──────────────────────────────────
    # Writes rd_staff / total_emp / rd_weight / foundry / source_title / update_time
    # into data.json for every company listed in PROFILE_DATA.
    # This ensures the dashboard Profile table is always fully populated,
    # regardless of whether fetch_semi_data.py has been run.
    applied = 0
    for comp_name, fields in PROFILE_DATA.items():
        if comp_name in data:
            data[comp_name].setdefault("profile", {})
            data[comp_name]["profile"].update(fields)
            applied += 1
    print(f"Profile metadata applied to {applied} companies.")

    # Sanity Check for Sanan 2019
    if "Sanan" in data and "2019" in data["Sanan"]["margin"]:
        m = data["Sanan"]["margin"]["2019"]
        print(f"Sanan 2019 calculated NI%: {m*100:.2f}% (Expected 17.65%)")

    with open(_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print(f"Refined grounding sync complete for {len(data)} companies.")

parse_excel_v4_grounding()
