# Parallel Execution Architecture for Long-Running Public Data Pipelines

## Status

This document defines the preferred engineering pattern for long-running, decomposable, independently verifiable public-data workflows in this repository.

The V4-A Capital/PIT materialization workflow is the current reference implementation. This document records the reusable architecture; it does **not** change evidence eligibility, research scope, model behavior, production authority, or trading authority.

## When to use this architecture

Prefer a parallel DAG when a workflow has all or most of the following characteristics:

- wall-clock runtime is material;
- multiple stages can execute independently after a small shared preflight;
- stages can be partitioned into deterministic shards;
- each stage can emit immutable outputs and an independently verifiable receipt;
- failures should be recoverable without rerunning already completed work;
- final acceptance requires one canonical aggregate rather than multiple competing outputs.

Do not force parallelism when stages share mutable state, have unavoidable sequential dependencies, or cannot be independently verified.

## Reusable manual-stage architecture

V4-A no longer treats one GitHub Actions run as the unit of successful historical materialization. The unit of reuse is an immutable stage bundle.

A stage bundle is accepted across repository commits only when the current code independently recomputes the same stage-specific producer fingerprint, date window, and upstream bundle identities. The original stage commit remains recorded in lineage, while the final canonical public artifact is bound to the current finalizer commit.

Persistent inter-workflow handoff uses the public release registry `v4a-stage-bundles-v1`. GitHub Actions cache remains engineering resume acceleration only and is not a canonical stage handoff.

Engineering resume state may be layered: current semantic-generation progress,
exact audited compatibility bridges, and read-only legacy/query/index caches.
Those layers never grant qualification. Query/index caches are restored
independently from document/parser progress so a parser cache hit cannot
accidentally re-enable years of provider pagination.

The manual stage order, failure recovery matrix, and trigger policy are defined
in [V4-A Manual Reusable Stage Runbook](v4a_manual_reusable_stage_runbook.md).
The repository-wide planning/cancellation/monitoring rules are defined in
[Long-Running Engineering Execution Protocol](long_running_engineering_execution_protocol.md).

This architecture deliberately prevents a Policy-only fix from invalidating Capital, Financing, Prices, or unrelated issuer bundles. Conversely, a change to Shared/frozen scope invalidates all bundles that name that Shared bundle identity as an input.

## Core design

A compliant long-running pipeline should normally have five layers:

1. **Preflight**
   - validate repository/public-tree constraints;
   - run relevant tests;
   - validate reachability and source connectivity;
   - resolve the immutable execution window;
   - build shared deterministic inputs such as a real trading calendar or frozen symbol scope.

2. **Parallel materialization**
   - run independent source or data-family jobs concurrently;
   - use matrix shards when a source can be partitioned deterministically;
   - cap source-specific concurrency where needed to avoid overloading public providers.

3. **Progress persistence and stage sealing**
   - long units persist engineering progress before their qualification gate;
   - failure diagnostics are preserved before the gate where practical;
   - each qualified job/work unit emits an immutable artifact plus receipt;
   - the receipt binds the artifact to the exact execution identity;
   - completed qualified units are published immediately instead of waiting for
     all siblings;
   - incomplete or inconsistent stages fail closed.

4. **Aggregation**
   - consume stage artifacts, not mutable caches;
   - verify every receipt, file hash, scope, schema, source commit, and shard coverage;
   - reject missing shards, duplicate scope, conflicting identities, or hash drift;
   - re-run end-to-end invariants that cannot be proven by individual shards alone.

5. **Canonical finalization**
   - produce one canonical bundle and one external artifact receipt;
   - keep runtime/upload identity outside canonical content where required for deterministic hashing;
   - downstream private verification remains independent from public materialization success.

## Preflight-production contract parity

Preflight may narrow scope, but it must reuse production eligibility, identity, availability, and parsing contracts wherever possible.

A representative smoke test must not maintain a looser parallel selector simply because it processes fewer records. This includes:

- document/report eligibility;
- immutable document identity;
- publication/evidence-available ordering;
- source/host allowlists;
- fact/unit parsing semantics;
- freshness and exact-date rules.

If a preflight needs a narrower representative subset, narrow after applying the production contract.

This rule exists because row-order-dependent or independently reimplemented smoke logic can validate the wrong object even while production code is correct.

## Diagnostic artifact contract

Every long-running workflow should preserve enough diagnostics to explain a failure without rerunning it.

For representative preflights, persist a small diagnostic artifact on both success and failure where practical. The diagnostic should expose the failure class and the relevant identity envelope rather than only raw stack traces.

For document-driven probes, the preferred envelope includes:

