# Cross-Sector 基本面日期增量 V1

本阶段只采集公开披露增量，不重新计算旧 A/B，不生成同日 Fundamental qualification，不开展 pairwise 或 outcome research。新路径是 manual-only；源头解析器、日期规则与旧 Fundamental state builder 的原始字节被 contract 固定，没有改动。

## 固定范围与日期

- 目标 market session：2026-09-30；旧 Fundamental cutoff：2026-09-29。
- 冻结 membership：STAR50、ChiNext50 各 50；Innovation Drug 50；Defense 50；A500 500，合并后 572 个唯一证券。Technology 复用现有 anchor + dated adjustment 的既有重建器，其他三域复用原 official snapshot；不新增股票池。
- 查询 publication window：2026-09-29 至 2026-09-30。必须包含前一日盘后/日期-only 披露，才能捕获首次可用于 9/30 的文件；仅保留 inherited availability >9/29 且 <=9/30 的 numeric filing facts。
- 9/30 的日期-only、盘后披露明确排除，即使 calendar 未包含下一交易日，也不能将其提升为 same-day。这里是历史公开事实重建，不声称 contemporaneous prospective availability 或补回 clean evidence。

## 执行与恢复

Exact workflow：`.github/workflows/cross-sector-fundamental-date-increment-v1.yml`，建议从合并后的 `main` 手动运行，首次 `resume_run_id` 留空。

1. Code/contract preflight：公开边界审计、受影响测试、frozen source hash、五份 membership 与 union 检查。
2. Live preflight：真实 official calendar；复用 A 成果中的 exact official PDF identity 做同一下载/解析入口检查；五组各一个代表证券跑同一 delta capture 路径。
3. `sorted(scope)[unit_index::16]` 固定分片，每片 35–36 个证券，provider-facing max parallel=4。所有 ref 的同一 workflow 排队，不通过取消破坏运行中进度。
4. 独立 query 与 document checkpoint，每证券 heartbeat 记录 completed、executed/resumed、硬错误、elapsed、ETA 和 watermark。相同 phase/error signature 第三次硬错误停止该单元；soft DATA_INSUFFICIENCY 不算 transport failure。
5. 成功单元立即上传 immutable artifact；失败只保留诊断与 current-SHA progress。独立 finalizer 验证全部 16 个单元、572 个实体、文件摘要、PIT 日期和 authority false flags，缺任何单元都失败。

超时上限为 preflight 20 分钟、单元 30 分钟、finalizer 10 分钟。尚无新路径 hosted-runner 测量，不给出已实测 ETA；首次 pilot/单元结束后按 heartbeat throughput 更新。少量新 PDF 的窄日期查询应明显少于旧多年查询，但该判断仍待实际 run 证明。

恢复优先级：已成功 immutable unit → 同一 SHA 的 query/document progress → provider 重算。恢复到新 run 时可填写 `resume_run_id`，其单元必须与当前 source SHA、producer/contract/scope digest 完全相同；不接受 generic compatibility bypass。Immutable unit reuse 的 executed query/document counters 为零。失败恢复先选 Re-run failed jobs；若使用新 run，填写原 run id，成功 sibling 不重新采集。

每个 query/document 完成即写 checkpoint。取消前确认 cache save 和 immutable upload 已成功；最大重算范围是每个 active runner 正在执行的单元。未返回的 provider query 不能假定已保存。

## 输出与后续门槛

Final artifact：`cross-sector-fundamental-date-increment-2026-09-30-v1`，只含显式 allowlist：raw filing facts、每实体 query-window receipt、每文档 receipt、errors、scope、real calendar、五份 membership、capture contract 和 bundle manifest。Manifest 固定 producer/source SHA、contract/scope/file digest、unit receipts；保留旧 baseline/A/B identity 作为后续复用计划，不在本阶段重新下载或认证旧包。

完整空查询可以证明该证券没有新披露；provider 失败永远不能变成空查询。新 numeric filing 的解析不足必须保留 soft gap，不能用旧状态掩盖。公开 stage success 只说明捕获与结构完整；不等于私有 same-date state READY。

下一阶段需验收真实 run/artifact/archive/member digest，合并 exact baseline + A/B + delta；对新文件影响的实体用现有 state builder 重新解析，复用未变化实体的原证据，保留不足与冲突。再按原 80% coverage、strict-majority、Technology 两组件规则生成独立的同日 private receipt。旧证据日期和资格不重标，未定义的研究、阈值、Production、交易权限保持不变。

公开 bundle 保留 90 天；取得成功后冻结该 producer identity 到完成私有 intake，不因收尾或绿色状态重跑。
