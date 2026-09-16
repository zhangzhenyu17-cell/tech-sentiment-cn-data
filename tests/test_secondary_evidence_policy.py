from pathlib import Path


def test_secondary_evidence_policy_keeps_research_only_boundary() -> None:
    text = (Path(__file__).parents[1] / "docs/secondary_evidence_policy.md").read_text(encoding="utf-8")
    assert "historical research data eligibility" in text
    assert "does not grant production authority" in text
    assert "does not automatically" not in text or "not automatically" in text
    assert "Q1/Q3 top holdings are cross-check only" in text
    assert "Conflicts remain fail-closed" in text
