# Prospective Public Raw Intermediate Checkpoint Persistence v1

## Purpose

This engineering layer makes the manual `prospective-context-raw-v1` workflow restartable:

> completed compatible work is restored first; only missing work is recomputed.

It is public-data infrastructure only. It does not grant evidence qualification and does not change model, signal, threshold, universe, PIT/no-lookahead, Production, portfolio, or trading semantics.

## Persistence unit

For one exact `operation_date` the workflow can persist:

- STAR50 PIT membership + live witness + constituent daily history + index history;
- ChiNext50 PIT membership + live witness + constituent daily history + index history;
- SSE 588000 ETF-share monthly chunks;
- SSE + SZSE A-share turnover monthly chunks;
- SZSE 159915 ETF-share monthly chunks.

Every unit is stored through `ImmutableCheckpointStore` and retains its exact checkpoint fingerprint and receipt hash.

## Durable progress registry

Each operation date has an independent immutable release registry:

`prospective-context-checkpoints-YYYY-MM-DD`

A run packages all completed permanent-eligible units into one deterministic progress snapshot:

`pcraw-progress-v1-YYYYMMDD-<identity>.tar.gz`

The manifest and SHA-256 are uploaded beside the archive. Existing assets are never overwritten. Equal asset names must be byte-identical; otherwise publication fails closed.

Multiple snapshots may coexist for the same date as later retries add completed units.

## Restore precedence

Before provider recomputation:

1. derive the exact expected checkpoint fingerprints from the current materialization semantics and exact operation date;
2. download progress manifests from that date's release only;
3. choose snapshots that contribute current expected fingerprints;
4. reject a fingerprint if two immutable snapshots claim different receipt hashes;
5. verify archive SHA, bundle identity, checkpoint identity, checkpoint receipt and file hashes;
6. restore compatible units into `.cache/prospective_context_raw`;
7. recompute only exact missing units.

Restored units are revalidated by the ordinary final materialization gates. A checkpoint hit never grants qualification.

## Semantic identity versus operational commits

Checkpoint compatibility is based on a materialization semantic fingerprint rather than the ordinary Git commit SHA.

Semantic fingerprint inputs include:

- frozen public raw contract materialization semantics;
- source-routing / parsing / normalization code relevant to the data family;
- PIT anchors and official adjustment files for each universe.

It deliberately excludes:

- workflow wiring;
- retry/backoff orchestration;
- checkpoint packaging/publishing format;
- diagnostics;
- ordinary Git commit changes that do not alter materialized data semantics.

The actual producer Git SHA is still retained as provenance.

## Same-date firewall

`operation_date` is part of checkpoint identity.

Cross-operation-date automatic reuse is forbidden in v1. This is intentional: adjusted historical prices and public-source backfills can change with capture time, so a previous capture must not silently become today's forward-known input.

An exact same-date restored capital checkpoint may satisfy the same-day freshness preflight without contacting that provider again. A prior date cannot.

## Permanent eligibility

A unit is published permanently only when its transport/materialization state is safe for reuse.

Examples:

- a universe work unit with unresolved constituent-history download errors is not promoted to permanent reuse;
- bilateral turnover requires no recorded source errors;
- SZSE ETF monthly chunks require no recorded source errors;
- SSE ETF chunks can preserve explicit `NO_MATCHING_ETF_ROW` observations because the frozen downstream 60-day coverage gate, not checkpoint persistence, decides whether missing observations are acceptable.

The final capture still requires exact operation-date rows and the frozen trailing-60 / 80% ETF coverage rule.

## Pre-open V2 provider-publication readiness

The evidence-eligible capture window opens after the market session closes at 15:00
Asia/Shanghai and closes at the next trading day's 05:30 freeze. That eligibility
window does **not** imply that every official upstream source has published the
session's final row immediately after 15:00.

Operational evidence observed on the first two capture days:

- run `35622176775` started at approximately 23:55 Asia/Shanghai for
  2026-09-21 and successfully obtained the required public capital inputs;
- run `35707188405` started at approximately 16:52 Asia/Shanghai for
  2026-09-22 and failed closed because SSE `588000` returned
  `NO_MATCHING_ETF_ROW` and SZSE `159915` returned no rows, while the
  bilateral turnover rail was already present.

The manual pre-open workflow therefore uses an **operational** same-session
not-before guard of 23:45 Asia/Shanghai. This does not narrow or expand evidence
eligibility: it only prevents a known-premature provider probe and wasted runner
work. A run on the decision date before 05:30 remains allowed.

