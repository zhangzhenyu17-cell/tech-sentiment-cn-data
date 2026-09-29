# Prospective Capture V3 — formal source-observation rail

## Scope

V3 decouples **public-data acquisition time** from **decision eligibility**.
As of the explicit 2026-09-29 activation, V3 is the formal public capture rail.
It supersedes Timing V2 for future capture/qualification timing only. Historical
V2 receipts and existing canonical gaps are not reinterpreted or rewritten.
Individual source packages still do not grant downstream evidence qualification.

## Invariants

- `market_session_date=T` remains the event date.
- `decision_date` must be the immediate next A-share trading day.
- capture may continue after the former 05:30 SLA and after the formal source-eligibility cutoff;
- every successful source receipt preserves `first_observed_at_asia_shanghai`;
- event date never implies that the value was known earlier;
- an observation first seen after the cutoff is permanently ineligible for that
  decision under formal V3 timing;
- late recovery may improve data completeness, but never retroactively converts a
  historical `NON_BACKFILLABLE_PROSPECTIVE_EVIDENCE_GAP` into a clean prospective day;
- no forward outcome, private model semantics, portfolio data, Production change,
  or trading authority is allowed.

## Source-level persistence

The active formal collector treats the four capital inputs independently:

- `SSE_588000`
- `SZSE_159915`
- `SSE_TURNOVER`
- `SZSE_TURNOVER`

A successful source is packaged and published independently under an immutable
source-specific release. Later retries request only still-missing sources.
This prevents an SSE failure from discarding already successful SZSE observations.

Publication is archive-first. If an interrupted GitHub run leaves a partial
release with the archive present, the next collector heals checksum/manifest
sidecars from that exact archive instead of recapturing the source. This
preserves the original `first_observed_at_asia_shanghai`. A partial release
without its archive fails closed, and any byte conflict fails closed.

## Scheduling

The scheduled GitHub-hosted collector starts at 15:15 Asia/Shanghai, fifteen minutes after the market-session close. It uses a 30-minute baseline cadence, then increases observation density to every five minutes at 08:15, 08:20, 08:25, 08:30, 08:35, and 08:40 before the immutable 08:45 formal source cutoff. An 08:45 dispatch is retained only as a late-recovery observation; source-fetch completion must itself be no later than 08:45 to qualify for that decision date. Sparse post-cutoff late-recovery attempts remain available. This reduces conservative false gaps without changing eligibility semantics.

The collector retries
through the morning decision window, and continues low-frequency late recovery
through 14:15. Manual dispatch may provide an exact T/T+1 pair after that window.

The 05:30 time is no longer a capture hard wall in V3. It remains historical V2
semantics and may still be reported as an operational SLA comparison.

## Transport diversity

The source observation receipt records `transport_origin` and `runner_name`.
Two public-only GitHub-hosted transport origins are active in formal capture mode:
`GITHUB_HOSTED` (Ubuntu primary) and `GITHUB_HOSTED_MACOS_FALLBACK`.
They share the same official source identities, normalization semantics,
immutable release tags, and one concurrency group. The macOS rail is lower
frequency and attempts only source observations that remain missing. Automatic
macOS resolution is restricted to the latest closed session; older sessions
require an explicit exact T/T+1 manual dispatch.

The contract remains ready for a future isolated `SELF_HOSTED_PUBLIC_DATA`
transport, but self-hosted infrastructure is **not connected** and still
requires a separate security audit. It must use the same official source
identities, exact T, normalization rules, and immutable observation schema.

## Activation

Formal consumer activation was explicitly authorized on 2026-09-29. The
compatibility aggregate artifact remains named `prospective-context-raw-preopen-v2`,
but future qualification requires V3 source-fetch-completion timestamps no later
than 08:45 Asia/Shanghai, aggregate artifact completion no later than 09:15, and
qualification before the 09:30 first eligible execution boundary. Source-level
`formal_evidence_handoff=false` remains mandatory because qualification occurs
only after the verified aggregate/private gate.

## Compatibility field

The package schema retains the legacy compatibility key `shadow_decision_eligible` to avoid breaking existing immutable package readers. Under formal V3 activation this key is interpreted only as the source-level “first observed by the formal 08:45 cutoff” flag; it does not itself grant downstream evidence qualification.
