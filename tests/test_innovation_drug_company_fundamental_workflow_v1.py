from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/innovation-drug-company-fundamental-v1.yml"


def test_workflow_is_manual_only_and_scope_strict() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"(?m)^on:\s*$", text)
    assert re.search(r"(?m)^  workflow_dispatch:\s*$", text)
    for trigger in ("schedule:", "workflow_run:", "pull_request:", "push:"):
        assert trigger not in text
    assert "innovation_drug_company_fundamental_scope.csv" in text
    assert "600276" in text
    assert "--shard-count 1" in text
    assert "--fail-on-hard-errors" in text
    assert "future_prices_or_returns_used" in text
    assert "evidence_qualification_changed" in text
