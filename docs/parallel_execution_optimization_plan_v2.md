# Parallel Execution Optimization Plan V2

## Status

**PLANNED / NOT YET ACTIVE**

This document records the next engineering optimization plan for long-running public-data materialization. It is intentionally documentation-only while the current reference run is still executing.

Reference profiling run:

- workflow: `.github/workflows/qualify-capital-inputs.yml`
- run id: `35311581264`
- source commit: `297578ad10b0c576038380593f1ca9f1b08771d1`
- execution mode: manual `workflow_dispatch`

No change described here is authorized to alter research scope, evidence eligibility, PIT/no-lookahead semantics, model definitions, thresholds, production authority, trading authority, or public/private boundaries.

## Objective

Reduce wall-clock time and failure blast radius without weakening evidence quality.

Optimization order:

1. measure the real critical path;
2. remove artificial serialization;
3. split only stages with material wall-clock contribution;
4. preserve immutable stage contracts and exact-identity resume;
5. re-run end-to-end verification after aggregation;
6. stop optimizing when artifact/setup overhead approaches producer runtime or source-provider concurrency becomes the limiting factor.

Runner capacity is not a reason to broaden scope or increase source pressure without evidence.

## Non-negotiable invariants

Every implementation under this plan must preserve all of the following:

- one frozen execution window;
- one frozen public symbol scope;
- exact source-commit identity;
- deterministic shard membership;
- immutable per-stage receipts;
- SHA-256 verification for formal cross-job handoff;
- cache used only for producer-local resume;
- artifact used for formal cross-job transport;
- fail-closed handling of missing, duplicate, conflicting, or unverifiable inputs;
- PIT/no-lookahead replay checks after merge;
- append-only/revision-safe evidence semantics;
- no interpolation, forward fill, backfill, or future-evidence substitution;
- one canonical final materialization bundle;
- one post-upload artifact identity receipt;
- independent downstream private qualification;
- manual-only qualification/materialization trigger unless separately authorized.

## Phase 0 — Critical-path profiling

Do not refactor further based only on intuition. First profile a completed real run.

For each job and matrix shard, capture:

- job wall-clock time;
- materializer step wall-clock time;
- setup/install time;
- artifact download/upload time;
- checkpoint restore/save time;
- resumed vs executed checkpoint counts;
- processed symbol/month/document counts;
- source retry/failure counts;
- materialized row counts;
- shard skew: max / median / min runtime;
- bytes uploaded/downloaded where available.

The profile should identify:

- the true critical path;
- whether source I/O, CPU parsing, aggregation, or artifact transport dominates;
- whether current shard counts are balanced;
- whether a producer is source-rate-limited rather than runner-limited.

No second-stage parallelization should be merged before this profile is reviewed.

## Phase 1 — Low-risk issuer sharding

### SSE issuer

Current shape:

```text
issuer_sse: one job over all eligible SH symbols
```

Candidate shape:

```text
issuer_sse[0..1] -> issuer aggregate
```

Use the existing deterministic `--shard-index` / `--shard-count` support.

Acceptance requirements:

- every expected SH symbol appears in exactly one shard;
- union of shard scope equals frozen SH scope;
- no duplicate symbol across shards;
- each shard has an immutable receipt;
- aggregate independently verifies completeness;
- canonical issuer aggregate is byte/semantic equivalent under deterministic fixture inputs.

Default candidate shard count: **2**. Do not increase beyond the smallest count that removes the measured bottleneck.

### SZSE issuer

Apply the same pattern to SZ symbols.

Default candidate shard count: **2**.

SSE and SZSE shard counts need not match if measured runtimes differ materially.

## Phase 2 — Capital producer separation

Current implementation performs ETF-share retrieval and SSE+SZSE turnover retrieval inside the same monthly loop. These are logically independent producers and should not serialize each other.

Candidate DAG:

