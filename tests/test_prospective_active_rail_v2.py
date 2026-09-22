from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_active_public_prospective_rail_is_single_and_v2() -> None:
    registry = json.loads(
        (ROOT / "reference/prospective_active_rail_v2.json").read_text(encoding="utf-8")
    )
    assert registry["status"] == "ACTIVE"
    assert registry["active_public_orchestrator"] == "prospective-public-daily-orchestrator-v1.yml"
    assert registry["active_public_capture"] == "prospective-context-raw-preopen-v2.yml"
    assert registry["legacy_fail_closed"] == ["prospective-context-raw-v1.yml"]
    for key, value in registry["safety"].items():
        assert value is False, key


def test_legacy_same_day_raw_workflow_cannot_materialize_new_capture() -> None:
    text = (
        ROOT / ".github/workflows/prospective-context-raw-v1.yml"
    ).read_text(encoding="utf-8")
    assert "LEGACY_PROSPECTIVE_RAIL_SUPERSEDED_BY_TIMING_V2" in text
    assert "build_prospective_context_raw_v1.py" not in text
    assert "contents: read" in text
    assert "contents: write" not in text


def test_public_daily_orchestrator_dispatches_only_preopen_v2() -> None:
    text = (
        ROOT / ".github/workflows/prospective-public-daily-orchestrator-v1.yml"
    ).read_text(encoding="utf-8")
    assert "prospective-context-raw-preopen-v2.yml" in text
    assert "prospective-context-raw-v1.yml" not in text
