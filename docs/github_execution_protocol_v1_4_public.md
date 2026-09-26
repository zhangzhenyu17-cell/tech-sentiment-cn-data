# GitHub v1.4 — Public Data Repository Execution Protocol

Status: **CURRENT PUBLIC-DATA ENGINEERING GOVERNANCE / NO PRIVATE MODEL OR EVIDENCE AUTHORITY**

Project command: **`按 GitHub v1.4 执行。`**

Effective date: **2026-09-26**

This repository is the public-data side of the project. GitHub v1.4 here means:

**public data compute is cost-relaxed, scope-strict, reproducible, resumable, and incapable of granting private model/evidence/Production/trading authority.**

## 1. Allowed autonomous work

Within an already defined task:

- public market-data acquisition;
- public point-in-time reconstruction;
- source/transport validation;
- parser/materialization engineering;
- allowlisted bundle packaging;
- manifest/checksum/release publication;
- deterministic sharding/checkpoint recovery;
- normal tests/CI and mechanical repairs;
- failure-learning and observability improvements.

## 2. Forbidden public expansion

Never move into this repository:

- private model logic or thresholds;
- investment signals;
- private research results;
- OOS/holdout private evidence;
- personal holdings or portfolio data;
- credentials/secrets;
- private Production or trading authority.

Public runner cost or availability never authorizes broader research.

## 3. Long-running execution

Current detailed mechanics remain:

- `docs/long_running_engineering_execution_protocol.md`
- `reference/long_running_engineering_execution_contract_v2.json`

For >=30-minute or >=200-item work, default to:

- representative preflight/pilot;
- deterministic work units;
- durable query/document progress;
- immutable successful unit publication;
- bounded provider parallelism;
- heartbeat + measured ETA;
- repeated-failure circuit breaker;
- separate aggregation/finalizer;
- smallest-rerun recovery.

Already immutable successful units must not be recomputed because sibling units fail.

## 4. Failure taxonomy

Separate:

- hosted-runner/platform allocation;
- deterministic code/orchestration;
- provider transport/protocol;
- content encoding/class;
- parser/layout;
- qualification/publication;
- performance regression.

Do not misclassify HTTP/WAF/HTML/gzip transport failures as data absence.

## 5. Public/private handoff

A valid public bundle requires exact:

- source/run/ref identity;
- explicit file allowlist;
- manifest;
- cryptographic hashes;
- immutable publication identity.

Public stage success does not equal private evidence qualification.

## 6. Workflow governance

New workflows default manual-only.

Any new `schedule`, `workflow_run`, `push`, or `pull_request` automatic trigger requires explicit project authorization and a documented necessity.

Historical automatic research exceptions are not templates for new automation.

## 7. Retry and cancellation

- smallest rerun first;
- preserve immutable units;
- inspect durable progress before cancel;
- verify actual save/artifact outcomes after cancel;
- live run remains bound to launch SHA;
- no broad restart solely to obtain a green status.

## 8. Execution channels

Use GitHub connector/API for supported actions.

If an exact authorized action is unsupported, use authenticated browser only when actually available; otherwise ask for the smallest exact manual action.

Read-only exact-artifact replay may support audit/diagnosis but does not create an official evidence-writing receipt.

## 9. Completion

Report in Chinese:

- changes;
- tests/CI;
- run/artifact/PR identity;
- whether any private/research boundary was touched;
- known limitations;
- exact remaining manual action, if any.

Stop at acceptance.
