# AnalogPD — Competitors Analysis Dashboard

A competitive intelligence platform tracking **22 semiconductor companies** (11 Analog + 11 P&D/Discrete) across China A-share, Taiwan, and US markets.

**线上地址：** https://analog-dashboard-460989091461.asia-east1.run.app

---

## 🆕 v4.0 更新说明 (2026-04-23)

### 新增：趋势图 2025 季度分段显示

19-26 趋势分析图中，2025 年柱状图改为 **Q1 / Q2 / Q3 / Q4 叠加分段**呈现：

| 情况 | 显示效果 |
|---|---|
| FY2025 已全年披露（A 股） | Q1+Q2+Q3+Q4 四段全实心，由浅到深同色系，总高度 = 年度实际营收 |
| Q1+Q2+Q3 已知、Q4 待披露 | Q1/Q2/Q3 实心 + Q4 **斜纹**（估算 = Q3 镜像） |
| 仅 H1 已知 | Q1/Q2 实心 + Q3/Q4 斜纹（估算 = H1 ÷ 2） |
| 无季度数据（MPWR/NVTS/Silergy） | FY 年度值填入 Q4 slot，单块实心，tooltip 显示 "2025 FY" |

- 叠加总高度 = 年度营收，与 2024 bar **直接可比**
- 排序逻辑不变：已披露 FY2025 实际值的公司按营收降序排前，估算公司沉底

### 新增：GitHub Actions 数据刷新工作流

| 工作流 | 触发方式 | 作用 |
|---|---|---|
| **Refresh YJBB Annual Data** | 手动 dispatch，输入 `years`（默认 2025） | 刷新 A 股 yjbb 年报 + 季报，自动 commit |
| **Refresh EDGAR Quarterly** | 手动 dispatch，输入 `tickers`（默认 MPWR NVTS） | 从 SEC EDGAR 拉取 MPWR/NVTS 季度数据，自动 commit |
| **Smart Sync** | 每工作日 09:00 北京时间自动触发 + 手动 dispatch | 检查披露日历，有新年报/季报才执行同步 |

> 由于本地沙箱环境无法访问 AkShare / EDGAR 外部网络，所有数据刷新操作均通过 GitHub Actions 完成。

---

## 📋 v3.0 功能说明 (2026-04-10)

### 筛选查找功能
趋势分析页顶部新增 Filter Bar，支持：

| 控件 | 功能 |
|---|---|
| 🔍 文本框 | 按公司名或股票代码实时搜索（输入即响应） |
| Sector | All / Analog / P&D 分类筛选 |
| Market | All / A股(RMB) / Taiwan(TWD) / US(USD) 市场筛选 |
| YoY | All / >+20% / 0~+20% / <0% 增速筛选 |
| ↺ Reset | 一键清空所有筛选条件 |

筛选结果同步作用于**趋势图**和**Profile 表格**。

---

## 📋 v2.0 功能说明 (2026-04-09)

> **核心变更：A 股数据源全面切换至 AkShare yjbb，放弃 Excel 手动维护路径。**

### 数据源重构
- **A 股（19家）** 营收/净利润率来自 `AkShare stock_yjbb_em`，存入 `yjbb_annual.json`，yjbb 优先级高于 `data.json`
- **非 A 股**（Silergy / MPWR / NVTS）继续使用 `data.json` 手工维护

### 统计口径透明化
- 统计栏改为 **"N 家样本公司营收合计"**，明确非行业 TAM
- 未全部披露时显示 `~X.XXB USD (部分披露)`，隐藏 YoY/CAGR 避免误导
- 末尾实时显示 **"2025年报 N/M 已披露"**

### 完整功能列表

