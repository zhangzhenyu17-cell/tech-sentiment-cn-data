import json
from pathlib import Path


def test_price_refresh_is_manual_only_read_only_and_explicitly_allowlisted():
    root = Path(__file__).resolve().parents[1]
    path = ".github/workflows/cross-sector-price-refresh-v1.yml"
    text = (root / path).read_text()
    assert "workflow_dispatch:" in text
    assert all(token not in text for token in ("schedule:", "workflow_run:", "push:", "pull_request:"))
    assert "contents: read" in text
    assert "contents: write" not in text
    assert "--as-of-date 2026-09-30" in text
    assert "stage/cross_sector_price.csv" in text and "stage/cross_sector_price_manifest.json" in text
    assert "cancel-in-progress: false" in text
    contract = json.loads((root / "reference/public_data_governance_control_plane_v1.json").read_text())
    governance = next(v for v in contract.values() if isinstance(v, dict) and "new_workflow_allowlist" in v)
    entries = [item for item in governance["new_workflow_allowlist"] if item["path"] == path]
    assert len(entries) == 1
    assert entries[0]["trigger_policy"] == "WORKFLOW_DISPATCH_ONLY"
    assert entries[0]["automatic_triggers_allowed"] is False
