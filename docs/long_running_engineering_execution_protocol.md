# Long-Running Engineering Execution Protocol

## Status

This document defines the default engineering operating protocol for long-running,
restartable GitHub Actions work in this repository.

It incorporates the September 18-19, 2026 execution incidents and recovery work.
It is an engineering-control document only. It does not change evidence
eligibility, PIT/no-lookahead semantics, research scope, model behavior,
production authority, trading authority, or the public/private security boundary.

## Why this protocol exists

Recent long runs exposed a recurring failure pattern:

1. a workflow ran for a long time before revealing a deterministic bug;
2. successful work was coupled to a larger failing run and had to be repeated;
3. checkpoint persistence happened too late;
4. changing an operational setting accidentally invalidated reusable progress;
5. restoring parser progress accidentally skipped a different cache layer
   required to avoid expensive provider queries;
6. a qualification CLI path was not tested even though its helper functions were;
7. a later engineering fix changed the semantic fingerprint even though the
   materialization semantics had not changed;
8. cancellation decisions were made without first proving what would persist;
9. exact cache keys were correct but still unreadable because the replacement run
   started from a different Git ref than the branch that created the cache;
10. a restore bridge was initially verified only structurally, not by executing
    the actual workflow resolver path;
11. real recovery performance was not considered proven until counters showed
    zero provider queries and zero document re-download/reparse work.

The protocol below makes those failure modes first-class design constraints.

## Default state machine

A long-running engineering task should normally move through these states:

```text
DESIGN
  -> PREFLIGHT
  -> PILOT / REPRESENTATIVE EXECUTION
  -> PARTITIONED MATERIALIZATION
  -> PER-UNIT SEAL
  -> PER-UNIT QUALIFICATION GATE
  -> IMMUTABLE UNIT PUBLICATION
  -> GROUP ASSEMBLY
  -> CANONICAL FINALIZATION
```

Do not begin expensive full materialization until DESIGN and PREFLIGHT acceptance
criteria are satisfied.

A smaller pilot may reduce scope for engineering validation, but it must reuse
the same production eligibility, identity, availability, parsing, and
qualification contracts. A pilot must not become an alternative evidence rule.

## Mandatory run plan before the first expensive run

Before launching a long run, write down the following:

- exact workflow and branch/SHA;
- immutable start/end window;
- frozen scope identity;
- expected work-unit count and partition rule;
- maximum parallelism;
- timeout **per job**, not only a workflow-level expectation;
- expected wall-clock range from measured or closest comparable runs;
- what is the smallest reusable unit of success;
- where partial progress is saved;
- where qualified completed units are persisted immutably;
- restore precedence;
- cancellation-safe checkpoint point;
- failure classes and response for each;
- acceptance criteria;
- authorization boundary;
- stop condition.

Use [Long-Running Run Plan Template](templates/long_running_run_plan.md).

## Reuse hierarchy

Reuse must be layered. Do not collapse distinct cache purposes into one condition.

Preferred order:

1. **Immutable completed work-unit bundle**
   - highest-priority reuse;
   - independently verified;
   - already passed the unit qualification gate;
   - may be reused across compatible repository commits.

2. **Current semantic-generation durable progress**
   - engineering resume only;
   - may contain document/query checkpoints;
   - never grants qualification.

3. **Explicitly audited compatibility bridge**
   - allowed only for a narrowly proven non-semantic change;
   - exact run/cache/source/fingerprint/window identity must be frozen;
   - fail closed on any mismatch;
   - has a removal/expiry condition.

4. **Legacy/query/index cache**
   - read-only fallback;
   - restores expensive source index/query results independently from parser
     progress;
   - must not be skipped merely because a parser/document progress cache hit.

5. **Provider/network recomputation**
   - last resort.

A cache hit at one layer must not suppress a different independent cache layer
unless the workflow proves the suppressed layer is unnecessary.

## Cache visibility is part of the restore identity

A cache key is not sufficient proof that a cache is reusable. GitHub Actions
cache visibility is also constrained by Git ref/branch scope.

For every cache-backed recovery plan, record and validate:

- the exact branch/ref that created the cache;
- the exact run id and run attempt;
- the source commit;
- the semantic generation and fingerprint/key;
- the date window and work-unit identity;
- whether the intended replacement run can actually read that cache from its
  dispatch ref.

