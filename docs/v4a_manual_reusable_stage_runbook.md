# V4-A Manual Reusable Stage Runbook

## Purpose

V4-A public historical qualification is intentionally split into manual-only,
failure-isolated workflows. A successful stage is sealed into an immutable
persistent bundle and may be reused by later runs when its producer fingerprint,
date window, and upstream bundle identities remain compatible.

This architecture exists to prevent an unrelated failure or code repair from
forcing every successful multi-hour stage to run again.

It does not change evidence eligibility, PIT/no-lookahead semantics, frozen
scope, readiness thresholds, public/private security boundaries, Production
authority, or trading authority.

The default operating rules for any long-running execution are defined in
[Long-Running Engineering Execution Protocol](long_running_engineering_execution_protocol.md).
This V4-A runbook is the stage-specific application of that protocol.

## Persistent registry

Reusable public-data stage bundles are published as immutable release assets
under:

`v4a-stage-bundles-v1`

Each bundle has three assets:

- `<asset-base>.tar.gz`
- `<asset-base>.manifest.json`
- `<asset-base>.sha256`

The asset base is derived from:

- stage family;
- start/end window;
- stage-specific producer fingerprint;
- exact upstream persistent bundle identities.

The producer fingerprint covers the workflow, producer entrypoints, recursively
imported local `tech_sentiment` modules, relevant frozen reference inputs, and
`pyproject.toml`.

An unrelated repository commit therefore does not invalidate a successful
stage. A change to the stage producer, frozen input, or any upstream bundle does.

Existing assets are never overwritten. A repeated or interrupted publication
first verifies every already-present asset byte-for-byte, uploads only missing
assets, and then re-verifies the complete three-asset bundle. Any byte mismatch
fails closed.

## Manual workflow order

### 1. Shared inputs

Workflow: `v4a-01-shared-inputs`

Run first for a new date window or whenever the frozen scope/calendar/freshness
producer changes.

It creates the shared real trading calendar, frozen Capital/PIT symbol scope,
representative provider preflights, and the persistent shared bundle.

### 2. Independent public-data stages

After Shared succeeds, the following may be run independently and in parallel:

- `v4a-02-capital`
- `v4a-03-financing`
- `v4a-04-issuer-source` with `source=cninfo`
- `v4a-04-issuer-source` with `source=sse`
- `v4a-04b-issuer-szse-migration` for the frozen 300114 -> 302132 same-security migration window
- `v4a-06-fundamental-earnings`
- `v4a-07-prices`
- `v4a-08-policy`

CNINFO, SSE, and SZSE issuer archives are deliberately separate persistent
bundles. A transport failure in one issuer source must not invalidate the other
two.

For the current frozen window, SZSE must use `v4a-04b-issuer-szse-migration`.
The frozen scope contains the same listed security across the official
`300114` -> `302132` code/name migration effective 2025-02-17. The migration
workflow keeps the canonical SZSE announcement endpoint, pagination proof,
official document-host checks, and qualification gate unchanged. It only
partitions that explicitly frozen same-security identity at the effective date.
It does not add an evidence source, change evidence eligibility, change
PIT/no-lookahead semantics, or read future outcomes. The legacy
`v4a-04-issuer-source source=szse` path remains fail-closed for this window and
must not be used as the canonical SZSE producer.

Prices retains bounded four-shard parallelism internally.

Fundamental/Earnings uses 16 deterministic work units with `max-parallel: 4`.
Each unit persists durable engineering progress before its qualification gate.
A unit that passes the gate immediately publishes an immutable reusable
work-unit bundle; later runs restore compatible completed units before doing any
materialization. Query/index caches and document/parser progress caches are
restored independently. The final Fundamental group bundle is published only
after all 16 units are present and verified.

### 3. Issuer aggregate

Workflow: `v4a-05-issuer-aggregate`

Run after all three issuer-source bundles exist. It verifies the three source
bundles against the current producer fingerprints, records each source's original
producer commit, aggregates coverage/evidence, and publishes an issuer aggregate
bundle.

