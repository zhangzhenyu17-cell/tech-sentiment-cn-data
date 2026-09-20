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

- Estimated independent network/document work items:
- Estimated wall-clock before decomposition:
- Decomposition trigger hit (>=30 min or >=200 independent items):
- Unit count:
- Partition rule:
- Why 8–16 preferred units is used or why an exception is safer:
- Max parallelism:
- Provider concurrency cap:
- Expected entities per unit:
- Target maximum recomputation loss per active runner:
- Timeout per job:
- p95 unit duration expected below half timeout:
- Six-hour monolith exception reason (required if applicable):
- Expected median unit time:
- Expected full wall-clock range:
- Fully resumed unit expected time:
- Targeted semantic-repair unit expected time:
- Provider-recompute unit expected time:
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
- Published asset base / bundle identity / compatibility key:
- Archive SHA-256 / stage receipt SHA-256:
- Producer files that must remain frozen for downstream handoff:

## Restore precedence

1. Immutable completed unit:
2. Current semantic-generation progress:
3. Explicit compatibility bridge:
4. Legacy/query/index cache:
5. Provider recomputation:

Explain any deviation:

## Preflight

### Layer 1 — code / contract

- [ ] public-tree/security audit green
- [ ] relevant tests green
- [ ] actual CLI entrypoints tested
- [ ] workflow-inline guards/resolvers executed in tests when they control expensive work

### Layer 2 — live provider / transport / content

- [ ] representative provider path checked when data-path changed
- [ ] every provider family represented
- [ ] exact production transport stack exercised on hosted runner
- [ ] content encoding decoded before file parser
- [ ] HTML/WAF/challenge body classified before PDF/file parser
- [ ] redirect remains inside frozen provider family
- [ ] canonical URL, retrieval URL, transport method and transport/document hashes captured

### Layer 3 — representative production execution class

- [ ] production eligibility/selector reused exactly
- [ ] at least one representative from each materially different execution class succeeds
- [ ] synthetic-only coverage is not treated as sufficient for hosted-runner transport changes

### Restore / persistence

- [ ] cache restore path checked
- [ ] cache-producing branch/ref recorded
- [ ] intended recovery ref can read required caches
- [ ] wrong-ref path fails before expensive materialization when branch scope matters
- [ ] workflow-inline restore/resolver guard execution-tested when it controls expensive work
- [ ] diagnostics emitted before gate
- [ ] cancellation/save behavior understood
- [ ] immutable publication verification checked when changed

## Observability

- Progress heartbeat interval:
- Maximum expected silent interval (must be <=10 minutes for >=10-minute steps):
- Circuit-breaker normalized failure signature:
- Circuit-breaker threshold and scope:
- ETA refresh after first representative batch:

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
- succeeded / unclassified / hard-error counts:
- elapsed time / observed throughput:
- ETA range:
- checkpoint watermark:
- transport method / content class:

## Failure matrix

| Failure class | Detection | Preserve | Repair | Rerun scope |
| --- | --- | --- | --- | --- |
| deterministic code/orchestration | | | | |
| transport/protocol | | | | |
| content encoding/content class (gzip/HTML/WAF) | | | | |
| parser/layout | | | | |
| qualification blocker | | | | |
| performance regression | | | | |
| publication/aggregation | | | | |

## Phase isolation

Record whether each phase can be rerun independently without replaying valid prior output:

- [ ] preflight
- [ ] transport/source-index
- [ ] document/item materialization
- [ ] qualification gate
- [ ] immutable publication
- [ ] aggregate/finalizer
- [ ] private intake, if applicable

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

## Post-success handoff freeze

- Downstream consumers that must use the just-published producer identity:
- Producer-identity freeze starts when:
- Producer-identity freeze ends when:
- One-time recovery code logically retired:
- Physical cleanup deferred until downstream handoff:
- Stage success explicitly distinguished from final pipeline qualification:

## Acceptance

- [ ] all required units accounted for
- [ ] all reusable successful units immutable
- [ ] aggregate completeness/uniqueness verified
- [ ] diagnostics retained
- [ ] relevant CI green
- [ ] lineage/current SHA recorded
- [ ] published group bundle identity / compatibility key / archive hash recorded
- [ ] downstream producer-identity freeze window declared where applicable
- [ ] no unauthorized boundary change
- [ ] known limitations recorded
- [ ] stop condition reached
