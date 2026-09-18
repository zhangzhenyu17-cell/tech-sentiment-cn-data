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

Existing assets are never overwritten. A repeated publication succeeds only if
all existing bytes are identical.

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
- `v4a-04-issuer-source` with `source=szse`
- `v4a-06-fundamental-earnings`
- `v4a-07-prices`
- `v4a-08-policy`

CNINFO, SSE, and SZSE issuer archives are deliberately separate persistent
bundles. A transport failure in one issuer source must not invalidate the other
two.

Fundamental/Earnings and Prices retain bounded four-shard parallelism internally
but publish one persistent group bundle after all shards succeed.

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

1. keep every already-published successful stage bundle;
2. diagnose and repair only the failed stage or its direct producer contract;
3. run tests/CI for the code change;
4. rerun only that manual stage;
5. rerun only downstream aggregate stages whose upstream bundle identity changed;
6. run the finalizer after the dependency chain is complete.

Examples:

- Policy fix: rerun Policy -> Derived -> Finalizer.
- SSE issuer fix: rerun Issuer Source(SSE) -> Issuer Aggregate -> Derived -> Finalizer.
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

## Cache policy

GitHub Actions cache is producer-local acceleration only. It is not a canonical
cross-stage data bus and is never sufficient evidence for final qualification.

Cache keys bind both the stage compatibility identity and the exact repository
source commit. Producer checkpoints therefore never bridge code revisions.

Cross-commit reuse is provided only by a successfully sealed persistent stage
bundle whose producer fingerprint and upstream bundle identities are revalidated.

The immutable release bundle is the formal reusable inter-workflow handoff.

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
