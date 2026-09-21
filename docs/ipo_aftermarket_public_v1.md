# IPO Aftermarket Public Inputs V1

Status: **PUBLIC INPUT MATERIALIZATION ONLY / NO RESEARCH RESULT**

This contract supports the private IPO-AFTERMARKET-1 study without moving private
research semantics into the public repository.

Allowed materialization:

- IPO symbol / name / board / listing date / issue price and provider-reported issue metadata;
- raw, unadjusted post-listing OHLC / amount / turnover, capped at the first 120 observed sessions;
- public restricted-share unlock dates plus an explicit per-symbol query status;
- raw broad-A benchmark price history for index 000985;
- provider / file provenance, hashes, missingness and download errors.

The public bundle deliberately excludes:

- Candidate A/B/C definitions or hits;
- private thresholds;
- IPO underpricing / aftermarket returns;
- MAE / MFE / max drawdown;
- research group statistics or verdicts;
- model output, portfolio holdings, trading permissions or private evidence.

Provider tables may expose first-day performance fields. Those fields are explicitly
discarded before the public metadata bundle is written. The private study must derive
all authorized historical outcomes from the allowlisted raw inputs after preregistration
has been frozen.

The workflow is manual `workflow_dispatch` only. No schedule, push, pull_request or
workflow_run trigger is introduced.
