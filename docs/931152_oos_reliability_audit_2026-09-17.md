# 931152 OOS input qualification reliability preflight — 2026-09-17

Status: `ENGINEERING_PREFLIGHT_ONLY / HOLDOUT_UNOPENED`

This audit covers only the public, outcome-free input qualification path for the frozen Innovation Drug V0 holdout. It does not compute or inspect model events, forward returns, support conclusions, or holdout outcomes.

## Frozen semantics preserved

- index: `931152`
- holdout: `2024-01-01` through `2026-09-11`
- stock warmup start: `2022-01-01`
- stock adjustment: `qfq`
- point-in-time membership and 2023-12-31 frozen anchor unchanged
- ETF same-report membership cross-check unchanged
- strict member-day limit-rule coverage remains `>= 95%` every trading day
- official index rail remains CSI `csindex:index_perf`
- workflow remains `workflow_dispatch` only
- no model, threshold, weighting, universe, Production, Evidence qualification, V2, or Execution Sizing semantics changed

## Failure surfaces reviewed

1. EastMoney ETF holdings: transport retries already exist; builder now re-fetches only missing report years when a successful response is semantically incomplete.
2. Sina PIT membership: provider-level bounded retries remain fail-closed; workflow concurrency is reduced to 3 and failed symbols receive one sequential second pass.
3. Stock price history: EastMoney/Tencent fallback is retained; retries are raised to 2 per provider, a short inter-symbol delay is added, and request timeout is explicit.
4. Official CSI index history: official source is retained; bounded retry count is raised without changing data semantics.
5. BaoStock lifecycle/limit inputs: login and per-query transient failures receive bounded reconnect/retry. Empty semantic responses remain invalid. Failures still flow into the strict coverage gate.
6. Special-day semantics: existing lifecycle derivation remains unchanged. Unknown/no-limit/suspended rows do not become eligible merely because data was fetched.
7. Strict coverage: expected member-days remain independent of provider row availability; missing/invalid rows stay in the denominator; the 95% daily gate is unchanged.
8. Packaging: the workflow verifies qualification status and outcome-free flags before packaging, uses deterministic tar/gzip metadata, and verifies the resulting SHA256.
9. Runtime: job timeout is increased from 60 to 120 minutes to accommodate bounded provider backoff; this does not relax any qualification condition.

## Stop rule

If the next run fails because a frozen semantic gate genuinely does not pass (membership mismatch, incomplete required report set after bounded retries, missing price history after provider fallback, or strict limit-rule coverage below 95%), do not lower the gate. Report the qualification failure as data insufficient and keep the holdout unopened.
