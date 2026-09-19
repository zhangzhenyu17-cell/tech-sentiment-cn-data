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

### 8. Correct cache keys were still insufficient because cache visibility was branch-scoped

The presentation-classifier compatibility bridge initially froze exact cache
keys from runs `35444225741`, `35440581921`, and `35438201372`.

The keys and fingerprints were correct, but all three source runs had executed
on:

`v4a/fundamental-resume-295710`

A replacement run dispatched directly from `main` would therefore be unable to
read those feature-branch caches even with the correct exact key.

This was a real orchestration defect because it could silently turn a
well-designed resume path back into expensive provider/document recomputation.

The fix froze the cache-scope branch/ref in the bridge and added a
pre-materialization fail-fast guard. The recovery branch was then fast-forwarded
without force to the latest intended `main` commit before the replacement run.

Lesson:

> Cache identity includes visibility scope. Exact key + exact fingerprint is not
> sufficient if the replacement Git ref cannot read the cache.

### 9. The recovery guard needed execution-level testing, not only YAML assertions

A static workflow assertion could prove that a branch guard string existed, but
could not prove that the actual inline Python resolver parsed, received the
expected environment, and emitted the required cache keys.

The regression test was strengthened to extract the real Python heredoc from the
workflow and execute it twice:

- recovery branch ref -> must succeed and emit the three presentation cache keys;
- `main` ref -> must fail before materialization with the branch-scope message.

Lesson:

> Any inline resolver/guard controlling an expensive path should have an
> execution-level regression test, just like a CLI gate or finalizer.

### 10. Real recovery success was proven by zero-work counters, not by green cache steps

The first real `fundamental-v9-presentation-recheck-v2` recovery run was:

`35451946515`

Units 0-2 restored the audited prior durable progress and completed current
semantic materialization and qualification in roughly one minute per unit.

The decisive proof was not merely that cache restore steps were green. Runtime
summaries showed:

- `executed_symbol_queries = 0`;
- filing/earnings `executed_documents = 0`;
- roughly 305-311 current-parser documents resumed per unit;
- 12 progress symbol-query checkpoints resumed per unit;
- current qualification and current-generation publication still ran.

This demonstrated that the recovery path removed the expensive provider and
document work without inheriting old qualification status.

Lesson:

> A recovery optimization is operationally proven only when counters show the
> expensive path was avoided and current qualification still executes.

### 11. The successful recovery still had two execution classes

Run `35451946515` completed 16 / 16 units and the final package successfully in
approximately 10 minutes 50 seconds.

The run showed that recovered units should not be forecast with one uniform
per-unit duration.

Fully resumed units such as 0-2 had:

- zero executed provider symbol queries;
- zero executed documents;
- hundreds of current-parser documents resumed;
- current materialization, qualification, and publication still executed;
- roughly one-minute end-to-end job duration.

The previously failing unit 5 correctly selected newer recovery progress but
still required bounded additional execution on the repaired path and took
roughly six minutes before qualification and publication.

Lesson:

> Forecast a mixed recovery run by execution class: fully resumed, targeted
> semantic repair, and provider recomputation. The slowest historical run is not
> the correct estimate for every restored unit.

### 12. Final package success created a producer-identity freeze window

The successful package job did more than turn the workflow green. It downloaded
all 16 qualified shard artifacts, verified their artifact digests, assembled the
208-file `fundamental-all` stage, and published:

- asset base
  `v4a-fundamental-20220104-20260917-56e8c44ca678668bfc17`;
- bundle identity
  `f558db45af7a9e8b5e69dad1275f98e93e6ee49c09914fa591edd4348cfde4c4`;
- compatibility key
  `56e8c44ca678668bfc17fd7ef5da5a9c32503dcbac03eb86fa3963ecd0cf751b`;
- archive SHA-256
  `c1df2ec495657f52cf34935feb5ab94c7159f43e4cbf6f24bb8fbcf43ffdb54e`;
- stage receipt SHA-256
  `2b27a25584e22521811ceebfdc873363d6a8fc43cf82d1b442d8ced121b91770`.

The producer fingerprint explicitly includes the Fundamental workflow and frozen
Fundamental reference inputs.

Therefore, immediately deleting the now-obsolete one-time recovery branch guard
or editing the Fundamental checkpoint reuse contract would change the producer
identity before `v4a-09-derived` consumes the successful bundle.

That would be a cleanup-induced regression: the data would remain valid, but the
next stage could fail to discover the exact bundle it is supposed to consume.

Lesson:

> After publishing an immutable stage bundle, freeze producer-identity inputs
> until downstream handoff is complete. Logically retire temporary recovery
> scaffolding first; physically remove it only after the published compatibility
> identity no longer needs to be consumed.

### 13. Stage success is not pipeline qualification

The Fundamental workflow succeeded completely, but V4-A is not yet allowed to
declare `HISTORICAL_DATA_QUALIFIED`.

The public stage still must feed the downstream PIT assembly/finalizer and the
private PostRun Preflight / Artifact Intake.

Lesson:

> Keep a qualification ladder explicit. A successful producer stage is a valid
> handoff, not permission to skip downstream provenance, completeness, or intake
> gates.

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
12. record cache branch/ref visibility as part of restore identity;
13. fast-forward a dedicated recovery branch to the intended latest code when
    same-branch cache visibility is required;
14. fail fast before materialization if the dispatch ref cannot read required
    recovery caches;
15. execution-test inline workflow resolvers/guards that control expensive work;
16. prove resume efficiency using query/document executed-vs-resumed counters;
17. estimate mixed recovery runs by fully-resumed / targeted-repair /
    provider-recompute execution class;
18. after group publication, freeze producer-identity inputs until downstream
    handoff consumes the published compatibility key;
19. logically retire one-time recovery scaffolding before physically removing it;
20. distinguish stage success from final pipeline qualification;
21. estimate remaining wall-clock from completed-batch throughput;
22. stop after acceptance instead of expanding scope.

See [Long-Running Engineering Execution Protocol](long_running_engineering_execution_protocol.md)
for the reusable procedure.