```text
shared calendar
   |-- ETF-share producer ---------|
   |                               |--> capital aggregate
   |-- SSE+SZSE turnover producer -|
```

Required engineering changes:

- separate producer entrypoints or explicit producer modes;
- separate exact-identity checkpoint namespaces;
- separate stage receipts;
- capital aggregator that verifies:
  - execution window;
  - trading calendar identity;
  - fund scope;
  - turnover scope;
  - source identities;
  - file hashes;
  - coverage/readiness semantics.

Only if profiling still shows a material critical path after producer separation may time-based sharding be considered.

### Optional time sharding

If required, partition by deterministic calendar chunks, preferably month groups, with non-overlapping explicit ranges.

The aggregate must prove:

- all required months are present;
- no month/date overlap;
- no date gap relative to the shared trading calendar;
- identical canonical ordering after merge.

Do not time-shard merely to maximize runner count.

## Phase 3 — Filing / fundamental / earnings decomposition

Current `fundamental_earnings` shards combine three different workloads:

1. CNINFO filing index/document I/O;
2. official PDF parsing and versioned fact extraction;
3. deterministic fundamental mapping plus separate earnings-direction materialization.

Candidate architecture:

```text
shared inputs
   |
   |--> filing/PDF shards --> filing aggregate --> deterministic fundamental mapping --|
   |                                                                                   |
   |--> earnings shards ---------------------------------------------------------------|--> derived aggregate
```

### Filing/PDF shards

Keep symbol-based deterministic sharding.

Each shard must preserve:

- official attachment URL identity;
- document id;
- document SHA-256;
- publication timestamp;
- evidence-available date;
- parser version;
- revision identity;
- exact unit proof;
- schemaful empty outputs;
- no OCR fallback unless separately authorized.

### Fundamental mapping

This stage is deterministic transformation over verified filing facts.

It must:

- consume only sealed filing artifacts;
- not perform new network I/O;
- preserve the frozen `FUNDAMENTAL_PIT_STATE_CONTRACT_V1`;
- map missing/insufficient evidence to `DATA_INSUFFICIENT`, never fabricate WATCH/positive evidence;
- remain independent of prices, returns, or forward outcomes.

### Earnings materialization

Separate earnings-direction acquisition/mapping from filing/PDF parsing when profiling confirms meaningful overlap opportunity.

It must preserve the existing exact source identity, checkpoint contract, availability-date handling, and deterministic mapping semantics.

## Phase 4 — Avoid duplicate CNINFO index work where contract-safe

Potential optimization: reuse an upstream immutable CNINFO announcement index instead of querying the same symbol/date index again in filing materialization.

This is **not automatic**.

Before implementation, prove that the upstream stage contains every field required to reconstruct the filing query identity, including at least:

- symbol/entity identity;
- publication timestamp;
- announcement/document identity;
- immutable attachment URL identity;
- exact query window/source identity;
- revision/availability information required by the filing materializer.

If any required identity is missing or weaker than the current filing query, keep the independent query. Performance is not sufficient justification to weaken source identity.

If reuse is approved mechanically, the filing stage must consume a sealed upstream artifact and verify its receipt before use.

## Phase 5 — Derived-rail decomposition only if measured

Do not split `derived_aggregate` by default.

If profiling shows it is a material critical-path component, consider independent deterministic sub-stages such as:

- valuation rail construction;
- major-negative review/coverage;
- combined PIT ledger assembly;
- replay/no-lookahead audit.

The final derived aggregator must still re-check cross-rail invariants after all sub-stages merge.

No sub-stage may self-grant final materialization or qualification status.

## Finalization remains singular

`finalize` should remain a single logical authority.

Parallelism may prepare inputs, but finalization must retain one place that:

- verifies final handoff receipts;
- assembles the public output allowlist;
- summarizes checkpoint identities;
- computes canonical manifest/content identities;
- writes one canonical tar/bundle;
- uploads one formal materialization artifact;
- writes one external artifact identity receipt.

