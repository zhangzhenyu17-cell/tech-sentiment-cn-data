from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tech_sentiment.prospective_preopen_timing_v2 import (
    validate_preopen_capture_window,
)


class _CalendarClient:
    def __init__(self, dates):
        self._dates = dates

    def tool_trade_date_hist_sina(self):
        return pd.DataFrame({"trade_date": self._dates})


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo("Asia/Shanghai"))


def test_preopen_window_accepts_previous_session_before_0530() -> None:
    result = validate_preopen_capture_window(
        market_session_date="2026-09-21",
        decision_date="2026-09-22",
        captured_at=_dt("2026-09-22T00:36:00"),
        client=_CalendarClient(["2026-09-18", "2026-09-21", "2026-09-22"]),
    )
    assert result["data_freeze_deadline_asia_shanghai"].endswith("05:30:00+08:00")
    assert result["first_eligible_execution_at"].endswith("09:30:00+08:00")
    assert result["minimum_preopen_buffer_hours"] == 4


def test_preopen_window_accepts_exact_0530_deadline() -> None:
    validate_preopen_capture_window(
        market_session_date="2026-09-21",
        decision_date="2026-09-22",
        captured_at=_dt("2026-09-22T05:30:00"),
        client=_CalendarClient(["2026-09-21", "2026-09-22"]),
    )


def test_preopen_window_rejects_after_four_hour_freeze_deadline() -> None:
    with pytest.raises(ValueError, match="PREOPEN_DATA_FREEZE_DEADLINE_PASSED"):
        validate_preopen_capture_window(
            market_session_date="2026-09-21",
            decision_date="2026-09-22",
            captured_at=_dt("2026-09-22T05:30:01"),
            client=_CalendarClient(["2026-09-21", "2026-09-22"]),
        )


def test_preopen_window_rejects_skipped_trading_day() -> None:
    with pytest.raises(ValueError, match="immediate next A-share trading day"):
        validate_preopen_capture_window(
            market_session_date="2026-09-18",
            decision_date="2026-09-22",
            captured_at=_dt("2026-09-21T23:30:00"),
            client=_CalendarClient(["2026-09-18", "2026-09-21", "2026-09-22"]),
        )


def test_preopen_window_rejects_capture_before_session_close() -> None:
    with pytest.raises(ValueError, match="before market-session close"):
        validate_preopen_capture_window(
            market_session_date="2026-09-21",
            decision_date="2026-09-22",
            captured_at=_dt("2026-09-21T14:59:59"),
            client=_CalendarClient(["2026-09-21", "2026-09-22"]),
        )


def test_preopen_workflow_is_manual_only() -> None:
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    text = (
        root / ".github/workflows/prospective-context-raw-preopen-v2.yml"
    ).read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "\n  schedule:" not in text
    assert "\n  workflow_run:" not in text
    assert "\n  pull_request:" not in text
    assert "\n  push:" not in text
    assert "market_session_date" in text
    assert "decision_date" in text
    assert "prospective-context-checkpoints-${{ inputs.market_session_date }}" in text
    assert "PREOPEN_PUBLIC_SOURCE_PUBLICATION_WINDOW_NOT_YET_MATURE" in text
    assert "time(23, 45)" in text
    assert '"akshare==1.18.94"' in text
    assert '"pandas==3.0.5"' in text
    assert '"pytest==8.4.2"' in text
    assert "python -m pip check" in text


def test_public_daily_orchestrator_is_exactly_allowlisted() -> None:
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "reference/prospective_daily_automation_v1.json").read_text(
            encoding="utf-8"
        )
    )
    entry = manifest["automatic_workflow_allowlist"][
        "prospective-public-daily-orchestrator-v1.yml"
    ]
    assert entry["triggers"] == ["schedule"]
    assert entry["cron_utc"] == "55 15 * * *"
    assert manifest["authorization_scope"] == "FROZEN_PROSPECTIVE_DAILY_OPERATIONS_ONLY"

    safety = manifest["safety"]
    assert safety["trading_calendar_required"] is True
    assert safety["non_trading_day_outside_active_window_noop"] is True
    assert safety["intervening_non_trading_days_preserve_active_window"] is True
    assert safety["exact_dated_release_required"] is True
    for key in (
        "rolling_latest_substitution_allowed",
        "historical_backfill_allowed",
        "private_model_material_allowed",
        "private_evidence_allowed",
        "forward_outcome_read_allowed",
        "research_result_allowed",
        "evidence_qualification_changed",
        "trading_authority_changed",
    ):
        assert safety[key] is False, key

    text = (
        root / ".github/workflows/prospective-public-daily-orchestrator-v1.yml"
    ).read_text(encoding="utf-8")
    assert 'cron: "55 15 * * *"' in text
    assert "workflow_dispatch:" in text
    assert "actions: write" in text
    assert "publish-market-bundle.yml" in text
    assert "prospective-context-raw-preopen-v2.yml" in text
    assert "market-bundle-$MARKET_SESSION_DATE" in text
    assert 'gh release view "$RAW_TAG"' in text
    assert "--ref main" in text
    assert "akshare==1.18.94" in text
    assert "pandas==3.0.5" in text
    assert '-f target_date="$MARKET_SESSION_DATE"' in text

    publisher = (
        root / ".github/workflows/publish-market-bundle.yml"
    ).read_text(encoding="utf-8")
    assert "target_date:" in publisher
    assert "EXACT_BUNDLE_TARGET_MUST_BE_LATEST_CLOSED_A_SHARE_SESSION" in publisher
    assert "REQUESTED_EXACT_TARGET_DATE" in publisher
    assert "session_close = datetime.combine" in text
    assert "freeze_deadline = datetime.combine" in text
    assert "ACTIVE_SESSION_TO_DECISION_PREOPEN_WINDOW" in text
