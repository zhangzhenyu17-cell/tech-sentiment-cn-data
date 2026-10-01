# 日期增量本地 preflight lesson

分类：NORMAL_ENGINEERING / ROOT_CAUSE_VERIFIED / NO_PROTOCOL_CHANGE_NEEDED。无失败 hosted Actions run。

本地原始文件经普通文本中转时多写了尾部空行，导致 membership SHA256 不匹配。最初误判为 BOM/换行规范差异，原始字节检查证实是尾部空行，已改为 base64/raw-byte 获取；原冻结摘要没有改写。Inherited parser/source hash 也全部从 exact base commit 的原始字节建立，并逐一比对 Git blob identity。Regression：文本中转额外尾部空行必须导致 digest fail closed。

日期-only 与精确时钟混合的测试暴露 pandas 对单一推断格式的限制。查询 publication 日期检查采用明确 mixed-format 解析，后续仍逐行遵循原 availability 规则。Regression 同时覆盖 9/29 盘后、9/30 15:00、15:00:01 与日期-only。

本地最初运行测试缺少 Requests，并因将整个 pip vendor 目录加入路径而遮蔽完整 Pygments。已仅给测试隔离提供 vendored Requests；GitHub CI/正式 public workflow 按原 `[data,dev]` 安装真实依赖。本地测试不运行 live provider 或声称正式 workflow identity；正式 workflow 的 live pilot 是 scale-out 前门槛。

复用：A/B 完成成果与 source/parser 语义均未重跑或修改；公开价格 artifact 已独立验收。重算只涉及本地受影响测试，未启动旧 Current Fundamental 或历史研究。最小复验为 delta 路径、实际 CLI gate/finalizer、affected source tests，再由既有 PR CI 做公开边界审计和完整 pytest。

协议决定：既有 exact bytes、fail-closed、三层 preflight 和最小重跑规则足够，不改变协议。不得把 local engineering preflight、公开捕获或后续绿色 CI 解释为 evidence qualification、Production 或交易权限。