If reusable caches were created on a recovery branch, the preferred procedure is:

1. finish or harvest the current run;
2. verify progress-save outcomes;
3. fast-forward the recovery branch to the intended latest main commit when the
   history is linear and the update is safe;
4. dispatch the replacement run from that same recovery branch;
5. fail fast before materialization if the workflow is launched from a ref that
   cannot access the required recovery caches.

Do not solve cache-scope visibility by copying data blindly between branches or
by weakening cache identity checks.

A branch/ref guard is orchestration-only when it changes no materialization,
PIT, evidence, qualification, model, Production, or trading semantics. Such a
guard should be execution-tested with both the allowed-ref success path and the
wrong-ref fail-fast path.

## Separate semantic identity from operational configuration

Operational-only changes should not force expensive data recomputation when the
materialization semantics are unchanged.

Examples of operational changes:

- job timeout;
- runner orchestration;
- cache plumbing;
- diagnostic upload behavior;
- non-semantic CI wiring.

Examples of semantic changes:

- parser version or parser behavior;
- evidence eligibility;
- PIT/availability logic;
- source identity;
- qualification rules;
- frozen symbol scope;
- query window;
- materialized schema.

When a non-semantic change still alters a producer fingerprint, cross-version
reuse requires an explicit compatibility bridge. Never silently weaken the
fingerprint or make all commits mutually reusable.

## Work-unit sizing

The unit of execution should be small enough that a timeout or cancellation does
not destroy hours of work.

Guidelines:

- partition deterministically;
- every entity belongs to exactly one unit;
- keep provider concurrency bounded independently of unit count;
- prefer more bounded units with limited parallelism over a few multi-hour
  monoliths;
- size units using measured critical-path time, not only entity count;
- after the first representative units complete, revise the wall-clock forecast
  using observed throughput.

A job timeout is a **per-job safety ceiling**. It is not a target duration.
Repeated units approaching the timeout indicate a decomposition or performance
problem.

After a recovery architecture has real measurements, estimate time by execution
class instead of applying one average to every unit:

- **fully resumed units**: query/document execution should normally be zero and
  wall-clock is dominated by reconstruction, validation, qualification, and
  publication;
- **targeted semantic-repair units**: may execute bounded re-proof or parser work
  only for the affected subset;
- **provider-recompute units**: the slow fallback and the class that should drive
  the conservative tail estimate.

A mixed run should forecast its critical path from the count of units in each
class and the configured parallelism. Do not extrapolate a repaired unit's time
to fully resumed siblings, or vice versa.

## Progress persistence contract

For long materialization steps:

- write document/query checkpoints incrementally;
- persist engineering progress before the qualification gate;
- use an `always()`-style save path where cancellation/failure semantics allow;
- preserve failure diagnostics before the gate;
- publish an immutable completed-unit bundle immediately after the gate passes;
- never wait for all sibling units before persisting a successfully qualified
  reusable unit.

The expected maximum recomputation loss should be approximately one currently
running work unit per active runner, not an entire matrix or workflow.

## Query cache and document cache are different assets

Historical source-index queries and parsed-document checkpoints solve different
problems.

A parsed-document cache may eliminate downloads and parsing while a missing query
cache still forces expensive pagination across years of provider history.

Therefore:

- restore query/index caches independently;
- record query resume/execution counters;
- record document resume/execution counters;
- do not infer "fully resumed" from document checkpoint hits alone;
- performance debugging must distinguish provider-query time from document
  parsing and aggregate reconstruction time.

## Preflight and pilot requirements

Before expensive scale-out after a data-path or orchestration change:

- public-tree/security audit passes;
- relevant unit/integration tests pass;
- representative provider path is exercised;
- exact production selector/identity logic is reused;
- cache restore path is exercised;
- cache branch/ref visibility is explicitly verified when recovery depends on
  GitHub Actions cache;
- cancellation/save behavior is known;
- qualification CLI entrypoint is executed in a test, not only helper functions;
- any workflow-inline restore/resolver guard that controls expensive execution is
  executed in a regression test, not only inspected as YAML text;
- immutable publication and re-download verification are tested when changed.

For a workflow-only orchestration change, do not rerun historical research to
validate the change. Use engineering fixtures, existing compatible bundles, and
representative execution paths.

## Gate testing rule

