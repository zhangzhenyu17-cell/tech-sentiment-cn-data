# Public Data Qualification Runbook

## Status

This runbook defines the preferred operating procedure for long-running public-data qualification and materialization workflows in this repository.

The V4-A Capital/PIT workflow is the current reference implementation:

- workflow: `.github/workflows/qualify-capital-inputs.yml`
- trigger: manual `workflow_dispatch` only
- reference successful preflight run: `35368616602`
- reference successful preflight commit: `9d9e0e7b66c3364701728ad9df3a11f23264e8af`

This runbook is an engineering execution contract. It does not change evidence eligibility, PIT/no-lookahead semantics, research scope, model behavior, production authority, trading authority, or public/private security boundaries.

## Operating principle

A long-running qualification job should fail cheaply, diagnose precisely, resume narrowly, and only spend material compute after representative preflights prove that the current code/source contract is reachable.

The default progression is:

1. static and provider preflights in parallel;
2. one unified gate;
3. parallel materialization stages;
4. immutable stage sealing;
5. verified aggregation;
6. one canonical finalizer.

Do not skip directly to a multi-hour materialization after a code or source-contract change when a representative preflight can prove the same failure class in minutes.

## Two execution modes

### Preflight-only

Use `preflight_only=true` after:

- source transport changes;
- parser changes;
- candidate-selection changes;
- provider API changes;
- freshness-contract changes;
- workflow/preflight engineering changes;
- any code change that affects the data path before expensive materialization.

A healthy preflight-only run must prove:

- repository/public-tree constraints;
- representative source reachability;
- immutable official document identity;
- transport fallback behavior;
- representative parser semantics;
- freshness boundaries;
- frozen scope construction;
- representative PIT/fundamental/valuation integration;
- unified gate behavior;
- expensive jobs remain skipped.

### Full materialization

Use `preflight_only=false` only after the same source commit has a healthy preflight or the change is demonstrably documentation-only and does not affect workflow/data execution.

A full run is allowed to conclude either:

- qualified according to the declared public contract; or
- honestly data-insufficient / qualification-blocked.

A full run must never be made green by relaxing evidence, provenance, freshness, PIT, or source-identity requirements.

## Preflight-production contract parity

Preflight logic must reuse production eligibility, identity, and parsing contracts wherever possible.

Do not duplicate a production rule with a looser "smoke-test version."

The September 2026 V4-A incident demonstrated why: the formal filing materializer already excluded English translations, but the preflight used a separate title filter and selected the first matching row. It therefore parsed an English annual report with a Chinese numeric-filing parser and generated repeated false parser failures.

Required rule:

> A preflight may use a smaller representative scope, but not a different eligibility or identity contract.

Examples:

- report eligibility: reuse the production report predicate;
- immutable document identity: reuse the production announcement/document identity parser;
- date availability: reuse the production PIT availability rule;
- unit semantics: reuse the production fact parser;
- source allowlists: reuse production host/source checks.

If representative test code needs a narrower selector, it should narrow *after* production eligibility, not replace it.

## Failure taxonomy

Every failure should be classified into the earliest applicable category before code is changed.

### 1. REPOSITORY_CONTRACT

Examples:

- public-tree audit failure;
- forbidden file/source present;
- workflow trigger or boundary violation;
- schema/reference file mismatch.

Action: fix repository/workflow engineering. Do not touch data semantics.

### 2. TRANSPORT

Examples:

- timeout;
- connection reset;
- HTTP 403/WAF;
- non-JSON challenge page;
- TLS/provider-session instability.

Action:

- bounded retry/backoff;
- same-provider HTTPS session warm-up;
- browser-fingerprint transport when justified;
- alternate official interface on the same provider/identity contract when verified.

Do not silently switch to a third-party source or HTTP downgrade.

### 3. CANDIDATE_SELECTION

Examples:

- English translation chosen instead of Chinese primary report;
- summary chosen instead of full report;
- row-order-dependent selection;
- duplicate API rows mistaken for distinct versions.

Action:

- reuse production eligibility;
- deduplicate immutable identities;
- sort by explicit official publication order;
- fail closed on true same-time ambiguity.

Never resolve ambiguity by source row order.

### 4. DOCUMENT_IDENTITY

Examples:

- wrong issuer;
- wrong report period;
- wrong attachment;
- redirect outside official allowlist;
- document hash/identity inconsistency.

Action: stop and repair identity resolution. Do not loosen identity guards.

### 5. TEXT_LAYER

Examples:

- official PDF bytes are correct but one parser engine loses text;
- unit label split by layout extraction;
- Unicode compatibility/control artifacts.

Action:

- stay on the same immutable PDF bytes;
- use bounded non-OCR text-layer parser fallbacks;
- normalize only layout/Unicode artifacts;
- preserve semantic proof requirements.

OCR or image inference requires separate authorization if it changes the evidence contract.

### 6. SEMANTIC_PARSE

Examples:

- fact label split by table layout;
- numeric cell interrupts visible label;
- explicit per-share unit layout differs;
- real fact remains missing after usable text extraction.

Action:

- add a minimal real-layout regression fixture;
- keep local unit scope;
- preserve fail-closed missing-fact behavior;
- never hard-code issuer values.

### 7. FRESHNESS / SOURCE_BOUNDARY

Examples:

- latest requested boundary unavailable;
- stale source;
- date mismatch;
- no exact observation for a frozen boundary.

Action: distinguish transport failure from real unavailability. If the data is genuinely insufficient, preserve `DATA_INSUFFICIENT`; do not forward-fill/backfill unless the frozen contract explicitly authorizes it.

### 8. PIT / INTEGRATION_CONTRACT

Examples:

