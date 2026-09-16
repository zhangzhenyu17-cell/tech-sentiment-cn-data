# tech-sentiment-cn-data

Public utilities for reconstructing point-in-time A-share index membership and preparing validated market-data bundles.

This repository contains only the market-data layer. It does not contain sentiment-model features, thresholds, portfolio rules, personal holdings, forward-validation records, or investment signals. Generated bundles are assembled from an explicit file allowlist and are rejected when point-in-time membership or coverage checks fail.

## Local checks

```bash
python -m pip install -e ".[dev]"
python scripts/audit_public_tree.py
pytest -q
```

Install the optional data dependency before live downloads:

```bash
python -m pip install -e ".[data,dev]"
```

The scheduled workflow publishes three assets under the `latest-market-bundle` release: a compressed bundle, its SHA-256 checksum, and a machine-readable manifest. The bundle contains public market inputs only.

No open-source license is granted by this repository unless a license file is added later.