Every gate or finalizer CLI used by a long-running workflow must have at least
one test that executes its actual entrypoint path with representative arguments.

Testing only an internal helper is insufficient.

The regression test should prove:

- argument parsing works;
- payload rendering works;
- success exit behavior works;
- blocker/failure exit behavior works where applicable.

This rule exists because a one-line CLI-only variable error can invalidate every
unit after hours of correct materialization.

## Diagnostics before failure

Diagnostics are produced **before** the qualification gate whenever practical.

For every work unit, preserve:

- work-unit/shard id;
- exact scope;
- source commit;
- semantic/progress generation identity;
- query resume vs executed counts;
- document resume vs executed counts;
- parser-upgrade counts;
- soft vs hard failure counts;
- qualification readiness;
- checkpoint save result;
- immutable bundle publication result.

A failure that cannot be diagnosed without rerunning the provider path is an
observability defect.

A recovery run is not considered operationally validated merely because cache
restore steps are green. Confirm the expensive path was actually avoided using
counters. For a fully resumed unit, the preferred proof is:

- provider/source query executed count = 0;
- document download/parse executed count = 0;
- resumed query/document counts are non-zero and plausible for the unit;
- qualification is rerun under the current semantics;
- the current-generation immutable unit is published after the gate.

If a semantic repair requires targeted re-proof, non-zero work is acceptable
only for the explicitly affected subset and should be visible in a dedicated
counter such as presentation-conflict recheck documents.

## Failure classification and response

### Deterministic code/orchestration bug

Examples: NameError, bad workflow condition, wrong path, missing variable.

Response:

1. preserve current progress;
2. identify whether the bug is before or after progress save;
3. add a regression test reproducing the exact failure path;
4. repair the smallest scope;
5. assess semantic fingerprint impact;
6. add an exact compatibility bridge only if the change is proven
   non-semantic;
7. rerun only what is not already immutably complete.

### Provider transport/protocol failure

Response:

- classify transport separately from data absence;
- preserve official source identity;
- use only approved same-provider transport fallback;
- keep retries bounded;
- do not loosen parsing/evidence requirements.

### Parser/layout failure

Response:

- verify exact document identity and SHA first;
- preserve explicit unit/evidence requirements;
- fix layout representation only;
- add a real-layout regression fixture.

### Qualification blocker

Response:

- distinguish hard provenance/integrity failure from explicit row-level data
  insufficiency;
- do not change a frozen qualification rule merely to make CI green;
- any qualification-boundary change requires the appropriate authorization.

### Performance regression

Response:

- identify the missing cache or repeated phase;
- compare query/document resume counters;
- inspect restore conditions for unintended coupling;
- repair orchestration without changing semantic identity when possible;
- do not solve a performance problem by dropping validation.

### Publication/aggregation failure

Response:

- retain already-published immutable units;
- repair publication/aggregation only;
- never rerun completed materialization solely to restore a green status.

## Cancellation decision protocol

Cancellation is a controlled engineering action.

Before cancellation, answer:

1. Which completed units are already immutable?
2. Which active units have durable progress already saved?
3. Does cancellation trigger a final progress-save step?
4. Is there any one-time evidence/research computation that must not be repeated?
5. Is a fixed/new SHA materially better than the current run?
6. Is the current run deterministically unable to succeed?

Cancel when, for example:

- a deterministic global bug will make every remaining unit fail;
- a newer mechanical fix removes a known large performance regression;
- completed units are immutable and active work is cancellation-safe.

Do not cancel only because a job is slow if its progress is not yet safely
persisted and no deterministic blocker exists.

After cancellation, inspect every active unit and record whether its progress
save actually succeeded before launching the replacement run.

## Code-change decision protocol during a live run

A running workflow is bound to its original SHA. Merging a fix does not alter it.

For every live-run repair, explicitly choose one:

- **continue current run** because the fix affects only future runs and the
  current run can still succeed;
- **continue current run for progress harvesting** because it cannot succeed but
  safely saves useful progress;
- **cancel and restart** because the deterministic blocker or performance
  regression dominates any remaining value.

Do not assume that a merged fix repairs an already-running job.

## CI discipline

For normal engineering repairs:

