# Repository boundary

This public repository is limited to market-data acquisition, point-in-time universe reconstruction, input validation, and public bundle packaging.

Do not add model features, scoring rules, thresholds, portfolio logic, personal holdings, backtest conclusions, forward-validation records, private-repository credentials, or cross-repository write access.

All changes must pass:

```bash
python scripts/audit_public_tree.py
pytest -q
```

A bundle must be created from the explicit allowlist in `tech_sentiment.bundle`; never archive a directory wholesale.