The capital freshness preflight also distinguishes:

- `NOT_YET_PUBLISHED`: only the known exact-date ETF rows are absent through
  the expected empty/no-row provider responses. Pre-open V2 retries this state
  four probes with short bounded backoff;
- `SOURCE_FAILURE_OR_INCOMPLETE`: schema, transport, or otherwise unexpected
  incompleteness. This class fails closed immediately after each provider's own
  bounded transport retries.

Neither class permits stale carry-forward, alternate dates, interpolation,
forward-fill, historical replay, or evidence promotion. If
`NOT_YET_PUBLISHED` still exhausts after 23:45, the correct action is a later
retry before 05:30, not a fallback to older data.

## Daily automation wrapper

The frozen `prospective-context-raw-preopen-v2` workflow remains a manual
`workflow_dispatch` surface. A separate thin orchestrator,
`.github/workflows/prospective-public-daily-orchestrator-v1.yml`, is explicitly
allowlisted under `reference/prospective_daily_automation_v1.json`.

Its bounded retry schedule starts at the earliest operationally mature **23:45 Asia/Shanghai** window and then retries every **15 minutes** through a final **04:00 Asia/Shanghai** opportunity (`45 15 * * *`, `0,15,30,45 16-19 * * *`, and `0 20 * * *` UTC). The denser wrapper cadence is an operational resilience response to observed hour-scale GitHub scheduled-Action delivery delay; it does not widen the evidence window. Exact-release completeness checks and active-run checks make every later attempt an idempotent no-op once the exact bundle/raw capture succeeds. A 04:00 dispatch may have less than the raw workflow's nominal 90-minute bound remaining, but the target builder independently enforces that materialization must actually complete no later than the fixed 05:30 data-freeze boundary, so a late completion still fails closed before formal raw publication. It:

1. resolves the exact A-share trading-day pair from the live trading calendar;
2. preserves the active session-close → next-trading-day 05:30 window across intervening weekends or exchange holidays, and no-ops only when the current time is outside that exact trading-calendar window;
3. treats `market-bundle-T` as ready only when the immutable release contains the exact archive / sha256 / manifest three-asset set; if the release is missing or partial, it requests the existing `publish-market-bundle.yml` with guarded `target_date=T` so the publisher can create or heal the exact release without arbitrary historical backfill;
4. treats `prospective-context-raw-preopen-v2-T-for-T+1` as ready only when its archive / sha256 / manifest three-asset set is complete; otherwise, and only when no raw capture is already active, it dispatches the existing manual pre-open raw workflow;
5. the raw builder checks the operational completion window again after materialization: completion before T 23:45 or after decision-day 05:30 fails closed before the formal immutable raw package is published;
6. partial immutable market-bundle or raw releases are healed only by uploading missing assets after every already-present asset is verified byte-identical; any existing byte drift fails closed.

The wrapper does not materialize model outputs or evidence. It cannot substitute
rolling latest, stale rows, prior dates, interpolation, historical replay, or
backfill. Provider publication readiness, PIT validation, and the 05:30 freeze
remain enforced by the target workflow itself. A delayed scheduler invocation
therefore cannot silently extend the evidence window past 05:30 or substitute a
prior-session row.

## Failure behavior

Normal capture failure:

- the capture step fails closed;
- the subsequent `always()` checkpoint packaging step runs;
- completed permanent-eligible work is published to the date registry;
- the next retry restores it and executes only missing units.

Hard runner termination / platform kill:

- post-failure publication cannot be guaranteed;
- only work already published by an earlier progress snapshot is durable;
- current-run local work that never reached publication may be lost.

This limit is explicit in the frozen public raw contract.

## Runtime proof of reuse

The workflow writes recovery diagnostics and a runtime summary. For a fully resumed unit, the expected proof is:

- checkpoint restore selected the exact fingerprint;
- `resumed_chunks > 0`;
- the corresponding `executed_chunks == 0`;
- final public validation still reran successfully.

Do not infer successful reuse from a green restore step alone.

## Security and research boundary

Checkpoint releases contain only public raw/intermediate data and public provenance.

They must never contain:

- private model semantics;
- thresholds or signals;
- portfolio or holdings;
- private evidence;
- forward outcomes;
- research results;
- evidence-tier decisions;
- trading authority.

The checkpoint contract explicitly sets:

- `formal_evidence_handoff = false`;
- `qualification_granted_by_checkpoint = false`.