| 功能 | 说明 |
|---|---|
| **2025 季度分段** | 趋势图 2025 bar 拆分为 Q1-Q4 叠加，斜纹区分估算季度 |
| **筛选查找** | 文本搜索 + Sector / Market / YoY 三级下拉，图表与表格同步响应 |
| **公司 Profile 弹窗** | 有档案的公司名前显示 🔍，悬停弹出详情卡（注册全称、实控人、董事长、企业属性、发行价、主营业务等） |
| **Profile 表格排序** | 点击任意列标题升降序，缺失值自动置底 |
| **Export Excel** | 右上角 ⬇ 导出，文件名 `AnalogPD_Revenue_YYYY-MM-DD.xlsx`，每年含 Rev(M RMB) / Rev(M USD) / NI(M RMB) / NI(M USD) / NI% / 披露日期 6 列 |
| **中英文切换** | EN/中 按钮，覆盖全部静态文字 |
| **数据质量验证** | `validate_data.py` 13 条规则，退出码 0/1/2/3 对应 INFO/WARN/ERROR/CRITICAL |
| **智能门控同步** | `smart_sync.py` 每日 09:00 检查披露日历，有新年报/季报才触发同步 |

---

## 📁 文件结构

| 文件 | 说明 |
|---|---|
| `dashboard.html` | 主界面：筛选栏、趋势图（含2025季度分段）、Profile 表格、弹窗、排序、导出 |
| `data.json` | 中央数据库：22 家公司历史营收、净利润、profile、audit |
| `yjbb_annual.json` | AkShare yjbb 年报：19 家 A 股 2019–2025 营收与净利润率 |
| `yjbb_quarterly.json` | AkShare yjbb 季报：19 家 A 股 2024–2025 各季度营收与净利润 |
| `profiles_xq.json` | 雪球 XQ 档案：19 家 A 股注册全称、实控人、发行价等 |
| `app.py` | Cloud Run Flask 服务：静态文件 + GCS JSON 路由 + /refresh 端点 |
| `Dockerfile` | 容器构建配置 |
| `requirements_cloudrun.txt` | Cloud Run Python 依赖 |
| `sync_data.py` | Excel → `data.json` 同步 |
| `fetch_yjbb_annual.py` | AkShare yjbb 年报 → `yjbb_annual.json` |
| `fetch_yjbb_quarterly.py` | AkShare yjbb 季报 → `yjbb_quarterly.json` |
| `fetch_edgar_to_json.py` | SEC EDGAR 季度数据 → `data.json`（MPWR / NVTS） |
| `fetch_semi_data.py` | 季报数据底层抓取（A 股 AkShare / 台湾 MOPS / 美股 EDGAR） |
| `fetch_profiles.py` | 雪球 XQ 档案 → `profiles_xq.json` |
| `validate_data.py` | 13 规则数据质量验证 |
| `smart_sync.py` | 披露日历门控自动同步（每日 09:00 工作日） |
| `.github/workflows/refresh-yjbb-annual.yml` | GitHub Actions：手动刷新 A 股年报 + 季报 |
| `.github/workflows/refresh-edgar.yml` | GitHub Actions：手动刷新 MPWR/NVTS EDGAR 季度数据 |
| `.github/workflows/smart-sync.yml` | GitHub Actions：工作日自动门控同步 |

---

## 🚀 标准工作流

### 日常（自动）
```
GitHub Actions Smart Sync 每工作日 09:00 自动运行：
  ↓ 检查 AkShare 披露日历
  ↓ 若有目标公司年报/季报 → 自动抓取 + 验证 + commit 回 main
```

### 年报季手动刷新（GitHub Actions）

1. **刷新 A 股年报数据**
   - GitHub → Actions → "Refresh YJBB Annual Data" → Run workflow
   - 输入 `years`: `2025`（或 `2024 2025`）
   - 自动更新 `yjbb_annual.json` + `yjbb_quarterly.json` 并 commit

2. **刷新 MPWR / NVTS 季度数据**
   - GitHub → Actions → "Refresh EDGAR Quarterly (MPWR / NVTS)" → Run workflow
   - 输入 `tickers`: `MPWR NVTS`
   - 自动更新 `data.json` 并 commit

