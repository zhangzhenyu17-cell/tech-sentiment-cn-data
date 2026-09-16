# 931152 secondary evidence probes

These scripts are outcome-free public-data probes only. They do not calculate sector temperature, events, returns, MAE/MFE, or touch the 2024+ holdout.

- `probe_eastmoney_159992_holdings.py`: fetches 159992 historical fund holdings from EastMoney/Tiantian Fund for 2020-2023 and records raw response hashes plus candidate disclosure batches.
- `probe_secondary_931152_reconstruction.py`: uses dated Sina related-index membership intervals to independently filter/cross-check candidate sets around the registered 931152 rebalance nodes.

Artifacts are candidate evidence until reviewed against `docs/secondary_evidence_policy.md` and the membership gate. A conflict never auto-resolves.
