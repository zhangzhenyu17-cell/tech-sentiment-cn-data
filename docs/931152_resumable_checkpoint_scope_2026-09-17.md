# 931152 OOS input qualification resumable checkpoint scope

This engineering change reduces repeated public-data work after a failed manual qualification run without changing research or evidence semantics.

Checkpointed public stages:

- EastMoney tracking ETF holdings response text plus identity metadata.
- Sina related-index pages plus original response digest metadata.
- A-share stock-history CSV with provider/date/adjustment identity metadata; retries fetch only missing symbols.
- Official CSI 931152 index-history CSV with index/date identity metadata.
- BaoStock successful derived stage outputs with PIT-universe identity metadata.

All checkpoints are transport/recovery artifacts only. They are accepted only after schema, source/date/parameter, file/text hash, and stage-specific structural validation. Invalid checkpoints are ignored and the affected public stage is fetched again.

Unchanged qualification semantics:

- frozen holdout start/end and stock warmup dates;
- frozen 2023-12-31 membership anchor;
- exact 931152 membership parsing and half-open interval semantics;
- ETF same-report cross-checks;
- official CSI index rail;
- strict daily limit-rule coverage threshold of 95%;
- Innovation Drug V0 model, weights, thresholds and outcome isolation;
- no production/evidence promotion and no automated trading authority.

The workflow remains `workflow_dispatch` only. No schedule, push, pull_request, workflow_run, or other automatic trigger is added.
