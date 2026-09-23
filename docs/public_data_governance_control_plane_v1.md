# Public Data Governance Control Plane V1

## Status

`PUBLIC_DATA_GOVERNANCE_CONTROL_PLANE_V1` is a repository-native engineering governance layer for public data products.

It is deliberately **not** an evidence authority. It cannot promote evidence, change PIT/no-lookahead semantics, change a model/factor/threshold/signal, change production authority, or authorize trading.

Machine-readable contracts:

- `reference/public_data_governance_control_plane_v1.json`
- `reference/public_data_quarantine_ledger_v1.json`

CI enforcement:

- `python scripts/audit_public_tree.py`
- `pytest -q tests/test_public_data_governance_control_plane.py`

No new workflow or automatic trigger is introduced by this control plane. Existing repository CI already executes the public-tree audit and pytest suite.

## Why this layer exists

The repository already had strong local controls for:

- source eligibility and transport;
- PIT/no-lookahead;
- provenance and immutable artifact identity;
- preflight/full-run parity;
- resumability and cache identity;
- private handoff boundaries;
- fail-closed data insufficiency.

Those controls were distributed across individual contracts, workflows, tests and runbooks. The missing layer was a single answer to these questions:

1. What public data products are governance-sensitive?
2. What contract and producer define each product?
3. What lineage identity must be retained for a run and dataset?
4. What is structural quality versus semantic correctness?
5. Which semantic changes invalidate which cache layers?
6. Which artifacts are quarantined and therefore forbidden from downstream use?
7. Can any public state grant private qualification, evidence promotion, production authority or trading authority?

V1 centralizes those answers without reclassifying existing evidence.

## External patterns adapted

The design borrows small, useful concepts from mature open-source governance systems without importing their runtime stacks.

### OpenLineage

Reference project: `OpenLineage/OpenLineage`.

Adopted concepts:

- **Job** identity is distinct from a **Run** identity.
- Produced data is represented as a named **Dataset** rather than only as a workflow artifact filename.
- Provenance, schema, quality and other metadata are represented as distinct facets instead of one overloaded status field.

We do not add an OpenLineage server, emitter or dependency. The repository already has strong immutable run/artifact identities; V1 only normalizes how those identities are described.

### Open Data Contract Standard (ODCS)

Reference project: `bitol-io/open-data-contract-standard`.

Adopted concepts:

- a data contract is an explicit producer-consumer agreement;
- schema and data-quality expectations are first-class;
- authoritative definitions and service expectations should be explicit when applicable.

We do not replace existing frozen repository contracts with the ODCS schema. Existing contracts remain authoritative. V1 only requires governance-sensitive products to point to their exact existing contract and producer.

### GX Core / Great Expectations

Reference project: `fivetran/great_expectations`.

Adopted concepts:

- data-quality requirements should be executable expectations, not prose alone;
- validation results should preserve institutional knowledge about failure classes.

We do not add GX as a dependency. Existing pytest and audit contracts already provide the appropriate lightweight execution surface for this repository.

## Lifecycle

The default public-data lifecycle is:

1. `REPOSITORY_CONTRACT`
2. `DATA_CONTRACT`
3. `LINEAGE_PROVENANCE`
4. `STRUCTURAL_QUALITY`
5. `PIT_NO_LOOKAHEAD`
6. `SEMANTIC_ARTIFACT_REVIEW`
7. `QUARANTINE_CHECK`
8. `PUBLIC_HANDOFF_READY`

These are governance gates, not evidence states.

A successful GitHub Actions run proves only that the declared execution completed under its workflow conditions. It does **not** prove that parsed values semantically represent the intended fields, and it never grants private qualification.

## Structural quality versus semantic correctness

Structural acceptance asks questions such as:

- does the artifact have the declared schema?
- is provenance present?
- is scope accounting complete?
- are identities unique where required?
- is the artifact digest available and bound to the run?

Semantic review asks different questions:

- does the parser extract the intended financial field rather than a note reference or year token?
- are units locally proven?
- are current/prior-period columns distinguished correctly?
- do revisions remain PIT-safe?
- do representative real layouts behave as the contract claims?

The Extended PIT incident demonstrated that structural success can coexist with semantic risk. Therefore structural acceptance can never substitute for semantic review when parser/builder semantics matter.

## Diagnostic anomalies are not repair thresholds

Unusual values can trigger investigation. They must not automatically rewrite or reject facts merely because they are numerically large, small, round, year-like, or otherwise surprising.

Required discipline:

- anomaly -> investigate layout/source/identity;
- verified semantic defect -> repair the parser/contract narrowly;
- add positive and negative regression coverage;
- re-materialize only affected semantic layers;
- keep ambiguous layouts fail-closed.

