# 931152 innovation-drug V0 public design bundle contract

Status: outcome-blind public-input contract for the frozen 2019-04-22 through 2023-12-31 design study. It does not contain model events, forward returns, MAE/MFE, holdout data, portfolio data, or trading permissions.

## Frozen time boundary

- technical-price warm-up begins `2018-01-01`;
- model design interval begins `2019-04-22`;
- public design bundle ends no later than `2023-12-31`;
- any `2024-01-01+` row makes the design bundle ineligible;
- `holdout_included=false` and `model_outcomes_included=false` are mandatory manifest assertions.

The earlier warm-up is input history only. Point-in-time membership remains bounded to the official 931152 design interval, so warm-up rows cannot become sector members or events.

## Two price/rule rails

The bundle deliberately separates two semantics that must not be conflated.

### Technical price rail

Rolling MA20/MA60, 60-day low, 252-day high, equal-weight returns and related price features use `qfq` (front-adjusted) stock close histories. `pct_chg` is recomputed from the same QFQ close series as `qfq_close_to_close`.

This choice is frozen before any innovation-drug design outcome is opened. Its purpose is to prevent dividends, splits and other corporate actions from creating artificial trend/new-high/new-low signals. The V0 feature formulas themselves are unchanged.

### Exchange-limit rail

ST/trading status, IPO/special-day qualification and daily `limit_pct` remain sourced from the separately audited BaoStock lifecycle/status evidence plus exchange board/date rules. These are not inferred from QFQ prices or realized price moves.

A stock-day with uncertain limit semantics remains `limit_eligible=false`. The ≥95% daily limit-rule gate is unchanged.

## Required files / fields

A formal design bundle is expected to contain at least:

- point-in-time `universe` with bounded `effective_start/effective_end`;
- stock `prices` containing the full warm-up plus design-period history, QFQ technical semantics, and attached qualified limit fields;
- official 931152 `index_prices` through 2023-12-31 only;
- a manifest recording source/public-repo commit and file hashes, the 11/11 membership gate, strict limit-rule gate result, minimum daily coverage, frozen dates and no-holdout assertions.

All 71 symbols in the qualified design universe must have public price history. Missing provider rows must be reported; they cannot be silently removed from the expected member-day denominator.

## Qualification scope

Passing this contract grants only `historical_design_research_input_only` status. It does not grant holdout, forward-production, Fusion, position-sizing, automatic execution, or real-trading permission.
