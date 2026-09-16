# Sector public-data eligibility gate

This public-data contract supports sector-adapter research without exposing model rules, thresholds, outcomes, portfolio logic, or private evidence.

It governs only two public-input questions:

1. Can historical index membership be reconstructed point in time from dated evidence rather than today's constituents?
2. Can each active stock-day's price-limit semantics be used without silently assuming a board-wide historical default?

## Membership evidence

`audit_membership_evidence()` is outcome-free and fail-closed. Every expected scheduled-rebalance period must have one manifest row. A row is complete only when it records:

- the exact effective date;
- `changed` or `no_change`;
- an admissible evidence type;
- a durable `http(s)` source URL.

A missing notice or an unsuccessful search is not evidence of `no_change`.

For the first sector, CSI Brand Name Drug Industry Index (`931152`), the design-period registry is:

`data/reference/sector_931152_membership_evidence.csv`

The registered expected periods cover June/December 2019 through June/December 2023. They remain `pending` until primary dated evidence is versioned. This file is therefore an evidence-gap registry, not a reconstructed constituent history.

Useful source classes include official index-provider adjustment/effective-sample material and dated ETF creation/redemption baskets when the ETF is documented to track the same index. The existence of a tracking ETF or a PCF publication rule does not by itself prove any historical membership date; the dated source must still be captured.

Reference pages used to establish the public-data source path include:

- CSI 931152 factsheet: https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/931152factsheet.pdf
- Shenzhen Stock Exchange notice for ETF 159992 listing and start of creation/redemption on 2020-04-10: https://www.szse.cn/disclosure/notice/general/t20200407_576104.html

## Stock-day price-limit semantics

`audit_daily_limit_rule_coverage()` requires a genuine point-in-time universe and explicit stock-day fields:

- `limit_pct`: applicable daily price-limit percentage when usable;
- `limit_eligible`: whether that row has a usable price-limit rule for normalized extreme-move features;
- `limit_rule_source`: provenance or an explicit reason why the rule is not eligible.

The audit deliberately does **not** infer historical ST status or special trading-day rules. A row may be retained for ordinary price/breadth calculations while being marked ineligible for price-limit-normalized extreme features.

The caller supplies `min_daily_coverage`. This public module does not define the research threshold; it only measures whether the provided threshold is satisfied. Threshold selection belongs in the private preregistered research layer.

## Separation from existing technology data

The current technology pipeline is unchanged. In particular, this gate does not rewrite existing historical files, model features, provider order, adjustment mode, or production semantics. Sector inputs must use their own explicit public-data path rather than mutating technology evidence.

## Required checks

All public-repository changes continue to require:

```bash
python scripts/audit_public_tree.py
pytest -q
```

No market outcome, signal, model verdict, or private-repository material belongs in this repository.
