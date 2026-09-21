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

## Failure behavior

Normal capture failure:

- the capture step fails closed;
- the subsequent `always()` checkpoint packaging step runs;
- completed permanent-eligible work is published to the date registry;
- the next retry restores it and executes only missing work.

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
