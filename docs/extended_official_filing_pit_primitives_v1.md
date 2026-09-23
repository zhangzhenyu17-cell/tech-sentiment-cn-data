# Extended Official Filing PIT Primitives V1

状态：**PUBLIC DATA ENGINEERING / OUTCOME BLIND / NOT EVIDENCE QUALIFICATION**

本层只从已经下载并通过官方来源身份约束的财报 PDF 文本层中提取直接、可核对的原始科目。它不包含私有模型、阈值、信号、持仓或研究结果，也不产生任何 Production / trading authority。

当前 parser：`official-filing-extended-pit-primitives-v2-column-safe`。

## 当前 raw primitives

- `MONETARY_FUNDS`：货币资金；
- `CASH_AND_CASH_EQUIVALENTS_END`：期末现金及现金等价物余额；
- `CAPEX_CASH_PAID`：购建固定资产、无形资产和其他长期资产支付的现金；
- `R_AND_D_EXPENSE`：研发费用；
- `SHORT_TERM_BORROWINGS`：短期借款；
- `CURRENT_PORTION_NON_CURRENT_LIABILITIES`：一年内到期的非流动负债；
- `LONG_TERM_BORROWINGS`：长期借款；
- `BONDS_PAYABLE`：应付债券；
- `LEASE_LIABILITIES`：租赁负债。

所有金额都必须来自局部可证明的显式人民币金额单位，并确定性归一到 CNY。没有显式单位时 fail closed；缺失科目保持缺失。

## 金额列语义加固

历史全量 materialization 暴露出一类真实的 PDF 表格语义风险：中国财务报表常在科目名称与当期金额之间插入 `附注` 列。如果只读取科目名称后的第一个数字，可能把附注号、年份或其他元数据误当作金额。

`v2-column-safe` 因此增加以下保守约束：

- 仅在科目 label 能在首个 numeric cell 之前完整重建时读取；
- 仍要求局部显式人民币金额单位；
- 未出现 `附注` 表头时，只接受至多两个可证明的金额 cell；
- 出现 `附注` 表头时，仅在行内同时存在明确的紧凑附注编号和两个金额 cell 时跳过附注编号并读取当期金额；
- 空白附注、数字打断 label、额外 numeric cells 或其他无法证明列位置的布局全部保持缺失；
- 不使用金额大小阈值，不根据同行/跨期数值猜测，也不做缺失补零。

这项加固只改变 extended raw parser。既有 V4-A qualified canonical filing parser 不修改、不重新定义。

parser version 参与 immutable document checkpoint identity。因此从 v1 升级为 `v2-column-safe` 会自然失效旧的 parsed-document facts，同时仍允许 materializer 复用既有 symbol query / filing-index checkpoint；不会出现“新 parser + 旧 facts”的混用。

## 明确不做的语义合成

本层**不生成** `CASH` 或 `DEBT` 聚合字段。尤其债务保持为原始组成项，不在公开数据仓中自行决定哪些科目应计入模型意义上的“债务”。

`share_count` 也暂不在本版本中提取，因为其单位是股/万股等非金额单位，需要独立的显式 share-unit parser 后才能安全标准化。

## 独立 materializer

工程入口：

`src/tech_sentiment/extended_filing_materialization.py`

手动 CLI：

`scripts/materialize_fundamental_extended_pit.py`

该 materializer：

- 复用 CNINFO exact announcement/document identity；
- 使用 immutable checkpoint；
- 支持跨 runtime commit 的 progress checkpoint identity；
- 保留 document URL / SHA256 / publication / available date；
- 缺失 extended line item 记为 `SOFT_DATA_INSUFFICIENCY`；
- immutable attachment / provenance 破坏记为 hard failure；
- 输出 raw facts / coverage / errors / summary；
- 明确记录 `outcome_read=false` 与 `evidence_qualification_changed=false`。

## PIT / provenance

输出行沿用现有 filing fact provenance columns，包括：

- entity / period；
- evidence available date；
- official publication timestamp；
- source identity / provider；
- document id / revision id；
- immutable document URL / SHA256；
- parser version。

这仍不足以自动满足任何私有 Fundamental coverage gate。source-native revision sequence、完整覆盖、字段到私有架构的适配以及正式 qualification 都必须在各自授权边界内另行完成。

## 边界

本层：

- 不改变已有 V4-A qualified facts；
- 不修改现有 canonical filing parser 的版本或既有 checkpoint identity；
- 不读取 forward / historical outcome；
- 不执行 validation / holdout / OOS / parameter search；
- 不改变 evidence qualification、Production 或 trading authority；
- semantic hardening 的目标仅是宁可缺失，也不把无法证明列位置的 numeric token 当作财务金额。
