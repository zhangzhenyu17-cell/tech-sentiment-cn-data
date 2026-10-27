# Market Regime V0 四宽基公开输入手动采集（Issue #663）

工作流：`.github/workflows/market-regime-raw-capture-manual-v1.yml`，仅 `workflow_dispatch`。与既有契约 `reference/market_regime_v0_public_input_contract.json` 的 `manual_only=true`、`automatic_trigger=false` 一致。

## 操作和验证

1. GitHub Actions 中选择 **Market Regime V0 Public Raw Input (manual)**，手动输入已经完成交易的 `market_date=YYYY-MM-DD`，`start_date` 需提供至少121个交易日历史。生产器沿用 `scripts/materialize_market_regime_v0_public_input.py`，只取四个公开指数 `000985/000300/000905/399006`。
2. Run 必须同时完成公开边界审计、生产器回归测试、对应日期原始数据采集、date alignment、four-index universe、CSV SHA256 与无 private/evidence/model 字段的机器校验。数据源晚到/失败不可补写或声明 READY。
3. 产物为 `market-regime-v0-raw-<market_date>-<run_id>` artifact（CSV+manifest），仅是公共原始输入；`GITHUB_TOKEN` 保持只读，工作流不提交、不发布当前状态、不升级私有资格。
4. 私有仓按现有 `scripts/build_market_regime_v0.py` 的 manifest、digest、provenance 与日期契约受控消费，再核验最新完成交易日；同日 breadth / capital context 没有材料则显式 `DATA_INSUFFICIENT`。正式 Current Registry 的 rebuild 与 State Sync 不得跳过。
5. **截至本次工程 PR，没有对新市场日运行 capture，也没有改变当前 `latest_market_date=2026-09-30` 的历史正式文件。**

本工作流的目的在于建立可复现的 public input 运维入口。自动 schedule、workflow_run、push、pull_request 等触发仍未授权；如希望未来实现每日自动化，必须经过 GitHub v1.4 对新增触发的显式 allowlist 和范围审查。

## 相关故障验收

市场日漂移、来源错误、manifest/digest 不一致、少于121交易日或缺少任一宽基时必须失败；合法失败不产生 fresh qualification。历史 Market Regime 的 BEAR 不得冒充最新市场日。私有持仓/信号/阈值不会进入公开仓。
