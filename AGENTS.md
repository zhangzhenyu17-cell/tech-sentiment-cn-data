# GitHub v1.4 public-data execution

**项目口令：`按 GitHub v1.4 执行。`**

当前公开仓执行权威：`docs/github_execution_protocol_v1_4_public.md`。
长时任务继续遵循 `docs/long_running_engineering_execution_protocol.md` 与机器契约 `reference/long_running_engineering_execution_contract_v2.json`。

公开数据算力成本宽松但范围严格；正常 public data / PIT / validation / allowlisted bundle / release / CI / mechanical repair 可连续推进。>=30 分钟或 >=200 独立项优先 deterministic shards、durable progress、immutable unit、bounded parallelism、heartbeat/ETA、circuit breaker 和最小重跑。已完成 immutable unit 不因 sibling 失败重算。

新 workflow 默认 manual-only；新增自动 trigger 需要项目明确授权。公开仓永远不得承载私有模型、阈值、信号、持仓、私有证据、研究结果、secrets、Production 或交易权限。

# Repository boundary

This public repository is limited to market-data acquisition, point-in-time universe reconstruction, input validation, and public bundle packaging.

Do not add model features, scoring rules, thresholds, portfolio logic, personal holdings, backtest conclusions, forward-validation records, private-repository credentials, or cross-repository write access.

All changes must pass:

```bash
python scripts/audit_public_tree.py
pytest -q
```

A bundle must be created from the explicit allowlist in `tech_sentiment.bundle`; never archive a directory wholesale.

## Public data governance

Governance-sensitive public data products and semantic changes must follow `docs/public_data_governance_control_plane_v1.md` and the machine-readable contracts in `reference/public_data_governance_control_plane_v1.json` and `reference/public_data_quarantine_ledger_v1.json`.

A successful public workflow does not grant private qualification, evidence promotion, production authority, or trading authority. Structural artifact success does not replace outcome-blind semantic review when parser/builder semantics matter.

If an artifact is in an active quarantine entry, do not consume it downstream or pass it into private qualification intake. Parser semantic changes invalidate parsed-document facts and affected derived outputs; upstream query/index caches may be reused only when their own semantic identity is unchanged.