### 4. Derived PIT assembly

Workflow: `v4a-09-derived`

Run after these persistent bundles exist:

- Shared;
- Issuer Aggregate;
- Fundamental/Earnings;
- Prices;
- Policy.

This workflow performs no provider materialization. It verifies the upstream
bundles, then rebuilds the cross-source PIT rails, trailing valuation, major
negative coverage/review, and PIT replay audit.

Derived is phase-resumable. Its engineering checkpoint identity binds the
frozen symbol scope, trading calendar, upstream source commits, and exact
upstream stage-receipt hashes. Completed phases are persisted independently:

- Fundamental/Earnings aggregation and state derivation;
- Price aggregation plus trailing valuation;
- major-negative coverage/review;
- PIT replay audit;
- final assembled output tree.

A phase is resumable only after its completed-phase marker is atomically written.
Partial files without the marker are ignored. This checkpoint state is
engineering-only and never grants qualification.
### 5. Canonical public finalizer

Workflow: `qualify-capital-inputs`

This name is intentionally preserved for the frozen private V4-A intake contract.

The finalizer is assembly-only. It downloads and independently verifies all
compatible persistent stage bundles, builds the stage-lineage
`checkpoint_receipt_summary.json`, assembles the existing
`capital-pit-materialization-v4a2` managed-file tree, and emits the canonical
public artifact plus external artifact receipt.

It must not query CNINFO, SSE, SZSE, CSRC, or other market-data providers.

## Failure recovery

When a stage fails:

1. keep every already-published successful stage bundle and completed work-unit bundle;
2. inspect whether active units saved durable progress before assuming work was lost;
3. classify the failure as deterministic code/orchestration, provider transport,
   parser/layout, qualification, performance, or publication/aggregation;
4. diagnose and repair only the failed stage or its direct producer contract;
5. add a regression test for the exact deterministic failure path;
6. run tests/CI for the code change;
7. assess whether the change is semantic or operational-only;
8. if a non-semantic change invalidates progress identity, use only an exact,
   audited, expiring compatibility bridge;
9. rerun only the manual stage/work units that are not already immutably complete;
10. rerun only downstream aggregate stages whose upstream bundle identity changed;
11. run the finalizer after the dependency chain is complete.

Examples:

- Policy fix: rerun Policy -> Derived -> Finalizer.
- SSE issuer fix: rerun Issuer Source(SSE) -> Issuer Aggregate -> Derived -> Finalizer.
- SZSE code-migration fix: rerun Issuer SZSE Migration -> Issuer Aggregate -> Derived -> Finalizer.
- Price fix: rerun Prices -> Derived -> Finalizer.
- Capital fix: rerun Capital -> Finalizer.
- Financing fix: rerun Financing -> Finalizer.
- Shared/frozen-scope fix: all directly dependent stages become incompatible and
  must be regenerated.

Do not rerun an unchanged compatible stage merely to obtain a newer repository
SHA.

## Compatibility and lineage rules

Cross-commit reuse is allowed only when the current workflow independently
recomputes the producer fingerprint and exact input bundle identities and they
match the persistent manifest.

Every final lineage row records:

- original source commit;
- producer fingerprint;
- compatibility key;
- bundle identity;
- archive SHA-256;
- stage receipt SHA-256;
- exact input bundle identities;
- completion state.

The finalizer commit remains the canonical public artifact source commit. Earlier
stage commits are execution lineage, not an alternative finalizer identity.

## Cache and progress policy

All V4-A jobs use a 360-minute hard job ceiling. Long checkpointed
materialization steps use a 330-minute soft process ceiling so the remaining
job window is reserved for `always()` checkpoint persistence and cleanup.

For Capital, Financing, issuer sources, SZSE migration, Prices, Policy, and
Derived, engineering checkpoint keys use a semantic-progress identity rather
than the workflow commit SHA. The semantic-progress identity covers:

- materialization/PIT entrypoints;
- recursively imported local semantic modules;
- semantic reference contracts;
- the exact date window;
- exact upstream persistent bundle identities.

