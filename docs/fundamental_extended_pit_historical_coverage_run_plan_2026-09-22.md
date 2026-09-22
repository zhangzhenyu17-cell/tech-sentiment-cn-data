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

- deterministic work units: **48**
- partition: sorted scope rows, row-index modulo 48
- symbols per unit: 4–5
- provider-facing max parallelism: 4
- per-unit timeout ceiling: 360 minutes
- aggregate timeout ceiling: 30 minutes
- smallest reusable success unit: one work-unit checkpoint + work-unit output artifact
- progress generation id: `fund-ext-pit-v1-20220917-window-20260922`
- runtime cache generation: `fund-ext-pit-v2-48unit-query-reuse-20260922`

The 48-unit decomposition is an explicit measured-performance exception to the
usual preferred 8–16 unit range. Run `35718373670` showed that two-symbol
representative full-history materialization exceeded 29m40s without completing
the first symbol. A 16–17 symbol unit therefore could not satisfy the repository
rule that p95 unit duration should remain below half the 360-minute safety
ceiling. Four to five symbols per unit restores that safety margin while keeping
provider concurrency fixed at four.

## Three-layer preflight

1. code/contract:
   - public-tree audit;
   - targeted parser/materializer/coverage/scope tests;
   - compile / CLI path through normal PR CI.
2. live source:
   - build exact real trading calendar;
   - select one deterministic SH and one deterministic SZ member that map to the
     same legacy 4-shard query-cache partition;
   - restore the exact audited V4-A filing-index cache for that source shard;
   - run the exact materializer/selector/transport/parser on one real official
     financial document per representative symbol.
3. production execution-class parity:
   - same scope builder;
   - same calendar;
   - same CNINFO query/document identity;
   - same parser;
   - same immutable checkpoint implementation;
   - exact legacy query/index checkpoint lookup before provider fallback;
   - same hard-failure classification as full work units.

The preflight document limit is only a scope reduction. It does not alter title
eligibility, publication ordering, official attachment identity, parser rules,
unit proof, PIT availability or failure classification. It exists to prevent the
preflight itself from becoming a duplicate full-history materialization job.

Full scale-out is blocked unless preflight succeeds.

## Persistence and recovery

Each work unit first restores the exact audited V4-A mixed cache corresponding to
`unit_index % 4` from source run `35431952238`. Only compatible
`cninfo-filing-index` query checkpoints are eligible for the extended
materializer. Old parsed Fundamental facts cannot collide with the new extended
parser because checkpoint producer/version identities differ.

The recovery run must be dispatched from:

`v4a/fundamental-resume-295710`

because the exact source caches are branch/ref scoped. The branch is fast-forwarded
to the intended current main commit without force before dispatch.

The extended materializer checks the exact historical query identities from both:

- `2ef3ee784c7e6ced5801cf35b6a5113a68e9ab6a`;
- `29571066bbf69da5ba252982f71b592e07969314`.

Provider recomputation is used only if neither exact query checkpoint exists.

Each work unit then restores the most recent current extended-PIT progress cache
matching the frozen runtime generation and saves current progress under a
run/attempt-specific immutable key.

The materializer emits per-symbol heartbeat JSON with executed/resumed query and
document counts plus throughput-derived ETA. Runtime summary must disclose exact
legacy-query hits by source commit.

Each work unit uploads its facts / coverage / errors / summary even on failure
where the files exist. Aggregate artifacts are produced only from the exact
run's 48 work-unit outputs.

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

## Failure lesson addendum — run 35718373670

The first run did not expose a Fundamental data-quality blocker. It exposed a
long-run orchestration defect:

- preflight duplicated full historical materialization rather than proving one
  representative live document per execution class;
- the new extended workflow did not reuse already-audited V4-A filing-index
  query checkpoints;
- 12 work units left 16–17 symbols per unit, which was inconsistent with the
  observed >29m40s first-symbol latency.

The durable repair is therefore decomposition + cache reuse + bounded preflight,
not timeout expansion alone. The 360-minute ceilings remain safety ceilings and
are not the performance design target.
