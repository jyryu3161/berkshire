# 财务数据获取与交叉验证规范

> **[출력 언어] 최종 리포트는 시장과 무관하게 무조건 한국어로 작성한다. (원문 데이터·소스 인용은 원어 병기 가능)**

本规范适用于所有涉及企业财务数据的研究。**每个关键数据必须来自两个独立来源，误差>1%须标记。**

---

## 数据源优先级

### 美股（PDD、腾讯ADR、网易ADR等）

| 优先级 | 来源 | URL | 获取方式 |
|--------|------|-----|---------|
| 1（主） | **macrotrends** | macrotrends.net/stocks/charts/{ticker} | 直接访问，无需注册 |
| 2（副） | **stockanalysis** | stockanalysis.com/stocks/{ticker}/financials | 直接访问，无需注册 |
| 原始一手 | SEC EDGAR | sec.gov/cgi-bin/browse-edgar | 10-K / 10-Q 原文 |

### 港股（腾讯0700、网易9999、美团3690等）

| 优先级 | 来源 | URL | 获取方式 |
|--------|------|-----|---------|
| 1（主） | **aastocks** | aastocks.com/tc/stocks/analysis/company-fundamental | 直接访问 |
| 2（副） | **macrotrends**（ADR代码） | 腾讯用TCEHY，网易用NTES | 直接访问 |
| 原始一手 | HKEX披露易 | hkexnews.hk | 年报PDF |

### A股（三七互娱、吉比特等）

| 优先级 | 来源 | URL | 获取方式 |
|--------|------|-----|---------|
| 1（主） | **东方财富** | eastmoney.com → 搜股票代码 → 财务报表 | 直接访问 |
| 2（副） | **巨潮资讯** | cninfo.com.cn | 原始年报/季报PDF |

### 한국 (코스피/코스닥 — 삼성전자 005930, 에코프로비엠 247540 등)

| 우선순위 | 소스 | 도구/URL | 취득 방식 |
|--------|------|-----|---------|
| 1（주-시세/밸류에이션） | **네이버 금융** | `python3 tools/krx_data.py {quote\|valuation\|financials\|search} {코드}` | 무키, 즉시 |
| 2（주-공시/재무 원천） | **DART(전자공시)** | `python3 tools/dart_data.py {corpcode\|company\|financials\|disclosures} ...` | 무료 인증키 필요 |
| 원천 1차자료 | **DART 원문** | dart.fss.or.kr (사업보고서/반기/분기) | 공시 원문 PDF/뷰어 |

> **한국 종목 필수 절차**:
> 1. `krx_data.py search {회사명}` 으로 6자리 종목코드 확인
> 2. `krx_data.py quote/valuation/financials` 로 시세·PER/PBR·연간 재무 수집(네이버, 무키)
> 3. `dart_data.py corpcode {종목코드}` → DART `corp_code` 획득
> 4. `dart_data.py financials {corp_code} {연도}` 로 **DART 원천 재무제표(연결 CFS)** 교차검증
> 5. `dart_data.py disclosures {corp_code}` 로 최근 공시(사업보고서·주요사항·지분변동) 확인 후 보고서에 반영
>
> DART 인증키 발급(무료, 즉시): https://opendart.fss.or.kr → 인증키 신청/관리.
> 설정: `export DART_API_KEY=키` 또는 `echo 키 > ~/.dart_api_key`.
> 재무 데이터는 **네이버(가공) + DART(원천)** 2개 독립 소스로 교차검증하며, 오차>1%는 표기한다.

---

## 执行规范

### 第一步：获取数据

对每个财务指标（收入、净利润、毛利率、经营现金流、资产负债率等），分别从**来源1**和**来源2**取数。

### 第二步：误差计算与标记

```
误差率 = |来源1数值 - 来源2数值| / 来源1数值 × 100%
```

| 误差 | 处理方式 |
|------|---------|
| ≤ 1% | ✅ 一致，取来源1数值，标注两个来源 |
| 1% ~ 5% | ⚠️ 标记"数据存在差异"，注明两个数值，说明可能原因（汇率/会计口径） |
| > 5% | ❌ 标记"数据存在重大差异"，必须查原始财报核实，不得直接使用 |

### 第三步：数据呈现格式

每个关键数据必须按以下格式标注：

```
收入：1,239亿元 ✅
  - macrotrends: 1,241亿元
  - stockanalysis: 1,237亿元
  - 误差: 0.3%
```

差异示例：
```
净利润：245亿元 ⚠️ 数据存在差异
  - macrotrends: 245亿元（GAAP）
  - stockanalysis: 278亿元（Non-GAAP）
  - 误差: 13.5% — 原因：会计口径不同（GAAP vs Non-GAAP）
```

---

## 常见差异原因（不一定是数据错误）

| 原因 | 说明 |
|------|------|
| GAAP vs Non-GAAP | 最常见，尤其是利润类数据 |
| 汇率换算 | 港币/人民币/美元换算时间点不同 |
| 财年定义 | 自然年 vs 财年（如苹果财年10月结束） |
| 合并口径 | 是否含少数股东权益 |
| 数据更新滞后 | 某平台尚未更新最新一期财报 |

---

## 特别规则

1. **未上市公司**（米哈游、莉莉丝等）：只有一手数据来源时，数据前标记 `[估计]`，不执行交叉验证
2. **季度数据 vs 年度数据**：优先使用年度数据做交叉验证，季度数据部分来源可能有滞后
3. **原始财报优先**：若两个来源均与原始财报（10-K/年报PDF）不符，以原始财报为准，标记来源错误

---

## 快速索引

| 场景 | 主要来源 | 备用来源 |
|------|---------|---------|
| PDD / 拼多多 | macrotrends.net/stocks/charts/PDD | stockanalysis.com/stocks/pdd |
| 腾讯 | macrotrends.net/stocks/charts/TCEHY | aastocks（0700.HK） |
| 网易 | macrotrends.net/stocks/charts/NTES | aastocks（9999.HK） |
| 三七互娱 | eastmoney.com（002555） | cninfo.com.cn |
| 吉比特 | eastmoney.com（603444） | cninfo.com.cn |
| Nintendo | macrotrends.net/stocks/charts/NTDOY | stockanalysis.com/stocks/ntdoy |
| Capcom | macrotrends（CCOEY） | stockanalysis（CCOEY） |
