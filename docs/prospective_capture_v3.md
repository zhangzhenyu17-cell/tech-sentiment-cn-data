# Prospective Capture V3 — shadow source-observation rail

## Scope

V3 decouples **public-data acquisition time** from **decision eligibility**.
It is shadow-only. It does not replace `PROSPECTIVE_TIMING_V2`, does not modify
formal evidence qualification, and does not rewrite existing canonical gaps.

## Invariants

- `market_session_date=T` remains the event date.
- `decision_date` must be the immediate next A-share trading day.
- capture may continue after the former 05:30 SLA and after the shadow decision cutoff;
- every successful source receipt preserves `first_observed_at_asia_shanghai`;
- event date never implies that the value was known earlier;
- an observation first seen after the cutoff is permanently ineligible for that
  decision in V3 shadow semantics;
- late recovery may improve data completeness, but never retroactively converts a
  historical `NON_BACKFILLABLE_PROSPECTIVE_EVIDENCE_GAP` into a clean prospective day;
- no forward outcome, private model semantics, portfolio data, Production change,
  or trading authority is allowed.

## Source-level persistence

The active shadow collector treats the four capital inputs independently:

- `SSE_588000`
- `SZSE_159915`
- `SSE_TURNOVER`
- `SZSE_TURNOVER`

A successful source is packaged and published independently under an immutable
source-specific release. Later retries request only still-missing sources.
This prevents an SSE failure from discarding already successful SZSE observations.\n\nPublication is archive-first. If an interrupted GitHub run leaves a partial\nrelease with the archive present, the next collector heals checksum/manifest\nsidecars from that exact archive instead of recapturing the source. This\npreserves the original `first_observed_at_asia_shanghai`. A partial release\nwithout its archive fails closed, and any byte conflict fails closed.\n
## Scheduling

The scheduled GitHub-hosted collector starts at 23:45 Asia/Shanghai, retries
through the morning decision window, and continues low-frequency late recovery
through 14:15. Manual dispatch may provide an exact T/T+1 pair after that window.

The 05:30 time is no longer a capture hard wall in V3. It remains historical V2
semantics and may still be reported as an operational SLA comparison.

## Transport diversity

The source observation receipt records `transport_origin` and `runner_name`.
Two public-only GitHub-hosted transport origins are active in shadow mode:
`GITHUB_HOSTED` (Ubuntu primary) and `GITHUB_HOSTED_MACOS_FALLBACK`.
They share the same official source identities, normalization semantics,
immutable release tags, and one concurrency group. The macOS rail is lower
frequency and attempts only source observations that remain missing.

The contract remains ready for a future isolated `SELF_HOSTED_PUBLIC_DATA`
transport, but self-hosted infrastructure is **not connected** and still
requires a separate security audit. It must use the same official source
identities, exact T, normalization rules, and immutable observation schema.

## Activation

Formal consumer activation requires a later explicit gate after shadow evidence
shows that timing, identity, and transport behavior are stable. Until then,
`formal_evidence_handoff=false` is mandatory.
