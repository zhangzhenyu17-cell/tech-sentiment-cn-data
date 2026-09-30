# Cross-Sector Relative Mispricing V0 — Public Input

状态：**PUBLIC RAW PIT INPUT ONLY / NO MODEL OUTPUT / NO PRIVATE QUALIFICATION**

本数据产品只为私有仓的 Cross-Sector Relative Mispricing V0 outcome-blind input feasibility 提供公开事实。

固定 benchmark：

- Technology：000688（STAR50）+ 399673（ChiNext50），两条 component rail 均保留；
- Innovation Drug：931152；
- Defense：399973；
- Core Beta：000510（CSI A500）。

来源遵循 benchmark operator 官方源优先：000688 / 931152 / 399973 / 000510 使用中证指数有限公司官网 `index-perf`；399673 使用国证指数网官方 daily market API。中证官方响应中的 `rolling_pe` 与价格字段一起保留。国证 399673 官方历史行情接口不暴露历史指数 P/E，因此该 rail 的 `rolling_pe` 明确保留为空，不用当前 P/E 回填历史，也不引入第三方估值。公开仓不解释估值高低，也不进行跨行业原始 P/E 比较。

## 运行

本产品不新增 GitHub Actions workflow。需要 materialize 时运行：

```bash
python scripts/materialize_cross_sector_relative_mispricing_v0_public_input.py \
  --as-of-date YYYY-MM-DD \
  --start-date 2024-01-01 \
  --source-commit <exact-public-code-commit>
```

默认输出：

- `data/reference/cross_sector_relative_mispricing_v0_public_input_latest.csv`
- `reference/cross_sector_relative_mispricing_v0_public_input_latest.json`

每个 benchmark 至少要求 451 个交易日历史（252 日最长价格窗口 + 至少 200 个有效分位观测所需的最小原始长度），且五条 rail 的最新市场日期必须一致。PE 缺失不会被插值；manifest 会显式报告每条 rail 的正值 PE 覆盖。

## 边界

- 不读取 forward outcome；
- 不生成 sector score / ranking / winner；
- 不含私有阈值、信号、持仓或证据结论；
- public materialization 成功不等于 private qualification；
- private 侧必须独立验证 exact commit、CSV hash、PIT、coverage、freshness 与 normalization contract。
