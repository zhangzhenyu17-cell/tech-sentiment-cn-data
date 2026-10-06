# Investment Decision Accounting Balance-Sheet Layout Proof V1

本轨道只对官方财报文本中的**资产负债表列布局与原始单元格归属**进行 outcome-blind 取证。它不执行 operating / financing 分类，不把空白或 `-` 解释为 0，也不改变任何 evidence、Production、portfolio 或 trading authority。

## 目的

既有 row-evidence 轨道在无法从 token 数量直接证明“附注 / 期末 / 期初”列归属时会保留 `ROW_PRESENT_LAYOUT_AMBIGUOUS`。本轨道在同一官方 CNINFO 文档、同一 `document_sha256`、同一原始行哈希上增加更严格的布局证据：

- 财报表头必须明确声明当前期与比较期金额列；
- 若使用字符位置进行列归属，金额列 anchor 必须来自同一表头行；
- 表头声明附注列时，若附注列 anchor 不在同一 anchor 行，禁止用位置法把紧凑附注编号误认成金额；
- `BLANK` 与 `DASH` 始终保留为非数值状态；
- 只有列归属被证明且 current cell 为 `NUMERIC` 时才输出 `current_value_cny`；
- 不读取历史或前瞻收益结果。

## 私有请求边界

捕获脚本接受外部 request CSV，但**不解释 request 的私有语义**，只使用实体、期间、fact、官方文档身份和原始行身份来做源证据核验。request 文件及其派生资格结果不得提交到公开仓；公开仓只保存通用 parser / capture 工程能力。

## Provenance

每次捕获必须显式传入 40 位 `--source-commit`，manifest 固定 parser version、source commit、request SHA256、源行/单位一致性、错误计数以及全部 authority=false 防线。正式私有资格化必须重新核对这些 provenance 后才能消费。
