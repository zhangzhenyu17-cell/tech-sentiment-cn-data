from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
REGISTRY = ROOT / "reference" / "prospective_control_plane_v2.json"
TRIGGERS = ("workflow_dispatch", "schedule", "workflow_run", "push", "pull_request")
AUTOMATIC = {"schedule", "workflow_run", "push", "pull_request"}


def _text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _triggers(text: str) -> list[str]:
    return [
        trigger
        for trigger in TRIGGERS
        if re.search(rf"^  {re.escape(trigger)}:", text, flags=re.MULTILINE)
    ]


def _crons(text: str) -> list[str]:
    return sorted(re.findall(r'cron:\s*["\']([^"\']+)["\']', text))


def _permissions(text: str) -> list[str]:
    match = re.search(
        r"^permissions:\s*\n((?:\s{2,}.+\n?)*)",
        text,
        flags=re.MULTILINE,
    )
    if match is None:
        return []
    return sorted(
        line.strip()
        for line in match.group(1).splitlines()
        if line.strip()
    )


def _payload() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def test_registered_public_prospective_surface_matches_yaml() -> None:
    payload = _payload()
    registered = {row["file"] for row in payload["workflows"]}
    expected = set(payload["active_chain"])
    expected.update(payload["legacy_fail_closed"])
    assert registered == expected

    for row in payload["workflows"]:
        text = _text(row["file"])
        assert row["triggers"] == _triggers(text), row["file"]
        assert sorted(row["crons"]) == _crons(text), row["file"]
        assert sorted(row["permissions"]) == _permissions(text), row["file"]


def test_only_public_prospective_orchestrator_is_automatic() -> None:
    payload = _payload()
    automatic = [
        row["file"]
        for row in payload["workflows"]
        if AUTOMATIC.intersection(row["triggers"])
    ]
    assert automatic == ["prospective-public-daily-orchestrator-v1.yml"]
    row = next(
        item
        for item in payload["workflows"]
        if item["file"] == "prospective-public-daily-orchestrator-v1.yml"
    )
    assert sorted(row["crons"]) == sorted(
        [
            "45 15 * * *",
            "0,15,30,45 16-19 * * *",
            "0 20 * * *",
        ]
    )


def test_public_v1_capture_is_fail_closed_tombstone() -> None:
    payload = _payload()
    assert payload["legacy_fail_closed"] == ["prospective-context-raw-v1.yml"]
    row = next(
        item
        for item in payload["workflows"]
        if item["file"] == "prospective-context-raw-v1.yml"
    )
    assert row["lifecycle_class"] == "LEGACY_FAIL_CLOSED"
    text = _text(row["file"])
    assert "LEGACY_PROSPECTIVE_RAIL_SUPERSEDED_BY_TIMING_V2" in text
    assert "timeout-minutes: 2" in text
    assert "build_prospective_context_raw_v1.py" not in text
    assert "contents: write" not in text


def test_v2_capture_remains_manual_leaf_with_fixed_freeze() -> None:
    payload = _payload()
    row = next(
        item
        for item in payload["workflows"]
        if item["file"] == "prospective-context-raw-preopen-v2.yml"
    )
    assert row["lifecycle_class"] == "ACTIVE_DISPATCHED_LEAF"
    assert row["triggers"] == ["workflow_dispatch"]
    text = _text(row["file"])
    assert "prospective-context-raw-preopen-v2" in text
    assert "05:30" in text
    for forbidden in ("\n  schedule:", "\n  workflow_run:", "\n  push:", "\n  pull_request:"):
        assert forbidden not in text


def test_control_plane_does_not_widen_evidence_window_or_authority() -> None:
    payload = _payload()
    assert payload["timing_contract"]["data_freeze_deadline_asia_shanghai"] == "05:30"
    assert payload["timing_contract"]["evidence_window_extended_by_retry_cadence"] is False
    assert payload["shared_upstream_dependency"]["may_promote_prospective_evidence"] is False
    for key in (
        "historical_backfill_allowed",
        "forward_outcome_read_allowed",
        "model_change_allowed",
        "threshold_change_allowed",
        "universe_change_allowed",
        "evidence_qualification_change_allowed",
        "production_change_allowed",
        "trading_authority_change_allowed",
    ):
        assert payload["invariants"][key] is False, key
    assert "EVIDENCE_QUALIFICATION" in payload["not_authoritative_for"]
    assert "TRADING_AUTHORITY" in payload["not_authoritative_for"]
