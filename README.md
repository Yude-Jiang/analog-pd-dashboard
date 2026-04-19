# AnalogPD — Competitors Analysis Dashboard

A competitive intelligence platform tracking **22 semiconductor companies** (11 Analog + 11 P&D/Discrete) across China A-share, Taiwan, and US markets.

**线上地址：** https://analog-dashboard-460989091461.asia-east1.run.app

---

## 🆕 v3.0 更新说明 (2026-04-10)

### 新增：筛选查找功能
趋势分析页顶部新增 Filter Bar，支持：

| 控件 | 功能 |
|---|---|
| 🔍 文本框 | 按公司名或股票代码实时搜索（输入即响应） |
| Sector | All / Analog / P&D 分类筛选 |
| Market | All / A股(RMB) / Taiwan(TWD) / US(USD) 市场筛选 |
| YoY | All / >+20% / 0~+20% / <0% 增速筛选 |
| ↺ Reset | 一键清空所有筛选条件 |

筛选结果同步作用于**趋势图**和**Profile 表格**。

### 本次修复
- GCS bucket 数据文件对齐（`data.json` / `yjbb_annual.json` / `profiles_xq.json` 同步至最新版本）
- Cloud Run 新增 `/yjbb_annual.json` 和 `/profiles_xq.json` 独立路由，从 GCS 动态拉取

---

## 📋 v2.0 功能说明 (2026-04-09)

> **核心变更：A 股数据源全面切换至 AkShare yjbb，放弃 Excel 手动维护路径。**

### 数据源重构
- **A 股（19家）** 营收/净利润率来自 `AkShare stock_yjbb_em`，存入 `yjbb_annual.json`，yjbb 优先级高于 `data.json`
- **2025 年报未披露 = 图表留空**，不填充估算值
- **非 A 股**（Silergy / MPWR / NVTS）继续使用 `data.json` 手工维护

### 统计口径透明化
- 统计栏改为 **"N 家样本公司营收合计"**，明确非行业 TAM
- 未全部披露时显示 `~X.XXB USD (部分披露)`，隐藏 YoY/CAGR 避免误导
- 末尾实时显示 **"2025年报 N/M 已披露"**

### 完整功能列表

| 功能 | 说明 |
|---|---|
| **筛选查找** | 文本搜索 + Sector / Market / YoY 三级下拉，图表与表格同步响应 |
| **公司 Profile 弹窗** | 有档案的公司名前显示 🔍，悬停弹出详情卡（注册全称、实控人、董事长、企业属性、发行价、主营业务等） |
| **Profile 表格排序** | 点击任意列标题升降序，缺失值自动置底 |
| **Export Excel** | 右上角 ⬇ 导出，文件名 `AnalogPD_Revenue_YYYY-MM-DD.xlsx`，每年含 Rev(M RMB) / Rev(M USD) / NI(M RMB) / NI(M USD) / NI% / 披露日期 6 列，可与年报直接核对 |
| **中英文切换** | EN/中 按钮，覆盖全部静态文字（标题、表头、统计栏、弹窗字段、来源说明） |
| **数据质量验证** | `validate_data.py` 13 条规则，退出码 0/1/2/3 对应 INFO/WARN/ERROR/CRITICAL |
| **智能门控同步** | `smart_sync.py` 每日 09:00 检查披露日历，有新年报/季报才触发同步 |
| **品牌** | `SemiIntel` → `AnalogPD` |
| **2026 追踪 Tab** | 已隐藏，界面默认仅展示 19-26 趋势分析 |

---

## 📁 文件结构

