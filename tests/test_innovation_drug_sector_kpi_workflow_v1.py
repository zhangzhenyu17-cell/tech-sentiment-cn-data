from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/innovation-drug-sector-kpi-raw-v1.yml"


def test_sector_kpi_workflow_is_manual_only_and_exact_scope() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.startswith("name: Innovation Drug Sector KPI Raw V1\n")
    assert re.search(r"(?m)^on:\s*$", text)
    assert re.search(r"(?m)^  workflow_dispatch:\s*$", text)
    for forbidden in ("\n  schedule:", "\n  push:", "\n  pull_request:", "\n  workflow_run:"):
        assert forbidden not in text
    assert 'SYMBOL: "600276"' in text
    assert 'START_DATE: "2020-01-01"' in text
    assert "materialize_innovation_drug_sector_kpi_raw_v1.py" in text
    assert "innovation-drug-sector-kpi-raw-v1-600276" in text


def test_sector_kpi_workflow_has_no_private_or_predictive_payload() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for forbidden in (
        "private_threshold",
        "forward_return",
        "model_weight",
        "trading_signal",
        "position_size",
        "tech-sentiment-cn.git",
    ):
        assert forbidden not in text.lower()
