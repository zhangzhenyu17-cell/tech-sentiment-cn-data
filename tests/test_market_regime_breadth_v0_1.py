from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from tech_sentiment.market_regime_breadth_v0_1 import (
    STATUS_PARTIAL,
    STATUS_QUALIFIED,
    compute_direct_security_breadth,
    validate_direct_security_breadth,
)


def _snapshot() -> pd.DataFrame:
    return pd.DataFrame([
        {"symbol": "600000", "name": "A", "latest": 100.0, "prev_close": 99.0, "timestamp": "15:00:00", "market_prefix": "sh"},
        {"symbol": "000001", "name": "B", "latest": 50.0, "prev_close": 51.0, "timestamp": "15:00:00", "market_prefix": "sz"},
    ])


def _history() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=130)
    rows = []
    for symbol, start, step in [("600000", 80.0, 0.2), ("000001", 70.0, -0.15)]:
        for i, date in enumerate(dates):
            rows.append({"date": date, "symbol": symbol, "close": start + step * i, "provider": "fixture"})
    frame = pd.DataFrame(rows)
    date = frame["date"].max()
    frame.loc[(frame["symbol"] == "600000") & (frame["date"] == date), "close"] = 100.0
    frame.loc[(frame["symbol"] == "000001") & (frame["date"] == date), "close"] = 50.0
    return frame


def test_direct_breadth_qualifies_only_with_complete_direct_queries() -> None:
    history = _history()
    market_date = history["date"].max().date().isoformat()
    diag, summary = compute_direct_security_breadth(
        _snapshot(), history, market_date=market_date,
        query_coverage={"query_symbol_count": 2, "complete_query_count": 2, "query_error_count": 0},
    )
    assert summary["status"] == STATUS_QUALIFIED
    assert summary["qualified_trend_breadth"] is True
    assert summary["metric_denominators"]["above_ma20_ratio"] == 2
    assert summary["metric_denominators"]["new_high_252_ratio"] == 2
    assert summary["scope_limitations"]["full_sh_sz_bj_market_breadth_claimed"] is False
    assert summary["authority"]["state_rewrite_allowed"] is False
    assert len(diag) == 2


def test_direct_breadth_fails_closed_on_query_error() -> None:
    history = _history()
    market_date = history["date"].max().date().isoformat()
    _, summary = compute_direct_security_breadth(
        _snapshot(), history, market_date=market_date,
        query_coverage={"query_symbol_count": 2, "complete_query_count": 1, "query_error_count": 1},
    )
    assert summary["status"] == STATUS_PARTIAL
    assert summary["qualified_trend_breadth"] is False


def test_direct_breadth_rejects_future_rows() -> None:
    history = _history()
    market_date = history["date"].max().date().isoformat()
    extra = pd.DataFrame([{"date": pd.Timestamp(market_date) + pd.Timedelta(days=1), "symbol": "600000", "close": 100.0, "provider": "fixture"}])
    with pytest.raises(ValueError, match="future history"):
        compute_direct_security_breadth(
            _snapshot(), pd.concat([history, extra], ignore_index=True), market_date=market_date,
            query_coverage={"query_symbol_count": 2, "complete_query_count": 2, "query_error_count": 0},
        )


def test_public_contract_is_raw_only() -> None:
    root = Path(__file__).resolve().parents[1]
    contract = json.loads((root / "reference/market_regime_v0_1_direct_breadth_contract.json").read_text())
    assert contract["market_scope"]["beijing_stock_exchange_included"] is False
    assert contract["market_scope"]["full_sh_sz_bj_market_breadth_claimed"] is False
    assert contract["qualification"]["query_errors_allowed_for_qualified_state"] == 0
    assert contract["authority"]["contains_model_output"] is False
    assert contract["authority"]["may_rewrite_market_regime_state"] is False
    assert contract["authority"]["forward_outcomes_read"] is False


def test_committed_20260930_direct_breadth_bundle_is_qualified_and_hash_bound() -> None:
    import hashlib
    root = Path(__file__).resolve().parents[1]
    base = root / "data/reference/market_regime_v0_1_direct_breadth/2026-09-30"
    summary = json.loads((base / "breadth_summary.json").read_text())
    receipt = json.loads((base / "capture_receipt.json").read_text())
    manifest = json.loads((base / "bundle_manifest.json").read_text())
    assert summary["status"] == STATUS_QUALIFIED
    assert summary["market_date"] == "2026-09-30"
    assert summary["universe_security_count"] == 5223
    assert summary["query_symbol_count"] == 5223
    assert summary["complete_query_count"] == 5223
    assert summary["query_error_count"] == 0
    assert summary["qualified_trend_breadth"] is True
    assert summary["same_day_history_security_count"] == 5212
    assert summary["same_day_history_coverage_ratio"] > 0.99
    assert summary["scope_limitations"]["beijing_stock_exchange_in_direct_trend_breadth"] is False
    assert summary["scope_limitations"]["full_sh_sz_bj_market_breadth_claimed"] is False
    assert summary["authority"]["state_rewrite_allowed"] is False
    assert summary["authority"]["forward_outcomes_read"] is False
    assert receipt["complete_query_count"] == receipt["query_symbol_count"] == 5223
    assert receipt["query_error_count"] == 0
    assert manifest["bundle_id"] == "MARKET_REGIME_V0_1_DIRECT_BREADTH_2026_09_30"
    assert manifest["contains_model_output"] is False
    assert manifest["contains_private_evidence"] is False
    by_path = {item["path"]: item for item in manifest["files"]}
    for rel, item in by_path.items():
        path = root / rel
        assert path.stat().st_size == item["size_bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
