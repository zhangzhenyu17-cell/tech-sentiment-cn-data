# Follow-on sector data availability pre-audit

Status: **OUTCOME_FREE_PREAUDIT_ONLY**

This note prepares the already-preregistered follow-on sectors after the 931152 innovation-drug method freeze. It does not qualify historical membership, does not run sector temperatures/events/outcomes, and does not open any holdout.

## Frozen follow-on benchmarks

| Sector | Index | Design start | Design end | Official operator | Adjustment cadence |
| --- | --- | --- | --- | --- | --- |
| Defense | 399973 CSI Defense Index | 2014-04-15 | 2023-12-31 | China Securities Index Co., Ltd. | semiannual |
| Securities | 399975 CSI All Share Securities Companies Index | 2013-07-15 | 2023-12-31 | China Securities Index Co., Ltd. | semiannual |

Official reference factsheets:

- 399973: https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/399973factsheet.pdf
- 399975: https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/399975factsheet.pdf

The design starts above are the frozen preregistration boundaries; current factsheets are used only to confirm operator/index identity and publication metadata, not to infer historical constituents.

## Reusable public-data components already present

1. `extract_index_changes_from_sheets(..., index_code=...)` and `parse_adjustment_workbook(..., index_code=...)` are index-code parameterized. Synthetic regression tests cover exact extraction for 399973 and 399975.
2. `parse_membership_intervals(..., index_code=...)` uses exact-cell matching and half-open `[start, end)` semantics. Synthetic regression tests cover 399973 and 399975 and reject prefix-like codes.
3. BaoStock lifecycle/ST/trading-status plus structural board limit semantics are universe-agnostic once genuine point-in-time membership is available.
4. The qfq stock-history downloader is universe-agnostic. A sector-specific official index rail still needs an explicit verified index-provider mapping before formal design research.

## Known blocker before formal follow-on membership collection

`csi_adjustment_evidence.parse_announcement_search()` currently applies the innovation-drug default design window (`2019-04-22` through `2023-12-31`) when filtering announcement search results. The workbook parser itself is generic, but the announcement-discovery date filter must be parameterized before 399973/399975 historical evidence collection.

That parameterization is ordinary engineering, but it must preserve the existing 931152 default behavior and remain outcome-free.

## Qualification boundary

This pre-audit does **not** assert that either follow-on sector is `DATA_ELIGIBLE`. Formal eligibility still requires, per sector:

- genuine point-in-time membership over its frozen design interval;
- required membership evidence nodes and independent cross-checks;
- strict historical limit-rule coverage at or above the preregistered threshold;
- qfq technical-price inputs and official index rail;
- no current-constituent backfill;
- no holdout access;
- no formal historical model run before the innovation-drug method freeze is complete.
