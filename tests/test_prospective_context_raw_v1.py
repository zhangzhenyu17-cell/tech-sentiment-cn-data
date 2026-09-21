from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd
import pytest

from tech_sentiment.prospective_context_raw_v1 import (
    RECEIPT_NAME,
    _validate_window_symbol_coverage,
    _validate_live_snapshot_exact,
    capture_trading_dates,
    package_capture,
)


ROOT = Path(__file__).resolve().parents[1]


class _CalendarClient:
    def __init__(self, dates: list[str]):
        self.dates = dates

    def tool_trade_date_hist_sina(self):
        return pd.DataFrame({"trade_date": pd.to_datetime(self.dates)})


def _membership(date: str = "2026-09-21") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": [f"{i:06d}" for i in range(1, 51)],
            "effective_start": ["2026-01-01"] * 50,
            "effective_end": [date] * 50,
        }
    )


def _live() -> pd.DataFrame:
    return pd.DataFrame({"symbol": [f"{i:06d}" for i in range(1, 51)]})


def test_capture_calendar_requires_real_trading_day_and_sufficient_warmup() -> None:
    dates = pd.bdate_range("2025-01-02", periods=360).date.astype(str).tolist()
    target = dates[-1]
    captured = capture_trading_dates(
        target,
        warmup_trading_days=340,
        client=_CalendarClient(dates),
    )
    assert len(captured) == 340
    assert captured[-1] == pd.Timestamp(target)

    with pytest.raises(ValueError, match="at least 313"):
        capture_trading_dates(
            target,
            warmup_trading_days=312,
            client=_CalendarClient(dates),
        )

    with pytest.raises(ValueError, match="not a confirmed"):
        capture_trading_dates(
            "2026-12-31",
            warmup_trading_days=340,
            client=_CalendarClient(dates),
        )


def test_live_snapshot_must_exactly_match_reconstructed_active_membership() -> None:
    active, live = _validate_live_snapshot_exact(
        membership=_membership(),
        live_snapshot=_live(),
        operation_date="2026-09-21",
        expected_constituents=50,
        universe="TEST50",
    )
    assert active == live

    drifted = _live()
    drifted.loc[0, "symbol"] = "999999"
    with pytest.raises(ValueError, match="does not exactly match"):
        _validate_live_snapshot_exact(
            membership=_membership(),
            live_snapshot=drifted,
            operation_date="2026-09-21",
            expected_constituents=50,
            universe="TEST50",
        )


def test_constituent_coverage_reuses_frozen_window_symbol_semantics() -> None:
    membership = _membership()
    symbols = membership["symbol"].astype(str).tolist()
    rows = [
        {"date": "2026-09-18", "symbol": symbol}
        for symbol in symbols[:48]
    ]
    coverage = _validate_window_symbol_coverage(
        prices=pd.DataFrame(rows),
        membership=membership,
        minimum_coverage=0.95,
        universe="TEST50",
    )
    assert coverage == pytest.approx(48 / 50)

    with pytest.raises(ValueError, match="coverage"):
        _validate_window_symbol_coverage(
            prices=pd.DataFrame(rows[:47]),
            membership=membership,
            minimum_coverage=0.95,
            universe="TEST50",
        )


def test_public_contract_is_forward_only_and_contains_no_private_model_semantics() -> None:
    contract = json.loads(
        (ROOT / "reference/prospective_context_raw_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert contract["operation_date_semantics"]["historical_replay_allowed"] is False
    assert (
        contract["operation_date_semantics"][
            "retroactive_evidence_qualification_allowed"
        ]
        is False
    )
    assert contract["workflow"]["workflow_dispatch_only"] is True
    assert contract["workflow"]["automatic_trigger_allowed"] is False
    firewall = contract["privacy_and_research_firewall"]
    assert all(value is False for value in firewall.values())
    serialized = json.dumps(contract, ensure_ascii=False).lower()
    for forbidden in (
        "market_liquidity_percentile",
        "long_horizon_flow_strength",
        "flow_persistence",
        "short_term_speculation_heat",
        "rotation_crowding_pressure",
        "crowding_score",
    ):
        assert forbidden not in serialized


def test_package_capture_is_byte_deterministic_for_same_capture(tmp_path: Path) -> None:
    root = tmp_path / "capture"
    root.mkdir()
    (root / "sample.csv").write_text("date,value\n2026-09-21,1\n", encoding="utf-8")
    receipt = {
        "schema_version": "prospective-context-public-raw-receipt-v1",
        "status": "PUBLIC_RAW_FORWARD_CAPTURE_COMPLETE",
        "operation_date": "2026-09-21",
        "captured_at_utc": datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc).isoformat(),
        "historical_replay_allowed": False,
        "retroactive_evidence_qualification_allowed": False,
        "private_model_semantics_materialized": False,
    }
    (root / RECEIPT_NAME).write_text(
        json.dumps(receipt, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    first = tmp_path / "first"
    second = tmp_path / "second"
    m1 = package_capture(root, output_dir=first, operation_date="2026-09-21")
    m2 = package_capture(root, output_dir=second, operation_date="2026-09-21")
    assert m1["bundle_identity"] == m2["bundle_identity"]
    a1 = first / m1["archive"]
    a2 = second / m2["archive"]
    assert a1.read_bytes() == a2.read_bytes()


def test_public_capture_workflow_is_manual_only() -> None:
    text = (
        ROOT / ".github/workflows/prospective-context-raw-v1.yml"
    ).read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "\n  schedule:" not in text
    assert "\n  push:" not in text
    assert "\n  pull_request:" not in text
    assert "\n  workflow_run:" not in text
    assert "contents: write" in text
