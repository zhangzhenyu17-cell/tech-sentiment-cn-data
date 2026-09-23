# Repository boundary

This public repository is limited to market-data acquisition, point-in-time universe reconstruction, input validation, and public bundle packaging.

Do not add model features, scoring rules, thresholds, portfolio logic, personal holdings, backtest conclusions, forward-validation records, private-repository credentials, or cross-repository write access.

All changes must pass:

```bash
python scripts/audit_public_tree.py
pytest -q
```

A bundle must be created from the explicit allowlist in `tech_sentiment.bundle`; never archive a directory wholesale.

## Public data governance

Governance-sensitive public data products and semantic changes must follow `docs/public_data_governance_control_plane_v1.md` and the machine-readable contracts in `reference/public_data_governance_control_plane_v1.json` and `reference/public_data_quarantine_ledger_v1.json`.

A successful public workflow does not grant private qualification, evidence promotion, production authority, or trading authority. Structural artifact success does not replace outcome-blind semantic review when parser/builder semantics matter.

If an artifact is in an active quarantine entry, do not consume it downstream or pass it into private qualification intake. Parser semantic changes invalidate parsed-document facts and affected derived outputs; upstream query/index caches may be reused only when their own semantic identity is unchanged.
