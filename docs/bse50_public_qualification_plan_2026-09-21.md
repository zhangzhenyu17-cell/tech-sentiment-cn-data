# BSE50 public qualification plan — 2026-09-21

Status: **ENGINEERING PREPARED / RESEARCH AUTHORIZED / OUTCOME BLIND / FAIL CLOSED**

本阶段只为已明确授权的北证50（899050）新 Universe 资格验证准备公开数据。

## Public / private boundary

公开仓只允许处理：

- 北交所官方指数公告与附件；
- 北证50 point-in-time 样本成员；
- 历史证券代码映射；
- 公开成分股行情；
- 899050 官方指数行情；
- provenance / coverage / hash / manifest；
- allowlisted public bundle。

公开仓不得包含 V1 私有阈值、私有模型输出、投资信号、个人持仓、forward outcome、私有 evidence、研究结论、Production 或交易权限。

## Mandatory preflight

长时数据物化前先通过三层 preflight：

1. **code / contract**：解析器、PIT、30% 涨跌幅、manual-only workflow、public boundary；
2. **live source**：北交所公告索引、首批样本、历史指数公告、当前指数页面、代码映射；
3. **representative execution classes**：至少覆盖常规定期调整、附件解析、旧 4/8 系代码、92 系代码、899050 官方指数轨道。

当前新增的 `bse50-source-preflight` 只做低成本 hosted-runner transport/source discovery，不读取 forward outcome，不授予数据资格。

## Fail-closed gates

以下任何一项不满足，都不得进入 private outcome study：

- 自 2022-11-21 到研究截止日无法得到连续 PIT membership；
- 任一生效快照无法唯一还原为 50 个样本；
- 临时调整存在缺口；
- 旧代码与 92 系代码映射会产生 lookahead 或身份歧义；
- 成分股价格覆盖低于冻结下限 95%；
- 899050 官方指数轨道不完整；
- provenance、allowlist、manifest 或 hash identity 不完整。

Public producer success 只说明公开数据阶段完成；最终 `HISTORICAL_DATA_QUALIFIED` 必须由 private verifier 给出。