Do not convert anomaly heuristics into issuer-specific hard-coded values or hidden validity thresholds.

## Semantic change and cache invalidation

Cache invalidation follows semantic identity, not convenience.

### Transport-only change

Reprove source/document identity. Do not invalidate unrelated semantic outputs automatically.

### Query/index semantic change

Invalidate the affected query/index layer and downstream derived products.

### Parser semantic change

Invalidate:

- parsed-document facts;
- downstream outputs derived from those facts.

A query/index cache may remain reusable only when its query/source identity is unchanged. Parsed facts must not be reused across incompatible parser semantic generations.

This is the pattern used for the Extended PIT v1 -> v2 parser repair: retain safe announcement/index discovery, invalidate v1 parsed facts, and rematerialize with the v2 semantic identity.

### PIT or evidence semantic change

This control plane cannot authorize it. Such a change remains outside ordinary engineering autonomy and requires explicit human authorization under the project boundary.

## Quarantine

Quarantine is logical isolation, not deletion.

An artifact enters quarantine when its execution identity is known but its semantic correctness is not established strongly enough for downstream consumption.

For an **active** quarantine entry:

- downstream consumption is false;
- private qualification intake is false;
- evidence promotion is false;
- production change is false;
- trading authority is false.

The artifact may remain stored for forensic comparison.

Release requires either:

- a replacement artifact produced under corrected semantic identity; or
- exact semantic reproof showing the quarantined concern does not apply.

Release itself still does not grant private qualification.

## Initial quarantine record

V1 records the successful Extended PIT v1 aggregate from run `35847821341` as the first active quarantine entry:

- source SHA: `9f71cd2d42735911fff26294a350f8ee4635084c`
- artifact: `fundamental-extended-pit-coverage-audit`
- artifact ID: `10744657001`
- digest: `sha256:4082a76bcd6567d3e27f07950842f3bc951efc80df834ccb8a4de951164d3069`
- parser generation: `official-filing-extended-pit-primitives-v1`

Reason: outcome-blind semantic review found parser layouts capable of accepting ambiguous amount-column structure. The artifact remains structurally useful for forensic comparison but is blocked from qualification intake.

The expected replacement generation is `official-filing-extended-pit-primitives-v2-column-safe`. A new successful run is not sufficient by itself: the replacement must also pass structural acceptance and outcome-blind semantic artifact review.

## Registry scope

V1 starts with critical/shared rails rather than forcing a risky one-shot migration of every historical public artifact.

Registered initially:

- Fundamental Extended PIT Historical Coverage;
- V4-A Capital/PIT public handoff;
- Prospective Context Raw Preopen V2;
- IPO Aftermarket Public V1.

Registration does not alter the prior qualification/evidence state of any rail.

New governance-sensitive public data products should register their:

- stable product id;
- exact existing contract path;
- exact producer/workflow path;
- logical dataset name;
- private consumer boundary;
- explicit statement that public workflow success cannot grant private qualification.

Legacy rails can be migrated incrementally when they are next materially changed. V1 must not invent a new status for an untouched historical product merely to achieve registry completeness.

## CI behavior

`scripts/audit_public_tree.py` now fails closed when it detects:

- missing governance contracts;
- an authority flag that would let governance promote evidence/change PIT/change production/authorize trading;
- lifecycle gate drift;
- a registered product pointing to a missing contract or producer;
- a registered product claiming public workflow success can grant private qualification;
- a quarantine contract whose blocking rules are disabled;
- an active quarantine entry that allows downstream consumption/private qualification/evidence promotion/production/trading;
- malformed source SHA or artifact SHA-256 identities.

`tests/test_public_data_governance_control_plane.py` locks the intended contract semantics and the first quarantine identity.

## Relationship to existing governance

This control plane does not replace:

- `docs/public_data_qualification_runbook.md`;
- `docs/long_running_engineering_execution_protocol.md`;
- `reference/long_running_engineering_execution_contract_v2.json`;
- product-specific PIT/provenance/qualification contracts;
- the private repository Global Safety Contract.

Instead:

- the long-running protocol controls efficient and safe execution;
- product-specific contracts define exact data semantics;
- this control plane provides repository-wide discovery, lifecycle separation and quarantine;
- private contracts remain the only place that can evaluate private qualification/evidence/production/trading boundaries.

## Adoption rule

For ordinary future engineering work:

1. reuse an existing registered product id when semantics are unchanged;
2. update semantic identity when parser/builder meaning changes;
3. invalidate only the affected cache/data layers;
4. quarantine any previously successful artifact whose semantic correctness becomes materially uncertain;
5. run the normal public-tree audit and pytest suite;
6. stop at `PUBLIC_HANDOFF_READY`; do not infer private qualification.
