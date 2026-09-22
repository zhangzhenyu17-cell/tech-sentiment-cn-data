# Extended Official Filing PIT Primitives V1

状态：**PUBLIC DATA ENGINEERING / OUTCOME BLIND / NOT EVIDENCE QUALIFICATION**

本层只从已经下载并通过官方来源身份约束的财报 PDF 文本层中提取直接、可核对的原始科目。它不包含私有模型、阈值、信号、持仓或研究结果，也不产生任何 Production / trading authority。

当前 parser：`official-filing-extended-pit-primitives-v1`。

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

**当前只完成工程接线，尚未运行 frozen 192-entity 历史 materialization。** 真正执行该历史 coverage 计算属于后续单独授权事项。

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
