# Long-Running Run Plan

Copy this template for any new or materially changed long-running engineering
execution before launching the expensive run.

## Identity

- Task:
- Repository:
- Workflow:
- Branch:
- Exact SHA:
- Start/end window:
- Frozen scope identity:
- Upstream immutable bundle identities:

## Authorization boundary

- Engineering-only scope:
- Explicitly unchanged evidence/PIT/model/threshold/production/trading rules:
- Any separately authorized boundary change:
- Automatic trigger status: manual-only unless explicitly allowlisted.

## Execution units

- Unit count:
- Partition rule:
- Max parallelism:
- Expected entities per unit:
- Timeout per job:
- Expected median unit time:
- Expected full wall-clock range:
- Provider concurrency constraints:

## Persistence

- Incremental checkpoint location:
- Query/index cache identity:
- Document/parser progress identity:
- Cache-producing branch/ref:
- Intended recovery dispatch branch/ref:
- Cache visibility from intended recovery ref verified:
- Save-before-gate step:
- Cancellation-safe save behavior:
- Immutable completed-unit bundle:
- Persistent registry/tag:
- Group aggregate/final bundle:

## Restore precedence

1. Immutable completed unit:
2. Current semantic-generation progress:
3. Explicit compatibility bridge:
4. Legacy/query/index cache:
5. Provider recomputation:

Explain any deviation:

## Preflight

- [ ] public-tree/security audit green
- [ ] relevant tests green
- [ ] actual CLI entrypoints tested
- [ ] representative provider path checked when data-path changed
- [ ] cache restore path checked
- [ ] cache-producing branch/ref recorded
- [ ] intended recovery ref can read required caches
- [ ] wrong-ref path fails before expensive materialization when branch scope matters
- [ ] workflow-inline restore/resolver guard execution-tested when it controls expensive work
- [ ] diagnostics emitted before gate
- [ ] cancellation/save behavior understood
- [ ] immutable publication verification checked when changed

## Observability

Required counters/fields:

- resumed/executed symbol queries:
- resumed/executed documents:
- selected restore layer/source run/ref:
- zero-provider-query recovery proof:
- zero-document-execution recovery proof:
- targeted semantic re-proof count:
- parser-upgrade documents:
- soft/hard failures:
- readiness:
- checkpoint save result:
- immutable publish result:

## Failure matrix

| Failure class | Detection | Preserve | Repair | Rerun scope |
| --- | --- | --- | --- | --- |
| deterministic code/orchestration | | | | |
| transport/protocol | | | | |
| parser/layout | | | | |
| qualification blocker | | | | |
| performance regression | | | | |
| publication/aggregation | | | | |

## Cancellation criteria

Cancel only if one or more applies:

- [ ] deterministic blocker makes remaining run unable to succeed
- [ ] fixed SHA removes a material performance/reliability defect
- [ ] completed units are immutable
- [ ] active progress is cancellation-safe or already saved
- [ ] cancellation will not rerun one-time research/evidence work

Before cancelling, record:

- completed immutable units:
- active units:
- durable progress saved:
- expected loss if cancelled now:

After cancelling, verify actual save outcomes before the replacement run.

## Acceptance

- [ ] all required units accounted for
- [ ] all reusable successful units immutable
- [ ] aggregate completeness/uniqueness verified
- [ ] diagnostics retained
- [ ] relevant CI green
- [ ] lineage/current SHA recorded
- [ ] no unauthorized boundary change
- [ ] known limitations recorded
- [ ] stop condition reached
