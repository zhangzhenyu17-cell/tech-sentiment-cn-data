# Fundamental Extended PIT Historical Coverage Run Plan — 2026-09-22

Status: **EXPLICITLY AUTHORIZED / OUTCOME BLIND / MANUAL ONLY / NO QUALIFICATION PROMOTION**

## Frozen task

Materialize the already-defined extended official-filing raw PIT primitives for the
current frozen Capital/PIT universe and mechanically audit field coverage,
provenance and same-timestamp revision ambiguity.

This run does **not** read any historical or forward return outcome and does not
change any private Fundamental evidence qualification by itself.

## Frozen scope and window

- scope builder: `scripts/build_capital_pit_symbol_scope.py`
- scope version: `capital-pit-frozen-universe-v2`
- exact current symbol count: **193**
- universe semantics: union of already-frozen STAR50 + ChiNext50 historical members
- known correction included: STAR50 carry-in `688065`
- target start: `2022-01-04`
- target end: `2026-09-17`
- query warmup: 2 years
- public source: exact CNINFO official announcement/document identity only

The 2026-09-17 end date intentionally matches the existing V4-A historical
qualification artifact window. No new universe or later historical window is
opened by this run.

## Execution class

Class L/XL public data materialization.

- deterministic shards: 12
- partition: sorted scope rows, row-index modulo 12
- provider-facing max parallelism: 4
- per-shard timeout ceiling: 180 minutes
- aggregate timeout ceiling: 30 minutes
- smallest reusable success unit: one shard checkpoint + shard output artifact
- progress generation id: `fund-ext-pit-v1-20220917-window-20260922`

## Three-layer preflight

1. code/contract:
   - public-tree audit;
   - targeted parser/materializer/coverage/scope tests;
   - compile / CLI path through normal PR CI.
2. live source:
   - build exact real trading calendar;
   - run exact materializer on one deterministic SH and one deterministic SZ scope member.
3. production execution-class parity:
   - same scope builder;
   - same calendar;
   - same CNINFO query/document identity;
   - same parser;
   - same checkpoint path;
   - same hard-failure classification as full shards.

Full scale-out is blocked unless preflight succeeds.

## Persistence and recovery

Each shard restores the most recent cache matching:

`fund-ext-pit-v1-20220917-window-20260922-shard-N-*`

and saves current progress under a run/attempt-specific immutable key.

The materializer emits per-symbol heartbeat JSON with executed/resumed query and
document counts plus throughput-derived ETA.

Each shard uploads its facts / coverage / errors / summary even on failure where
the files exist. Aggregate artifacts are produced only from the exact run's 12
shard outputs.

## Circuit breaker

The materializer stops a shard after 3 repeated normalized hard failures with the
same signature. Soft `DATA_INSUFFICIENT` line-item absence does not trip the
breaker.

A breaker does not convert failed work into valid empty data. The aggregate gate
fails closed on missing scope coverage or hard failures.

## Aggregate acceptance

The public coverage audit must verify:

- exact 193-entity scope;
- exactly one coverage row per scope entity;
- no out-of-scope facts;
- complete required provenance columns;
- per raw fact type entity coverage;
- same available/publication timestamp multi-revision groups;
- value-conflict groups;
- source-native revision sequence remains unqualified;
- `outcome_read=false`;
- `evidence_qualification_changed=false`;
- `production_changed=false`;
- `trading_authority_changed=false`.

The public audit may conclude only:

`OUTCOME_BLIND_RAW_PIT_COVERAGE_AUDIT_COMPLETE_NOT_QUALIFIED`

or a fail-closed blocker state.

## Boundary

Explicitly forbidden in this run:

- historical or forward return outcome read;
- Fundamental validation;
- clean holdout / OOS;
- parameter / threshold / weight / subset search;
- new factor, model or universe;
- CASH or DEBT model aggregation changes;
- source-native revision ordering invention;
- evidence qualification promotion;
- Production / position-sizing / trading-authority change;
- schedule / push / pull_request / workflow_run execution trigger.

## Next legal gate

After a successful public aggregate, private intake may mechanically review the
exact artifact to decide which raw fields remain `DATA_INSUFFICIENT`, which are
blocked by revision ambiguity, and which satisfy the already-frozen input
requirements. Outcomes remain unread until a later explicitly authorized gate.
