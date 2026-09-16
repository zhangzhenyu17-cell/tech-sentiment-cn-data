from pathlib import Path


def test_acceptance_checklist_preserves_holdout_and_authority_boundaries() -> None:
    text = (Path(__file__).parents[1] / "docs/secondary_evidence_acceptance_checklist.md").read_text(encoding="utf-8")
    assert "2024+ holdout data is not read" in text
    assert "never production or trading authority" in text
    assert "independent source cross-check" in text
    assert "fail-closed" in text
