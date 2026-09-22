from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INTEGRITY = ROOT / "reference/system_integrity_registry_v1.json"


def _json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_public_integrity_sources_exist() -> None:
    payload = json.loads(INTEGRITY.read_text(encoding="utf-8"))
    assert (ROOT / payload["sources"]["workflow_control_plane"]).is_file()
    assert (ROOT / payload["sources"]["prospective_active_rail"]).is_file()
    assert (ROOT / payload["sources"]["public_tree_audit"]).is_file()


def test_public_integrity_contract_grants_no_model_or_trading_authority() -> None:
    payload = json.loads(INTEGRITY.read_text(encoding="utf-8"))
    for key, value in payload["invariants"].items():
        assert value is False, key


def test_public_prospective_active_rail_matches_control_plane() -> None:
    payload = json.loads(INTEGRITY.read_text(encoding="utf-8"))
    control = _json(payload["sources"]["workflow_control_plane"])
    active = _json(payload["sources"]["prospective_active_rail"])
    rows = {row["file"]: row for row in control["workflows"]}

    orchestrator = active["active_public_orchestrator"]
    capture = active["active_public_capture"]
    assert rows[orchestrator]["governance_class"] == "ACTIVE_AUTOMATED_ORCHESTRATOR"
    assert rows[orchestrator]["triggers"] == ["workflow_dispatch", "schedule"]
    assert rows[capture]["governance_class"] == "ACTIVE_DISPATCHED_LEAF"
    assert rows[capture]["triggers"] == ["workflow_dispatch"]

    for legacy in active["legacy_fail_closed"]:
        assert rows[legacy]["governance_class"] == "LEGACY_FAIL_CLOSED"


def test_public_prospective_safety_firewalls_remain_closed() -> None:
    payload = json.loads(INTEGRITY.read_text(encoding="utf-8"))
    active = _json(payload["sources"]["prospective_active_rail"])
    for key, value in active["safety"].items():
        assert value is False, key