Do not shard canonical finalization into competing final outputs.

## Source-provider concurrency policy

Concurrency must be provider-aware.

### CNINFO

Current reference maximum parallel issuer shard count is 4.

Do **not** increase CNINFO concurrency above the current level until profiling demonstrates:

- stable response/error rate;
- no evidence of throttling;
- bounded retry behavior;
- no increase in incomplete symbol windows;
- no provider-protocol instability.

A faster runner graph that causes more retries or failed coverage is a regression.

### SSE / SZSE

Start with two shards only where measured issuer runtime justifies it. Increase only after observing stable provider behavior.

### Other providers

Use the smallest concurrency that removes the critical-path bottleneck. Unbounded fan-out is prohibited.

## Quality gates for every optimization PR

Every implementation PR must include relevant automated tests for:

### Identity and handoff

- source-commit mismatch fails;
- start/end scope mismatch fails;
- shard-id/count mismatch fails;
- file hash mismatch fails;
- missing receipt fails;
- unexpected file fails when governed by an allowlist.

### Shard completeness

- missing shard fails;
- duplicate shard fails;
- duplicate entity/date fails;
- overlapping time shard fails;
- expected-scope gap fails;
- deterministic partition test passes.

### PIT/revision safety

- future evidence rejected;
- event date vs evidence-available date remains distinct;
- later revision does not rewrite prior as-of state;
- duplicate/conflicting history fails closed;
- prefix/as-of replay remains stable.

### Canonical determinism

Under fixed fixture inputs:

- fresh and resumed paths produce the same canonical outputs;
- serial reference and parallel aggregate produce the same canonical outputs where a serial reference exists;
- artifact/runtime metadata does not contaminate canonical content identity.

### Workflow governance

- qualification workflow remains `workflow_dispatch` only unless separately authorized;
- no new automatic evidence/research trigger;
- public-tree audit passes.

## Rollout protocol

For each second-stage optimization:

1. implement on a dedicated engineering branch;
2. run unit/contract tests;
3. run public-tree audit;
4. run relevant integration/aggregation tests;
5. open PR with explicit boundary statement;
6. merge only after CI is green;
7. do not interrupt a healthy formal run merely to adopt the optimization;
8. first live execution after merge remains manual;
9. compare critical-path profile with the prior reference run;
10. retain the change only if quality is unchanged and wall-clock/failure isolation improves.

## Rollback criteria

Rollback or revise an optimization if any of the following occurs:

- canonical output semantics change unexpectedly;
- PIT/no-lookahead/revision audit weakens or fails;
- source error/timeout rate materially increases;
- provider throttling appears;
- shard skew makes added fan-out ineffective;
- artifact/setup overhead consumes most of the saved time;
- retry behavior requires broader reruns than before;
- exact-identity checkpoint safety becomes weaker;
- public/private or evidence-governance boundaries become less explicit.

## Success criteria

Quality is mandatory; speed is secondary.

The optimization program is successful when:

- all existing evidence and provenance invariants remain intact;
- failures are isolated to smaller rerunnable units;
- formal outputs remain singular and independently verifiable;
- the real critical-path wall-clock decreases materially;
- no source provider is stressed beyond stable bounded concurrency;
- maintenance complexity remains justified by measured savings.

A practical engineering target is to reduce a healthy full materialization run toward the **1.5–2 hour range** if the source providers and PDF workload permit it. This is a target, not an acceptance requirement. The system must prefer a slower correct run over a faster unverifiable one.

## Explicit exclusions

This plan does not authorize:

- new models or factors;
- parameter or threshold search;
- new stock universes;
- new holdout/OOS research;
- forward-return studies;
- probability calibration;
- evidence-eligibility changes;
- research/shadow to production promotion;
- position sizing or trading automation;
- relaxation of public/private boundaries;
- new automatic workflow triggers.
