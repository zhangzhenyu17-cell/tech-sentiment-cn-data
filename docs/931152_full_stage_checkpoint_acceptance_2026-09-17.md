# 931152 full-stage checkpoint acceptance

Acceptance criteria for the resumable public-data qualification engineering change:

1. Existing public-tree audit passes.
2. Full pytest suite passes.
3. Corrupted or identity-mismatched checkpoints are not silently accepted.
4. A partial stock-history checkpoint only refetches missing symbols.
5. Official CSI and ETF checkpoints preserve source/date identity.
6. A successful BaoStock stage can be reused only for the identical PIT universe.
7. The workflow remains manual-only (`workflow_dispatch`).
8. No model, threshold, holdout, evidence-qualification, production, V2, sizing, or trading-authority semantics change.
