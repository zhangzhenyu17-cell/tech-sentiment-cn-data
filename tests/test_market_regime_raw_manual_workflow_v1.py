from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/market-regime-raw-capture-manual-v1.yml"


def test_market_regime_raw_capture_workflow_manual_only_and_fail_closed():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    # This exact workflow is a manual-only access path, not a new scheduler.
    event_block = text.split("on:", 1)[1].split("permissions:", 1)[0]
    assert "workflow_dispatch:" in event_block
    for forbidden in ("schedule:", "push:", "pull_request:", "workflow_run:"):
        assert forbidden not in event_block
    assert "contents: read" in text
    assert "scripts/materialize_market_regime_v0_public_input.py" in text
    assert "requested exact session unavailable" in text
    assert "ZoneInfo(\"Asia/Shanghai\")" in text
    assert "now.time() < time(15, 0)" in text
    assert "intraday index bars rejected" in text
    assert "manifest[\"latest_market_date\"] == requested" in text
    assert "sha256" in text
    assert "contains_private_evidence" in text
    assert "public_handoff_ready_grants_private_qualification" in text
    assert "actions/upload-artifact@v4" in text
    assert "retention-days: 90" in text
    assert "contents: write" not in text
    assert "git push" not in text


def test_public_capture_does_not_mention_private_model_or_portfolio():
    text = WORKFLOW.read_text(encoding="utf-8")
    for forbidden in (
        "tech-sentiment-cn/actions", "reference/private/", "portfolio_positions",
        "PERSONAL_HOLDINGS", "STRATEGIC_BUDGET", "PRODUCTION_SIGNAL",
    ):
        assert forbidden not in text
