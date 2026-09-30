# Cross-Sector Current Fundamental Input V1

状态：**OUTCOME-BLIND CURRENT FACT MATERIALIZATION / MANUAL-ONLY / NO PAIRWISE AUTHORITY**

日期：2026-09-30

本产品用于补齐 Cross-Sector Relative Mispricing V0 四个既有 Domain 中，Technology 之外的当前 `FUNDAMENTAL_EXPECTATION_STATE` 数据基础。

它不新增 Fundamental 模型。底层继续使用：

- CNINFO versioned official filing materialization；
- `FUNDAMENTAL_PIT_STATE_CONTRACT_V1`；
- revenue / parent net profit / operating cash flow / net profit margin 四个既有核心事实；
- sign + exact-direction 分类，不做收益优化。

## 为什么采用 current-only

既有 V4-A artifact 对 2026-09-30 当前 benchmark 的合格覆盖只有：

- Innovation Drug 931152：13 / 50；
- Defense 399973：8 / 50；
- Core Beta A500：109 / 500。

完整扩展 2022–2026 历史会产生大量当前任务不需要的计算。当前任务只需要截至 2026-09-30 已公开的最新可比会计状态，因此固定：

- target start = 2026-01-01；
- end = 2026-09-30；
- filing query warmup = 1 year；
- 不做完整历史研究或 outcome study。

## 固定 scope

2026-09-30 通过 CSI official constituent XLS 捕获：

- 931152：50；
- 399973：50；
- 000510：500。

与 exact baseline Fundamental artifact 比较后：

- Priority（Innovation Drug + Defense）缺口：79 个唯一 symbol；
- A500 缺口：391，其中 33 与 Priority 重叠；
- A500 remaining：358；
- 总计只需新增 materialize 437 个唯一 symbol。

## 执行

新增 workflow `.github/workflows/cross-sector-current-fundamental-v1.yml` 被显式 allowlist，且只有 `workflow_dispatch`。

顺序：

1. preflight + exact calendar；
2. Priority 16 shards，provider-facing max parallel = 4；
3. Priority aggregate；
4. A500 48 shards，provider-facing max parallel = 4；
5. final aggregate。

Priority 先于 A500，保证最先打通 Technology ↔ Innovation Drug / Defense 的 matching fact axis。每个 shard 保存 engineering checkpoint；checkpoint 不是 evidence，也不授予 private qualification。

## 安全边界

workflow 不读取 forward outcome，不运行 parameter / threshold / weight / feature / ML search，不改变现有 evidence qualification、Production 或 trading authority。public workflow 成功也不等于 pairwise comparable；private 侧仍需合并 baseline + new evidence、按 80% coverage gate 聚合，并重新检查 same-date freshness。
