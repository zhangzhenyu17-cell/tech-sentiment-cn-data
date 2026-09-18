# V4-A Preflight Incident Review — September 2026

## Purpose

This document records the engineering lessons from stabilizing the V4-A Capital/PIT public-data preflight before formal historical materialization.

The goal is not to preserve every debugging detail. It is to preserve the failure patterns, root causes, and engineering rules that should prevent similar multi-run diagnosis in future qualification tasks.

Final successful preflight:

- workflow: `qualify-capital-inputs`
- run: `35368616602`
- source commit: `9d9e0e7b66c3364701728ad9df3a11f23264e8af`
- result: all five preflights and the unified gate succeeded; all expensive jobs correctly skipped under `preflight_only=true`.

## What went wrong

### CNINFO transport was initially mistaken for a parser problem

Early runs failed on CNINFO HTTP 403 responses from GitHub hosted runners.

The correct fix was transport-only:

- preserve the same immutable CNINFO bulletin identity;
- keep HTTPS;
- use same-provider deterministic download fallback;
- add session/browser-fingerprint transport only after ordinary paths fail;
- reject redirects outside the official allowlist.

Lesson: classify transport before changing parsing logic.

### Hosted-runner WAF behavior was intermittent across providers

SSE ETF scale and CSRC endpoints behaved differently across runs:

- JSON decode failure from an HTML/challenge response;
- 403 under one browser transport attempt;
- timeout despite bounded retry;
- later success using the same semantic query through a stable same-provider interface/session path.

Lesson: "worked once" is not a stable transport contract. Representative preflight must exercise the hosted-runner path, not only local/unit fixtures.

### Real PDF layouts were more irregular than synthetic tests

Annual reports exposed:

- Chinese labels split across physical lines;
- numeric table cells interrupting visible labels;
- per-share units split over lines;
- Unicode/control-character artifacts;
- different text-layer behavior across PDF engines.

The repair pattern that worked was:

- preserve the same official PDF bytes;
- separate text-layer extraction from semantic parsing;
- add bounded non-OCR text-layer fallbacks;
- normalize layout artifacts narrowly;
- add real-layout regression fixtures;
- preserve explicit unit proof and fail-closed missing facts.

Lesson: parser tolerance may increase only for layout representation, not for semantic evidence requirements.

### The longest debugging loop was caused by preflight-production contract drift

The decisive failure log eventually showed:

`title='贵州茅台2022年年度报告（英文版）'`

The formal filing materializer already excluded English translations, but the preflight had its own looser title filter and selected `iloc[0]`.

As a result, repeated work attempted to make a Chinese filing parser understand the wrong document.

The final fix:

- preflight reuses production `is_numeric_financial_filing_title()`;
- immutable duplicate identities are deduplicated;
- explicit publication ordering replaces source row order;
- true same-time ambiguity fails closed.

Lesson:

> A representative preflight may reduce scope, but it must not invent a parallel eligibility contract.

### Better diagnostics changed the quality of debugging

Initially, errors reported only a missing fact or parser failure.

Later diagnostics added:

- role;
- symbol;
- selected title;
- raw and eligible candidate counts;
- canonical URL;
- retrieval URL;
- SHA-256;
- parser error.

That additional identity exposed the English-version selection immediately.

Lesson: diagnostic identity is part of pipeline quality, not optional logging.

## Failure progression

The important progression was:

1. multi-provider connectivity/transport failures;
2. CNINFO WAF/403 resolved;
3. parser reached real PDF facts and exposed layout failures;
4. SSE/CSRC hosted-runner instability resolved with same-provider transport hardening;
5. shared/static/issuer/policy all became green;
6. CNINFO remained the only failure;
7. richer diagnostics revealed English-version candidate selection;
8. preflight was aligned with production selection;
9. run `35368616602` passed all five preflights.

The key point is that later failures were not regressions in earlier fixes. They were deeper layers becoming visible after the prior layer was repaired.

## What we should do earlier next time

### Add identity-rich diagnostics before the first live run

For document-driven probes, log document title/id/hash from the first implementation.

### Reuse production predicates from day one

A preflight selector should import or call production eligibility logic rather than duplicate strings/regexes.

### Separate protocol smoke from semantic smoke

Treat these as different assertions:

- can the official source be reached?
- is the exact immutable document reachable?
- does it have a usable text layer?
- can standard facts be parsed?
- does the resulting evidence satisfy PIT/integration contracts?

### Persist diagnostics

Small diagnostic artifacts are cheap and allow postmortem inspection without rerunning a provider.

### Convert every deterministic failure into a regression test

If a failure is fixed only in code and not in a test, it is likely to recur.

## Practices that worked well

- five independent preflights in parallel;
- unified expensive-materialization gate;
- bounded timeout budgets;
- fail-closed behavior;
- official-source-only transport fallbacks;
- no HTTP downgrade;
- no unofficial canonical substitution;
- immutable PDF SHA-256 binding;
- parser regression fixtures;
- manual-only full qualification workflow;
- explicit `preflight_only` mode;
- new run after every code-changing fix rather than rerunning an old SHA.

## Practices to avoid

- interpreting non-JSON/403 as data absence;
- adding retries before classifying deterministic failures;
- loosening parser semantics before confirming document identity;
- relying on source row order;
- keeping separate "smoke-test eligibility" and production eligibility rules;
- launching full historical materialization to discover a representative source failure;
- changing evidence-source rules merely to restore a green workflow.

## Resulting engineering rules

The following rules are now considered part of the preferred repository practice:

1. representative preflights must share production eligibility/identity semantics;
2. every provider/document failure should be classified before repair;
3. official transport redundancy must preserve source identity;
4. document identity is verified before parser modification;
5. text-layer fallbacks remain on the same immutable bytes;
6. deterministic failures become tests;
7. diagnostics include enough identity to explain selection and retrieval;
8. preflight-only success precedes expensive full materialization after data-path changes;
9. code changes require a new SHA/run for validation;
10. evidence and PIT requirements are never weakened to make CI green.

See [Public Data Qualification Runbook](public_data_qualification_runbook.md) for the reusable operating procedure.
