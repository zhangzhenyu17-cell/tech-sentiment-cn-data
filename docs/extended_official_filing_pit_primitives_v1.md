# Extended Official Filing PIT Primitives V1

状态：**PUBLIC DATA ENGINEERING / OUTCOME BLIND / NOT EVIDENCE QUALIFICATION**

本层只从已经下载并通过官方来源身份约束的财报 PDF 文本层中提取直接、可核对的原始科目。它不包含私有模型、阈值、信号、持仓或研究结果，也不产生任何 Production / trading authority。

当前 parser：`official-filing-extended-pit-primitives-v9-subtotal-reconciled-zero-safe`。v9 保留 v8 的全部 direct-statement 语义：standalone `-` 仍只作为列占位符，blank / dash 本身永远不会被转换为 0。v9 额外增加一条独立、窄化的 `SUBTOTAL_RECONCILED_ZERO` 证据路线：仅在**合并资产负债表**、显式单位为**元/CNY**、无附注列歧义、标准化“非流动负债”component set 完整、全部显式 component 非负，且“非流动负债合计”与显式 component 之和的 current-period 残差在半分以内严格为 0 时，才允许把该封闭 non-negative liability scope 内的空白 debt component（仅 `LONG_TERM_BORROWINGS` / `BONDS_PAYABLE` / `LEASE_LIABILITIES`）证明为数值 0。任何缺行、非零残差、单位为万元/千元、负值、单列歧义或 scope 不完整均 fail closed。该路径是会计恒等式证明，不是 blank/dash→0 imputation。

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

## V9 subtotal-reconciled-zero 边界

该路线的设计目标是恢复“官方报表已经通过小计恒等式证明为 0、但目标行没有直接数字”的极少数 debt fact。它不扩展到利润表、现金流量表、流动负债，也不对任意 note table 做 residual 推断。

当前工程只提供 parser + regression tests；**不自动运行历史全量 materialization，不自动改变任何私有 Fundamental evidence qualification，也不自动改变 ESS / sample gate**。若后续需要把 V9 产物纳入私有 Hardened V2 coverage，必须在独立、显式授权下验证 exact document identity、PIT/provenance、support-set identity 与 private qualifier 后再重算。

## 明确不做的语义合成

本层**不生成** `CASH` 或 `DEBT` 聚合字段。尤其债务保持为原始组成项，不在公开数据仓中自行决定哪些科目应计入模型意义上的“债务”。

`share_count` 也暂不在本版本中提取，因为其单位是股/万股等非金额单位，需要独立的显式 share-unit parser 后才能安全标准化。

## 独立 materializer

工程入口已经提供：

`src/tech_sentiment/extended_filing_materialization.py`

手动 CLI：

`scripts/materialize_fundamental_extended_pit.py`

该 materializer：

- 复用 CNINFO exact announcement/document identity；
- 使用 immutable checkpoint；
- 支持跨 runtime commit 的 progress checkpoint identity；
- 保留 document URL / SHA256 / publication / available timestamp；
- 缺失 extended line item 记为 `SOFT_DATA_INSUFFICIENCY`；
- immutable attachment / provenance 破坏记为 hard failure；
- 输出 raw facts / coverage / errors / summary；
- 明确记录 `outcome_read=false` 与 `evidence_qualification_changed=false`。

该 materializer 已可由既有 **manual-only** historical coverage workflow 在冻结公开 scope 上运行；公开 materialization / coverage 成功只产生 public raw-data artifact，不会自动授予任何私有 evidence qualification、模型权限或交易权限。后续 parser 版本升级如需反映到历史 artifact，必须通过单独的 manual materialization 运行并保留新的 parser/provenance identity。

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

本次工程：

- 当前提交本身不运行历史全量 materialization；materializer 仅提供未来授权后的可复用工程入口；
- 不改变已有 V4-A qualified facts；
- 不修改现有 canonical filing parser 的版本或既有 checkpoint identity；
- 不读取 forward / historical outcome；
- 不执行 validation / holdout / OOS / parameter search；
- 不改变 evidence qualification、Production 或 trading authority。
