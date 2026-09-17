# Capital Context public input qualification sources

This public-only diagnostic layer acquires and validates market inputs. It does not contain model scores, thresholds, trading rules, positions, or research outcomes.

## SSE ETF shares

- Official source identity: `SSE_ETF_SCALE_DAILY`
- Official page: Shanghai Stock Exchange ETF product scale/share history.
- Adapter: `akshare.fund_etf_scale_sse(date=YYYYMMDD)`.
- AKShare maps SSE `TOT_VOL` to `基金份额` and multiplies the exchange value by 10,000, so the normalized public output is stored in **shares**.
- Historical qualification is performed against an explicit trading calendar. Missing observations remain missing: no interpolation and no forward fill.
- The diagnostic reports the exact trailing-60-trading-day observed coverage and the fixed 80% eligibility gate. The public layer does not alter that gate.

## SSE/SZSE A-share turnover

The diagnostic builds a directly exchange-sourced **SSE + SZSE A-share** daily turnover series:

- SSE: `主板A + 科创板` from the daily stock overview. SSE publishes daily trading value in `亿元`; normalization multiplies by `1e8` to RMB yuan. B shares and stock buybacks are excluded.
- SZSE: `主板A股 + 创业板A股`, with historical support for the pre-merger `中小板` row. The SZSE market-overview adapter exposes `成交金额` in RMB yuan. B shares and non-stock securities are excluded.

The resulting scope is deliberately labeled `SSE_SZSE_A_SHARES`. It is **not** labeled canonical all-A turnover because Beijing Stock Exchange historical daily aggregate turnover has not yet been qualified to the same source/provenance standard. Until that is resolved, `canonical_all_a_state=INCOMPLETE_BSE_NOT_INCLUDED`.

## Financing balances

Both exchange/statistical definitions identify financing/margin balance as a daily RMB-yuan metric. However, the previously collected historical SZSE artifact is many orders of magnitude below a plausible yuan-scale exchange balance while the SSE side is around the expected `1e12` scale. The legacy rows therefore remain quarantined.

The new validator accepts a combined financing series only when:

1. both source units are explicitly `yuan`;
2. both exchange balances pass broad magnitude sanity bounds; and
3. the cross-exchange median scale ratio is plausible.

It never infers a multiplier from the mismatch itself. Failed rows remain `QUARANTINED_UNIT_UNVERIFIED` or `QUARANTINED_UNIT_MISMATCH`.

## Execution

`.github/workflows/qualify-capital-inputs.yml` is `workflow_dispatch` only. It has no schedule, push, pull-request, or workflow-run trigger. Its output is a diagnostic artifact and is not published into the production market bundle automatically.
