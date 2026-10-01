# Cross-Sector 同日价格刷新

本次修复保留五条既有 official price rail 各自的最后观测日。`latest_market_date` 继续表示 envelope 的最小日期；CSV 不再因此裁剪其他 rail 的真实当日行。

新输出使用 `PER_BENCHMARK_LATEST_OFFICIAL_MARKET_DATE_NO_FORWARD_FILL`，逐 benchmark 日期写入 `latest_available_market_date_by_benchmark`。不 forward-fill，不生成私有模型或行业比较。

`.github/workflows/cross-sector-price-refresh-v1.yml` 仅允许手动执行，固定刷新截至 2026-09-30 的既有五条 rail，只发布 CSV 与 manifest 两个 allowlisted 文件，不写 main，不访问 private repo。它已登记在 public governance 的 manual-only allowlist。

该工作流的成功不保证 399673 已有 9/30 official row，也不保证私有 Fundamental axis 已就绪。缺失仍应由私有 intake 明确阻断相应 Domain 与 pair。新包需要 exact run/artifact/digest/SHA 校验后才能进入私有同日输入评估。