- role/entity/query window;
- raw and eligible candidate counts;
- selected title/document id;
- canonical and retrieval URLs;
- content hash;
- parser/version identity;
- exact failed invariant.

GitHub job summaries should expose a quick preflight status table, while detailed diagnostic artifacts remain available for postmortem inspection.

## Artifact and checkpoint contract

### Checkpoints

Checkpoints exist only to support resume/retry of a producer.

They must:

- be exact-identity within their declared semantic generation;
- bind producer/version, query/scope identity, and relevant source identity;
- distinguish operational execution identity from semantic materialization identity;
- restore only when the declared compatibility contract matches;
- never silently bridge incompatible parser/evidence/PIT/scope semantics;
- use an explicit, exact, fail-closed compatibility bridge for a proven
  non-semantic cross-commit repair;
- keep query/index cache restoration independent from document/parser progress;
- remain safe to ignore without changing canonical output semantics.

A cache is an optimization, not evidence. A compatibility bridge is engineering
resume policy, not permission to weaken a producer fingerprint globally.

### Stage artifacts

Stage artifacts are the formal cross-job transport.

Each stage receipt should bind at least:

- stage kind and stage id;
- source commit;
- start/end scope;
- schema/version identity where applicable;
- each managed file path;
- each file SHA-256;
- file size/byte count;
- deterministic shard identity when sharding is used.

A consumer must verify the receipt before reading the stage.

### Why cache is not the data bus

GitHub Actions cache is optimized for reuse, not for formal immutable handoff. A downstream job must not infer evidence identity from a cache hit. Formal cross-job inputs travel through immutable artifacts plus receipts; cache remains limited to producer-local resume semantics.

## Sharding rules

A shard partition must be deterministic and reviewable.

Required properties:

- the shard count and shard-index rule are explicit;
- every eligible entity belongs to exactly one shard;
- aggregate scope equals the frozen expected scope;
- no entity is duplicated across shards;
- shard failure cannot be interpreted as empty-but-valid data;
- the aggregator proves completeness rather than assuming matrix success implies completeness.

Changing shard count is an engineering execution change, not permission to change the underlying research/data universe.

## Dependency rules

Parallel jobs should depend only on true prerequisites.

Typical pattern:

```text
preflight/shared inputs
        |
        +--> source A shard 0..N
        +--> source B
        +--> source C
        +--> capital
        +--> financing
        +--> prices
        +--> policy
                 |
          stage aggregators
                 |
            finalizer
```

Avoid artificial serialization between jobs that only share immutable preflight inputs.

## Fail-closed behavior

The pipeline must not convert missing data or execution failure into a positive qualification state.

Examples that must fail closed:

- missing shard artifact;
- receipt/source-commit mismatch;
- unexpected or duplicate entity coverage;
- schema mismatch;
- hash mismatch;
- incomplete provider coverage;
- duplicate/conflicting point-in-time history;
- missing provenance;
- impossible or unverifiable no-lookahead invariants.

A public workflow may report successful **materialization** only according to its public contract. It must not self-grant any private qualification state.

## Retry and repair policy

When a failure occurs:

1. identify the failed stage/work unit and failure class;
2. preserve successful immutable stage/work-unit artifacts;
3. inspect whether active units saved durable progress before assuming loss;
4. prefer the narrowest compatible checkpoint/progress resume;
5. make only mechanical engineering fixes needed to restore the declared contract;
6. add a regression test for deterministic code/orchestration failures, including
   the actual CLI entrypoint when that path failed;
7. after a code change, determine whether the change is semantic or operational;
8. use an exact audited compatibility bridge only when the old progress is proven
   semantically compatible;
9. rerun only non-immutable units and affected downstream aggregators.

Do not rerun completed one-shot research/evidence work merely to make CI green.

## Aggregator responsibilities

An aggregator is a verifier, not a concatenation script.

It should independently prove, as applicable:

- expected artifact set is complete;
- every stage receipt matches source commit and execution window;
- per-file hashes match;
- shard identities are unique and complete;
- entity scope has no gaps or overlaps;
- schemas are compatible;
- provenance is complete;
- point-in-time/no-lookahead constraints still hold after merge;
- revision/append-only invariants remain valid;
- canonical output is deterministic.

If any required proof fails, aggregation stops.

## Canonical output rule

Parallel execution may create many intermediate artifacts, but formal output remains singular.

The preferred pattern is:

- many immutable stage artifacts;
- one verified aggregate;
- one canonical materialization bundle;
- one post-upload artifact identity receipt;
- one downstream verification entrypoint.

Intermediate shards must never become competing canonical products.

## Workflow trigger governance

Long-running qualification/materialization workflows remain manual-only unless a separate governance decision explicitly authorizes another trigger.

