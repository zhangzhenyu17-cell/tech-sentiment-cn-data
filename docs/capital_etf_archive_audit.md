# 588000 公开 ETF 份额 archive 候选源审计

## 目的与边界

本能力只审计公开历史 archive 是否可作为后续人工资格复核的候选输入，不计算模型事件、收益、信号或持仓，也不改变任何 production / evidence 资格。

即使某个 archive 在交易日历上的覆盖率达到 80% 或更高，也不会因为本审计自动成为 canonical input。历史文件的存在只能证明“当前可取得这些历史记录”，不能单独证明这些记录在原交易日当时已经以同样内容可用，因此 point-in-time provenance 必须另行证明。

## 候选 archive：eyesofblue/trade-sse_etf_data

固定版本：`8759e44647832ecff4141f96f435036d19482174`

候选文件：`data/curated/sse_etf_shares.csv`

仓库文档明确给出的上交所来源链为：

- 来源机构：上海证券交易所；
- 接口：`https://query.sse.com.cn/commonQuery.do`；
- ETF 规模查询标识：`COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L`；
- 日期字段：`STAT_DATE`；
- 基金代码字段：`SEC_CODE`；
- 总份额字段：`TOT_VOL`；
- 原始单位：万份；
- archive 字段 `total_shares_10k` 对应上述万份口径。

该仓库同时明确记录 588000（科创50ETF华夏）为其跟踪标的之一。现有公开 archive 可作为候选历史轨，但不得把 archive 自身的后采集时间误写成原始交易日的 evidence-available time。

当前审计已确认该候选轨的已知历史从 2023-01-03 起可见，而 Capital Regime 当前完整样本从 2022-01-04 起。因此即使 2023 年以后覆盖良好，它也不能冒充完整的 2022–2026 历史输入；审计状态应保留 `CANDIDATE_ARCHIVE_PARTIAL_PREHISTORY`，直到更早历史来源被独立补齐并通过资格复核。

## 方法交叉证明：zxtcc/sse-etf-share

固定版本：`0f9795f23881bbb3199001247c9b2d80d3bb1fd4`

该项目的 README 和 `sse_client.py` 独立实现了相同的上交所 ETF 规模来源链：

- `https://query.sse.com.cn/commonQuery.do`；
- `sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L`；
- `STAT_DATE=YYYY-MM-DD`；
- 从 `TOT_VOL` 读取总份额（万份）；
- 按基金代码筛选目标 ETF。

正式物化以该按日期全市场查询为首选，并允许在同一官方来源内使用 `commonSoaQuery.do` / JSONP 协议变体。若这些 interface-family transport 全部失败，还可使用同一 `query.sse.com.cn/commonQuery.do` 的精确查询：

- `sqlId=COMMON_SSE_ZQPZ_ETFZL_ETFJBXX_JJGM_SEARCH_L`；
- `SEC_CODE=<目标 ETF>`；
- `STAT_DATE=YYYY-MM-DD`；
- 仍只接受 `TOT_VOL`（万份）；
- 必须恰好匹配目标代码和目标日期，禁止用“最近可用日”代替。

这些路径只改变官方接口的 transport/query shape，不产生新的 source identity，也不改变 point-in-time、coverage 或 canonical qualification 规则。

但当前 Git 版本没有可直接版本固定并复核的 588000 历史数据文件。因此它只登记为 `METHOD_REFERENCE_ONLY`，用于交叉证明采集方法和字段语义，不作为历史 archive 数据源。

## 审计实现

`src/tech_sentiment/capital_etf_archive_audit.py`：

- 只接受已登记的 archive 数据源；
- 将 `total_shares_10k` 以十进制定点语义转换为实际份数；
- 对重复日期、非法/非正份额失败关闭；
- 保留固定 source commit、provider interface、SQL id、字段和 archive fetch time；
- 将 point-in-time 状态固定为 `NOT_ESTABLISHED_BY_ARCHIVE`；
- 复用 trailing coverage gate，但任何覆盖结果都不会自动授予 canonical/evidence 资格。

`scripts/audit_capital_etf_archive.py` 只读取本地 archive CSV 与本地交易日历 CSV，不访问网络、不新增 workflow、不运行研究结果计算。输出：

- `candidate_etf_shares_normalized.csv`；
- `candidate_etf_share_coverage.csv`；
- `candidate_etf_archive_audit.json`。

## 状态语义

- `CANDIDATE_ARCHIVE_EMPTY`：目标 ETF 没有记录；
- `CANDIDATE_ARCHIVE_PARTIAL_PREHISTORY`：目标交易日历开始早于 archive 首条记录；
- `CANDIDATE_ARCHIVE_INSUFFICIENT_COVERAGE`：完整起点存在，但总体覆盖不足；
- `CANDIDATE_ARCHIVE_AUDITED_NOT_CANONICAL`：结构与覆盖可审计，但仍没有自动获得 point-in-time / canonical 资格。

所有状态下均保持：

- `point_in_time_qualified=false`；
- `canonical_input_qualified=false`；
- `production_or_model_output=false`。