It deliberately excludes workflow YAML, timeout settings, cache wiring,
qualification-gate orchestration, receipt writing, packaging, and publication.
Those operational files remain governed by the formal persistent-bundle
producer identity and do not gain cross-version evidence compatibility.
GitHub Actions cache is engineering acceleration only. It is not a canonical
cross-stage data bus and is never sufficient evidence for final qualification.

The formal reusable handoff is an immutable release bundle whose compatibility,
upstream identities, receipt, and hashes are revalidated.

Long-running Fundamental work uses layered engineering resume state:

1. immutable completed work-unit bundle;
2. current semantic-generation durable progress;
3. exact audited compatibility bridge for a proven non-semantic repair;
4. read-only legacy/query/index cache;
5. provider recomputation.

Query/index caches and document/parser progress caches are independent assets.
A hit in one layer must not suppress another layer merely because both are
implemented with GitHub Actions cache.

Cross-commit engineering progress reuse is allowed when the semantic-progress
fingerprint, date window, and exact upstream persistent bundle identities match.
Older cache generations that did not carry this identity still require an
explicit frozen compatibility bridge identifying the exact old
run/commit/fingerprint/window and proving that materialization, checkpoint, PIT,
evidence, and qualification semantics did not change. In both cases the reuse
remains engineering-only, grants no qualification, and fails closed on mismatch.

Operational changes such as timeout or cache orchestration must not silently
weaken semantic fingerprinting. Semantic changes require a new compatible
generation or fresh computation.

After cancellation, inspect the actual cache-save outcomes of every active work
unit before deciding what the replacement run must recompute.

### Fundamental recovery-branch procedure

GitHub Actions cache visibility is branch/ref-scoped. The Fundamental durable
progress used by the current presentation-classifier recovery chain was created
on:

`v4a/fundamental-resume-295710`

Therefore, while the frozen presentation progress bridge remains active:

1. do not dispatch the recovery run directly from `main`;
2. verify the recovery branch can be fast-forwarded cleanly to the intended
   latest `main` commit;
3. fast-forward the recovery branch without force;
4. dispatch `v4a-06-fundamental-earnings` manually from that recovery branch;
5. require the workflow's pre-materialization branch guard to pass;
6. verify the restore chain selects the newest readable exact progress before
   older fallback layers;
7. verify resume effectiveness from runtime counters, not only green cache steps.

For a fully resumed unit, the expected performance proof is normally:

- `executed_symbol_queries = 0`;
- filing/earnings `executed_documents = 0`;
- non-zero resumed query/document counts;
- current semantic materialization completes;
- qualification gate passes;
- a new current-generation immutable work-unit bundle is published.

The first real `fundamental-v9-presentation-recheck-v2` recovery run,
`35451946515`, validated this path for units 0-2: the workflow restored the
audited durable progress, executed zero provider symbol queries, executed zero
new document downloads/parses, reran current-semantic materialization and
qualification, and completed in roughly one minute per unit instead of the prior
multi-tens-of-minutes provider/parser path.

A targeted semantic repair may legitimately execute non-zero re-proof work only
for the affected subset. For the 688122 presentation incident, the relevant
proof is exact official-byte download plus checkpoint SHA verification before
the corrected presentation classifier is allowed to resolve the conflicting
carrier.

After all 16 work units have immutable bundles under the current presentation
generation, the recovery branch/bridge is logically eligible for retirement.
However, physical cleanup must respect the producer-identity handoff freeze
below.

### Successful recovery closure — run 35451946515

The current presentation-recheck recovery completed successfully on
`v4a/fundamental-resume-295710` at source commit
`f55033f31e00bfed5bda798929dc5dccecbceac7`.

Observed result:

- 16 / 16 work units passed;
- the final package job passed;
- all 16 qualified shard artifacts were downloaded and digest-verified during
  group assembly;
- the Fundamental group contained 208 files;
- total workflow wall-clock was approximately 10 minutes 50 seconds;
- fully resumed units such as 0-2 completed in roughly one minute with
  `executed_symbol_queries = 0` and `executed_documents = 0`;