| 文件 | 说明 |
|---|---|
| `dashboard.html` | 主界面：筛选栏、趋势图、Profile 表格、弹窗、排序、导出 |
| `data.json` | 中央数据库：22 家公司历史营收、净利润、profile、audit |
| `yjbb_annual.json` | AkShare yjbb 年报：19 家 A 股 2019–2025 营收与净利润率 |
| `profiles_xq.json` | 雪球 XQ 档案：19 家 A 股注册全称、实控人、发行价等 |
| `Semi_Maker_Revenue_202603.xlsx` | 主 Excel（Silergy / MPWR / NVTS 手工维护） |
| `app.py` | Cloud Run Flask 服务：静态文件 + GCS JSON 路由 + /refresh 端点 |
| `Dockerfile` | 容器构建配置 |
| `requirements_cloudrun.txt` | Cloud Run Python 依赖 |
| `sync_data.py` | Excel → `data.json` 同步 |
| `fetch_yjbb_annual.py` | AkShare yjbb 年报 → `yjbb_annual.json` |
| `fetch_profiles.py` | 雪球 XQ 档案 → `profiles_xq.json` |
| `fetch_semi_data.py` | 季报数据（A 股 AkShare / 台湾 MOPS / 美股 EDGAR） |
| `validate_data.py` | 13 规则数据质量验证 |
| `smart_sync.py` | 披露日历门控自动同步（每日 09:00 工作日） |

---

## 🚀 标准工作流

### 日常（自动）
```
smart_sync.py 每日 09:00 自动运行：
  ↓ 检查 AkShare 披露日历
  ↓ 若有目标公司年报 → 提示更新 Excel + 运行 sync_data.py
  ↓ 若有目标公司季报 → 自动抓取 + sync + validate
```

### 年报季手动刷新
```bash
# 1. 刷新 yjbb 年报数据（A 股 19 家）
python fetch_yjbb_annual.py --years 2025

# 2. 更新 Excel（Silergy / MPWR / NVTS 手工填入），同步到 data.json
python sync_data.py

# 3. 刷新公司档案（每年更新一次即可）
python fetch_profiles.py

# 4. 数据质量验证
python validate_data.py

# 5. 本地预览
python -m http.server 8000
```

### 推送 Google Cloud Run
```bash
# 构建并推送镜像
IMAGE="asia-east1-docker.pkg.dev/st-china-ai-force/cloud-run-source-deploy/analog-dashboard:vN"
gcloud builds submit --tag "$IMAGE" --region asia-east1 --project st-china-ai-force .

# 部署
gcloud run deploy analog-dashboard \
  --image "$IMAGE" \
  --region asia-east1 --project st-china-ai-force \
  --set-env-vars GCS_BUCKET=st-china-ai-force-dashboard,REFRESH_SECRET=<secret> \
  --allow-unauthenticated --memory 1Gi --cpu 1 --timeout 300

# 更新 GCS 数据文件
gcloud storage cp data.json yjbb_annual.json profiles_xq.json gs://st-china-ai-force-dashboard/
```

---

## 📊 数据源说明

### A 股公司（19 家）— yjbb 优先
| 字段 | 来源 | 说明 |
|---|---|---|
| 营收、净利润率 | `AkShare stock_yjbb_em` | 按年报披露日期抓取；2025 年报未出则留空 |
| 净利润 | 归母净利润（不含少数股东） | 列名：`净利润-净利润` |
| 公司档案 | 雪球 XQ `stock_individual_basic_info_xq` | 注册全称、实控人、企业属性、发行价等 |

### 非 A 股公司（3 家）— data.json 手工维护
| 公司 | 数据源 | 货币 |
|---|---|---|
| Silergy (6415) | WSJ / 公司年报 | M TW（÷29.59 → USD） |
| MPWR | EDGAR 10-K | M USD |
| NVTS | EDGAR 10-K | M USD |

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
| **2025 年报进度** | Analog 9/11 已披露（Injoinic、Southchip 待披露）；P&D 2/11 已披露（Yangjie、NCE） |
| **GCS 三文件** | 每次更新数据后需同步推送 `data.json` + `yjbb_annual.json` + `profiles_xq.json` 至 GCS |
| **Export 文件名** | `AnalogPD_Revenue_YYYY-MM-DD.xlsx`，每年含 M RMB + M USD 双列，可直接对照年报核实 |
