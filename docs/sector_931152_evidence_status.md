# 931152 sector-data evidence status

This document is outcome-free. It records only the public-data qualification state for the preregistered 931152 design interval (2019-04-22 through 2023-12-31). It must not contain model events, forward returns, MAE/MFE, or support conclusions.

## Membership chain

The design interval begins on the official 2019-04-22 index publication date. Therefore a complete point-in-time membership chain requires an initial dated launch constituent anchor **before** the first semiannual rebalance, plus every June/December rebalance period through 2023-12.

The canonical registry is `data/reference/sector_931152_membership_evidence.csv`. It now contains 11 required periods:

- 2019-04 launch anchor;
- 2019-06 and 2019-12;
- 2020-06 and 2020-12;
- 2021-06 and 2021-12;
- 2022-06 and 2022-12;
- 2023-06 and 2023-12.

Known calendar/effective dates are recorded even while the rows remain `pending`. A known rebalance date is **not** membership evidence. The row can become `complete` only after the exact 931152 constituent/adjustment evidence for that period is captured with a durable source URL.

Current status: `MEMBERSHIP_EVIDENCE_INCOMPLETE`.

## Historical ST/trading status

BaoStock is admitted only as a **candidate historical status source**, not as a complete price-limit source. Its daily interface exposes `tradestatus` and `isST`; its stock-basic interface exposes listing/delisting metadata. The project pins the research-only optional dependency to `baostock==0.9.3`.

The outcome-free structural rule layer is `src/tech_sentiment/sector_limit_semantics.py`. It encodes exchange-supported board/date/ST percentages while keeping special-day qualification separate.

For the 2019-2023 design interval the supported structural rules are:

- SSE/SZSE main-board ordinary A shares: 10%; risk-warning shares: 5%;
- ChiNext before the 2020-08-24 reform: ordinary 10%, risk-warning 5%;
- ChiNext from 2020-08-24: 20%, including risk-warning shares;
- STAR: 20% structural limit after the board began trading.

The module also records the 2026-07-06 main-board risk-warning change to 10% so a later untouched-holdout data audit does not silently carry the historical 5% rule forward.

Primary rule references are exchange materials embedded in the module. BaoStock status provenance is retained separately.

## Special-day fail-closed boundary

`isST` and `tradestatus` are not sufficient to establish every stock-day price-limit rule. IPO no-limit windows, relisting, delisting-transition first days, and any other exchange-recognized no-limit/special day require a separate auditable lifecycle/special-day status.

Accordingly `enrich_baostock_structural_limit_rows()` requires `special_day_status`:

- `ordinary`: independent lifecycle/special-day audit found no exemption;
- `no_limit`: an evidenced no-price-limit day;
- `unknown`: not yet evidenced.

Only `ordinary` rows with a valid structural rule and `tradestatus=1` can become `limit_eligible=true`. `unknown`, explicit no-limit days, suspensions, malformed status values, and unsupported boards fail closed.

Current status: `LIMIT_RULE_EVIDENCE_PARTIAL`. BaoStock materially closes the historical ST/trading-status subproblem, but does not by itself close the special-day subproblem.

## Probe

`scripts/probe_baostock_sector_status.py` is a manual research probe. It downloads unadjusted daily rows (`adjustflag=3`) plus stock-basic metadata and writes a provenance manifest. It labels its own qualification `CANDIDATE_STATUS_SOURCE_ONLY` and cannot promote data eligibility.

Install only for this research path:

```bash
pip install -e '.[sector-data]'
```

This extra is deliberately separate from the normal public market-bundle `data` extra so routine production does not acquire a new dependency or network path.

## Stop condition

Do not generate or inspect 931152 model outcomes until both are true:

1. all 11 membership periods pass the point-in-time membership evidence audit;
2. formal event days meet the preregistered >=95% stock-day price-limit-rule coverage threshold after special-day qualification.