- the previously failing unit 5 restored the newest compatible progress and
  completed the repaired path in roughly six minutes before qualification and
  immutable publication.

The published canonical persistent Fundamental handoff is:

- asset base:
  `v4a-fundamental-20220104-20260917-56e8c44ca678668bfc17`;
- bundle identity:
  `f558db45af7a9e8b5e69dad1275f98e93e6ee49c09914fa591edd4348cfde4c4`;
- compatibility key:
  `56e8c44ca678668bfc17fd7ef5da5a9c32503dcbac03eb86fa3963ecd0cf751b`;
- archive SHA-256:
  `c1df2ec495657f52cf34935feb5ab94c7159f43e4cbf6f24bb8fbcf43ffdb54e`;
- stage receipt SHA-256:
  `2b27a25584e22521811ceebfdc873363d6a8fc43cf82d1b442d8ced121b91770`;
- original source commit:
  `f55033f31e00bfed5bda798929dc5dccecbceac7`.

### Producer-identity freeze before Derived

Do **not** modify files contributing to the Fundamental producer fingerprint
before the current persistent bundle has been consumed by the required
downstream V4-A stages.

This includes, in particular:

- `.github/workflows/v4a-fundamental-earnings.yml`;
- `reference/v4a_fundamental_checkpoint_reuse_contract_v1.json`;
- `reference/v4a_fundamental_pit_state_contract_v1.json`;
- `reference/v4a_qualification_tolerance_contract_v1.json`;
- the Fundamental materialization/package entrypoints and recursively imported
  producer modules.

The one-time recovery branch guard and presentation bridge have completed their
operational purpose, but their **physical removal is deferred** until the
published Fundamental compatibility identity has been consumed by
`v4a-09-derived` and the required canonical handoff chain.

Documentation, incident-review, run-plan, and generic engineering-contract
changes outside the Fundamental producer fingerprint may proceed without
invalidating this handoff.

This is deliberate: removing a harmless one-time guard immediately after a
green run would change the Fundamental producer fingerprint and could cause
Derived to reject or fail to locate the just-published valid bundle.

Before dispatching `v4a-09-derived`, perform an upstream-identity preflight:
resolve the current Fundamental persistent-stage key using the same frozen
window and Shared manifest, and require the result to match the already
published Fundamental handoff:

- expected asset base:
  `v4a-fundamental-20220104-20260917-56e8c44ca678668bfc17`;
- expected compatibility key:
  `56e8c44ca678668bfc17fd7ef5da5a9c32503dcbac03eb86fa3963ecd0cf751b`.

If the current key differs, stop before Derived. Inspect which producer file
changed. Do not automatically rerun Fundamental merely because a cleanup or
documentation-adjacent engineering change accidentally changed its producer
identity.

### Qualification boundary after public success

A successful Fundamental workflow and persistent bundle mean the public
Fundamental stage has completed its qualified input production and immutable
handoff.

They do **not** by themselves mean:

`HISTORICAL_DATA_QUALIFIED`.

The remaining V4-A chain still requires the applicable downstream assembly,
canonical public finalizer, private PostRun Preflight, and private Artifact
Intake. The final state must remain either the intake-supported qualified state
or an honest fail-closed / data-insufficient state.

## Trigger policy

Every V4-A workflow in this architecture is `workflow_dispatch` only.

There are no:

- schedules;
- `workflow_run` triggers;
- push triggers;
- pull-request triggers;
- repository-dispatch chains;
- workflow self-launches.

The human operator controls progression between stages.

## Stop / authorization boundary

Mechanical repairs inside an existing stage may proceed under normal engineering
authority.

Explicit authorization remains required for changes to:

- canonical evidence-source eligibility;
- evidence qualification rules;
- PIT/no-lookahead semantics;
- model/factor/threshold definitions;
- frozen research universe;
- holdout/OOS qualification;
- public/private security boundary;
- Production or trading authority;
- automatic workflow triggers.

Persistent reuse does not widen any of those permissions.
