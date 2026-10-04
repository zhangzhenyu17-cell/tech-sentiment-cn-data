# Investment Decision Nonrecurring Disclosure V1

**状态：PUBLIC RAW PIT ENGINEERING / WORKFLOW_DISPATCH ONLY / NO PRIVATE CLASSIFICATION**

本 rail 只在冻结 `629` scope 内，从官方 CNINFO 财务报告的“非经常性损益项目和金额”监管披露表提取直接行项目。它是 Accounting Reformulation V1 的 `EXPLICIT_DISCLOSURE_ONLY` 数据输入，不是 private core/unusual 模型。

输出保留：entity / period / fact type / reported amount / source row label / disclosure role / evidence-available date / publication timestamp / document id / revision / URL / SHA256 / parser version。25 个 fact identities 对应公开披露表中的 22 类标准项目以及所得税影响、少数股东权益影响和披露合计。

严格禁止：
- 把披露表的 after-tax `合计` 直接当作 pretax unusual operating item；
- 判断某披露项目是否已经包含在 `OPERATING_PROFIT_CN_GAAP`；
- 在 public repo 生成 core operating income / NOPAT / NOA / ROIC / RNOA；
- 把空白金额补 0；
- 读取 forward outcome 或改变 Evidence / Production / trading authority。

若 parser 没覆盖某个发行人明确披露的罕见项目，private reconciliation 应 fail closed；后续只能机械增加该公开 raw row identity，不得用残差猜测。