Adding `schedule`, `workflow_run`, `push`, or `pull_request` automation is not implied by adopting this architecture.

Parallelization is an execution optimization only. It does not broaden:

- research scope;
- evidence eligibility;
- model/factor definitions;
- thresholds or signals;
- holdout/OOS authority;
- production authority;
- trading authority;
- public/private security boundaries.

## Source-provider concurrency

Parallel execution must respect the operational characteristics of public providers.

Use bounded concurrency when necessary:

- avoid unbounded simultaneous requests to one provider;
- prefer deterministic shard counts;
- use bounded retry/backoff for transient network/protocol errors;
- distinguish source unavailability from a valid empty result;
- preserve provider/source identity and immutable document identity.

More runner capacity is not permission to increase research scope or stress a provider irresponsibly.

## Observability

Every long-running stage should expose enough information to diagnose failure without rerunning the entire pipeline.

At minimum, record:

- stage/work-unit identity;
- source and scope identity;
- complete/failed entity counts;
- materialized record counts;
- resumed vs executed source-query counts;
- resumed vs executed document counts;
- parser-upgrade counts where applicable;
- structured soft/hard error output where feasible;
- progress-generation/compatibility identity;
- checkpoint save outcome;
- immutable publication outcome;
- output/receipt identity.

A summary that only says "failed" is insufficient for a multi-hour pipeline.

## Cost and wall-clock guidance

Optimize wall-clock by removing unnecessary dependencies, not by weakening validation.

Good optimizations:

- independent jobs;
- deterministic matrix shards;
- checkpoint/resume;
- artifact reuse inside the same execution identity;
- failed-job-only retry;
- avoiding redundant merge-after-PR full CI when policy permits.

Unacceptable optimizations:

- dropping provenance checks;
- reducing PIT/no-lookahead validation;
- silently reusing checkpoints across incompatible commits;
- skipping required stage aggregation;
- lowering evidence requirements;
- moving private material into public compute.

## Reference implementation

The current reference implementation includes:

- manual V4-A reusable-stage workflows;
- shared preflight stage;
- bounded provider concurrency;
- Fundamental/Earnings as 16 deterministic work units with `max-parallel: 4`;
- durable progress saved before the Fundamental unit gate;
- immutable qualified Fundamental work-unit bundles;
- independent query/index and document/parser resume layers;
- independent Capital, financing, issuer-source, price, and policy stages;
- immutable stage receipts;
- issuer and derived aggregation;
- single final canonical bundle and external artifact receipt.

The implementation may evolve, but changes should preserve the invariants in this document.

## Review checklist for future long-running workflows

Before accepting a new or refactored long-running pipeline, confirm:

- [ ] only true dependencies are serialized;
- [ ] deterministic shard partitioning is documented;
- [ ] provider concurrency is bounded;
- [ ] each stage has an immutable receipt;
- [ ] stage consumers verify receipts before use;
- [ ] cache is used only for resume, not formal handoff;
- [ ] aggregator proves completeness and uniqueness;
- [ ] fail-closed semantics are preserved;
- [ ] canonical output remains singular;
- [ ] triggers comply with workflow governance;
- [ ] no research/evidence/production/trading boundary changed implicitly;
- [ ] source-commit / semantic-generation identity and retry semantics are explicit;
- [ ] long materialization progress is persisted before the qualification gate;
- [ ] qualified completed units are persisted immutably without waiting for siblings;
- [ ] query/index cache restore is independent from document/parser progress;
- [ ] deterministic CLI entrypoints have direct regression coverage;
- [ ] timeout is documented as a per-job safety ceiling;
- [ ] cancellation-safe checkpoint behavior and restart criteria are documented;
- [ ] any cross-commit compatibility bridge is exact, fail-closed, and expiring.

## Operating runbook

Operational execution, failure classification, rerun decisions, cancellation,
monitoring, compatibility bridges, and minimum diagnostics are defined in
[Long-Running Engineering Execution Protocol](long_running_engineering_execution_protocol.md)
and [Public Data Qualification Runbook](public_data_qualification_runbook.md).

The September 2026 lessons are recorded in
[V4-A Preflight Incident Review](v4a_preflight_incident_review_2026-09.md) and
[V4-A Fundamental Long-Run Incident Review](v4a_fundamental_long_run_incident_review_2026-09-19.md).

## Optimization roadmap

Further wall-clock optimization must follow the measured, quality-gated plan in [Parallel Execution Optimization Plan V2](parallel_execution_optimization_plan_v2.md). Additional sharding or producer decomposition is adopted only after critical-path profiling and contract-preserving tests.
