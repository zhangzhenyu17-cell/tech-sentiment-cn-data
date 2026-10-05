# Investment Decision Accounting Balance Sheet Row Evidence V1

本层只为已经冻结的 629-entity Decision Chain balance-sheet primitive scope 补充公开来源的**行级与单元格证据**。它不创建新的会计分类方法，不把缺失、空白或 `-` 解释为 0，也不在公开仓写入 operating / financing、NOA、Invested Capital、ROIC / RNOA 等私有语义。

输出保留 issuer、period、fact type、source row label、statement unit、note reference、current/prior cell kind 与 token、document identity / SHA-256、PIT available date 及 row SHA-256。只有当前单元格能够被同一合并资产负债表的列结构证明为 numeric 时，才保留 `current_value_cny`；`DASH` 仅作为原始 source token 保存，`current_value_cny` 继续为空。

执行采用新的 manual-only workflow `investment-decision-accounting-balance-sheet-row-evidence-v1.yml`。它复用原 balance-sheet capture 的 query-index checkpoint（固定 legacy source commit `7afa41d06eeb6443f11fa1482ec58364224bbbdd`），但因为 parser semantics 已变化，禁止复用旧 parsed-document facts。pilot 通过后才允许 full 32-shard capture。

Public workflow success 只代表 raw artifact engineering success。任何 explicit-zero proof qualification、review-item operating / financing split、private classification receipt、Evidence、Production、Strategic Budget、portfolio action 或 trading authority 都必须在私有仓按独立 contract 处理，本层无权授予。
