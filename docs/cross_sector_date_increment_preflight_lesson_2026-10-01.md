# 日期增量本地 preflight lesson

分类：NORMAL_ENGINEERING / ROOT_CAUSE_VERIFIED / NO_PROTOCOL_CHANGE_NEEDED。

本地原始文件经普通文本中转时多写了尾部空行，导致 membership SHA256 不匹配。最初误判为 BOM/换行规范差异，原始字节检查证实是尾部空行，已改为 base64/raw-byte 获取；原冻结摘要没有改写。Inherited parser/source hash 也全部从 exact base commit 的原始字节建立，并逐一比对 Git blob identity。Regression：文本中转额外尾部空行必须导致 digest fail closed。

日期-only 与精确时钟混合的测试暴露 pandas 对单一推断格式的限制。查询 publication 日期检查采用明确 mixed-format 解析，后续仍逐行遵循原 availability 规则。Regression 同时覆盖 9/29 盘后、9/30 15:00、15:00:01 与日期-only。

本地最初运行测试缺少 Requests，并因将整个 pip vendor 目录加入路径而遮蔽完整 Pygments。已仅给测试隔离提供 vendored Requests；GitHub CI/正式 public workflow 按原 `[data,dev]` 安装真实依赖。本地测试不运行 live provider 或声称正式 workflow identity；正式 workflow 的 live pilot 是 scale-out 前门槛。

复用：A/B 完成成果与 source/parser 语义均未重跑或修改；公开价格 artifact 已独立验收。重算只涉及本地受影响测试，未启动旧 Current Fundamental 或历史研究。最小复验为 delta 路径、实际 CLI gate/finalizer、affected source tests，再由既有 PR CI 做公开边界审计和完整 pytest。

协议决定：既有 exact bytes、fail-closed、三层 preflight 和最小重跑规则足够，不改变协议。不得把 local engineering preflight、公开捕获或后续绿色 CI 解释为 evidence qualification、Production 或交易权限。

## PR CI 的 allowlist 顺序假设

- Repository/workflow/run/attempt：`tech-sentiment-cn-data` / `tests` / `36832243648` / 1；PR #277，branch `engineering/cross-sector-fundamental-date-increment-v1`，head `92610dba305aeff543e586930214de4e1e651489`。
- Failed job/step：`110271284522` / Test；pytest 17.99 秒，723 passed / 1 failed；public-tree audit 已通过。
- Failure class/signature：DETERMINISTIC_CODE_OR_ORCHESTRATION；`test_external_patterns_are_adapted_without_runtime_dependencies` 对 `allowlist[-1]` 作价格 workflow 的固定 dict 比较。
- Verified root cause：正常追加一个 manual-only 增量工作流后，旧测试的 last-position 假设失效；原价格条目的全部权限/用途字段未改变。不是 provider、parser、state、PIT 或 evidence failure。
- Preflight missed it：首次只运行输入/解析相关测试与公开路径审计，未同时覆盖被修改治理契约的完整测试文件。
- Correction/regression：改为按 exact workflow path 获取授权条目，继续完整比较价格条目与新增增量条目，并检查路径唯一；将治理测试加入真实 workflow preflight。不得通过删弱 authority 字段或改变旧条目恢复绿色。
- Reuse/recompute：新数据任务从未 dispatch，全部 baseline/A/B/价格 artifact 保留；只重新运行本地相关测试与 latest-head 普通 PR CI。不重跑已完成 data/evidence 任务。
- Smallest rerun scope：当前 PR 的唯一 pytest job；head 改变后使用既有 PR CI，不重跑旧 head。无原 immutable capture unit 需要重算。
- Protocol decision：NO_PROTOCOL_CHANGE_NEEDED。既有相关契约测试规则足够；本次补齐遗漏覆盖。新 workflow 仍 manual-only，研究/证据资格/Production/交易权限不变。
