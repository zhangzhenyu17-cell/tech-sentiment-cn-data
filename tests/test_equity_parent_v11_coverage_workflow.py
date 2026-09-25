from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/fundamental-equity-parent-v11-coverage.yml"


def test_workflow_is_manual_only_and_exact_scope() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.startswith("name: Fundamental EQUITY_PARENT V11 Historical Coverage\n")
    assert re.search(r"(?m)^on:\s*$", text)
    assert re.search(r"(?m)^  workflow_dispatch:\s*$", text)
    for forbidden in ("schedule:", "workflow_run:", "pull_request:", "push:"):
        assert forbidden not in text
    assert "permissions:\n  contents: read" in text
    assert 'SHARD_COUNT: "48"' in text
    assert "max-parallel: 4" in text
    assert "timeout-minutes: 360" in text
    assert "cec1194806ccf0c8bac043548985d58b88a0c8d9" in text
    assert "ede3f472d5c3c4bd8384ce8071988bd54a7687a1" in text
    assert "v4a-fundamental-20220104-20260917-56e8c44ca678668bfc17" in text
    assert "c1df2ec495657f52cf34935feb5ab94c7159f43e4cbf6f24bb8fbcf43ffdb54e" in text
    assert "fundamental-equity-parent-v11-coverage-audit" in text


def test_workflow_does_not_add_research_or_outcome_steps() -> None:
    text = WORKFLOW.read_text(encoding="utf-8").lower()
    for forbidden in (
        "forward_return",
        "holdout",
        "parameter_search",
        "threshold_search",
        "production_promotion",
        "trading_authority",
    ):
        assert forbidden not in text
