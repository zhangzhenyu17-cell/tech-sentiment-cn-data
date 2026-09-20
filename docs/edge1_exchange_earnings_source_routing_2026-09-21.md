# EDGE-1 earnings source-routing engineering bridge — 2026-09-21

状态：

`ENGINEERING_READY_NOT_AUTHORIZED_FOR_FORMAL_MATERIALIZATION`

## 发现

封存 V4-A final artifact 中：

- SSE 官方公告源已有 93 份 `ISSUER_EARNINGS_FORECAST`，覆盖 48 个实体；
- SZSE 官方公告源已有 539 份，覆盖 86 个实体；
- 但 562 条正式 `ISSUER_EARNINGS_DIRECTION` 全部来自 CNINFO。

因此 STAR50 earnings coverage 的 0/低覆盖并不能解释为“上交所没有 earnings forecast 文档”。当前正式方向 materializer 只走 CNINFO，是一个 source-routing 工程缺口。

## 本次工程修复

新增独立、未接入正式 bundle 的 exchange earnings bridge：

- 只消费已经 materialized 的 SSE/SZSE `ISSUER_EARNINGS_FORECAST`；
- 只消费 `HISTORICAL_RECONSTRUCTABLE`；
- 复用同一个 `issuer-explicit-guidance-v1` classifier；
- 读取 exact official PDF body；
- event date / evidence available date / source identity 全部继承；
- UNKNOWN 继续 UNKNOWN，不当作 not-DOWN；
- 不使用 numeric threshold、price、return 或 outcome。

## 为什么现在不运行正式 rematerialization

把 SSE/SZSE 的方向记录接入正式 public bundle，可能改变 frozen event 的 PIT evidence eligibility，因此属于 evidence-qualification 变化。

当前只完成代码、合同和 synthetic tests，不：

- 接入 derived/finalizer；
- 修改 workflow；
- 运行真实 SSE/SZSE PDF materialization；
- 改 private evidence qualification；
- 打开 Low/High outcome；
- 改 Production / trading。

这使下一步真正需要授权时，只剩“执行已冻结 bridge + verifier”，而不是边跑边设计。
