# V4-A Fundamental Long-Run Incident Review — 2026-09-19

## Purpose

This document records the execution failures and recovery architecture learned
from the V4-A Fundamental/Earnings historical materialization work on
September 18-19, 2026.

The purpose is to preserve operational lessons that were expensive to discover
so future long-running engineering work does not repeat them.

This review is engineering-only. It does not change evidence eligibility,
PIT/no-lookahead semantics, frozen research scope, model/threshold behavior,
production authority, trading authority, or public/private security boundaries.

## Incident sequence

### 1. Four large shards created an unacceptable recomputation blast radius

The first Fundamental V9 migration used four large shards with a 180-minute
job timeout.

The parser upgrade had to revisit a much larger population of legacy filing
checkpoints than ordinary steady-state runs. All four shards spent well over an
hour in materialization.

The core design defect was not merely "timeout too short". The real defect was
that the reusable engineering unit was too large.

Lesson:

> Timeout expansion is a safety measure, not a substitute for smaller reusable
> units.

The workflow was changed to 16 deterministic work units with
`max-parallel: 4`, preserving provider concurrency while reducing the maximum
recomputation loss.

### 2. Progress was written locally but persisted too late

The original materializer wrote document checkpoints incrementally on the runner,
but the GitHub Actions cache save step occurred only after the long materialize
step.

A hard timeout could therefore destroy a large amount of runner-local work.

Cancellation behavior later proved that an `always()` save step could preserve
progress when the job was cancelled cleanly, but relying on end-of-job save
alone remained too fragile.

Lesson:

- incremental local checkpoints are necessary but not sufficient;
- the work unit must be small enough to reach persistence frequently;
- completed qualified units should be persisted immutably immediately.

### 3. Cancelling the old run unexpectedly preserved valuable V9 work

When the old four-shard run was cancelled, each active shard's
`Save filing checkpoints` step completed successfully.

Those exact caches contained a mixture of legacy V8 checkpoints and already
generated V9/earnings-v2 checkpoints.

A one-time strict migration bridge was created:

- exact source run id;
- exact source commit;
- exact cache keys;
- exact progress source commit;
- allowed producer/version list;
- current-unit symbol filter;
- checkpoint receipt and file validation before and after copy;
- no evidence qualification granted.

Lesson:

> After cancellation, inspect actual save outcomes before assuming work was lost.

### 4. Durable progress and formal evidence had to be separated

The new architecture introduced two distinct reusable objects:

- durable engineering progress cache;
- immutable qualified work-unit bundle.

The progress cache accelerates resume but grants no qualification.

A work-unit bundle is published only after the unit gate passes and is verified
again before cross-run reuse.

Lesson:

> "Reusable computation" and "formal reusable evidence-stage output" are
> different contracts.

### 5. A one-line qualification CLI bug invalidated otherwise correct units

The first 16-unit run exposed:

`NameError: name 'kind' is not defined`

The materialization, progress save, receipt sealing, and diagnostics all
succeeded. The failure was in the CLI payload rendering path of the
qualification gate:

`kind in {...}` instead of `args.kind in {...}`.

Helper-level tests had passed because the actual `main()` path was not executed.

Lesson:

> Every long-running gate/finalizer CLI must have an entrypoint-level regression
> test. Testing helper functions alone is insufficient.

A gate-only compatibility bridge preserved already-saved progress across the
mechanical fix without weakening the semantic fingerprint globally.

### 6. A compatibility bridge caused a performance regression by coupling caches

The gate-bug-compatible V9 progress bridge worked, but its workflow condition
accidentally suppressed restoration of the older mixed cache.

That mixed cache also contained CNINFO symbol-query/index checkpoints.

As a result, work units with V9 document checkpoints still had to query years of
CNINFO announcements again. The provider uses paginated 30-row requests with
retry/backoff and page sleeps, so wall-clock time remained unexpectedly high.

Lesson:

> Query/index cache and document/parser cache are independent assets. A hit in
> one must not suppress restoration of the other.

The workflow was corrected so the exact mixed cache remains available as a
read-only legacy query/index store even when current V9 progress is already
restored.

### 7. Immutable unit publication finally changed the economics of cancellation

After the gate fix, units 0-3 completed successfully and each immediately
published an immutable work-unit bundle.

At that point a later performance fix could justify cancelling and restarting:
those four units would be directly restored rather than recomputed, and active
units could still preserve progress.

Lesson:

> A safe restart decision depends on what is already immutable, not only on how
> long the current run has been executing.

## Failure patterns to remember

### Pattern A: "Everything is still materializing"

Do not immediately assume a hang.

Check:

- work-unit size;
- parser migration population;
- query-cache hit rate;
- document-cache hit rate;
- network pagination;
- conflict/presentation rechecks;
- aggregate reconstruction.

### Pattern B: "The cache restored successfully"

Do not assume the expensive path is avoided.

Ask which cache layer restored:

- source query/index;
- legacy document;
- current parser document;
- derived stage output;
- completed immutable unit.

### Pattern C: "The fix is merged"

A running job remains bound to the old SHA.

Explicitly choose whether to:

- let the old run finish;
- harvest progress then cancel;
- cancel immediately after proving persistence safety.

### Pattern D: "The run failed after hours"

Before rerunning:

1. inspect completed units;
2. inspect progress-save steps;
3. inspect diagnostics artifacts;
4. classify whether the failure is deterministic, data, transport,
   qualification, performance, or publication;
5. repair only that class;
6. add the exact regression test;
7. determine compatibility impact;
8. rerun only non-immutable work.

## Resulting default rules

The following are now mandatory preferred practice for large engineering runs:

1. design the reusable work unit before the first expensive run;
2. keep provider concurrency bounded independently of work-unit count;
3. persist engineering progress before the qualification gate;
4. publish every qualified completed unit immutably without waiting for siblings;
5. restore query/index and document/parser caches independently;
6. execute CLI entrypoints in regression tests;
7. preserve diagnostics before gate failure;
8. treat timeout as a per-job safety ceiling, not a design target;
9. distinguish operational changes from semantic changes;
10. use exact, expiring compatibility bridges for proven non-semantic fixes;
11. inspect save outcomes after cancellation;
12. estimate remaining wall-clock from completed-batch throughput;
13. stop after acceptance instead of expanding scope.

See [Long-Running Engineering Execution Protocol](long_running_engineering_execution_protocol.md)
for the reusable procedure.
