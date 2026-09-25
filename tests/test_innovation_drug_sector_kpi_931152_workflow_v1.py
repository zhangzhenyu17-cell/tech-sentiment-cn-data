from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/innovation-drug-sector-kpi-931152-raw-v1.yml"


def test_workflow_is_manual_only_and_uses_frozen_pit_membership() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.startswith("name: Innovation Drug 931152 Sector KPI Raw V1\n")
    assert re.search(r"(?m)^on:\s*$", text)
    assert re.search(r"(?m)^  workflow_dispatch:\s*$", text)
    for forbidden in ("\n  schedule:", "\n  push:", "\n  pull_request:", "\n  workflow_run:"):
        assert forbidden not in text
    assert 'START_DATE: "2019-04-22"' in text
    assert 'END_DATE: "2026-09-11"' in text
    assert 'MEMBERSHIP_SCOPE: "data/reference/sector_931152_kpi_membership_scope_v1.csv"' in text
    assert "materialize_innovation_drug_sector_kpi_931152_raw_v1.py" in text
    assert "current_constituent_backfill_used" in text
    assert "sector_kpi_formal_state" in text
    assert "DATA_INSUFFICIENT" in text


def test_workflow_has_no_model_or_trading_payload() -> None:
    text = WORKFLOW.read_text(encoding="utf-8").lower()
    for forbidden in (
        "forward_return",
        "position_size",
        "private_threshold",
        "model_weight",
        "auto_trade",
        "schedule:",
        "workflow_run:",
    ):
        assert forbidden not in text
