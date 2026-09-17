# Capital / PIT materialization v1

## Purpose

This public-data layer materializes only auditable public inputs. It does not contain model thresholds, signals, private evidence, portfolio data, forward-return research, or Production permissions.

The existing `qualify-capital-inputs` workflow remains `workflow_dispatch` only. It now emits an immutable `capital-pit-materialization` artifact containing raw/public inputs, source errors, an artifact manifest, and a readiness matrix.

## Frozen gates

- 588000 target start: `2022-01-04`.
- Real trading calendar only.
- ETF shares: no interpolation / forward fill / backfill.
- Trailing-60 coverage gate: `>= 80%`.
- 20d and 60d endpoint availability are reported separately.
- `SSE_SZSE_A_SHARES` turnover requires both exchanges on every target day; unit is CNY.
- Financing source-unit contract is fixed: SSE raw=`CNY`, SZSE raw=`CNY_100M`, canonical=`CNY`.
- Financing role remains `RESEARCH_INPUT` even after input qualification and is never injected into an existing Capital Regime composite by this repository.
- No liquidity threshold, model parameter, signal, or research outcome is computed here.

## PIT announcement materialization

`CNINFO_ANNOUNCEMENT_ARCHIVE` is materialized as immutable raw issuer-document metadata when an explicit public issuer list is supplied to the manual workflow. The public layer stores:

- `evidence_id`
- `entity_id`
- `evidence_type`
- `event_date`
- `evidence_available_date`
- `source_identity`
- `provider`
- `document_id`
- `revision_id`
- `provenance`
- `ingestion_identity`
- `availability_state`

It deliberately does **not** infer fundamental quality, earnings direction, valuation direction, event direction, `PANIC_MISPRICING`, or any future outcome from announcement titles.

Daily research is close-based. If the upstream record contains a precise publication time at or before 15:00 on a real trading day, that date is usable. Date-only records and after-close/non-trading-day publications are conservatively delayed to the next real trading day. If that next trading date is not present in the supplied real calendar, the row fails closed instead of being backfilled into the publication date.

Each document record is append-only. Re-observing the same `evidence_id` with identical content is idempotent; changing historical content for an existing `evidence_id` is an error. Later correction documents receive their own document identity and never overwrite earlier replay prefixes.

## Readiness states

The materializer uses only:

- `QUALIFIED_INPUT`
- `HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED`
- `PARTIAL_COVERAGE`
- `FORWARD_ONLY`
- `DATA_INSUFFICIENT`
- `UNAVAILABLE`

`QUALIFIED_INPUT` means the **public input rail** passed the frozen input gate. It does not mean a private research/evidence model has been promoted.

## Major-negative exclusion

Raw issuer announcements alone are not sufficient to declare `major_negative_event_exclusion_complete=true`. That gate additionally needs demonstrably complete relevant issuer, exchange/regulatory, litigation, credit, earnings-warning/guidance-down, and other registered adverse-event sources for the target market date. Unknown coverage remains unknown; absence of a retrieved record is not proof of absence.

Therefore this public materializer defaults `major_negative_exclusion` to `DATA_INSUFFICIENT` until the full registered source set is materially covered and replay-qualified.

## Clean-forward readiness

The materializer does not create or start a clean-forward evidence clock. Private-side readiness may become `READY_MANUAL_ONLY` only after stable daily market inputs, stable PIT sources, exact market-date identity, provenance, revision handling, append-only replay, frozen schema, and V2/Execution isolation all pass. No schedule is added by this change.