- consolidate related changes into one PR when practical;
- cancel/ignore superseded PR CI;
- use the latest head as the only authoritative CI result;
- rerun only failed jobs when a transient retry is appropriate;
- avoid duplicate full CI after a sufficiently validated PR merge;
- never rerun completed one-time research/evidence work for cosmetic green CI.

## Monitoring protocol

Monitor meaningful state transitions, not just elapsed time.

Notify on:

- unit materialization completion;
- durable progress save;
- qualification gate pass/fail;
- immutable unit publication;
- new hard failure;
- package/aggregate start or completion;
- whole-run completion/cancellation;
- material performance regression relative to expected unit time.

After the first full new batch completes, update the remaining wall-clock
estimate from observed throughput.

## Compatibility bridge requirements

An engineering compatibility bridge is exceptional and must include:

- source run id and run attempt;
- source branch/ref and its cache-visibility relationship to the replacement run;
- source commit;
- old semantic fingerprint/key;
- exact date window;
- exact cache/bundle identity or key template;
- narrowly stated reason compatibility is safe;
- explicit invariants that did not change;
- fail-closed validation;
- `formal_evidence_handoff=false`;
- `qualification_granted=false`;
- removal/expiry condition.

Never create a generic "ignore fingerprint" path.

## Post-success handoff freeze

A successful stage can create a temporary **producer-identity freeze window**.

When a persistent group bundle has been published and downstream workflows locate
it by producer fingerprint / compatibility key, do not immediately clean up
one-time recovery code if that cleanup changes any file included in the producer
fingerprint.

Required sequence:

1. record the successful run id, source commit, asset base, bundle identity,
   compatibility key, archive SHA-256, and stage-receipt SHA-256;
2. identify every file that contributes to the producer fingerprint;
3. freeze those producer files until the required downstream aggregate,
   finalizer, or intake has successfully consumed and recorded the bundle;
4. permit documentation/test changes only when they are outside the producer
   identity and do not alter the published bundle contract;
5. mark obsolete bridges/guards as **logically retired but physically deferred**
   when their removal would change the producer fingerprint;
6. remove them later through a separately reviewed cleanup after downstream
   handoff no longer depends on the frozen identity.

"All jobs are green" is not sufficient reason to mutate a just-published
producer. A cleanup that makes a valid immutable bundle undiscoverable by the
next stage is a regression even if the cleanup itself is semantically harmless.

Stage acceptance and pipeline acceptance are different. For a qualification
pipeline, the ladder is normally:

`unit qualification -> group bundle -> downstream assembly -> canonical
finalizer -> private intake / final qualification state`.

Before dispatching a downstream consumer, recompute/resolve the expected upstream
producer identity from the current repository state and compare it with the
recorded published bundle. If the asset base, compatibility key, bundle identity,
or required input identities no longer match:

- do not automatically rerun the expensive upstream producer;
- first identify the exact producer-file drift;
- determine whether the drift is intentional and semantic or merely cleanup;
- prefer reverting/defering accidental cleanup when the published handoff is
  still the intended canonical input.

Do not interpret a successful public materialization stage as the final
historical/evidence qualification unless the governing intake contract says so.

## Acceptance checklist

Before declaring a long-running engineering task complete:

- all required units are accounted for;
- every successful reusable unit is immutable;
- no missing unit is treated as valid empty data;
- aggregate verifies uniqueness/completeness;
- diagnostics are preserved;
- current SHA and lineage are recorded;
- published group bundle identity / compatibility key / archive hash are recorded;
- any downstream producer-identity freeze window is declared;
- relevant CI is green;
- no research/evidence/production/trading boundary changed implicitly;
- known limitations are documented;
- no unnecessary follow-on work is started after acceptance.

## Stop rule

Once the requested engineering acceptance criteria are met, stop.

Do not use remaining runner capacity as a reason to widen research scope, tune
models, change thresholds, introduce new automated triggers, or perform unrelated
architecture work.

## Related documents

- [V4-A Preflight Incident Review](v4a_preflight_incident_review_2026-09.md)
- [V4-A Fundamental Long-Run Incident Review](v4a_fundamental_long_run_incident_review_2026-09-19.md)
- [V4-A Manual Reusable Stage Runbook](v4a_manual_reusable_stage_runbook.md)
- [Parallel Execution Architecture](parallel_execution_architecture.md)
- [Public Data Qualification Runbook](public_data_qualification_runbook.md)
