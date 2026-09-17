# Capital Market Inputs + PIT External Evidence Materialization V1

状态：`MANUAL_MATERIALIZATION_READY / QUALIFICATION_FAIL_CLOSED`

本阶段只负责公开输入获取、PIT 证据物化、provenance/replay 所需身份和资格输出；不包含模型、阈值、信号、持仓、forward outcome、holdout outcome 或私有研究结果。

## 1. 588000 ETF share rail

- 目标起点固定为 `2022-01-04`。
- 保留真实交易日历、no interpolation、no forward fill、trailing-60 `>=80%` gate。
- 现有 candidate archive 从 2023-01-03 起，且无法单独证明原交易日 point-in-time provenance，因此仍不能作为 canonical input。
- `qualify-capital-inputs` manual workflow 会继续逐目标交易日查询上交所 ETF 份额接口；只有实际 artifact 通过完整 coverage/provenance 审计后才可能进入 private qualification。

## 2. SSE + SZSE A-share turnover

- SSE：主板 A + 科创板，来源金额亿元转 CNY。
- SZSE：股票成交额减 B 股成交额，来源金额 CNY。
- 双边任一目标交易日缺失时不得静默删除；qualification artifact 保留 error dates。
- scope 固定称为 `SSE_SZSE_A_SHARES`，不冒充含北交所的严格全 A。
- old CSI800 proxy vs candidate comparison 只能在 private 侧确认历史完整资格后运行；70/30、252 lookback、60 min-history 不在 public materializer 中调整。

## 3. Financing

`financing_materialization.py` 与 manual CLI 保持：

- SSE official range query，raw unit 固定 `CNY`；
- SZSE official per-trading-day query，raw unit 固定 `CNY_100M`；
- canonical unit 为 `CNY`；
- 每个目标交易日都保留在 aligned raw rail；
- bilateral missing 明确记录；
- 不从异常数值反推倍率；
- 即使通过单位/量级资格，role 仍固定 `RESEARCH_INPUT`，不进入 Capital Regime composite。

## 4. PIT issuer disclosure materialization

CNINFO official-designated disclosure materializer 保存字段包括：

- `evidence_id`
- `entity_id`
- `evidence_type`
- `event_date`
- `evidence_available_date`
- `source_identity`
- `provider`
- `document_id`
- `revision_id`
- `provenance`
- `ingestion_identity`
- `availability_state`

公开 adapter 只对公告标题做文档类型 taxonomy，不做市场方向判断，不使用价格、未来收益或模型结果反推原因。

CNINFO 公告层的 `event_date` 保持 publication-level event date；若以后解析报告期、政策生效日或临床事件真实发生日，应作为独立字段/版本增加，不得重写既有记录。

### Close-based `evidence_available_date`

PIT materialization 必须消费与 Capital qualification 同一轮产生的**真实交易日历**，不能用自然日推断。

- 若上游 `公告时间` 含可证明的精确时刻，且发布时间位于真实交易日 **15:00 或之前**，该交易日可作为 `evidence_available_date`；
- 若公告在收盘后发布，则推迟到下一真实交易日；
- 若上游只提供日期、无法证明盘中具体时刻，则保守推迟到下一真实交易日；
- 若公告发生在非交易日，则推迟到下一真实交易日；
- 若提供的交易日历没有下一交易日，则 fail closed，不得把信息回填进公告日期。

因此历史 replay 的可知性由 `evidence_available_date` 决定，而不是简单把公告日期等同于研究可用日期。

## 5. Source coverage 与 major-negative exclusion

`query_status=COMPLETE_WINDOW` 只表示指定来源在指定 entity/window 的查询成功，不等于“没有重大负面事件”。

`major_negative_event_exclusion_complete` 只有在 private 侧确认所有预注册主要负面来源覆盖完整，并完成相应事件审查后才可为 true。

当前未 materialize 的来源（例如独立 SSE/SZSE announcement archive、official policy/regulatory archive 的完整历史 rail）不得因 CNINFO 成功而被推断为已覆盖。

## 6. Append-only / replay / revision semantics

- CNINFO `announcementId` 固定为 document identity；
- 每次 capture 生成 ingestion/materialization identity；
- later correction/replacement 必须保留为新 document/revision，不能覆盖 earlier record；
- 重复追加完全相同的 `evidence_id` 是幂等 replay；
- 同一 `evidence_id` 的历史字段发生变化必须 fail closed；
- as-of replay 只能读取 `evidence_available_date <= market_date` 的记录；
- prefix replay equality 用于证明后来追加的证据不能改变较早 market-date 的可见前缀；
- ordinary web search 只能帮助定位 canonical source，不是 canonical evidence。

## 7. Workflow

现有 `.github/workflows/qualify-capital-inputs.yml` 仍保持：

`workflow_dispatch` only

它现在可以一次生成：

1. ETF-share / turnover qualification artifact；
2. financing materialization artifact；
3. 可选 CNINFO PIT evidence artifact（需要显式传入 `pit_symbols`）；
4. immutable materialization manifest + readiness matrix。

CNINFO PIT step 显式读取 `output/capital_input_qualification/trading_calendar.csv`，以保证 market-date alignment 与同一轮 Capital 输入完全一致。

没有新增 schedule、workflow_run、push 或 pull_request 数据触发。
