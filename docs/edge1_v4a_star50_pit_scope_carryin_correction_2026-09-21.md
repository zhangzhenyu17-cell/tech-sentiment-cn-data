# EDGE-1 / V4-A STAR50 PIT scope carry-in correction — 2026-09-21

状态：`ENGINEERING_CORRECTION / PUBLIC_DATA_ONLY / OUTCOME_BLIND / NO_EVIDENCE_PROMOTION`

## 问题

V4-A Capital/PIT symbol scope V1 使用：

- 2026-09-14 STAR50 anchor；
- 2022–2026 STAR50 adjustments。

这套输入会漏掉一个在 2022-01-04 已经属于 frozen STAR50、但在 2026-09-14 才退出的历史成分：

`688065.SH`

它于 2021-06-15 进入 STAR50，因此既不在 2022+ adjustment 的 `in_symbol` 中，也不在退出后的 2026-09-14 anchor 中。

结果是 V4-A V1 的 PIT scope 为 192 symbols，且 exact frozen STAR50 event-date major-negative completeness 会被该缺口 fail closed。

## 修复

在 scope builder 中增加 already-existing、official、in-window 的：

`data/reference/kc50_anchor_2026-06-16.csv`

作为 STAR50 carry-in anchor。

这不是新增 universe，也不是添加研究股票池。它只恢复 frozen STAR50 历史成员集合中原本遗漏的 `688065`。

修复后的 scope：

- total symbols: 193
- SH: 103
- SZ: 90
- BJ: 0
- universe labels remain only STAR50 / CHINEXT50

同时把该 anchor 纳入 V4-A shared persistent-stage producer fingerprint，确保 bundle identity 会随依赖变化而变化。

## 边界

本修复：

- 不读取 outcome / forward return；
- 不改变 80% fundamental / earnings / valuation coverage threshold；
- 不改变 100% major-negative completeness rule；
- 不改变 frozen event identities；
- 不改变 STAR50 / ChiNext50 universe definition；
- 不运行模型、参数搜索、holdout/OOS；
- 不改变 Evidence / Production / trading authority；
- 不把任何个人持仓或私有数据写入公开仓。

代码修复本身**不授予历史资格**。新的 public bundle 仍必须经过既有 private V4-A verifier；若 earnings 等 event-date coverage 仍不足，EDGE-1 继续 fail closed。
