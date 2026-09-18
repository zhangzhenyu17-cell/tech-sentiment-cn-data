# V4-A Private Qualification Handoff Activation

日期：2026-09-18

状态：`ACTIVE / STRUCTURALLY_REACHABLE / OUTCOME_BLIND`

Public baseline：`tech-sentiment-cn-data@dd98e5345b0ffaa427ef9c95e97b1010ae54f272`

Private verifier merge：`tech-sentiment-cn@b953b9c302467f40dbdb51d421b06ed1f78904b1`

Private verifier contract：`v4a_artifact_intake_contract_v2`

## 1. 激活内容

`reference/v4a_private_qualification_handoff_v2.json` 现已绑定已通过 private CI 的
verifier merge SHA，并把 `activation_state` 从 `PENDING_PRIVATE_MERGE` 切换为
`ACTIVE`。

该激活只解除 public V4-A formal materialization 的结构性 preflight blocker。
它不等于 historical qualification，也不打开 V4-C real-outcome gate。

## 2. Fail-closed checks

Public reachability 现在同时核对：

- handoff ID 与 public schema v4a2；
- workflow 仍为 `qualify-capital-inputs`；
- public success 只允许 `PUBLIC_MATERIALIZATION_COMPLETED`；
- public 不得授予 historical qualification；
- private repo/module/contract identity；
- private merge SHA 必须为 40 位十六进制 commit SHA；
- 激活前不得 formal long run，激活后仅允许通过既有 manual-only workflow；
- `forward_outcome_read_required=false`；
- `parameter_search_required=false`；
- `production_or_trading_change_required=false`。

任一字段漂移都会使 Reachability Gate 继续 `FAIL_CLOSED`。

## 3. Research / evidence boundary

本次没有：

- 读取新的 Capital Context forward outcome；
- 运行 Baseline vs Context 或 Cause Attribution validation；
- 运行新 holdout/OOS、参数搜索或 ML；
- 修改 V1、ICE、Top Exit、Innovation Drug；
- 修改 Production、仓位或自动交易权限；
- 新增 schedule / push / pull_request / workflow_run evidence trigger。

`qualify-capital-inputs` 仍保持 `workflow_dispatch` only。

## 4. 下一步

只有本 activation PR 的 public-tree audit 与完整 pytest 通过并合并后，
formal public materialization 才可由人工显式触发。其产出的 artifact 仍必须进入
private verifier，只有 private receipt 为 `HISTORICAL_DATA_QUALIFIED` 才可能满足
V4-C pre-open gate。
