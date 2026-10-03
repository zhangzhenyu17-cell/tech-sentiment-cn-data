# Investment Decision Driver Primitives V1

状态：**PUBLIC DATA ENGINEERING / OUTCOME-BLIND / MANUAL-ONLY**

本模块为私有 `Investment Decision Chain V1` 提供公开、可复核、PIT 的原始财报输入，不包含私有模型、阈值、组合、信号或交易语义。

## 1. 为什么新增独立 primitive

现有 Cross-Sector current Fundamental rail 已能统一提供 `OPERATING_REVENUE`、归母净利润、经营现金流和净利率；extended PIT rail 还能提供 CAPEX、货币资金、研发费用与若干债务项目。

但 Price-Implied Expectations / Economic Profit 等后续研究若直接把“净利润”当 EBIT/NOPAT，或把“所得税费用”当现金税，会引入明确的会计语义错误。因此本模块只新增三条**直接报表行项目**：

- `OPERATING_PROFIT_CN_GAAP`：营业利润；
- `TOTAL_PROFIT_CN_GAAP`：利润总额；
- `INCOME_TAX_EXPENSE_CN_GAAP`：所得税费用。

名称保留 `CN_GAAP`，用来阻止下游把这些原始行项目静默改名为 EBIT、NOPAT 或 cash tax。
## 2. 解析边界

解析器复用现有 official-filing extended PIT 的 statement-scoped 安全逻辑：

- 只读取官方财报 PDF；
- 只接受显式声明的人民币金额单位；
- 只在利润表范围内寻找目标行；
- 当前值/上期值列归属无法证明时保持缺失；
- 不从 MD&A、附注或其他表格回填利润表缺失；
- 不把 blank / dash 变成 0；
- 保留 document / revision / publication / availability / SHA provenance。

## 3. 冻结范围

范围文件：

`data/reference/investment_decision_chain_v1_driver_scope_2026-09-30.csv`

固定为 629 个 symbol：

- 192 个既有 STAR50 / ChiNext50 numeric PIT Fundamental baseline entities；
- 437 个已冻结 Cross-Sector current Fundamental 补充范围；
- 两部分 overlap = 0。

这不是新股票池，也不增加 Cross-Sector domain 或 benchmark。
## 4. 执行

Workflow：

`.github/workflows/investment-decision-driver-primitives-v1.yml`

仅 `workflow_dispatch`，支持：

- `pilot`：7 个代表 symbol，先验证真实 provider / PDF / parser；
- `full`：629 symbol，32 deterministic shards，`max-parallel=4`，checkpoint + circuit breaker + final aggregate。

>=200-item 的 full run 只有 pilot 通过后才运行。

## 5. 输出

每个 shard：

- `decision_driver_facts.csv`
- `coverage.csv`
- `errors.csv`
- `stage_manifest.json`

full aggregate：

- 上述三类合并表；
- `bundle_manifest.json`
- 冻结 contract；
- 冻结 scope。
公开 artifact 成功只表示原始公开数据捕获成功；不得据此宣称私有 Decision Chain 已产生 reverse-implied growth、ROIC/RNOA、WACC、expected return、target weight 或交易信号。

## 6. 权限边界

本模块不读取任何 forward outcome，不进行 full historical outcome research、OOS/holdout、参数/阈值/权重/feature/ML 搜索，不改变 evidence qualification、Production、Strategic Budget、仓位或 trading authority。
## 7. 2026-10-03 Full Tail Recovery

原 full run `37097668812` 的 32/32 shard job 均成功，但 final aggregate 在全局 fail-closed 检查中发现 10 条 `HARD_FAILURE`，因此正确拒绝生成 final bundle。

根因审计：

- 8 条来自 PDF embedded-text engine 的局部异常（6× `ZeroDivisionError`、1× `KeyError('/Contents')`、1× `IndexError`）；原 extractor 没有在单一 engine exception 后继续同一官方 PDF bytes 的后备 text engines。修复后，各 engine exception 只影响本 engine，仍严格要求 explicit unit，不启用 OCR。
- 2 条来自 2024 半年报的 CNINFO immutable static URL 与由同一 bulletin id/date 推导的 CNINFO 官方 download endpoint 均返回 404。这类 exact-source bilateral 404 被定义为 `SOFT_DATA_INSUFFICIENCY`；不搜索替代文档、不更换 provider、不补 0。

恢复遵循 GitHub v1.4 最小重跑：仅重算 shard `2, 3, 11, 14, 26, 31`，其余 26 个 shard 复用 Run `37097668812` 的 pinned 成功 artifact。recovery aggregate 必须显式记录每个 shard 的 `source_commit`，并继续要求全局 `hard_failure_rows = 0`。

该 recovery 不读取 outcome，不改变 evidence qualification、Production、Strategic Budget、target weight 或 trading authority。