- evidence available before publication;
- revision rewrites prior state;
- fundamental comparable coverage incomplete;
- valuation integration produces no valid evidence;
- stage receipt or source-commit mismatch.

Action: stop the pipeline and repair the invariant. Do not weaken the check.

## Minimum diagnostic envelope

Representative source/parser preflights should expose enough identity to diagnose a failure without a second blind run.

Where applicable, include:

- preflight role;
- symbol/entity;
- query window;
- raw candidate count;
- eligible candidate count;
- selected title/type;
- immutable document/announcement id;
- canonical source URL;
- actual retrieval URL;
- document SHA-256;
- parser version;
- transport/interface used;
- exact missing facts or failed invariant;
- readiness/contract id for integration probes.

Long-running stages should additionally expose:

- stage/shard id;
- expected and completed scope counts;
- executed vs resumed checkpoint counts;
- structured errors;
- output record counts;
- stage receipt identity.

The workflow persists small `v4a-preflight-*-diagnostics` artifacts so a failed run can be diagnosed without rerunning it.

## Transport fallback policy

A transport fallback is allowed only when it preserves the same evidence identity and semantics.

Preferred order:

1. ordinary official HTTPS;
2. bounded retry;
3. official session/cookie warm-up;
4. browser-fingerprint HTTPS to the same official host;
5. verified sibling interface on the same official provider using the same query identity/fields.

Every accepted redirect must remain on the official HTTPS allowlist.

A fallback must not:

- change issuer/document/report identity;
- substitute a mirror;
- use an unofficial aggregator as canonical evidence;
- silently change date or query scope;
- weaken field semantics.

If all allowed transports fail, report transport failure. Do not report "no data."

## Parser repair policy

For every real parser failure:

1. verify the selected document identity first;
2. verify bytes are a PDF and bind SHA-256;
3. verify a usable text layer exists;
4. distinguish text-layer extraction from semantic parsing;
5. add a representative regression test before or with the fix;
6. keep the fix as narrow as the observed layout requires;
7. preserve negative tests that prove fail-closed behavior.

Do not keep modifying the parser when diagnostics show that the wrong document was selected.

## Rerun decision matrix

### Same SHA, transient platform/provider failure

Prefer:

- rerun failed job(s), if the exact execution identity and upstream artifacts remain valid;
- otherwise rerun the workflow only when the platform cannot isolate the failed unit.

### Code changed

Do not rerun the old workflow attempt as validation of the new code.

Create a new run on the new SHA.

### Preflight failure

Do not start full materialization until the failure class is repaired or explicitly determined to be genuine data insufficiency.

### Full materialization stage failure

Preserve successful stage artifacts/checkpoints. Prefer failed-stage-only recovery where exact identity permits. Do not rerun completed one-off evidence/research tasks merely to make the workflow green.

## Regression rule

Every non-transient production-like failure that leads to a code change should leave behind at least one regression test or contract assertion.

Examples from V4-A:

- CNINFO 403 transport -> same-provider browser fallback tests;
- SSE non-JSON/403 -> official-interface fallback tests;
- wrapped Chinese labels -> PDF layout regression tests;
- unit labels split by whitespace -> unit-proof tests;
- English annual report selected -> candidate-selection parity tests.

The test should encode the invariant, not one issuer's numeric values unless the values are part of an explicit fixture.

## Speed without quality loss

Preferred speed improvements:

- parallel independent preflights;
- unified fast-fail gate;
- short provider-specific timeout budgets;
- deterministic shards;
- source-aware bounded concurrency;
- exact-identity checkpoints;
- immutable artifact handoff;
- failed-job-only retry;
- representative smoke integration before full history;
- persistent diagnostic artifacts.

Avoid:

- broad retries that hide deterministic failures;
- repeated full-history runs to diagnose a five-minute failure;
- unbounded provider fan-out;
- lowering validation intensity;
- changing source/evidence semantics to make CI green.

## Time-budget guidance

The objective of preflight is to discover likely full-run blockers before expensive work starts.

Guideline:

- repository/static: under ~10 minutes;
- individual provider probe: under ~10 minutes;
- shared/freshness preflight: under ~20 minutes;
- gate/report: under ~2 minutes.

If a preflight itself becomes a material historical computation, it is no longer a preflight and should be redesigned.

## Stop and escalation rules

Stop automatic mechanical repair and request explicit authorization if the required fix would change:

- canonical source eligibility;
- evidence eligibility;
- PIT/no-lookahead semantics;
- model/factor/threshold definitions;
- research universe;
- holdout/OOS/evidence qualification;
- public/private security boundary;
- production/trading authority;
- workflow automation triggers beyond the existing allowlist.

If an official alternate source is desired as canonical evidence rather than transport redundancy, treat that as an evidence-source architecture decision.

## Successful preflight acceptance

A preflight is accepted only when:

- every required preflight job succeeds;
- the unified gate succeeds;
- representative integration probes reach their expected contract states;
- shared freshness/scope checks succeed;
- the run uses the intended source commit;
- preflight-only mode correctly skips expensive materialization.

A green workflow caused by skipped/disabled checks is not acceptance.

## Full-run completion report

At the end of a formal materialization run, report:

- source commit and run id;
- stage/job status;
- final qualification/materialization state;
- canonical artifact and receipt identities;
- whether any stage resumed from checkpoint;
- whether research/evidence boundaries were touched;
- known limitations or incomplete coverage;
- measured critical path for future optimization.

## Related documents

- [Parallel Execution Architecture](parallel_execution_architecture.md)
- [Parallel Execution Optimization Plan V2](parallel_execution_optimization_plan_v2.md)
- [V4-A Preflight Incident Review](v4a_preflight_incident_review_2026-09.md)
