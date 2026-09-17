# Capital / PIT Materialization V1

Status: `MANUAL_ONLY / FAIL_CLOSED / PUBLIC_DATA_ONLY`

This layer turns public qualification outputs into immutable, auditable materialization artifacts without changing model logic, thresholds, evidence qualification, portfolio permissions or trading authority.

## Frozen rules

- 588000 target history starts from the real trading calendar beginning 2022-01-04.
- No interpolation, forward fill or backward fill.
- ETF rolling coverage remains trailing 60 trading days with an 80% gate.
- 20d and 60d endpoint availability is reported explicitly.
- SSE + SZSE A-share turnover requires both exchanges on every target trading day and is normalized to CNY.
- Financing remains `RESEARCH_INPUT` even when canonical units are qualified.
- Market-liquidity 70/30 boundaries, 252 lookback and 60 minimum history are not changed here.

## Immutable materialization identity

Every materialized diagnostic now carries a deterministic manifest containing:

- dataset id and schema version;
- target date range;
- readiness state;
- source identities and provider interfaces;
- per-file SHA256 and byte size;
- a deterministic manifest SHA256;
- frozen dataset-specific metadata.

A dataset cannot be labelled `QUALIFIED_INPUT` without an explicit source identity and provider interface.

## Readiness states

Only these public-data readiness states are accepted:

- `QUALIFIED_INPUT`
- `HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED`
- `PARTIAL_COVERAGE`
- `FORWARD_ONLY`
- `DATA_INSUFFICIENT`
- `UNAVAILABLE`

The manual `qualify-capital-inputs` workflow emits a readiness matrix. It does not promote any private model or evidence status.

## Current limitations

The known 588000 candidate archive beginning 2023-01-03 remains non-canonical because it neither covers the full 2022 prehistory nor independently proves original point-in-time availability. It must not be used to bypass the official-source provenance gate.

Historical financing, issuer announcements, fundamentals, earnings expectations, trailing valuation and major-event evidence remain unmaterialized until an actual versioned historical ledger is collected. Schema/contract readiness alone is not materialization.

`major_negative_event_exclusion_complete` cannot be inferred from the absence of a record. It may become true only after all required negative-event source families for the market date have been explicitly checked.

No automatic schedule is added by this change.