3. **部署到 Cloud Run**（在 Cloud Shell 执行）
   ```bash
   cd analog-pd-dashboard
   git pull origin main        # 拉取最新数据和代码
   gcloud run deploy analog-dashboard \
     --source . \
     --project st-china-ai-force \
     --region asia-east1 \
     --allow-unauthenticated \
     --port 8080
   ```

### 本地预览
```bash
python -m http.server 8000
# 访问 http://localhost:8000/dashboard.html
```

> **注意**：本地预览使用本地 JSON 文件；线上 Cloud Run 从 GCS bucket `st-china-ai-force-dashboard` 动态拉取最新数据。

---

## 📊 数据源说明

### A 股公司（19 家）— yjbb 优先
| 字段 | 来源 | 说明 |
|---|---|---|
| 年度营收、净利润率 | `AkShare stock_yjbb_em` | 按年报披露日期抓取；未披露则留空 |
| 季度营收、净利润 | `AkShare stock_yjbb_em`（累计报转单季） | 存入 `yjbb_quarterly.json`，用于趋势图Q分段 |
| 净利润 | 归母净利润（不含少数股东） | 列名：`净利润-净利润` |
| 公司档案 | 雪球 XQ `stock_individual_basic_info_xq` | 注册全称、实控人、企业属性、发行价等 |

### 非 A 股公司（3 家）— data.json 手工维护
| 公司 | 数据源 | 货币 |
|---|---|---|
| Silergy (6415) | WSJ / 公司年报 | M TW（÷29.59 → USD） |
| MPWR | EDGAR 10-K / 10-Q | M USD |
| NVTS | EDGAR 10-K / 10-Q | M USD |

> ⚠️ **MPWR 2024 净利润修正**：EDGAR XBRL 曾错误拉取 `GrossProfit` 标签，实际 `NetIncomeLoss = 499.5M`，已在 `data.json` 中修正，勿回退。

### FX 汇率（固定）
| 货币 | 换算 |
|---|---|
| M RMB | ÷ 7.2 |
| M TW / M TWD | ÷ 29.59 |
| M USD | ÷ 1.0 |

---

## 🏢 公司名单（22 家）

**Analog A 股（8）**：SG micro (300661)、3-Peak (688536)、Chipown (688508)、Fortior (688279)、Southchip (688484)、Joulwatt (688141)、Injoinic (688209)、Novosense (688052)

**Analog 非 A 股（3）**：Silergy (6415, TWD)、MPWR (USD)、NVTS (USD)

**P&D A 股（11）**：Silan (600460)、CR Micro (688396)、Yangjie (300373)、Sino-Micro (600360)、Star Power (603290)、NCE (605111)、JieJie Micro (300623)、Oriental (688261)、Macmicst (688711)、Sanan (600703)、UNT (688469)

---

## ⚠️ 关键注意事项

| 项目 | 说明 |
|---|---|
| **yjbb 单位** | 原始值为元（Yuan）÷1e6 = M RMB，÷7.2 = M USD |
| **Silergy 货币** | 存为 `"M TW"`（非 `"M TWD"`），`fxRate()` 用 `includes('TW')` 匹配，不要改 |
| **A 股判断** | code 前缀 3/6/688 → A 股 → yjbb；其余 → data.json |
| **2025 季度估算逻辑** | Q1+Q2+Q3已知 → Q4 est = Q3；H1已知 → Q3/Q4 est = H1÷2；仅Q1 → Q2/Q3/Q4 est = Q1 |
| **2025 排序** | 已披露 FY2025 实际值按营收降序排前；估算公司沉底，以 2024 营收作二次排序 |
| **deploy 前必须 git pull** | Cloud Shell 部署前务必先 `git pull origin main`，否则部署旧代码 |
| **GCS 三文件** | 每次更新数据后需同步推送 `data.json` + `yjbb_annual.json` + `profiles_xq.json` 至 GCS |
| **Export 文件名** | `AnalogPD_Revenue_YYYY-MM-DD.xlsx`，每年含 M RMB + M USD 双列，可直接对照年报核实 |
