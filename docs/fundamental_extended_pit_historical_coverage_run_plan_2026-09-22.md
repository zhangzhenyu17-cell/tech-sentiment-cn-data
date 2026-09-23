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


## Failure lesson addendum — run 35732714685

This run passed the repaired preflight completely, including exact V4-A legacy
query reuse and the bounded SH/SZ live-document execution-class check. It then
exposed a separate artifact-layout orchestration defect before any full work unit
reached provider-facing materialization.

The preflight upload contains paths under both `stage/shared/**` and
`stage/preflight/**`. `actions/upload-artifact` therefore selected `stage/`
as the least common ancestor and stored artifact members as:

- `shared/pit_symbol_scope/**`;
- `shared/calendar/**`;
- `preflight/**`.

After downloading the artifact into `stage/shared-download`, the workflow
incorrectly addressed those files as `stage/shared-download/stage/shared/**`.
Every materialize matrix job consequently failed at the local
`Build deterministic shard scope` step with the same `FileNotFoundError`;
no affected work unit reached legacy-cache restore or live provider requests.

Durable repair:

1. address downloaded files from the actual root
   `stage/shared-download/shared/**`;
2. add a single `shared_inputs_gate` job between preflight and the 48-unit
   matrix;
3. the gate validates the exact expected artifact files, 193-symbol manifest,
   successful legacy-query preflight reuse, and outcome/evidence boundary flags;
4. materialization fan-out is blocked unless that single gate succeeds;
5. aggregate uses the same canonical downloaded artifact paths.

This is an execution-orchestration correction only. It does not change scope,
window, parser, PIT semantics, provider concurrency, evidence qualification,
outcome access, Production, sizing, or trading authority.

## Data-governance lesson addendum — successful run 35847821341 and semantic audit

Run `35847821341` completed successfully at the workflow level and produced a
structurally coherent aggregate artifact: exact scope coverage, complete required
provenance, no hard-failure rows and an explicit
`OUTCOME_BLIND_RAW_PIT_COVERAGE_AUDIT_COMPLETE_NOT_QUALIFIED` state.

That success was **not** sufficient to establish raw-fact semantic correctness.
A post-run artifact review found values whose shapes were inconsistent with the
intended amount-column semantics, including note-like small integers / years and
extreme concatenated numeric tokens. The root cause was not a qualification-rule
failure. It was an upstream parser semantic risk: Chinese financial statements
may place an `附注` column before the current-period amount, and PDF text-layer
extraction may also collapse adjacent current/prior amount cells into one token.

The durable governance rule is therefore:

> **A green materialization plus structurally valid provenance is necessary but
> not sufficient for qualification intake. The exact aggregate artifact must
> receive an outcome-blind semantic review before any downstream qualification
> gate is allowed to consume it.**

### Required post-materialization semantic review

For public raw-data qualification pipelines, perform this review after aggregate
construction and before private qualification intake:

1. **Structural integrity** — exact scope, provenance, source identity, parser
   version, uniqueness, hard-failure state and revision diagnostics.
2. **Field-shape diagnostics** — inspect distributions, repeated tiny integers,
   year-like values, impossible token shapes, sign patterns and extreme tails as
   *diagnostic triggers*.
3. **Source-layout trace** — when a suspicious shape is found, trace it back to
   the immutable official document/text layout and determine whether the parser
   can prove the intended column position.
4. **Parser contract decision** — if the layout is not provable, fail closed and
   leave the fact missing. Do not invent an issuer-specific correction.
5. **Regression coverage** — encode both the positive layout and the ambiguous
   negative layout in parser tests before rerunning history.
6. **Semantic-generation reset** — parser behavior/version changes invalidate
   parsed-document facts from the old parser generation.
7. **Layered cache reuse** — preserve independent query/index caches when their
   source, window and identity contracts did not change. A parser repair should
   not force unrelated historical provider-query recomputation.
8. **New-SHA validation** — validate the repaired parser on a new run bound to
   the repaired SHA; never treat rerunning an old attempt as validation of new
   code.

### Diagnostic values are not repair thresholds

Suspicious values may reveal a parser defect, but they must not become hidden
financial heuristics. In particular:

- do not reject or rewrite a value merely because it is unusually large/small;
- do not add issuer-specific hard-coded values;
- do not infer the correct amount from cross-period magnitude;
- do not treat a year-like or note-like token as wrong without first proving the
  table-column semantics;
- do use these patterns to select documents for source-layout inspection and to
  design structural regression tests.

The repair must be expressed in layout/column semantics. For the observed case,
the safe contract is structural: prove the row owns the target label, prove the
local explicit unit, prove the amount-column shape, and otherwise return missing.

### Semantic identity and cache invalidation

Parser semantics are part of the materialized fact identity. When parser behavior
changes materially:

- increment/freeze a new parser version;
- include that parser version in immutable parsed-document checkpoint identity;
- reject old parsed facts under the new parser generation;
- keep query/index checkpoint identities independent so safe source-query work can
  still be reused;
- expose resumed vs executed query/document counts so reuse is auditable rather
  than assumed.

This is the preferred balance between correctness and compute efficiency:
**invalidate exactly the layer whose semantics changed, and no broader layer.**

### Qualification boundary remains unchanged

This lesson does not authorize a Fundamental qualification promotion. The rerun
remains public-data engineering and outcome blind. A corrected aggregate must
still pass the existing private intake, revision-order, field-mapping, coverage
and provenance requirements before any qualification state can change.

The key distinction is now explicit:

`workflow success -> structural artifact acceptance -> semantic artifact review -> private outcome-blind qualification intake`

No earlier state may be collapsed into a later one merely because the GitHub run
is green.